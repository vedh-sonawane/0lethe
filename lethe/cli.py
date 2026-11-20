"""The way in, for the operator.

    python -m lethe.cli run    [--minutes N|--leads N]  run the engine continuously
    python -m lethe.cli case   <url>              investigate one ghost
    python -m lethe.cli geo    [neighborhoods...]  recover lost GeoCities homepages
    python -m lethe.cli hunt   <wikipedia-article> find & work ghosts it cites
    python -m lethe.cli foresee <wikipedia-article> find living things at risk & preserve them
    python -m lethe.cli text   [author [year]]    rebuild a lost ancient work from quoted fragments
    python -m lethe.cli places [limit]            stand up demolished buildings at their real coordinates
    python -m lethe.cli library [limit]           rebuild works humanity knows it lost (the Ghost Library)
    python -m lethe.cli recognize [n]             find lost lines surviving inside surviving books
    python -m lethe.cli views [n]                 recompose any lost thing from its surviving images
    python -m lethe.cli world [n]                 bake the real walkable world around a lost place
    python -m lethe.cli show   <ghost-id>          print a reconstruction + its trail
    python -m lethe.cli ghosts                     the most-referenced missing things (convergence)
    python -m lethe.cli rebuild <url>              rebuild a copy-less page from its inbound shadows
    python -m lethe.cli field                      list what the machine has recovered
    python -m lethe.cli export [dir]               freeze the field into a static, hostable site

The operator uses this. The world sees only the darkness (web/).
"""

from __future__ import annotations

import sys

from . import db
from .case import (discover_from_wikipedia, foresee_wikipedia, gather_views,
                   gather_world, hunt_geocities, recognize_fragments, recover_lost_texts,
                   recover_lost_works, recover_structures, reconstruct_text, run_case)

_STATUS_MARK = {"recovered": "+", "inferred": "~", "missing": "-"}


def _show(conn, ghost_id: int) -> None:
    g = conn.execute("SELECT * FROM ghost WHERE id=?", (ghost_id,)).fetchone()
    if not g:
        print(f"no ghost #{ghost_id}")
        return
    print("=" * 72)
    print(f"GHOST #{g['id']}  [{g['status']}]  {g['identity']}")
    if g["label"]:
        print(f"  \"{g['label']}\"")
    if g["existed_from"]:
        print(f"  alive {g['existed_from'][:10]} .. {g['existed_until'][:10]}"
              + (f"  confirmed gone {g['confirmed_gone_at']}" if g["confirmed_gone_at"] else ""))
    print(f"  references pointing here: {g['reference_count']}")

    rec = conn.execute(
        "SELECT * FROM reconstruction WHERE ghost_id=? ORDER BY id DESC LIMIT 1",
        (ghost_id,)).fetchone()
    if not rec:
        print("  (no reconstruction yet)")
        return
    print(f"\nRECONSTRUCTION #{rec['id']}  confidence {rec['confidence']:.2f}  [{rec['method']}]")
    print(f"  {rec['summary']}")
    print(f"\n  {'legend: + recovered   ~ inferred   - missing'}")
    claims = conn.execute(
        "SELECT * FROM claim WHERE reconstruction_id=? ORDER BY "
        "CASE status WHEN 'recovered' THEN 0 WHEN 'inferred' THEN 1 ELSE 2 END, field",
        (rec["id"],)).fetchall()
    for c in claims:
        mark = _STATUS_MARK.get(c["status"], "?")
        val = (c["value"] or "").replace("\n", " ")
        if len(val) > 90:
            val = val[:87] + "..."
        print(f"\n  [{mark}] {c['field']}  ({c['confidence']:.2f})")
        if val:
            print(f"      {val}")
        print(f"      why: {c['rationale']}")
        trail = conn.execute(
            "SELECT s.kind, s.uri, s.captured_at FROM claim_shadow cs "
            "JOIN shadow s ON s.id=cs.shadow_id WHERE cs.claim_id=?", (c["id"],)).fetchall()
        for t in trail:
            when = f" @{t['captured_at'][:10]}" if t["captured_at"] else ""
            print(f"      <- {t['kind']}{when}  {t['uri'] or ''}")
    print("=" * 72)


