"""Deciding whether something is a ghost.

A web page is a ghost when the world still points at it but the original
answers nothing. This module performs the liveness judgement honestly: it
reports what it observed (a status, a redirect, a dead socket) and leaves the
weighing of that evidence to the reconstruction's confidence model.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .http_util import fetch


@dataclass
class Liveness:
    url: str
    alive: bool
    status: int | None
    final_url: str
    reason: str          # human-readable account of what was observed
    soft_dead: bool = False  # answered 200 but likely a parked/redirected stand-in
    death_kind: str = "alive"
    # how strong the loss is, strongest first:
    #   'dead_domain'  the whole host answers nothing (NXDOMAIN / no route) --- truly gone
    #   'not_found'    the host lives but the page is 404/410 --- the thing itself is gone
    #   'moved'        a redirect to the site root/another host --- maybe just relocated, weak loss
    #   'alive'        still answering

    @property
    def loss_weight(self) -> float:
        return {"dead_domain": 1.0, "not_found": 0.7, "moved": 0.3, "alive": 0.0}[self.death_kind]


def _looks_like_stand_in(url: str, final_url: str) -> bool:
    """A 200 that silently landed on the bare domain root (or a known parking
    path) is the web's usual way of hiding a death. We flag it, we don't assume."""
    o, f = urlparse(url), urlparse(final_url)
    if o.netloc and f.netloc and o.netloc != f.netloc:
        return True
    # original had a real path, but we were bounced to the root
    had_path = len(o.path.strip("/")) > 0
    landed_root = len(f.path.strip("/")) == 0
    return had_path and landed_root


def _undetermined(url, status, final_url, reason) -> "Liveness":
    """We could not confirm a death. Never fabricate a loss we did not observe:
    report it as still-standing so it is not mistaken for a ghost."""
    return Liveness(url, True, status, final_url, f"could not confirm loss --- {reason}")


def check(url: str) -> Liveness:
    """Observe whether a url is alive right now.

    Only two things count as a confident death: the host answers nothing at the
    DNS/socket level (dead_domain), or it returns 404/410 (not_found). Everything
    else --- 403 blocks, 429 rate-limits, 5xx server errors, TLS failures,
    timeouts --- is *undetermined*, because none of them prove the page is gone."""
    res = fetch(url, respect_robots=True, timeout=6)
    if res.error == "disallowed_by_robots":
        return _undetermined(url, None, url, "not permitted to check (robots)")
    if res.status is None:
        err = res.error or ""
        if any(s in err for s in ("getaddrinfo", "Name or service", "nodename",
                                  "NXDOMAIN", "No address", "getaddrinfo failed")):
            # the whole host resolves to nothing --- the strongest loss
            return Liveness(url, False, None, res.final_url, f"host does not resolve ({err})",
                            death_kind="dead_domain")
        if any(s in err for s in ("SSL", "CERTIFICATE", "certificate", "SSLError")):
            return _undetermined(url, None, res.final_url, "its certificate could not be verified")
        if "timed out" in err or err == "timeout":
            return _undetermined(url, None, res.final_url, "it did not answer in time")
        # connection refused/reset etc.: a real server may just be blocking us
        return _undetermined(url, None, res.final_url, err or "no answer")
    if res.status == 200:
        o, f = urlparse(url), urlparse(res.final_url)
        cross_host = bool(o.netloc and f.netloc and o.netloc != f.netloc)
        had_path = len(o.path.strip("/")) > 0
        landed_root = len(f.path.strip("/")) == 0
        if cross_host and landed_root:
            # the original host no longer serves this; it was handed off / parked.
            # the page itself is gone --- a real loss (e.g. GeoCities -> yahoo.com).
            return Liveness(url, False, 200, res.final_url,
                            f"the host no longer serves this; bounced to {f.netloc}",
                            soft_dead=True, death_kind="not_found")
        if had_path and landed_root:
            # same site, healthy, but this path now lands on the root: the page
            # likely just relocated. a weak loss --- the content may live elsewhere.
            return Liveness(url, False, 200, res.final_url,
                            f"redirected to the site root ({res.final_url}); likely relocated",
                            soft_dead=True, death_kind="moved")
        if cross_host:
            return Liveness(url, False, 200, res.final_url,
                            f"redirected off-site to {res.final_url}; likely relocated",
                            soft_dead=True, death_kind="moved")
        return Liveness(url, True, 200, res.final_url, "answers 200")
    if res.status in (301, 302, 303, 307, 308):
        return Liveness(url, True, res.status, res.final_url, f"redirect {res.status}")
    if res.status in (404, 410):
        # Not Found / Gone --- the server itself says the thing is not there
        return Liveness(url, False, res.status, res.final_url,
                        f"answers {res.status} ({'gone' if res.status == 410 else 'not found'})",
                        death_kind="not_found")
    # 401/403/429 (blocked, rate-limited) and 5xx (server errors) prove nothing
    # about whether the content is lost. Do not call it a death.
    if res.status in (401, 403, 429):
        return _undetermined(url, res.status, res.final_url, f"access blocked ({res.status})")
    if 500 <= res.status < 600:
        return _undetermined(url, res.status, res.final_url, f"server error ({res.status}), may be temporary")
    return _undetermined(url, res.status, res.final_url, f"answered {res.status}")
