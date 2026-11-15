"""The one shape of the field, read from the graph.

Both the live server and the static export read the field through here, so the
darkness looks identical whether it is served by a running machine or sitting
frozen on a free static host that the world stumbled onto.
"""

from __future__ import annotations

from . import db


def field_marks(conn) -> list[dict]:
    """Every mark on the field: one per ghost the machine has touched."""
    rows = conn.execute(
        "SELECT g.id, g.status, g.label, g.identity, g.field_x, g.field_y, g.kind, "
        "g.existed_from, g.existed_until, g.confirmed_gone_at, g.reference_count, "
        "g.worth, g.death_kind, g.risk, g.epoch_year, r.confidence, "
        "(SELECT COUNT(*) FROM claim c JOIN reconstruction rr ON rr.id=c.reconstruction_id "
        " WHERE rr.ghost_id=g.id AND c.status='recovered') AS n_recovered, "
        "(SELECT COUNT(*) FROM shadow s WHERE s.ghost_id=g.id) AS n_shadows "
        "FROM ghost g LEFT JOIN reconstruction r ON r.id = "
        "(SELECT id FROM reconstruction WHERE ghost_id=g.id ORDER BY id DESC LIMIT 1)"
    ).fetchall()
    return [dict(r) for r in rows]


def ghost_detail(conn, ghost_id: int) -> dict | None:
    g = conn.execute("SELECT * FROM ghost WHERE id=?", (ghost_id,)).fetchone()
    if not g:
        return None
    rec = conn.execute(
        "SELECT * FROM reconstruction WHERE ghost_id=? ORDER BY id DESC LIMIT 1",
        (ghost_id,)).fetchone()
    claims = []
    if rec:
        for c in conn.execute(
            "SELECT * FROM claim WHERE reconstruction_id=? ORDER BY "
            "CASE status WHEN 'recovered' THEN 0 WHEN 'inferred' THEN 1 ELSE 2 END",
            (rec["id"],)):
            trail = [dict(t) for t in conn.execute(
                "SELECT s.kind, s.uri, s.captured_at FROM claim_shadow cs "
                "JOIN shadow s ON s.id=cs.shadow_id WHERE cs.claim_id=? ORDER BY s.captured_at",
                (c["id"],))]
            claims.append({**dict(c), "trail": trail})
    shadows = [dict(s) for s in conn.execute(
        "SELECT kind, captured_at, status_code FROM shadow WHERE ghost_id=? ORDER BY captured_at",
        (ghost_id,))]
    recognitions = [dict(r) for r in conn.execute(
        "SELECT rc.other_ghost_id, rc.similarity, rc.kind, g2.label, g2.identity, g2.kind AS g_kind "
        "FROM recognition rc JOIN ghost g2 ON g2.id=rc.other_ghost_id "
        "WHERE rc.ghost_id=? ORDER BY rc.similarity DESC NULLS LAST LIMIT 24", (ghost_id,))]
    return {"ghost": dict(g), "reconstruction": dict(rec) if rec else None,
            "claims": claims, "shadows": shadows, "recognitions": recognitions}
