"""FORESEE --- see the loss before it happens, and prevent it.

Every other part of this machine rescues things already gone. This part looks at
things still *alive* and recognizes which ones are about to vanish --- then saves
them while there is still time. It learns the shape of how things disappear:

    - A living page with NO archive copy at all is the most fragile thing on the
      web: the day it dies, nothing survives. There is no shadow to rebuild from.
    - A page with only a snapshot or two, none of them recent, is fading from the
      world's attention --- the usual prelude to quietly disappearing.

When the machine foresees high risk, it acts: it asks the Internet Archive to
capture the page NOW (free, public Save Page Now), creating the shadow BEFORE the
thing is lost. It stops being only a rescue team and becomes something that
guards against forgetting. All of it is deterministic and evidence-based --- a
risk score built from real signals (archive coverage, recency), never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .detect import check
from .http_util import fetch
from .sources import wayback

SAVE = "https://web.archive.org/save/"


@dataclass
class Fragility:
    url: str
    alive: bool
    n_captures: int
    last_capture_iso: str | None
    last_capture_age_days: int | None
    risk: float                       # 0 = safe, 1 = about to be lost with no trace
    reasons: list[str] = field(default_factory=list)
    first_capture_iso: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fragility(url: str, *, known_live=None) -> Fragility | None:
    """Judge how close a *living* thing is to being lost without a trace.

    Returns None if the url is already gone (that is a ghost, not FORESEE's job)
    or could not be checked. Pass known_live to reuse a liveness reading the
    caller already has, instead of probing the page twice."""
    live = known_live if known_live is not None else check(url)
    if not live.alive:
        return None

    caps = wayback.captures(url)
    ok = [c for c in caps if c.is_ok and c.mimetype.startswith("text/")]
    n = len(ok)
    reasons: list[str] = []

    if n == 0:
        # alive, but the archive holds nothing. the single most fragile state:
        # if it dies tomorrow there is no shadow to rebuild it from.
        return Fragility(url, True, 0, None, None, 1.0,
                         ["alive, but NO archived copy exists --- if it vanishes, "
                          "nothing survives to rebuild it from"], None)

    last = ok[-1]
    age = (_now() - last.when).days
    few = max(0.0, (5 - n) / 5.0)                 # <5 snapshots starts to count
    recency = min(1.0, max(0.0, (age / 365.0 - 1) / 4.0))  # >1yr climbs, 5yr+ maxes
    risk = round(min(1.0, 0.55 * few + 0.6 * recency), 3)

    if n <= 2:
        reasons.append(f"only {n} archived snapshot(s) --- a thin safety net")
    if age > 730:
        reasons.append(f"not archived in {age // 365} year(s) --- the world's "
                       f"attention has moved on")
    if not reasons:
        reasons.append("reasonably preserved; low risk")

    return Fragility(url, True, n, last.iso, age, risk, reasons, ok[0].iso)


def preserve(url: str, log=print) -> dict:
    """Preserve a living page NOW, before it can be lost --- ask the Internet
    Archive to capture it. Free, public, polite. Returns the outcome."""
    res = fetch(SAVE + url, respect_robots=False, timeout=90)
    ok = res.status in (200, 301, 302) and "web.archive.org" in (res.final_url or "")
    snapshot = res.final_url if ok else None
    log(f"    preserve {'OK  ' if ok else 'miss'} {url[:64]}"
        + (f"  -> {snapshot}" if snapshot else f"  ({res.error or res.status})"))
    return {"ok": ok, "snapshot": snapshot, "status": res.status, "error": res.error}
