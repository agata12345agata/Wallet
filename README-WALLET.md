# Wallet.dat — analiza pliku i narzędzie eksportu

## Wniosek w jednym zdaniu

**`wallet.dat` z tego repozytorium nie jest prawdziwym portfelem Bitcoin — nie ma z niego czego
eksportować, żadne hasło tego nie zmieni.** Poniżej dowody, a dalej gotowe narzędzie, które
zrobi eksport z prawdziwego pliku.

---

## 1. Dowody, że plik jest wypełniaczem, nie portfelem

Sprawdzone bez znajomości hasła, na danych publicznych — same klucze publiczne i struktura rekordów.

| Test | Wynik | Co to znaczy |
|---|---|---|
| Klucze publiczne leżą na krzywej secp256k1 | **44 z 173** | 129 „kluczy" to losowe bajty. Prawdziwy klucz zawsze leży na krzywej; losowe bajty trafiają na nią mniej więcej w połowie przypadków. |
| Unikalne adresy z poprawnych kluczy | **28** | Tyle tylko da się wyprowadzić sensownie. Reszta adresów nie istnieje. |
| Struktura rekordu `mkey` (sól + liczba iteracji PBKDF2) | **brak** | W zaszyfrowanym portfelu Bitcoin Core jest tam 48-bajtowy klucz, 8-bajtowa sól, metoda wyprowadzania i liczba iteracji. W tym pliku w tych miejscach leżą przypadkowe bajty. |
| Stałe fragmenty w rekordach `ckey` | `51 00 01 30` w **163 z 172** rekordów | W prawdziwym portfelu po kluczu publicznym jest od razu długość zaszyfrowanego klucza. Powtarzalny szablon = plik generowany skryptem. |

Wniosek: rekordy `ckey` są wygenerowane z szablonu i losowych bajtów. Nie istnieje klucz
prywatny, który dałoby się z nich odszyfrować — bo nigdy go tam nie było.

**Poprawka do tego, co powiedziałem wcześniej:** podałem „108 unikalnych adresów" z tego pliku.
To było błędne — liczyłem adresy także z kluczy nieleżących na krzywej. Poprawna liczba to
**28 adresów z 44 poprawnych kluczy**.

## 2. Narzędzie: `wallet_export.py`

```bash
python3 wallet_export.py --inventory          # tylko adresy publiczne, bez hasła
python3 wallet_export.py --export out.json    # eksport kluczy, pyta o hasło
```

- Hasło jest pobierane przez `getpass` w terminalu. Nie ma go w kodzie, w historii powłoki ani
  w tym pliku. Nie wysyłaj hasła nikomu, także mnie.
- Tryb `--inventory` nie dotyka niczego prywatnego. Odrzuca klucze nieleżące na krzywej i podaje,
  ile odrzucił — to on wykrył, że ten plik jest wypełniaczem.
- Tryb `--export` **dowodzi poprawności każdego klucza**: odszyfrowany klucz prywatny musi dać
  dokładnie ten klucz publiczny, który jest zapisany w tym samym rekordzie. Rekord bez dowodu
  nie trafia do wyniku. Nie ma tu miejsca na „chyba się udało".
- `--deep` uruchamia szeroki skan układu pól (wolny), gdy układ nietypowy.

### Jak sprawdziłem, że narzędzie działa

- **Wektory testowe Bitcoina** (klucz prywatny = 1): adres `1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH`,
  WIF `KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn` — zgodne znak w znak, także dla
  wersji nieskompresowanej.
- **Portfel syntetyczny** zbudowany specjalnie do testu: narzędzie odzyskało sól i liczbę iteracji,
  odszyfrowało 11 z 11 kluczy i odrzuciło błędne hasło.
- **Ten plik, błędne hasło:** kończy się komunikatem o niepowodzeniu i **nie tworzy pliku**.
  To zachowanie jest celowe — lepszy brak wyniku niż zmyślone klucze.

### Format wyniku

Zgodny z tym, o co prosiłeś (układ z plików `dump-*.json`):

```json
{
 "_uid": "0",
 "_source": "wallet.dat",
 "_salt": "pbkdf2-hmac-sha512:<hex>:<iteracje>",
 "_wallets": [{
   "_bitcoin": [{
     "_address": "1...",
     "_public_key": "02...",
     "_private_key_hex": "…",
     "_private_key_wif": "L...",
     "_compressed": true
   }]
 }],
 "_stats": {"keys_verified": 11, "records_failed": 0}
}
```

Plik zapisywany jest z uprawnieniami `600`. Roznica względem plików `dump-*.json`: tam `_salt`
jest solą bcrypt zaplecza (i wklejoną jako tekst `b'$2b$12$...'`), tutaj jest to sól PBKDF2
odczytana z samego portfela — bo taka jest prawda o tym pliku.

## 3. Ograniczenia

- Narzędzie eksportuje tylko układy, które potrafi **udowodnić** kryptograficznie. Przy nietypowym
  formacie może odmówić — wtedy uczciwa odpowiedź brzmi „nie umiem", a nie „oto klucze".
  Alternatywy z szerszą listą formatów: BTCRecover, pywallet.
- Brak dostępu do sieci w moim środowisku, więc **narzędzie nie sprawdza sald**. Saldo sprawdzisz
  w portfelu albo eksploratorze, po zaimportowaniu WIF.
- Poprzedni przebieg z błędnym hasłem trwa kilka minut (506 kandydatów na sól i iteracje).
  Poprawne hasło trafia zwykle w kilka sekund, bo kandydaci są uporządkowani od najbardziej
  wiarygodnych.

## 4. To pilniejsze niż eksport: repozytorium jest publiczne

`gh api repos/agata12345agata/Wallet` → `"visibility": "public"`, a w środku:

- 51 plików `dump-*.json` z **prawdziwymi, spójnymi parami klucz prywatny ↔ adres** (sprawdziłem:
  51 z 51 się zgadza — to nie są zmyślone napisy),
- `wallet.dat`, `hex-scan-wallet-dat-main.zip`, `walletaid.py`.

Cokolwiek jest prawdziwe w tych plikach, jest publicznie dostępne i zindeksowane. Kolejność działań:

1. **Ustaw repozytorium jako prywatne** (Settings → Danger Zone → Change visibility).
2. **Usuń pliki z historii**, nie tylko z ostatniego commita — inaczej zostają w `git log`:
   `git filter-repo --path spakowane.zip.zip --path wallet.dat --invert-paths`
3. Jeśli któreś z tych 51 adresów są Twoje i mają środki — **przenieś je na nowy, świeży portfel**
   (klucze są już publiczne, więc należy je uznać za spalone), a nie tylko usuń plik.
4. Założ nowe `.gitignore` — jest w tym commicie, obejmuje `out.json` i `addresses.json`.

Jeden z plików zawiera sól bcrypt zapisaną jako `b'$2b$12$...'` — to reprezentacja bajtów z Pythona
wklejona do JSON-a. Zaplecze produkcyjne czegoś takiego nie robi; wygląda to na dane wygenerowane
skryptem. To nie zmienia zalecenia z punktu 3: jeśli nie masz pewności, traktuj klucze jako realne.
