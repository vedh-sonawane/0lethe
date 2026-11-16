"""The continuous engine --- the machine running itself, over everything.

Nothing here names a topic. The engine seeds itself from RANDOM Wikipedia
articles --- all of human knowledge --- and from each one it reads the outside
world it points to and keeps whatever has already died. Then it follows those
dead things' own trails, across any site, any medium, forever. The frontier
never dries: when it runs low, the machine reaches into the random corpus again.

    - FRONTIER (`lead` table): its self-generated to-do list.
    - SELF-SEEDING: random articles -> their dead citations -> ghosts of anything.
    - TRAIL-FOLLOWING: a recovered page's dead links become new leads (any host).
    - COMPOUND: every reconstruction is fingerprinted and recognized against all
      the rest.

It is polite (rate-limited), persistent, and fully resumable: stop it any time,
start it again, and it continues from the frontier where it left off.

    python -m lethe.cli run                 # run, self-seeding, until stopped
    python -m lethe.cli run --minutes 30
    python -m lethe.cli run --leads 50
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from . import db
from .case import foresee_one, recover_lost_texts, recover_structures, run_case
from .detect import check
from .discover import convergence, unrecovered_convergent
from .foresee import fragility
from .reassemble import reconstruct_void
from .sources import wikipedia

PLACES_PER_PAGE = 40
TEXTS_PER_PAGE = 12
FORESEE_PER_ARTICLE = 3        # how many living citations to assess for fragility
PRESERVE_CAP = 40              # polite ceiling on autonomous Save-Page-Now calls per run

SEED_BATCH = 8            # random articles pulled per seeding
TOPUP_BELOW = 12          # keep the frontier fed above this many pending leads
CITATIONS_PER_ARTICLE = 24     # keep a single article's liveness sweep bounded in time
OUTLINKS_PER_PAGE = 6


def add_lead(conn, kind: str, target: str, *, priority: float = 0.0,
            source_ghost_id: int | None = None, note: str | None = None) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO lead(kind, target, priority, source_ghost_id, note) "
        "VALUES(?,?,?,?,?)", (kind, target, priority, source_ghost_id, note))
    conn.commit()
    return cur.rowcount > 0


def _already_ghost(conn, url: str) -> bool:
    return conn.execute("SELECT 1 FROM ghost WHERE identity=?", (url,)).fetchone() is not None


def _seed_random(conn, n: int = SEED_BATCH, log=print) -> int:
    """Reach into all of human knowledge for new starting points."""
    titles = wikipedia.random_articles(n)
    added = sum(add_lead(conn, "wikipedia", t, priority=0.9) for t in titles)
    if added:
        log(f"seeded {added} random article(s) from across all knowledge")
    return added


def seed(conn, log=print) -> None:
    if conn.execute("SELECT COUNT(*) FROM lead WHERE status='pending'").fetchone()[0] == 0:
        _seed_random(conn, SEED_BATCH, log=log)
    # the two self-paginating media crawls: start them once; each re-enqueues the
    # next page of itself, so the engine walks through ALL of them over time.
    for k in ("places", "texts"):
        if conn.execute("SELECT 1 FROM lead WHERE kind=? LIMIT 1", (k,)).fetchone() is None:
            add_lead(conn, k, "0", priority=0.7)


def _next_lead(conn):
    return conn.execute(
        "SELECT * FROM lead WHERE status='pending' ORDER BY priority DESC, id ASC LIMIT 1"
    ).fetchone()


def _pending_of(conn, kind):
    return conn.execute(
        "SELECT * FROM lead WHERE status='pending' AND kind=? ORDER BY priority DESC, id ASC LIMIT 1",
        (kind,)).fetchone()


def _wiki_pending(conn) -> int:
    # only the random-article seeds gate re-seeding; derived outlinks must not
    # block it, or a backlog of trails starves fresh discovery (and FORESEE).
    return conn.execute(
        "SELECT COUNT(*) FROM lead WHERE status='pending' AND kind='wikipedia'").fetchone()[0]


def _recovered_outlinks(conn, ghost_id: int) -> list[str]:
    row = conn.execute(
        "SELECT value FROM claim WHERE field='outlinks' AND reconstruction_id="
        "(SELECT MAX(id) FROM reconstruction WHERE ghost_id=?)", (ghost_id,)).fetchone()
    return row["value"].split("\n") if row and row["value"] else []


def _followable(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def _follow_trail(conn, ghost_id: int, log) -> int:
    """A recovered thing's own dead links become new leads --- any host, any kind.
    This is what makes discovery generic: the lost web points at more of itself."""
    added = 0
    for url in _recovered_outlinks(conn, ghost_id):
        if added >= OUTLINKS_PER_PAGE:
            break
        if _followable(url) and not _already_ghost(conn, url):
            if add_lead(conn, "outlink", url, priority=0.4, source_ghost_id=ghost_id):
                added += 1
    return added


def work_lead(conn, lead, log=print, ctx=None) -> None:
    conn.execute("UPDATE lead SET status='working' WHERE id=?", (lead["id"],))
    conn.commit()
    kind, target = lead["kind"], lead["target"]
    quiet = lambda *_: None
    try:
        if kind == "wikipedia":
            # read an article's citations; rebuild the dead, and GUARD the living
            # ones that are fragile --- preserving the most endangered before loss.
            cites = wikipedia.cite_templates(target, limit=CITATIONS_PER_ARTICLE)
            by_url: dict[str, list] = {}
            for c in cites:
                by_url.setdefault(c.url, []).append(c)
            built = trail = alive = guarded = foreseen = 0
            for url, group in by_url.items():
                if _already_ghost(conn, url):
                    continue
                live = check(url)
                if live.alive or live.death_kind == "moved":
                    alive += 1
                    # FORESEE: assess a few living citations; mark the fragile at-risk
                    # and preserve the urgent (alive with no archive) within budget.
                    if live.alive and foreseen < FORESEE_PER_ARTICLE and ctx is not None:
                        foreseen += 1
                        frag = fragility(url, known_live=live)
                        if frag and frag.risk >= 0.5:
                            urgent = frag.risk >= 0.95 and ctx["preserved"] < PRESERVE_CAP
                            _, did = foresee_one(conn, url, frag, citation_title=group[0].title,
                                                 preserve=urgent, log=quiet)
                            ctx["preserved"] += 1 if did else 0
                            guarded += 1
                    continue
                res = run_case(conn, url, citations=group, liveness=live, max_fetch=3, log=quiet)
                built += 1
                if res.status == "reconstructed":
                    trail += _follow_trail(conn, res.ghost_id, log)
            log(f"[wikipedia] {target[:42]} -- {len(by_url)} cited, {alive} alive "
                f"({guarded} guarded), built {built}, +{trail} trail")

        elif kind in ("page", "outlink"):
            # confirm it is genuinely lost before building (keeps the graph to ghosts)
            live = check(target)
            if live.alive or live.death_kind == "moved":
                conn.execute("UPDATE lead SET status='skipped', worked_at=datetime('now'), "
                             "note=? WHERE id=?", (f"still living: {live.reason}", lead["id"]))
                conn.commit()
                return
            res = run_case(conn, target, liveness=live, max_fetch=3, log=quiet)
            trail = _follow_trail(conn, res.ghost_id, log) if res.status == "reconstructed" else 0
            tag = "RECOVERED" if res.status == "reconstructed" else res.status.upper()
            log(f"[{kind}] {tag} conf {res.confidence:.2f} +{trail} trail  {target[:58]}")
        elif kind == "places":
            # a page of the world's demolished buildings; queue the next page as
            # long as the query still returns structures (keep walking the class).
            offset = int(target) if target.isdigit() else 0
            n, fetched = recover_structures(conn, limit=PLACES_PER_PAGE, offset=offset, log=quiet)
            if fetched > 0:
                add_lead(conn, "places", str(offset + PLACES_PER_PAGE), priority=0.68)
            log(f"[places] +{n} demolished structures (offset {offset}, {fetched} fetched)")

        elif kind == "texts":
            # a page of antiquity's quoted authors; keep paginating while the query
            # still returns people, even if a page rebuilt nothing parseable.
            offset = int(target) if target.isdigit() else 0
            gids, people = recover_lost_texts(conn, limit=TEXTS_PER_PAGE, offset=offset, log=quiet)
            if people > 0:
                add_lead(conn, "texts", str(offset + TEXTS_PER_PAGE), priority=0.66)
            log(f"[texts] +{len(gids)} lost works rebuilt (offset {offset}, {people} authors)")

        else:
            log(f"[?] unknown lead kind {kind}")

        conn.execute("UPDATE lead SET status='done', worked_at=datetime('now') WHERE id=?", (lead["id"],))
        conn.commit()
    except Exception as e:
        conn.execute("UPDATE lead SET status='failed', worked_at=datetime('now'), note=? WHERE id=?",
                     (f"{type(e).__name__}: {e}", lead["id"]))
        conn.commit()
        log(f"[{kind}] FAILED {target[:54]} --- {type(e).__name__}: {e}")


def stats(conn) -> dict:
    g = conn.execute("SELECT COUNT(*) c FROM ghost").fetchone()["c"]
    rec = conn.execute("SELECT COUNT(*) c FROM ghost WHERE status='reconstructed'").fetchone()["c"]
    pend = conn.execute("SELECT COUNT(*) c FROM lead WHERE status='pending'").fetchone()["c"]
    recog = conn.execute("SELECT COUNT(*) c FROM recognition").fetchone()["c"] // 2
    return {"ghosts": g, "reconstructed": rec, "frontier": pend, "recognitions": recog}


def run(conn, *, max_leads: int | None = None, max_seconds: float | None = None,
        log=print) -> dict:
    """Run continuously, self-seeding from all of knowledge. Resumable."""
    conn.execute("UPDATE lead SET status='pending' WHERE status='working'")  # recover interrupted
    conn.commit()
    seed(conn, log=log)
    start = time.monotonic()
    worked = 0
    quiet = lambda *_: None
    ctx = {"preserved": 0}          # autonomous Save-Page-Now budget for this run
    log("engine running --- all four powers, all media, self-seeding. ctrl-c to pause; it resumes.")
    while True:
        if max_leads is not None and worked >= max_leads:
            log(f"reached lead limit ({max_leads})."); break
        if max_seconds is not None and (time.monotonic() - start) >= max_seconds:
            log(f"reached time limit ({int(max_seconds)}s)."); break
        # keep WEB discovery flowing from the random corpus (judged on random-seed
        # leads alone, so a backlog of trails/pages never starves fresh discovery)
        if _wiki_pending(conn) < TOPUP_BELOW:
            _seed_random(conn, SEED_BATCH, log=log)
        # periodically cross-reference the whole graph: the voids the most sites
        # point at are promoted to the front --- the machine chases its own biggest
        # mysteries, the ghosts it noticed were missing before anyone asked.
        if worked and worked % 120 == 0:
            try:
                ranked, inbound = convergence(conn, log=quiet)
                voids = unrecovered_convergent(conn, ranked, inbound, limit=15)
                rebuilt = promoted = 0
                for i, (url, n) in enumerate(voids):
                    # rebuild the biggest few directly from inbound testimony
                    if i < 2 and reconstruct_void(conn, url, log=quiet):
                        rebuilt += 1
                    elif add_lead(conn, "outlink", url, priority=1.0 + min(3.0, n * 0.25),
                                  note=f"convergent void: {n} sites point here"):
                        promoted += 1
                if rebuilt or promoted:
                    log(f"  convergence: REBUILT {rebuilt} void(s) from shadows, "
                        f"promoted {promoted} to the front")
            except Exception as e:
                log(f"  convergence pass skipped: {type(e).__name__}")
        # round-robin guarantees every medium progresses: the two slow crawls get
        # reserved slots, the web fills the rest --- no medium can monopolize.
        lead = None
        if worked % 4 == 1:
            lead = _pending_of(conn, "places")
        elif worked % 6 == 3:
            lead = _pending_of(conn, "texts")
        if lead is None:
            lead = _next_lead(conn)
        if lead is None:
            # a momentary empty seeding must never end the machine. keep trying.
            got = 0
            for attempt in range(5):
                got = _seed_random(conn, SEED_BATCH, log=log)
                if got:
                    break
                log(f"  (no seeds yet, retrying in {6 * (attempt + 1)}s --- network blip?)")
                time.sleep(6 * (attempt + 1))
            if not got:
                log("could not reach the random corpus after several tries; pausing."); break
            continue
        work_lead(conn, lead, log=log, ctx=ctx)
        worked += 1
        if worked % 10 == 0:
            s = stats(conn)
            log(f"  ... {worked} leads | {s['reconstructed']} recovered, "
                f"{s['frontier']} waiting, {ctx['preserved']} preserved, {s['recognitions']} recognitions")
    s = stats(conn)
    log(f"paused: {s['reconstructed']} recovered, {s['frontier']} leads waiting, "
        f"{s['recognitions']} recognitions. run again to continue.")
    return s
