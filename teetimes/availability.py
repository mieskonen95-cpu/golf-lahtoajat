"""Turn a WiseGolf calendar + tee sheet into bookable tee times.

This mirrors what the WiseGolf web app shows a guest (not logged in) for a
golf calendar (reservation type 5). The logic follows the app's own
time-table code (`generateDayTimes`); rule names are WiseGolf's Finnish ones:

- aikaSulku               time closed (competition, maintenance, ...)
- kuumatAjat              "etulähtö": bookable no earlier than N minutes before start
- kalenteriNakyvyysPaivat how many days ahead the calendar is open
- kalenteriNakyvyysAvaus  local time at which the last day opens
- tasoitusraja            max combined handicap of the group, by group size
- resurssiPoikkeus        per-day override of first/last start and interval
- resurssiYliajo          offset of the first start

Where the app's behaviour is ambiguous we choose the conservative reading
(a restriction applies), so a time we report as free really is bookable.

All datetimes are naive local time (Europe/Helsinki), like the API's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

GOLF_CALENDAR_TYPE = 5
MIN_TEE_INTERVAL = 5  # minutes; shorter "intervals" are shotgun-start calendars


@dataclass
class TeeTime:
    start: datetime
    resource_id: int
    resource_name: str
    capacity: int
    free: int
    price: float | None
    hot_minutes: int | None = None  # etulähtö: opens this many minutes before start
    notes: list[str] = field(default_factory=list)

    @property
    def bookable(self) -> bool:
        return self.free > 0


@dataclass
class CourseDay:
    """Bookable tee times for one course (product) on one day."""

    product_id: int
    product_name: str
    holes: int | None
    interval: int
    tee_times: list[TeeTime]
    handicap_limits: dict[str, float]  # "default" or group size -> max combined hcp
    calendar_open: bool = True
    closed_reason: str | None = None
    list_price: float | None = None  # highest guest green fee of the day


def list_price(settings: dict, day: date) -> float | None:
    """The course's highest guest green fee on `day` (base price or price rules).

    Used to tell budget courses from premium ones, so it ignores cheap
    twilight times on purpose.
    """
    prices = [_to_float(settings.get("price"))]
    for res in golf_resources(settings):
        for r in res.get("priceRules") or []:
            if not _weekday_ok(r, day):
                continue
            ds, de = _parse_date(r.get("dateStart")), _parse_date(r.get("dateEnd"))
            if (ds and day < ds) or (de and day > de):
                continue
            prices.append(_to_float(r.get("newPrice")))
    prices = [p for p in prices if p]
    return max(prices) if prices else None


# --- helpers -----------------------------------------------------------------

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def _parse_time(s: str) -> time:
    if s == "24:00:00":
        return time(23, 59, 59)
    return datetime.strptime(s[:8], "%H:%M:%S").time()


def _parse_date(s: str | None) -> date | None:
    return datetime.strptime(s[:10], "%Y-%m-%d").date() if s else None


def _weekday_ok(rule: dict, d: date) -> bool:
    """recurrenceDays is Monday-first in the API."""
    days = rule.get("recurrenceDays")
    if isinstance(days, str):
        import json
        days = json.loads(days)
    if isinstance(days, list) and len(days) == 7:
        return bool(days[d.weekday()])
    return True


def rule_applies_on(rule: dict, d: date) -> bool:
    """Date range (startDate/endDate, both inclusive) and weekday."""
    if not _weekday_ok(rule, d):
        return False
    sd, ed = _parse_date(rule.get("startDate")), _parse_date(rule.get("endDate"))
    if sd and d < sd:
        return False
    if ed and d > ed:
        return False
    return True


def rule_covers(rule: dict, start: datetime, end: datetime) -> bool:
    """Whether a time block lies inside the rule's time window (app's `mB`)."""
    if not _weekday_ok(rule, start.date()):
        return False
    ds, de = rule.get("dateStart"), rule.get("dateEnd")
    if ds and de:  # price rules: datetime range
        lo = datetime.combine(_parse_date(ds), _parse_time(rule.get("startTime") or "00:00:00"))
        hi = datetime.combine(_parse_date(de), _parse_time(rule.get("endTime") or "23:59:59"))
        if not (start >= lo and end <= hi):
            return False
    st, et = rule.get("startTime"), rule.get("endTime")
    if isinstance(st, str) and isinstance(et, str):
        lo = datetime.combine(start.date(), _parse_time(st))
        hi = datetime.combine(start.date(), _parse_time(et))
        if hi < lo:
            hi += timedelta(days=1)
        if not (start >= lo and end <= hi):
            return False
    return True


def _rules(all_rules: list[dict], name: str, resource_id: int) -> list[dict]:
    return [r for r in all_rules
            if r.get("ruleName") == name and r.get("resourceId") in (None, resource_id)]


def _user_or_common(rules: list[dict]) -> list[dict]:
    """The app prefers category rules that apply to the user, else general rules.

    As a guest no category rule applies to us (isUserRule false), and rules for
    categories we don't have are ignored.
    """
    mine = [r for r in rules if r.get("categoryId") and r.get("isUserRule")]
    return mine or [r for r in rules if not r.get("categoryId")]


def _minutes(rule: dict) -> int:
    v = rule.get("ruleValue")
    if isinstance(v, dict):
        return int(v.get("minutes") or 0)
    return int(v or 0)


# --- calendar-level checks ---------------------------------------------------

def booking_window_days(settings: dict, rules: list[dict], today: date) -> int:
    vis = [r for r in rules if r.get("ruleName") == "kalenteriNakyvyysPaivat"
           and r.get("isUserRule") and rule_applies_on(r, today)]
    if vis:
        return max(int(r.get("ruleValue") or 0) for r in vis)
    limit = settings.get("limitFutureReservations")
    if isinstance(limit, int) and limit:
        return limit
    return 365


def calendar_closed_reason(settings: dict, rules: list[dict], day: date,
                           now: datetime) -> str | None:
    """Why the calendar for `day` can't be booked yet, or None if it can."""
    sd, ed = _parse_date(settings.get("startDate")), _parse_date(settings.get("endDate"))
    if sd and day < sd:
        return f"kausi alkaa {sd:%d.%m.%Y}"
    if ed and day > ed:
        return f"kausi päättyi {ed:%d.%m.%Y}"
    if not _weekday_ok(settings, day):
        return "suljettu tänä viikonpäivänä"
    ahead = (day - now.date()).days
    if ahead < 0:
        return "päivä on mennyt"
    window = booking_window_days(settings, rules, now.date())
    if ahead > window:
        return f"varaus aukeaa {window} pv ennen"
    if ahead + 1 >= window:
        opening = [r for r in rules if r.get("ruleName") == "kalenteriNakyvyysAvaus"]
        if opening:
            local = (opening[0].get("ruleValue") or {}).get("localTime")
            if local:
                h, m = (int(x) for x in local.split(":")[:2])
                if now.time() < time(h, m):
                    return f"varaus aukeaa tänään klo {local}"
    return None


