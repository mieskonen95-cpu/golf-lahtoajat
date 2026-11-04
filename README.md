# golf-lahtoajat – vapaat lähtöajat

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

## Hosting on Render (for phone use)

`render.yaml` sets up a free Render web service. In Render: **New → Blueprint**,
connect this GitHub repo, pick the branch, and apply. Render then redeploys on
every push. On the phone, open the `…onrender.com` address and use
"Add to Home Screen". The free tier sleeps when idle, so the first search
after a break takes longer.

## Finding courses

- **Seurat:** tick clubs in the dropdown (search by club or town). Empty = all clubs.
- **Lähellä:** clubs within 20–200 km of Tapiola or of any club town
  (straight-line distance; locations in `data/club_locations.tsv`).
- **Edulliset / Premium:** by the course's highest 18-hole guest fee of the day:
  at most €55 / at least €85 (9-hole courses: twice the 9-hole fee). On a summer
  Saturday 2026 the middle half of courses cost €58–75.

## Alerts ("Hälytä kun vapautuu")

After a search, the alert button saves it. Every 15 minutes GitHub Actions
(`.github/workflows/alerts.yml`) runs the saved searches and sends a push
notification through [ntfy](https://ntfy.sh) when a matching tee time appears
that wasn't there before. Saved searches live in `alerts/watches.json`, what
they last found in `alerts/state.json`.

An alert must be limited to at most 25 clubs (pick clubs or use "Lähellä"),
at most 10 alerts at a time, and alerts end after their day.

Setup (once):

1. **ntfy:** install the ntfy app, subscribe to a hard-to-guess topic name.
2. **GitHub repo → Settings → Secrets and variables → Actions:** secret
   `NTFY_TOPIC` = that topic name.
3. **GitHub token** for the web app: fine-grained token with *Contents: read and
   write* on this repo only.
4. **Render → Environment:** `GITHUB_TOKEN` = that token, `ALERT_PIN` = a PIN you
   choose (asked once per phone when you create or delete alerts).

Scheduled workflows run only on the default branch (`main`), and GitHub may run
them some minutes late.

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
- `teetimes/search.py` – group matching, price tiers, parallel search over all clubs
- `teetimes/query.py` – search parameters shared by the page and alerts
- `teetimes/alerts.py` – saved searches, checker, ntfy push
- `teetimes/server.py`, `teetimes/static/index.html` – local web UI (Finnish)
- `data/wisegolf_clubs.tsv` – club hosts, extracted from the WiseGolf app bundle
- `data/club_locations.tsv` – club towns (from each club's website) and approximate coordinates
- `docs/RESEARCH.md` – API notes and decisions

## Tests

```bash
python3 -m unittest discover -s tests -t .
```
