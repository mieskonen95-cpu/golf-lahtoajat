# upgraded-octo-fortnight – vapaat lähtöajat

A personal tool that searches free tee times across Finnish golf clubs that use
WiseGolf (about 88 clubs, ~160 courses). It reads each club's tee sheet live on
every search and links you to the booking page. It never books anything.

No login is needed. The search uses WiseGolf's public guest view, where player
names are hidden.

## Run

Requires Python 3.10+ and nothing else (standard library only).

```bash
# Browser UI at http://127.0.0.1:8000/
python3 -m teetimes serve

# Command line
python3 -m teetimes search la -p 4 --from 9 --to 14
python3 -m teetimes search 2026-05-16 -p 8 --clubs tapiola,master,peuramaa --holes 18
python3 -m teetimes search huomenna -p 2 --show-closed
```

Day can be `2026-05-16`, `16.5.`, `ma`…`su`, `tänään` or `huomenna`.

## What counts as a match

| Players | Needs |
|---|---|
| 1–4 | one tee time with at least that many free places |
| 4 | a fully empty tee time |
| 5–8 | consecutive tee times on the same tee, group split evenly (8 → 4+4, 6 → 3+3) |

"Consecutive" means directly after each other. `--max-gap 20` allows a gap
between starts of up to 20 minutes.

A tee time is free only if a guest could book it right now. The search checks:

- the club's season dates and how many days ahead booking is open
- closed times (competitions, maintenance)
- *etulähtö* member times, which open to others N hours before the start
- times that have already passed

Each result shows the guest green fee for that time, and the combined-handicap
limit for your group size when the club has one.

## Layout

- `teetimes/wisegolf.py` – anonymous WiseGolf REST client
- `teetimes/availability.py` – calendar + tee sheet → bookable tee times (mirrors the WiseGolf app's rules)
- `teetimes/search.py` – group matching, parallel search over all clubs
- `teetimes/server.py`, `teetimes/static/index.html` – local web UI (Finnish)
- `data/wisegolf_clubs.tsv` – club hosts, extracted from the WiseGolf app bundle
- `docs/RESEARCH.md` – API notes and decisions

## Tests

```bash
python3 -m unittest discover -s tests -t .
```
