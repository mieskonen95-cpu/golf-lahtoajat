"""Small local web UI: GET / (page) and GET /api/search (JSON)."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .parsing import parse_day, parse_time
from .clubs import load_hosts
from .search import filter_hosts, now_local, search, warm_up

PAGE = Path(__file__).with_name("static") / "index.html"
MAX_PLAYERS = 16
# A public deployment shouldn't flood the clubs' servers: run at most this many
# searches at once (each one already queries all clubs in parallel).
MAX_CONCURRENT_SEARCHES = 2
_search_slots = threading.BoundedSemaphore(MAX_CONCURRENT_SEARCHES)


def run_search(qs: dict[str, str]) -> dict:
    day = parse_day(qs.get("date") or "huomenna")
    players = int(qs.get("players") or 2)
    if not 1 <= players <= MAX_PLAYERS:
        raise ValueError(f"pelaajia 1–{MAX_PLAYERS}")
    earliest = parse_time(qs["from"]) if qs.get("from") else None
    latest = parse_time(qs["to"]) if qs.get("to") else None
    holes = int(qs["holes"]) if qs.get("holes") in ("9", "18") else None
    max_gap = int(qs["max_gap"]) if qs.get("max_gap") else None
    hosts = filter_hosts(load_hosts(), qs.get("clubs"))
    r = search(day, players, hosts=hosts, earliest=earliest, latest=latest,
               holes=holes, max_gap=max_gap)
    matches = []
    for m in r.matches:
        d = asdict(m)
        d["starts"] = [t.strftime("%H:%M") for t in m.starts]
        matches.append(d)
    return {
        "date": day.isoformat(), "players": players,
        "searched_at": now_local().isoformat(timespec="seconds"),
        "courses_checked": r.courses_checked, "matches": matches,
        "closed": [{"club": c, "course": n, "reason": why} for c, n, why in r.closed],
        "errors": [{"club": c, "message": msg} for c, msg in r.errors],
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/healthz":
            self._send(200, b"ok", "text/plain")
        elif url.path == "/api/search":
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            if not _search_slots.acquire(blocking=False):
                self._json(429, {"error": "Palvelin on kiireinen, yritä hetken päästä uudelleen."})
                return
            try:
                self._json(200, run_search(qs))
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # upstream failure
                self._json(502, {"error": f"Haku epäonnistui: {e}"})
            finally:
                _search_slots.release()
        else:
            self._send(404, b"not found", "text/plain")

    def _json(self, code: int, body: dict):
        self._send(code, json.dumps(body, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def serve(host: str = "127.0.0.1", port: int = 8000):
    httpd = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=warm_up, daemon=True).start()
    print(f"Avaa selaimessa http://{host}:{port}/  (Ctrl+C lopettaa)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
