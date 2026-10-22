"""Google Search Console, read with a service account (the CB_GSC_SA_JSON secret: its JSON key, read-only
scope, added to the property as a Restricted user; docs/RUNBOOK.md §15d).

- URL Inspection (2,000 a day per property) on the key pages: indexed or not, Google's coverage state, last
  crawl, and whether Google picked another canonical.
- Search Analytics over 28 days that ended 3 days ago (Search Console's final data): clicks and impressions in
  Brazil and worldwide, Discover, and the queries split brand against the rest (counts only, never the texts).
- Sitemaps: how many pages the submitted sitemap lists. Google's API no longer says how many are indexed.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .http import Api, ApiError, check, describe
from .queries import QuerySplit, Totals, split_queries
from .site import KEY_PATHS, Sitemap, url

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
SITES_API = "https://www.googleapis.com/webmasters/v3/sites"
#: The Domain property verified by DNS on 9 Oct 2026 (#103); CB_GSC_PROPERTY overrides it.
DEFAULT_PROPERTY = "sc-domain:cervejabarata.com.br"
#: Search Console's dates are Pacific time; its final data trails by about three days.
PACIFIC = ZoneInfo("America/Los_Angeles")
DAYS = 28
LAG_DAYS = 3
FAILURES = (ApiError, httpx.HTTPError, ValueError, KeyError, TypeError)


class BadKey(ValueError):
    """The service account key can't be used (the message never quotes the key)."""


@dataclass(slots=True)
class Inspection:
    url: str
    #: PASS ("URL is on Google"), NEUTRAL (excluded), FAIL, or "" when it couldn't be inspected.
    verdict: str = ""
    #: Google's words: "Submitted and indexed", "Crawled - currently not indexed", "URL is unknown to Google".
    coverage: str = ""
    last_crawl: str | None = None
    #: Google indexed another URL as the canonical of this one.
    other_canonical: bool = False

    @property
    def indexed(self) -> bool:
        return self.verdict == "PASS"


@dataclass(slots=True)
class Google:
    status: str = "ok"
    note: str = ""
    property: str = DEFAULT_PROPERTY
    start: str = ""
    end: str = ""
    inspections: list[Inspection] = field(default_factory=list)
    #: Key pages the sitemap doesn't list yet (not inspected).
    unlisted: list[str] = field(default_factory=list)
    indexed: int = 0
    inspected: int = 0
    sitemap_submitted: int | None = None
    sitemap_errors: int | None = None
    sitemap_warnings: int | None = None
    sitemap_read_at: str | None = None
    brazil: Totals = field(default_factory=Totals)
    world: Totals = field(default_factory=Totals)
    discover: Totals = field(default_factory=Totals)
    queries: QuerySplit = field(default_factory=QuerySplit)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _compact(value: Mapping[str, Any]) -> str:
    return b64url(json.dumps(value, separators=(",", ":")).encode())


def parse_account(raw: str) -> dict[str, str]:
    """The service account's JSON key, checked for the fields the token needs."""
    try:
        account = json.loads(raw)
    except json.JSONDecodeError:
        raise BadKey("CB_GSC_SA_JSON isn't JSON: store the whole downloaded key file") from None
    if not isinstance(account, dict) or account.get("type") != "service_account":
        raise BadKey("CB_GSC_SA_JSON isn't a service account key (type service_account)")
    missing = [name for name in ("client_email", "private_key") if not account.get(name)]
    if missing:
        raise BadKey(f"CB_GSC_SA_JSON lacks {', '.join(missing)}")
    return account


def signed_jwt(account: Mapping[str, str], now: int, scope: str = SCOPE) -> str:
    """The token request's assertion: a JWT signed RS256 with the service account's key, valid one hour."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    try:
        key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    except (ValueError, TypeError):
        raise BadKey("CB_GSC_SA_JSON's private_key can't be read") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise BadKey("CB_GSC_SA_JSON's private_key isn't an RSA key")
    header = {"alg": "RS256", "typ": "JWT"}
    if account.get("private_key_id"):
        header["kid"] = account["private_key_id"]
    claims = {"iss": account["client_email"], "scope": scope, "aud": TOKEN_URL, "iat": now, "exp": now + 3600}
    signing_input = f"{_compact(header)}.{_compact(claims)}"
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{b64url(signature)}"


def access_token(api: Api, account: Mapping[str, str], now: int) -> str:
    response = check(api.post(TOKEN_URL, data={"grant_type": GRANT, "assertion": signed_jwt(account, now)}))
    token = response.json().get("access_token")
    if not token:
        raise ApiError(response.status_code, "no access_token in Google's answer")
    return str(token)


def window(now: datetime, days: int = DAYS, lag: int = LAG_DAYS) -> tuple[date, date]:
    """The last `days` days of final data, in Search Console's Pacific dates."""
    end = now.astimezone(PACIFIC).date() - timedelta(days=lag)
    return end - timedelta(days=days - 1), end


