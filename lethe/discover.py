"""DISCOVER, at its deepest: finding ghosts nobody catalogued as lost.

A single dead link is a small ghost. But when MANY recovered things all point at
the same missing address, that void is something larger --- a thing the world
kept referring to, that no longer exists anywhere. Its importance is not written
in any one source; it emerges only from the whole graph, from the convergence of
references. This reads the entire web of recovered outlinks and surfaces the
voids the most things point at --- the biggest holes in what survived.

This is the machine noticing what is missing before anyone asks.
"""

from __future__ import annotations

from urllib.parse import urlparse


# Infrastructure, not lost culture: hit-counters, trackers, ad/widget servers and
# living giants pollute the convergence signal. They are pointed at by everything
# but are not things anyone lost. Excluded from "ghostliness".
_JUNK_HOSTS = (
    "hitbox", "addfreestats", "sitemeter", "statcounter", "extreme-dm", "nedstat",
    "bravenet", "webring", "doubleclick", "google-analytics", "googletagmanager",
    "googlesyndication", "google.com", "youtube.com", "facebook.com", "twitter.com",
    "x.com", "instagram.com", "fastcounter", "counter", "/serv", "amazon-adsystem",
    "geocities.yahoo.com/addons", "yahoo.com/addons", "paypal.com", "w3.org",
    "adsense", "quantserve", "scorecardresearch", "addthis", "sharethis",
)


def _host(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _norm(url: str) -> str | None:
    """A canonical form so http/https, www, trailing slashes and :80 collapse ---
    and infrastructure/tracker/widget addresses are dropped entirely."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    host = p.netloc.lower().replace(":80", "").replace(":443", "")
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    full = f"{host}{path}" + (f"?{p.query}" if p.query else "")
    if any(j in full for j in _JUNK_HOSTS):
        return None
    return full


def convergence(conn, *, min_refs: int = 3, log=print):
    """Cross-reference every recovered outlink, counting how many DISTINCT source
    domains point at each address --- so a void the whole web referred to ranks
    above one site's internal cross-links. Returns ranked (url, domain_count) and
    writes each ghost's reference_count (distinct domains pointing at it)."""
    inbound: dict[str, set[str]] = {}
    for identity, val in conn.execute(
        "SELECT g.identity, c.value FROM claim c JOIN reconstruction r ON r.id=c.reconstruction_id "
        "JOIN ghost g ON g.id=r.ghost_id WHERE c.field='outlinks' AND c.value IS NOT NULL"):
        src = _host(identity)
        if not src:
            continue
        for u in val.split("\n"):
            nu = _norm(u)
            if nu and _host(u) != src:            # ignore a site pointing at itself
                inbound.setdefault(nu, set()).add(src)

    conn.execute("UPDATE ghost SET reference_count=0 WHERE kind='web_page'")  # clear stale
    updated = 0
    for row in conn.execute("SELECT id, identity FROM ghost WHERE kind='web_page'"):
        nu = _norm(row["identity"])
        if nu and nu in inbound:
            refs = len(inbound[nu] - {_host(row["identity"])})
            if refs:
                conn.execute("UPDATE ghost SET reference_count=? WHERE id=?", (refs, row["id"]))
                updated += 1
    conn.commit()

    ranked = sorted(((u, len(s)) for u, s in inbound.items() if len(s) >= min_refs),
                    key=lambda t: t[1], reverse=True)
    log(f"cross-referenced {len(inbound)} addresses; {len(ranked)} are pointed at by "
        f">= {min_refs} DISTINCT domains; updated {updated} ghost(s)")
    return ranked, inbound


def unrecovered_convergent(conn, ranked, inbound, *, limit: int = 40) -> list[tuple[str, int]]:
    """Of the convergent voids, the ones the machine has NOT yet recovered --- the
    most-referenced missing things still waiting to be investigated. Returns the
    original (un-normalized) best URL for each, with its inbound count."""
    have = {_norm(r["identity"]) for r in conn.execute("SELECT identity FROM ghost")}
    out = []
    for nu, n in ranked:
        if nu in have:
            continue
        # recover a usable absolute URL for the normalized key
        out.append(("https://" + nu, n))
        if len(out) >= limit:
            break
    return out
