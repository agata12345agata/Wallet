#!/usr/bin/env python3
"""
wif_tool.py - narzedzie do analizy portfeli z plikow dump-*.json (spakowane.zip)

Co robi:
  1) buduje indeks (uid / address / public_key -> private_key_wif) z archiwum ZIP
     lub katalogu z plikami JSON,
  2) wyszukuje WIF po uid, adresie albo kluczu publicznym,
  3) wyprowadza public_key / address / WIF z private_key_hex (pelny lancuch Bitcoin),
  4) weryfikuje spojnosc calego zbioru danych.

WAZNE (patrz ANALIZA.md): z samego uid / address / public_key NIE DA SIE
matematycznie wyliczyc klucza prywatnego (problem logarytmu dyskretnego na
krzywej secp256k1). Dlatego tryb "lookup" dziala na zasadzie wyszukania w
posiadanych danych, a nie obliczenia.

Bez zewnetrznych zaleznosci - czysty Python 3.

Przyklady:
  python3 wif_tool.py index spakowane.zip.zip -o index.json
  python3 wif_tool.py lookup --uid 100006085755590626624017165289976955820 -i index.json
  python3 wif_tool.py lookup --address 1DZDAf3kg8x3zA7CRbnZdP2JfX5ozA5jRk -i index.json
  python3 wif_tool.py lookup --pubkey 036b4b...50037 -i index.json
  python3 wif_tool.py derive 7f60495922b771f449050f4e8e2fb667a79c1bd4900c10355579d570dfcef95a
  python3 wif_tool.py verify spakowane.zip.zip
"""

import argparse
import glob
import hashlib
import json
import os
import sys
import zipfile

# --------------------------------------------------------------------------
# secp256k1 (czysty Python)
# --------------------------------------------------------------------------
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % P == 0:
        return None
    if p == q:
        lam = (3 * p[0] * p[0]) * pow(2 * p[1], P - 2, P) % P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], P - 2, P) % P
    x = (lam * lam - p[0] - q[0]) % P
    return (x, (lam * (p[0] - x) - p[1]) % P)


def _mul(k, p=(Gx, Gy)):
    r = None
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def privkey_to_pubkey(priv_hex, compressed=True):
    """private_key_hex -> public_key (hex)"""
    k = int(priv_hex, 16)
    if not 0 < k < N:
        raise ValueError("klucz prywatny poza zakresem krzywej secp256k1")
    x, y = _mul(k)
    if compressed:
        return ("02" if y % 2 == 0 else "03") + "%064x" % x
    return "04" + "%064x" % x + "%064x" % y


# --------------------------------------------------------------------------
# RIPEMD-160 / hash160
# --------------------------------------------------------------------------
def hash160(data: bytes) -> bytes:
    sha = hashlib.sha256(data).digest()
    try:
        return hashlib.new("ripemd160", sha).digest()
    except Exception:
        try:
            from Crypto.Hash import RIPEMD160  # pycryptodome
            return RIPEMD160.new(sha).digest()
        except Exception as e:
            raise RuntimeError(
                "Brak RIPEMD-160: zainstaluj pycryptodome (pip install pycryptodome)"
            ) from e


# --------------------------------------------------------------------------
# Base58Check
# --------------------------------------------------------------------------
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode_check(payload: bytes) -> str:
    data = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


