"""Minimal anonymous client for the WiseGolf REST API.

No login is used: the public (guest) view returns the tee sheet with player
names hidden, which is all a search needs.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "teetimes/0.1 (personal tee time search)"
TIMEOUT = 20
# The club hosts appear to share one backend that rate-limits per client IP
# (HTTP 429), so all requests from this process share one pace and back off
# together when the limit is hit.
MAX_REQUESTS_PER_SECOND = float(os.environ.get("WISEGOLF_RPS", "8"))
CALENDAR_CACHE_SECONDS = 600  # rules and prices; tee sheets are never cached


class _Pacer:
    def __init__(self, rate: float):
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.interval
        if at > now:
            time.sleep(at - now)

    def back_off(self, seconds: float):
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


_pacer = _Pacer(MAX_REQUESTS_PER_SECOND)


class ApiError(Exception):
    pass


def get_json(url: str, params: dict | None = None, retries: int = 3) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "x-session-type": "wisegolf",
                 "User-Agent": USER_AGENT},
    )
    last: Exception | None = None
    for attempt in range(retries + 1):
        _pacer.wait()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                last = e
                _pacer.back_off(_retry_after(e, attempt))
                continue
            if e.code < 500:
                raise ApiError(f"HTTP {e.code} for {url}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    if isinstance(last, urllib.error.HTTPError) and last.code == 429:
        raise ApiError("WiseGolf rajoitti hakuja (HTTP 429), yritä hetken päästä")
    raise ApiError(f"{type(last).__name__}: {last} for {url}")


def _retry_after(e: urllib.error.HTTPError, attempt: int) -> float:
    try:
        return min(float(e.headers.get("Retry-After", "")), 30.0)
    except ValueError:
        return 2.0 * 2 ** attempt  # 2, 4, 8 s


def list_products(rest_url: str) -> list[dict]:
    """Reservable products (type 6). Golf courses are among them."""
    data = get_json(f"{rest_url}/products/",
                    {"select": "productid,meta,type,price,active", "type": 6})
    return [p for p in data.get("rows") or [] if p.get("active")]


_calendar_cache: dict[tuple, tuple[float, dict]] = {}
_calendar_lock = threading.Lock()


def calendar_settings(rest_url: str, product_id: int, date: str) -> dict:
    """Calendar setup, rules and prices. Cached briefly: they rarely change."""
    key = (rest_url, product_id, date)
    with _calendar_lock:
        hit = _calendar_cache.get(key)
        if hit and time.monotonic() - hit[0] < CALENDAR_CACHE_SECONDS:
            return hit[1]
    data = get_json(f"{rest_url}/reservations/calendarsettings/",
                    {"productid": product_id, "date": date})
    if not data.get("success"):
        raise ApiError(f"calendarsettings failed: {data.get('error')}")
    with _calendar_lock:
        if len(_calendar_cache) > 2000:
            _calendar_cache.clear()
        _calendar_cache[key] = (time.monotonic(), data)
    return data


def reservations(rest_url: str, product_id: int, date: str) -> dict:
    data = get_json(f"{rest_url}/reservations/",
                    {"productid": product_id, "date": date, "golf": 1})
    if not data.get("success"):
        raise ApiError(f"reservations failed: {data.get('error') or data.get('errors')}")
    return data
