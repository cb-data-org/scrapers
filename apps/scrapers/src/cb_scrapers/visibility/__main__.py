"""`python -m cb_scrapers.visibility`: the visibility monitor's run.

    uv run python -m cb_scrapers.visibility --out DIR [--previous FILE] [--submit] [--archive]
    uv run python -m cb_scrapers.visibility --out DIR --only crawlers --fail-on-alert

The first is the monthly report (on the 1st); the second, the weekly crawler check, whose run fails when a
crawler is refused. DIR gets report.md (the run's summary), visibility.json (next month's --previous) and
digest.txt (the report's one line).

Environment, each optional (a missing one is reported, never silently skipped): CB_GSC_SA_JSON (and
CB_GSC_PROPERTY), BING_WEBMASTER_API_KEY (and CB_BING_SITE), CB_CF_ANALYTICS_TOKEN with CB_CF_ACCOUNT_ID. No
token for the site's admin API: the report stays in the run (summary and artifact).

TODO(visibility-monitor): send the line to the owner on Telegram once the site has an ingest-scoped endpoint
for it. POST /api/admin/owner/message takes only admin tokens, and an admin token doesn't belong on the
public mirror.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core import PoliteClient
from . import analytics, archive, bing, commoncrawl, crawlers, google, listings
from .http import Api
from .report import Report, digest, markdown
from .site import fetch_sitemap

log = logging.getLogger("cb.visibility")

SECTIONS = ("google", "bing", "crawlers", "common_crawl", "listings", "analytics")


def previous_hubs(previous: Mapping[str, Any] | None) -> list[str] | None:
    """Last month's hub pages, when last month's sitemap was read."""
    if not previous or previous.get("sitemap_status") != "ok" or not isinstance(previous.get("hubs"), list):
        return None
    return [str(hub) for hub in previous["hubs"]]


def run(
    api: Api,
    polite: PoliteClient,
    env: Mapping[str, str],
    now: datetime,
    *,
    only: tuple[str, ...] = SECTIONS,
    previous: Mapping[str, Any] | None = None,
    submit: bool = False,
    capture: bool = False,
) -> Report:
    sitemap = fetch_sitemap(api)
    report = Report(generated_at=now.isoformat(timespec="seconds"))
    if sitemap.status == "ok":
        report.sitemap_urls, report.hubs = len(sitemap.urls), sitemap.hubs()
    else:
        report.sitemap_status = sitemap.note or sitemap.status
    if "crawlers" in only:
        report.crawlers = crawlers.collect(api, sitemap)
    if "google" in only:
        report.google = google.collect(
            api, env.get("CB_GSC_SA_JSON"), sitemap, now, env.get("CB_GSC_PROPERTY")
        )
    if "bing" in only:
        report.bing = bing.collect(
            api,
            env.get("BING_WEBMASTER_API_KEY"),
            sitemap,
            now,
            site=env.get("CB_BING_SITE"),
            submit_urls=submit,
            previous_hubs=previous_hubs(previous),
        )
    if "common_crawl" in only:
        report.common_crawl = commoncrawl.collect(api)
    if "listings" in only:
        report.listings = listings.collect(api, polite)
    if "analytics" in only:
        report.analytics = analytics.collect(
            api, env.get("CB_CF_ANALYTICS_TOKEN"), env.get("CB_CF_ACCOUNT_ID"), now
        )
    if capture:
        report.archive = archive.collect(api, sitemap)
    return report


def load_previous(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        log.warning("last month's report couldn't be read; no changes this time")
        return None
    return data if isinstance(data, dict) else None


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    api: Api | None = None,
    polite: PoliteClient | None = None,
    now: datetime | None = None,
) -> int:
    """The run; tests pass their own env, clients (on recorded answers) and time."""
    parser = argparse.ArgumentParser(
        prog="python -m cb_scrapers.visibility", description="The visibility monitor"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="folder for report.md, visibility.json, digest.txt"
    )
    parser.add_argument("--previous", type=Path, help="last month's visibility.json, for the changes")
    parser.add_argument("--only", help=f"comma-separated sections (default all: {', '.join(SECTIONS)})")
    parser.add_argument("--submit", action="store_true", help="submit new hub pages to Bing (SubmitUrlBatch)")
    parser.add_argument("--archive", action="store_true", help="ask the Wayback Machine to capture key pages")
    parser.add_argument("--fail-on-alert", action="store_true", help="exit 1 when a crawler is refused")
    args = parser.parse_args(argv)
    only = tuple(part.strip() for part in args.only.split(",")) if args.only else SECTIONS
    unknown = [part for part in only if part not in SECTIONS]
    if unknown:
        parser.error(f"unknown section(s): {', '.join(unknown)}")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    # httpx logs every URL at INFO, and Bing's key travels in the query string.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    env = env if env is not None else dict(os.environ)
    now = now or datetime.now(UTC)
    previous = load_previous(args.previous)

    with ExitStack() as stack:
        api = api or stack.enter_context(Api())
        polite = polite or stack.enter_context(PoliteClient(delay=3.0))
        report = run(
            api, polite, env, now, only=only, previous=previous, submit=args.submit, capture=args.archive
        )
    line = digest(report, previous, now)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "digest.txt").write_text(line + "\n")
    (args.out / "report.md").write_text(markdown(report, line, now))
    (args.out / "visibility.json").write_text(
        json.dumps({**report.to_json(), "digest": line}, ensure_ascii=False, indent=2) + "\n"
    )
    for alert in report.alerts:
        print(f"::warning::{alert}")
    print(line)
    return 1 if args.fail_on_alert and report.alerts else 0


if __name__ == "__main__":
    sys.exit(main())