def b58decode_check(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    pad = len(s) - len(s.lstrip("1"))
    data = b"\x00" * pad + body
    payload, chk = data[:-4], data[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != chk:
        raise ValueError("bledna suma kontrolna Base58Check")
    return payload


# --------------------------------------------------------------------------
# Konwersje Bitcoin
# --------------------------------------------------------------------------
def privkey_to_wif(priv_hex: str, compressed: bool = True, mainnet: bool = True) -> str:
    raw = bytes.fromhex(priv_hex)
    if len(raw) != 32:
        raise ValueError("private_key_hex musi miec 32 bajty (64 znaki hex)")
    prefix = b"\x80" if mainnet else b"\xef"
    return b58encode_check(prefix + raw + (b"\x01" if compressed else b""))


def wif_to_privkey(wif: str):
    p = b58decode_check(wif)
    compressed = len(p) == 34 and p[-1] == 1
    return p[1:33].hex(), compressed


def pubkey_to_address(pub_hex: str, version: int = 0x00) -> str:
    return b58encode_check(bytes([version]) + hash160(bytes.fromhex(pub_hex)))


def derive_all(priv_hex: str, compressed: bool = True) -> dict:
    pub = privkey_to_pubkey(priv_hex, compressed)
    return {
        "_private_key_hex": priv_hex.lower(),
        "_public_key": pub,
        "_address": pubkey_to_address(pub),
        "_private_key_wif": privkey_to_wif(priv_hex, compressed),
    }


# --------------------------------------------------------------------------
# Wczytywanie danych
# --------------------------------------------------------------------------
def iter_json(source: str):
    """Zwraca (nazwa_pliku, obiekt_json) z ZIP-a, katalogu albo pojedynczego JSON-a."""
    if os.path.isdir(source):
        for f in sorted(glob.glob(os.path.join(source, "**", "*.json"), recursive=True)):
            with open(f, "rb") as fh:
                yield os.path.basename(f), json.load(fh)
    elif zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as z:
            for name in sorted(z.namelist()):
                if name.lower().endswith(".json"):
                    with z.open(name) as fh:
                        yield os.path.basename(name), json.load(fh)
    else:
        with open(source, "rb") as fh:
            yield os.path.basename(source), json.load(fh)


def extract_records(source: str):
    """Plaska lista rekordow: uid, address, public_key, private_key_hex, wif, salt."""
    recs = []
    for fname, j in iter_json(source):
        uid = str(j.get("_uid", ""))
        for wallet in j.get("_wallets", []) or []:
            salt = wallet.get("_salt", "")
            for coin_key, coin in wallet.items():
                if not isinstance(coin, list):
                    continue
                for e in coin:
                    if not isinstance(e, dict) or "_private_key_hex" not in e:
                        continue
                    recs.append({
                        "file": fname,
                        "uid": uid,
                        "coin": coin_key.lstrip("_"),
                        "salt": salt,
                        "address": e.get("_address", ""),
                        "public_key": e.get("_public_key", ""),
                        "private_key_hex": e.get("_private_key_hex", ""),
                        "private_key_wif": e.get("_private_key_wif", ""),
                    })
    return recs


def build_index(source: str) -> dict:
    recs = extract_records(source)
    idx = {"by_uid": {}, "by_address": {}, "by_pubkey": {}, "records": recs}
    for r in recs:
        if r["uid"]:
            idx["by_uid"].setdefault(r["uid"], []).append(r)
        if r["address"]:
            idx["by_address"][r["address"]] = r
        if r["public_key"]:
            idx["by_pubkey"][r["public_key"].lower()] = r
    return idx


# --------------------------------------------------------------------------
# Komendy CLI
# --------------------------------------------------------------------------
def cmd_index(a):
    idx = build_index(a.source)
    with open(a.output, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1)
    print(f"Zapisano indeks: {a.output} ({len(idx['records'])} rekordow, "
          f"{len(idx['by_uid'])} uid, {len(idx['by_address'])} adresow)")


def _load_index(path_or_source):
    if path_or_source.lower().endswith(".json") and os.path.isfile(path_or_source):
        try:
            data = json.load(open(path_or_source, encoding="utf-8"))
            if isinstance(data, dict) and "by_address" in data:
                return data
        except Exception:
            pass
    return build_index(path_or_source)


def cmd_lookup(a):
    idx = _load_index(a.index)
    hits = []
    if a.address:
        r = idx["by_address"].get(a.address)
        hits += [r] if r else []
    if a.pubkey:
        r = idx["by_pubkey"].get(a.pubkey.lower())
        hits += [r] if r else []
    if a.uid:
        hits += idx["by_uid"].get(str(a.uid), [])
    if not hits:
        print("Nie znaleziono pasujacego rekordu w danych.", file=sys.stderr)
        print("Pamietaj: WIF-a nie da sie WYLICZYC z uid/adresu/klucza publicznego.",
              file=sys.stderr)
        return 1
    seen = set()
    for r in hits:
        if r["private_key_wif"] in seen:
            continue
        seen.add(r["private_key_wif"])
        if a.quiet:
            print(r["private_key_wif"])
        else:
            chk = derive_all(r["private_key_hex"])
            print(json.dumps({
                "uid": r["uid"], "address": r["address"],
                "public_key": r["public_key"],
                "private_key_hex": r["private_key_hex"],
                "private_key_wif": r["private_key_wif"],
                "weryfikacja_ok": (chk["_public_key"] == r["public_key"].lower()
                                   and chk["_address"] == r["address"]
                                   and chk["_private_key_wif"] == r["private_key_wif"]),
            }, indent=1, ensure_ascii=False))
    return 0


def cmd_derive(a):
    priv = a.private_key_hex
    if not all(c in "0123456789abcdefABCDEF" for c in priv):  # podano WIF
        priv, comp = wif_to_privkey(priv)
        a.uncompressed = not comp
    out = derive_all(priv, compressed=not a.uncompressed)
    print(json.dumps(out, indent=1))
    return 0


def cmd_verify(a):
    recs = extract_records(a.source)
    bad = 0
    for r in recs:
        d = derive_all(r["private_key_hex"])
        ok = (d["_public_key"] == r["public_key"].lower()
              and d["_address"] == r["address"]
              and d["_private_key_wif"] == r["private_key_wif"])
        if not ok:
            bad += 1
            print("NIEZGODNOSC:", r["file"], r["address"])
    print(f"Sprawdzono {len(recs)} rekordow: zgodnych {len(recs)-bad}, bledow {bad}")
    return 1 if bad else 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("index", help="zbuduj indeks z ZIP/katalogu JSON")
    s.add_argument("source")
    s.add_argument("-o", "--output", default="index.json")
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("lookup", help="znajdz WIF po uid / adresie / kluczu publicznym")
    s.add_argument("-i", "--index", default="spakowane.zip.zip",
                   help="index.json albo ZIP/katalog z plikami JSON")
    s.add_argument("--uid")
    s.add_argument("--address")
    s.add_argument("--pubkey")
    s.add_argument("-q", "--quiet", action="store_true", help="wypisz sam WIF")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("derive", help="z private_key_hex (lub WIF) wylicz pub/address/WIF")
    s.add_argument("private_key_hex")
    s.add_argument("--uncompressed", action="store_true")
    s.set_defaults(func=cmd_derive)

    s = sub.add_parser("verify", help="sprawdz spojnosc wszystkich rekordow")
    s.add_argument("source", nargs="?", default="spakowane.zip.zip")
    s.set_defaults(func=cmd_verify)

    a = p.parse_args()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
