"""The weekly search report: Google's and Bing's numbers, and the site's visits, against the CEO's targets.

    uv run python -m cb_scrapers.visibility.weekly --targets ../../docs/targets.yaml --out DIR \
        [--previous FILE]

It runs in the private repository's own workflow (Search report (weekly), Mondays at 09:00 in Salvador), which
turns DIR/report.md into docs/seo/weekly/<date>.md through a pull request that merges itself, and sends
DIR/message.txt to the owner on Telegram. Unlike the monthly monitor on the public mirror, its run and its doc
are private, so it names pages and queries. The CEO keeps the targets (docs/targets.yaml, from
docs/business-case.md); last week's report.json gives the changes.

Environment, each optional (a missing key is said in the report, never skipped silently): CB_GSC_SA_JSON,
BING_WEBMASTER_API_KEY, CB_CF_ANALYTICS_TOKEN with CB_CF_ACCOUNT_ID.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml

from . import analytics, bing, google
from .http import Api, describe
from .queries import split_queries
from .site import KEY_PATHS, url

log = logging.getLogger("cb.visibility.weekly")

SALVADOR = ZoneInfo("America/Bahia")
#: Pages and queries listed per engine.
TOP = 10
#: Telegram takes 4,096 characters; the owner-message route, 3,500 with the doc's link.
MESSAGE_MAX = 3200


@dataclass(slots=True)
class Period:
    start: str = ""
    end: str = ""
    clicks: int = 0
    impressions: int = 0
    #: Average position, weighted by impressions (None without any).
    position: float | None = None

    @property
    def ctr(self) -> float | None:
        return self.clicks / self.impressions if self.impressions else None


@dataclass(slots=True)
class Row:
    key: str
    clicks: int
    impressions: int
    position: float | None = None

    @property
    def ctr(self) -> float | None:
        return self.clicks / self.impressions if self.impressions else None


@dataclass(slots=True)
class Engine:
    status: str = "ok"
    note: str = ""
    week: Period = field(default_factory=Period)
    month: Period = field(default_factory=Period)
    #: Google: the same 28 days in Brazil only (the targets with `country: BRA`).
    month_brazil: Period = field(default_factory=Period)
    pages: list[Row] = field(default_factory=list)
    queries: list[Row] = field(default_factory=list)
    #: Google: non-brand queries at average position 10 or better over 28 days.
    nonbrand_top10: int | None = None
    #: Google: pages inspected and found indexed (URL Inspection), by path.
    inspected: dict[str, bool] = field(default_factory=dict)
    #: Bing: pages in its index, on the newest day it reports.
    in_index: int | None = None
    #: Bing: pages it crawled over the last 7 days.
    crawled_week: int | None = None
    sitemap_urls: int | None = None
    #: Google: the CEO's test pages, path → their last 28 days.
    baselines: dict[str, Period] = field(default_factory=dict)


@dataclass(slots=True)
class Visits:
    status: str = "ok"
    note: str = ""
    week: int | None = None
    month: int | None = None


@dataclass(slots=True)
class Check:
    """One of the CEO's numbers against the week's (met, behind, missed, due, passes, failed, no data)."""

    kind: str
    name: str
    target: str
    now: str
    verdict: str


@dataclass(slots=True)
class Weekly:
    generated_at: str
    day: str
    google: Engine = field(default_factory=Engine)
    bing: Engine = field(default_factory=Engine)
    visits: Visits = field(default_factory=Visits)
    checks: list[Check] = field(default_factory=list)
    targets_note: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# Collecting ------------------------------------------------------------------------------------------------


def path_of(address: str) -> str:
    parts = urlsplit(address)
    return parts.path or "/"


def _period(rows: list[dict[str, Any]], start: date, end: date) -> Period:
    row = rows[0] if rows else {}
    position = row.get("position")
    return Period(
        start=start.isoformat(),
        end=end.isoformat(),
        clicks=round(float(row.get("clicks", 0))),
        impressions=round(float(row.get("impressions", 0))),
        position=round(float(position), 1) if position is not None and row.get("impressions") else None,
    )


def _rows(rows: list[dict[str, Any]], key: Callable[[str], str] = lambda value: value) -> list[Row]:
    return [
        Row(
            key=key(str((row.get("keys") or [""])[0])),
            clicks=round(float(row.get("clicks", 0))),
            impressions=round(float(row.get("impressions", 0))),
            position=round(float(row["position"]), 1) if row.get("position") is not None else None,
        )
        for row in rows
    ]


def top(rows: Iterable[Row], count: int = TOP) -> list[Row]:
    return sorted(rows, key=lambda row: (-row.clicks, -row.impressions, row.key))[:count]


def collect_google(
    api: Api,
    raw_key: str | None,
    now: datetime,
    *,
    prop: str | None = None,
    inspect: Iterable[str] = KEY_PATHS,
    baselines: Iterable[str] = (),
) -> Engine:
    """Search Analytics over the last 7 and 28 days of final data (every country, and Brazil's 28 days), the
    week's top pages and queries, URL Inspection on the given pages and the sitemap's count; a missing or
    refused key is a status."""
    result = Engine()
    if not raw_key:
        result.status, result.note = "skipped", "no key: set the secret CB_GSC_SA_JSON (issue #805)"
        return result
    try:
        account = google.parse_account(raw_key)
        client = google.Client(
            api, google.access_token(api, account, int(now.timestamp())), prop or google.DEFAULT_PROPERTY
        )
    except google.BadKey as error:
        result.status, result.note = "error", str(error)
        return result
    except google.FAILURES as error:
        result.status, result.note = "error", f"no access token: {describe(error)}"
        return result

    week_start, week_end = google.window(now, days=7)
    month_start, month_end = google.window(now, days=28)
    week = {"startDate": week_start.isoformat(), "endDate": week_end.isoformat(), "dataState": "final"}
    month = {"startDate": month_start.isoformat(), "endDate": month_end.isoformat(), "dataState": "final"}
    every = {"type": "web", "rowLimit": 25000}
    problems: list[str] = []

    def totals() -> None:
        result.week = _period(client.search({**week, "type": "web"}), week_start, week_end)
        result.month = _period(client.search({**month, "type": "web"}), month_start, month_end)
        result.month_brazil = _period(
            client.search(google.in_brazil({**month, "type": "web"})), month_start, month_end
        )

    def pages() -> None:
        result.pages = top(_rows(client.search({**week, **every, "dimensions": ["page"]}), path_of))
        wanted = set(baselines)
        if wanted:
            for row in _rows(client.search({**month, **every, "dimensions": ["page"]}), path_of):
                if row.key in wanted:
                    result.baselines[row.key] = Period(
                        start=month["startDate"],
                        end=month["endDate"],
                        clicks=row.clicks,
                        impressions=row.impressions,
                        position=row.position,
                    )

    def queries() -> None:
        result.queries = top(_rows(client.search({**week, **every, "dimensions": ["query"]})))
        # In Brazil, as the monthly monitor and the 10 Dec gate count them.
        rows = client.search(google.in_brazil({**month, **every, "dimensions": ["query"]}))
        result.nonbrand_top10 = split_queries(
            (
                str((row.get("keys") or [""])[0]),
                row.get("clicks", 0),
                row.get("impressions", 0),
                row.get("position"),
            )
            for row in rows
        ).nonbrand_top10

    def inspections() -> None:
        for path in dict.fromkeys(inspect):
            result.inspected[path] = client.inspect(url(path)).indexed

    def sitemaps() -> None:
        for entry in client.sitemaps():
            if str(entry.get("path", "")).endswith("/sitemap.xml"):
                web = next((part for part in entry.get("contents", []) if part.get("type") == "web"), {})
                result.sitemap_urls = google._number(web.get("submitted"))

    for name, step in (
        ("Search Analytics", totals),
        ("pages", pages),
        ("queries", queries),
        ("URL Inspection", inspections),
        ("Sitemaps", sitemaps),
    ):
        try:
            step()
        except google.FAILURES as error:
            problems.append(f"{name}: {describe(error)}")
    if problems:
        result.status, result.note = "partial", "; ".join(problems)
    return result


def _bing_sum(rows: list[dict[str, Any]], since: datetime, field_name: str) -> int:
    return sum(
        int(row.get(field_name) or 0)
        for row in rows
        if (moment := bing.parse_date(row.get("Date"))) and moment >= since
    )


def _bing_rows(rows: list[dict[str, Any]], since: datetime, key: Callable[[str], str]) -> list[Row]:
    """GetQueryStats and GetPageStats: weekly rows per query or page, added up, the position weighted by
    impressions."""
    sums: dict[str, list[float]] = {}
    for row in rows:
        moment = bing.parse_date(row.get("Date"))
        if not moment or moment < since:
            continue
        clicks, impressions = float(row.get("Clicks") or 0), float(row.get("Impressions") or 0)
        position = float(row.get("AvgImpressionPosition") or 0)
        entry = sums.setdefault(key(str(row.get("Query") or "")), [0.0, 0.0, 0.0, 0.0])
        entry[0] += clicks
        entry[1] += impressions
        if position > 0:
            entry[2] += position * impressions
            entry[3] += impressions
    return [
        Row(name, round(clicks), round(impressions), round(weighted / weight, 1) if weight else None)
        for name, (clicks, impressions, weighted, weight) in sums.items()
    ]


def _position(rows: Iterable[Row]) -> float | None:
    weighted = [(row.position, row.impressions) for row in rows if row.position and row.impressions]
    weight = sum(impressions for _, impressions in weighted)
    return (
        round(sum(position * impressions for position, impressions in weighted) / weight, 1)
        if weight
        else None
    )


def collect_bing(api: Api, key: str | None, now: datetime, *, site: str | None = None) -> Engine:
    """Clicks and impressions over the last 7 and 28 days, the week's top pages and queries, the index and the
    week's crawl, and the sitemap's count; a missing or refused key is a status."""
    result = Engine()
    if not key:
        result.status, result.note = "skipped", "no key: set the secret BING_WEBMASTER_API_KEY (issue #805)"
        return result
    client = bing.Client(api, key, site or bing.DEFAULT_SITE)
    week_since, month_since = now - timedelta(days=7), now - timedelta(days=28)
    result.week = Period(start=week_since.date().isoformat(), end=now.date().isoformat())
    result.month = Period(start=month_since.date().isoformat(), end=now.date().isoformat())
    problems: list[str] = []

    def feeds() -> None:
        for feed in bing._rows(client.get("GetFeeds")):
            if str(feed.get("Url", "")).endswith("/sitemap.xml"):
                result.sitemap_urls = int(feed.get("UrlCount") or 0)

    def traffic() -> None:
        rows = bing._rows(client.get("GetRankAndTrafficStats"))
        for period, since in ((result.week, week_since), (result.month, month_since)):
            period.clicks = _bing_sum(rows, since, "Clicks")
            period.impressions = _bing_sum(rows, since, "Impressions")

    def queries() -> None:
        rows = bing._rows(client.get("GetQueryStats"))
        week = _bing_rows(rows, week_since, lambda query: query)
        result.queries = top(week)
        result.week.position = _position(week)
        result.month.position = _position(_bing_rows(rows, month_since, lambda query: query))

    def pages() -> None:
        result.pages = top(_bing_rows(bing._rows(client.get("GetPageStats")), week_since, path_of))

    def crawl() -> None:
        rows = bing._rows(client.get("GetCrawlStats"))
        dated = [(moment, row) for row in rows if (moment := bing.parse_date(row.get("Date")))]
        if dated:
            result.in_index = int(max(dated, key=lambda pair: pair[0])[1].get("InIndex") or 0)
        result.crawled_week = _bing_sum(rows, week_since, "CrawledPages")

    try:
        # The first call also checks the key: a wrong or revoked one fails every call the same way.
        feeds()
    except bing.FAILURES as error:
        result.status, result.note = "error", f"GetFeeds: {describe(error)}"
        return result
    for name, step in (
        ("GetRankAndTrafficStats", traffic),
        ("GetQueryStats", queries),
        ("GetPageStats", pages),
        ("GetCrawlStats", crawl),
    ):
        try:
            step()
        except bing.FAILURES as error:
            problems.append(f"{name}: {describe(error)}")
    if problems:
        result.status, result.note = "partial", "; ".join(problems)
    return result


def collect_visits(api: Api, token: str | None, account: str | None, now: datetime) -> Visits:
    week = analytics.collect(api, token, account, now, days=7)
    if week.status != "ok":
        return Visits(status=week.status, note=week.note)
    month = analytics.collect(api, token, account, now, days=28)
    if month.status != "ok":
        return Visits(status=month.status, note=month.note, week=week.visits)
    return Visits(week=week.visits, month=month.visits)


# The CEO's targets -----------------------------------------------------------------------------------------


def load_targets(path: Path | None) -> tuple[dict[str, Any], str]:
    """The targets file and a note on it ("" when it was read)."""
    if path is None:
        return {}, "no targets file given: the report has no comparison with the CEO's numbers"
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        return {}, f"the targets file couldn't be read ({type(error).__name__}): no comparison this week"
    if not isinstance(data, dict):
        return {}, "the targets file isn't a mapping: no comparison this week"
    return data, ""


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.{digits}f}"


def percent(share: float | None, digits: int = 1) -> str:
    return "n/a" if share is None else f"{share * 100:.{digits}f} %"


def actuals(report: Weekly) -> dict[str, float | None]:
    """The week's value of each metric the targets file can name (None when not measured)."""
    g = report.google if report.google.status != "skipped" and report.google.status != "error" else None
    b = report.bing if report.bing.status != "skipped" and report.bing.status != "error" else None
    v = report.visits if report.visits.status == "ok" else None
    indexed = sum(report.google.inspected.values()) if g and report.google.inspected else None
    return {
        "visits_7d": v.week if v else None,
        "visits_28d": v.month if v else None,
        "google_clicks_7d": g.week.clicks if g else None,
        "google_clicks_28d": g.month.clicks if g else None,
        "google_impressions_7d": g.week.impressions if g else None,
        "google_impressions_28d": g.month.impressions if g else None,
        "bing_clicks_7d": b.week.clicks if b else None,
        "bing_clicks_28d": b.month.clicks if b else None,
        "bing_impressions_7d": b.week.impressions if b else None,
        "bing_impressions_28d": b.month.impressions if b else None,
        "nonbrand_queries_top10": g.nonbrand_top10 if g else None,
        "google_key_pages_indexed": indexed,
        "bing_pages_indexed": b.in_index if b else None,
    }


def _goal(kind: str, entry: Mapping[str, Any], value: float | None, today: date) -> Check:
    """A target to reach by a date (traffic, search): met, behind (how far, how long is left) or missed. A row
    whose note calls it a line (the targets file's convention) reads over or under the line instead."""
    target = float(entry.get("value", entry.get("visits_28d", 0)) or 0)
    by = _date(entry.get("by"))
    name = str(entry.get("name") or entry.get("metric") or "target")
    line = "a line" in str(entry.get("note", "")).lower()
    wanted = f"{number(target)}" + (f" by {by:%d %b %Y}" if by else "")
    if value is None:
        return Check(kind, name, wanted, "n/a", "no data")
    if value >= target:
        return Check(kind, name, wanted, number(value), "over the line" if line else "met")
    share = value / target if target else None
    if by and today > by:
        missed = "under the line" if line else "missed"
        return Check(kind, name, wanted, number(value), f"{missed} ({percent(share, 0)} of it)")
    left = f", {(by - today).days} days left" if by else ""
    behind = "under the line" if line else "behind"
    return Check(kind, name, wanted, number(value), f"{behind}: {percent(share, 0)} of it{left}")


def _gate(entry: Mapping[str, Any], value: float | None, today: date, of: int | None) -> Check:
    """A dated gate: passes, due (how short it is) or failed."""
    target = float(entry.get("value") or 0)
    when = _date(entry.get("date"))
    name = str(entry.get("name") or entry.get("metric") or "gate")
    total = entry.get("of", of)
    wanted = f"{number(target)}" + (f" of {total}" if total else "") + (f" on {when:%d %b}" if when else "")
    if value is None:
        return Check("gate", name, wanted, "n/a", "no data")
    now = f"{number(value)}" + (f" of {of}" if of else "")
    if value >= target:
        return Check("gate", name, wanted, now, "passes")
    if when and today > when:
        return Check("gate", name, wanted, now, f"failed on {when:%d %b}")
    due = f"due in {(when - today).days} days, " if when else ""
    return Check("gate", name, wanted, now, f"{due}{number(target - value)} short")


def gate_pages(targets: Mapping[str, Any]) -> list[str]:
    """Pages a gate names (e.g. the ones the owner asked Google to index), inspected besides the key pages."""
    pages: list[str] = []
    for entry in targets.get("gates") or []:
        if isinstance(entry, dict):
            pages += [str(page) for page in entry.get("pages") or []]
    return pages


def baseline_pages(targets: Mapping[str, Any]) -> list[str]:
    baselines = targets.get("ctr_baselines") or {}
    pages = baselines.get("pages") if isinstance(baselines, dict) else None
    return [str(page) for page in pages] if isinstance(pages, dict) else []


def compare(report: Weekly, targets: Mapping[str, Any], today: date) -> list[Check]:
    values = actuals(report)
    checks: list[Check] = []
    for entry in targets.get("traffic") or []:
        if isinstance(entry, dict):
            checks.append(_goal("traffic", entry, values["visits_28d"], today))
    brazil = report.google.month_brazil if report.google.status in ("ok", "partial") else None
    for entry in targets.get("search") or []:
        if not isinstance(entry, dict):
            continue
        metric = str(entry.get("metric"))
        value = values.get(metric)
        if str(entry.get("country", "")).upper() in ("BRA", "BR") and brazil is not None:
            # Brazil's 28 days (the non-brand queries are counted in Brazil already).
            value = {"google_impressions_28d": brazil.impressions, "google_clicks_28d": brazil.clicks}.get(
                metric, value
            )
        checks.append(_goal("search", entry, value, today))
    for entry in targets.get("gates") or []:
        if not isinstance(entry, dict):
            continue
        metric, of = str(entry.get("metric")), None
        value = values.get(metric)
        if metric.startswith("google_indexed"):
            # The gate's own pages only: never another list in their place.
            pages = [str(page) for page in entry.get("pages") or []]
            seen = [report.google.inspected[page] for page in pages if page in report.google.inspected]
            value, of = (float(sum(seen)), len(seen)) if seen else (None, None)
        elif metric not in values:
            # e.g. crawlers_seen_7d: the monthly monitor's probe and AI Crawl Control read it, not this.
            unread = _gate(entry, None, today, None)
            checks.append(Check("gate", unread.name, unread.target, "n/a", "not read weekly"))
            continue
        checks.append(_gate(entry, value, today, of))
    baselines = targets.get("ctr_baselines") or {}
    if isinstance(baselines, dict) and isinstance(baselines.get("pages"), dict):
        reread = _date(baselines.get("reread"))
        for page, before in baselines["pages"].items():
            period = report.google.baselines.get(str(page))
            now = (
                f"{percent(period.ctr)} ({number(period.impressions)} impressions, 28 days)"
                if period and period.impressions
                else "no impressions in 28 days"
            )
            verdict = "no data"
            if period and period.ctr is not None and isinstance(before, (int, float)):
                verdict = "up" if period.ctr > before else "down" if period.ctr < before else "same"
                if reread:
                    verdict += f" (re-read {reread:%d %b})"
            checks.append(Check("ctr", str(page), f"{percent(float(before))} on 23 Oct", now, verdict))
    return checks


# Writing ---------------------------------------------------------------------------------------------------


def _before(previous: Mapping[str, Any] | None, *keys: str) -> Any:
    value: Any = previous
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def change(now: float | None, before: Any) -> str:
    if now is None or not isinstance(before, (int, float)) or isinstance(before, bool):
        return ""
    if before == 0:
        return "" if now == 0 else " (new)"
    return f" ({(now - before) / before:+.0%})".replace("%", " %")


def key_pages(engine: Engine) -> tuple[int, int]:
    """How many of the key pages (site.KEY_PATHS) Google has indexed, of those inspected."""
    seen = [engine.inspected[page] for page in KEY_PATHS if page in engine.inspected]
    return sum(seen), len(seen)


def _engine_line(label: str, engine: Engine, previous: Mapping[str, Any] | None, key: str) -> str:
    if engine.status in ("skipped", "error"):
        return f"{label}: {engine.note}"
    week = engine.week
    line = (
        f"{label}, 7 days: {number(week.impressions)} impressions"
        f"{change(week.impressions, _before(previous, key, 'week', 'impressions'))}, "
        f"{number(week.clicks)} clicks{change(week.clicks, _before(previous, key, 'week', 'clicks'))}, "
        f"CTR {percent(week.ctr)}, position {number(week.position, 1)}"
    )
    if key == "google" and engine.inspected:
        indexed, inspected = key_pages(engine)
        line += f" · key pages indexed: {indexed} of {inspected}"
    if key == "bing" and engine.in_index is not None:
        line += f" · {number(engine.in_index)} pages in its index"
    return line


def message(report: Weekly, previous: Mapping[str, Any] | None) -> str:
    """The owner's Telegram message (owner tools are in English); the workflow adds the doc's link."""
    lines = [
        f"Weekly search report · {report.day}",
        _engine_line("Google", report.google, previous, "google"),
        _engine_line("Bing", report.bing, previous, "bing"),
    ]
    visits = report.visits
    if visits.status == "ok":
        lines.append(
            f"Visits: {number(visits.week)} in 7 days"
            f"{change(visits.week, _before(previous, 'visits', 'week'))}, {number(visits.month)} in 28 days"
        )
    else:
        lines.append(f"Visits: {visits.note}")
    checks = [check for check in report.checks if check.kind != "ctr"]
    if checks:
        lines.append("Against the CEO's numbers:")
        lines += [f"• {check.name}: {check.now} for {check.target} → {check.verdict}" for check in checks]
    elif report.targets_note:
        lines.append(report.targets_note)
    text = "\n".join(lines)
    return text if len(text) <= MESSAGE_MAX else text[: MESSAGE_MAX - 1] + "…"


def _table(rows: list[Row], first: str) -> list[str]:
    if not rows:
        return ["_None this week._"]
    out = [f"| {first} | Clicks | Impressions | CTR | Position |", "|---|---:|---:|---:|---:|"]
    for row in rows:
        out.append(
            f"| {row.key.replace('|', '/')} | {number(row.clicks)} | {number(row.impressions)} | "
            f"{percent(row.ctr)} | {number(row.position, 1)} |"
        )
    return out


def _periods(engine: Engine, previous: Mapping[str, Any] | None, key: str) -> list[str]:
    last = _before(previous, key, "week") or {}
    last_ctr = (
        last.get("clicks", 0) / last["impressions"]
        if isinstance(last, Mapping) and last.get("impressions")
        else None
    )
    rows = [
        (
            "Impressions",
            number(engine.week.impressions),
            number(engine.month.impressions),
            number(last.get("impressions") if isinstance(last, Mapping) else None),
        ),
        (
            "Clicks",
            number(engine.week.clicks),
            number(engine.month.clicks),
            number(last.get("clicks") if isinstance(last, Mapping) else None),
        ),
        ("CTR", percent(engine.week.ctr), percent(engine.month.ctr), percent(last_ctr)),
        (
            "Average position",
            number(engine.week.position, 1),
            number(engine.month.position, 1),
            number(last.get("position") if isinstance(last, Mapping) else None, 1),
        ),
    ]
    out = [
        f"| | Last 7 days ({engine.week.start} to {engine.week.end}) | Last 28 days | The 7 days before |",
        "|---|---:|---:|---:|",
    ]
    out += [f"| {name} | {week} | {month} | {before} |" for name, week, month, before in rows]
    return out


def markdown(report: Weekly, previous: Mapping[str, Any] | None, summary: str) -> str:
    g, b, v = report.google, report.bing, report.visits
    out = [
        f"# Weekly search report, {report.day}",
        "",
        "*Written by the Search report (weekly) workflow from Search Console, Bing Webmaster Tools and "
        "Cloudflare Web Analytics, against the CEO's numbers in `docs/targets.yaml`. Search Console's "
        "final data trails by about three days.*",
        "",
        "## This week",
        "",
        "```",
        summary,
        "```",
        "",
    ]
    checks = [check for check in report.checks if check.kind != "ctr"]
    out += ["## Against the CEO's numbers", ""]
    if checks:
        out += ["| Target | The CEO's number | Now | Verdict |", "|---|---|---|---|"]
        out += [f"| {check.name} | {check.target} | {check.now} | {check.verdict} |" for check in checks]
    else:
        out.append(f"_{report.targets_note or 'The targets file names no target yet.'}_")
    ctr = [check for check in report.checks if check.kind == "ctr"]
    if ctr:
        out += ["", "**The snippet test (PR #790):** each page's click rate on Google before and now.", ""]
        out += ["| Page | 23 Oct | Now | |", "|---|---|---|---|"]
        out += [f"| {check.name} | {check.target} | {check.now} | {check.verdict} |" for check in ctr]
    out += ["", "## Google", ""]
    if g.status in ("skipped", "error"):
        out.append(f"_{g.note}_")
    else:
        out += _periods(g, previous, "google")
        if g.inspected:
            indexed, inspected = key_pages(g)
            missing = [page for page in KEY_PATHS if g.inspected.get(page) is False]
            out += ["", f"Key pages indexed (URL Inspection): **{indexed} of {inspected}**."]
            if missing:
                out.append("Not indexed yet: " + ", ".join(f"`{page}`" for page in missing) + ".")
        if g.sitemap_urls is not None:
            out.append(f"The sitemap lists {number(g.sitemap_urls)} pages in Search Console.")
        if g.nonbrand_top10 is not None:
            out.append(f"Non-brand queries at position 10 or better (28 days): {number(g.nonbrand_top10)}.")
        out += ["", "**Top pages, 7 days**", "", *_table(g.pages, "Page")]
        out += ["", "**Top queries, 7 days**", "", *_table(g.queries, "Query")]
        if g.status == "partial":
            out += ["", f"_Partly read: {g.note}_"]
    out += ["", "## Bing", ""]
    if b.status in ("skipped", "error"):
        out.append(f"_{b.note}_")
    else:
        out += _periods(b, previous, "bing")
        facts = []
        if b.in_index is not None:
            facts.append(f"{number(b.in_index)} pages in Bing's index")
        if b.crawled_week is not None:
            facts.append(f"{number(b.crawled_week)} pages crawled in 7 days")
        if b.sitemap_urls is not None:
            facts.append(f"the sitemap lists {number(b.sitemap_urls)} pages")
        if facts:
            out += ["", "; ".join(facts).capitalize() + "."]
        out += ["", "**Top pages, 7 days**", "", *_table(b.pages, "Page")]
        out += ["", "**Top queries, 7 days**", "", *_table(b.queries, "Query")]
        if b.status == "partial":
            out += ["", f"_Partly read: {b.note}_"]
    out += ["", "## Visits", ""]
    if v.status == "ok":
        out.append(
            "Cloudflare Web Analytics (bots left out; the owner's own visits never count): "
            f"{number(v.week)} visits in "
            f"7 days{change(v.week, _before(previous, 'visits', 'week'))}, {number(v.month)} in 28 days."
        )
    else:
        out.append(f"_{v.note}_")
    out.append("")
    return "\n".join(out)


# The run ---------------------------------------------------------------------------------------------------


def run(
    api: Api, env: Mapping[str, str], now: datetime, targets: Mapping[str, Any], targets_note: str
) -> Weekly:
    report = Weekly(
        generated_at=now.isoformat(timespec="seconds"), day=now.astimezone(SALVADOR).date().isoformat()
    )
    report.google = collect_google(
        api,
        env.get("CB_GSC_SA_JSON"),
        now,
        prop=env.get("CB_GSC_PROPERTY"),
        inspect=[*KEY_PATHS, *gate_pages(targets)],
        baselines=baseline_pages(targets),
    )
    report.bing = collect_bing(api, env.get("BING_WEBMASTER_API_KEY"), now, site=env.get("CB_BING_SITE"))
    report.visits = collect_visits(api, env.get("CB_CF_ANALYTICS_TOKEN"), env.get("CB_CF_ACCOUNT_ID"), now)
    report.targets_note = targets_note
    report.checks = compare(report, targets, now.astimezone(SALVADOR).date())
    return report


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    api: Api | None = None,
    now: datetime | None = None,
) -> int:
    """The run; tests pass their own env, client (on recorded answers) and time."""
    parser = argparse.ArgumentParser(prog="python -m cb_scrapers.visibility.weekly", description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="folder for report.md, report.json, message.txt"
    )
    parser.add_argument("--targets", type=Path, help="the CEO's targets (docs/targets.yaml)")
    parser.add_argument("--previous", type=Path, help="last week's report.json, for the changes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    # httpx logs every URL at INFO, and Bing's key travels in the query string.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    env = env if env is not None else dict(os.environ)
    now = now or datetime.now(UTC)
    targets, note = load_targets(args.targets)
    previous: dict[str, Any] | None = None
    if args.previous and args.previous.is_file():
        try:
            loaded = json.loads(args.previous.read_text())
            previous = loaded if isinstance(loaded, dict) else None
        except (OSError, json.JSONDecodeError):
            log.warning("last week's report couldn't be read; no changes this time")

    with ExitStack() as stack:
        api = api or stack.enter_context(Api())
        report = run(api, env, now, targets, note)
    summary = message(report, previous)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "message.txt").write_text(summary + "\n")
    (args.out / "report.md").write_text(markdown(report, previous, summary))
    (args.out / "report.json").write_text(json.dumps(report.to_json(), ensure_ascii=False, indent=2) + "\n")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
