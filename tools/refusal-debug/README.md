# `DEBUG STOP: refusal` / `[Model nie zwrócił tekstu]` — co to jest i jak naprawić

## 1. Krótka diagnoza

To, co widzisz:

```
DEBUG STOP: refusal
DEBUG BLOCKS: []
HAJKA AI > [Model nie zwrócił tekstu]
```

nie jest awarią sieci, złym kluczem API ani błędem Twojego kodu w warstwie transportu.
To **poprawna odpowiedź HTTP 200**, w której model odmówił wygenerowania treści:

```json
{
  "content": [],
  "stop_reason": "refusal",
  "stop_details": {
    "type": "refusal",
    "category": "cyber",
    "explanation": "This request was declined because it could enable cyber harm."
  },
  "usage": { "input_tokens": 412, "output_tokens": 0 }
}
```

Trzy rzeczy, które wynikają z tego wprost:

| Co widzisz | Co to znaczy |
|---|---|
| `DEBUG STOP: refusal` | `stop_reason` z API — klasyfikator bezpieczeństwa odrzucił **całe żądanie**, nie pojedyncze zdanie odpowiedzi |
| `DEBUG BLOCKS: []` | `content` jest pustą listą — model nie wyprodukował ani jednego bloku (nawet `thinking`) |
| `[Model nie zwrócił tekstu]` | to Twój własny fallback w kodzie, który zakłada „pusto = coś się zepsuło”. Nie zepsuło się — odmowa jest legalnym wynikiem |

Kluczowy szczegół: `refusal` to **nie wyjątek**. `try/except` wokół wywołania API nic nie złapie.
Trzeba sprawdzić `stop_reason` *przed* czytaniem `content`.

Dlaczego „hej” może zostać odrzucone? Bo klasyfikator widzi **całe żądanie**: system prompt,
definicje narzędzi, historię rozmowy, załączone pliki i skille. Jedno słowo „hej” jest
nieszkodliwe — ale razem z resztą kontekstu już nie musi być. W praktyce odmowa przy
„hej” niemal zawsze oznacza, że problem siedzi w system prompcie, w opisach narzędzi
albo w historii rozmowy (albo w proxy, przez które idziesz do API).

## 2. Co zrobić w 3 krokach

### Krok 1 — odczytaj `stop_details`

Bez tego zgadujesz. `category` mówi, **który** classifier zadziałał:

| `category` | Co go zwykle odpala |
|---|---|
| `cyber` | słownictwo: exploit, shell, exec, SSH, reverse engineering, skany, malware, krypto-portfele. Bardzo często **false positive** przy normalnej pracy infrastrukturalnej |
| `reasoning_extraction` | prompt prosi o `<thinking>`, „pokaż swoje rozumowanie”, pole `reasoning`/`trace` w JSON-ie, cytowanie wewnętrznych notatek. **To zawsze wina promptu** i nie ma fallbacku — trzeba przeredagować |
| `frontier_llm` | treści o trenowaniu/destylacji modeli, datasetach |
| `bio` | treści biologiczne / laboratoryjne |
| `general_harms` | pozostałe obszary polityki użycia |
| `null` | odmowa bez przypisanej kategorii — traktuj jak ogólną |

`explanation` jest dla człowieka — **wyświetlaj je, nie parsuj** (tekst nie jest stabilny).

### Krok 2 — ustal, która część żądania blokuje

Uruchom dołączony skrypt. Wysyła on serię żądań: od gołego „hej”, przez „hej + system prompt”,
„hej + narzędzia”, „historia + hej”, aż po pełny zestaw — i pokazuje, przy którym pojawia się odmowa.

```bash
cd tools/refusal-debug
export ANTHROPIC_API_KEY=...        # albo ANTHROPIC_AUTH_TOKEN

python3 diagnose_refusal.py \
  --model TWOJ_MODEL \
  --system-file system.txt \
  --history-file history.json \
  --tools-file tools.json \
  --save refusal-report.json
```

- `system.txt` — dokładnie ten system prompt, którego używa agent.
- `history.json` — lista `messages` z poprzednich tur, np. `[{"role":"user","content":"..."}]`.
- `tools.json` — lista definicji narzędzi.
- `--stream` — jeśli agent czyta odpowiedź strumieniowo (klasyfikator w streamie zachowuje się tak samo, ale warto sprawdzić tę samą ścieżkę).
- `--base-url` — jeśli idziesz przez proxy/gateway, wskaż je tutaj; potem powtórz test bezpośrednio na `https://api.anthropic.com` z tym samym kluczem. Jeśli przez proxy jest odmowa, a bezpośrednio nie ma — winne jest proxy.

Skrypt kończy się kodem `1`, gdy cokolwiek zostało odrzucone, i wypisuje werdykt: który probe
zadziałał i jaka kategoria.

### Krok 3 — napraw przyczynę, nie objaw

- **`reasoning_extraction`** → usuń z promptu prośby o `<thinking>` / „pokaż rozumowanie” / pola
  `reasoning` w JSON-ie. Zamiast tego poproś o krótkie uzasadnienie odpowiedzi albo użyj
  natywnych bloków `thinking` z `display: "summarized"`.
- **`cyber` / `general_harms`** → złagodź słownictwo w system prompcie i opisach narzędzi,
  dopisz kontekst legalności („to audyt własnego portfela, dane należą do użytkownika”),
  usuń fragmenty historii, które wyglądają jak instrukcje ofensywne. Jeśli to oczywisty
  false positive — poproś o wsparcie, podając `request-id` z odpowiedzi.
