"""Small web app: the search page plus a JSON API.

GET  /                   the page
GET  /api/search?...     search (parameters: see teetimes/query.py)
GET  /api/clubs          clubs for the picker, with location
GET  /api/origins        places to measure "nearby" from
GET  /api/alerts         saved alerts
POST /api/alerts         save the search in the body as an alert   (needs PIN)
DELETE /api/alerts/<id>  remove an alert                            (needs PIN)

Alerts need GITHUB_TOKEN (and optionally GITHUB_REPO, GITHUB_BRANCH) to save
them in the repo, and ALERT_PIN so strangers with the link can't create them.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import alerts, query
from .clubs import club_catalog, origins
from .search import now_local, warm_up

PAGE = Path(__file__).with_name("static") / "index.html"
# A public deployment shouldn't flood the clubs' servers: run at most this many
# searches at once (each one already queries many clubs in parallel).
MAX_CONCURRENT_SEARCHES = 2
_search_slots = threading.BoundedSemaphore(MAX_CONCURRENT_SEARCHES)


def run_search(qs: dict[str, str]) -> dict:
    q = query.clean(qs)
    day, result = query.execute(q)
    return query.to_json(q, day, result)


def pin_ok(given: str | None) -> bool:
    expected = os.environ.get("ALERT_PIN")
    return bool(expected) and hmac.compare_digest((given or "").encode(), expected.encode())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/healthz":
            self._send(200, b"ok", "text/plain")
        elif url.path == "/api/clubs":
            self._json(200, {"clubs": club_catalog()})
        elif url.path == "/api/origins":
            self._json(200, {"origins": [o[0] for o in origins()]})
        elif url.path == "/api/alerts":
            self._alerts_call(lambda: {
                "alerts": alerts.list_alerts(alerts.store_from_env(), now_local())})
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

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/alerts":
            return self._send(404, b"not found", "text/plain")
        if not self._check_pin():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(length, 10_000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "virheellinen pyyntö"})
        self._alerts_call(lambda: {"alert": alerts.add_alert(
            alerts.store_from_env(), body.get("query") or {}, now_local(),
            body.get("repeat"))}, created=True)

    def do_DELETE(self):  # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith("/api/alerts/"):
            return self._send(404, b"not found", "text/plain")
        if not self._check_pin():
            return
        alert_id = path.rsplit("/", 1)[-1]

        def delete():
            alerts.delete_alert(alerts.store_from_env(), alert_id, now_local())
            return {"ok": True}
        self._alerts_call(delete)

    def _check_pin(self) -> bool:
        if not os.environ.get("ALERT_PIN"):
            self._json(503, {"error": "Hälytykset eivät ole käytössä: ALERT_PIN puuttuu palvelimelta."})
            return False
        if not pin_ok(self.headers.get("X-Alert-Pin")):
            self._json(403, {"error": "Väärä PIN."})
            return False
        return True

    def _alerts_call(self, fn, created: bool = False):
        try:
            self._json(201 if created else 200, fn())
        except ValueError as e:  # includes AlertError: bad input
            self._json(400, {"error": str(e)})
        except Exception as e:  # GitHub or WiseGolf failure
            self._json(502, {"error": f"Hälytyspalvelu ei vastannut: {e}"})

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
