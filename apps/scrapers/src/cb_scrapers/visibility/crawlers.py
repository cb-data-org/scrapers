"""Do search and AI crawlers still get our pages? Each key page is fetched with each crawler's own user agent,
and robots.txt is read for each crawler's token. Cloudflare changed its AI-crawler defaults once already
(15 Sep 2026) and can again, so a 403, a 429 or a challenge page here is an alert.

These requests come from GitHub's machines, not the vendors' published addresses, so Cloudflare can't verify
them as the real bots: a 200 doesn't prove the real crawler gets in, but a refused user agent is a clear
signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.robotparser import RobotFileParser

import httpx

from .http import Api
from .site import PROBE_PATHS, Sitemap, url

#: Each crawler's user agent as its vendor documents it (Oct 2026).
CRAWLERS: dict[str, str] = {
    "Googlebot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; "
        "+http://www.google.com/bot.html) Chrome/141.0.7390.122 Safari/537.36"
    ),
    "Bingbot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; "
        "+http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36"
    ),
    "GPTBot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.3; "
        "+https://openai.com/gptbot"
    ),
    "OAI-SearchBot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.3; "
        "+https://openai.com/searchbot"
    ),
    "ChatGPT-User": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; "
        "+https://openai.com/bot"
    ),
    "ClaudeBot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; "
        "+claudebot@anthropic.com)"
    ),
    "Claude-SearchBot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; "
        "+Claude-SearchBot@anthropic.com)"
    ),
    "Claude-User": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; "
        "+Claude-User@anthropic.com)"
    ),
    "PerplexityBot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; "
        "+https://perplexity.ai/perplexitybot)"
    ),
    "Perplexity-User": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; "
        "+https://perplexity.ai/perplexity-user)"
    ),
    "CCBot": "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "meta-externalagent": (
        "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)"
    ),
}
#: robots.txt tokens without a user agent of their own: they decide how crawled pages may be used (Gemini,
#: Apple Intelligence), so only robots.txt is checked for them.
TOKENS_ONLY = ("Google-Extended", "Applebot-Extended")
#: A bot check instead of the page (Cloudflare's "Just a moment…", its challenge scripts).
CHALLENGE = re.compile(
    r"<title>\s*(?:just a moment|attention required|um momento)|cf-chl-|challenge-platform", re.I
)


@dataclass(slots=True)
class CrawlerCheck:
    name: str
    #: Path → final HTTP status (0: no answer; 403 also stands for a challenge page served with a 200).
    statuses: dict[str, int] = field(default_factory=dict)
    robots_allowed: bool = True

    @property
    def ok(self) -> bool:
        return self.robots_allowed and all(status == 200 for status in self.statuses.values())

    @property
    def refused(self) -> bool:
        return any(status in (401, 403, 429) for status in self.statuses.values())


@dataclass(slots=True)
class Crawlers:
    status: str = "ok"
    note: str = ""
    paths: list[str] = field(default_factory=list)
    checks: list[CrawlerCheck] = field(default_factory=list)
    #: robots.txt tokens that are disallowed on a probed page.
    robots_blocked: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    @property
    def passing(self) -> int:
        return sum(check.ok for check in self.checks)


def robots_rules(api: Api) -> RobotFileParser | None:
    rules = RobotFileParser(url("/robots.txt"))
    try:
        response = api.get(url("/robots.txt"))
    except httpx.HTTPError:
        return None
    if not response.is_success:
        return None
    rules.parse(response.text.splitlines())
    return rules


def fetch_as(api: Api, agent: str, address: str) -> int:
    try:
        response = api.get(address, headers={"User-Agent": agent})
    except httpx.HTTPError:
        return 0
    if response.status_code == 200 and CHALLENGE.search(response.text[:20000]):
        return 403
    return response.status_code


def collect(api: Api, sitemap: Sitemap) -> Crawlers:
    result = Crawlers(paths=list(PROBE_PATHS))
    bar = sitemap.first_bar()
    if bar:
        result.paths.append(bar.removeprefix(url("")))
    rules = robots_rules(api)
    if rules is None:
        result.note = "robots.txt couldn't be read"
    for name, agent in CRAWLERS.items():
        check = CrawlerCheck(name=name)
        for path in result.paths:
            check.statuses[path] = fetch_as(api, agent, url(path))
            if rules is not None and not rules.can_fetch(name, url(path)):
                check.robots_allowed = False
        result.checks.append(check)
        if check.refused:
            refused = [path for path, status in check.statuses.items() if status in (401, 403, 429)]
            result.alerts.append(
                f"{name} refused on {len(refused)} of {len(result.paths)} pages ({refused[0]} …)"
            )
        elif not check.robots_allowed:
            result.alerts.append(f"robots.txt disallows {name}")
    if rules is not None:
        for token in TOKENS_ONLY:
            if not all(rules.can_fetch(token, url(path)) for path in result.paths):
                result.robots_blocked.append(token)
                result.alerts.append(f"robots.txt disallows {token}")
    failing = [check for check in result.checks if not check.ok]
    if failing and not result.alerts:
        # Not a refusal (a 404 or a 5xx): worth a look, not an alarm.
        result.note = "; ".join(
            f"{check.name}: "
            + ", ".join(f"{path} {code}" for path, code in check.statuses.items() if code != 200)
            for check in failing[:3]
        )
    if result.alerts:
        result.status = "alert"
    return result
