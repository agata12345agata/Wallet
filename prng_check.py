#!/usr/bin/env python3
"""
prng_check.py - diagnostyka: czy klucze prywatne daloby sie PRZEWIDZIEC
                przy wiekszej ilosci danych?

Sprawdza jedyny realny scenariusz, w ktorym "wiecej danych" cokolwiek daje:
klucze wygenerowane modulem `random` Pythona (Mersenne Twister MT19937),
np. `random.getrandbits(256)`, zamiast `os.urandom`.

MT19937 ma stan 624 slow 32-bitowych. Majac 624 KOLEJNE slowa wyjsciowe
(= 78 kluczy po 256 bitow, w oryginalnej kolejnosci generowania) mozna
odtworzyc stan i przewidziec wszystkie nastepne (i poprzednie) klucze.

Uzycie:
    python3 prng_check.py spakowane.zip.zip
    python3 prng_check.py katalog_z_jsonami/

Wynik:
  - "ZLAMANE"  -> klucze pochodza z MT19937, da sie napisac generator
  - "brak dopasowania" -> klucze z CSPRNG (os.urandom) -> matematycznie nie da sie
  - "za malo danych" -> potrzeba >= 79 kluczy w kolejnosci generowania
"""
import sys
from wif_tool import extract_records, derive_all

N_STATE = 624


# ---------------- MT19937 ----------------
def untemper(y):
    y ^= y >> 18
    y ^= (y << 15) & 0xEFC60000
    # odwrocenie y ^= (y << 7) & 0x9D2C5680
    x = y
    for _ in range(4):
        x = y ^ ((x << 7) & 0x9D2C5680)
    y = x & 0xFFFFFFFF
    # odwrocenie y ^= y >> 11
    x = y
    for _ in range(3):
        x = y ^ (x >> 11)
    return x & 0xFFFFFFFF


class MT:
    def __init__(self, state):
        self.mt = list(state)
        self.i = N_STATE

    def _gen(self):
        for i in range(N_STATE):
            y = (self.mt[i] & 0x80000000) + (self.mt[(i + 1) % N_STATE] & 0x7FFFFFFF)
            self.mt[i] = self.mt[(i + 397) % N_STATE] ^ (y >> 1)
            if y & 1:
                self.mt[i] ^= 0x9908B0DF
        self.i = 0

    def next32(self):
        if self.i >= N_STATE:
            self._gen()
        y = self.mt[self.i]
        self.i += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        return (y ^ (y >> 18)) & 0xFFFFFFFF

    def getrandbits256(self, little_first=True):
        words = [self.next32() for _ in range(8)]
        if little_first:
            return sum(w << (32 * i) for i, w in enumerate(words))
        return sum(w << (32 * (7 - i)) for i, w in enumerate(words))


def key_to_words(k, little_first=True):
    w = [(k >> (32 * i)) & 0xFFFFFFFF for i in range(8)]
    return w if little_first else w[::-1]


def try_crack(keys, little_first):
    """keys: lista intow w domniemanej kolejnosci generowania."""
    words = []
    for k in keys:
        words += key_to_words(k, little_first)
    if len(words) < N_STATE + 8:
        return None, len(words)
    state = [untemper(w) for w in words[:N_STATE]]
    mt = MT(state)
    # sprawdz, czy klon odtwarza kolejne, niewykorzystane klucze
    used_keys = N_STATE // 8  # klucze zuzyte na odtworzenie stanu
    ok = 0
    for k in keys[used_keys:used_keys + 3]:
        if mt.getrandbits256(little_first) == k:
            ok += 1
        else:
            return False, len(words)
    return (ok > 0), len(words)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "spakowane.zip.zip"
    recs = extract_records(src)
    # kolejnosc generowania ~ kolejnosc uid (UUID v1 = znacznik czasu)
    recs.sort(key=lambda r: int(r["uid"] or 0))
    keys = [int(r["private_key_hex"], 16) for r in recs]
    print(f"Rekordow: {len(keys)}  (slow 32-bit: {len(keys)*8}, potrzeba >= {N_STATE+8})")

    if len(keys) * 8 < N_STATE + 8:
        brak = -(-(N_STATE + 8) // 8) - len(keys)
        print(f"ZA MALO DANYCH: brakuje ok. {brak} kluczy w oryginalnej kolejnosci "
              f"generowania, zeby w ogole sprobowac odtworzyc stan MT19937.")
        return 2

    for lf in (True, False):
        res, _ = try_crack(keys, lf)
        if res:
            print(f"ZLAMANE! Klucze pochodza z random.getrandbits(256) "
                  f"(kolejnosc slow: {'LE' if lf else 'BE'}). "
                  f"Mozna napisac generator przewidujacy kolejne klucze.")
            return 0
    print("Brak dopasowania MT19937 -> klucze najpewniej z CSPRNG (os.urandom). "
          "Wiecej danych NIE pomoze; odzyskanie klucza z adresu/pubkey to ECDLP.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
