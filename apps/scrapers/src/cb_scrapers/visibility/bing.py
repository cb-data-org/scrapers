"""Bing Webmaster Tools, read with the site's API key (the BING_WEBMASTER_API_KEY secret; RUNBOOK §15d).
Bing's index also feeds ChatGPT search, Copilot, DuckDuckGo and Meta AI.

- GetFeeds: the sitemap's status and page count. GetCrawlStats: pages in the index, crawled, crawl errors and
  pages blocked by robots.txt, from the newest day. GetRankAndTrafficStats and GetQueryStats: clicks and
  impressions over the last 28 days, the queries split brand against the rest (counts only).
- GetUrlInfo on the key pages: whether Bing has crawled each one, and when.
- With submit: SubmitUrlBatch for the key pages Bing hasn't crawled and the hub pages that are new since the
  last report, within the day's quota (GetUrlSubmissionQuota) and at most MAX_SUBMIT a month. Every deploy
  pings IndexNow already; this catches what that missed.

The API has no AI Performance (Copilot citations) and no IndexNow report (checked Oct 2026): the report says
to look at them in the dashboard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .http import Api, ApiError, check, describe
from .queries import QuerySplit, Totals, split_queries
from .site import KEY_PATHS, Sitemap, url

API = "https://ssl.bing.com/webmaster/api.svc/json"
#: The site as Bing Webmaster Tools lists it (GetUserSites); CB_BING_SITE overrides it.
DEFAULT_SITE = "https://cervejabarata.com.br/"
DAYS = 28
MAX_SUBMIT = 50
DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")
FAILURES = (ApiError, httpx.HTTPError, ValueError, KeyError, TypeError)


@dataclass(slots=True)
class PageInfo:
    url: str
    #: When Bing last crawled it (ISO date), or None when Bing doesn't know the page yet.
    crawled: str | None = None
    http_status: int | None = None


@dataclass(slots=True)
class Bing:
    status: str = "ok"
    note: str = ""
    site: str = DEFAULT_SITE
    sitemap_status: str | None = None
    sitemap_urls: int | None = None
    #: From the newest day of GetCrawlStats (None while Bing reports none).
    crawl_date: str | None = None
    in_index: int | None = None
    crawled_pages: int | None = None
    crawl_errors: int | None = None
    blocked_by_robots: int | None = None
    traffic: Totals = field(default_factory=Totals)
    queries: QuerySplit = field(default_factory=QuerySplit)
    quota_daily: int | None = None
    quota_monthly: int | None = None
    pages: list[PageInfo] = field(default_factory=list)
    known: int = 0
    submitted: list[str] = field(default_factory=list)
    submit_note: str = ""


def parse_date(value: Any) -> datetime | None:
    """Bing's "/Date(1316156400000-0700)/": the milliseconds are UTC; the offset is where it was written."""
    match = DATE.fullmatch(str(value or ""))
    if not match:
        return None
    try:
        moment = datetime.fromtimestamp(int(match.group(1)) / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    # Unknown dates come back as the epoch or as .NET's DateTime.MinValue.
    return moment if moment.year >= 2000 else None


class Client:
    def __init__(self, api: Api, key: str, site: str) -> None:
        self.api = api
        self.key = key
        self.site = site

    def get(self, method: str, **params: str) -> Any:
        query = {"siteUrl": self.site, **params, "apikey": self.key}
        return check(self.api.get(f"{API}/{method}", params=query)).json().get("d")

    def post(self, method: str, body: dict[str, Any]) -> Any:
        response = check(self.api.post(f"{API}/{method}", params={"apikey": self.key}, json=body))
        return response.json().get("d") if response.content else None


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _recent(rows: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    return [row for row in rows if (moment := parse_date(row.get("Date"))) and moment >= since]


def _feeds(client: Client, result: Bing) -> None:
    feeds = _rows(client.get("GetFeeds"))
    if feeds:
        result.sitemap_urls = sum(int(feed.get("UrlCount") or 0) for feed in feeds)
        result.sitemap_status = ", ".join(sorted({str(feed.get("Status") or "?") for feed in feeds}))


def _crawl(client: Client, result: Bing) -> None:
    days = [(parse_date(row.get("Date")), row) for row in _rows(client.get("GetCrawlStats"))]
    dated = [(moment, row) for moment, row in days if moment]
    if not dated:
        return
    moment, newest = max(dated, key=lambda pair: pair[0])
    result.crawl_date = moment.date().isoformat()
    result.in_index = int(newest.get("InIndex") or 0)
    result.crawled_pages = int(newest.get("CrawledPages") or 0)
    result.crawl_errors = int(newest.get("CrawlErrors") or 0)
    result.blocked_by_robots = int(newest.get("BlockedByRobotsTxt") or 0)


def _traffic(client: Client, result: Bing, since: datetime) -> None:
    for row in _recent(_rows(client.get("GetRankAndTrafficStats")), since):
        result.traffic.add(row.get("Clicks") or 0, row.get("Impressions") or 0)


def _queries(client: Client, result: Bing, since: datetime) -> None:
    """GetQueryStats is weekly rows per query: added up per query, the position weighted by impressions."""
    per_query: dict[str, list[float]] = {}
    for row in _recent(_rows(client.get("GetQueryStats")), since):
        clicks, impressions = float(row.get("Clicks") or 0), float(row.get("Impressions") or 0)
        position = float(row.get("AvgImpressionPosition") or 0)
        sums = per_query.setdefault(str(row.get("Query") or ""), [0.0, 0.0, 0.0, 0.0])
        sums[0] += clicks
        sums[1] += impressions
        if position > 0:
            sums[2] += position * impressions
            sums[3] += impressions
    result.queries = split_queries(
        (query, clicks, impressions, weighted / weight if weight else None)
        for query, (clicks, impressions, weighted, weight) in per_query.items()
    )


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _quota(client: Client, result: Bing) -> None:
    quota = client.get("GetUrlSubmissionQuota") or {}
    if isinstance(quota, dict):
        result.quota_daily = _int(quota.get("DailyQuota"))
        result.quota_monthly = _int(quota.get("MonthlyQuota"))


def _pages(client: Client, result: Bing, sitemap: Sitemap) -> None:
    listed, _ = sitemap.listed(KEY_PATHS)
    for path in listed:
        page = PageInfo(url=url(path))
        try:
            info = client.get("GetUrlInfo", url=page.url) or {}
        except ApiError as error:
            # A page Bing doesn't know answers with an error rather than empty fields.
            if error.status and error.status < 500:
                result.pages.append(page)
                continue
            raise
        crawled = parse_date(info.get("LastCrawledDate")) if isinstance(info, dict) else None
        page.crawled = crawled.date().isoformat() if crawled else None
        page.http_status = info.get("HttpStatus") if isinstance(info, dict) and crawled else None
        result.pages.append(page)
    result.known = sum(page.crawled is not None for page in result.pages)


def candidates(result: Bing, sitemap: Sitemap, previous_hubs: list[str] | None) -> list[str]:
    """What to submit: key pages Bing hasn't crawled, then hubs that weren't in last month's sitemap (none on
    the first report, which has nothing to compare with)."""
    fresh = [page.url for page in result.pages if page.crawled is None]
    if previous_hubs is not None:
        before = set(previous_hubs)
        fresh += [hub for hub in sitemap.hubs() if hub not in before]
    return list(dict.fromkeys(fresh))


def submit(client: Client, result: Bing, urls: list[str]) -> None:
    """SubmitUrlBatch within the day's quota (10 when Bing doesn't say) and MAX_SUBMIT; the rest waits."""
    if not urls:
        result.submit_note = "nothing to submit"
        return
    room = min(MAX_SUBMIT, result.quota_daily if result.quota_daily is not None else 10)
    batch = urls[: max(room, 0)]
    if not batch:
        result.submit_note = "no submission quota left today"
        return
    client.post("SubmitUrlBatch", {"siteUrl": result.site, "urlList": batch})
    result.submitted = batch
    left = len(urls) - len(batch)
    result.submit_note = f"{len(batch)} submitted" + (f", {left} left for next month" if left else "")


def collect(
    api: Api,
    key: str | None,
    sitemap: Sitemap,
    now: datetime,
    *,
    site: str | None = None,
    submit_urls: bool = False,
    previous_hubs: list[str] | None = None,
) -> Bing:
    result = Bing(site=site or DEFAULT_SITE)
    if not key:
        result.status = "skipped"
        result.note = "no key: set the secret BING_WEBMASTER_API_KEY (docs/RUNBOOK.md §15d)"
        return result
    client = Client(api, key, result.site)
    since = now - timedelta(days=DAYS)
    try:
        # The first call also checks the key: a wrong or revoked one fails every call the same way.
        _feeds(client, result)
    except FAILURES as error:
        result.status, result.note = "error", f"GetFeeds: {describe(error)}"
        return result
    problems: list[str] = []
    steps = (
        ("GetCrawlStats", lambda: _crawl(client, result)),
        ("GetRankAndTrafficStats", lambda: _traffic(client, result, since)),
        ("GetQueryStats", lambda: _queries(client, result, since)),
        ("GetUrlSubmissionQuota", lambda: _quota(client, result)),
        ("GetUrlInfo", lambda: _pages(client, result, sitemap)),
    )
    for name, step in steps:
        try:
            step()
        except FAILURES as error:
            problems.append(f"{name}: {describe(error)}")
    if submit_urls and sitemap.status == "ok":
        try:
            submit(client, result, candidates(result, sitemap, previous_hubs))
        except FAILURES as error:
            problems.append(f"SubmitUrlBatch: {describe(error)}")
    if problems:
        result.status, result.note = "partial", "; ".join(problems)
    return result
