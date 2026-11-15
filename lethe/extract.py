"""Reading content out of a recovered shadow.

Pulls title, visible text, meta description and outbound links from archived
HTML, using only the standard library's HTML parser. No invention: it reports
exactly what the markup contained, nothing more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

_SKIP = {"script", "style", "noscript", "template", "head"}
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES = re.compile(r"\n{3,}")


@dataclass
class Page:
    title: str | None = None
    description: str | None = None
    text: str = ""
    links: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)   # original src of embedded images

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class _Extractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: str | None = None
        self.description: str | None = None
        self.links: list[str] = []
        self.headings: list[str] = []
        self.images: list[str] = []
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_heading = False
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3"):
            self._in_heading = True
            self._heading_buf = []
        elif tag == "a" and a.get("href"):
            href = a["href"].strip()
            if href and not href.startswith(("javascript:", "#", "mailto:", "data:")):
                try:
                    self.links.append(urljoin(self.base_url, href))
                except ValueError:
                    pass
        elif tag == "img" and a.get("src"):
            src = a["src"].strip()
            if src and not src.startswith(("data:", "javascript:")):
                try:
                    u = urljoin(self.base_url, src)
                    low = u.lower()
                    # skip layout spacers and invisible tracking beacons / hit
                    # counters / ad pixels; keep only real pictures from the page.
                    junk = ("spacer", "pixel.gif", "clear.gif", "1x1", "blank.gif",
                            "dot.gif", "transparent", "visit.geocities", "geo.yahoo.com",
                            "/serv?", "counter", "cgi-bin", "hitcounter", "webcounter",
                            "doubleclick", "adforce", "imgis", "/ads/", "hitbox",
                            "spylog", "extreme-dm", "sitemeter", "fastcounter")
                    if not any(j in low for j in junk):
                        self.images.append(u)
                except ValueError:
                    pass
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and a.get("content") and not self.description:
                self.description = a["content"].strip()
        elif tag in ("p", "br", "div", "li", "tr"):
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3"):
            self._in_heading = False
            h = _WS.sub(" ", "".join(self._heading_buf)).strip()
            if h:
                self.headings.append(h)
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title = ((self.title or "") + data).strip() or None
        if self._in_heading:
            self._heading_buf.append(data)
        self._chunks.append(data)

    def page(self) -> Page:
        raw = "".join(self._chunks)
        raw = _WS.sub(" ", raw)
        raw = "\n".join(line.strip() for line in raw.split("\n"))
        raw = _BLANKLINES.sub("\n\n", raw).strip()
        # de-duplicate the link list while preserving order
        seen: set[str] = set()
        links = [u for u in self.links if not (u in seen or seen.add(u))]
        iseen: set[str] = set()
        images = [u for u in self.images if not (u in iseen or iseen.add(u))]
        return Page(
            title=self.title.strip() if self.title else None,
            description=self.description,
            text=raw,
            links=links,
            headings=self.headings,
            images=images,
        )


def extract(html: str, base_url: str = "") -> Page:
    p = _Extractor(base_url)
    try:
        p.feed(html)
    except Exception:
        pass  # malformed archived HTML is the norm; keep whatever parsed
    return p.page()


def _norm_target(url: str) -> str:
    """Loose canonical form for matching a link against a target void."""
    try:
        q = urljoin("", url)
        from urllib.parse import urlparse
        pr = urlparse(q)
        host = pr.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host + (pr.path.rstrip("/") or "/")
    except Exception:
        return url


class _AnchorCtx(HTMLParser):
    """Finds how a surviving page refers to one particular (lost) target: the
    anchor text of each link to it, and a PASSAGE of the words around the link ---
    before and after --- so a page with no copy can be rebuilt from how others
    wrote about it, not just from what they called it."""

    def __init__(self, base_url: str, target: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.target = _norm_target(target)
        self.recent: list[str] = []          # rolling window of preceding text
        self.cap: dict | None = None         # currently inside the anchor
        self.pending: dict | None = None     # finished anchor; gathering the words after it
        self.hits: list[dict] = []
        self._skip = 0

    def _flush(self) -> None:
        if self.pending is None:
            return
        before = _WS.sub(" ", self.pending["before"]).strip()
        after = _WS.sub(" ", "".join(self.pending["after"])).strip()
        passage = (before + "  " + after).strip()
        self.hits.append({"anchor": self.pending["anchor"], "context": before, "passage": passage})
        self.pending = None

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
            return
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href:
                try:
                    if _norm_target(urljoin(self.base, href)) == self.target:
                        self._flush()
                        self.cap = {"anchor": [], "before": " ".join(self.recent)[-320:]}
                except ValueError:
                    pass

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "a" and self.cap is not None:
            anchor = _WS.sub(" ", "".join(self.cap["anchor"])).strip()
            self.pending = {"anchor": anchor, "before": self.cap["before"], "after": [], "n": 0}
            self.cap = None

    def handle_data(self, data):
        if self._skip:
            return
        if self.cap is not None:
            self.cap["anchor"].append(data)
        elif self.pending is not None:
            self.pending["after"].append(data)
            self.pending["n"] += len(data)
            if self.pending["n"] >= 340:     # a full passage after the link; close it
                self._flush()
        self.recent.append(data)
        if len(self.recent) > 90:
            self.recent.pop(0)


def anchor_contexts(html: str, base_url: str, target: str) -> list[dict]:
    """Every way a surviving page named and framed a link to the target --- the
    raw material for rebuilding a page that has no surviving copy of its own."""
    p = _AnchorCtx(base_url, target)
    try:
        p.feed(html)
    except Exception:
        pass
    p._flush()
    return p.hits
