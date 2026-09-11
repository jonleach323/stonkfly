"""Local watch site: serves site/ and computes the API from a run directory.

The same JSON the Vercel functions serve from published snapshots is built
here on demand, so the page works on a laptop with no hosting at all.
"""

import json
import threading
import time
import urllib.error
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .publish import audit, snapshot
from .satrush.api import ENDPOINTS, SatRushApi

SITE = Path(__file__).resolve().parent.parent / "site"


class BoardCache:
    """Server-side proxy for the public board; one upstream call per second at most."""

    def __init__(self, api, ttl=1.0, clock=time.time):
        self.api = api
        self.ttl = ttl
        self.clock = clock
        self.lock = threading.Lock()
        self.value = None
        self.at = 0.0

    def get(self):
        with self.lock:
            now = self.clock()
            if self.value is None or now - self.at > self.ttl:
                board = self.api.board()
                self.value = {**board.summary(), "live": True}
                self.at = now
            return self.value


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, run_dir=None, board=None, **kwargs):
        self.run_dir = run_dir
        self.board = board
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, format, *args):  # Quiet by default; the loop prints enough.
        pass

    def _json(self, payload, status=200, cache="no-store"):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", cache)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/state":
                return self._json(snapshot(self.run_dir))
            if path == "/api/audit":
                return self._json(audit(self.run_dir))
            if path == "/api/board":
                if self.board is None:
                    return self._json({"live": False, "error": "no board source"}, 503)
                try:
                    return self._json(self.board.get())
                except (urllib.error.URLError, RuntimeError, ValueError) as e:
                    return self._json({"live": False, "error": type(e).__name__}, 502)
            if path == "/api/sensory.png":
                frame = Path(self.run_dir) / "latest-input.png"
                if not frame.exists():
                    self.send_error(404)
                    return
                data = frame.read_bytes()
                self.send_response(200)
                self.send_header("content-type", "image/png")
                self.send_header("cache-control", "no-store")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        except Exception as e:  # Never let a snapshot bug take the page down.
            return self._json({"ready": False, "error": type(e).__name__}, 500)
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()


def make_server(run_dir, host="127.0.0.1", port=8787, network="mainnet", board_api=None):
    api = board_api or SatRushApi(ENDPOINTS[network]["api"])
    handler = partial(Handler, run_dir=Path(run_dir), board=BoardCache(api))
    return ThreadingHTTPServer((host, port), handler)


def serve(run_dir, host="127.0.0.1", port=8787, network="mainnet"):
    server = make_server(run_dir, host, port, network)
    print(json.dumps({"watch": f"http://{host}:{port}/", "run": str(run_dir)}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
