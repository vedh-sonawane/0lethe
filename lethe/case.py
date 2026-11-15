"""The detective loop.

One case, one ghost. The machine never vacuums the trillions; it starts from a
thing referenced-but-missing and follows only the slice of the world that thing
points to:

    1. CONFIRM    does it answer nothing now, while being pointed at?
    2. FOLLOW     gather the trail: archived captures, citations.
    3. FINGERPRINT leave a signature so future fragments can be recognized.
    4. ASSEMBLE   rebuild and score, every fact labelled and sourced.
    5. COMPOUND   persist it all into the graph; the next case starts richer.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from urllib.parse import urlparse

from . import db
from . import foresee as _foresee
from .detect import check
from .extract import extract
from .fingerprint import hexdigest, simhash, from_hex, hamming, similarity, BITS
from .reconstruct import assemble
from .sources import wayback, wikidata, wikipedia, wikiquote


def _field_position(identity: str) -> tuple[float, float]:
    """A deterministic place on the field, as a star chart of identity.

    Things that belong together cluster together: the host plus the first path
    segment (for GeoCities, its *neighborhood*) sets a constellation centre, and
    the full address scatters the page within that constellation. So a whole
    neighborhood of lost homepages gathers into one patch of sky, honest about
    being conceptual rather than geographic."""
    p = urlparse(identity)
    segs = [s for s in p.path.split("/") if s]
    host = (p.netloc or identity).replace("www.", "").replace(":80", "").lower()
    cluster = host + "/" + (segs[0].lower() if segs else "")
    ch = int.from_bytes(hashlib.blake2b(cluster.encode(), digest_size=8).digest(), "big")
    fh = int.from_bytes(hashlib.blake2b(identity.encode(), digest_size=8).digest(), "big")
    cangle = (ch % 1_000_000) / 1_000_000 * 2 * math.pi
    cradius = 0.28 + 0.6 * (((ch >> 20) % 1_000_000) / 1_000_000)
    cx, cy = cradius * math.cos(cangle), cradius * math.sin(cangle)
    # scatter within the constellation
    ja = (fh % 1_000_000) / 1_000_000 * 2 * math.pi
    jr = 0.22 * (((fh >> 20) % 1_000_000) / 1_000_000) ** 0.5
    return round(cx + jr * math.cos(ja), 6), round(cy + jr * math.sin(ja), 6)


def _upsert_ghost(conn, identity: str) -> int:
    fx, fy = _field_position(identity)
    conn.execute(
        "INSERT INTO ghost(kind, identity, field_x, field_y) VALUES('web_page',?,?,?) "
        "ON CONFLICT(identity) DO UPDATE SET updated_at=datetime('now')",
        (identity, fx, fy))
    conn.commit()
    return int(conn.execute("SELECT id FROM ghost WHERE identity=?", (identity,)).fetchone()["id"])


def _record_shadow(conn, ghost_id, source_id, kind, *, uri=None, captured_at=None,
                   status_code=None, mimetype=None, digest=None, meta=None) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO shadow(ghost_id, source_id, kind, uri, captured_at, "
        "status_code, mimetype, digest, meta) VALUES(?,?,?,?,?,?,?,?,?)",
        (ghost_id, source_id, kind, uri, captured_at, status_code, mimetype, digest,
         json.dumps(meta) if meta is not None else None))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM shadow WHERE ghost_id=? AND source_id=? AND kind=? "
        "AND uri IS ? AND captured_at IS ?",
        (ghost_id, source_id, kind, uri, captured_at)).fetchone()
    if row:
        return int(row["id"])
    # uri/captured_at NULL comparisons: fall back to a looser match
    row = conn.execute(
        "SELECT id FROM shadow WHERE ghost_id=? AND source_id=? AND kind=? "
        "ORDER BY id DESC LIMIT 1", (ghost_id, source_id, kind)).fetchone()
    return int(row["id"])


@dataclass
class CaseResult:
    ghost_id: int
    identity: str
    status: str
    confidence: float
    summary: str
    n_captures: int
    n_citations: int


def run_case(conn, url: str, *, citations: list[wikipedia.Citation] | None = None,
             liveness=None, max_fetch: int = 6, log=print) -> CaseResult:
    """Investigate one ghost end to end, writing everything into the graph.

    `liveness` may be supplied by the caller (e.g. discovery already read it) so
    the page is not probed twice with possibly-disagreeing results."""
    src_live = db.register_source(conn, "live", "Present-day web",
                                  "liveness", "polite liveness checks, robots respected")
    src_wb = db.register_source(conn, "wayback", "Internet Archive Wayback Machine",
                                "web_archive", wayback.TERMS)
    src_wiki = db.register_source(conn, "wikipedia", "Wikipedia",
                                  "encyclopedia", wikipedia.TERMS)

    ghost_id = _upsert_ghost(conn, url)
    log(f"  ghost #{ghost_id}  {url}")

    # 1. CONFIRM ----------------------------------------------------------------
    live = liveness if liveness is not None else check(url)
    live_sid = _record_shadow(conn, ghost_id, src_live, "liveness", uri=live.final_url,
                              status_code=live.status,
                              meta={"alive": live.alive, "reason": live.reason,
                                    "soft_dead": live.soft_dead})
    log(f"  now: {'ALIVE' if live.alive else 'GONE '} - {live.reason}")

    # citations: references that point here, each carrying the description a
    # surviving page wrote about the lost thing (title, publisher, date, author)
    cite_pairs: list[tuple[dict, int]] = []
    for cit in (citations or []):
        meta = {"article": cit.article, "title": cit.title, "work": cit.work,
                "publisher": cit.publisher, "date": cit.date, "author": cit.author}
        sid = _record_shadow(conn, ghost_id, src_wiki, "citation",
                             uri=f"https://en.wikipedia.org/wiki/{cit.article.replace(' ', '_')}",
                             meta=meta)
        cite_pairs.append((meta, sid))
    if cite_pairs:
        described = sum(1 for c, _ in cite_pairs if c.get("title") or c.get("work"))
        log(f"  trail: {len(cite_pairs)} citation(s) point here, {described} describe what it was")

    # 2. FOLLOW: archived captures ---------------------------------------------
    caps = wayback.captures(url)
    log(f"  archive: {len(caps)} capture(s) on record")
    # record every capture as a shadow (cheap metadata = evidence over time)
    cap_sid: dict[str, int] = {}
    for c in caps:
        sid = _record_shadow(conn, ghost_id, src_wb, "snapshot", uri=c.viewer_url(),
                             captured_at=c.iso, status_code=int(c.statuscode) if c.statuscode.isdigit() else None,
                             mimetype=c.mimetype, digest=c.digest)
        cap_sid[c.timestamp] = sid

    # fetch bytes only for a bounded, high-value set: latest live captures with
    # distinct digests (richest, most recent copies). fingerprint, don't hoard.
    live_caps = [c for c in caps if c.is_ok and c.mimetype.startswith("text/")]
    chosen: list[wayback.Capture] = []
    seen_digests: set[str] = set()
    for c in reversed(live_caps):            # newest first
        if c.digest in seen_digests:
            continue
        seen_digests.add(c.digest)
        chosen.append(c)
        if len(chosen) >= max_fetch:
            break
    chosen.reverse()

    assembled_input: list[tuple[wayback.Capture, object, int]] = []
    for c in chosen:
        res = wayback.fetch_capture(c)
        if res.ok:
            page = extract(res.text, base_url=c.original)
            assembled_input.append((c, page, cap_sid[c.timestamp]))
            log(f"    recovered {page.word_count:>5} words  {c.iso[:10]}  "
                f"\"{(page.title or '')[:48]}\"")

    # 4. ASSEMBLE & SCORE -------------------------------------------------------
    asm = assemble(assembled_input, cite_pairs, (live, live_sid))

    recon_id = int(conn.execute(
        "INSERT INTO reconstruction(ghost_id, method, confidence, summary) VALUES(?,?,?,?)",
        (ghost_id, asm.method, asm.confidence, asm.summary)).lastrowid)
    for cl in asm.claims:
        claim_id = int(conn.execute(
            "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
            "VALUES(?,?,?,?,?,?)",
            (recon_id, cl.field, cl.value, cl.status, cl.confidence, cl.rationale)).lastrowid)
        for sid in cl.shadow_ids:
            conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id, role) "
                         "VALUES(?,?,'supports')", (claim_id, sid))
    conn.commit()

    # 3. FINGERPRINT + RECOGNIZE ------------------------------------------------
    if asm.best_text:
        fp = simhash(asm.best_text)
        conn.execute(
            "INSERT INTO fingerprint(ghost_id, kind, value, bits, note) VALUES(?,?,?,?,?)",
            (ghost_id, "simhash64_text", hexdigest(fp), BITS,
             "signature of recovered body; lets a later fragment be recognized"))
        conn.commit()
        # COMPOUND: does this match anything already recovered? a mirror, a
        # repost, the same lost page living under another address.
        matches = 0
        for row in conn.execute(
            "SELECT DISTINCT ghost_id, value FROM fingerprint WHERE kind='simhash64_text' "
            "AND ghost_id != ?", (ghost_id,)):
            d = hamming(fp, from_hex(row["value"]))
            if d <= 3:                        # ~>=0.95 similar: the same content, not shared boilerplate
                sim = round(similarity(fp, from_hex(row["value"])), 4)
                for a, b in ((ghost_id, row["ghost_id"]), (row["ghost_id"], ghost_id)):
                    conn.execute(
                        "INSERT OR IGNORE INTO recognition(ghost_id, other_ghost_id, kind, "
                        "distance, similarity) VALUES(?,?,'fingerprint_match',?,?)", (a, b, d, sim))
                matches += 1
        if matches:
            conn.commit()
            log(f"  recognized {matches} match(es) --- this content exists elsewhere in the graph")

    # 5. COMPOUND: settle the ghost's standing in the graph --------------------
    #   reconstructed : we rebuilt content or what-it-was from real shadows
    #   confirmed     : gone, but nothing recovered yet (a bare ghost)
    #   living        : still answers --- not a ghost
    status = ("reconstructed" if (asm.best_text or asm.label) else
              ("confirmed" if not live.alive else "living"))
    conn.execute(
        "UPDATE ghost SET label=COALESCE(?, label), status=?, reference_count=?, "
        "worth=?, death_kind=?, existed_from=?, existed_until=?, "
        "confirmed_gone_at=CASE WHEN ? THEN datetime('now') ELSE confirmed_gone_at END, "
        "updated_at=datetime('now') WHERE id=?",
        (asm.label, status, len(cite_pairs), asm.worth,
         None if live.alive else live.death_kind,
         asm.existed_from, asm.existed_until, 0 if live.alive else 1, ghost_id))
    conn.commit()

    log(f"  => {status.upper()}  conf {asm.confidence:.2f}  worth {asm.worth:.2f}  [{live.death_kind}]")
    return CaseResult(ghost_id, url, status, asm.confidence, asm.summary,
                      len(assembled_input), len(cite_pairs))


# The lost city. GeoCities organized personal homepages into themed
# "neighborhoods"; all of it went dark in 2009. Each is a real human artifact.
GEO_NEIGHBORHOODS = ["Athens", "SunsetStrip", "Area51", "Heartland", "EnchantedForest",
                     "Hollywood", "Tokyo", "Paris", "CapeCanaveral", "SoHo", "Broadway",
                     "Vienna", "WestHollywood", "BourbonStreet", "RodeoDrive"]


def _is_user_page(url: str, neighborhood: str) -> bool:
    segs = [s for s in urlparse(url).path.split("/") if s]
    if len(segs) < 2:                       # at least Neighborhood/<something>
        return False
    last = segs[-1].lower()
    return (segs[-1].isdigit() or segs[-1].startswith("~") or len(segs) >= 3
            or last.endswith((".html", ".htm")))


def hunt_geocities(conn, neighborhoods: list[str] | None = None, *, per: int = 8,
                   log=print) -> list[CaseResult]:
    """DISCOVER the real ghost class: lost personal homepages. Reads the archive's
    own index of what once lived in each neighborhood and brings the richest
    back --- their words, their images, their changes through time."""
    hoods = neighborhoods or GEO_NEIGHBORHOODS
    results: list[CaseResult] = []
    for n in hoods:
        urls = wayback.archived_urls(f"geocities.com/{n}/", limit=5000, min_length=2500)
        picked, seen = [], set()
        for orig, _ln in urls:
            key = orig.rstrip("/").lower().replace("www.", "").replace(":80", "")
            if key in seen or not _is_user_page(orig, n):
                continue
            seen.add(key); picked.append(orig)
            if len(picked) >= per:
                break
        log(f"{n}: {len(urls)} archived pages on record; recovering {len(picked)} lost homepages")
        for u in picked:
            try:
                results.append(run_case(conn, u, max_fetch=3, log=log))
            except Exception as e:
                log(f"  (skipped {u}: {e})")
    reb = sum(1 for r in results if r.status == "reconstructed")
    log(f"recovered {reb} lost homepage(s) across {len(hoods)} neighborhood(s)")
    results.sort(key=lambda r: r.confidence, reverse=True)
    return results


def reconstruct_text(conn, author: str, epoch_year: float, *, log=print) -> int | None:
    """Second medium: a LOST TEXT, rebuilt from the fragments that survive quoted
    inside other people's books. The Heraclitus case --- the whole principle, run
    across 2,500 years instead of 15. Each fragment is tied to the surviving work
    that preserved it; when the same fragment surfaces in more than one source,
    the machine recognizes it and counts the independent attestations."""
    src_q = db.register_source(conn, "wikiquote", "Wikiquote", "quotations", wikiquote.TERMS)
    frags = wikiquote.fragments(author)
    if not frags:
        log(f"no sourced fragments found for {author}")
        return None
    identity = f"text:{author}"
    gid = _upsert_ghost(conn, identity)
    log(f"  ghost #{gid}  lost work of {author}  ({len(frags)} surviving fragment(s))")

    recon_id = int(conn.execute(
        "INSERT INTO reconstruction(ghost_id, method, confidence, summary) VALUES(?,?,?,?)",
        (gid, "lost_text.v1", 0.0, "")).lastrowid)

    # merge fragments that are the same thing quoted by different sources
    merged: list[dict] = []   # {text, original, sources:set, sids:list, fp}
    sources_seen: dict[str, int] = {}
    for fr in frags:
        fp = simhash(fr.translation)
        hit = None
        for m in merged:
            if hamming(fp, m["fp"]) <= 8:
                hit = m
                break
        # the surviving source is a shadow; dedupe identical source strings
        src = fr.source or "an unnamed ancient source"
        if src not in sources_seen:
            sources_seen[src] = _record_shadow(conn, gid, src_q, "quotation",
                                               uri=None, meta={"preserved_in": src})
        sid = sources_seen[src]
        if hit:
            hit["sources"].add(src)
            if sid not in hit["sids"]:
                hit["sids"].append(sid)
        else:
            merged.append({"text": fr.translation, "original": fr.original,
                           "sources": {src}, "sids": [sid], "fp": fp})

    for m in merged:
        n = len(m["sources"])
        # more independent ancient sources attesting the same words = more belief
        conf = round(min(0.95, 0.6 + 0.12 * (n - 1)), 3)
        val = m["text"] + (f"\n— {m['original']}" if m["original"] else "")
        rationale = (f"survives quoted in {n} source(s): " + "; ".join(sorted(m["sources"]))[:240]
                     if n > 1 else f"survives quoted in: {next(iter(m['sources']))}")
        cid = int(conn.execute(
            "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
            "VALUES(?,?,?,?,?,?)", (recon_id, "fragment", val, "recovered", conf, rationale)).lastrowid)
        for sid in m["sids"]:
            conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id) VALUES(?,?)", (cid, sid))

    overall = round(sum(min(0.95, 0.6 + 0.12 * (len(m["sources"]) - 1)) for m in merged) / len(merged), 3)
    attested = sum(1 for m in merged if len(m["sources"]) > 1)
    summary = (f"A lost work rebuilt from {len(merged)} distinct fragment(s) surviving across "
               f"{len(sources_seen)} ancient source(s); {attested} independently attested by more than one.")
    conn.execute("UPDATE reconstruction SET confidence=?, summary=? WHERE id=?",
                 (overall, summary, recon_id))
    conn.execute("UPDATE ghost SET kind='text', status='reconstructed', "
                 "label=?, worth=?, epoch_year=?, updated_at=datetime('now') WHERE id=?",
                 (f"{author} — lost writings", round(0.5 + 0.5 * min(1, len(merged) / 20), 3),
                  epoch_year, gid))
    conn.commit()
    log(f"  => REBUILT {len(merged)} fragment(s) from {len(sources_seen)} source(s), "
        f"{attested} multiply-attested  conf {overall:.2f}")
    return gid


def recover_structures(conn, *, limit: int = 400, offset: int = 0, log=print) -> int:
    """Third medium: LOST PLACES. Stand the world's demolished buildings back up
    at their true coordinates, in the years they stood --- each with the real
    photograph that survived it. A whole medium, pulled generically from the
    structured memory of Wikidata; no building is named by hand. offset paginates
    through every one of them."""
    src = db.register_source(conn, "wikidata", "Wikidata + Wikimedia Commons",
                             "structured", wikidata.TERMS)
    places = wikidata.demolished_structures(limit, offset, log=log)
    if not places:
        log("no structures returned (query may be rate-limited; try again later)")
        return 0, 0
    log(f"standing up {len(places)} demolished structure(s) ...")
    n = 0
    for p in places:
        gid = _upsert_ghost(conn, f"place:{p.qid}")
        # placed by REAL geography: the field gains a world map of what's gone
        fx = round(max(-0.96, min(0.96, p.lon / 190.0)), 6)
        fy = round(max(-0.96, min(0.96, -p.lat / 100.0)), 6)
        sid = _record_shadow(conn, gid, src, "record",
                             uri=p.article or f"https://www.wikidata.org/wiki/{p.qid}",
                             meta={"qid": p.qid, "lat": p.lat, "lon": p.lon})
        recon_id = int(conn.execute(
            "INSERT INTO reconstruction(ghost_id, method, confidence, summary) VALUES(?,?,?,?)",
            (gid, "place.v1", 0.9,
             f"A demolished structure, stood back up from its record and surviving photograph."
             )).lastrowid)
        img = p.image + ("&" if "?" in p.image else "?") + "width=480" if p.image else None
        inc_y = wikidata._year(p.inception)
        dem_y = wikidata._year(p.demolished)
        claims = [
            ("location", f"{p.lat:.5f}, {p.lon:.5f}", "recovered", 0.97,
             "its coordinates on the earth, from the record"),
            ("existed", (f"{p.inception[:10]} .. " if p.inception else "built ? .. ") + (p.demolished[:10] if p.demolished else "?"),
             "recovered", 0.9, "the span it stood, from inception to demolition"),
            ("demolished", p.demolished[:10] if p.demolished else "?", "recovered", 0.95,
             "the date it was torn down --- the moment it became a ghost"),
        ]
        if img:
            claims.append(("images", img, "recovered", 0.9,
                           "a surviving photograph of the lost structure"))
        if p.description:
            claims.append(("description", p.description, "recovered", 0.85, "the record's own words"))
        for fld, val, st, cf, rat in claims:
            cid = int(conn.execute(
                "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
                "VALUES(?,?,?,?,?,?)", (recon_id, fld, val, st, cf, rat)).lastrowid)
            conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id) VALUES(?,?)", (cid, sid))
        epoch = inc_y if inc_y is not None else (dem_y - 40 if dem_y is not None else None)
        conn.execute(
            "UPDATE ghost SET kind='place', status='reconstructed', label=?, field_x=?, field_y=?, "
            "epoch_year=?, existed_from=?, existed_until=?, worth=0.7, "
            "confirmed_gone_at=COALESCE(?, confirmed_gone_at), updated_at=datetime('now') WHERE id=?",
            (p.label, fx, fy, epoch, p.inception, p.demolished, p.demolished, gid))
        conn.commit()
        n += 1
    log(f"stood up {n} lost structure(s) at their real coordinates")
    return n, len(places)


def recover_lost_texts(conn, *, limit: int = 20, offset: int = 0, log=print) -> list[int]:
    """Reach back into antiquity: rebuild lost works from their quoted fragments.
    The authors are DISCOVERED generically --- every ancient figure whose words
    survive quoted, pulled from Wikidata with their real era --- never a list.
    offset paginates through all of them, so the field keeps reaching further
    back in time on its own."""
    voices = wikidata.ancient_quoted_people(limit, offset, log=log)
    out = []
    for author, year in voices:
        try:
            gid = reconstruct_text(conn, author, year, log=log)
            if gid:
                out.append(gid)
        except Exception as e:
            log(f"  (skipped {author}: {type(e).__name__}: {e})")
    log(f"recovered {len(out)} lost work(s) from antiquity")
    return out, len(voices)


def foresee_one(conn, url: str, frag, *, citation_title: str | None = None,
                preserve: bool = False, log=print) -> tuple[int, bool]:
    """Record one living-but-fragile thing as at-risk, and optionally preserve it
    now (create the shadow before it is lost). Returns (ghost_id, did_preserve).
    Shared by the `foresee` command and the autonomous engine."""
    src_live = db.register_source(conn, "live", "Present-day web", "liveness",
                                  "polite liveness checks, robots respected")
    gid = _upsert_ghost(conn, url)
    lsid = _record_shadow(conn, gid, src_live, "liveness", uri=url,
                          meta={"alive": True, "risk": frag.risk, "reasons": frag.reasons})
    recon_id = int(conn.execute(
        "INSERT INTO reconstruction(ghost_id, method, confidence, summary) VALUES(?,?,?,?)",
        (gid, "foresee.v1", frag.risk,
         f"Alive, but at risk of being lost (risk {frag.risk:.2f}): " + "; ".join(frag.reasons))).lastrowid)
    cov = (f"{frag.n_captures} snapshot(s); last {frag.last_capture_iso[:10]}"
           if frag.n_captures else "no archived copy exists")
    for fld, val, st, cf, rat in [
        ("at_risk", "; ".join(frag.reasons), "inferred", frag.risk,
         "fragility judged from archive coverage and recency --- deterministic, no guesswork"),
        ("archive_coverage", cov, "recovered", 0.9, "the archive's current holdings for this living page")]:
        cid = int(conn.execute(
            "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
            "VALUES(?,?,?,?,?,?)", (recon_id, fld, val, st, cf, rat)).lastrowid)
        conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id) VALUES(?,?)", (cid, lsid))
    conn.execute("UPDATE ghost SET status='at_risk', risk=?, label=COALESCE(label,?), "
                 "existed_from=?, updated_at=datetime('now') WHERE id=?",
                 (frag.risk, citation_title, frag.first_capture_iso, gid))
    conn.commit()

    did = False
    if preserve:
        src_pres = db.register_source(conn, "preserve", "Internet Archive Save Page Now",
                                      "preservation", "public, free Save Page Now; used gently")
        out = _foresee.preserve(url, log=log)
        psid = _record_shadow(conn, gid, src_pres, "preservation", uri=out.get("snapshot"), meta=out)
        if out.get("ok"):
            cid = int(conn.execute(
                "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
                "VALUES(?,?,?,?,?,?)", (recon_id, "preserved", out["snapshot"], "recovered", 0.95,
                "the machine foresaw the risk and archived it --- a shadow now exists before the thing is lost")).lastrowid)
            conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id) VALUES(?,?)", (cid, psid))
            conn.execute("UPDATE ghost SET status='preserved' WHERE id=?", (gid,))
            conn.commit()
            did = True
    return gid, did


def foresee_wikipedia(conn, article: str, *, scan: int = 50, preserve_top: int = 4,
                      log=print) -> list[_foresee.Fragility]:
    """FORESEE: scan a Wikipedia article's *living* citations for the ones about
    to be lost, record them as at-risk, and preserve the most fragile few --- so
    the shadow exists before the thing is gone. Tomorrow's ghost, caught today."""
    src_live = db.register_source(conn, "live", "Present-day web", "liveness",
                                  "polite liveness checks, robots respected")
    src_pres = db.register_source(conn, "preserve", "Internet Archive Save Page Now",
                                  "preservation", "public, free Save Page Now; used gently")
    log(f"foreseeing loss among the living citations of \"{article}\" ...")
    cites = wikipedia.cite_templates(article, limit=scan)
    seen: set[str] = set()
    at_risk: list[tuple[_foresee.Fragility, int]] = []
    for c in cites:
        if c.url in seen:
            continue
        seen.add(c.url)
        f = _foresee.fragility(c.url)
        if not f or f.risk < 0.5:
            continue
        gid = _upsert_ghost(conn, c.url)
        lsid = _record_shadow(conn, gid, src_live, "liveness", uri=c.url,
                              meta={"alive": True, "risk": f.risk, "reasons": f.reasons})
        recon_id = int(conn.execute(
            "INSERT INTO reconstruction(ghost_id, method, confidence, summary) VALUES(?,?,?,?)",
            (gid, "foresee.v1", f.risk,
             f"Alive, but at risk of being lost (risk {f.risk:.2f}): " + "; ".join(f.reasons))).lastrowid)
        cov = (f"{f.n_captures} snapshot(s); last {f.last_capture_iso[:10]}"
               if f.n_captures else "no archived copy exists")
        for fld, val, st, cf, rat, sid in [
            ("at_risk", "; ".join(f.reasons), "inferred", f.risk,
             "fragility judged from archive coverage and recency --- deterministic, no guesswork", lsid),
            ("archive_coverage", cov, "recovered", 0.9,
             "the archive's current holdings for this living page", lsid)]:
            cid = int(conn.execute(
                "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
                "VALUES(?,?,?,?,?,?)", (recon_id, fld, val, st, cf, rat)).lastrowid)
            conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id) VALUES(?,?)", (cid, sid))
        conn.execute("UPDATE ghost SET status='at_risk', risk=?, label=COALESCE(label,?), "
                     "existed_from=?, updated_at=datetime('now') WHERE id=?",
                     (f.risk, c.title, f.first_capture_iso, gid))
        conn.commit()
        at_risk.append((f, gid))
        log(f"  AT RISK {f.risk:.2f}  {c.url[:66]}")

    at_risk.sort(key=lambda t: t[0].risk, reverse=True)
    if at_risk and preserve_top:
        log(f"preserving the {min(preserve_top, len(at_risk))} most fragile, before they vanish ...")
        for f, gid in at_risk[:preserve_top]:
            out = _foresee.preserve(f.url, log=log)
            psid = _record_shadow(conn, gid, src_pres, "preservation",
                                  uri=out.get("snapshot"), meta=out)
            if out.get("ok"):
                recon_id = conn.execute(
                    "SELECT MAX(id) FROM reconstruction WHERE ghost_id=?", (gid,)).fetchone()[0]
                cid = int(conn.execute(
                    "INSERT INTO claim(reconstruction_id, field, value, status, confidence, rationale) "
                    "VALUES(?,?,?,?,?,?)", (recon_id, "preserved", out["snapshot"], "recovered", 0.95,
                    "the machine foresaw the risk and archived it --- a shadow now exists "
                    "before the thing is lost")).lastrowid)
                conn.execute("INSERT OR IGNORE INTO claim_shadow(claim_id, shadow_id) VALUES(?,?)", (cid, psid))
                conn.execute("UPDATE ghost SET status='preserved' WHERE id=?", (gid,))
                conn.commit()
    log(f"foresaw {len(at_risk)} fragile thing(s); preserved {min(preserve_top, len(at_risk))}.")
    return [f for f, _ in at_risk]


