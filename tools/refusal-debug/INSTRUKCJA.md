# Instrukcja krok po kroku — obsługa odmów w agencie HAJKA AI

Czas: ~10 minut. Nie musisz znać się na API — każdy krok ma komendę do wklejenia
i informację, **co powinieneś zobaczyć**. Jeśli wynik się nie zgadza, pod krokiem jest
„Jeśli nie działa”.

Zakładamy: Debian, agent w Pythonie 3, plik agenta gdzieś w katalogu domowym.

---

## Krok 0 — co robimy i dlaczego

Twój agent dostaje od API odpowiedź **HTTP 200** z pustą treścią i `stop_reason: "refusal"`.
To nie błąd — to odmowa modelu. Twój kod tego nie rozpoznaje i dlatego wypisuje
`[Model nie zwrócił tekstu]`.

Poprawka to trzy rzeczy:

1. rozpoznać odmowę **zanim** kod sięgnie po `content[0]`,
2. pokazać użytkownikowi kategorię i wyjaśnienie zamiast „model nic nie zwrócił”,
3. usunąć odrzuconą wiadomość z historii, żeby nie blokowała kolejnych tur.

Zrobimy to modułem `refusal_guard.py`, który już jest gotowy i przetestowany.

---

## Krok 1 — pobierz pliki na Debiana

Jeśli masz już sklonowane repo `Wallet`:

```bash
cd ~/Wallet
git fetch origin arena/e9d2fd92-wallet
git checkout arena/e9d2fd92-wallet
git pull origin arena/e9d2fd92-wallet
ls tools/refusal-debug
```

Jeśli repo nie masz:

```bash
cd ~
git clone -b arena/e9d2fd92-wallet https://github.com/agata12345agata/Wallet.git
ls ~/Wallet/tools/refusal-debug
```

**Powinieneś zobaczyć** cztery pliki: `diagnose_refusal.py`, `example_loop.py`,
`refusal_guard.py`, `selftest.py` (oraz `README.md`).

> Jeśli `git` nie jest zainstalowany: `sudo apt install -y git python3`.

---

## Krok 2 — skopiuj moduł do katalogu agenta

Znajdź katalog, w którym leży plik Twojego agenta (ten, co wypisuje `TY >`):

```bash
grep -rln "HAJKA AI" ~ --include="*.py" 2>/dev/null
grep -rln "DEBUG STOP" ~ --include="*.py" 2>/dev/null
```

Pierwsza komenda powinna wskazać ścieżkę, np. `/home/twojuser/hajka/agent.py`.
Skopiuj moduł **do tego samego katalogu**:

```bash
cp ~/Wallet/tools/refusal-debug/refusal_guard.py ~/hajka/
cp ~/Wallet/tools/refusal-debug/selftest.py ~/hajka/
```

**Powinieneś zobaczyć:** brak komunikatu = sukces.

**Jeśli nie działa:** `ls ~/hajka` — sprawdź, czy `refusal_guard.py` tam jest.

---

## Krok 3 — sprawdź, czy Python widzi moduł

```bash
cd ~/hajka
python3 -c "import refusal_guard; print('import OK')"
```

**Powinieneś zobaczyć:** `import OK`

**Jeśli nie działa:**

| Komunikat | Co zrobić |
|---|---|
| `ModuleNotFoundError: No module named 'refusal_guard'` | jesteś w złym katalogu — `cd` do katalogu agenta i powtórz |
| `SyntaxError` | masz starszy Python — sprawdź `python3 -V` (potrzebny 3.8+) |
| `ImportError: cannot import name` | podmieniony plik jest niekompletny — skopiuj go ponownie z kroku 2 |

---

## Krok 4 — test offline (bez API, bez kosztów)

```bash
cd ~/hajka
python3 selftest.py
```

**Powinieneś zobaczyć:**

```
23/23 testów przeszło
Wszystko działa. Możesz podłączać moduł do agenta.
```

Jeśli którykolwiek test pokaże `BŁĄD`, **nie przechodź dalej** — wklej mi ten fragment
albo skopiuj plik jeszcze raz.

---

## Krok 5 — zrób kopię zapasową pliku agenta

```bash
cp ~/hajka/agent.py ~/hajka/agent.py.bak
```

To jest Twoja droga powrotu, jeśli coś pójdzie nie tak:

```bash
cp ~/hajka/agent.py.bak ~/hajka/agent.py
```

---

## Krok 6 — znajdź miejsce do zmiany

Otwórz plik agenta w edytorze (`nano ~/hajka/agent.py`) i znajdź linijkę, która drukuje
debugowanie odmowy. Szukaj w edytorze `Ctrl+W` frazy:

