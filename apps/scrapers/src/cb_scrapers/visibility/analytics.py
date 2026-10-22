"""Visits from AI assistants, from Cloudflare Web Analytics (the cookieless beacon on every page, bots left
out): visits and page views over 28 days, and the referring hosts. Same token as the site's daily digest
(Account Analytics Read, `cb-watchdog`), stored on the mirror as CB_CF_ANALYTICS_TOKEN with the
CB_CF_ACCOUNT_ID variable.

Lower bounds: many assistants' apps send no referrer, and the owner's own visits never count (NextDNS blocks
the beacon on that network).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from .http import Api, ApiError, check, describe
from .site import HOST

ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
DAYS = 28
QUERY = """query ($account: string!, $from: Date, $to: Date, $host: string) {
  viewer {
    accounts(filter: { accountTag: $account }) {
      total: rumPageloadEventsAdaptiveGroups(
        limit: 1
        filter: { date_geq: $from, date_leq: $to, requestHost: $host, bot: 0 }
      ) {
        count
        sum { visits }
      }
      referrers: rumPageloadEventsAdaptiveGroups(
        limit: 100
        filter: { date_geq: $from, date_leq: $to, requestHost: $host, bot: 0 }
        orderBy: [count_DESC]
      ) {
        count
        sum { visits }
        dimensions { refererHost }
      }
    }
  }
}"""

#: Assistants and the hosts their links come from.
ASSISTANTS: dict[str, tuple[str, ...]] = {
    "ChatGPT": ("chatgpt.com", "chat.openai.com"),
    "Perplexity": ("perplexity.ai",),
    "Claude": ("claude.ai",),
    "Copilot": ("copilot.microsoft.com", "copilot.cloud.microsoft"),
    "Gemini": ("gemini.google.com", "bard.google.com"),
    "Meta AI": ("meta.ai",),
    "DeepSeek": ("chat.deepseek.com",),
    "Mistral": ("chat.mistral.ai",),
    "Grok": ("grok.com",),
}


@dataclass(slots=True)
class Analytics:
    status: str = "ok"
    note: str = ""
    start: str = ""
    end: str = ""
    visits: int = 0
    page_views: int = 0
    #: Assistant → visits (only those with any).
    assistants: dict[str, int] = field(default_factory=dict)
    #: The ten referring hosts with the most visits (hosts only).
    top_referrers: list[tuple[str, int]] = field(default_factory=list)

    @property
    def ai_visits(self) -> int:
        return sum(self.assistants.values())


def assistant(host: str) -> str | None:
    host = host.lower().removeprefix("www.")
    for name, domains in ASSISTANTS.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return name
    return None


def collect(api: Api, token: str | None, account: str | None, now: datetime) -> Analytics:
    result = Analytics()
    if not token or not account:
        result.status = "skipped"
        result.note = (
            "no token: set the secret CB_CF_ANALYTICS_TOKEN and the variable CB_CF_ACCOUNT_ID "
            "(docs/RUNBOOK.md §15d)"
        )
        return result
    end = now.date() - timedelta(days=1)
    start = end - timedelta(days=DAYS - 1)
    result.start, result.end = start.isoformat(), end.isoformat()
    variables = {"account": account, "from": result.start, "to": result.end, "host": HOST}
    try:
        response = check(
            api.post(
                ENDPOINT,
                json={"query": QUERY, "variables": variables},
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        body = response.json()
        if body.get("errors"):
            # GraphQL reports refusals with a 200: the message is the news.
            raise ApiError(0, str(body["errors"][0].get("message", "GraphQL error")))
        groups = (body.get("data") or {}).get("viewer", {}).get("accounts", [{}])[0]
    except (ApiError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
        result.status, result.note = "error", describe(error)
        return result
    total = (groups.get("total") or [{}])[0]
    result.page_views = int(total.get("count") or 0)
    result.visits = int((total.get("sum") or {}).get("visits") or 0)
    referrers: list[tuple[str, int]] = []
    for row in groups.get("referrers") or []:
        host = str((row.get("dimensions") or {}).get("refererHost") or "")
        visits = int((row.get("sum") or {}).get("visits") or 0)
        if not host or host == HOST or host.endswith(f".{HOST}") or not visits:
            continue
        referrers.append((host, visits))
        name = assistant(host)
        if name:
            result.assistants[name] = result.assistants.get(name, 0) + visits
    result.top_referrers = sorted(referrers, key=lambda pair: -pair[1])[:10]
    return result
