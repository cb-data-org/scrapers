"""The visibility monitor (cb_scrapers.visibility) on recorded answers. The public sources (our sitemap and
robots.txt, the MCP Registry, Smithery, Common Crawl's index) were recorded on 15 Oct 2026; Search Console,
Bing Webmaster Tools and Cloudflare need the site's keys, so their fixtures follow the documented answers."""

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from cb_scrapers.core import PoliteClient
from cb_scrapers.visibility import analytics, archive, bing, commoncrawl, crawlers, google, listings
from cb_scrapers.visibility.__main__ import main
from cb_scrapers.visibility.http import Api, ApiError, error_message
from cb_scrapers.visibility.queries import is_brand, split_queries
from cb_scrapers.visibility.report import Report, delta, digest
from cb_scrapers.visibility.site import KEY_PATHS, Sitemap, fetch_sitemap, parse_sitemap
from tests.fakes import FakeClock, FakeSite

FIXTURES = Path(__file__).parent / "fixtures" / "visibility"
SITE = "https://cervejabarata.com.br"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
PAGE = "<!doctype html><title>Cerveja Barata</title><p>Preços</p>"
BAR = "/salvador/bares/djalmas-drink-s"
#: Key pages Google has indexed, and Bing has crawled, in these tests.
INDEXED = {"/", "/salvador", "/salvador/preco-da-cerveja", "/dados", "/en/salvador"}
CRAWLED_BY_BING = {"/", "/salvador"}
SECRETS = ("bing-test-key", "cf-test-token", "PRIVATE KEY", "ya29.test-access-token")
QUERY_TEXTS = (
    "preço cerveja salvador",
    "happy hour rio vermelho",
    "preco cerveja salvador",
    "bar barato pituba",
)

Route = Callable[[httpx.Request], httpx.Response]
Polite = Callable[..., PoliteClient]


def text(name: str) -> str:
    return (FIXTURES / name).read_text()


def recorded(name: str) -> Any:
    return json.loads(text(name))


def answer(name: str, status: int = 200) -> Route:
    return lambda request: httpx.Response(status, json=recorded(name))


def api_for(site: FakeSite, clock: FakeClock) -> Api:
    return Api(transport=httpx.MockTransport(site), clock=clock, sleep=clock.sleep)


def sitemap_urls() -> list[str]:
    urls, _ = parse_sitemap(text("sitemap.xml"))
    return urls


