#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wallet_export.py — narzędzie do inwentaryzacji i eksportu Wallet.dat.

Dwa tryby:

  ./wallet_export.py --inventory                  # tylko adresy publiczne (bez hasła)
  ./wallet_export.py --export out.json            # eksport kluczy (pyta o hasło)

Dlaczego osobne tryby: w trybie --inventory nie dotykamy niczego prywatnego.
Klucze prywatne powstają wyłącznie w trybie --export, po podaniu hasła w
terminalu (getpass — hasła NIE podaje się na wejście skryptu, nie ma go w
historii powłoki, nie ma go w tym pliku).

Zasada projektowa: układ pól w tym pliku jest niestandardowy, więc nic nie
jest "zakładane". Skrypt przeszukuje kandydatów na położenie zaszyfrowanego
klucza i soli, a następnie DOWODZI poprawność kryptograficznie: odszyfrowany
klucz prywatny musi dać dokładnie ten klucz publiczny, który jest zapisany
w tym samym rekordzie. Rekord bez dowodu nie trafia do wyniku.
"""

import sys
import os
import json
import stat
import base64
import hashlib
import argparse
import getpass
from binascii import hexlify, unhexlify

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from aes import AESModeOfOperationCBC
except ImportError:
    sys.exit("Brak pliku aes.py obok skryptu (czysty Python AES, licencja MIT).")

# --------------------------------------------------------------------------
# secp256k1 — tylko to, co potrzebne do wyprowadzenia klucza publicznego
# --------------------------------------------------------------------------
P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def _inv(a, m=P):
    return pow(a, m - 2, m)


# Arytmetyka w Jacobian: mnożenie punktu jest ~100x szybsze niż wprost,
# dzięki czemu odrzucanie błędnych haseł nie trwa minut.
def _jdouble(pt):
    X, Y, Z = pt
    if Z == 0 or Y == 0:
        return (0, 0, 0)
    YY = Y * Y % P
    S = 4 * X * YY % P
    M = 3 * X * X % P
    X3 = (M * M - 2 * S) % P
    Y3 = (M * (S - X3) - 8 * YY * YY) % P
    return (X3, Y3, 2 * Y * Z % P)


def _jadd(p, q):
    if p[2] == 0:
        return q
    if q[2] == 0:
        return p
    X1, Y1, Z1 = p
    X2, Y2, Z2 = q
    Z1Z1 = Z1 * Z1 % P
    Z2Z2 = Z2 * Z2 % P
    U1 = X1 * Z2Z2 % P
    U2 = X2 * Z1Z1 % P
    S1 = Y1 * Z2 * Z2Z2 % P
    S2 = Y2 * Z1 * Z1Z1 % P
    if U1 == U2:
        if S1 != S2:
            return (0, 0, 0)
        return _jdouble(p)
    H = (U2 - U1) % P
    I = (2 * H) ** 2 % P
    J = H * I % P
    r = 2 * (S2 - S1) % P
    V = U1 * I % P
    X3 = (r * r - J - 2 * V) % P
    Y3 = (r * (V - X3) - 2 * S1 * J) % P
    Z3 = ((Z1 + Z2) ** 2 - Z1Z1 - Z2Z2) * H % P
    return (X3, Y3, Z3)


_TABLE = None


def _build_table():
    global _TABLE
    base = (GX, GY, 1)
    rows = []
    for _ in range(64):
        row = [None]
        cur = base
        for _j in range(1, 16):
            row.append(cur)
            cur = _jadd(cur, base)
        rows.append(row)
        for _k in range(4):
            base = _jdouble(base)
    _TABLE = rows


def _mul(k):
    """k*G we współrzędnych afinicznych; None dla punktu w nieskończoności."""
    if _TABLE is None:
        _build_table()
    acc = (0, 0, 0)
    for i, row in enumerate(_TABLE):
        d = (k >> (4 * i)) & 0xF
        if d:
            acc = _jadd(acc, row[d])
    if acc[2] == 0:
        return None
    zi = _inv(acc[2])
    zi2 = zi * zi % P
    return (acc[0] * zi2 % P, acc[1] * zi2 * zi % P)


def on_curve(pub):
    """Czy 33-bajtowy klucz publiczny jest poprawnym punktem krzywej secp256k1."""
    if len(pub) != 33 or pub[0] not in (2, 3):
        return False
    x = int.from_bytes(pub[1:], "big")
    if x >= P:
        return False
    rhs = (pow(x, 3, P) + 7) % P
    y = pow(rhs, (P + 1) // 4, P)
    return y * y % P == rhs and (y & 1) == (pub[0] & 1)


def pubkey_of(priv_int, compressed=True):
    pt = _mul(priv_int)
    if pt is None:
        raise ValueError("klucz prywatny daje punkt w nieskończoności")
    x, y = pt
    if compressed:
        return bytes([2 + (y & 1)]) + x.to_bytes(32, "big")
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


# --------------------------------------------------------------------------
# Base58 / adresy / WIF
# --------------------------------------------------------------------------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58check(payload):
    chk = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    raw = payload + chk
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


def h160(b):
    return hashlib.new("ripemd160", hashlib.sha256(b).digest()).digest()


def address_of(pub, version=0x00):
    return b58check(bytes([version]) + h160(pub))


def wif_of(priv32, compressed=True, version=0x80):
    return b58check(bytes([version]) + priv32 + (b"\x01" if compressed else b""))


# --------------------------------------------------------------------------
# Parsowanie pliku
# --------------------------------------------------------------------------
MARK_CKEY = b"ckey"
MARK_MKEY = b"mkey"


def load(path):
    with open(path, "rb") as f:
        return f.read()


def find_ckey_records(blob):
    """Zwraca listę (offset, rekord) — rekord = 124 B od 'ckey' do następnego."""
    offs, i = [], 0
    while True:
        i = blob.find(MARK_CKEY, i)
        if i < 0:
            break
        offs.append(i)
        i += 1
    recs = []
    for k, o in enumerate(offs):
        end = offs[k + 1] if k + 1 < len(offs) else o + 124
        end = min(end, o + 124)
        recs.append((o, blob[o:end]))
    return recs


def pubkey_from_record(rec):
    """W rekordzie ckey: 'ckey' + 0x21 + 33B klucza publicznego."""
    v = rec[4:]
    if len(v) >= 34 and v[0] == 0x21 and v[1] in (0x02, 0x03):
        return v[1:34], 34
    # zapasowo: szukaj sekwencji 0x21 0x02/0x03 w pierwszej części
    for p in range(0, min(40, len(v) - 34)):
        if v[p] == 0x21 and v[p + 1] in (0x02, 0x03):
            return v[p + 1:p + 34], p + 34
    return None, 0


def cbc_decrypt(ct, iv, key):
    """pyaes w tej wersji szyfruje pojedynczy blok — łańcuch prowadzimy sami."""
    c = AESModeOfOperationCBC(key, iv)
    out = b""
    for i in range(0, len(ct), 16):
        blk = ct[i:i + 16]
        if len(blk) != 16:
            raise ValueError("niepełny blok")
        out += c.decrypt(blk)
    return out


def cbc_encrypt(pt, iv, key):
    c = AESModeOfOperationCBC(key, iv)
    out = b""
    for i in range(0, len(pt), 16):
        blk = pt[i:i + 16]
        if len(blk) != 16:
            raise ValueError("niepełny blok")
        out += c.encrypt(blk)
    return out


def try_decrypt(ct32, iv16, key32):
    try:
        pt = cbc_decrypt(ct32, iv16, key32)
    except Exception:
        return None
    if len(pt) != 32:
        return None
    k = int.from_bytes(pt, "big")
    if not (0 < k < N):
        return None
    return k


def derive_master_key(password, salt, iters):
    return hashlib.pbkdf2_hmac("sha512", password, salt, iters, 64)


# --------------------------------------------------------------------------
# Tryb 1: inwentaryzacja (bez hasła)
# --------------------------------------------------------------------------
def inventory(blob):
    recs = find_ckey_records(blob)
    seen, rows = {}, []
    off_curve = 0
    for off, rec in recs:
        pub, _ = pubkey_from_record(rec)
        if not pub:
            continue
        if not on_curve(pub):
            # Klucz nie leży na krzywej => nie jest kluczem. Nie liczymy z niego
            # adresu, bo wyszedłby adres bez pokrycia w rzeczywistości.
            off_curve += 1
            continue
        a = address_of(pub)
        if a in seen:
            continue
        seen[a] = True
        rows.append({"offset": off, "public_key": hexlify(pub).decode(), "address": a})
    return rows, off_curve


# --------------------------------------------------------------------------
# Tryb 2: eksport (z hasłem), z dowodem na każdym kluczu
# --------------------------------------------------------------------------
def _candidate_pairs(region):
    """Kandydaci (salt, iters) wyprowadzeni ze struktury rekordu mkey.

    Nic nie jest zakładane na sztywno: zbieramy realne okna 8-bajtowe na sól
    i realne 4-bajtowe wartości w sensownym zakresie na liczbę iteracji,
    a poprawność i tak rozstrzyga dopiero dowód kryptograficzny.
    """
    pairs = []
    # 1) najpierw układy kanoniczne (sól za szyfrogramem, iteracje po metodzie)
    for p in range(0, len(region) - 69):
        if region[p] != 0x30:
            continue
        salt = region[p + 49:p + 57]
        if len(salt) == 8 and salt != b"\x00" * 8:
            pairs.append((salt, int.from_bytes(region[p + 61:p + 65], "little")))
            pairs.append((salt, int.from_bytes(region[p + 57:p + 61], "little")))
        salt2 = region[max(0, p - 8):p]
        if len(salt2) == 8:
            pairs.append((salt2, int.from_bytes(region[p + 49:p + 53], "little")))
    pairs = [(s_, i_) for (s_, i_) in pairs if 1000 <= i_ <= 5_000_000]
    # 2) szeroki skan strukturalny — tylko na żądanie (--deep), bywa wolny
    iters_at = []
    for p in range(0, len(region) - 4):
        v = int.from_bytes(region[p:p + 4], "little")
        if 1000 <= v <= 5_000_000:
            iters_at.append((p, v))
    markers = [p for p in range(0, len(region) - 49) if region[p] == 0x30]
    for p in markers:
        for (ip, iv) in iters_at:
            if p <= ip < p + 70:                      # w obrębie rekordu mkey
                for sp in range(max(0, p - 8), min(len(region) - 8, p + 70)):
                    if p + 1 <= sp < p + 49:          # sól nie leży w szyfrogramie
                        continue
                    salt = region[sp:sp + 8]
                    if len(salt) < 8 or salt == b"\x00" * 8:
                        continue
                    pairs.append((salt, iv))
    # deduplikacja
    seen, out = set(), []
    for s_, i_ in pairs:
        if (s_, i_) not in seen:
            seen.add((s_, i_))
            out.append((s_, i_))
    # kolejność: najpierw wiarygodne liczby iteracji (typowo 10k-500k),
    # bo od tego zależy tylko czas, nie poprawność
    out.sort(key=lambda t: (t[1] > 1_000_000, t[1]))
    return out, markers


def discover_layout(blob, password, verbose=True, deep=False):
    """Znajduje (mk, salt, iters, layout) taki, że odszyfrowany klucz daje
    klucz publiczny zapisany w tym samym rekordzie. Dowód, nie domysł."""
    recs = find_ckey_records(blob)
    if not recs:
        raise SystemExit("Nie znalazłem rekordów 'ckey' w pliku.")
    good = [(o, r) for o, r in recs if len(r) >= 100]
    if not good:
        raise SystemExit("Rekordy 'ckey' są niepełne — plik wygląda na uszkodzony.")

    m = blob.find(MARK_MKEY)
    if m < 0:
        raise SystemExit("Brak rekordu 'mkey' — portfel nie jest zaszyfrowany hasłem.")
    region = blob[max(0, m - 8):m + 200]

    pairs, markers = _candidate_pairs(region)
    if verbose:
        print("kandydatów (sól, iteracje): %d, znaczników 0x30: %d"
              % (len(pairs), len(markers)), file=sys.stderr)

    proof = good[:2]
    total = len(pairs)
    seen_n = 0
    for exhaustive in ((False, True) if deep else (False,)):   # pełny skan tylko z --deep
      for salt, iters in pairs:
        seen_n += 1
        if verbose and seen_n % 50 == 0:
            print("  ... %d/%d (iteracje %d)" % (seen_n, total, iters),
                  file=sys.stderr, flush=True)
        mk = derive_master_key(password, salt, iters)
        key32, iv16 = mk[:32], mk[32:48]
        for ct_first in (True, False):
            for off, rec in proof:
                pub, end = pubkey_from_record(rec)
                if not pub:
                    continue
                v = rec[4:]
                # najpierw pozycja strukturalna, potem skan zapasowy
                cands = [p + 1 for p in range(end, len(v) - 49) if v[p] == 0x30]
                if exhaustive or not cands:
                    cands += [st for st in range(end, len(v) - 48 + 1) if st not in cands]
                for st in cands:
                    blk = v[st:st + 48]
                    if len(blk) < 48:
                        continue
                    ct, iv = (blk[:32], blk[32:]) if ct_first else (blk[16:], blk[:16])
                    k = try_decrypt(ct, iv, key32)
                    if k is None:
                        continue
                    try:
                        if pubkey_of(k, compressed=(pub[0] in (2, 3))) != pub:
                            continue
                    except ValueError:
                        continue
                    return mk, salt, iters, (st, ct_first)
    return None, None, None, None


def do_export(blob, password, uid, deep=False):
    mk, salt, iters, layout = discover_layout(blob, password, deep=deep)
    if mk is None:
        raise SystemExit(
            "Nie udało się odszyfrować ani jednego klucza.\n"
            "Najczęstsze przyczyny: błędne hasło albo plik w formacie, którego ten\n"
            "skrypt nie obsługuje. Spróbuj ponownie, a jeśli powtórzy się — użyj\n"
            "BTCRecover / pywallet, które mają szerszą listę formatów."
        )

    st, ct_first = layout
    key32 = mk[:32]
    out, bad = [], 0
    for off, rec in find_ckey_records(blob):
        pub, end = pubkey_from_record(rec)
        if not pub:
            continue
        v = rec[4:]
        blk = v[st:st + 48]
        if len(blk) < 48:
            bad += 1
            continue
        ct, iv = (blk[:32], blk[32:]) if ct_first else (blk[16:], blk[:16])
        k = try_decrypt(ct, iv, key32)
        if k is None:
            bad += 1
            continue
        priv = k.to_bytes(32, "big")
        comp = pub[0] in (2, 3)
        try:
            if pubkey_of(k, compressed=comp) != pub:
                bad += 1
                continue
        except ValueError:
            bad += 1
            continue
        out.append({
            "_address": address_of(pub),
            "_public_key": hexlify(pub).decode(),
            "_private_key_hex": hexlify(priv).decode(),
            "_private_key_wif": wif_of(priv, compressed=comp),
            "_compressed": comp,
        })

    doc = {
        "_uid": uid,
        "_source": "wallet.dat",
        "_salt": "pbkdf2-hmac-sha512:%s:%d" % (hexlify(salt).decode(), iters),
        "_wallets": [{"_bitcoin": out}],
        "_stats": {"keys_verified": len(out), "records_failed": bad},
    }
    return doc


def main():
    ap = argparse.ArgumentParser(description="Inwentaryzacja i eksport Wallet.dat")
    ap.add_argument("wallet", nargs="?", default=None)
    ap.add_argument("--inventory", action="store_true", help="tylko adresy publiczne (bez hasła)")
    ap.add_argument("--export", metavar="PLIK.json", help="eksport kluczy do JSON (pyta o hasło)")
    ap.add_argument("--uid", default="0", help="wartość _uid w wyniku")
    ap.add_argument("--deep", action="store_true",
                    help="szeroki skan układu pól (wolny; gdy układ kanoniczny nie pasuje)")
    a = ap.parse_args()

    if a.wallet is None:
        for cand in ("wallet.dat", "Wallet.dat", "WALLET.DAT", "wallet.DAT"):
            if os.path.exists(cand):
                a.wallet = cand
                break
        if a.wallet is None:
            sys.exit("Nie znalazłem wallet.dat w katalogu. Podaj ścieżkę jawnie.")
    if not os.path.exists(a.wallet):
        sys.exit("Nie ma pliku: %s" % a.wallet)
    blob = load(a.wallet)

    if a.inventory or not a.export:
        rows, off_curve = inventory(blob)
        out = {"_source": a.wallet, "_count": len(rows),
               "_rejected_off_curve": off_curve, "_addresses": rows}
        dst = "addresses.json"
        with open(dst, "w") as f:
            json.dump(out, f, indent=1)
        print("Adresy: %d (unikalne) -> %s" % (len(rows), dst))
        print("Kluczy odrzuconych (nie na krzywej): %d" % off_curve)
        if off_curve:
            pct = 100.0 * off_curve / (off_curve + len(rows) or 1)
            print("UWAGA: %.0f%% rekordów nie zawiera poprawnych kluczy —" % pct)
            print("       taki plik nie jest prawdziwym portfelem." )
        print("Pierwszy:", rows[0]["address"] if rows else "—")
        if not a.inventory:
            print("\nAby wyeksportować klucze: --export out.json")
        return

    pw = getpass.getpass("Hasło do portfela (nie jest nigdzie zapisywane): ")
    doc = do_export(blob, pw.encode("utf-8"), a.uid, deep=a.deep)
    with open(a.export, "w") as f:
        json.dump(doc, f, indent=1)
    os.chmod(a.export, stat.S_IRUSR | stat.S_IWUSR)  # 600
    s = doc["_stats"]
    print("Zapisano: %s  (uprawnienia 600)" % a.export)
    print("Kluczy zweryfikowanych: %d, rekordów nieudanych: %d" %
          (s["keys_verified"], s["records_failed"]))
    if s["records_failed"]:
        print("UWAGA: część rekordów się nie odszyfrowała — nie zgaduj, sprawdź hasło.")


if __name__ == "__main__":
    main()
