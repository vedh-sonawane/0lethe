"""The threshold.

A local, zero-dependency server that stands between the evidence graph and the
darkness. It never computes or invents; it only hands over what the engine has
already recovered and recorded. The world sees the field; the field is nothing
but the graph, rendered as light scratched out of soot.

    python web/server.py        # then open the printed address
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lethe import api, db  # noqa: E402

WEB = Path(__file__).resolve().parent
_GHOST_RE = re.compile(r"^/api/ghost/(\d+)$")
_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json"}


def field_marks() -> list[dict]:
    conn = db.connect()
    try:
        return api.field_marks(conn)
    finally:
        conn.close()


def ghost_detail(ghost_id: int) -> dict | None:
    conn = db.connect()
    try:
        return api.ghost_detail(conn, ghost_id)
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/field":
            return self._json(field_marks())
        m = _GHOST_RE.match(path)
        if m:
            d = ghost_detail(int(m.group(1)))
            return self._json(d) if d else self._send(404, b"{}", "application/json")
        # static
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB / rel).resolve()
        if WEB not in target.parents and target != WEB / rel or not target.is_file():
            return self._send(404, b"not here", "text/plain")
        ctype = _MIME.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    def log_message(self, *args) -> None:
        pass  # the machine is quiet


def main(port: int = 1860) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"the field is open at  http://127.0.0.1:{port}")
    print("(ctrl-c to close it)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1860)