```
DEBUG STOP
```

albo:

```
nie zwrócił tekstu
```

Zobaczysz coś w tym stylu:

```python
while True:
    line = input("TY > ")
    history.append({"role": "user", "content": line})

    # --- Twój obecny kod ---
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=history,
    )

    print("DEBUG STOP:", response.stop_reason)
    print("DEBUG BLOCKS:", response.content)

    if not response.content:
        print("HAJKA AI > [Model nie zwrócił tekstu]")
        continue

    text = response.content[0].text
    print("HAJKA AI >", text)
```

Fragment jest pokazany **razem z pętlą**, żeby było jasne, jakie ma być wcięcie
(w Twoim pliku pętla już istnieje — nie dodawaj drugiej).

**Nie zmieniaj jeszcze nic** — najpierw dodaj import na górze pliku.

---

## Krok 7 — dodaj import

Na samej górze pliku agenta (zaraz po `import ...`):

```python
import refusal_guard as rg
```

**Sprawdź:** uruchom agenta i od razu zakończ (`/quit`). Jeśli nie ma błędu
`ModuleNotFoundError`, import działa.

---

## Krok 8 — wariant minimalny (3 linie)

Podmień blok `if not response.content:` na to:

```python
while True:
    line = input("TY > ")
    history.append({"role": "user", "content": line})

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=history,
    )

    # --- nowy kod: najpierw sprawdź stop_reason ---
    reply = rg.parse_any(response)

    if reply.refused:
        print("HAJKA AI >", rg.render(reply))
        history = rg.strip_last_turn(history)
        continue

    print("HAJKA AI >", reply.text)
    history.append({"role": "assistant", "content": reply.text})
```

I **usuń** dwie linijki `print("DEBUG STOP:", ...)` oraz `print("DEBUG BLOCKS:", ...)`.

Cały fragment ma teraz wyglądać tak:

```python
while True:
    line = input("TY > ")
    history.append({"role": "user", "content": line})

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=history,
    )

    reply = rg.parse_any(response)

    if reply.refused:
        print("HAJKA AI >", rg.render(reply))
        history = rg.strip_last_turn(history)
        continue

    print("HAJKA AI >", reply.text)
    history.append({"role": "assistant", "content": reply.text})
```

Zapisz plik (`Ctrl+O`, `Enter`, `Ctrl+X` w nano).

---

## Krok 9 — uruchom agenta i sprawdź

```bash
cd ~/hajka
python3 agent.py
```

Wpisz `hej`.

**Możliwe wyniki:**

| Co widzisz | Co to znaczy |
|---|---|
| `HAJKA AI > Cześć! ...` | działa, odmowa znikła |
| `HAJKA AI > [odmowa modelu] ... kategoria: ...` | **też dobrze** — to jest nowa, poprawna obsługa. Problem był w prompcie, przejdź do kroku 12 |
| nadal `[Model nie zwrócił tekstu]` | Twoja zmiana nie została wczytana — sprawdź, czy edytujesz ten sam plik, który uruchamiasz (`grep -n "Model nie zwrócił" ~/hajka/*.py`) |

---

## Krok 10 — dodaj fallback (opcjonalny, ale zalecany)

Fallback = gdy główny model odmówi, agent **raz** pyta inny model. Nie pętli się.

Ustaw model zapasowy jako zmienną środowiskową — musi to być **inny** model,
do którego Twój klucz ma dostęp (sprawdź w panelu swojego dostawcy):

```bash
export FALLBACK_MODEL="tu-wpisz-id-innego-modelu"
```

Sprawdź, że zmienna jest ustawiona:

```bash
echo $FALLBACK_MODEL
```

W pliku agenta **zastąp całe wywołanie** `client.messages.create(...)` tym kodem:

```python
import os

def send(model, messages, max_tokens=1024, system=None, tools=None, **kw):
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system is not None:
        kwargs["system"] = system
    if tools is not None:
        kwargs["tools"] = tools
    return client.messages.create(**kwargs)


while True:
    line = input("TY > ")
    history.append({"role": "user", "content": line})

    reply = rg.ask(
        send,
        model=MODEL,
        messages=history,
        fallback_model=os.environ.get("FALLBACK_MODEL"),
        max_tokens=2048,
    )

    if reply.refused:
        print("HAJKA AI >", rg.render(reply))
        history = rg.strip_last_turn(history)
        continue

    print("HAJKA AI >", reply.text)
    history.append({"role": "assistant", "content": reply.text})
```

> Uwaga: jeśli w Twoim agencie wywołanie ma dodatkowe parametry (`system`, `tools`,
> `temperature`), dopisz je do funkcji `send` w ten sam sposób.

