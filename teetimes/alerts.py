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
from datetime import date, datetime, timedelta
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
#
# A one-day alert has query["date"]. A recurring alert has no date in its query
# but a "repeat": {"days": [5, 6], "start": "2027-05-01", "end": "2027-09-30"}
# (weekdays Monday=0). Recurring alerts only check days within HORIZON_DAYS:
# calendars further ahead are not open yet, and WiseGolf rate-limits us.
#
# What an alert has seen is stored as "<date>|<club>:<tee>:<starts>" keys.

HORIZON_DAYS = 14
MAX_REPEAT_DAYS = 400


def _fmt_day(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.day}.{d.month}."


def _filters(q: dict[str, str]) -> list[str]:
    parts = [f"{q.get('players', '2')} pelaajaa"]
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
    return parts


def describe(q: dict[str, str], day: date | None = None, repeat: dict | None = None) -> str:
    """Short Finnish label, e.g. 'la+su 1.5.–30.9.2027 · 8 pelaajaa · Tapiola Golf'."""
    if repeat:
        s, e = date.fromisoformat(repeat["start"]), date.fromisoformat(repeat["end"])
        when = "+".join(WEEKDAYS[i] for i in sorted(repeat["days"]))
        when += f" {s.day}.{s.month}.–{e.day}.{e.month}.{e.year}"
    else:
        when = _fmt_day(day)
    return " · ".join([when] + _filters(q))


def parse_repeat(repeat: dict | None, now: datetime) -> dict | None:
    if not repeat:
        return None
    try:
        days = sorted({int(d) for d in repeat.get("days") or []})
        start = date.fromisoformat(str(repeat.get("start") or now.date().isoformat()))
        end = date.fromisoformat(str(repeat["end"]))
    except (KeyError, TypeError, ValueError):
        raise AlertError("Toistuvalle hälytykselle tarvitaan viikonpäivät ja aikaväli.")
    if not days or any(d < 0 or d > 6 for d in days):
        raise AlertError("Valitse ainakin yksi viikonpäivä.")
    if end < start:
        raise AlertError("Aikavälin loppu on ennen alkua.")
    if end < now.date():
        raise AlertError("Aikaväli on jo mennyt.")
    if (end - start).days > MAX_REPEAT_DAYS:
        raise AlertError("Aikaväli voi olla enintään vuoden.")
    return {"days": days, "start": start.isoformat(), "end": end.isoformat()}


def new_alert(qs: dict, now: datetime, repeat: dict | None = None) -> tuple[dict, dict]:
    """Validate a search for alerting; returns the alert and its query args."""
    q = query.clean(qs)
    args = query.parse(q)
    rep = parse_repeat(repeat, now)
    ids = args["club_ids"]
    if ids is None or len(ids) > MAX_ALERT_CLUBS:
        raise AlertError(f"Rajaa hälytys enintään {MAX_ALERT_CLUBS} seuraan "
                         "(valitse seurat tai 'Lähellä').")
    alert = {"id": secrets.token_hex(4), "created": now.isoformat(timespec="seconds")}
    if rep:
        q.pop("date", None)
        alert.update(query=q, repeat=rep, label=describe(q, repeat=rep), initialized=False)
    else:
        if args["day"] < now.date():
            raise AlertError("Päivä on jo mennyt.")
        q["date"] = args["day"].isoformat()  # "la" etc. must not drift to next week
        alert.update(query=q, label=describe(q, args["day"]))
    return alert, args


def add_alert(store, qs: dict, now: datetime, repeat: dict | None = None) -> dict:
    alert, _ = new_alert(qs, now, repeat)
    if not alert.get("repeat"):
        # One-day alert: what the search shows now is already known to you.
        day, result = query.execute(alert["query"])
        alert["baseline"] = sorted(dated_key(day, m) for m in result.matches)
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
        days = dates_for(w, now.date())
        q = dict(w["query"])
        if days and "date" not in q:
            q["date"] = days[0].isoformat()  # link opens the next checked day
        today = now.date().isoformat()
        seen = [k for k in _seen(w, s) if k.split("|", 1)[0] >= today]
        out.append({"id": w["id"], "label": w["label"], "query": q,
                    "repeat": w.get("repeat"), "created": w["created"],
                    "checked_at": s.get("checked_at"), "notified_at": s.get("notified_at"),
                    "next_days": [d.isoformat() for d in days], "count": len(seen)})
    return out


def dated_key(day: date, m) -> str:
    return f"{day.isoformat()}|{m.key}"


def _seen(w: dict, s: dict) -> set[str]:
    keys = s.get("seen", w.get("baseline", []))
    d = w["query"].get("date")
    # Keys saved before dates were added: they belong to the alert's only day.
    return {k if "|" in k else f"{d}|{k}" for k in keys}


