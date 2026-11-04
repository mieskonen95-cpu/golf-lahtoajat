"""Search free tee times for a group across WiseGolf clubs."""
from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import wisegolf
from .availability import (CourseDay, TeeTime, course_day, golf_resources,
                           handicap_limit_for, is_golf_calendar)
from .clubs import Host, load_hosts

TZ = ZoneInfo("Europe/Helsinki")
BOOKING_APP_URL = "https://app.wisegolf.fi/#/golf/welcome"


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


@dataclass
class CourseOption:
    """One way to play from a tee time, e.g. 18 holes or 9 holes."""

    course: str
    holes: int | None
    price: float | None  # green fee per player, guest price


@dataclass
class Match:
    """A tee time (or consecutive tee times) that fits the whole group."""

    club: str
    tee_id: int
    tee: str
    starts: list[datetime]
    free: list[int]
    options: list[CourseOption]
    handicap_limit: float | None
    notes: list[str]
    site_url: str
    booking_url: str = BOOKING_APP_URL

    @property
    def course(self) -> str:
        return " / ".join(o.course for o in self.options)

    @property
    def holes(self) -> list[int]:
        return sorted({o.holes for o in self.options if o.holes})


@dataclass
class SearchResult:
    matches: list[Match] = field(default_factory=list)
    closed: list[tuple[str, str, str]] = field(default_factory=list)  # club, course, reason
    errors: list[tuple[str, str]] = field(default_factory=list)  # club, message
    courses_checked: int = 0


def split_group(players: int, capacity: int) -> list[int]:
    """How many players go in each consecutive tee time.

    2 -> [2], 4 -> [4], 8 -> [4, 4], 6 -> [3, 3], 5 -> [3, 2].
    """
    if players < 1:
        raise ValueError("players must be >= 1")
    n = math.ceil(players / capacity)
    base, extra = divmod(players, n)
    return [base + 1] * extra + [base] * (n - extra)


def find_group_slots(tee_times: list[TeeTime], players: int,
                     max_gap: int | None = None,
                     interval: int | None = None) -> list[list[TeeTime]]:
    """Runs of tee times on the same tee that fit the group.

    For one tee time: free >= players. For a bigger group: N strictly
    consecutive tee times (each next one within `max_gap` minutes, default the
    tee interval) with enough free places in each.
    """
    out: list[list[TeeTime]] = []
    by_tee: dict[int, list[TeeTime]] = {}
    for t in tee_times:
        by_tee.setdefault(t.resource_id, []).append(t)
    for times in by_tee.values():
        times.sort(key=lambda t: t.start)
        if not times:
            continue
        sizes = split_group(players, times[0].capacity)
        step = interval or _interval(times)
        gap = timedelta(minutes=max_gap if max_gap is not None else step)
        for i in range(len(times) - len(sizes) + 1):
            run = times[i:i + len(sizes)]
            if all(t.free >= need for t, need in zip(run, sizes)) and all(
                    b.start - a.start <= gap for a, b in zip(run, run[1:])):
                out.append(run)
    return out


def _interval(times: list[TeeTime]) -> int:
    diffs = [int((b.start - a.start).total_seconds() // 60) for a, b in zip(times, times[1:])]
    diffs = [d for d in diffs if d > 0]
    return min(diffs) if diffs else 10


# --- product discovery (cached per process; courses rarely change) ------------

_products: dict[str, list[dict]] = {}
_products_lock = threading.Lock()


def _host_products(host: Host) -> list[dict]:
    with _products_lock:
        if host.rest_url in _products:
            return _products[host.rest_url]
    seen, prods = set(), []
    for p in wisegolf.list_products(host.rest_url):
        if p["productId"] not in seen:
            seen.add(p["productId"])
            prods.append(p)
    with _products_lock:
        _products[host.rest_url] = prods
    return prods


def warm_up(hosts: list[Host] | None = None, workers: int = 8) -> None:
    """Load every club's course list ahead of the first search."""
    def one(h):
        try:
            _host_products(h)
        except wisegolf.ApiError:
            pass
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, hosts if hosts is not None else load_hosts()))