def _field(conn) -> None:
    rows = conn.execute(
        "SELECT g.id, g.status, g.label, g.identity, r.confidence "
        "FROM ghost g LEFT JOIN reconstruction r ON r.id = "
        "(SELECT id FROM reconstruction WHERE ghost_id=g.id ORDER BY id DESC LIMIT 1) "
        "ORDER BY g.updated_at DESC").fetchall()
    if not rows:
        print("the field is dark. no ghosts worked yet.")
        return
    print(f"{len(rows)} mark(s) on the field:\n")
    for r in rows:
        conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else "----"
        label = r["label"] or r["identity"]
        print(f"  #{r['id']:<4} [{r['status']:<13}] {conf}  {label[:60]}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    conn = db.connect()
    try:
        if cmd == "run":
            from .engine import run as run_engine
            mins = leads = None
            if "--minutes" in argv:
                mins = float(argv[argv.index("--minutes") + 1]) * 60
            if "--leads" in argv:
                leads = int(argv[argv.index("--leads") + 1])
            run_engine(conn, max_leads=leads, max_seconds=mins)
        elif cmd == "case" and len(argv) >= 3:
            run_case(conn, argv[2])
        elif cmd == "geo":
            hunt_geocities(conn, argv[2:] or None)
        elif cmd == "hunt" and len(argv) >= 3:
            discover_from_wikipedia(conn, " ".join(argv[2:]))
        elif cmd == "foresee" and len(argv) >= 3:
            foresee_wikipedia(conn, " ".join(argv[2:]))
        elif cmd == "places":
            recover_structures(conn, limit=int(argv[2]) if len(argv) >= 3 else 400)
        elif cmd == "library":
            recover_lost_works(conn, limit=int(argv[2]) if len(argv) >= 3 else 60)
        elif cmd == "recognize":
            recognize_fragments(conn, limit_ghosts=int(argv[2]) if len(argv) >= 3 else 10)
        elif cmd == "views":
            gather_views(conn, limit_ghosts=int(argv[2]) if len(argv) >= 3 else 8)
        elif cmd == "world":
            gather_world(conn, limit_ghosts=int(argv[2]) if len(argv) >= 3 else 6)
        elif cmd == "text":
            if len(argv) >= 4 and argv[3].lstrip("-").isdigit():
                reconstruct_text(conn, argv[2], float(argv[3]))
            elif len(argv) >= 3:
                reconstruct_text(conn, argv[2], -500)
            else:
                recover_lost_texts(conn)   # the whole chorus of antiquity
        elif cmd == "show" and len(argv) >= 3:
            _show(conn, int(argv[2]))
        elif cmd == "rebuild" and len(argv) >= 3:
            from .reassemble import reconstruct_void
            gid = reconstruct_void(conn, argv[2])
            if gid:
                _show(conn, gid)
            else:
                print("not enough surviving testimony to rebuild that void")
        elif cmd == "ghosts":
            from .discover import convergence
            ranked, _ = convergence(conn)
            have = {}
            for r in conn.execute("SELECT identity, status FROM ghost"):
                from .discover import _norm
                k = _norm(r["identity"])
                if k:
                    have[k] = r["status"]
            print("\nthe voids the world points at most (by distinct referring sites):\n")
            for u, n in ranked[:30]:
                st = have.get(u, "undiscovered")
                print(f"  {n:>3} sites  [{st:<13}]  {u[:58]}")
        elif cmd == "export":
            from .export_static import export
            export(argv[2] if len(argv) >= 3 else "site")
        elif cmd == "field":
            _field(conn)
        else:
            print(__doc__)
            return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
