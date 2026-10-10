"""`cb`: run the scrapers.

    uv run cb list                         adapters and cadences
    uv run cb run --due                    every adapter whose cadence says it's time
    uv run cb run cardapioweb --dry-run    print items instead of sending them
    uv run cb run precodahora --limit 2    a trial run: stop after two tasks
    uv run cb bairros build                refetch the official bairro boundaries into data/seed/bairros
    uv run cb menus find --out DIR         menu images and PDFs from venues' own websites (#305)
    uv run cb spike vision --manifest FILE the free vision model against menus read by a person (#45)
    uv run cb venues audit                 suggest keep/hide for venues on the map (#331; admin token)
    uv run cb venues apply [--apply]       do what data/seed/venue-review.csv says (dry run unless --apply)

Sending needs CB_API_URL (site or staging origin) and CB_INGEST_TOKEN (an ingest-scoped admin token).
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .core.ingest import DryRunSink, IngestClient
from .core.registry import all_adapters
from .core.runner import Sink, due_adapters, run_adapter

log = logging.getLogger("cb")


def _sink(dry_run: bool, parser: argparse.ArgumentParser) -> Sink:
    if dry_run:
        return DryRunSink()
    url, token = os.environ.get("CB_API_URL"), os.environ.get("CB_INGEST_TOKEN")
    if not url or not token:
        parser.error("set CB_API_URL and CB_INGEST_TOKEN to send items, or use --dry-run")
    return IngestClient(url, token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cb", description="Cerveja Barata scrapers")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list adapters and their cadence")
    run = commands.add_parser("run", help="run adapters")
    run.add_argument("names", nargs="*", metavar="ADAPTER", help="adapters to run")
    run.add_argument("--due", action="store_true", help="also run every adapter whose cadence is due")
    run.add_argument(
        "--dry-run", action="store_true", help="print items as JSON lines instead of sending them"
    )
    run.add_argument("--limit", type=int, help="at most this many tasks per adapter (a trial run)")
    bairros = commands.add_parser("bairros", help="bairro polygons in data/seed/bairros")
    bairros.add_argument(
        "action", choices=["build"], help="build: refetch and simplify the official boundaries"
    )
    menus = commands.add_parser("menus", help="menu images and PDFs from venues' own websites (#305)")
    menus.add_argument("action", choices=["find"], help="find: one polite pass over the venues' websites")
    menus.add_argument("--out", type=Path, required=True, help="folder for candidates/ and the CSV reports")
    menus.add_argument("--limit", type=int, help="crawl at most this many sites (per city with --city)")
    menus.add_argument(
        "--city",
        action="append",
        default=[],
        help="bars in this city instead of the covered ones (repeatable)",
    )
    spike = commands.add_parser("spike", help="spikes: one-off measurements")
    spike.add_argument("name", choices=["vision"], help="vision: Workers AI against known menus (#45)")
    spike.add_argument("--manifest", type=Path, required=True, help="menus and their expected beer prices")
    spike.add_argument("--report", type=Path, help="also write every reply as JSON here")
    venues = commands.add_parser("venues", help="the reviewed list of doubtful venues (#331)")
    venues.add_argument(
        "action", choices=["audit", "apply"], help="audit: suggest; apply: do what the list says"
    )
    venues.add_argument("--apply", action="store_true", help="apply: change production (a dry run otherwise)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    if args.command == "bairros":
        from .core import PoliteClient
        from .geo.build import build

        with PoliteClient(delay=1.0) as client:
            build(client)
        return 0

    if args.command == "menus":
        from .core import PoliteClient
        from .menus import find_menus

        with PoliteClient(delay=3.0) as client:
            print(find_menus(client, args.out, args.limit, cities=args.city))
        return 0

    if args.command == "spike":
        import httpx

        from .ai import WorkersAI
        from .ai.spike import load_manifest, run_spike, summary, write_report
        from .core import PoliteClient

        account, token = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CF_AI_TOKEN")
        if not account or not token:
            parser.error("set CLOUDFLARE_ACCOUNT_ID and CF_AI_TOKEN (Workers AI)")
        with PoliteClient(delay=3.0) as client, httpx.Client() as api:
            results = run_spike(client, WorkersAI(account, token, api), load_manifest(args.manifest))
        if args.report:
            write_report(args.report, results)
        print(summary(results))
        return 0

    if args.command == "venues":
        url, token = os.environ.get("CB_API_URL"), os.environ.get("CB_ADMIN_TOKEN")
        if not url or not token:
            parser.error("set CB_API_URL and CB_ADMIN_TOKEN (an admin-scoped token)")
        from .review_cli import run_venues

        print(run_venues(args.action, url, token, apply_changes=args.apply))
        return 0

    adapters = all_adapters()
    if args.command == "list":
        for name, cls in adapters.items():
            print(f"{name}\t{cls.cadence.label or cls.cadence.every}\t{cls.delay:g}s between requests")
        return 0

    if not args.names and not args.due:
        parser.error("name the adapters to run or pass --due")
    unknown = [name for name in args.names if name not in adapters]
    if unknown:
        parser.error(f"unknown adapter(s): {', '.join(unknown)} (known: {', '.join(adapters) or 'none yet'})")

    sink = _sink(args.dry_run, parser)
    selected = [adapters[name] for name in args.names]
    if args.due:
        selected += [
            cls
            for cls in due_adapters(adapters, sink.last_success(), datetime.now(UTC))
            if cls not in selected
        ]
    if not selected:
        log.info("nothing is due")
        return 0

    reports = [run_adapter(cls, sink, limit=args.limit) for cls in selected]
    for report in reports:
        log.info(
            "%s: %s · %d tasks · %d items · %d sent · %d errors",
            report.adapter,
            report.status,
            report.tasks,
            report.items,
            report.sent,
            report.error_count,
        )
    # A block is an expected, reported outcome (the daily digest shows it); only crashes fail the job.
    return 1 if any(report.status == "failed" for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