def discover_from_wikipedia(conn, article: str, *, limit: int = 40, log=print) -> list[CaseResult]:
    """DISCOVER: mine an article's citations, find the ones that answer nothing,
    and open a case on each. Finds things referenced-but-gone that nobody flagged."""
    log(f"discovering ghosts cited by \"{article}\" ...")
    cites = wikipedia.cite_templates(article, limit=limit)
    by_url: dict[str, list[wikipedia.Citation]] = {}
    for c in cites:
        by_url.setdefault(c.url, []).append(c)
    log(f"  {len(by_url)} distinct cited references to test")

    results: list[CaseResult] = []
    skipped_moved = skipped_alive = 0
    for url, group in by_url.items():
        live = check(url)
        if live.alive:
            skipped_alive += 1
            continue
        if live.death_kind == "moved":
            # the site is healthy and the page merely relocated --- not a real
            # loss. skip it so trivial link-rot never pollutes the field.
            skipped_moved += 1
            continue
        log(f"  GHOST: {url}  ({live.reason})")
        results.append(run_case(conn, url, citations=group, liveness=live, log=log))
    log(f"discovered {len(results)} ghost(s) from \"{article}\" "
        f"(skipped {skipped_alive} alive, {skipped_moved} merely-moved)")
    # strongest losses first
    results.sort(key=lambda r: r.confidence, reverse=True)
    return results
