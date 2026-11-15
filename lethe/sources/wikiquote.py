"""Wikiquote: where the fragments of lost works survive as quotations.

A lost ancient book leaves no copy --- but its sentences live on, quoted inside
the surviving books of others. Wikiquote gathers those quotations and, for each,
records WHO preserved it and WHERE: "As quoted by Plato in Cratylus, 401d". That
attribution is the shadow's source --- the surviving work that carried the
fragment across 2,500 years.

This reads the free Wikiquote API and parses those fragments and their sources.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from ..http_util import fetch

API = "https://en.wikiquote.org/w/api.php"
TERMS = ("Wikiquote (Wikimedia) action API --- free, for programmatic use under a "
         "courteous rate and honest User-Agent.")

_SOURCE_HINT = re.compile(r"quoted|cited|fragment|diels|\bDK\b|\bin\b.*''|translat",
                          re.IGNORECASE)
_STOP_SECTION = re.compile(r"\b(about|see also|external links?|references|sources|"
                           r"notes|bibliography|disputed|misattributed)\b", re.IGNORECASE)


@dataclass
class Fragment:
    translation: str          # the recovered words, in English
    original: str | None      # the original-language text, if preserved
    source: str | None        # the surviving work that quoted it (its provenance)


def _wikitext(page: str) -> str:
    params = {"action": "parse", "page": page, "prop": "wikitext",
              "format": "json", "formatversion": "2", "redirects": "1"}
    res = fetch(f"{API}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return ""
    try:
        return json.loads(res.text).get("parse", {}).get("wikitext", "") or ""
    except Exception:
        return ""


def _clean(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)     # [[a|b]] -> b
    s = re.sub(r"\[\[(?:w:|wikipedia:)?([^\]]*)\]\]", r"\1", s)
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fragments(author: str, *, limit: int = 120) -> list[Fragment]:
    """The surviving fragments of an author's lost work, each with the source
    that preserved it. Only entries with a real source attribution are kept, so
    section noise ('Quotes about X', 'See also') is excluded."""
    wt = _wikitext(author)
    out: list[Fragment] = []
    cur: dict | None = None
    stopped = False
    for raw in wt.split("\n"):
        line = raw.rstrip()
        s = line.lstrip()
        if s.startswith("==") and s.strip("= ").strip():
            # a section header --- stop once we leave the fragments proper
            stopped = bool(_STOP_SECTION.search(s))
            continue
        if stopped or not s.startswith("*"):
            if s == "" and cur is None:
                continue
            continue
        stars = len(s) - len(s.lstrip("*"))
        content = s[stars:].strip()
        if not content:
            continue
        if stars == 1:
            if cur and cur.get("translation") and cur.get("source"):
                out.append(Fragment(cur["translation"], cur.get("original"), cur["source"]))
            cur = {"original": _clean(content), "translation": None, "source": None}
        elif stars == 2 and cur is not None:
            is_bold = content.startswith("'''")
            c = _clean(content)
            if not c:
                continue
            if is_bold and not cur["translation"]:
                cur["translation"] = c
            elif not cur["source"]:
                # prefer a line that names a quoting source; else take the first
                # attribution line as the provenance.
                if _SOURCE_HINT.search(content) or not cur.get("_srcskip"):
                    cur["source"] = c
        if len(out) >= limit:
            break
    if cur and cur.get("translation") and cur.get("source"):
        out.append(Fragment(cur["translation"], cur.get("original"), cur["source"]))
    # if a fragment had no bold translation, its original line may itself be
    # English --- keep those too, as long as a source attests them
    return out[:limit]