def handicap_limits(rules: list[dict], resource_id: int) -> dict[str, float]:
    """Max combined handicap; general rules only (guest view)."""
    out: dict[str, float] = {}
    for r in _user_or_common(_rules(rules, "tasoitusraja", resource_id)):
        v = r.get("ruleValue")
        if not isinstance(v, dict):
            continue
        if isinstance(v.get("default"), (int, float)):
            out.setdefault("default", v["default"])
        for size, limit in (v.get("groupSizes") or {}).items():
            out.setdefault(str(size), limit)
    return out


def handicap_limit_for(limits: dict[str, float], players: int) -> float | None:
    if str(players) in limits:
        return limits[str(players)]
    return limits.get("default")


# --- tee times ---------------------------------------------------------------

def _day_times(settings: dict, resource: dict, rules: list[dict], day: date):
    """First start, last start bound and interval for one tee on one day."""
    start = datetime.combine(day, _parse_time(settings["startTime"]))
    end = datetime.combine(day, _parse_time(settings["endTime"]))
    interval = int(settings.get("duration") or 10)
    rid = resource["resourceId"]
    exc = [r for r in rules if r.get("ruleName") == "resurssiPoikkeus"
           and r.get("resourceId") in (None, rid) and rule_applies_on(r, day)]
    exc = [r for r in exc if r.get("isUserRule")] or [r for r in exc if not r.get("isUserRule")]
    if exc:
        r = exc[0]
        if r.get("startTime"):
            start = datetime.combine(day, _parse_time(r["startTime"]))
        if r.get("endTime"):
            end = datetime.combine(day, _parse_time(r["endTime"]))
        if (r.get("ruleValue") or {}).get("duration"):
            interval = int(r["ruleValue"]["duration"])
    if end <= start:
        end += timedelta(days=1)
    over = _rules(rules, "resurssiYliajo", rid)
    if over and (over[0].get("ruleValue") or {}).get("startOffset"):
        start += timedelta(minutes=int(over[0]["ruleValue"]["startOffset"]))
    return start, end, interval


