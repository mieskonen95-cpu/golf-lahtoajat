"""WiseGolf club hosts, loaded from data/wisegolf_clubs.tsv."""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_FILE = DATA_DIR / "wisegolf_clubs.tsv"
LOCATIONS_FILE = DATA_DIR / "club_locations.tsv"

# Default origin for "nearby": Tapiola, Espoo.
DEFAULT_ORIGIN = ("Tapiola (Espoo)", 60.175, 24.805)


@dataclass(frozen=True)
class Host:
    """One WiseGolf installation. Several clubs can share a host."""

    name: str  # e.g. "tapiolagolf"
    display_name: str
    domain: str
    rest_url: str
    golf_club_ids: tuple[int, ...]
    club_names: tuple[tuple[int, str], ...] = ()

    def club_id(self, club_id) -> int:
        """The calendar's club, or the host's own club if the calendar has none."""
        try:
            cid = int(club_id)
            if cid in self.golf_club_ids:
                return cid
        except (TypeError, ValueError):
            pass
        return self.golf_club_ids[0]

    def club_name(self, club_id) -> str:
        return dict(self.club_names).get(self.club_id(club_id), self.display_name)

    @property
    def site_url(self) -> str:
        return f"https://{self.domain}/"


def load_hosts(path: Path = DATA_FILE) -> list[Host]:
    """Return one Host per distinct REST URL, skipping test and non-club entries."""
    by_url: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            url = row["rest_url"].strip()
            if not url or not row["golf_club_id"].strip():
                continue
            club_id = int(row["golf_club_id"])
            if club_id >= 99990:  # WiseGolf guest/network/hall pseudo-clubs
                continue
            entry = by_url.setdefault(
                url,
                {"name": row["name"], "display_name": row["display_name"],
                 "domain": row["domain"], "ids": []},
            )
            entry["ids"].append(club_id)
            entry.setdefault("names", []).append((club_id, row["display_name"]))
    return [
        Host(name=e["name"], display_name=e["display_name"], domain=e["domain"],
             rest_url=url, golf_club_ids=tuple(e["ids"]),
             club_names=tuple(e["names"]))
        for url, e in by_url.items()
    ]


@dataclass(frozen=True)
class Location:
    place: str
    municipality: str
    region: str
    lat: float
    lon: float


@lru_cache(maxsize=1)
def load_locations(path: Path = LOCATIONS_FILE) -> dict[int, Location]:
    out: dict[int, Location] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            out[int(row["golf_club_id"])] = Location(
                row["place"], row["municipality"], row["region"],
                float(row["lat"]), float(row["lon"]))
    return out


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance (haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def origins() -> list[tuple[str, float, float]]:
    """Places to measure "nearby" from: Tapiola plus every club municipality."""
    by_muni: dict[str, list[Location]] = {}
    for loc in load_locations().values():
        by_muni.setdefault(loc.municipality, []).append(loc)
    out = [DEFAULT_ORIGIN]
    for muni in sorted(by_muni):
        locs = by_muni[muni]
        out.append((muni, sum(l.lat for l in locs) / len(locs),
                    sum(l.lon for l in locs) / len(locs)))
    return out


def find_origin(name: str | None) -> tuple[str, float, float]:
    if name:
        for o in origins():
            if o[0].lower() == name.strip().lower():
                return o
    return DEFAULT_ORIGIN


def club_catalog() -> list[dict]:
    """Every WiseGolf club with its location, for the club picker."""
    locs = load_locations()
    seen, out = set(), []
    for h in load_hosts():
        for cid, name in h.club_names:
            if cid in seen:
                continue
            seen.add(cid)
            loc = locs.get(cid)
            out.append({"id": cid, "name": name.strip(), "host": h.name,
                        "municipality": loc.municipality if loc else None,
                        "region": loc.region if loc else None,
                        "lat": loc.lat if loc else None, "lon": loc.lon if loc else None})
    return sorted(out, key=lambda c: c["name"].lower())
