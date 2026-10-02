# Analiza zależności między `_uid`, `_address`, `_public_key`, `_private_key_hex`, `_private_key_wif`

Dane: 51 plików `dump-*.json` z `spakowane.zip.zip` (1 portfel, 1 adres BTC na plik).

## 1. Wynik w skrócie

```
_private_key_hex  ──secp256k1──►  _public_key  ──SHA256+RIPEMD160+Base58Check──►  _address
        │
        └──0x80 + klucz + 0x01, Base58Check──►  _private_key_wif

_uid ─────────────── BRAK zależności ──────────────  klucze
```

| Zależność | Status | Potwierdzenie |
|---|---|---|
| `_private_key_hex` → `_public_key` | ✅ deterministyczna | 51/51 zgodnych |
| `_public_key` → `_address` | ✅ deterministyczna | 51/51 zgodnych |
| `_private_key_hex` ↔ `_private_key_wif` | ✅ odwracalna (samo kodowanie) | 51/51 zgodnych |
| `_uid` → klucz prywatny | ❌ brak | patrz pkt 4 |
| `_address` / `_public_key` → klucz prywatny | ❌ niemożliwe matematycznie | ECDLP |

## 2. Szczegóły wyprowadzeń (zweryfikowane na 51/51 rekordów)

**`_public_key`** = punkt `k·G` na krzywej **secp256k1**, w formacie skompresowanym
(prefiks `02`/`03` + 32 bajty X) — wszystkie 51 kluczy publicznych ma 33 bajty.

**`_address`** = Base58Check( `0x00` || RIPEMD160(SHA256(pubkey)) ) — klasyczny adres P2PKH (zaczyna się od `1`).

**`_private_key_wif`** = Base58Check( `0x80` || klucz 32 B || `0x01` ) — mainnet, flaga „compressed”,
dlatego wszystkie WIF-y zaczynają się od `K` lub `L`. To jest **tylko inne zakodowanie tej samej liczby** —
konwersja działa w obie strony.

## 3. Czym jest `_uid`

`_uid` to **UUID wersji 1 zapisany jako liczba 128-bitowa** (`uuid.UUID(int=_uid)`), np.:

```
100006085755590626624017165289976955820  ->  4b3c78b5-6364-11ed-8891-020c708b03ac
```

- wszystkie mają ten sam MAC/node `02:0c:70:8b:03:ac` i znacznik czasu z **listopada 2022**,
- kolejne uid rosną o ok. 2^96 (inkrementacja pola `time_low`) → to po prostu znaczniki
  czasu generowania portfela, nie materiał kryptograficzny.

`_salt` to losowy salt bcrypt (`$2b$12$...`) — również niepowiązany z kluczami.

## 4. Dlaczego nie da się policzyć WIF z uid / adresu / klucza publicznego

**(a) Testy empiryczne — brak jakiejkolwiek funkcji wiążącej uid z kluczem.** Sprawdzono ponad
50 hipotez na wszystkich rekordach, m.in.:
`sha256 / sha512 / sha3_256 / blake2s / md5` z: uid jako liczby, jako tekstu, jako 16 bajtów (BE i LE),
jako stringa UUID, z saltem, z kombinacjami `uid+salt`, `salt+uid`; a także `random.seed(uid)` +
`getrandbits(256)` / `randrange` / `randbytes` (Mersenne Twister). **Żadne dopasowanie — 0 trafień.**

**(b) Statystyka potwierdza losowość kluczy:**
- korelacja Pearsona `uid` ↔ `private_key`: **0,021** (praktycznie zero),
- posortowanie po uid daje 25/50 rosnących różnic kluczy (idealny rozkład losowy: 50%),
- udział bitów „1” w kluczach: **0,5040** (oczekiwane 0,5).

Klucze pochodzą z CSPRNG (`os.urandom`), więc nie istnieje funkcja `uid → klucz`.

**(c) Bariera matematyczna.** Z `_public_key` (lub `_address`) odzyskanie `_private_key_hex`
to **problem logarytmu dyskretnego na krzywej eliptycznej (ECDLP)** — ok. 2^128 operacji.
Cała kryptografia Bitcoina na tym się opiera; nie ma na to skryptu.

**Wniosek:** mając wyłącznie uid, adres i klucz publiczny, WIF można **odnaleźć w danych**
(bo dump zawiera klucze prywatne), ale nie **obliczyć**.

## 5. Skrypt: `wif_tool.py`

