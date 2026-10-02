"""Near-duplicate text detection with MinHash + LSH. Script-agnostic (works for Devanagari, Urdu, etc.)."""
from __future__ import annotations

import re
import unicodedata
import zlib
from collections import defaultdict
from itertools import pairwise

import numpy as np

_URL = re.compile(r"https?://\S+|www\.\S+")
_MENTION = re.compile(r"@\w+")
_WS = re.compile(r"\s+")

_NUM_PERM = 64
_BANDS = 16
_ROWS = _NUM_PERM // _BANDS
_PRIME = (1 << 61) - 1
_rng = np.random.default_rng(1234)
_A = _rng.integers(1, _PRIME, _NUM_PERM, dtype=np.uint64)
_B = _rng.integers(0, _PRIME, _NUM_PERM, dtype=np.uint64)


def normalize(text: str) -> str:
    """Lowercase, drop URLs/mentions/punctuation/symbols/emoji, collapse whitespace.

    Removes by Unicode category (P*, S*) rather than keeping \\w, so combining marks in
    Indic scripts survive.
    """
    t = _MENTION.sub(" ", _URL.sub(" ", unicodedata.normalize("NFKC", text or "").lower()))
    t = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in t)
    return _WS.sub(" ", t).strip()


def shingles(norm: str, k: int = 5) -> set[int]:
    if len(norm) <= k:
        return {zlib.crc32(norm.encode())}
    return {zlib.crc32(norm[i:i + k].encode()) for i in range(len(norm) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _minhash(sh: set[int]) -> np.ndarray:
    x = np.fromiter(sh, dtype=np.uint64)
    # (a*x + b) mod p, computed in uint64 with wraparound; good enough as a hash family here
    h = (np.outer(_A, x) + _B[:, None]) % np.uint64(_PRIME)
    return h.min(axis=1)


class _UF:
    def __init__(self):
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def near_duplicate_groups(items: list[tuple[str, str]], threshold: float, min_len: int) -> list[list[str]]:
    """items: (id, text). Returns groups (size >= 2) of ids whose texts are near-duplicates."""
    sh: dict[str, set[int]] = {}
    for pid, text in items:
        n = normalize(text)
        if len(n) >= min_len:
            sh[pid] = shingles(n)
    buckets: dict[tuple, list[str]] = defaultdict(list)
    for pid, s in sh.items():
        sig = _minhash(s)
        for b in range(_BANDS):
            buckets[(b, sig[b * _ROWS:(b + 1) * _ROWS].tobytes())].append(pid)

    uf = _UF()
    checked: set[tuple[str, str]] = set()
    for ids in buckets.values():
        if len(ids) < 2:
            continue
        # all pairs for small buckets, a chain for huge ones (union-find is transitive anyway)
        pairs = ((ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))) \
            if len(ids) <= 50 else pairwise(ids)
        for a, b in pairs:
            key = (a, b) if a < b else (b, a)
            if key in checked:
                continue
            checked.add(key)
            if jaccard(sh[a], sh[b]) >= threshold:
                uf.union(a, b)

    groups: dict[str, list[str]] = defaultdict(list)
    for pid in sh:
        if pid in uf.p:
            groups[uf.find(pid)].append(pid)
    return [g for g in groups.values() if len(g) >= 2]
