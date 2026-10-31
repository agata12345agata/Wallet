#!/usr/bin/env python3
"""selftest.py - test refusal_guard.py BEZ wywołań API (zero kosztów).

Sprawdza, czy moduł poprawnie rozpoznaje wszystkie warianty odpowiedzi
i czy fallback działa. Uruchom po skopiowaniu refusal_guard.py:

    python3 selftest.py

Kod wyjścia 0 = wszystko OK, 1 = coś nie działa (szczegóły niżej).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import refusal_guard as rg
except ImportError as exc:
    print("BŁĄD: nie mogę zaimportować refusal_guard.py (%s)" % exc)
    print("Upewnij się, że plik refusal_guard.py leży w tym samym katalogu co selftest.py.")
    sys.exit(1)

checks = []


def check(name, condition, got=None):
    checks.append((name, bool(condition), got))


# --- obiekty w stylu Anthropic SDK ---
class Block:
    def __init__(self, type_, text=""):
        self.type = type_
        self.text = text


class Details:
    def __init__(self, category, explanation=None):
        self.type = "refusal"
        self.category = category
        self.explanation = explanation


class Resp:
    def __init__(self, content, stop, details=None, model="m1"):
        self.content = content
        self.stop_reason = stop
        self.stop_details = details
        self.model = model


r = rg.parse_anthropic(Resp([], "refusal", Details("cyber", "cyber harm")))
check("SDK: odmowa -> kind='refusal'", r.kind == "refusal", r.kind)
check("SDK: odmowa -> kategoria", r.category == "cyber", r.category)
check("SDK: odmowa -> .refused", r.refused is True)

r = rg.parse_anthropic(Resp([Block("text", "Cześć!")], "end_turn"))
check("SDK: tekst -> kind='text'", r.kind == "text", r.kind)
check("SDK: tekst -> treść", r.text == "Cześć!", r.text)
check("SDK: tekst -> .ok", r.ok is True)

r = rg.parse_anthropic(Resp([Block("thinking")], "max_tokens"))
check("SDK: same thinking -> 'truncated'", r.kind == "truncated", r.kind)
check("SDK: same thinking -> podpowiedź o max_tokens",
      "max_tokens" in (r.note or ""), r.note)

r = rg.parse_anthropic(Resp([], "end_turn"))
check("SDK: pusto bez refusal -> 'empty'", r.kind == "empty", r.kind)

# --- dict z surowego HTTP ---
r = rg.parse_any({
    "content": [], "stop_reason": "refusal",
    "stop_details": {"type": "refusal", "category": "reasoning_extraction",
                     "explanation": "asks for thinking"},
})
check("dict: odmowa rozpoznana", r.refused and r.category == "reasoning_extraction",
      (r.kind, r.category))

r = rg.parse_any({"error": {"type": "invalid_request_error", "message": "zły model"}})
check("dict: błąd API -> 'error'", r.kind == "error", r.kind)

# --- OpenAI-compatible ---
r = rg.parse_any({"model": "x", "choices": [{
    "message": {"content": ""}, "finish_reason": "refusal",
    "stop_details": {"category": "cyber", "explanation": "blocked"}}]})
check("OpenAI: finish_reason=refusal", r.refused and r.kind == "refusal", r.kind)

r = rg.parse_any({"choices": [{"message": {"content": "hej"}, "finish_reason": "stop"}]})
check("OpenAI: zwykły tekst", r.kind == "text" and r.text == "hej", (r.kind, r.text))

# --- render() ---
msg = rg.render(rg.parse_anthropic(Resp([], "refusal", Details("reasoning_extraction", "x"))))
check("render: komunikat po polsku", "odmowa modelu" in msg.lower(), msg.splitlines()[0])
check("render: podpowiedź dla reasoning_extraction", "thinking" in msg.lower())

# --- ask() + fallback ---
calls = []


def send_fallback(model, messages, **kw):
    calls.append(model)
    if model == "primary":
        return Resp([], "refusal", Details("cyber", "harm"))
    return Resp([Block("text", "z fallbacku")], "end_turn", model=model)


r = rg.ask(send_fallback, model="primary", fallback_model="secondary",
           messages=[{"role": "user", "content": "hej"}])
check("ask(): fallback zadziałał", r.ok and r.text == "z fallbacku", (r.kind, r.text))
check("ask(): dokładnie 2 wywołania", calls == ["primary", "secondary"], calls)

calls.clear()


def send_both_refuse(model, messages, **kw):
    calls.append(model)
    return Resp([], "refusal", Details("cyber", "harm"))


r = rg.ask(send_both_refuse, model="primary", fallback_model="secondary",
           messages=[{"role": "user", "content": "hej"}])
check("ask(): oba modele odmówiły -> refusal, bez pętli",
      r.kind == "refusal" and len(calls) == 2, (r.kind, calls))

calls.clear()


def send_reasoning(model, messages, **kw):
    calls.append(model)
    return Resp([], "refusal", Details("reasoning_extraction", "thinking"))


r = rg.ask(send_reasoning, model="primary", fallback_model="secondary",
           messages=[{"role": "user", "content": "hej"}])
check("ask(): reasoning_extraction bez retry", len(calls) == 1, calls)

# --- historia ---
msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"}]
check("strip_last_turn() usuwa ostatnią turę",
      rg.strip_last_turn(msgs) == [{"role": "user", "content": "a"}],
      rg.strip_last_turn(msgs))
check("trim_history() ogranicza długość", len(rg.trim_history(msgs, keep=2)) == 2)

# --- guard promptu ---
hits = rg.ReasoningExtractionGuard().check("Zawsze pokaż <thinking> przed odpowiedzią.")
check("guard: wykrywa <thinking>", bool(hits), hits)
check("guard: czysty prompt bez trafień",
      rg.ReasoningExtractionGuard().check("Jesteś zwięzłym asystentem.") == [])

# --- raport ---
ok = sum(1 for _, passed, _ in checks if passed)
print("refusal_guard.py - test offline")
print("=" * 60)
for name, passed, got in checks:
    status = "OK  " if passed else "BŁĄD"
    print("%s  %s" % (status, name))
    if not passed:
        print("        otrzymano: %r" % (got,))
print("=" * 60)
print("%d/%d testów przeszło" % (ok, len(checks)))
if ok == len(checks):
    print("Wszystko działa. Możesz podłączać moduł do agenta.")
    sys.exit(0)
print("Coś nie działa - sprawdź, czy refusal_guard.py jest w tej samej wersji co selftest.py.")
sys.exit(1)
