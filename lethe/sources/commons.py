"""Wikimedia Commons: every surviving eye.

A demolished building did not leave one photograph. It left dozens, often
hundreds --- taken across decades by people who had no idea they were making the
last record of something. Commons holds them, filed by subject. This gathers the
whole surviving photographic record of a lost structure, so the machine can stand
it back up not as a single flat picture but as a thing seen from every angle that
survived: the building reassembled from every surviving eye.

Free MediaWiki action API on Commons + the Wikidata claim that links a thing to
its Commons category. No key.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ..http_util import fetch

COMMONS = "https://commons.wikimedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
SITE = "https://commons.wikimedia.org/wiki/"
TERMS = ("Wikimedia Commons via the free MediaWiki action API --- freely-licensed "
         "media, with its date and author, read at a courteous rate.")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_IMG_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif")


def _clean(v) -> str:
    return _WS.sub(" ", _TAG.sub(" ", str(v or ""))).strip()


def commons_category(qid: str) -> str | None:
    """The Commons category a Wikidata item points to (P373) --- the folder that
    holds the surviving photographs of this exact subject."""
    params = {"action": "wbgetclaims", "entity": qid, "property": "P373",
              "format": "json"}
    res = fetch(f"{WIKIDATA}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return None
    try:
        claims = json.loads(res.text).get("claims", {}).get("P373", [])
        val = claims[0]["mainsnak"]["datavalue"]["value"]
        return f"Category:{val}"
    except Exception:
        return None


def _category_files(category: str, limit: int) -> list[str]:
    params = {"action": "query", "list": "categorymembers", "cmtitle": category,
              "cmtype": "file", "cmlimit": str(min(limit, 50)),
              "format": "json", "formatversion": "2"}
    res = fetch(f"{COMMONS}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return []
    try:
        mem = json.loads(res.text).get("query", {}).get("categorymembers", [])
        return [m["title"] for m in mem
                if m.get("title", "").lower().endswith(_IMG_EXT)]
    except Exception:
        return []


def _imageinfo(titles: list[str], *, thumb_width: int = 560) -> list[dict]:
    if not titles:
        return []
    params = {"action": "query", "titles": "|".join(titles[:50]),
              "prop": "imageinfo", "iiprop": "url|extmetadata|size",
              "iiurlwidth": str(thumb_width), "format": "json", "formatversion": "2"}
    res = fetch(f"{COMMONS}?{urlencode(params)}", respect_robots=False)
    if not res.ok:
        return []
    out = []
    try:
        pages = json.loads(res.text).get("query", {}).get("pages", [])
    except Exception:
        return []
    for pg in pages:
        ii = (pg.get("imageinfo") or [{}])[0]
        if not ii.get("thumburl"):
            continue
        ext = ii.get("extmetadata", {}) or {}
        date = _clean(ext.get("DateTimeOriginal", {}).get("value")) or \
            _clean(ext.get("DateTime", {}).get("value"))
        out.append({
            "title": pg.get("title", ""),
            "url": ii.get("url", ""),
            "thumb": ii.get("thumburl", ""),
            "width": ii.get("width"), "height": ii.get("height"),
            "date": date[:40],
            "author": _clean(ext.get("Artist", {}).get("value"))[:80],
        })
    return out


def views_for(qid: str, *, limit: int = 16, thumb_width: int = 560) -> list[dict]:
    """The surviving photographic record of a subject: up to `limit` real images
    with their date and author, newest-caption metadata preserved. Empty if the
    subject has no Commons category or no photographs survived there."""
    cat = commons_category(qid)
    if not cat:
        return []
    files = _category_files(cat, limit)
    return _imageinfo(files, thumb_width=thumb_width)


def url_for(title: str) -> str:
    return SITE + title.replace(" ", "_")
