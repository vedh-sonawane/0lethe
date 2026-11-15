"""The Wayback Machine: the deepest shadow the open web casts.

The Internet Archive's CDX server is a free, keyless public API over every
capture it holds. We read it to learn when a page was alive, when it stopped
answering, and to recover the actual bytes of a capture --- the raw original,
via the `id_` modifier that strips the archive's own injected toolbar.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from ..http_util import fetch

CDX = "http://web.archive.org/cdx/search/cdx"
TERMS = ("Internet Archive Wayback Machine --- public CDX + capture APIs, "
         "free and keyless, intended for programmatic access.")


@dataclass
class Capture:
    timestamp: str          # YYYYMMDDhhmmss
    original: str
    mimetype: str
    statuscode: str
    digest: str
    length: str | None = None

    @property
    def when(self) -> datetime:
        return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)

    @property
    def iso(self) -> str:
        return self.when.isoformat()

    @property
    def is_ok(self) -> bool:
        return self.statuscode == "200"

    def raw_url(self) -> str:
        # `id_` -> the untouched original bytes, no Wayback chrome injected.
        return f"http://web.archive.org/web/{self.timestamp}id_/{self.original}"

    def viewer_url(self) -> str:
        return f"http://web.archive.org/web/{self.timestamp}/{self.original}"


def captures(url: str, *, limit: int = 2000) -> list[Capture]:
    """All captures the archive holds for a url, oldest first."""
    q = urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp,original,mimetype,statuscode,digest,length",
        "limit": str(limit),
        "filter": "statuscode:[23].*",   # keep 2xx/3xx; drop the archive's own errors
    })
    res = fetch(f"{CDX}?{q}", respect_robots=False)
    if not res.ok or not res.body.strip():
        return []
    try:
        rows = json.loads(res.text)
    except json.JSONDecodeError:
        return []
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    out: list[Capture] = []
    for row in rows[1:]:
        d = dict(zip(header, row))
        out.append(Capture(
            timestamp=d.get("timestamp", ""),
            original=d.get("original", url),
            mimetype=d.get("mimetype", ""),
            statuscode=d.get("statuscode", ""),
            digest=d.get("digest", ""),
            length=d.get("length"),
        ))
    out.sort(key=lambda c: c.timestamp)
    return out


def fetch_capture(cap: Capture) -> "object":
    """Recover the raw bytes of one capture."""
    return fetch(cap.raw_url(), respect_robots=False)


def image_url(timestamp: str, original: str) -> str:
    """The archive's copy of an embedded image: the `im_` modifier returns the
    raw image bytes as they were captured --- a real recovered fragment."""
    return f"http://web.archive.org/web/{timestamp}im_/{original}"


def archived_urls(prefix: str, *, limit: int = 6000, min_length: int = 1800) -> list[tuple[str, int]]:
    """Distinct archived URLs under a prefix (e.g. a GeoCities neighborhood),
    with their capture size. This is how the machine finds lost things nobody
    catalogued: it reads the archive's own index of what once existed there.

    Returns [(original_url, best_length)], richest first."""
    q = [("url", prefix), ("matchType", "prefix"), ("collapse", "urlkey"),
         ("fl", "original,statuscode,mimetype,length"),
         ("filter", "statuscode:200"), ("filter", "mimetype:text/html"),
         ("output", "json"), ("limit", str(limit))]
    # keep '/', ':' and ',' literal --- CDX treats an encoded prefix as a different
    # match and ends up scanning everything (and timing out).
    qs = "&".join(f"{k}={quote(str(v), safe='/:,')}" for k, v in q)
    url = f"{CDX}?{qs}"
    # big neighborhoods hold hundreds of thousands of captures; the scan is slow
    # and the archive sheds load under pressure. retry with a longer patience.
    res = None
    for attempt in range(3):
        res = fetch(url, respect_robots=False, timeout=90 + attempt * 60)
        if res.ok and res.body.strip():
            break
        time.sleep(3)
    if not res or not res.ok or not res.body.strip():
        return []
    try:
        rows = json.loads(res.text)
    except json.JSONDecodeError:
        return []
    if len(rows) < 2:
        return []
    header = rows[0]
    best: dict[str, int] = {}
    for row in rows[1:]:
        d = dict(zip(header, row))
        orig = d.get("original", "")
        try:
            ln = int(d.get("length") or 0)
        except ValueError:
            ln = 0
        if ln < min_length:
            continue
        if orig not in best or ln > best[orig]:
            best[orig] = ln
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)