def tee_times_for_resource(settings: dict, resource: dict, rules: list[dict],
                           reservations: list[dict], day: date,
                           now: datetime) -> list[TeeTime]:
    rid = resource["resourceId"]
    capacity = int(resource.get("quantity") or 4)
    first, last, interval = _day_times(settings, resource, rules, day)
    step = interval + int(settings.get("breakTime") or 0)
    base_price = _to_float(settings.get("price"))

    day_rules = [r for r in rules if rule_applies_on(r, day)]
    closures = _user_or_common(_rules(day_rules, "aikaSulku", rid))
    hot_rules = _rules(day_rules, "kuumatAjat", rid)
    price_rules = [r for r in resource.get("priceRules") or [] if _weekday_ok(r, day)]
    disallow_h = settings.get("disallowReservationsWithin") or 0
    not_before = now + timedelta(hours=int(disallow_h)) if disallow_h else None

    mine = [r for r in reservations
            if any(x.get("resourceId") == rid for x in r.get("resources") or [])]
    counted: set = set()
    out: list[TeeTime] = []
    t = first
    while t < last:
        end = t + timedelta(minutes=interval)
        if t <= now or (not_before and t < not_before):
            t += timedelta(minutes=step)
            continue
        # Reservations in this block (each counted once, like the app does).
        used = 0
        for r in mine:
            key = r.get("reservationTimeId")
            if key in counted:
                continue
            rs, re_ = _parse_dt(r["start"]), _parse_dt(r["end"])
            if abs((rs - t).total_seconds()) / 60 >= step * 2:
                continue
            if rs < end and re_ > t:
                counted.add(key)
                used += next((int(x.get("quantity") or 0) for x in r["resources"]
                              if x.get("resourceId") == rid), 0)
        free = max(capacity - used, 0)
        notes: list[str] = []

        if any(rule_covers(r, t, end) for r in closures):
            free = 0
            notes.append("suljettu")

        hot = _pick_hot_rule([r for r in hot_rules if rule_covers(r, t, end)])
        hot_minutes = _minutes(hot) if hot else None
        if hot_minutes and now < t - timedelta(minutes=hot_minutes):
            free = 0
            notes.append(f"etulähtö, vapautuu {_fmt_minutes(hot_minutes)} ennen")

        price = base_price
        pr = next((r for r in price_rules if rule_covers(r, t, end)), None)
        if pr is not None and pr.get("newPrice") is not None:
            price = _to_float(pr["newPrice"])
        if not price:  # 0 means the calendar has no price, not a free round
            price = None

        out.append(TeeTime(start=t, resource_id=rid,
                           resource_name=resource.get("resourceName") or "",
                           capacity=capacity, free=free, price=price,
                           hot_minutes=hot_minutes, notes=notes))
        t += timedelta(minutes=step)
    return out


def _pick_hot_rule(rules: list[dict]) -> dict | None:
    best = None
    for r in rules:
        if r.get("categoryId") and r.get("isUserRule"):
            if best is None or _minutes(r) > _minutes(best):
                best = r
    if best:
        return best
    return next((r for r in rules if not r.get("categoryId")), None)


def _fmt_minutes(m: int) -> str:
    if m % 1440 == 0:
        return f"{m // 1440} vrk"
    if m % 60 == 0:
        return f"{m // 60} h"
    return f"{m} min"


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def golf_resources(settings: dict) -> list[dict]:
    """Tees of a golf calendar; simulators, ranges etc. have no golf category."""
    return [r for r in settings.get("resources") or []
            if str(r.get("resourceCategory") or "").startswith("golf")
            and r.get("active", 1)]


def is_golf_calendar(settings: dict) -> bool:
    return (settings.get("type") == GOLF_CALENDAR_TYPE
            and int(settings.get("duration") or 0) >= MIN_TEE_INTERVAL
            and bool(golf_resources(settings)))


def holes_of(settings: dict, product_name: str) -> int | None:
    h = str(settings.get("holes") or "").strip()
    if h.isdigit():
        return int(h)
    cats = {r.get("resourceCategory") for r in golf_resources(settings)}
    name = product_name.lower()
    if "9r" in name.replace(" ", "") or "9 rei" in name or cats == {"golf9"}:
        return 9
    if "18" in name or cats == {"golf18"}:
        return 18
    return None


def course_day(calendar: dict, tee_sheet: dict, day: date, now: datetime) -> CourseDay:
    settings = calendar["reservationSettings"]
    rules = calendar.get("resourceRules") or []
    name = settings.get("productName") or ""
    resources = golf_resources(settings)
    limits = handicap_limits(rules, resources[0]["resourceId"]) if resources else {}
    cd = CourseDay(product_id=settings["productId"], product_name=name,
                   holes=holes_of(settings, name),
                   interval=int(settings.get("duration") or 10)
                   + int(settings.get("breakTime") or 0),
                   tee_times=[], handicap_limits=limits,
                   list_price=list_price(settings, day))
    reason = calendar_closed_reason(settings, rules, day, now)
    if reason:
        cd.calendar_open, cd.closed_reason = False, reason
        return cd
    rows = tee_sheet.get("rows") or []
    for res in resources:
        cd.tee_times.extend(tee_times_for_resource(settings, res, rules, rows, day, now))
    cd.tee_times.sort(key=lambda x: (x.start, x.resource_id))
    return cd
