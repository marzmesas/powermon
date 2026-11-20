#!/usr/bin/env python3
"""Work on dashboard.html against a real powermon, with no deploy step.

Serves the dashboard.html next to this file and forwards api/ calls to a
running powermon. The page reloads itself whenever the file is saved.

    ssh -N -L 8788:127.0.0.1:8787 <user>@<your-server> &    # arrives as loopback: no token
    python3 dev.py                                          # http://localhost:8790

Or point it straight at the server:

    POWERMON_TOKEN=... python3 dev.py http://<your-server>:8787
"""
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).resolve().parent / "dashboard.html"
UPSTREAM = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788").rstrip("/")
PORT = int(os.environ.get("PORT", "8790"))
RELOAD = b"""<script>
let seen; setInterval(async () => {
  const m = await (await fetch("__mtime")).text();
  if (seen && m !== seen) location.reload();
  seen = m;
}, 700);
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.reply(200, "text/html; charset=utf-8", PAGE.read_bytes() + RELOAD)
        elif path == "/__mtime":
            self.reply(200, "text/plain", str(PAGE.stat().st_mtime_ns).encode())
        else:
            self.forward()

    def forward(self):
        # Writes too, so the Settings dialog works: the body and the two
        # headers the write API insists on go through unchanged.
        headers = {"X-Powermon-Token": os.environ.get("POWERMON_TOKEN", "")}
        for name in ("Content-Type", "X-Powermon-Write"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        request = urllib.request.Request(UPSTREAM + self.path, data=body,
                                         method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as r:
                self.reply(r.status, r.headers.get("Content-Type", "application/json"), r.read())
        except urllib.error.HTTPError as exc:
            self.reply(exc.code, exc.headers.get("Content-Type", "text/plain"), exc.read())
        except OSError as exc:
            self.reply(502, "text/plain", f"cannot reach {UPSTREAM}: {exc}".encode())

    do_POST = do_DELETE = forward

    def reply(self, status, ctype, body):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"dashboard.html on http://localhost:{PORT}, data from {UPSTREAM}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