- **`frontier_llm` / `bio`** → zwykle trzeba po prostu przeredagować treść.

> **Nie ponawiaj tego samego żądania.** Odmówiona tura zostaje w historii rozmowy i przy
> kolejnym wywołaniu klasyfikator zadziała znowu. Retry „z mocniejszym promptem” też nie pomaga.
> Albo usuń/przeredaguj winny fragment historii, albo przełącz się na inny model.

## 3. Poprawka w kodzie agenta

Minimalna, poprawna obsługa — zamiast `[Model nie zwrócił tekstu]` agent powinien powiedzieć
użytkownikowi, *co* się stało:

```python
FALLBACK_MODEL = "..."  # inny model tego samego dostawcy, dozwolony w Twoim produkcie

def ask_agent(client, messages, model, system=None, tools=None, max_tokens=1024):
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    resp = client.messages.create(**kwargs)

    # 1. Odmowa: HTTP 200, puste content, stop_reason == "refusal"
    if resp.stop_reason == "refusal":
        details = resp.stop_details            # może być None -> traktuj jak ogólną odmowę
        category = getattr(details, "category", None)
        explanation = getattr(details, "explanation", None) or "Model odmówił odpowiedzi."
        print(f"[refusal] kategoria={category!r}: {explanation}")

        # 2. Jedna próba na innym modelu - z tą samą treścią, ale innym modelem.
        if FALLBACK_MODEL and model != FALLBACK_MODEL:
            kwargs["model"] = FALLBACK_MODEL
            retry = client.messages.create(**kwargs)
            if retry.stop_reason != "refusal":
                return text_of(retry)

        # 3. Nadal nic: powiedz prawdę i NIE zapętlaj.
        return REFUSAL_MESSAGE.format(category=category, explanation=explanation)

    # 4. Inne stop_reason też trzeba obsłużyć - "pusto" nie zawsze znaczy "refusal"
    if resp.stop_reason == "max_tokens":
        return "[Odpowiedź ucięta na max_tokens - zwiększ limit]"
    if resp.stop_reason == "pause_turn":
        ...  # dołóż content i wywołaj ponownie

    text = text_of(resp)
    if not text:
        # bywa przy samych blokach "thinking" - nie udawaj refusal
        return "[Model zwrócił tylko bloki thinking - zwiększ max_tokens]"
    return text


def text_of(resp):
    return "".join(b.text for b in resp.content if b.type == "text")
```

Wersja dla surowego HTTP (bez SDK) — pole `stop_details` bywa nieobecne w JSON-ie:

```python
data = response.json()
if data.get("stop_reason") == "refusal":
    d = data.get("stop_details") or {}
    print("refusal:", d.get("category"), "-", d.get("explanation"))
```

## 4. Inne przyczyny pustej odpowiedzi (żeby nie mylić ich z refusal)

| Objaw | Prawdziwa przyczyna |
|---|---|
| `stop_reason: "refusal"`, `content: []` | klasyfikator bezpieczeństwa — sprawa z tego dokumentu |
| `stop_reason: "max_tokens"`, brak bloku `text` | `max_tokens` zjadły bloki `thinking`; zwiększ limit |
| `stop_reason: "end_turn"`, `content` pełne, ale tekst pusty | filtr w Twoim kodzie: czytasz tylko `type == "text"` i gubisz resztę |
| `finish_reason: "refusal"` / pusty `content` w trybie OpenAI-compatible | ten sam refusal, tylko zmapowany przez gateway — obsłuż oba pola |
| HTTP 200, `content: []`, ale `stop_reason` inny niż `refusal` | zwykle błąd proxy/przekierowania — porównaj z `request-id` |

## 5. Notatki o kosztach

- Odmowa **przed** wygenerowaniem czegokolwiek jest rozliczana tylko w kategoriach
  `bio`, `frontier_llm` i `reasoning_extraction` (stan na wrzesień 2026). W `cyber`,
  `general_harms` i przy `null` — nie jest.
- Każda odmowa **liczy się do limitów zapytań** (rate limit), nawet niepłatna.
- Odmowa w środku streamu: to, co już wyciekło, jest niekompletne — **odrzuć** częściową
  odpowiedź, nie pokazuj jej użytkownikowi.

## 6. Checklist

- [ ] Logujesz `stop_reason`, `stop_details.category`, `stop_details.explanation` i `request-id`.
- [ ] Twoja pętla sprawdza `stop_reason` **przed** `content[0]`.
- [ ] Komunikat dla użytkownika pokazuje kategorię i wyjaśnienie, a nie „model nic nie zwrócił”.
- [ ] Nie ma automatycznego retry tego samego żądania w pętli.
- [ ] Jest jeden fallback na inny model (albo świadoma decyzja, że go nie ma).
- [ ] Historię rozmowy można wyczyścić / skrócić po odmowie.
- [ ] `reasoning_extraction` traktujesz jako błąd promptu, nie jako przejściówkę.

## Źródła

- Anthropic — *Refusals and fallback*: <https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback>
- Anthropic — *Stop reasons*: <https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons>
- Anthropic — *Fallback credit*: <https://platform.claude.com/docs/en/build-with-claude/fallback-credit>
