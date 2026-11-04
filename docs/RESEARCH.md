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
  Response shapes verified 2026-11-04 (see "Verified API behaviour" below).
- **GolfBox**: in Finland mostly for competitions. Add only if a non-WiseGolf club nearby books via GolfBox.

## Verified API behaviour (2026-11-04)
- **No login needed for search.** All GETs below work anonymously; the guest view
  hides player names ("Varattu"). Login only adds rules for the user's own
  member categories, so the app does not use credentials at all.
- `GET products/?select=productid,meta,type,price,active&type=6`: reservable products.
  Golf calendars are those whose calendarsettings have `type: 5` and resources with
  `resourceCategory` `golf9`/`golf18`. Others are simulators, ranges, gyms and
  shotgun-start calendars (interval 1 min).
- `GET reservations/calendarsettings/?productid=&date=` returns `reservationSettings`
  (start/end time, interval `duration`, `price`, season `startDate`/`endDate`,
  `resources[]` with `quantity` (players per start, usually 4) and `priceRules[]`)
  plus `resourceRules[]` and `golfProducts[]`.
- `GET reservations/?productid=&date=&golf=1` returns `rows[]`: one row per booking,
  `start`, `end`, `resources[{resourceId, quantity}]`, `status`.
  Free places = resource quantity minus the summed quantities of overlapping rows.
- 9-hole and 18-hole products often share one resource (the same first tee and tee
  sheet), so results are merged per tee and start time.
- Several clubs can share a host (e.g. espoogolf.fi = Espoon Golfseura + Gumböle);
  `resources[].clubId` gives the actual club.
- `recurrenceDays` arrays are Monday-first.
- Rules (`ruleName`) that the search applies, following the web app's `generateDayTimes`:
  `aikaSulku` (closed), `kuumatAjat` ("etulähtö": bookable at the earliest
  `ruleValue.minutes` before start), `kalenteriNakyvyysPaivat` (days ahead),
  `kalenteriNakyvyysAvaus` (time the last day opens), `tasoitusraja` (max
  combined handicap, `default` and `groupSizes`), `resurssiPoikkeus`, `resurssiYliajo`.
  Rules with a `categoryId` only apply to members of that category; as a guest
  they are ignored, except that general rules always apply.
- Rules not used yet: `vierailijaHinta` (visitor price for a category member's guest),
  `kymppikortti` (series card), `jatkolahto` (price to continue another 9 holes),
  `pelioikeus` (play rights), `minuuttiHinta`, `lohkoAsetukset`.
- Booking deep links are not consistent between club sites; results link to
  https://app.wisegolf.fi/#/golf/welcome and the club's own site.
- A full search of all clubs for one day takes about 15 s (16 parallel workers).

## Next steps
1. Last-minute ("äkkilähtö") and prepay discounts: these are probably only visible
   via `POST carts/pricecheck/`, which needs checking before relying on it.
2. Inventory non-WiseGolf clubs (golf.fi club list) and their booking systems.
3. Backlog: driving-time filter (origin Tapiola), phone geolocation, multi-day range.