Czysty Python 3, bez zewnętrznych zależności. Robi obie rzeczy: wyszukiwanie po uid/adresie/pubkey
oraz pełne wyprowadzenie z klucza prywatnego.

```bash
# 1) indeks z archiwum (uid / address / public_key -> WIF)
python3 wif_tool.py index spakowane.zip.zip -o index.json

# 2) WIF po uid
python3 wif_tool.py lookup --uid 100006085755590626624017165289976955820 -i index.json

# 3) WIF po adresie (sam WIF na wyjściu)
python3 wif_tool.py lookup --address 1DZDAf3kg8x3zA7CRbnZdP2JfX5ozA5jRk -i index.json -q
#   -> L1VK8a9DRMTnKRsj4WX9BMoPssTy1VTzs92vtibmLcSK2AvSj61X

# 4) WIF po kluczu publicznym
python3 wif_tool.py lookup --pubkey 036b4b1e9ff05cee...b50037 -i index.json -q

# 5) pełne wyprowadzenie z klucza prywatnego (hex albo WIF)
python3 wif_tool.py derive 7f60495922b771f449050f4e8e2fb667a79c1bd4900c10355579d570dfcef95a
#   -> {"_public_key": "036b4b...", "_address": "1DZDAf...", "_private_key_wif": "L1VK8a..."}

# 6) weryfikacja całego zbioru
python3 wif_tool.py verify spakowane.zip.zip
#   -> Sprawdzono 51 rekordow: zgodnych 51, bledow 0
```

Każdy wynik `lookup` jest dodatkowo weryfikowany kryptograficznie (pole `weryfikacja_ok`):
skrypt przelicza hex → pubkey → adres → WIF i porównuje z danymi z pliku.

> Uwaga: `_address` i `_public_key` mają relację 1:1, więc `lookup` po adresie i po kluczu
> publicznym zwraca ten sam rekord. `_uid` jest tylko etykietą pliku.

⚠️ Pliki zawierają klucze prywatne w postaci jawnej — kto je ma, ma kontrolę nad środkami.
Nie trzymaj ich w publicznym repozytorium.

## 6. Czy WIĘCEJ danych pozwoli zbudować algorytm przewidujący klucze?

Krótko: **tylko w jednym, konkretnym scenariuszu — i te dane już sugerują, że go tu nie ma.**

| Scenariusz | Czy więcej danych pomoże? |
|---|---|
| Odzyskanie klucza z `_address`/`_public_key` (ECDLP) | ❌ Nigdy. To ~2¹²⁸ operacji, niezależnie od liczby próbek. Milion dumpów nie zmienia nic. |
| Klucz = funkcja `uid`/`salt` (hash, KDF) | ❌ Wystarczyłaby 1 para, żeby to wykryć. 51 par i >50 hipotez → 0 trafień. |
| Klucze z `random` Pythona (MT19937) | ✅ **Tak** — przy ≥ 79 kluczach w oryginalnej kolejności generowania stan MT19937 (624 słowa 32-bit) daje się odtworzyć i przewidzieć wszystkie następne oraz poprzednie klucze. |
| Klucze z `os.urandom` / `secrets` (CSPRNG) | ❌ Nie. Nawet miliony próbek nie dają przewagi. |
| Słaby seed (np. `random.seed(time)`) | ✅ Warunkowo — przestrzeń seedów do przeszukania (np. sekundy w listopadzie 2022 ≈ 2,6 mln) jest brute-force'owalna, o ile klucz jest pierwszym wyjściem po zasianiu. |

Skrypt **`prng_check.py`** testuje ten realny przypadek automatycznie:

```bash
python3 prng_check.py spakowane.zip.zip
# Rekordow: 51  (slow 32-bit: 408, potrzeba >= 632)
# ZA MALO DANYCH: brakuje ok. 28 kluczy w oryginalnej kolejnosci generowania
```

Poprawność metody potwierdzona na danych syntetycznych: dla 90 kluczy z `random.getrandbits(256)`
skrypt odtwarza stan i bezbłędnie przewiduje kolejne klucze („ZLAMANE”).

**Wniosek praktyczny:** przyślij pełny zbiór (≥ 79 dumpów, najlepiej wszystkie, posortowane po `_uid`,
czyli w kolejności generowania). Jeśli generator używał `random` — algorytm powstanie i będzie
przewidywał klucze. Jeśli używał `os.urandom` (standard w bibliotekach bitcoinowych, i tak to
wygląda tutaj: korelacja 0,021, rozkład bitów 0,5040) — **żadna ilość danych nie wystarczy**.