def _number(value: Any) -> int | None:
    """Google sends int64 fields as strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def in_brazil(body: dict[str, Any]) -> dict[str, Any]:
    country = {"dimension": "country", "operator": "equals", "expression": "BRA"}
    return {**body, "dimensionFilterGroups": [{"groupType": "and", "filters": [country]}]}


class Client:
    """Search Console calls with one access token."""

    def __init__(self, api: Api, token: str, prop: str) -> None:
        self.api = api
        self.prop = prop
        self.auth = {"Authorization": f"Bearer {token}"}

    def inspect(self, address: str) -> Inspection:
        body = {"inspectionUrl": address, "siteUrl": self.prop, "languageCode": "en-US"}
        response = check(self.api.post(INSPECT_URL, json=body, headers=self.auth))
        status = response.json().get("inspectionResult", {}).get("indexStatusResult", {})
        canonical, declared = status.get("googleCanonical"), status.get("userCanonical")
        return Inspection(
            url=address,
            verdict=str(status.get("verdict", "")),
            coverage=str(status.get("coverageState", "")),
            last_crawl=status.get("lastCrawlTime"),
            other_canonical=bool(canonical and declared and canonical != declared),
        )

    def search(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        address = f"{SITES_API}/{quote(self.prop, safe='')}/searchAnalytics/query"
        rows = check(self.api.post(address, json=body, headers=self.auth)).json().get("rows", [])
        return rows if isinstance(rows, list) else []

    def sitemaps(self) -> list[dict[str, Any]]:
        address = f"{SITES_API}/{quote(self.prop, safe='')}/sitemaps"
        entries = check(self.api.get(address, headers=self.auth)).json().get("sitemap", [])
        return entries if isinstance(entries, list) else []


def _totals(rows: list[dict[str, Any]]) -> Totals:
    totals = Totals()
    for row in rows:
        totals.add(row.get("clicks", 0), row.get("impressions", 0))
    return totals


def _search(client: Client, result: Google) -> None:
    period = {"startDate": result.start, "endDate": result.end, "dataState": "final"}
    result.brazil = _totals(client.search(in_brazil({**period, "type": "web"})))
    result.world = _totals(client.search({**period, "type": "web"}))
    result.discover = _totals(client.search({**period, "type": "discover"}))
    rows = client.search(in_brazil({**period, "type": "web", "dimensions": ["query"], "rowLimit": 25000}))
    result.queries = split_queries(
        (
            str((row.get("keys") or [""])[0]),
            float(row.get("clicks", 0)),
            float(row.get("impressions", 0)),
            row.get("position"),
        )
        for row in rows
    )


def _sitemaps(client: Client, result: Google) -> None:
    read: list[str] = []
    for entry in client.sitemaps():
        web = next((part for part in entry.get("contents", []) if part.get("type") == "web"), {})
        result.sitemap_submitted = (result.sitemap_submitted or 0) + (_number(web.get("submitted")) or 0)
        result.sitemap_errors = (result.sitemap_errors or 0) + (_number(entry.get("errors")) or 0)
        result.sitemap_warnings = (result.sitemap_warnings or 0) + (_number(entry.get("warnings")) or 0)
        if entry.get("lastDownloaded"):
            read.append(str(entry["lastDownloaded"]))
    result.sitemap_read_at = max(read, default=None)


def collect(
    api: Api, raw_key: str | None, sitemap: Sitemap, now: datetime, prop: str | None = None
) -> Google:
    """Everything above; a missing or refused key is a status with the reason, never an exception."""
    result = Google(property=prop or DEFAULT_PROPERTY)
    if not raw_key:
        result.status = "skipped"
        result.note = "no key: set the secret CB_GSC_SA_JSON (docs/RUNBOOK.md §15d)"
        return result
    start, end = window(now)
    result.start, result.end = start.isoformat(), end.isoformat()
    try:
        account = parse_account(raw_key)
        client = Client(api, access_token(api, account, int(now.timestamp())), result.property)
    except BadKey as error:
        result.status, result.note = "error", str(error)
        return result
    except FAILURES as error:
        result.status, result.note = "error", f"no access token: {describe(error)}"
        return result

    problems: list[str] = []
    listed, result.unlisted = sitemap.listed(KEY_PATHS)
    for path in listed:
        try:
            result.inspections.append(client.inspect(url(path)))
        except FAILURES as error:
            # A refusal (not a user of the property, the day's quota) applies to every page: stop asking.
            problems.append(f"URL Inspection: {describe(error)}")
            break
    result.inspected = len(result.inspections)
    result.indexed = sum(inspection.indexed for inspection in result.inspections)
    for part, step in (("Search Analytics", _search), ("Sitemaps", _sitemaps)):
        try:
            step(client, result)
        except FAILURES as error:
            problems.append(f"{part}: {describe(error)}")
    if problems:
        result.status = "error" if len(problems) == 3 and not result.inspections else "partial"
        result.note = "; ".join(problems)
    return result