def dates_for(w: dict, today: date) -> list[date]:
    """The days this alert checks now."""
    if w.get("repeat"):
        r = w["repeat"]
        first = max(date.fromisoformat(r["start"]), today)
        last = min(date.fromisoformat(r["end"]), today + timedelta(days=HORIZON_DAYS))
        out, d = [], first
        while d <= last:
            if d.weekday() in r["days"]:
                out.append(d)
            d += timedelta(days=1)
        return out
    d = date.fromisoformat(w["query"]["date"])
    return [d] if d >= today else []


def _expired(w: dict, now: datetime) -> bool:
    end = w["repeat"]["end"] if w.get("repeat") else w["query"]["date"]
    return end < now.date().isoformat()


# --- checking -----------------------------------------------------------------

def check_all(watches: list[dict], state: dict, now: datetime, notify,
              app_url: str) -> dict:
    """Run every saved search; notify about tee times not seen before.

    Returns the new state. `notify(title, message, click_url)` sends one push
    per alert and run. For a recurring alert, a day checked for the first time
    is announced as "Varattavissa" (its booking just opened); later changes as
    "Vapautui". The first run of a new recurring alert only records what is
    free, without a push.
    """
    new_state = {}
    today, stamp = now.date(), now.isoformat(timespec="seconds")
    for w in watches:
        if _expired(w, now):
            continue
        prev = state.get(w["id"], {})
        seen = _seen(w, prev)
        known_days = set(prev.get("days", []))
        silent = bool(w.get("repeat")) and not prev.get("initialized") \
            and w.get("initialized") is False
        current: dict[str, tuple[date, object]] = {}
        keep: set[str] = set()  # keys of days we couldn't read completely
        errors, opened, checked = [], [], []
        for day in dates_for(w, today):
            q = {**w["query"], "date": day.isoformat()}
            try:
                _, result = query.execute(q)
            except Exception as e:  # keep checking other days and alerts
                errors.append(f"{day}: {e}"[:200])
                keep |= {k for k in seen if k.startswith(f"{day}|")}
                continue
            checked.append(day.isoformat())
            for m in result.matches:
                current[dated_key(day, m)] = (day, m)
            if result.errors:
                # Courses we could not read must not look like "all booked" next time.
                keep |= {k for k in seen if k.startswith(f"{day}|")}
            if w.get("repeat") and day.isoformat() not in known_days \
                    and any(k.startswith(f"{day}|") for k in current):
                opened.append(day)
        fresh = [current[k] for k in sorted(set(current) - seen)]
        entry = {"seen": sorted(set(current) | keep),
                 "days": sorted(known_days | set(checked)),
                 "checked_at": stamp, "notified_at": prev.get("notified_at")}
        if w.get("repeat"):
            entry["initialized"] = True
        if errors:
            entry["error"] = "; ".join(errors)[:300]
        if fresh and not silent:
            first_day = fresh[0][0]
            click = f"{app_url.rstrip('/')}/?{urlencode({**w['query'], 'date': first_day.isoformat()})}"
            all_opened = all(d in opened for d, _ in fresh)
            title = ("Varattavissa: " if all_opened else "Vapautui: ") + w["label"]
            try:
                notify(title, _message(fresh, dated=bool(w.get("repeat"))), click)
                entry["notified_at"] = stamp
            except Exception as e:
                # Not announced: leave these out of "seen" so the next run retries.
                entry["seen"] = sorted(set(entry["seen"]) - {dated_key(d, m) for d, m in fresh})
                entry["days"] = sorted(known_days)
                entry["error"] = f"ilmoitus epäonnistui: {e}"[:200]
                print(f"Ilmoitus epäonnistui ({w['id']}): {e}")
        new_state[w["id"]] = entry
    return new_state


def _message(fresh, dated: bool = False) -> str:
    lines = []
    for day, m in fresh[:10]:
        when = m.starts[0].strftime("%H:%M")
        if len(m.starts) > 1:
            when += "–" + m.starts[-1].strftime("%H:%M")
        if dated:
            when = f"{_fmt_day(day)} {when}"
        price = next((f" {o.price:.0f} €" for o in m.options if o.price), "")
        lines.append(f"{when} {m.club.strip()} ({'+'.join(map(str, m.free))} vapaana){price}")
    if len(fresh) > 10:
        lines.append(f"… ja {len(fresh) - 10} muuta")
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
        # Fail loudly: without a topic every alert would look "sent" but nothing arrives.
        raise SystemExit("NTFY_TOPIC puuttuu: lisää se repon Settings → Secrets and "
                         "variables → Actions → Secrets. Mitään ei tarkistettu.")
    new_state = check_all(watches, state, now, notify,
                          os.environ.get("APP_URL") or "https://golf-lahtoajat.onrender.com")
    if _meaningful(new_state) != _meaningful(state) and not dry_run:
        store.write(STATE_PATH, new_state, None, "Update alert state")
    print(f"{len(watches)} hälytystä tarkistettu, {len(sent)} ilmoitusta.")
    return len(sent)
