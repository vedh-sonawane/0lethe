"""Rebuilding what cast the shadow --- a page with no surviving copy.

Some lost pages were never archived. No snapshot exists anywhere. But they are
not truly gone: every surviving page that linked to one still carries a trace of
it --- the words it was called by (the anchor text), and the sentence around the
link (how it was described). Gather those traces from many independent survivors
and a shape reappears: what the lost page WAS, in the collective testimony of
everything that pointed at it. No single source holds this. The machine fuses it.

This is the deepest form of the principle: reading the shadows cast by a thing,
and rebuilding the thing itself from them.
"""

from __future__ import annotations

import re
from collections import Counter

from . import db
from .detect import check
from .discover import _host, _norm
from .extract import anchor_contexts
from .sources import wayback


_STOP = set((
    "the a an and or of to in on at for with from by is are was were be been it its this that these "
    "those as his her their our your you we they he she him them who which what when where there here "
    "not but if then than so too very can will just about into out over under more most some any all "
    "page site home click here web html new one www com http https net org link links back next also "
    "have has had do does did would could should may might must been being other same each see get"
).split())
_WORD = re.compile(r"[a-z][a-z'\-]{3,}")


def _topics(contexts: list[tuple[str, str]], n: int = 6) -> list[str]:
    """The words that recur across the testimony --- a crude but honest sense of
    what the lost page was about, taken straight from how others wrote around it."""
    cnt: Counter = Counter()
    for c, _ in contexts:
        for w in _WORD.findall(c.lower()):
            if w not in _STOP:
                cnt[w] += 1
    return [w for w, c in cnt.most_common(n * 3) if c >= 2][:n]


def _sources_pointing_at(conn, void_norm: str, *, max_sources: int = 10) -> list[str]:
    """Identities of recovered pages (one per distinct domain) that link to the
    void --- the witnesses whose testimony we will gather."""
    out, domains = [], set()
    for identity, val in conn.execute(
        "SELECT g.identity, c.value FROM claim c JOIN reconstruction r ON r.id=c.reconstruction_id "
        "JOIN ghost g ON g.id=r.ghost_id WHERE c.field='outlinks' AND c.value IS NOT NULL"):
        dom = _host(identity)
        if dom in domains:
            continue
        for u in val.split("\n"):
            if _norm(u) == void_norm and _host(u) != dom:
                out.append(identity)
                domains.add(dom)
                break
        if len(out) >= max_sources:
            break
    return out


