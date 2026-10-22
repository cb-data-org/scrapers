"""The month's numbers as one line (owner tools stay in English; it heads the report, and will be the Telegram
message once the site has an ingest-scoped endpoint for it), a Markdown report for the run's summary, and JSON
for the artifact that next month's run compares against. All three carry counts, statuses and our own URLs
only: no query texts, no keys (a public repository's run summary and artifacts are public)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .analytics import Analytics
from .archive import Archive
from .bing import Bing
from .commoncrawl import CommonCrawl
from .crawlers import TOKENS_ONLY, Crawlers
from .google import Google
from .listings import Listings
from .site import SITE

Previous = dict[str, Any] | None


@dataclass(slots=True)
class Report:
    generated_at: str
    sitemap_status: str = "ok"
    sitemap_urls: int | None = None
    #: The sitemap's hub pages, so next month's run knows which ones are new.
    hubs: list[str] = field(default_factory=list)
    google: Google | None = None
    bing: Bing | None = None
    crawlers: Crawlers | None = None
    common_crawl: CommonCrawl | None = None
    listings: Listings | None = None
    analytics: Analytics | None = None
    archive: Archive | None = None

    @property
    def alerts(self) -> list[str]:
        return list(self.crawlers.alerts) if self.crawlers else []

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def number(value: float) -> str:
    return f"{round(value):,}"


def percent(share: float) -> str:
    return f"{round(share * 100)} %"


def day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b %Y}"


def page(address: str) -> str:
    return f"`{address.removeprefix(SITE) or '/'}`"


def _get(previous: Previous, *keys: str) -> Any:
    value: Any = previous
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _before(previous: Previous, section: str) -> Previous:
    """Last month's report, when that section worked then (a skipped or failed one has nothing to compare)."""
    return previous if _get(previous, section, "status") in ("ok", "partial", "alert") else None


def delta(now: float | None, before: Any) -> str:
    """ " (+3)" against last month's figure; nothing without one, or when it didn't move."""
    if now is None or isinstance(before, bool) or not isinstance(before, (int, float)) or now == before:
        return ""
    return f" ({now - before:+,})"


def _google_line(google: Google, previous: Previous) -> str:
    if google.status == "skipped":
        return "Google: no key"
    if google.status == "error":
        return "Google: error (see the report)"
    before = _before(previous, "google")
    clicks, impressions = google.brazil.clicks, google.brazil.impressions
    line = f"Google {google.indexed}/{google.inspected} key pages indexed"
    line += delta(google.indexed, _get(before, "google", "indexed"))
    line += f", Brazil 28 d: {number(clicks)} clicks"
    line += delta(clicks, _get(before, "google", "brazil", "clicks"))
    line += f" / {number(impressions)} impr."
    line += delta(impressions, _get(before, "google", "brazil", "impressions"))
    share = google.queries.brand_click_share
    if share is not None:
        line += f", brand {percent(share)}"
    return line + f", {google.queries.nonbrand_top10} non-brand in the top 10"


def _bing_line(bing: Bing, previous: Previous) -> str:
    if bing.status == "skipped":
        return "Bing: no key"
    if bing.status == "error":
        return "Bing: error (see the report)"
    if bing.in_index is None:
        line = "Bing: no index count yet"
    else:
        line = f"Bing {number(bing.in_index)} pages indexed"
        line += delta(bing.in_index, _get(_before(previous, "bing"), "bing", "in_index"))
    if bing.pages:
        line += f", {bing.known}/{len(bing.pages)} key pages crawled"
    line += f", {number(bing.traffic.clicks)} clicks"
    if bing.submitted:
        line += f", {len(bing.submitted)} pages submitted"
    return line


def _crawlers_line(crawlers: Crawlers) -> str:
    line = f"AI crawlers {crawlers.passing}/{len(crawlers.checks)} OK"
    refused = [check.name for check in crawlers.checks if check.refused]
    if refused:
        return f"⚠️ {line} ({', '.join(refused)} refused)"
    if crawlers.robots_blocked:
        return f"⚠️ {line} (robots.txt blocks {', '.join(crawlers.robots_blocked)})"
    return line


def _common_crawl_line(cc: CommonCrawl, previous: Previous) -> str:
    if cc.status == "error":
        return "Common Crawl: error"
    if not cc.captures:
        return f"Common Crawl: none in {cc.crawl}"
    change = delta(cc.captures, _get(_before(previous, "common_crawl"), "common_crawl", "captures"))
    return f"Common Crawl {number(cc.captures)}{change} ({cc.crawl})"


def _listings_line(listings: Listings) -> str:
    names = [item.name.removeprefix("MCP ") for item in listings.items if item.listed == "yes"]
    line = f"MCP listings {listings.found}/{len(listings.items)}"
    return line + (f" ({', '.join(names)})" if names else "")


def _ranked(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in sorted(counts.items(), key=lambda pair: -pair[1]))


