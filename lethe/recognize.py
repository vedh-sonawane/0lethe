"""Cross-shadow recognition --- the Shazam of history.

The oldest proof of this machine's principle: the words of Heraclitus are lost,
yet survive quoted inside other people's surviving books. Whole-document matching
(fingerprint.py) finds a page living under another address. This finds something
harder and far stranger: a FRAGMENT of a lost thing surviving INSIDE a much
larger surviving thing --- a single lost sentence embedded in a whole book.

A long book's one signature is swamped by its own content, so the lost line
hides. The answer is to fingerprint the haystack in overlapping windows: slide a
small frame across the surviving text and fingerprint each frame, so the lost
line matches the one window it actually occupies --- and the machine can point to
the exact passage where the forgotten thing still lives.

Nothing is invented. A recognition is only recorded when the surviving text
genuinely carries the words, confirmed two ways: a tight fingerprint distance AND
a real overlap of distinctive words. The matched passage is quoted back as the
evidence, with its source.
"""

from __future__ import annotations

import re

from .fingerprint import _hash64, simhash, hamming, BITS, _MASK

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "the a an and or of to in on at by for with from as is are was were be been "
    "being that this these those it its he she his her they them their we you i "
    "not but if then so than too very can will would should could may might must "
    "do does did has have had who whom which what when where why how all any each "
    "there here no nor only own same such own into out up down over under again "
    "more most other some one two".split())


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def distinctive(text: str) -> set[str]:
    """The content words of a passage --- what makes it recognizable, with the
    connective tissue every text shares stripped away."""
    return {t for t in _tokens(text) if len(t) > 3 and t not in _STOP}


def windows(text: str, *, size: int = 14, step: int = 7):
    """Slide a frame of `size` words across a text, `step` words at a time.
    Yields (start_token_index, window_text)."""
    toks = _TOKEN.findall(text)          # keep original case for quoting back
    if len(toks) <= size:
        if toks:
            yield 0, " ".join(toks)
        return
    for i in range(0, len(toks) - size + 1, step):
        yield i, " ".join(toks[i:i + size])


def _longest_run(needle: str, haystack: str):
    """The longest contiguous run of the needle's words that literally appears in
    the surviving text --- the actual anchor where the lost line survives, and
    the whole measure of the match. Returns (position, char_length, word_count)
    for the best run, or (-1, 0, 0). Works in word-space so punctuation and
    spacing differences between the two copies never break the match. A real
    quotation leaves a long literal run; scattered common words do not."""
    nt = _tokens(needle)
    if not nt:
        return -1, 0, 0
    spans = [(m.group().lower(), m.start(), m.end()) for m in _TOKEN.finditer(haystack.lower())]
    ht = [t for t, _s, _e in spans]
    where: dict[str, list[int]] = {}
    for p, t in enumerate(ht):
        where.setdefault(t, []).append(p)
    best_pos, best_end, best_span = -1, -1, 0
    for i in range(len(nt)):
        if len(nt) - i <= best_span:
            break                                   # can't beat the best from here
        for p in where.get(nt[i], ()):              # every place this word occurs
            k = 0
            while i + k < len(nt) and p + k < len(ht) and nt[i + k] == ht[p + k]:
                k += 1
            if k > best_span:
                best_span, best_pos, best_end = k, spans[p][1], spans[p + k - 1][2]
    if best_pos < 0:
        return -1, 0, 0
    return best_pos, best_end - best_pos, best_span


def _passage_around(text: str, pos: int, mlen: int, *, pad: int = 220) -> str:
    """Quote back the real surviving passage around where the lost line sits,
    so what is shown is the surviving text itself, never a paraphrase."""
    if pos < 0:
        return ""
    a = max(0, pos - pad)
    b = min(len(text), pos + mlen + pad)
    snippet = text[a:b].strip()
    return ("…" if a > 0 else "") + snippet + ("…" if b < len(text) else "")


def locate(needle: str, haystack: str, *, min_run: int = 5, min_coverage: float = 0.5):
    """Find where a lost fragment (`needle`) survives inside a surviving text
    (`haystack`). Returns (coverage, overlap, passage) for a genuine match, or
    None. A match requires a long *contiguous literal run* of the lost line in
    the surviving text --- the hallmark of a real quotation, not a coincidence of
    scattered common words. Precision over recall: when the surviving copy uses a
    different wording, no run forms and nothing is claimed. Nothing invented.
    """
    nd = distinctive(needle)
    if len(nd) < 3:
        return None                      # too generic to recognize safely
    nwords = len(_tokens(needle))
    if nwords < min_run:
        return None
    pos, clen, run_words = _longest_run(needle, haystack)
    if pos < 0:
        return None
    coverage = run_words / nwords
    run_text = haystack[pos:pos + clen]
    run_dist = distinctive(run_text)
    # the surviving run must be both long enough and carry real content words,
    # so a run of only filler ("and of the in a") can never qualify
    if run_words < min_run or len(run_dist) < 2:
        return None
    if coverage < min_coverage and run_words < 8:
        return None
    overlap = len(nd & run_dist) / len(nd)
    return round(coverage, 4), round(overlap, 4), _passage_around(haystack, pos, clen)
