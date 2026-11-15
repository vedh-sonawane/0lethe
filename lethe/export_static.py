"""Freezing the field so the world can find it.

Writes the whole dark field to a self-contained folder of static files --- the
same index/soot/scratch front-end, plus the graph dumped to JSON. No server, no
database, no dependencies: drop the folder on any free static host (or open it
from disk) and the field is there, exactly as the machine left it.

The engine keeps running and recovering; re-run this to refresh the public
snapshot. That is the zero-budget shape of it: a machine that runs on your own
computer, quietly pushing new memory out to a place the world can stumble onto.

    python -m lethe.cli export [dir]      # default: ./site
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import api, db

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
_FRONT = ("index.html", "field.css", "field.js")

_DEPLOY = """This folder IS the field --- a static copy, no server needed.

Look at it now:
  open index.html in a browser (or run: python -m http.server --directory . 8000)

Put it where the world can find it (all free):
  - Vercel:  `npm i -g vercel` then run `vercel` inside this folder (or drag it
             into the Vercel dashboard). It is detected as a static site; no build.
  - GitHub Pages / Cloudflare Pages / Netlify: push or drag this folder.

To refresh after the machine has recovered more:
  python -m lethe.cli export <this folder>
"""

# Static project, no build step; let the dark page own the viewport and keep the
# JSON fresh-ish. Vercel serves this folder as-is.
_VERCEL = """{
  "cleanUrls": false,
  "trailingSlash": false,
  "headers": [
    { "source": "/field.json", "headers": [{ "key": "Cache-Control", "value": "public, max-age=300" }] },
    { "source": "/ghosts/(.*)", "headers": [{ "key": "Cache-Control", "value": "public, max-age=3600" }] }
  ]
}
"""


def export(out_dir: str = "site", *, log=print) -> int:
    out = Path(out_dir)
    (out / "ghosts").mkdir(parents=True, exist_ok=True)
    for name in _FRONT:
        shutil.copyfile(WEB / name, out / name)
    (out / "DEPLOY.txt").write_text(_DEPLOY, encoding="utf-8")
    (out / "vercel.json").write_text(_VERCEL, encoding="utf-8")

    conn = db.connect()
    try:
        marks = api.field_marks(conn)
        (out / "field.json").write_text(json.dumps(marks), encoding="utf-8")
        n = 0
        for m in marks:
            detail = api.ghost_detail(conn, m["id"])
            if detail is not None:
                (out / "ghosts" / f"{m['id']}.json").write_text(
                    json.dumps(detail), encoding="utf-8")
                n += 1
            if n % 500 == 0 and n:
                log(f"  ... {n} reconstructions written")
    finally:
        conn.close()

    log(f"froze {len(marks)} marks + {n} reconstructions into {out}/")
    log(f"open {out}/index.html , or host the folder (see {out}/DEPLOY.txt)")
    return len(marks)