def _analytics_line(analytics: Analytics, previous: Previous) -> str:
    if analytics.status == "skipped":
        return "Web Analytics: no token"
    if analytics.status == "error":
        return "Web Analytics: error"
    line = f"AI referrals {analytics.ai_visits}"
    if analytics.assistants:
        line += f" ({_ranked(analytics.assistants)})"
    visits = analytics.visits
    change = delta(visits, _get(_before(previous, "analytics"), "analytics", "visits"))
    return line + f" · {number(visits)} visits (28 d){change}"


def digest(report: Report, previous: Previous, now: datetime) -> str:
    """One line: what the dated gates read, as far as the free APIs give it."""
    parts: list[str] = []
    if report.google:
        parts.append(_google_line(report.google, previous))
    if report.bing:
        parts.append(_bing_line(report.bing, previous))
    if report.crawlers:
        parts.append(_crawlers_line(report.crawlers))
    if report.common_crawl:
        parts.append(_common_crawl_line(report.common_crawl, previous))
    if report.listings:
        parts.append(_listings_line(report.listings))
    if report.analytics:
        parts.append(_analytics_line(report.analytics, previous))
    return f"🔎 Visibility, {day(now)}: " + " · ".join(parts)


def _status(section: Any) -> str:
    if not section.note:
        return "" if section.status in ("ok", "alert") else f"**{section.status}.**\n\n"
    label = {"skipped": "Skipped", "error": "Failed", "partial": "Partly done", "alert": "Alert"}
    return f"**{label.get(section.status, 'Note')}:** {section.note}\n\n"


def _google_md(google: Google) -> str:
    text = "## Google Search Console\n\n" + _status(google)
    if google.status in ("skipped", "error"):
        return text
    q = google.queries
    text += f"Property `{google.property}`, {google.start} to {google.end} (Pacific time, final data).\n\n"
    text += f"- Key pages on Google: **{google.indexed} of {google.inspected}** inspected"
    if google.unlisted:
        text += f" (not in the sitemap yet, so not asked: {', '.join(google.unlisted)})"
    text += ".\n"
    if google.sitemap_submitted is not None:
        text += (
            f"- Sitemap: {number(google.sitemap_submitted)} pages submitted, {google.sitemap_errors or 0} "
            f"errors, {google.sitemap_warnings or 0} warnings, last read "
            f"{google.sitemap_read_at or 'never'}.\n"
        )
    text += (
        f"- Brazil: {number(google.brazil.clicks)} clicks, {number(google.brazil.impressions)} impressions. "
        f"Worldwide: {number(google.world.clicks)} clicks, {number(google.world.impressions)} impressions. "
        f"Discover: {number(google.discover.clicks)} clicks, {number(google.discover.impressions)} "
        "impressions.\n"
        f"- Queries in Brazil (counts only): brand {number(q.brand.clicks)} clicks / "
        f"{number(q.brand.impressions)} impressions; non-brand {number(q.nonbrand.clicks)} / "
        f"{number(q.nonbrand.impressions)} over {q.nonbrand_queries} queries, {q.nonbrand_top10} of them at "
        "an average position of 10 or better.\n"
    )
    if google.inspections:
        text += "\n| Page | On Google | Coverage | Last crawl |\n|---|---|---|---|\n"
        for item in google.inspections:
            mark = "✅" if item.indexed else ("⚠️" if item.verdict == "FAIL" else "no")
            coverage = item.coverage or item.verdict or "?"
            if item.other_canonical:
                coverage += " (Google chose another canonical)"
            text += f"| {page(item.url)} | {mark} | {coverage} | {(item.last_crawl or 'never')[:10]} |\n"
    return text + "\n"


def _bing_md(bing: Bing) -> str:
    text = "## Bing Webmaster Tools\n\n" + _status(bing)
    if bing.status in ("skipped", "error"):
        return text
    q = bing.queries
    if bing.sitemap_urls is not None:
        text += f"- Sitemap: {bing.sitemap_status}, {number(bing.sitemap_urls)} pages.\n"
    if bing.crawl_date:
        text += (
            f"- Crawl stats for {bing.crawl_date}: {number(bing.in_index or 0)} pages in the index, "
            f"{number(bing.crawled_pages or 0)} crawled, {bing.crawl_errors or 0} crawl errors, "
            f"{bing.blocked_by_robots or 0} blocked by robots.txt.\n"
        )
    else:
        text += "- No crawl stats yet.\n"
    text += (
        f"- Last 28 days: {number(bing.traffic.clicks)} clicks, {number(bing.traffic.impressions)} "
        "impressions. "
        f"Queries (counts only): brand {number(q.brand.clicks)} clicks / {number(q.brand.impressions)} "
        f"impressions, non-brand {number(q.nonbrand.clicks)} / {number(q.nonbrand.impressions)} over "
        f"{q.nonbrand_queries} queries, {q.nonbrand_top10} of them at an average position of 10 or better.\n"
    )
    if bing.quota_daily is not None:
        text += f"- URL submission quota: {bing.quota_daily} today, {bing.quota_monthly} this month.\n"
    if bing.submit_note:
        text += f"- Submissions: {bing.submit_note}.\n"
    text += "- Not in the API: AI Performance (Copilot citations) and the IndexNow page; see the dashboard.\n"
    if bing.pages:
        text += f"\nKey pages Bing has crawled: **{bing.known} of {len(bing.pages)}**.\n\n"
        text += "| Page | Last crawl |\n|---|---|\n"
        for info in bing.pages:
            text += f"| {page(info.url)} | {info.crawled or 'not yet'} |\n"
    if bing.submitted:
        text += "\nSubmitted this month: " + ", ".join(page(address) for address in bing.submitted) + ".\n"
    return text + "\n"


