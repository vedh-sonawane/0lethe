"""Wikisource: the surviving library.

Where Wikipedia *describes* things, Wikisource holds the *actual texts* --- full
public-domain books, letters, speeches, the surviving corpus of literate
humanity. It is the natural haystack for cross-shadow recognition: if a lost
work's fragment still exists anywhere a machine can read it for free, it is
likely sitting inside one of these surviving books, quoted by someone who had the
original in hand.

Free MediaWiki action API, no key. This does two things: full-text SEARCH for a
phrase, and pulling the plain text of a surviving page to read a fragment out of.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ..http_util import fetch

_TAG = re.compile(r"<[^>]+>")
_ENT = re.compile(r"&#?\w+;")
_WS = re.compile(r"\s+")

API = "https://en.wikisource.org/w/api.php"
SITE = "https://en.wikisource.org/wiki/"
TERMS = ("Wikisource via the free MediaWiki action API --- public-domain and "
         "freely-licensed source texts, read at a courteous rate.")


def _do_search(srsearch: str, limit: int) -> list[dict]:
    params = {"action": "query", "list": "search", "srsearch": srsearch,
              "srnamespace": "0", "srlimit": str(limit), "srwhat": "text",
              "format": "json", "formatversion": "2"}
    res = fetch(f"{API}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return []
    try:
        hits = json.loads(res.text).get("query", {}).get("search", [])
        return [{"title": h["title"], "pageid": h.get("pageid")} for h in hits if h.get("title")]
    except Exception:
        return []


_STOPW = frozenset(
    "the a an and or of to in on at by for with from as is are was were be i you "
    "he she it we they not but if so do did has have had who which what that this "
    "my your his her its our their me him them no nor one two into upon".split())


def _inner_phrases(clean: str, n: int = 5, k: int = 4) -> list[str]:
    """The most distinctive runs of `n` consecutive words. Several, not one, so
    that if the top run happens to contain the word a different translation varies
    ('could not' vs 'cannot'), another run avoids it."""
    import re
    words = re.findall(r"[A-Za-z']+", clean)
    if len(words) < n:
        return [" ".join(words)] if len(words) >= 3 else []
    scored = []
    for i in range(len(words) - n + 1):
        run = words[i:i + n]
        score = sum(len(w) for w in run if w.lower() not in _STOPW)
        scored.append((score, " ".join(run)))
    scored.sort(key=lambda s: -s[0])
    out, seen = [], set()
    for _s, ph in scored:
        if ph.lower() not in seen:
            seen.add(ph.lower()); out.append(ph)
        if len(out) >= k:
            break
    return out


def search(phrase: str, *, limit: int = 5) -> list[dict]:
    """Surviving works that may carry this phrase. Asks the index for an exact
    contiguous string --- high precision, so a river *story* never masquerades as
    the book that quotes a lost philosopher. First the whole line; if the surviving
    copy differs by an edge word, a few distinctive inner phrases. recognize.locate
    then makes the final, verbatim-run call on each candidate."""
    clean = phrase.strip().strip('."“”‘’')
    hits = _do_search(f'"{clean}"', limit)
    if hits:
        return hits
    for inner in _inner_phrases(clean):
        hits = _do_search(f'"{inner}"', limit)
        if hits:
            return hits
    return []


def plain_text(title: str, *, chars: int = 60000) -> str:
    """The readable text of a surviving work --- the haystack to read a fragment
    out of. Uses the rendered body (parse/text), which on Wikisource includes the
    transcluded page text that the extract API misses, then strips the markup."""
    params = {"action": "parse", "page": title, "prop": "text",
              "format": "json", "formatversion": "2", "redirects": "1"}
    res = fetch(f"{API}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return ""
    try:
        html = json.loads(res.text).get("parse", {}).get("text", "") or ""
    except Exception:
        return ""
    txt = _WS.sub(" ", _ENT.sub(" ", _TAG.sub(" ", html))).strip()
    return txt[:chars]


def url_for(title: str) -> str:
    return SITE + title.replace(" ", "_")