---

## Krok 11 — sprawdź, że historia się czyści

Po odmowie Twój agent powinien powiedzieć, że usunął wiadomość z historii — w wariancie
minimalnym robi to `rg.strip_last_turn(history)`.

**Test bez API:** w konsoli Pythona:

```bash
cd ~/hajka
python3 -c "
import refusal_guard as rg
h = [{'role':'user','content':'a'},{'role':'assistant','content':'b'},{'role':'user','content':'c'}]
print(rg.strip_last_turn(h))
"
```

**Powinieneś zobaczyć:** `[{'role': 'user', 'content': 'a'}]`

---

## Krok 12 — jeśli odmowa nadal występuje

Teraz masz już komunikat z kategorią. Uruchom diagnostykę, żeby dowiedzieć się,
**co dokładnie** blokuje:

```bash
cd ~/Wallet/tools/refusal-debug
export ANTHROPIC_API_KEY="twoj-klucz"

python3 diagnose_refusal.py \
  --model TWOJ_MODEL \
  --system-file ~/hajka/system.txt
```

- `system.txt` to plik z Twoim system promptem (skopiuj go z kodu agenta, jeśli jest
  wpisany na sztywno).
- Jeśli agent używa narzędzi: dorzuć `--tools-file tools.json`.
- Jeśli agent wysyła historię: dorzuć `--history-file history.json`.
- Jeśli idziesz przez proxy: dorzuć `--base-url https://twoje-proxy/...`.

Skrypt powie na końcu, który element żądania wywołuje odmowę, np.:

```
Odmowa pojawia się dopiero przy pełnym żądaniu. Winowajca:
  -> 3. 'hej' + system prompt agenta
  -> kategoria: 'reasoning_extraction'
```

Wtedy zgodnie z tabelą w `README.md` (sekcja 2) poprawiasz **prompt**, nie kod.

---

## Krok 13 — szybkie sprawdzenie system promptu

Najczęstsza przyczyna odmowy przy „hej”: prompt każe modelowi pokazywać rozumowanie.

```bash
cd ~/hajka
python3 -c "
import os, refusal_guard as rg
prompt = open(os.path.expanduser('~/hajka/system.txt'), encoding='utf-8').read()
print(rg.ReasoningExtractionGuard().check(prompt))
"
```

Jeśli wypisze np. `['<thinking>']` — to jest przyczyna. Usuń z promptu prośby o
`<thinking>`, „pokaż swoje rozumowanie”, pola `reasoning`/`trace`.

---

## Krok 14 — porządki

Gdy wszystko działa:

```bash
cd ~/hajka
rm agent.py.bak          # opcjonalnie, po kilku dniach testów
```

Możesz też zostawić sobie diagnostykę w repo i wrócić do niej, gdy odmowa pojawi się
przy innej treści.

---

## Najczęstsze problemy

| Problem | Przyczyna | Rozwiązanie |
|---|---|---|
| `ModuleNotFoundError: refusal_guard` | moduł nie leży obok agenta | `cp` do katalogu agenta (krok 2) |
| `AttributeError: 'dict' object has no attribute ...` | mieszasz SDK i JSON | używaj `rg.parse_any(response)` — obsługuje oba |
| `SyntaxError: 'continue' not properly in loop` | wkleiłeś fragment poza pętlą `while` | wklej kod **wewnątrz** istniejącej pętli agenta, z takim wcięciem jak w kroku 8 |
| Odmowa dalej przy każdym „hej” | prompt / narzędzia / historia | krok 12 i 13 |
| Komunikat nadal `[Model nie zwrócił tekstu]` | edytujesz inny plik niż uruchamiasz | `grep -rn "nie zwrócił" ~/hajka` |
| `[odmowa modelu]` przy każdej wiadomości | jeden element promptu blokuje wszystko | krok 12, potem edycja promptu |
| Rozmowa „psuje się” po jednej odmowie | historia nie jest czyszczona | dodaj `history = rg.strip_last_turn(history)` |
| Fallback nie działa | `FALLBACK_MODEL` pusty albo ten sam model | `echo $FALLBACK_MODEL`, musi być inny model |

---

## Ściągawka: co jest w katalogu

| Plik | Kiedy używasz |
|---|---|
| `refusal_guard.py` | zawsze — kopiujesz do agenta |
| `selftest.py` | raz, po skopiowaniu (krok 4) |
| `diagnose_refusal.py` | gdy odmowa nie znika (krok 12) |
| `example_loop.py` | wzór całej pętli z obsługą odmów |
| `README.md` | pełne wyjaśnienie kategorii i zasad |
| `INSTRUKCJA.md` | ten plik |
