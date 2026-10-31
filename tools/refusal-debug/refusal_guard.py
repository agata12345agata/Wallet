"""refusal_guard.py - obsługa odmów modelu (stop_reason == "refusal") dla agenta.

Po co: API potrafi zwrócić HTTP 200 z pustym `content` i `stop_reason: "refusal"`.
Kod, który czyta `content[0]`, wywala się albo pokazuje "[Model nie zwrócił tekstu]".
Ten moduł zamienia każdą odpowiedź w jeden obiekt `Reply` z rozróżnieniem:
    - "text"      -> normalna odpowiedź
    - "truncated" -> ucięte na max_tokens (część tekstu bywa użyteczna)
    - "refusal"   -> model odmówił; jest kategoria i wyjaśnienie
    - "empty"     -> pusto bez refusal (np. same bloki thinking)
    - "error"     -> API zwróciło błąd

Działa z:
    - Anthropic SDK (obiekty z atrybutami),
    - surowym JSON-em z HTTP (dict),
    - bramkami OpenAI-compatible (finish_reason == "refusal" / "content_filter").

Zero zależności zewnętrznych.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "Reply",
    "parse_anthropic",
    "parse_openai",
    "parse_any",
    "render",
    "ask",
    "strip_last_turn",
    "trim_history",
    "ReasoningExtractionGuard",
]

# Kategorie, w których automatyczny retry na innym modelu ma sens.
# "reasoning_extraction" celowo tu nie ma - to błąd promptu, nie przejściówka.
RETRYABLE_CATEGORIES = {"cyber", "bio", "frontier_llm", "general_harms", None}


@dataclass
class Reply:
    kind: str                       # text | truncated | refusal | empty | error
    text: str = ""
    stop_reason: Optional[str] = None
    category: Optional[str] = None
    explanation: Optional[str] = None
    model: Optional[str] = None
    request_id: Optional[str] = None
    blocks: List[str] = field(default_factory=list)
    note: Optional[str] = None      # np. "fallback: claude-..."
    attempts: List["Reply"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind in ("text", "truncated") and bool(self.text)

    @property
    def refused(self) -> bool:
        return self.kind == "refusal"


# --------------------------------------------------------------------------
# pomocnicze: SDK zwraca obiekty, HTTP zwraca dicty - obsłuż oba
# --------------------------------------------------------------------------
def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text_from_blocks(blocks: Iterable[Any]) -> str:
    parts = []
    for block in blocks or []:
        if _get(block, "type") == "text":
            parts.append(_get(block, "text", "") or "")
    return "".join(parts)


def _explain(category: Optional[str], explanation: Optional[str]) -> str:
    if explanation:
        return explanation
    if category:
        return "Zażądanie zostało odrzucone w kategorii: %s." % category
    return "Żądanie zostało odrzucone przez klasyfikator bezpieczeństwa."


# --------------------------------------------------------------------------
# parsery
# --------------------------------------------------------------------------
def parse_anthropic(resp: Any, request_id: Optional[str] = None) -> Reply:
    """Zamienia odpowiedź Messages API (SDK albo dict) w Reply."""
    content = _get(resp, "content") or []
    blocks = [_get(b, "type") for b in content]
    stop = _get(resp, "stop_reason")
    model = _get(resp, "model")
    text = _text_from_blocks(content)

    if stop == "refusal":
        details = _get(resp, "stop_details") or {}
        category = _get(details, "category")
        explanation = _get(details, "explanation")
        return Reply(
            kind="refusal",
            text="",
            stop_reason=stop,
            category=category,
            explanation=_explain(category, explanation),
            model=model,
            request_id=request_id,
            blocks=blocks,
        )

    if stop == "max_tokens":
        return Reply(
            kind="truncated",
            text=text,
            stop_reason=stop,
            model=model,
            request_id=request_id,
            blocks=blocks,
            note="odpowiedź ucięta na max_tokens" if text else
                 "max_tokens zjedzone przez bloki thinking - zwiększ limit",
        )

    if stop is None and not content:
        return Reply(kind="error", stop_reason=stop, model=model,
                     request_id=request_id, note="odpowiedź bez content i bez stop_reason")

    if not text:
        return Reply(kind="empty", stop_reason=stop, model=model,
                     request_id=request_id, blocks=blocks,
                     note="są bloki %s, ale żaden nie jest tekstem" % (blocks or "[]"))

    return Reply(kind="text", text=text, stop_reason=stop, model=model,
                 request_id=request_id, blocks=blocks)


def parse_openai(resp: Any, request_id: Optional[str] = None) -> Reply:
    """Dla bramek OpenAI-compatible: finish_reason 'refusal' / 'content_filter'."""
    choices = _get(resp, "choices") or []
    if not choices:
        err = _get(resp, "error") or {}
        return Reply(kind="error", request_id=request_id,
                     explanation=_get(err, "message") or "brak choices w odpowiedzi")
    choice = choices[0]
    message = _get(choice, "message") or {}
    content = _get(message, "content")
    if isinstance(content, list):                # format blokowy
        content = _text_from_blocks(content)
    content = content or ""
    finish = _get(choice, "finish_reason")
    model = _get(resp, "model")

    if finish in ("refusal", "content_filter"):
        details = _get(choice, "stop_details") or _get(resp, "stop_details") or {}
        category = _get(details, "category")
        explanation = _get(details, "explanation") or _get(choice, "refusal_reason")
        return Reply(kind="refusal", stop_reason=finish, category=category,
                     explanation=_explain(category, explanation), model=model,
                     request_id=request_id, blocks=["text"] if content else [])

    if finish == "length":
        return Reply(kind="truncated", text=content, stop_reason=finish, model=model,
                     request_id=request_id, note="ucięte na limicie tokenów")

    if not content:
        return Reply(kind="empty", stop_reason=finish, model=model,
                     request_id=request_id, note="pusta treść bez refusal")

    return Reply(kind="text", text=content, stop_reason=finish, model=model,
                 request_id=request_id)


def parse_any(resp: Any, request_id: Optional[str] = None) -> Reply:
    """Rozpoznaje format: Anthropic (content/stop_reason) albo OpenAI (choices)."""
    if _get(resp, "choices") is not None:
        return parse_openai(resp, request_id)
    return parse_anthropic(resp, request_id)


# --------------------------------------------------------------------------
# komunikat dla użytkownika
# --------------------------------------------------------------------------
def render(reply: Reply, verbose: bool = False) -> str:
    """Tekst do pokazania w konsoli/czacie."""
    if reply.kind == "text":
        return reply.text

    if reply.kind == "truncated":
        body = reply.text or ""
        suffix = "\n\n[uwaga: %s]" % (reply.note or "odpowiedź ucięta")
        return (body + suffix) if body else "[%s]" % (reply.note or "odpowiedź ucięta")

    if reply.kind == "refusal":
        lines = ["[odmowa modelu] %s" % reply.explanation]
        if reply.category:
            lines.append("kategoria: %s" % reply.category)
        if reply.category == "reasoning_extraction":
            lines.append(
                "wskazówka: to blokada promptu - usuń prośby o <thinking> / "
                "'pokaż rozumowanie' / pole 'reasoning' w JSON-ie."
            )
        elif reply.category in ("cyber", "general_harms"):
            lines.append(
                "wskazówka: często false positive - złagodź słownictwo w system "
                "prompcie i opisach narzędzi, usuń podejrzane fragmenty historii."
            )
        if reply.note:
            lines.append(reply.note)
        if verbose and reply.request_id:
            lines.append("request-id: %s" % reply.request_id)
        return "\n".join(lines)

    if reply.kind == "empty":
        return "[pusta odpowiedź] %s" % (reply.note or "model nie zwrócił tekstu")

    return "[błąd API] %s" % (reply.explanation or reply.note or "nieznany błąd")


# --------------------------------------------------------------------------
# wywołanie z fallbackiem
# --------------------------------------------------------------------------
SendFn = Callable[..., Any]


def ask(
    send: SendFn,
    *,
    model: str,
    messages: Sequence[Dict[str, Any]],
    fallback_model: Optional[str] = None,
    system: Optional[Any] = None,
    tools: Optional[Sequence[Dict[str, Any]]] = None,
    max_tokens: int = 1024,
    retry_reasoning_extraction: bool = False,
    on_refusal: Optional[Callable[[Reply], None]] = None,
    **extra: Any,
) -> Reply:
    """Wywołuje model i zwraca Reply. `send` to Twoja funkcja wołająca API.

    `send` musi przyjmować keywordy: model, messages, system, tools, max_tokens
    (i dowolne `**extra`, które przekażesz) oraz zwracać surową odpowiedź.
    """

    def call(target_model: str) -> Reply:
        kwargs = dict(model=target_model, messages=list(messages),
                      max_tokens=max_tokens, **extra)
        if system is not None:
            kwargs["system"] = system
        if tools is not None:
            kwargs["tools"] = tools
        raw = send(**kwargs)
        request_id = None
        if isinstance(raw, dict):
            request_id = raw.get("_request_id")
        reply = parse_any(raw, request_id=request_id)
        reply.model = reply.model or target_model
        return reply

    first = call(model)
    if on_refusal and first.kind == "refusal":
        on_refusal(first)
    if first.kind != "refusal" or not fallback_model or fallback_model == model:
        return first

    category = first.category
    if category == "reasoning_extraction" and not retry_reasoning_extraction:
        first.note = ("nie ponawiam na innym modelu - w tej kategorii winny jest "
                      "prompt, nie model")
        return first
    if category not in RETRYABLE_CATEGORIES and category is not None:
        return first

    second = call(fallback_model)
    if second.kind != "refusal":
        second.note = "odpowiedź z modelu zapasowego: %s" % fallback_model
        second.attempts = [first, second]
        return second

    if on_refusal:
        on_refusal(second)
    first.note = "model zapasowy (%s) też odmówił" % fallback_model
    first.attempts = [first, second]
    return first


# --------------------------------------------------------------------------
# porządki w historii po odmowie
# --------------------------------------------------------------------------
def strip_last_turn(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Usuwa ostatnią turę (user + assistant). Odmówiona treść nie zostaje w historii."""
    out = list(messages)
    removed = 0
    while out and removed < 2 and _get(out[-1], "role") in ("user", "assistant"):
        out.pop()
        removed += 1
    return out


