"""The evidence graph.

This is the spine of the machine. Nothing the machine ever claims exists
outside this graph, and nothing exists in it without a trail back to a real
shadow. The one law: never invent. The schema enforces it structurally ---
a `claim` cannot be read as "recovered" unless it is tied, through
`claim_shadow`, to at least one `shadow` that was actually observed from a
real `source`.

Storage is a single local SQLite file. Zero budget, zero services, runs on
the user's own machine. It is not a copy of the world; it holds compact
records of what was *seen* and the reconstructions assembled from them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# The whole machine lives under data/. The db is the graph; cache/ holds the
# transient raw bytes of shadows currently under examination (fingerprint,
# don't hoard --- this is working memory for the active case, prunable).
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "lethe.sqlite"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Where shadows come from. Every source the machine reads from is registered
-- here together with the rules it agreed to respect while reading.
CREATE TABLE IF NOT EXISTS source (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,        -- 'wayback', 'wikipedia', 'live'
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- 'web_archive' | 'encyclopedia' | 'liveness'
    terms       TEXT,                        -- the rules of the place, in plain words
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A ghost: something that was referred to but is gone. Detectable because the
-- world keeps pointing at it while the original answers nothing.
CREATE TABLE IF NOT EXISTS ghost (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,             -- 'web_page' for this stage
    identity      TEXT NOT NULL UNIQUE,      -- canonical reference (the URL)
    label         TEXT,                      -- best-known title, once recovered
    status        TEXT NOT NULL DEFAULT 'suspected',
                                             -- suspected -> confirmed -> reconstructed -> living
    reference_count INTEGER NOT NULL DEFAULT 0,  -- how many things point at it (ghostliness)
    worth         REAL NOT NULL DEFAULT 0,   -- is this loss significant, or trivial link-rot?
    death_kind    TEXT,                      -- dead_domain | not_found | moved
    existed_from  TEXT,                      -- earliest evidence it was alive (ISO)
    existed_until TEXT,                      -- latest evidence it was alive (ISO)
    confirmed_gone_at TEXT,                  -- when the machine confirmed it answers nothing
    field_x       REAL,                      -- position on the field (identity-derived)
    field_y       REAL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A shadow: one concrete trace of the ghost found in something that survived.
-- An archived snapshot, a citation pointing at it, a cached copy, a mention.
-- captured_at is when the TRACE was made (e.g. snapshot time); observed_at is
-- when the machine saw the trace.
CREATE TABLE IF NOT EXISTS shadow (
    id           INTEGER PRIMARY KEY,
    ghost_id     INTEGER NOT NULL REFERENCES ghost(id) ON DELETE CASCADE,
    source_id    INTEGER NOT NULL REFERENCES source(id),
    kind         TEXT NOT NULL,              -- 'snapshot'|'citation'|'liveness'|'cache'|'mention'
    uri          TEXT,                       -- where the trace itself lives
    captured_at  TEXT,                       -- when the trace was made (ISO)
    observed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    status_code  INTEGER,                    -- for snapshots/liveness
    mimetype     TEXT,
    digest       TEXT,                       -- content digest reported by the source
    cache_path   TEXT,                       -- local raw bytes, if kept for this case
    meta         TEXT,                       -- JSON, source-specific detail
    UNIQUE(ghost_id, source_id, kind, uri, captured_at)
);

-- A compact signature of recovered content, so a fragment of this ghost can be
-- recognized later inside something else. This is how the machine compounds:
-- fingerprints outlive the raw bytes.
CREATE TABLE IF NOT EXISTS fingerprint (
    id          INTEGER PRIMARY KEY,
    ghost_id    INTEGER NOT NULL REFERENCES ghost(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,               -- 'simhash64_text'
    value       TEXT NOT NULL,               -- hex
    bits        INTEGER NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A reconstruction: one assembled attempt at the ghost, with an overall
-- confidence. A ghost may be reconstructed again later with more shadows.
CREATE TABLE IF NOT EXISTS reconstruction (
    id          INTEGER PRIMARY KEY,
    ghost_id    INTEGER NOT NULL REFERENCES ghost(id) ON DELETE CASCADE,
    method      TEXT NOT NULL,
    confidence  REAL NOT NULL,               -- 0..1, aggregate. never 1.0.
    summary     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A claim: one atomic thing the reconstruction asserts. The heart of the law.
-- status is exactly one of recovered / inferred / missing. confidence is the
-- machine's calibrated belief. A claim with no supporting shadow may only ever
-- be 'inferred' or 'missing' --- never 'recovered'.
CREATE TABLE IF NOT EXISTS claim (
    id                 INTEGER PRIMARY KEY,
    reconstruction_id  INTEGER NOT NULL REFERENCES reconstruction(id) ON DELETE CASCADE,
    field              TEXT NOT NULL,         -- 'title'|'body'|'existed_from'|'died'|'outlinks'|...
    value              TEXT,
    status             TEXT NOT NULL CHECK (status IN ('recovered','inferred','missing')),
    confidence         REAL NOT NULL,
    rationale          TEXT,                  -- why this status and confidence, in words
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The trail back. Every claim points at the shadows that justify it. This table
-- is what makes the machine legendary instead of slop: you can always walk from
-- any assertion to the real evidence that cast it.
CREATE TABLE IF NOT EXISTS claim_shadow (
    claim_id   INTEGER NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
    shadow_id  INTEGER NOT NULL REFERENCES shadow(id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'supports',  -- 'supports' | 'derived_from' | 'contradicts'
    PRIMARY KEY (claim_id, shadow_id, role)
);

-- The frontier: the machine's own to-do list. This is what lets it run
-- continuously and follow trails without a human in the loop. Every solved case
-- can add new leads (a recovered page's outbound links that are themselves gone),
-- so the lost web reconstructs itself outward from a handful of seeds.
CREATE TABLE IF NOT EXISTS lead (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,          -- 'neighborhood' | 'page' | 'outlink'
    target          TEXT NOT NULL,          -- a neighborhood name, or a url
    priority        REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|working|done|failed|skipped
    source_ghost_id INTEGER REFERENCES ghost(id) ON DELETE SET NULL,
    note            TEXT,
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    worked_at       TEXT,
    UNIQUE(kind, target)
);

-- Recognition: when a newly recovered thing's fingerprint matches one already in
-- the graph, the machine has recognized the same lost content somewhere else ---
-- a mirror, a repost, a fragment. This is how it COMPOUNDS and gets smarter.
CREATE TABLE IF NOT EXISTS recognition (
    ghost_id       INTEGER NOT NULL REFERENCES ghost(id) ON DELETE CASCADE,
    other_ghost_id INTEGER NOT NULL REFERENCES ghost(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,           -- 'fingerprint_match'
    distance       INTEGER,
    similarity     REAL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ghost_id, other_ghost_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_shadow_ghost ON shadow(ghost_id);
CREATE INDEX IF NOT EXISTS idx_claim_recon ON claim(reconstruction_id);
CREATE INDEX IF NOT EXISTS idx_recon_ghost ON reconstruction(ghost_id);
CREATE INDEX IF NOT EXISTS idx_fp_value ON fingerprint(value);
CREATE INDEX IF NOT EXISTS idx_lead_queue ON lead(status, priority DESC);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the graph, creating it and its schema if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "cache").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive column migrations, so an older graph keeps working."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(ghost)")}
    for col, decl in (("worth", "REAL NOT NULL DEFAULT 0"), ("death_kind", "TEXT"),
                      ("risk", "REAL NOT NULL DEFAULT 0"),   # risk: fragility (FORESEE)
                      ("epoch_year", "REAL")):               # signed year for all of time
        if col not in have:
            conn.execute(f"ALTER TABLE ghost ADD COLUMN {col} {decl}")
    conn.commit()


def register_source(conn: sqlite3.Connection, key: str, name: str, kind: str, terms: str) -> int:
    """Idempotently register a source and return its id."""
    conn.execute(
        "INSERT INTO source(key, name, kind, terms) VALUES(?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET name=excluded.name, kind=excluded.kind, terms=excluded.terms",
        (key, name, kind, terms),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM source WHERE key=?", (key,)).fetchone()
    return int(row["id"])


if __name__ == "__main__":
    # Materialize the graph and report it, so the foundation is inspectable.
    c = connect()
    tables = [r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"evidence graph at {DB_PATH}")
    for t in tables:
        cols = [r["name"] for r in c.execute(f"PRAGMA table_info({t})")]
        print(f"  {t}({', '.join(cols)})")
