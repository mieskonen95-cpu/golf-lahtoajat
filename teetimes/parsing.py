"""Parse dates and times typed in Finnish or ISO form."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .search import now_local

WEEKDAYS = ["ma", "ti", "ke", "to", "pe", "la", "su"]


def parse_day(s: str) -> date:
    s = s.strip().lower()
    today = now_local().date()
    if s in ("tänään", "today", "t"):
        return today
    if s in ("huomenna", "tomorrow", "h"):
        return today + timedelta(days=1)
    if s in WEEKDAYS:
        return today + timedelta(days=(WEEKDAYS.index(s) - today.weekday()) % 7)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m."):
        try:
            d = datetime.strptime(s, fmt).date()
            return d.replace(year=today.year) if fmt == "%d.%m." else d
        except ValueError:
            pass
    raise ValueError(f"tuntematon päivä: {s}")


def parse_time(s: str) -> time:
    s = s.strip().replace(".", ":")
    return datetime.strptime(s if ":" in s else f"{s}:00", "%H:%M").time()