def reconstruct_void(conn, void_url: str, *, max_sources: int = 10, log=print) -> int | None:
    """Rebuild a lost page from how the surviving web referred to it."""
    nu = _norm(void_url)
    if not nu:
        return None
    sources = _sources_pointing_at(conn, nu, max_sources=max_sources)
    if len(sources) < 2:
        return None  # not enough independent testimony

    # 1. gather the testimony first (archived witnesses), no DB writes yet
    witnesses = []   # (identity, capture, hits)
    for identity in sources:
        caps = [c for c in wayback.captures(identity) if c.is_ok and c.mimetype.startswith("text/")]
        if not caps:
            continue
        res = wayback.fetch_capture(caps[-1])
        if not res.ok:
            continue
        hits = anchor_contexts(res.text, caps[-1].original, void_url)
        if hits:
            witnesses.append((identity, caps[-1], hits))
    if len(witnesses) < 2:
        return None

    live = check(void_url)

    # 2. assemble the ghost from what the witnesses said
    from .case import _upsert_ghost
    src_in = db.register_source(conn, "inbound", "The surviving web that links inward",
                                "link_testimony",
                                "anchor text and context of inbound links, from archived survivors")
    gid = _upsert_ghost(conn, void_url)

    anchors: Counter = Counter()
    contexts: list[tuple[str, str]] = []
    all_sids: list[int] = []
    witness_gids: list[int] = []
    witness_isos: list[str] = []
    for identity, cap, hits in witnesses:
        witness_isos.append(cap.iso)
        wg = conn.execute("SELECT id FROM ghost WHERE identity=?", (identity,)).fetchone()
        if wg and wg["id"] != gid:
            witness_gids.append(int(wg["id"]))
        conn.execute(
            "INSERT OR IGNORE INTO shadow(ghost_id, source_id, kind, uri, captured_at, mimetype) "
            "VALUES(?,?,?,?,?,?)", (gid, src_in, "inbound_link", cap.viewer_url(), cap.iso, cap.mimetype))
        row = conn.execute("SELECT id FROM shadow WHERE ghost_id=? AND kind='inbound_link' AND uri=?",
                           (gid, cap.viewer_url())).fetchone()
        all_sids.append(int(row["id"]))
        dom = _host(identity)
        for h in hits:
            a = (h.get("anchor") or "").strip()
            if a and 1 <= len(a) <= 120:
                anchors[a] += 1
            passage = (h.get("passage") or h.get("context") or "").strip()
            if passage and len(passage) > 30:
                contexts.append((passage[:340], dom))
    conn.commit()

    recon_id = int(conn.execute(
        "INSERT INTO reconstruction(ghost_id, method, confidence, summary) VALUES(?,?,?,?)",
        (gid, "from_shadows.v1", round(min(0.8, 0.4 + 0.1 * len(witnesses)), 3),
         f"No copy of this page survives. It is rebuilt from the testimony of {len(witnesses)} "
         f"surviving page(s) that linked to it --- what they called it, and how they framed it.")).lastrowid)

    def _claim(field, value, conf, rationale):
        cid = int(conn.execute(
            "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
            "VALUES(?,?,?,?,?,?)", (recon_id, field, value, "recovered", conf, rationale)).lastrowid)
        for s in all_sids:
            conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id) VALUES(?,?)", (cid, s))

    label = None
    if anchors:
        named = "; ".join(f'"{a}" (x{n})' if n > 1 else f'"{a}"' for a, n in anchors.most_common(8))
        label = anchors.most_common(1)[0][0]
        _claim("known_as", named, round(min(0.85, 0.5 + 0.08 * sum(anchors.values())), 3),
               f"the words {len(witnesses)} surviving page(s) used to link to it --- its names in memory")
    # a readable PORTRAIT: the fullest, most distinct passages the surviving web
    # wrote around the lost page --- deduplicated so boilerplate collapses, so that
    # a page with no copy anywhere can still be READ, through its shadows.
    seen_keys: set = set()
    distinct: list[tuple[str, str]] = []
    for c, d in sorted(contexts, key=lambda t: -len(t[0])):
        key = re.sub(r"[^a-z0-9]", "", c.lower())[:90]
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        distinct.append((c, d))
        if len(distinct) >= 7:
            break
    if distinct:
        portrait = "\n\n".join(f"“{c}”\n    — as written on {d}" for c, d in distinct)
        _claim("portrait", portrait, round(min(0.7, 0.4 + 0.06 * len(distinct)), 3),
               f"paragraphs of how {len(witnesses)} surviving page(s) wrote around and about it --- "
               f"the lost page, read back through its shadows")
    _claim("referenced_by", str(len(sources)), 0.95,
           f"{len(sources)} distinct site(s) still point at this address --- the convergence that revealed it")

    # WHEN it existed: inferred from the window in which the world pointed at it.
    witness_isos.sort()
    first_iso = witness_isos[0] if witness_isos else None
    last_iso = witness_isos[-1] if witness_isos else None
    epoch = None
    if first_iso:
        epoch = int(first_iso[:4])
        _claim("active", f"{first_iso[:10]} .. {last_iso[:10]}", 0.55,
               "the window in which the surviving web still pointed at it --- when it was part of the living world")

    # WHAT it was about: the words that recur across all the testimony.
    topics = _topics(contexts)
    if topics:
        _claim("about", ", ".join(topics), 0.5,
               "the subjects that recur across everything that linked to it")

    if not live.alive:
        _claim("died", f"gone now ({live.reason})", 0.6, "confirmed absent today")

    conn.execute(
        "UPDATE ghost SET kind='web_page', status='reconstructed', label=COALESCE(?,label), "
        "reference_count=?, worth=?, death_kind=?, existed_from=?, existed_until=?, epoch_year=?, "
        "updated_at=datetime('now') WHERE id=?",
        (label, len(sources), round(min(0.9, 0.3 + 0.08 * len(sources)), 3),
         None if live.alive else live.death_kind, first_iso, last_iso, epoch, gid))
    # connect the void to the surviving pages it was rebuilt from --- a navigable
    # thread between a lost thing and the things that still remember it
    for wg in witness_gids:
        for a, b in ((gid, wg), (wg, gid)):
            conn.execute("INSERT OR IGNORE INTO recognition(ghost_id, other_ghost_id, kind, "
                         "distance, similarity) VALUES(?,?,'rebuilt_from',NULL,NULL)", (a, b))
    conn.commit()
    log(f"  rebuilt from {len(witnesses)} witness(es): \"{(label or void_url)[:46]}\"  ({len(sources)} refs)")
    return gid