def our_site(page: Route | None = None) -> dict[str, Any]:
    """The site: its sitemap, robots.txt and every page the sitemap lists."""
    routes: dict[str, Any] = {
        f"{SITE}/sitemap.xml": text("sitemap.xml"),
        f"{SITE}/robots.txt": text("robots.txt"),
    }
    for address in sitemap_urls():
        routes[address] = page or PAGE
    return routes


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def account(key: rsa.RSAPrivateKey) -> dict[str, str]:
    """A service account key file's fields, with a key made for the test (none is ever committed)."""
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return {
        "type": "service_account",
        "project_id": "cb-monitor-test",
        "private_key_id": "0123456789abcdef",
        "private_key": pem,
        "client_email": "visibility-monitor@cb-monitor-test.iam.gserviceaccount.com",
        "client_id": "100000000000000000000",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


# Google ---------------------------------------------------------------------------------------------------

SEARCH = "https://www.googleapis.com/webmasters/v3/sites/sc-domain:cervejabarata.com.br/searchAnalytics/query"
SITEMAPS = "https://www.googleapis.com/webmasters/v3/sites/sc-domain:cervejabarata.com.br/sitemaps"


def inspect(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["siteUrl"] == "sc-domain:cervejabarata.com.br"
    assert request.headers["authorization"] == "Bearer ya29.test-access-token"
    address = body["inspectionUrl"]
    if (address.removeprefix(SITE) or "/") not in INDEXED:
        return httpx.Response(200, json=recorded("gsc-inspect-unknown.json"))
    result = recorded("gsc-inspect-indexed.json")
    status = result["inspectionResult"]["indexStatusResult"]
    status["googleCanonical"] = status["userCanonical"] = address
    return httpx.Response(200, json=result)


def search(request: httpx.Request) -> httpx.Response:
    assert b"sc-domain%3Acervejabarata.com.br" in request.url.raw_path
    body = json.loads(request.content)
    assert body["startDate"] == "2026-10-02" and body["endDate"] == "2026-10-29"
    if body["type"] == "discover":
        return httpx.Response(200, json=recorded("gsc-search-empty.json"))
    if body.get("dimensions") == ["query"]:
        assert body["dimensionFilterGroups"][0]["filters"][0]["expression"] == "BRA"
        return httpx.Response(200, json=recorded("gsc-search-queries.json"))
    name = "gsc-search-totals.json" if "dimensionFilterGroups" in body else "gsc-search-world.json"
    return httpx.Response(200, json=recorded(name))


def token(request: httpx.Request) -> httpx.Response:
    form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
    assert form["grant_type"] == "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
    assert form["assertion"].count(".") == 2
    return httpx.Response(200, json=recorded("gsc-token.json"))


def google_routes() -> dict[str, Any]:
    return {
        google.TOKEN_URL: token,
        google.INSPECT_URL: inspect,
        SEARCH: search,
        SITEMAPS: answer("gsc-sitemaps.json"),
    }


def test_signed_jwt_is_rs256_and_verifies(account: dict[str, str], key: rsa.RSAPrivateKey) -> None:
    jwt = google.signed_jwt(account, now=1_793_534_400)
    header, claims, signature = jwt.split(".")
    assert json.loads(b64decode(header)) == {"alg": "RS256", "typ": "JWT", "kid": "0123456789abcdef"}
    assert json.loads(b64decode(claims)) == {
        "iss": account["client_email"],
        "scope": "https://www.googleapis.com/auth/webmasters.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": 1_793_534_400,
        "exp": 1_793_538_000,
    }
    key.public_key().verify(
        b64decode(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not json", "isn't JSON"),
        ('{"type": "authorized_user"}', "isn't a service account key"),
        ('{"type": "service_account", "client_email": "x@y"}', "lacks private_key"),
    ],
)
def test_bad_keys_are_explained_without_quoting_them(raw: str, message: str) -> None:
    with pytest.raises(google.BadKey, match=message):
        google.parse_account(raw)


def test_unreadable_private_key(account: dict[str, str]) -> None:
    with pytest.raises(google.BadKey, match="can't be read") as error:
        google.signed_jwt(
            {**account, "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"}, 0
        )
    assert "AAAA" not in str(error.value)


def test_window_is_28_days_of_final_pacific_data() -> None:
    start, end = google.window(NOW)
    assert (start.isoformat(), end.isoformat()) == ("2026-10-02", "2026-10-29")


def test_google_report(account: dict[str, str], clock: FakeClock) -> None:
    site = FakeSite({**our_site(), **google_routes()})
    api = api_for(site, clock)
    result = google.collect(api, json.dumps(account), fetch_sitemap(api), NOW)
    assert result.status == "ok", result.note
    assert (result.indexed, result.inspected) == (5, 24)
    assert result.unlisted == ["/salvador/indice"]
    first = result.inspections[0]
    assert (first.url, first.verdict, first.coverage) == (f"{SITE}/", "PASS", "Submitted and indexed")
    assert first.last_crawl == "2026-10-26T06:12:41.045123456Z" and not first.other_canonical
    assert result.inspections[2].coverage == "URL is unknown to Google"
    assert (result.brazil.clicks, result.brazil.impressions) == (230, 3100)
    assert (result.world.clicks, result.discover.clicks) == (251, 0)
    q = result.queries
    assert (q.brand.clicks, q.brand.impressions, q.nonbrand.clicks, q.nonbrand.impressions) == (
        80,
        540,
        45,
        2000,
    )
    assert (q.nonbrand_queries, q.nonbrand_top10) == (3, 2)
    assert (result.sitemap_submitted, result.sitemap_errors, result.sitemap_warnings) == (626, 0, 0)
    assert result.sitemap_read_at == "2026-10-28T03:44:02.104Z"


def test_google_without_a_key_is_reported(clock: FakeClock) -> None:
    result = google.collect(api_for(FakeSite({}), clock), None, Sitemap(), NOW)
    assert result.status == "skipped" and "CB_GSC_SA_JSON" in result.note


def test_a_service_account_without_access_stops_after_one_page(
    account: dict[str, str], clock: FakeClock
) -> None:
    routes = {**our_site(), **google_routes(), google.INSPECT_URL: answer("gsc-error-403.json", 403)}
    site = FakeSite(routes)
    api = api_for(site, clock)
    result = google.collect(api, json.dumps(account), fetch_sitemap(api), NOW)
    assert result.status == "partial"
    assert "URL Inspection: HTTP 403: User does not have sufficient permission" in result.note
    assert [r.url.path for r in site.requests].count("/v1/urlInspection/index:inspect") == 1
    assert result.brazil.clicks == 230


def test_a_refused_token_is_reported(account: dict[str, str], clock: FakeClock) -> None:
    site = FakeSite({google.TOKEN_URL: answer("gsc-token-error.json", 400)})
    result = google.collect(api_for(site, clock), json.dumps(account), Sitemap(), NOW)
    assert result.status == "error"
    assert result.note == "no access token: HTTP 400: Invalid JWT Signature."


# Bing -----------------------------------------------------------------------------------------------------


def bing_routes(submitted: list[dict[str, Any]]) -> dict[str, Any]:
    def info(request: httpx.Request) -> httpx.Response:
        assert request.url.params["siteUrl"] == "https://cervejabarata.com.br/"
        assert request.url.params["apikey"] == "bing-test-key"
        address = request.url.params["url"]
        if (address.removeprefix(SITE) or "/") in CRAWLED_BY_BING:
            return httpx.Response(200, json=recorded("bing-urlinfo.json"))
        return httpx.Response(200, json=recorded("bing-urlinfo-unknown.json"))

    def submit(request: httpx.Request) -> httpx.Response:
        assert request.url.params["apikey"] == "bing-test-key"
        submitted.append(json.loads(request.content))
        return httpx.Response(200, json=recorded("bing-submit.json"))

    return {
        f"{bing.API}/GetFeeds": answer("bing-feeds.json"),
        f"{bing.API}/GetCrawlStats": answer("bing-crawlstats.json"),
        f"{bing.API}/GetRankAndTrafficStats": answer("bing-traffic.json"),
        f"{bing.API}/GetQueryStats": answer("bing-querystats.json"),
        f"{bing.API}/GetUrlSubmissionQuota": answer("bing-quota.json"),
        f"{bing.API}/GetUrlInfo": info,
        f"{bing.API}/SubmitUrlBatch": submit,
    }


def test_bing_dates() -> None:
    assert bing.parse_date("/Date(1316156400000-0700)/") == datetime(2011, 9, 16, 7, 0, tzinfo=UTC)
    assert bing.parse_date("/Date(-62135596800000)/") is None
    assert bing.parse_date(None) is None


def test_bing_report_and_submissions(clock: FakeClock) -> None:
    submitted: list[dict[str, Any]] = []
    site = FakeSite({**our_site(), **bing_routes(submitted)})
    api = api_for(site, clock)
    sitemap = fetch_sitemap(api)
    before = [hub for hub in sitemap.hubs() if "brotas" not in hub]
    result = bing.collect(api, "bing-test-key", sitemap, NOW, submit_urls=True, previous_hubs=before)
    assert result.status == "ok", result.note
    assert (result.sitemap_status, result.sitemap_urls) == ("Success", 626)
    assert (result.crawl_date, result.in_index, result.crawled_pages, result.crawl_errors) == (
        "2026-10-28",
        240,
        55,
        1,
    )
    # The 1 Aug rows are older than 28 days.
    assert (result.traffic.clicks, result.traffic.impressions) == (25, 600)
    q = result.queries
    assert (q.brand.clicks, q.nonbrand.clicks, q.nonbrand_queries, q.nonbrand_top10) == (5, 3, 2, 1)
    assert (result.quota_daily, result.quota_monthly) == (100, 2800)
    assert (result.known, len(result.pages)) == (2, 24)
    assert result.pages[0].crawled == "2026-10-26"
    # Every key page Bing hasn't crawled, then the hubs new since last month.
    urls = submitted[0]["urlList"]
    assert submitted[0]["siteUrl"] == "https://cervejabarata.com.br/"
    assert len(urls) == 24 and urls[-2:] == [f"{SITE}/salvador/brotas", f"{SITE}/en/salvador/brotas"]
    assert f"{SITE}/" not in urls and f"{SITE}/salvador/mercados" in urls
    assert result.submitted == urls and result.submit_note == "24 submitted"


def test_bing_first_report_submits_only_key_pages_within_the_quota(clock: FakeClock) -> None:
    submitted: list[dict[str, Any]] = []
    routes = {**our_site(), **bing_routes(submitted)}
    routes[f"{bing.API}/GetUrlSubmissionQuota"] = lambda request: httpx.Response(
        200, json={"d": {"DailyQuota": 10, "MonthlyQuota": 50}}
    )
    api = api_for(FakeSite(routes), clock)
    result = bing.collect(api, "bing-test-key", fetch_sitemap(api), NOW, submit_urls=True)
    assert len(submitted[0]["urlList"]) == 10
    assert not any("brotas" in address for address in submitted[0]["urlList"])
    assert result.submit_note == "10 submitted, 12 left for next month"


def test_a_wrong_bing_key_is_said_once(clock: FakeClock) -> None:
    site = FakeSite({f"{bing.API}/GetFeeds": answer("bing-error-key.json", 400)})
    result = bing.collect(api_for(site, clock), "bad", Sitemap(), NOW, submit_urls=True)
    assert (result.status, result.note) == ("error", "GetFeeds: HTTP 400: InvalidApiKey")
    assert len(site.requests) == 1


def test_bing_without_a_key_is_reported(clock: FakeClock) -> None:
    result = bing.collect(api_for(FakeSite({}), clock), "", Sitemap(), NOW)
    assert result.status == "skipped" and "BING_WEBMASTER_API_KEY" in result.note


def test_pages_bing_doesnt_know_may_answer_with_an_error(clock: FakeClock) -> None:
    routes = {**our_site(), **bing_routes([])}
    routes[f"{bing.API}/GetUrlInfo"] = lambda request: httpx.Response(
        400, json={"ErrorCode": 7, "Message": "NotFound"}
    )
    api = api_for(FakeSite(routes), clock)
    result = bing.collect(api, "bing-test-key", fetch_sitemap(api), NOW)
    assert result.status == "ok" and result.known == 0 and len(result.pages) == 24


# AI crawlers ----------------------------------------------------------------------------------------------


def test_every_crawler_gets_every_page(clock: FakeClock) -> None:
    site = FakeSite(our_site())
    api = api_for(site, clock)
    result = crawlers.collect(api, fetch_sitemap(api))
    assert result.paths == ["/", "/salvador", "/salvador/preco-da-cerveja", "/dados", BAR]
    assert result.passing == len(crawlers.CRAWLERS) == 12 and not result.alerts and result.status == "ok"
    agents = {request.headers["user-agent"] for request in site.requests if request.url.path == "/dados"}
    assert agents == set(crawlers.CRAWLERS.values())


def test_a_refused_crawler_is_an_alert(clock: FakeClock) -> None:
    def page(request: httpx.Request) -> httpx.Response:
        if "GPTBot" in request.headers["user-agent"]:
            return httpx.Response(403, text="Forbidden")
        if "PerplexityBot" in request.headers["user-agent"]:
            return httpx.Response(200, text="<title>Just a moment...</title>")
        return httpx.Response(200, text=PAGE)

    api = api_for(FakeSite(our_site(page)), clock)
    result = crawlers.collect(api, fetch_sitemap(api))
    assert result.status == "alert" and result.passing == 10
    assert result.alerts == [
        "GPTBot refused on 5 of 5 pages (/ …)",
        "PerplexityBot refused on 5 of 5 pages (/ …)",
    ]


def test_robots_txt_blocking_a_token_is_an_alert(clock: FakeClock) -> None:
    routes = our_site()
    routes[f"{SITE}/robots.txt"] = "User-agent: Google-Extended\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    api = api_for(FakeSite(routes), clock)
    result = crawlers.collect(api, fetch_sitemap(api))
    assert result.robots_blocked == ["Google-Extended"]
    assert result.alerts == ["robots.txt disallows Google-Extended"]


# Common Crawl, listings, analytics, archive ---------------------------------------------------------------

CDX = "https://index.commoncrawl.org/CC-MAIN-2026-39-index"


def test_common_crawl_without_captures(clock: FakeClock) -> None:
    site = FakeSite(
        {
            commoncrawl.COLLECTIONS: text("collinfo.json"),
            CDX: lambda r: httpx.Response(404, text=text("cc-no-captures.json")),
        }
    )
    result = commoncrawl.collect(api_for(site, clock))
    assert (result.status, result.crawl, result.captures) == ("ok", "CC-MAIN-2026-39", 0)
    assert result.crawl_name == "September 2026 Index"
    assert site.requests[1].url.params["url"] == "cervejabarata.com.br"
    assert site.requests[1].url.params["matchType"] == "domain"


def test_common_crawl_captures_are_counted(clock: FakeClock) -> None:
    site = FakeSite({commoncrawl.COLLECTIONS: text("collinfo.json"), CDX: text("cc-captures.ndjson")})
    result = commoncrawl.collect(api_for(site, clock))
    assert (result.captures, result.ok_captures) == (4, 3)


def test_a_common_crawl_outage_is_retried_then_reported(clock: FakeClock) -> None:
    site = FakeSite({commoncrawl.COLLECTIONS: text("collinfo.json"), CDX: 504})
    result = commoncrawl.collect(api_for(site, clock))
    assert result.status == "error" and result.note.startswith("HTTP 504")
    assert len(site.requests) == 4


def listing_routes(glama: int = 200) -> dict[str, Any]:
    return {
        listings.REGISTRY: answer("registry-servers.json"),
        "https://glama.ai/robots.txt": "User-agent: AdsBot-Google\nDisallow: /api/\n",
        listings.GLAMA: glama if glama != 200 else "<title>Cerveja Barata - MCP Connector | Glama</title>",
        listings.SMITHERY: answer("smithery-servers.json"),
    }


def test_listings(clock: FakeClock, polite: Polite) -> None:
    site = FakeSite(listing_routes())
    result = listings.collect(api_for(site, clock), polite(site))
    assert [(item.name, item.listed, item.note) for item in result.items] == [
        ("MCP Registry", "yes", "version 0.2.1"),
        ("Glama", "yes", ""),
        ("Smithery", "no", ""),
    ]
    assert result.found == 2 and result.status == "ok"


def test_listings_not_there_yet(clock: FakeClock, polite: Polite) -> None:
    routes = listing_routes(glama=404)
    routes[listings.REGISTRY] = lambda request: httpx.Response(
        200, json={"servers": [], "metadata": {"count": 0}}
    )
    smithery = recorded("smithery-servers.json")
    smithery["servers"].append(
        {"qualifiedName": "cervejabarata/mcp", "homepage": "https://cervejabarata.com.br"}
    )
    routes[listings.SMITHERY] = lambda request: httpx.Response(200, json=smithery)
    site = FakeSite(routes)
    result = listings.collect(api_for(site, clock), polite(site))
    assert [item.listed for item in result.items] == ["no", "no", "yes"]
    assert result.items[2].url == "https://smithery.ai/server/cervejabarata/mcp"


def test_ai_referrals(clock: FakeClock) -> None:
    def graphql(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer cf-test-token"
        variables = json.loads(request.content)["variables"]
        assert variables == {
            "account": "a" * 32,
            "from": "2026-10-04",
            "to": "2026-10-31",
            "host": "cervejabarata.com.br",
        }
        return httpx.Response(200, json=recorded("cf-rum.json"))

    result = analytics.collect(
        api_for(FakeSite({analytics.ENDPOINT: graphql}), clock), "cf-test-token", "a" * 32, NOW
    )
    assert (result.status, result.visits, result.page_views) == ("ok", 2410, 5200)
    assert result.assistants == {"ChatGPT": 18, "Perplexity": 3, "Claude": 1} and result.ai_visits == 22
    assert result.top_referrers[:3] == [("www.google.com", 610), ("l.instagram.com", 25), ("chatgpt.com", 18)]
    assert all(host and host != "cervejabarata.com.br" for host, _ in result.top_referrers)


def test_analytics_errors_and_missing_token(clock: FakeClock) -> None:
    site = FakeSite({analytics.ENDPOINT: answer("cf-error.json")})
    result = analytics.collect(api_for(site, clock), "t", "a", NOW)
    assert (result.status, result.note) == ("error", "not authorized for that account")
    assert analytics.collect(api_for(site, clock), None, "a", NOW).status == "skipped"


def test_wayback_captures_are_spaced_and_stop_when_asked(clock: FakeClock) -> None:
    routes = our_site()
    saves = [f"{archive.SAVE}{SITE}{path}" for path in ("/", "/salvador", "/salvador/preco-da-cerveja")]
    routes.update({saves[0]: 200, saves[1]: 302, saves[2]: 429})
    api = api_for(FakeSite(routes), clock)
    sitemap = fetch_sitemap(api)
    clock.sleeps.clear()
    result = archive.collect(api, sitemap)
    assert result.captures == {"/": 200, "/salvador": 302, "/salvador/preco-da-cerveja": 429}
    assert result.saved == 2 and "slow down" in result.note
    assert clock.sleeps == [20.0, 20.0]


# Queries, report, the run ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "brand"),
    [
        ("cerveja barata", True),
        ("Cerveja Barata Salvador", True),
        ("cervejabarata.com.br", True),
        ("cerveja-barata", True),
        ("cerveja gelada barata", False),
        ("preço da cerveja", False),
    ],
)
def test_brand_queries(query: str, brand: bool) -> None:
    assert is_brand(query) is brand


def test_query_split() -> None:
    split = split_queries(
        [("cerveja barata", 10, 100, 2.0), ("chopp barato", 3, 50, 4.0), ("bar", 1, 9, None)]
    )
    assert (split.brand.clicks, split.nonbrand.clicks, split.nonbrand_queries, split.nonbrand_top10) == (
        10,
        4,
        2,
        1,
    )
    assert split.brand_click_share == pytest.approx(10 / 14)


def test_delta() -> None:
    assert delta(12, 9) == " (+3)" and delta(1200, 1500) == " (-300)" and delta(5, 5) == ""
    assert delta(5, None) == "" and delta(5, True) == ""


def test_key_pages() -> None:
    assert len(KEY_PATHS) == len(set(KEY_PATHS)) == 25
    assert all(path.startswith("/") for path in KEY_PATHS)


def test_a_sitemap_index_is_followed(clock: FakeClock) -> None:
    index = (
        '<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>{SITE}/sitemap-0.xml</loc></sitemap></sitemapindex>"
    )
    site = FakeSite({f"{SITE}/sitemap.xml": index, f"{SITE}/sitemap-0.xml": text("sitemap.xml")})
    sitemap = fetch_sitemap(api_for(site, clock))
    assert len(sitemap.urls) == 29 and sitemap.first_bar() == f"{SITE}{BAR}"
    assert len(sitemap.hubs()) == 26


def all_routes(submitted: list[dict[str, Any]]) -> dict[str, Any]:
    routes = {**our_site(), **google_routes(), **bing_routes(submitted), **listing_routes()}
    routes[commoncrawl.COLLECTIONS] = text("collinfo.json")
    routes[CDX] = lambda request: httpx.Response(404, text=text("cc-no-captures.json"))
    routes[analytics.ENDPOINT] = answer("cf-rum.json")
    for path in ("/", "/salvador", "/salvador/preco-da-cerveja", "/imprensa", "/dados"):
        routes[f"{archive.SAVE}{SITE}{path}"] = 200
    return routes


def environment(account: dict[str, str]) -> dict[str, str]:
    """The mirror's secrets the monitor reads: no token for the site's admin API among them."""
    return {
        "CB_GSC_SA_JSON": json.dumps(account),
        "BING_WEBMASTER_API_KEY": "bing-test-key",
        "CB_CF_ANALYTICS_TOKEN": "cf-test-token",
        "CB_CF_ACCOUNT_ID": "a" * 32,
    }


def test_the_monthly_run(
    tmp_path: Path,
    clock: FakeClock,
    polite: Polite,
    account: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    submitted: list[dict[str, Any]] = []
    site = FakeSite(all_routes(submitted))
    hubs = [hub for hub in Sitemap(urls=sitemap_urls()).hubs() if "brotas" not in hub]
    previous = {
        "sitemap_status": "ok",
        "hubs": hubs,
        "google": {"status": "ok", "indexed": 3, "brazil": {"clicks": 190, "impressions": 2500}},
        "bing": {"status": "ok", "in_index": 200},
        "common_crawl": {"status": "ok", "captures": 0},
        "analytics": {"status": "skipped", "visits": 0},
    }
    (tmp_path / "previous.json").write_text(json.dumps(previous))
    out = tmp_path / "out"
    code = main(
        ["--out", str(out), "--previous", str(tmp_path / "previous.json"), "--submit", "--archive"],
        env=environment(account),
        api=api_for(site, clock),
        polite=polite(site),
        now=NOW,
    )
    assert code == 0
    line = (
        "🔎 Visibility, 1 Nov 2026: Google 5/24 key pages indexed (+2), Brazil 28 d: 230 clicks (+40) / "
        "3,100 impr. (+600), brand 64 %, 2 non-brand in the top 10 · Bing 240 pages indexed (+40), 2/24 key "
        "pages crawled, 25 clicks, 24 pages submitted · AI crawlers 12/12 OK · Common Crawl: none in "
        "CC-MAIN-2026-39 · MCP listings 2/3 (Registry, Glama) · AI referrals 22 (ChatGPT 18, Perplexity 3, "
        "Claude 1) · 2,410 visits (28 d)"
    )
    assert (out / "digest.txt").read_text() == line + "\n"
    assert capsys.readouterr().out.strip().endswith(line)
    report = (out / "report.md").read_text()
    assert report.startswith(f"# Visibility report, 1 Nov 2026\n\n> {line}\n\n")
    assert "| `/salvador` | ✅ | Submitted and indexed | 2026-10-26 |" in report
    assert "5 of 5 captures went through" in report
    data = json.loads((out / "visibility.json").read_text())
    assert data["digest"] == line and data["sitemap_urls"] == 29 and len(data["hubs"]) == 26
    assert data["google"]["indexed"] == 5 and data["bing"]["in_index"] == 240
    assert len(submitted) == 1
    # The site's admin API is never called: the report stays in the run.
    assert not any(request.url.path.startswith("/api/admin") for request in site.requests)
    # Nothing secret, and no query text, in what a public run shows.
    for output in (report, json.dumps(data, ensure_ascii=False), line):
        for secret in (*SECRETS, account["private_key"][40:80], *QUERY_TEXTS):
            assert secret not in output


def test_the_weekly_check_passes_when_every_crawler_gets_in(
    tmp_path: Path, clock: FakeClock, polite: Polite, account: dict[str, str]
) -> None:
    site = FakeSite(all_routes([]))
    args = ["--out", str(tmp_path), "--only", "crawlers", "--fail-on-alert"]
    assert main(args, env=environment(account), api=api_for(site, clock), polite=polite(site), now=NOW) == 0
    assert "## Alerts" not in (tmp_path / "report.md").read_text()
    assert {request.url.host for request in site.requests} == {"cervejabarata.com.br"}


def test_the_weekly_check_fails_on_a_refusal(
    tmp_path: Path,
    clock: FakeClock,
    polite: Polite,
    account: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    routes = all_routes([])

    def page(request: httpx.Request) -> httpx.Response:
        refused = "ClaudeBot" in request.headers["user-agent"]
        return httpx.Response(403 if refused else 200, text=PAGE)

    for address in sitemap_urls():
        routes[address] = page
    site = FakeSite(routes)
    args = ["--out", str(tmp_path), "--only", "crawlers", "--fail-on-alert"]
    assert main(args, env=environment(account), api=api_for(site, clock), polite=polite(site), now=NOW) == 1
    assert "::warning::ClaudeBot refused on 5 of 5 pages" in capsys.readouterr().out
    report = (tmp_path / "report.md").read_text()
    assert "## Alerts\n\n- ⚠️ ClaudeBot refused on 5 of 5 pages (/ …)\n" in report
    assert "AI Crawl Control" in report
    # Without --fail-on-alert (the monthly report) the same refusal is reported, and the run passes.
    monthly = ["--out", str(tmp_path), "--only", "crawlers"]
    assert main(monthly, env={}, api=api_for(site, clock), polite=polite(site), now=NOW) == 0


def test_without_keys_the_run_still_reports(tmp_path: Path, clock: FakeClock, polite: Polite) -> None:
    site = FakeSite(all_routes([]))
    code = main(["--out", str(tmp_path)], env={}, api=api_for(site, clock), polite=polite(site), now=NOW)
    assert code == 0
    report = (tmp_path / "report.md").read_text()
    line = (tmp_path / "digest.txt").read_text()
    assert (
        "Google: no key · Bing: no key · AI crawlers 12/12 OK" in line and "Web Analytics: no token" in line
    )
    for secret in ("CB_GSC_SA_JSON", "BING_WEBMASTER_API_KEY", "CB_CF_ANALYTICS_TOKEN"):
        assert secret in report


def test_error_messages() -> None:
    def message(status: int, body: Any) -> str:
        return error_message(
            httpx.Response(status, json=body)
            if not isinstance(body, str)
            else httpx.Response(status, text=body)
        )

    assert message(403, recorded("gsc-error-403.json")).startswith("User does not have sufficient permission")
    assert message(400, recorded("bing-error-key.json")) == "InvalidApiKey"
    assert message(400, recorded("gsc-token-error.json")) == "Invalid JWT Signature."
    assert message(200, recorded("cf-error.json")) == "not authorized for that account"
    assert message(502, "  Bad\n gateway  ") == "Bad gateway"
    assert str(ApiError(429, "slow down")) == "HTTP 429: slow down"


def test_report_json_round_trips() -> None:
    report = Report(generated_at=NOW.isoformat(), hubs=[f"{SITE}/"])
    assert json.loads(json.dumps(report.to_json()))["hubs"] == [f"{SITE}/"]
    assert digest(report, None, NOW) == "🔎 Visibility, 1 Nov 2026: "
