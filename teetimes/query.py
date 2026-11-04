"""Search parameters shared by the web UI and saved alerts.

A query is a flat dict of strings (the page's URL parameters):

    date      2026-05-16 / 16.5. / la / huomenna
    players   1-16
    from, to  earliest / latest start, e.g. 8:00
    holes     9 or 18
    max_gap   minutes allowed between consecutive starts (big groups)
    clubs     comma-separated club ids (e.g. "134,41") or name fragments
    near      radius in km, e.g. 40
    origin    place to measure `near` from (default Tapiola)
    tier      budget | premium
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date

from .clubs import club_catalog, distance_km, find_origin, load_locations
from .parsing import parse_day, parse_time
from .search import TIERS, SearchResult, now_local, search

MAX_PLAYERS = 16
KEYS = ("date", "players", "from", "to", "holes", "max_gap", "clubs", "near", "origin", "tier")


def clean(qs: dict) -> dict[str, str]:
    """Known, non-empty parameters only."""
    return {k: str(qs[k]).strip() for k in KEYS if qs.get(k) not in (None, "")}


def resolve_club_ids(q: dict[str, str]) -> set[int] | None:
    """Clubs selected by `clubs` and `near`; None means all clubs."""
    ids: set[int] | None = None
    if q.get("clubs"):
        catalog = club_catalog()
        ids = set()
        for term in (t.strip() for t in q["clubs"].split(",")):
            if not term:
                continue
            if term.isdigit():
                ids.add(int(term))
            else:
                ids |= {c["id"] for c in catalog if term.lower() in c["name"].lower()
                        or term.lower() in c["host"]}
    if q.get("near"):
        radius = float(q["near"])
        _, lat, lon = find_origin(q.get("origin"))
        near = {cid for cid, loc in load_locations().items()
                if distance_km(lat, lon, loc.lat, loc.lon) <= radius}
        ids = near if ids is None else ids & near
    return ids


def parse(q: dict[str, str]) -> dict:
    players = int(q.get("players") or 2)
    if not 1 <= players <= MAX_PLAYERS:
        raise ValueError(f"pelaajia 1–{MAX_PLAYERS}")
    tier = q.get("tier") or None
    if tier and tier not in TIERS:
        raise ValueError(f"tuntematon hintaluokka: {tier}")
    return {
        "day": parse_day(q.get("date") or "huomenna"),
        "players": players,
        "earliest": parse_time(q["from"]) if q.get("from") else None,
        "latest": parse_time(q["to"]) if q.get("to") else None,
        "holes": int(q["holes"]) if q.get("holes") in ("9", "18") else None,
        "max_gap": int(q["max_gap"]) if q.get("max_gap") else None,
        "club_ids": resolve_club_ids(q),
        "tier": tier,
    }


def _run(args: dict) -> SearchResult:
    a = dict(args)
    return search(a.pop("day"), a.pop("players"), **a)


def execute(q: dict[str, str]) -> tuple[date, SearchResult]:
    args = parse(q)
    day = args["day"]
    result = _run(args)
    _, lat, lon = find_origin(q.get("origin"))
    locs = load_locations()
    for m in result.matches:
        loc = locs.get(m.club_id)
        if loc:
            m.municipality = loc.municipality
            m.distance_km = round(distance_km(lat, lon, loc.lat, loc.lon))
    return day, result


def to_json(q: dict[str, str], day: date, r: SearchResult) -> dict:
    matches = []
    for m in r.matches:
        d = asdict(m)
        d["starts"] = [t.strftime("%H:%M") for t in m.starts]
        d["key"] = m.key
        matches.append(d)
    return {
        "query": q, "date": day.isoformat(),
        "players": int(q.get("players") or 2),
        "searched_at": now_local().isoformat(timespec="seconds"),
        "courses_checked": r.courses_checked, "matches": matches,
        "closed": [{"club": c, "course": n, "reason": why} for c, n, why in r.closed],
        "errors": [{"club": c, "message": msg} for c, msg in r.errors],
    }
