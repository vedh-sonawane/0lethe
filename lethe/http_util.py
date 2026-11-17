"""Reading the world politely.

The machine respects the rules of the places it reads from. This module is the
only door to the outside: it identifies itself honestly, obeys robots.txt for
the open web, rate-limits itself, and caches raw bytes locally so a shadow is
never fetched twice during a case.

Archive and encyclopedia APIs (Wayback, Wikipedia) are built to be queried and
are exempted from robots gating by design --- but we are still gentle with them.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import io
import time
import zlib
import urllib.error
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .db import DATA_DIR

USER_AGENT = "lethe/0.1 (archival reconstruction; reads shadows, respects robots)"
CACHE_DIR = DATA_DIR / "cache"

# Hosts whose public APIs are meant to be queried programmatically. We stay
# polite (rate-limited) but do not apply robots gating to their API endpoints.
_API_HOSTS = {"web.archive.org", "archive.org", "en.wikipedia.org", "wikipedia.org",
              "en.wikisource.org", "wikisource.org",
              "commons.wikimedia.org", "www.wikidata.org"}

_last_hit: dict[str, float] = {}
_MIN_INTERVAL = 0.5  # seconds between requests to the same host
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


@dataclass
class Fetched:
    url: str
    status: int | None
    body: bytes
    mimetype: str | None
    final_url: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.body).hexdigest()


def _throttle(host: str) -> None:
    now = time.monotonic()
    last = _last_hit.get(host, 0.0)
    wait = _MIN_INTERVAL - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def allowed_by_robots(url: str) -> bool:
    """True if robots.txt permits us to fetch this url. API hosts are allowed.

    Fails open on an unreadable robots.txt (standard crawler convention) but
    fails *closed* on an explicit disallow."""
    host = urlparse(url).netloc.lower()
    if host in _API_HOSTS:
        return True
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        robots_url = f"{urlparse(url).scheme or 'https'}://{host}/robots.txt"
        rp.set_url(robots_url)
        try:
            _throttle(host)
            rp.read()
        except Exception:
            rp = None  # unreadable -> fail open
        _robots[host] = rp
    rp = _robots[host]
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def fetch(url: str, *, method: str = "GET", timeout: int = 25,
          respect_robots: bool = True, max_bytes: int = 5_000_000,
          use_cache: bool = True) -> Fetched:
    """Fetch a url, returning a Fetched record even on failure (error field set).

    Caches successful GET bodies by url-hash so a shadow is read only once ---
    except when use_cache is False (e.g. endpoints whose URL is constant but whose
    answer must be fresh each time, like 'give me a random article')."""
    host = urlparse(url).netloc.lower()

    if respect_robots and not allowed_by_robots(url):
        return Fetched(url, None, b"", None, url, error="disallowed_by_robots")

    cache_path = CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".bin")
    if method == "GET" and use_cache and cache_path.exists():
        body = cache_path.read_bytes()
        return Fetched(url, 200, body, None, url)

    _throttle(host)
    req = Request(url, method=method, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept": "*/*",
    })
    try:
        with urlopen(req, timeout=timeout) as resp:
            try:
                raw = resp.read(max_bytes)
            except http.client.IncompleteRead as e:
                # Wayback's id_ endpoint often closes the socket without a clean
                # content-length. The bytes already read ARE the recovered content.
                raw = e.partial
            if resp.headers.get("Content-Encoding") == "gzip" and raw[:2] == b"\x1f\x8b":
                # lenient gzip: a truncated stream (from a partial read) still
                # yields all the content before the cut --- salvage it rather than
                # throwing it away on the missing end-of-stream marker.
                try:
                    d = zlib.decompressobj(31)
                    out = d.decompress(raw)
                    try:
                        out += d.flush()
                    except Exception:
                        pass
                    raw = out or gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                except Exception:
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except (OSError, EOFError):
                        pass
            mimetype = resp.headers.get_content_type()
            final_url = resp.geturl()
            status = resp.status
            if method == "GET" and use_cache and status == 200:
                cache_path.write_bytes(raw)
            return Fetched(url, status, raw, mimetype, final_url)
    except urllib.error.HTTPError as e:
        return Fetched(url, e.code, b"", None, url, error=f"http_{e.code}")
    except urllib.error.URLError as e:
        return Fetched(url, None, b"", None, url, error=f"url_error:{e.reason}")
    except TimeoutError:
        return Fetched(url, None, b"", None, url, error="timeout")
    except Exception as e:  # DNS failure, connection reset, etc. --- all are signals of deadness
        return Fetched(url, None, b"", None, url, error=f"{type(e).__name__}:{e}")
