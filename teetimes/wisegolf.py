"""Minimal anonymous client for the WiseGolf REST API.

No login is used: the public (guest) view returns the tee sheet with player
names hidden, which is all a search needs.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "teetimes/0.1 (personal tee time search)"
TIMEOUT = 20


class ApiError(Exception):
    pass


def get_json(url: str, params: dict | None = None, retries: int = 2) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "x-session-type": "wisegolf",
                 "User-Agent": USER_AGENT},
    )
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise ApiError(f"HTTP {e.code} for {url}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    raise ApiError(f"{type(last).__name__}: {last} for {url}")


def list_products(rest_url: str) -> list[dict]:
    """Reservable products (type 6). Golf courses are among them."""
    data = get_json(f"{rest_url}/products/",
                    {"select": "productid,meta,type,price,active", "type": 6})
    return [p for p in data.get("rows") or [] if p.get("active")]


def calendar_settings(rest_url: str, product_id: int, date: str) -> dict:
    data = get_json(f"{rest_url}/reservations/calendarsettings/",
                    {"productid": product_id, "date": date})
    if not data.get("success"):
        raise ApiError(f"calendarsettings failed: {data.get('error')}")
    return data


def reservations(rest_url: str, product_id: int, date: str) -> dict:
    data = get_json(f"{rest_url}/reservations/",
                    {"productid": product_id, "date": date, "golf": 1})
    if not data.get("success"):
        raise ApiError(f"reservations failed: {data.get('error') or data.get('errors')}")
    return data
