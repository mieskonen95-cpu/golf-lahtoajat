"""Saved searches ("alerts") that notify when new matching tee times appear.

Storage is two JSON files in the GitHub repo, so alerts survive the web app
sleeping or redeploying:

    alerts/watches.json  the saved searches; written by the web app
    alerts/state.json    what each search last found; written by the checker

The checker (`python -m teetimes check-alerts`) runs in GitHub Actions every
15 minutes and sends a push notification through ntfy (https://ntfy.sh) when a
saved search finds a tee time it did not find before.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from . import query
from .clubs import club_catalog

WATCHES_PATH = "alerts/watches.json"
STATE_PATH = "alerts/state.json"
MAX_ALERTS = 10
MAX_ALERT_CLUBS = 25  # each check queries these clubs; be gentle with WiseGolf
WEEKDAYS = ["ma", "ti", "ke", "to", "pe", "la", "su"]


class AlertError(ValueError):
    pass


# --- storage ------------------------------------------------------------------

class LocalStore:
    """Files in a working copy of the repo (used by the checker in Actions)."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)

    def read(self, path: str) -> tuple[dict, str | None]:
        p = self.root / path
        return (json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}), None

    def write(self, path: str, data: dict, sha: str | None, message: str) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class GitHubStore:
    """Files in the GitHub repo via the contents API (used by the web app)."""

    def __init__(self, repo: str, token: str, branch: str = "main"):
        self.repo, self.token, self.branch = repo, token, branch

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        if method == "GET":
            url += "?" + urlencode({"ref": self.branch})
        req = urllib.request.Request(
            url, method=method, data=json.dumps(body).encode() if body else None,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "teetimes-alerts"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def read(self, path: str) -> tuple[dict, str | None]:
        try:
            meta = self._req("GET", path)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}, None
            raise
        return json.loads(base64.b64decode(meta["content"]).decode("utf-8")), meta["sha"]

    def write(self, path: str, data: dict, sha: str | None, message: str) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        body = {"message": message, "branch": self.branch,
                "content": base64.b64encode(text.encode("utf-8")).decode()}
        if sha:
            body["sha"] = sha
        self._req("PUT", path, body)


def store_from_env():
    """GitHub repo on the server; ALERTS_LOCAL_DIR for running on your own computer."""
    if os.environ.get("ALERTS_LOCAL_DIR"):
        return LocalStore(os.environ["ALERTS_LOCAL_DIR"])
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AlertError("Hälytykset eivät ole käytössä: GITHUB_TOKEN puuttuu palvelimelta.")
    return GitHubStore(os.environ.get("GITHUB_REPO", "mieskonen95-cpu/golf-lahtoajat"),
                       token, os.environ.get("GITHUB_BRANCH", "main"))


# --- alerts -------------------------------------------------------------------

def describe(q: dict[str, str], day: date) -> str:
    """Short Finnish label, e.g. 'la 7.11. · 8 pelaajaa · Tapiola Golf, Master · 8–14'."""
    parts = [f"{WEEKDAYS[day.weekday()]} {day.day}.{day.month}.",
             f"{q.get('players', '2')} pelaajaa"]
    if q.get("clubs"):
        names = {str(c["id"]): c["name"] for c in club_catalog()}
        sel = [names.get(t.strip(), t.strip()) for t in q["clubs"].split(",") if t.strip()]
        parts.append(", ".join(sel[:3]) + (f" +{len(sel) - 3}" if len(sel) > 3 else ""))
    if q.get("near"):
        parts.append(f"≤{q['near']} km {q.get('origin') or 'Tapiolasta'}")
    if q.get("tier"):
        parts.append({"budget": "edulliset", "premium": "premium"}[q["tier"]])
    if q.get("holes"):
        parts.append(f"{q['holes']} r")
    if q.get("from") and q.get("to"):
        parts.append(f"klo {q['from']}–{q['to']}")
    elif q.get("from"):
        parts.append(f"klo {q['from']} alkaen")
    elif q.get("to"):
        parts.append(f"klo {q['to']} mennessä")
    return " · ".join(parts)


def new_alert(qs: dict, now: datetime) -> tuple[dict, list[str]]:
    """Validate a search for alerting; returns the alert and its query args."""
    q = query.clean(qs)
    args = query.parse(q)
    if args["day"] < now.date():
        raise AlertError("Päivä on jo mennyt.")
    q["date"] = args["day"].isoformat()  # "la" etc. must not drift to next week
    ids = args["club_ids"]
    if ids is None or len(ids) > MAX_ALERT_CLUBS:
        raise AlertError(f"Rajaa hälytys enintään {MAX_ALERT_CLUBS} seuraan "
                         "(valitse seurat tai 'Lähellä').")
    alert = {"id": secrets.token_hex(4), "created": now.isoformat(timespec="seconds"),
             "query": q, "label": describe(q, args["day"])}
    return alert, args


def add_alert(store, qs: dict, now: datetime) -> dict:
    alert, _ = new_alert(qs, now)
    day, result = query.execute(alert["query"])
    alert["baseline"] = sorted(m.key for m in result.matches)
    alert["baseline_count"] = len(result.matches)
    data, sha = store.read(WATCHES_PATH)
    watches = [w for w in data.get("watches", []) if not _expired(w, now)]
    if len(watches) >= MAX_ALERTS:
        raise AlertError(f"Enintään {MAX_ALERTS} hälytystä kerrallaan. Poista jokin ensin.")
    watches.append(alert)
    store.write(WATCHES_PATH, {"watches": watches}, sha, f"Add alert: {alert['label']} [skip render]")
    return alert


