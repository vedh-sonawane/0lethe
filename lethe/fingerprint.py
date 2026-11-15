"""Fingerprint, don't hoard.

The machine does not keep copies of the world. It keeps compact signatures of
what it has seen, so a fragment of a lost thing can be recognized inside
something else --- the way a song is identified from a few seconds of audio.

SimHash gives a 64-bit signature where similar texts land at small Hamming
distance. This is what lets the machine COMPOUND: every reconstruction leaves a
fingerprint behind, and a later fragment that matches it can be tied back to the
ghost it belongs to.
"""

from __future__ import annotations

import hashlib
import re

_TOKEN = re.compile(r"[a-z0-9]+")
BITS = 64
_MASK = (1 << BITS) - 1


def _shingles(text: str, k: int = 4) -> list[str]:
    """Word k-shingles. Shingles beat bag-of-words for recognizing a fragment:
    they preserve local word order, so a quoted sentence still matches."""
    tokens = _TOKEN.findall(text.lower())
    if len(tokens) < k:
        return [" ".join(tokens)] if tokens else []
    return [" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)]


def _hash64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    """64-bit SimHash of text. Returns 0 for empty input."""
    shingles = _shingles(text)
    if not shingles:
        return 0
    v = [0] * BITS
    for sh in shingles:
        h = _hash64(sh)
        for i in range(BITS):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(BITS):
        if v[i] > 0:
            out |= (1 << i)
    return out & _MASK


def hexdigest(value: int) -> str:
    return f"{value:016x}"


def from_hex(h: str) -> int:
    return int(h, 16)


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & _MASK).bit_count()


def similarity(a: int, b: int) -> float:
    """1.0 == identical signature, 0.0 == fully opposed."""
    return 1.0 - hamming(a, b) / BITS
