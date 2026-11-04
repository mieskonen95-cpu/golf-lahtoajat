"""WiseGolf club hosts, loaded from data/wisegolf_clubs.tsv."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "wisegolf_clubs.tsv"


@dataclass(frozen=True)
class Host:
    """One WiseGolf installation. Several clubs can share a host."""

    name: str  # e.g. "tapiolagolf"
    display_name: str
    domain: str
    rest_url: str
    golf_club_ids: tuple[int, ...]
    club_names: tuple[tuple[int, str], ...] = ()

    def club_name(self, club_id) -> str:
        try:
            return dict(self.club_names)[int(club_id)]
        except (KeyError, TypeError, ValueError):
            return self.display_name

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
