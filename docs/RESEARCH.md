# Research notes

## Goal
Personal tool: live search of free tee times across Finnish golf courses.
Default origin: Tapiola, Espoo. Home club: Tapiola Golf. UI language: Finnish.
Data correctness is the priority; UI is secondary.

## Agreed requirements
- Search only; link to the club's booking page.
- Group size 2 / 4 / 8 (generalised):
  - 2 players -> a tee time with >= 2 free places
  - 4 players -> a fully empty tee time (max 4 per start)
  - 8 players -> N fully empty, strictly consecutive tee times (optional max gap, e.g. 30 min)
- Results show: time, course / 9 or 18 holes, green fee incl. discounts
  (twilight, last-minute "äkkilähtö", prepay "ennakkomaksajan etuhinta"),
  handicap limits, member-only / guest restrictions.
- Live query on every search.
- Backlog: driving-time filter (default origin Tapiola), phone geolocation, multi-day range.

## Data sources
- **NexGolf**: shut down 28.2.2026 -> not needed.
- **Golfpiste Teetime / teetime.fi**: aggregator on top of NexGolf+WiseGolf; golfpiste.com/teetime
  now redirects to the front page, teetime.fi does not respond -> not needed.
- **WiseGolf**: main source. ~107 clubs in `data/wisegolf_clubs.tsv` (extracted from the
  app.wisegolf.fi bundle `WiseSettings-*.js`). Tapiola Golf = golfClubId 134.
  Each club has its own hosts: `https://api.<domain>/api/1.0` (REST) and `https://ajax.<domain>`.
  Login (user account = email + password): `useRestLogin: true`, `POST ${restUrl}/auth` (+ `${baseUrl}?action=ajaxauthorize&appauth=<appauth>`).
  Endpoints seen in the web app:
  - `GET reservations/initialization/?...`
  - `GET reservations/calendarsettings/?productid=&date=`
  - `GET reservations/?productid=&date=[&golf=1][&week=1]` (tee sheet)
  - `GET reservations/getresourcereservations/?resources=&dateTimeStart=&dateTimeEnd=`
  - `GET golf/club/`, `golf/club/associated/`
  - `POST carts/pricecheck/`, `GET benefit/{id}/availability`
  Response shapes NOT yet verified (club API hosts were blocked by the network policy).
- **GolfBox**: in Finland mostly for competitions. Add only if a non-WiseGolf club nearby books via GolfBox.

## Next steps
1. Verify WiseGolf API responses with a real login (env vars WISEGOLF_USER, WISEGOLF_PASSWORD; network access Full).
2. Inventory non-WiseGolf clubs (golf.fi club list) and their booking systems.
3. Build plan, then implementation.
