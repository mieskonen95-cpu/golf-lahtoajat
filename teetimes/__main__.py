"""Command line: python -m teetimes [search options] | serve."""
from __future__ import annotations

import argparse
import os
import sys

from .clubs import load_hosts
from .parsing import parse_day, parse_time
from .search import filter_hosts, search


def fmt_match(m) -> str:
    when = m.starts[0].strftime("%H:%M")
    if len(m.starts) > 1:
        when += "–" + m.starts[-1].strftime("%H:%M") + f" ({len(m.starts)} lähtöä)"
    opts = ", ".join(
        o.course.strip() + (f" ({o.holes} r)" if o.holes and str(o.holes) not in o.course else "")
        + (f" {o.price:.0f} €" if o.price is not None else " hinta ?")
        for o in m.options)
    free = "+".join(str(f) for f in m.free)
    extra = []
    if m.handicap_limit is not None:
        extra.append(f"tasoitus yht. max {m.handicap_limit:g}")
    extra += m.notes
    tail = f"  [{', '.join(extra)}]" if extra else ""
    return f"{when:<22} {m.club.strip()} – {opts}, vapaana {free}{tail}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="teetimes", description="Vapaat lähtöajat (WiseGolf)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("search", help="hae lähtöaikoja")
    s.add_argument("day", type=parse_day, help="päivä: 2026-05-16, 16.5., la, huomenna")
    s.add_argument("-p", "--players", type=int, default=2, help="pelaajia (oletus 2)")
    s.add_argument("--from", dest="earliest", type=parse_time, help="aikaisin lähtö, esim. 8:00")
    s.add_argument("--to", dest="latest", type=parse_time, help="myöhäisin lähtö, esim. 14:00")
    s.add_argument("--holes", type=int, choices=[9, 18])
    s.add_argument("--clubs", help="seurat pilkulla eroteltuna, esim. 'tapiola,master'")
    s.add_argument("--max-gap", type=int, help="isolle ryhmälle: suurin väli lähtöjen välillä (min)")
    s.add_argument("--show-closed", action="store_true", help="näytä myös suljetut kalenterit")
    v = sub.add_parser("serve", help="käynnistä selainkäyttöliittymä")
    # Hosting services (e.g. Render) pass the port in $PORT and need 0.0.0.0.
    v.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    v.add_argument("--host", default="0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
    c = sub.add_parser("check-alerts", help="tarkista tallennetut hälytykset (GitHub Actions)")
    c.add_argument("--dir", default=".", help="repon juurihakemisto")
    c.add_argument("--dry-run", action="store_true", help="älä lähetä ilmoituksia äläkä tallenna")
    args = ap.parse_args(argv)

    if args.cmd == "check-alerts":
        from .alerts import run_checker
        from .search import now_local
        run_checker(args.dir, now_local(), dry_run=args.dry_run)
        return 0

    if args.cmd == "serve":
        from .server import serve
        serve(args.host, args.port)
        return 0
    if args.cmd != "search":
        ap.print_help()
        return 1

    hosts = filter_hosts(load_hosts(), args.clubs)
    if not hosts:
        print("Ei seuroja hakuehdolla.", file=sys.stderr)
        return 1
    r = search(args.day, args.players, hosts=hosts, earliest=args.earliest,
               latest=args.latest, holes=args.holes, max_gap=args.max_gap)
    print(f"{args.day:%a %d.%m.%Y}, {args.players} pelaajaa: {len(r.matches)} osumaa, "
          f"{r.courses_checked} kenttää tarkistettu")
    for m in r.matches:
        print(fmt_match(m))
    if args.show_closed and r.closed:
        print("\nEi varattavissa:")
        for club, course, why in r.closed:
            print(f"  {club} – {course}: {why}")
    if r.errors:
        print(f"\n{len(r.errors)} virhettä:", file=sys.stderr)
        for club, msg in r.errors:
            print(f"  {club}: {msg}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