def _search_host(host: Host, day: date, players: int, now: datetime,
                 earliest: time | None, latest: time | None, holes: int | None,
                 max_gap: int | None) -> SearchResult:
    res = SearchResult()
    ds = day.isoformat()
    try:
        products = _host_products(host)
    except wisegolf.ApiError as e:
        res.errors.append((host.display_name, str(e)))
        return res
    for p in products:
        try:
            cal = wisegolf.calendar_settings(host.rest_url, p["productId"], ds)
            settings = cal["reservationSettings"]
            if not is_golf_calendar(settings):
                continue
            cd_probe = course_day(cal, {"rows": []}, day, now)
            if holes and cd_probe.holes and cd_probe.holes != holes:
                continue
            res.courses_checked += 1
            club = host.club_name(golf_resources(settings)[0].get("clubId"))
            if not cd_probe.calendar_open:
                res.closed.append((club, cd_probe.product_name,
                                   cd_probe.closed_reason or ""))
                continue
            sheet = wisegolf.reservations(host.rest_url, p["productId"], ds)
            cd = course_day(cal, sheet, day, now)
        except wisegolf.ApiError as e:
            res.errors.append((host.display_name, f"{p.get('name')}: {e}"))
            continue
        res.matches.extend(_matches(host, club, cd, players, earliest, latest, max_gap))
    res.matches = merge_shared_tees(res.matches)
    return res


def _matches(host: Host, club: str, cd: CourseDay, players: int, earliest: time | None,
             latest: time | None, max_gap: int | None) -> list[Match]:
    times = [t for t in cd.tee_times
             if (earliest is None or t.start.time() >= earliest)
             and (latest is None or t.start.time() <= latest)]
    out = []
    for run in find_group_slots(times, players, max_gap, cd.interval):
        notes = sorted({n for t in run for n in t.notes})
        if any(t.hot_minutes for t in run):
            notes.append("etulähtö (vapautunut)")
        prices = [t.price for t in run if t.price is not None]
        out.append(Match(
            club=club, tee_id=run[0].resource_id, tee=run[0].resource_name,
            starts=[t.start for t in run], free=[t.free for t in run],
            options=[CourseOption(cd.product_name, cd.holes,
                                  max(prices) if prices else None)],
            handicap_limit=handicap_limit_for(cd.handicap_limits, players),
            notes=notes, site_url=host.site_url))
    return out


def merge_shared_tees(matches: list[Match]) -> list[Match]:
    """Clubs often sell 9 and 18 holes from the same first tee as separate
    calendars over one tee sheet. Show such a tee time once, with both options.
    """
    merged: dict[tuple, Match] = {}
    for m in matches:
        key = (m.club, m.tee_id, tuple(m.starts))
        if key not in merged:
            merged[key] = m
            continue
        cur = merged[key]
        cur.options.extend(m.options)
        cur.free = [min(a, b) for a, b in zip(cur.free, m.free)]
        cur.notes = sorted(set(cur.notes) | set(m.notes))
        if m.handicap_limit is not None:
            cur.handicap_limit = (m.handicap_limit if cur.handicap_limit is None
                                  else min(cur.handicap_limit, m.handicap_limit))
    for m in merged.values():
        m.options.sort(key=lambda o: -(o.holes or 0))
    return list(merged.values())


def search(day: date, players: int, *, hosts: list[Host] | None = None,
           earliest: time | None = None, latest: time | None = None,
           holes: int | None = None, max_gap: int | None = None,
           now: datetime | None = None, workers: int = 16) -> SearchResult:
    hosts = hosts if hosts is not None else load_hosts()
    now = now or now_local()
    total = SearchResult()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        parts = ex.map(lambda h: _search_host(h, day, players, now, earliest,
                                              latest, holes, max_gap), hosts)
        for part in parts:
            total.matches.extend(part.matches)
            total.closed.extend(part.closed)
            total.errors.extend(part.errors)
            total.courses_checked += part.courses_checked
    total.matches.sort(key=lambda m: (m.starts[0], m.club, m.tee_id))
    return total


def filter_hosts(hosts: list[Host], query: str | None) -> list[Host]:
    """Comma-separated, case-insensitive substrings of club or host name."""
    if not query:
        return hosts
    terms = [q.strip().lower() for q in query.split(",") if q.strip()]
    return [h for h in hosts
            if any(t in h.display_name.lower() or t in h.name.lower() for t in terms)]