def delete_alert(store, alert_id: str, now: datetime) -> None:
    data, sha = store.read(WATCHES_PATH)
    watches = data.get("watches", [])
    keep = [w for w in watches if w["id"] != alert_id and not _expired(w, now)]
    if len(keep) == len(watches):
        raise AlertError("Hälytystä ei löytynyt.")
    store.write(WATCHES_PATH, {"watches": keep}, sha, f"Remove alert {alert_id} [skip render]")


def list_alerts(store, now: datetime) -> list[dict]:
    data, _ = store.read(WATCHES_PATH)
    state, _ = store.read(STATE_PATH)
    out = []
    for w in data.get("watches", []):
        if _expired(w, now):
            continue
        s = state.get(w["id"], {})
        out.append({"id": w["id"], "label": w["label"], "query": w["query"],
                    "created": w["created"], "checked_at": s.get("checked_at"),
                    "notified_at": s.get("notified_at"),
                    "count": len(s.get("seen", w.get("baseline", [])))})
    return out


def _expired(w: dict, now: datetime) -> bool:
    return w["query"]["date"] < now.date().isoformat()


# --- checking -----------------------------------------------------------------

def check_all(watches: list[dict], state: dict, now: datetime, notify,
              app_url: str) -> dict:
    """Run every saved search; notify about tee times not seen before.

    Returns the new state. `notify(title, message, click_url)` sends one push.
    """
    new_state = {}
    for w in watches:
        if _expired(w, now):
            continue
        prev = state.get(w["id"], {})
        seen = set(prev.get("seen", w.get("baseline", [])))
        try:
            day, result = query.execute(w["query"])
        except Exception as e:  # keep checking the other alerts
            new_state[w["id"]] = {**prev, "error": str(e)[:200],
                                  "checked_at": now.isoformat(timespec="seconds")}
            continue
        current = {m.key: m for m in result.matches}
        fresh = [current[k] for k in sorted(set(current) - seen)]
        entry = {"seen": sorted(current), "checked_at": now.isoformat(timespec="seconds"),
                 "notified_at": prev.get("notified_at")}
        incomplete = bool(result.errors)
        if incomplete:
            # Courses we could not read must not look like "all booked" next time.
            entry["seen"] = sorted(set(current) | seen)
        if fresh:
            click = f"{app_url.rstrip('/')}/?{urlencode(w['query'])}"
            try:
                notify(f"Vapautui: {w['label']}", _message(fresh), click)
                entry["notified_at"] = now.isoformat(timespec="seconds")
            except Exception as e:
                # Not announced: leave these out of "seen" so the next run retries.
                entry["seen"] = sorted(set(entry["seen"]) - {m.key for m in fresh})
                entry["error"] = f"ilmoitus epäonnistui: {e}"[:200]
                print(f"Ilmoitus epäonnistui ({w['id']}): {e}")
        new_state[w["id"]] = entry
    return new_state


def _message(fresh) -> str:
    lines = []
    for m in fresh[:8]:
        when = m.starts[0].strftime("%H:%M")
        if len(m.starts) > 1:
            when += "–" + m.starts[-1].strftime("%H:%M")
        price = next((f" {o.price:.0f} €" for o in m.options if o.price), "")
        lines.append(f"{when} {m.club.strip()} ({'+'.join(map(str, m.free))} vapaana){price}")
    if len(fresh) > 8:
        lines.append(f"… ja {len(fresh) - 8} muuta")
    return "\n".join(lines)


def _meaningful(state: dict) -> dict:
    """State without timestamps of routine checks (no commit for those)."""
    return {k: {f: v for f, v in e.items() if f != "checked_at"} for k, e in state.items()}


def ntfy_sender(topic: str, server: str = "https://ntfy.sh"):
    def send(title: str, message: str, click: str) -> None:
        body = {"topic": topic, "title": title, "message": message, "click": click,
                "tags": ["golf"], "priority": 4}
        req = urllib.request.Request(server.rstrip("/"), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
    return send


def run_checker(root: str | Path, now: datetime, dry_run: bool = False) -> int:
    """Entry point for GitHub Actions. Returns the number of notifications sent."""
    store = LocalStore(root)
    watches = store.read(WATCHES_PATH)[0].get("watches", [])
    state = store.read(STATE_PATH)[0]
    sent: list[tuple[str, str, str]] = []
    topic = os.environ.get("NTFY_TOPIC")
    real = ntfy_sender(topic, os.environ.get("NTFY_SERVER") or "https://ntfy.sh") if topic else None

    def notify(title, message, click):
        sent.append((title, message, click))
        print(f"NOTIFY {title}\n{message}\n{click}\n")
        if real and not dry_run:
            real(title, message, click)

    if not topic and not dry_run:
        print("NTFY_TOPIC puuttuu: ilmoituksia ei lähetetä.")
    new_state = check_all(watches, state, now, notify,
                          os.environ.get("APP_URL") or "https://golf-lahtoajat.onrender.com")
    if _meaningful(new_state) != _meaningful(state) and not dry_run:
        store.write(STATE_PATH, new_state, None, "Update alert state")
    print(f"{len(watches)} hälytystä tarkistettu, {len(sent)} ilmoitusta.")
    return len(sent)