def _crawlers_md(crawlers: Crawlers) -> str:
    text = "## AI crawlers\n\n" + _status(crawlers)
    text += (
        "Each page fetched with each crawler's user agent, from GitHub's machines. Cloudflare can't verify "
        "them "
        "as the real bots, so a 200 here is necessary, not sufficient; a refusal is an alarm.\n\n"
    )
    text += "| Crawler | " + " | ".join(f"`{path}`" for path in crawlers.paths) + " | robots.txt |\n"
    text += "|---|" + "---|" * (len(crawlers.paths) + 1) + "\n"
    for check in crawlers.checks:
        cells = " | ".join(str(check.statuses.get(path, "")) for path in crawlers.paths)
        text += f"| {check.name} | {cells} | {'allowed' if check.robots_allowed else 'blocked'} |\n"
    blocked = ", ".join(crawlers.robots_blocked)
    text += (
        f"\nrobots.txt for {' and '.join(TOKENS_ONLY)}: {'blocked: ' + blocked if blocked else 'allowed'}.\n"
    )
    return text + "\n"


def _common_crawl_md(cc: CommonCrawl) -> str:
    text = "## Common Crawl\n\n" + _status(cc)
    if cc.status == "error":
        return text
    crawl = f"{cc.crawl} ({cc.crawl_name})"
    if cc.captures:
        text += f"{number(cc.captures)} captures in {crawl}, {number(cc.ok_captures)} of them 200.\n"
    else:
        text += f"No capture in {crawl}. It takes no submissions; links from pages it crawls help.\n"
    return text + "\n"


def _listings_md(listings: Listings) -> str:
    text = "## MCP listings\n\n" + _status(listings) + "| Listing | Listed | Note |\n|---|---|---|\n"
    for item in listings.items:
        text += f"| [{item.name}]({item.url}) | {item.listed} | {item.note} |\n"
    return text + "\n"


def _analytics_md(analytics: Analytics) -> str:
    text = "## Visits (Cloudflare Web Analytics)\n\n" + _status(analytics)
    if analytics.status in ("skipped", "error"):
        return text
    text += (
        f"{analytics.start} to {analytics.end}: {number(analytics.visits)} visits, "
        f"{number(analytics.page_views)} page views, bots left out. Lower bounds: many assistants' apps send "
        "no referrer.\n\n"
    )
    text += f"From AI assistants: {_ranked(analytics.assistants) or 'none yet'}.\n\n"
    if analytics.top_referrers:
        text += "| Referrer | Visits |\n|---|---|\n"
        for host, visits in analytics.top_referrers:
            text += f"| {host} | {number(visits)} |\n"
    return text + "\n"


def _archive_md(archive: Archive) -> str:
    text = "## Wayback Machine\n\n" + _status(archive)
    if archive.captures:
        asked = ", ".join(f"`{path}` {status or 'no answer'}" for path, status in archive.captures.items())
        text += f"{archive.saved} of {len(archive.captures)} captures went through: {asked}.\n"
    return text + "\n"


def markdown(report: Report, line: str, now: datetime) -> str:
    text = f"# Visibility report, {day(now)}\n\n> {line}\n\n"
    if report.alerts:
        text += "## Alerts\n\n" + "".join(f"- ⚠️ {alert}\n" for alert in report.alerts) + "\n"
        text += "Look at Cloudflare's Security → Bots and AI Crawl Control, and at robots.txt.\n\n"
    if report.sitemap_status != "ok":
        text += f"Our sitemap couldn't be read ({report.sitemap_status}): every key page was asked about.\n\n"
    elif report.sitemap_urls is not None:
        text += f"Our sitemap lists {number(report.sitemap_urls)} pages, {len(report.hubs)} of them hubs.\n\n"
    sections = (
        (report.google, _google_md),
        (report.bing, _bing_md),
        (report.crawlers, _crawlers_md),
        (report.common_crawl, _common_crawl_md),
        (report.listings, _listings_md),
        (report.analytics, _analytics_md),
        (report.archive, _archive_md),
    )
    for section, render in sections:
        if section is not None:
            text += render(section)
    return text + (
        "## Not read by this report\n\n"
        "- Bing's AI Performance (Copilot citations) and IndexNow pages, and Google's generative-AI report: "
        "dashboards only.\n"
        "- Whether assistants cite us: the 16-prompt panel (docs/geo-log.md, `geo-monitoring`).\n"
    )
