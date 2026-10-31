#!/usr/bin/env python3
"""
diagnose_refusal.py - znajdź przyczynę stop_reason == "refusal" w agencie.

Objaw, który diagnozujemy:
    DEBUG STOP: refusal
    DEBUG BLOCKS: []
    HAJKA AI > [Model nie zwrócił tekstu]

To NIE jest błąd sieci ani zły klucz. To poprawna odpowiedź HTTP 200,
w której model (a dokładniej classifier bezpieczeństwa) odmówił odpowiedzi.
Wtedy `content` jest puste, a `stop_details` mówi, KTÓRA kategoria zadziałała.

Skrypt wysyła serię żądań od minimalnego do pełnego (system prompt, narzędzia,
historia rozmowy) i pokazuje, przy którym z nich pojawia się refusal.
Dzięki temu wiadomo, czy blokuje samo "hej", czy coś, co dokleja Twój agent.

Zależności: tylko biblioteka standardowa (Python 3.8+).

Przykłady:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 diagnose_refusal.py --model claude-sonnet-4-5

    # z Twoim system promptem i historią z agenta
    python3 diagnose_refusal.py --model ... --system-file system.txt \
        --history-file history.json --tools-file tools.json

    # jeśli agent używa streamingu
    python3 diagnose_refusal.py --model ... --stream --system-file system.txt

Kod wyjścia:
    0 - żaden probe nie został odrzucony
    1 - co najmniej jeden probe dostał stop_reason "refusal"
    2 - błąd konfiguracji / transportu
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_VERSION = "2023-06-01"

CATEGORY_HINTS = {
    "cyber": (
        "klasyfikator 'cyber'. Potrafi odpalić się na słownictwie typu exploit, "
        "shell, exec, SSH, reverse engineering, scany portów, 'wallet.dat', "
        "krypto-recovery, malware. To często false positive."
    ),
    "bio": "klasyfikator 'bio' (treści biologiczne / laboratoryjne).",
    "frontier_llm": (
        "klasyfikator 'frontier_llm'. Odpala się na treściach o trenowaniu modeli, "
        "destylacji, datasetach do treningu konkurencyjnego LLM-a."
    ),
    "reasoning_extraction": (
        "klasyfikator 'reasoning_extraction'. Powód prawie zawsze jest w prompcie: "
        "prosisz o <thinking>, 'pokaż swoje rozumowanie', pole 'reasoning'/'trace' "
        "w JSON-ie albo o cytowanie wewnętrznych notatek."
    ),
    "general_harms": "kategoria 'general_harms' - inny obszar polityki użycia.",
    None: "refusal bez kategorii (category == null) - traktuj jak ogólną odmowę.",
}


def die(msg, code=2):
    print("BŁĄD: " + msg, file=sys.stderr)
    sys.exit(code)


def resolve_auth(args):
    key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not key and not token:
        die(
            "brak klucza. Ustaw ANTHROPIC_API_KEY (albo ANTHROPIC_AUTH_TOKEN), "
            "lub podaj --api-key."
        )
    return key, token


def build_headers(args):
    key, token = resolve_auth(args)
    headers = {
        "content-type": "application/json",
        "anthropic-version": args.api_version,
    }
    if key:
        headers["x-api-key"] = key
    else:
        headers["authorization"] = "Bearer " + token
    for extra in args.header or []:
        if ":" not in extra:
            die("--header musi mieć format 'Nazwa: wartość'")
        name, value = extra.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def url_for(base_url):
    base = base_url.rstrip("/")
    if base.endswith("/v1/messages"):
        return base
    if base.endswith("/v1"):
        return base + "/messages"
    return base + "/v1/messages"


def post_json(url, headers, payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except urllib.error.URLError as exc:
        die("nie mogę połączyć się z %s: %s" % (url, exc.reason))


def summarize_sse(raw):
    """Czyta strumień SSE i zwraca to samo podsumowanie co dla non-stream."""
    stop_reason = None
    stop_details = None
    blocks = []
    usage = {}
    text_len = 0
    model = None
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            evt = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype == "message_start":
            msg = evt.get("message", {})
            model = msg.get("model", model)
            usage.update(msg.get("usage") or {})
        elif etype == "content_block_start":
            block = evt.get("content_block") or {}
            blocks.append(block.get("type", "?"))
        elif etype == "content_block_delta":
            delta = evt.get("delta") or {}
            if delta.get("type") == "text_delta":
                text_len += len(delta.get("text") or "")
        elif etype == "message_delta":
            delta = evt.get("delta") or {}
            stop_reason = delta.get("stop_reason", stop_reason)
            stop_details = delta.get("stop_details", stop_details)
            usage.update(evt.get("usage") or {})
        elif etype == "error":
            err = evt.get("error") or {}
            return {
                "ok": False,
                "error": "%s: %s" % (err.get("type"), err.get("message")),
            }
    return {
        "ok": True,
        "model": model,
        "stop_reason": stop_reason,
        "stop_details": stop_details,
        "blocks": blocks,
        "text_len": text_len,
        "usage": usage,
    }


def summarize_json(raw):
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return {"ok": False, "error": "odpowiedź nie jest JSON-em: " + raw[:200].decode("utf-8", "replace")}
    if body.get("type") == "error":
        err = body.get("error") or {}
        return {"ok": False, "error": "%s: %s" % (err.get("type"), err.get("message"))}
    blocks = [b.get("type", "?") for b in body.get("content") or []]
    text = "".join(
        b.get("text", "") for b in body.get("content") or [] if b.get("type") == "text"
    )
    return {
        "ok": True,
        "model": body.get("model"),
        "stop_reason": body.get("stop_reason"),
        "stop_details": body.get("stop_details"),
        "blocks": blocks,
        "text_len": len(text),
        "usage": body.get("usage") or {},
        "raw": body,
    }


def run_probe(name, payload, args, headers, url):
    print("\n" + "=" * 72)
    print("PROBE: " + name)
    print("=" * 72)
    desc = []
    if payload.get("system"):
        desc.append("system=%d znaków" % len(json.dumps(payload["system"])))
    if payload.get("tools"):
        desc.append("tools=%d" % len(payload["tools"]))
    desc.append("messages=%d" % len(payload.get("messages") or []))
    desc.append("max_tokens=%s" % payload.get("max_tokens"))
    if payload.get("stream"):
        desc.append("stream=True")
    print("żądanie: " + ", ".join(desc))

    status, resp_headers, raw = post_json(url, headers, payload, args.timeout)
    request_id = (
        resp_headers.get("request-id")
        or resp_headers.get("x-request-id")
        or resp_headers.get("anthropic-request-id")
        or "-"
    )
    print("HTTP: %s   request-id: %s" % (status, request_id))

    if status != 200:
        info = summarize_json(raw)
        print("ODPOWIEDŹ BŁĘDU: %s" % info.get("error", raw[:300]))
        return {
            "name": name,
            "http": status,
            "ok": False,
            "refused": False,
            "error": info.get("error"),
            "request_id": request_id,
        }

    info = summarize_sse(raw) if payload.get("stream") else summarize_json(raw)
    if not info.get("ok"):
        print("BŁĄD W ODPOWIEDZI: %s" % info.get("error"))
        return {"name": name, "http": status, "ok": False, "refused": False, "error": info.get("error"), "request_id": request_id}

    details = info.get("stop_details") or {}
    refused = info.get("stop_reason") == "refusal"

    print("model:        %s" % info.get("model"))
    print("stop_reason:  %s" % info.get("stop_reason"))
    print("bloki:        %s" % (info.get("blocks") or "[]"))
    print("długość tekstu: %d znaków" % info.get("text_len", 0))
    print("usage:        %s" % json.dumps(info.get("usage") or {}, ensure_ascii=False))
    if refused:
        print("stop_details.category:    %r" % details.get("category"))
        print("stop_details.explanation: %s" % details.get("explanation"))
        if details.get("recommended_model"):
            print("stop_details.recommended_model: %s" % details.get("recommended_model"))
        hint = CATEGORY_HINTS.get(details.get("category"))
        if hint:
            print("WSKAZÓWKA: " + hint)
    return {
        "name": name,
        "http": status,
        "ok": True,
        "refused": refused,
        "stop_reason": info.get("stop_reason"),
        "category": details.get("category"),
        "explanation": details.get("explanation"),
        "blocks": info.get("blocks"),
        "text_len": info.get("text_len"),
        "request_id": request_id,
        "raw": info.get("raw"),
    }


def load_json_arg(path, what):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        die("nie mogę otworzyć %s (%s): %s" % (what, path, exc))
    except json.JSONDecodeError as exc:
        die("%s (%s) nie jest poprawnym JSON-em: %s" % (what, path, exc))


def load_text_arg(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        die("nie mogę otworzyć pliku %s: %s" % (path, exc))


def main():
    parser = argparse.ArgumentParser(
        description="Diagnostyka stop_reason == 'refusal' w agencie LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", required=True, help="model, którego używa agent (dokładnie ten sam string)")
    parser.add_argument("--base-url", default=os.environ.get("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL),
                        help="adres API / proxy (domyślnie %s)" % DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=None, help="klucz API (domyślnie z ANTHROPIC_API_KEY)")
    parser.add_argument("--api-version", default=DEFAULT_VERSION)
    parser.add_argument("--header", action="append", help="dodatkowy nagłówek, np. 'x-portkey-provider: anthropic'")
    parser.add_argument("--system-file", help="plik z system promptem Twojego agenta")
    parser.add_argument("--history-file", help="JSON: lista messages z agenta (poprzednie tury)")
    parser.add_argument("--tools-file", help="JSON: lista definicji narzędzi z agenta")
    parser.add_argument("--greeting", default="hej", help="treść wiadomości testowej (domyślnie 'hej')")
    parser.add_argument("--max-tokens", type=int, default=64, help="max_tokens dla probe'ów (domyślnie 64)")
    parser.add_argument("--stream", action="store_true", help="testuj przez streamowanie (jak agent na żywo)")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--save", help="zapisz surowe odpowiedzi (JSON) do pliku - przydatne do zgłoszenia do supportu")
    args = parser.parse_args()

    headers = build_headers(args)
    url = url_for(args.base_url)

    system_text = load_text_arg(args.system_file) if args.system_file else None
    history = load_json_arg(args.history_file, "historia rozmowy") if args.history_file else None
    tools = load_json_arg(args.tools_file, "definicje narzędzi") if args.tools_file else None

    if history is not None and not isinstance(history, list):
        die("--history-file musi zawierać listę messages, np. [{\"role\": \"user\", \"content\": \"...\"}]")

    print("endpoint: %s" % url)
    print("model:    %s" % args.model)

    user_msg = {"role": "user", "content": args.greeting}
    probes = []

    base = {
        "model": args.model,
        "max_tokens": args.max_tokens,
        "messages": [user_msg],
    }
    if args.stream:
        base["stream"] = True

    probes.append(("1. samo 'hej' (bez systemu, bez narzędzi, bez historii)", dict(base)))

    probes.append((
        "2. to samo, większy max_tokens (wyklucza ucięcie odpowiedzi)",
        dict(base, max_tokens=max(args.max_tokens, 1024)),
    ))

    if system_text:
        probes.append((
            "3. 'hej' + system prompt agenta",
            dict(base, system=system_text),
        ))

    if tools:
        probes.append((
            "4. 'hej' + definicje narzędzi (bez systemu)",
            dict(base, tools=tools),
        ))

    if history:
        probes.append((
            "5. historia rozmowy agenta + 'hej'",
            dict(base, messages=list(history) + [user_msg]),
        ))
        if system_text:
            probes.append((
                "6. pełny zestaw: system + narzędzia + historia + 'hej'",
                dict(base, system=system_text, tools=tools or None,
                     messages=list(history) + [user_msg]),
            ) if tools else (
                "6. pełny zestaw: system + historia + 'hej'",
                dict(base, system=system_text, messages=list(history) + [user_msg]),
            ))

    results = []
    for name, payload in probes:
        payload = {k: v for k, v in payload.items() if v is not None}
        results.append(run_probe(name, payload, args, headers, url))

    # ---------------- werdykt ----------------
    print("\n" + "=" * 72)
    print("WERDYKT")
    print("=" * 72)

    refused = [r for r in results if r.get("refused")]
    errors = [r for r in results if not r.get("ok")]

    for err in errors:
        print("- %s -> błąd HTTP %s: %s" % (err["name"], err.get("http"), err.get("error")))
    if errors and not refused:
        print("\nTo nie jest refusal, tylko błąd API. Popraw model / klucz / endpoint "
              "i uruchom ponownie.")
        sys.exit(2)

    if not refused:
        print("Żaden probe nie dostał stop_reason 'refusal'.")
        print("Jeśli agent nadal pokazuje '[Model nie zwrócił tekstu]', problem jest")
        print("w kodzie klienta albo w parametrach, których ten skrypt nie odtwarza:")
        print("  - inne parametry (thinking, temperature, stop_sequences, betas),")
        print("  - inny model / inny endpoint niż tu podane,")
        print("  - filtrowanie bloków: agent czyta tylko type == 'text', a dostaje")
        print("    same bloki 'thinking' -> tekst wychodzi pusty bez refusal.")
        sys.exit(0)

    if results and results[0].get("refused"):
        print("Odmowa leci już na samo 'hej'. To znaczy, że NIE blokuje jej treść")
        print("Twojego promptu, tylko jeden z trzech rzeczy:")
        print("  1) endpoint/proxy dokleja coś do żądania (nagłówki, system, kontekst),")
        print("  2) model jest inny niż myślisz - sprawdź pole 'model' w odpowiedzi,")
        print("  3) konto/klucz jest na liście ograniczeń po stronie dostawcy.")
        print("Sprawdź też --base-url: jeśli idziesz przez własne proxy, przetestuj")
        print("bezpośrednio api.anthropic.com z tym samym kluczem.")
    else:
        print("Odmowa pojawia się dopiero przy pełnym żądaniu. Winowajca:")
        culprit = refused[0]
        print("  -> " + culprit["name"])
        print("  -> kategoria: %r" % culprit.get("category"))
        if culprit.get("explanation"):
            print("  -> wyjaśnienie: %s" % culprit["explanation"])
        print("")
        print("Napraw to, usuwając/łagodząc tę część promptu (albo skracając historię")
        print("rozmowy), a nie ponawiając to samo żądanie - powtórka z tą samą treścią")
        print("w historii znów zostanie odrzucona.")

    print("\nPełna lista kategorii i zasad: https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback")

    if args.save:
        payload = [
            {k: v for k, v in r.items() if k != "raw"} for r in results
        ]
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump({"endpoint": url, "model": args.model, "probes": payload}, handle,
                      ensure_ascii=False, indent=2)
        print("Zapisano podsumowanie do: %s" % args.save)

    sys.exit(1)


if __name__ == "__main__":
    main()
