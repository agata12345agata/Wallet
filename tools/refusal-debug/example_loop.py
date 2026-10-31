#!/usr/bin/env python3
"""example_loop.py - minimalny agent konsolowy z poprawną obsługą odmów.

To jest wzorzec do wklejenia w Twoją pętlę (u Ciebie: "TY >" / "HAJKA AI >").
Nie wymaga SDK - gada z API po HTTP przez bibliotekę standardową.

Użycie:
    export ANTHROPIC_API_KEY=...
    export MODEL=twoj-model
    export FALLBACK_MODEL=inny-model        # opcjonalnie
    # opcjonalnie: export SYSTEM_PROMPT="Jesteś ..."
    python3 example_loop.py

Komendy w konsoli:
    /clear   - czyści historię rozmowy
    /debug   - pokazuje ostatnią odpowiedź (stop_reason, bloki, kategoria)
    /quit    - wyjście
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import refusal_guard as rg  # noqa: E402

BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
MODEL = os.environ.get("MODEL", "claude-sonnet-4-5")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL") or None
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT") or (
    "Jesteś HAJKA AI, zwięzłym asystentem w konsoli. Odpowiadaj po polsku."
)
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1024"))

URL = BASE_URL + ("" if BASE_URL.endswith("/v1/messages") else
                  ("/messages" if BASE_URL.endswith("/v1") else "/v1/messages"))
HEADERS = {
    "content-type": "application/json",
    "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"),
}


def _auth_headers():
    headers = dict(HEADERS)
    key = os.environ.get("ANTHROPIC_API_KEY")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if key:
        headers["x-api-key"] = key
    elif token:
        headers["authorization"] = "Bearer " + token
    else:
        sys.exit("Brak ANTHROPIC_API_KEY (albo ANTHROPIC_AUTH_TOKEN).")
    return headers


def send(**kwargs):
    """Funkcja 'send' dla refusal_guard.ask(). Zwraca dict + request-id."""
    payload = {k: v for k, v in kwargs.items() if v is not None}
    payload["stream"] = False
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode("utf-8"),
        headers=_auth_headers(), method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            body["_request_id"] = resp.headers.get("request-id")
            return body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return {"error": {"message": "HTTP %s: %s" % (exc.code, raw[:200])}}
        body["_request_id"] = exc.headers.get("request-id") if exc.headers else None
        return body


def main():
    print("Model: %s%s" % (MODEL, "  (fallback: %s)" % FALLBACK_MODEL if FALLBACK_MODEL else ""))
    print("Komendy: /clear, /debug, /quit\n")

    history = []
    last = None

    while True:
        try:
            line = input("TY > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            break
        if line == "/clear":
            history.clear()
            print("(historia wyczyszczona)\n")
            continue
        if line == "/debug" and last is not None:
            print(json.dumps({
                "kind": last.kind,
                "stop_reason": last.stop_reason,
                "category": last.category,
                "explanation": last.explanation,
                "blocks": last.blocks,
                "model": last.model,
                "request_id": last.request_id,
            }, ensure_ascii=False, indent=2) + "\n")
            continue

        history.append({"role": "user", "content": line})
        print("HAJKA AI myśli...\n")

        reply = rg.ask(
            send,
            model=MODEL,
            messages=history,
            fallback_model=FALLBACK_MODEL,
            system=SYSTEM_PROMPT,
            max_tokens=MAX_TOKENS,
        )
        last = reply

        if reply.kind == "text":
            print("HAJKA AI > %s\n" % reply.text)
            history.append({"role": "assistant", "content": reply.text})
        elif reply.kind == "refusal":
            print("HAJKA AI > %s\n" % rg.render(reply))
            # Kluczowe: nie zostawiaj odrzuconej treści w historii,
            # bo następne wywołanie znowu zostanie odrzucone.
            history = rg.strip_last_turn(history)
            print("(ostatnia wiadomość usunięta z historii - nie będzie blokować kolejnych tur)\n")
        else:
            print("HAJKA AI > %s\n" % rg.render(reply))


if __name__ == "__main__":
    main()