def trim_history(messages: Sequence[Dict[str, Any]], keep: int = 6) -> List[Dict[str, Any]]:
    """Zostawia tylko ostatnie `keep` wiadomości - mniejsza powierzchnia dla klasyfikatora."""
    msgs = list(messages)
    return msgs[-keep:] if keep > 0 else []


class ReasoningExtractionGuard:
    """Wykrywa prośby o rozumowanie w prompcie ZANIM wyślesz żądanie.

    To najczęstsza przyczyna odmowy przy zwykłym 'hej': system prompt każe
    modelowi pokazywać <thinking> albo wypełniać pole 'reasoning' w JSON-ie.
    """

    PATTERNS = (
        "<thinking>", "</thinking>", "<reasoning>", "<scratchpad>",
        "pokaż swoje rozumowanie", "pokaż rozumowanie", "krok po kroku pokaż myślenie",
        "show your reasoning", "show your thinking", "reveal your reasoning",
        "chain of thought", "chain-of-thought", "myślenie krok po kroku",
        "pole \"reasoning\"", "\"reasoning\":", "\"trace\":", "\"thinking\":",
        "internal notes", "notatki wewnętrzne",
    )

    def __init__(self, patterns: Iterable[str] = PATTERNS):
        self.patterns = tuple(p.lower() for p in patterns)

    def find(self, text: str) -> List[str]:
        low = (text or "").lower()
        return [p for p in self.patterns if p in low]

    def check(self, *texts: Optional[str]) -> List[str]:
        hits: List[str] = []
        for text in texts:
            if not text:
                continue
            if isinstance(text, (list, tuple)):
                text = "\n".join(str(t) for t in text)
            hits.extend(self.find(str(text)))
        return sorted(set(hits))
