"""Wikipedia: a vast field of references pointing outward --- and describing what
they point at.

Every citation in an article is a finger pointing at something on the open web,
AND a short human description of it: a title, who published it, when, by whom.
When the target is dead, those citation fields are a surviving *shadow* of the
lost page --- enough to reconstruct what it WAS even if no copy remains anywhere.

Reading the shadow, not just noting the dead link, is the whole difference
between a reconstruction engine and a broken-link checker.

Uses the free MediaWiki action API. No key required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from ..http_util import fetch

API = "https://en.wikipedia.org/w/api.php"
TERMS = ("Wikimedia MediaWiki action API --- free, documented for programmatic "
         "use under a courteous request rate and honest User-Agent.")

_URL_IN = re.compile(r"\burl\s*=\s*([^|}\s]+)", re.I)
_TITLE_FIELD = {"title": "title", "work": "work", "website": "work", "newspaper": "work",
                "publisher": "publisher", "magazine": "work", "date": "date", "year": "date",
                "author": "author", "last": "author", "first": "author_first"}


@dataclass
class Citation:
    url: str
    article: str
    title: str | None = None       # what a surviving page called the lost thing
    work: str | None = None        # where it was published
    publisher: str | None = None
    date: str | None = None
    author: str | None = None

    def described(self) -> bool:
        return bool(self.title or self.work or self.publisher or self.author)


def _wikitext(article: str) -> str:
    params = {"action": "parse", "page": article, "prop": "wikitext",
              "format": "json", "formatversion": "2", "redirects": "1"}
    res = fetch(f"{API}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return ""
    try:
        return json.loads(res.text).get("parse", {}).get("wikitext", "") or ""
    except Exception:
        return ""


def _iter_templates(text: str):
    """Yield the inner body of every {{...}} template, honoring nesting."""
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{" and i + 1 < n and text[i + 1] == "{":
            depth, j = 0, i
            while j < n:
                if text[j:j + 2] == "{{":
                    depth += 1; j += 2
                elif text[j:j + 2] == "}}":
                    depth -= 1; j += 2
                    if depth == 0:
                        yield text[i + 2:j - 2]; break
                else:
                    j += 1
            i = j
        else:
            i += 1


def _split_params(body: str) -> list[str]:
    """Split a template body on top-level | (not inside nested {{}} or [[]])."""
    parts, buf, depth = [], [], 0
    k = 0
    while k < len(body):
        two = body[k:k + 2]
        if two in ("{{", "[["):
            depth += 1; buf.append(two); k += 2
        elif two in ("}}", "]]"):
            depth -= 1; buf.append(two); k += 2
        elif body[k] == "|" and depth == 0:
            parts.append("".join(buf)); buf = []; k += 1
        else:
            buf.append(body[k]); k += 1
    parts.append("".join(buf))
    return parts


def _clean(v: str) -> str:
    v = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", v)   # [[a|b]] -> b
    v = re.sub(r"\[\[([^\]]*)\]\]", r"\1", v)            # [[a]] -> a
    v = re.sub(r"<[^>]+>", "", v)                         # strip tags
    v = v.replace("''", "").strip()
    return v


def cite_templates(article: str, *, limit: int = 400) -> list[Citation]:
    """Every external citation in an article, carrying the description a
    surviving page gave of the (possibly lost) thing it points at."""
    text = _wikitext(article)
    out: list[Citation] = []
    for body in _iter_templates(text):
        head = body.lstrip()[:12].lower()
        if not head.startswith("cite"):
            continue
        m = _URL_IN.search(body)
        if not m:
            continue
        url = _clean(m.group(1))
        if not url.startswith(("http://", "https://")):
            continue
        c = Citation(url=url, article=article)
        first = None
        for p in _split_params(body):
            if "=" not in p:
                continue
            key, _, val = p.partition("=")
            key, val = key.strip().lower(), _clean(val)
            if not val:
                continue
            field = _TITLE_FIELD.get(key)
            if field == "title" and not c.title: c.title = val
            elif field == "work" and not c.work: c.work = val
            elif field == "publisher" and not c.publisher: c.publisher = val
            elif field == "date" and not c.date: c.date = val
            elif field == "author" and not c.author: c.author = val
            elif field == "author_first": first = val
        if c.author and first:
            c.author = f"{first} {c.author}"
        out.append(c)
        if len(out) >= limit:
            break
    return out


# kept for compatibility: bare external links, no description
def external_links(article: str, *, limit: int = 500) -> list[Citation]:
    return cite_templates(article, limit=limit)


def article_extract(title_or_url: str, *, chars: int = 1500) -> str:
    """The plain-text account a surviving encyclopedia keeps of something ---
    for a lost work, this IS the testimony: what others recorded about it."""
    from urllib.parse import unquote
    title = title_or_url
    if title.startswith("http"):
        title = unquote(title.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ")
    params = {"action": "query", "prop": "extracts", "titles": title,
              "explaintext": "1", "exchars": str(chars), "exsectionformat": "plain",
              "format": "json", "formatversion": "2", "redirects": "1"}
    res = fetch(f"{API}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return ""
    try:
        pages = __import__("json").loads(res.text).get("query", {}).get("pages", [])
        return (pages[0].get("extract", "") if pages else "").strip()
    except Exception:
        return ""


def random_articles(n: int = 10) -> list[str]:
    """Random article titles from across ALL of human knowledge. This is how the
    machine seeds itself without anyone naming a topic: every article cites the
    outside world, and a huge share of what it cites is already gone."""
    params = {"action": "query", "list": "random", "rnnamespace": "0",
              "rnlimit": str(min(n, 50)), "format": "json", "formatversion": "2"}
    # never cache: the whole point is a fresh random draw each call
    res = fetch(f"{API}?{urlencode(params)}", respect_robots=False, use_cache=False)
    if not res.ok:
        return []
    try:
        data = __import__("json").loads(res.text)
        return [r["title"] for r in data.get("query", {}).get("random", []) if r.get("title")]
    except Exception:
        return []
