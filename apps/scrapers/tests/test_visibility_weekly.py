"""The weekly search report (cb_scrapers.visibility.weekly) on answers shaped like Search Console's,
Bing's and Cloudflare's (their recorded fixtures, plus per-window answers built here), against a small
targets file in the CEO's format (docs/targets.yaml)."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cb_scrapers.visibility import analytics, bing, google, weekly
from cb_scrapers.visibility.http import Api
from tests.fakes import FakeClock, FakeSite

FIXTURES = Path(__file__).parent / "fixtures" / "visibility"
SITE = "https://cervejabarata.com.br"
#: Monday 2 Nov 2026, 09:00 in Salvador: the workflow's hour.
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SEARCH = "https://www.googleapis.com/webmasters/v3/sites/sc-domain:cervejabarata.com.br/searchAnalytics/query"
SITEMAPS = "https://www.googleapis.com/webmasters/v3/sites/sc-domain:cervejabarata.com.br/sitemaps"
#: Pages Google has indexed in these tests.
INDEXED = {"/", "/salvador", "/salvador/mercados"}

TARGETS = """
updated: 2026-10-23
traffic:
  - {name: "8 Jan floor", by: 2027-01-08, visits_28d: 2500}
search:
  - {name: "30 Oct impressions", by: 2026-10-30, metric: google_impressions_28d, value: 100}
  - {name: "10 Dec impressions", by: 2026-12-10, metric: google_impressions_28d, value: 3000, country: BRA}
  - {name: "10 Dec non-brand", by: 2026-12-10, metric: nonbrand_queries_top10, value: 3}
gates:
  - name: "30 Oct pages on Google"
    date: 2026-10-30
    metric: google_indexed_of_requested
    value: 2
    of: 3
    pages: ["/", "/salvador", "/dados"]
  - {name: "1 Dec pages Bing lists", date: 2026-12-01, metric: bing_pages_indexed, value: 50}
  - {name: "30 Oct crawlers", date: 2026-10-30, metric: crawlers_seen_7d, value: 4, of: 6}
ctr_baselines:
  date: 2026-10-23
  reread: 2026-11-06
  pages: {"/salvador/bares/p2-bar": 0.006, "/preco-por-litro": 0.003}
"""


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def raw_key() -> str:
    """A service account key file made for the test (none is ever committed)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps(
        {
            "type": "service_account",
            "private_key_id": "0123456789abcdef",
            "private_key": pem,
            "client_email": "weekly-report@cb-test.iam.gserviceaccount.com",
            "token_uri": google.TOKEN_URL,
        }
    )


def total(clicks: int, impressions: int, position: float) -> dict[str, Any]:
    return {"rows": [{"clicks": clicks, "impressions": impressions, "ctr": 0, "position": position}]}


def rows(*items: tuple[str, int, int, float]) -> dict[str, Any]:
    return {
        "rows": [
            {"keys": [key], "clicks": clicks, "impressions": impressions, "ctr": 0, "position": position}
            for key, clicks, impressions, position in items
        ]
    }


def search(request: httpx.Request) -> httpx.Response:
    """Search Analytics by window (7 days: 24-30 Oct; 28 days: 3-30 Oct), dimension and country."""
    body = json.loads(request.content)
    days = (date.fromisoformat(body["endDate"]) - date.fromisoformat(body["startDate"])).days + 1
    assert body["endDate"] == "2026-10-30" and days in (7, 28) and body["dataState"] == "final"
    brazil = "dimensionFilterGroups" in body
    dimensions = body.get("dimensions")
    if dimensions is None:
        if days == 7:
            return httpx.Response(200, json=total(40, 3000, 7.4))
        return httpx.Response(200, json=total(110, 9000, 8.0) if not brazil else total(104, 8600, 7.9))
    if dimensions == ["page"]:
        if days == 7:
            return httpx.Response(
                200,
                json=rows(
                    (f"{SITE}/salvador/bares/p2-bar", 6, 400, 5.1),
                    (f"{SITE}/preco-por-litro", 4, 700, 6.0),
                    (f"{SITE}/", 9, 120, 2.0),
                ),
            )
        return httpx.Response(200, json=rows((f"{SITE}/salvador/bares/p2-bar", 12, 1000, 5.0)))
    assert dimensions == ["query"]
    if days == 7:
        return httpx.Response(200, json=rows(("p2 bar pituba", 5, 300, 4.0), ("cerveja barata", 2, 50, 30.0)))
    assert brazil, "non-brand queries are counted in Brazil"
    return httpx.Response(
        200,
        json=rows(
            ("p2 bar pituba", 8, 900, 4.0),
            ("bar do lula", 3, 200, 6.5),
            ("cerveja barata salvador", 2, 100, 12.0),
            ("preco litrao", 0, 80, 22.0),
        ),
    )


def inspect(request: httpx.Request) -> httpx.Response:
    address = json.loads(request.content)["inspectionUrl"]
    indexed = (address.removeprefix(SITE) or "/") in INDEXED
    return httpx.Response(
        200, json=recorded("gsc-inspect-indexed.json" if indexed else "gsc-inspect-unknown.json")
    )


def bing_date(days_ago: float) -> str:
    return f"/Date({int((NOW - timedelta(days=days_ago)).timestamp() * 1000)})/"


def bing_routes() -> dict[str, Any]:
    def answer(d: Any) -> Any:
        return lambda request: httpx.Response(200, json={"d": d})

    return {
        f"{bing.API}/GetFeeds": answer(
            [
                {"Url": f"{SITE}/sitemap.xml", "UrlCount": 664, "Status": "Success"},
                {"Url": f"{SITE}/atualizacoes.xml", "UrlCount": 13, "Status": "Success"},
            ]
        ),
        f"{bing.API}/GetRankAndTrafficStats": answer(
            [{"Date": bing_date(day), "Clicks": 1, "Impressions": 10} for day in (2, 5, 9, 20, 40)]
        ),
        f"{bing.API}/GetQueryStats": answer(
            [
                {
                    "Date": bing_date(3),
                    "Query": "cerveja barata",
                    "Clicks": 1,
                    "Impressions": 15,
                    "AvgImpressionPosition": 4,
                },
                {
                    "Date": bing_date(3),
                    "Query": "p2 bar",
                    "Clicks": 0,
                    "Impressions": 5,
                    "AvgImpressionPosition": 12,
                },
                {
                    "Date": bing_date(10),
                    "Query": "p2 bar",
                    "Clicks": 1,
                    "Impressions": 10,
                    "AvgImpressionPosition": 8,
                },
            ]
        ),
        f"{bing.API}/GetPageStats": answer(
            [
                {
                    "Date": bing_date(3),
                    "Query": f"{SITE}/salvador",
                    "Clicks": 1,
                    "Impressions": 12,
                    "AvgImpressionPosition": 5,
                }
            ]
        ),
        f"{bing.API}/GetCrawlStats": answer(
            [
                {"Date": bing_date(1), "InIndex": 41, "CrawledPages": 30},
                {"Date": bing_date(4), "InIndex": 12, "CrawledPages": 20},
                {"Date": bing_date(12), "InIndex": 2, "CrawledPages": 2},
            ]
        ),
    }


def visits(request: httpx.Request) -> httpx.Response:
    variables = json.loads(request.content)["variables"]
    days = (date.fromisoformat(variables["to"]) - date.fromisoformat(variables["from"])).days + 1
    body = recorded("cf-rum.json")
    body["data"]["viewer"]["accounts"][0]["total"] = [
        {"count": 900, "sum": {"visits": 300 if days == 7 else 1100}}
    ]
    return httpx.Response(200, json=body)


def api_with(routes: dict[str, Any]) -> tuple[Api, FakeSite]:
    site = FakeSite(routes)
    clock = FakeClock()
    return Api(transport=httpx.MockTransport(site), clock=clock, sleep=clock.sleep), site


def every_route() -> dict[str, Any]:
    return {
        google.TOKEN_URL: lambda request: httpx.Response(200, json=recorded("gsc-token.json")),
        google.INSPECT_URL: inspect,
        SEARCH: search,
        SITEMAPS: lambda request: httpx.Response(200, json=recorded("gsc-sitemaps.json")),
        **bing_routes(),
        analytics.ENDPOINT: visits,
    }


def keys(raw_key: str) -> dict[str, str]:
    return {
        "CB_GSC_SA_JSON": raw_key,
        "BING_WEBMASTER_API_KEY": "bing-test-key",
        "CB_CF_ANALYTICS_TOKEN": "cf-test-token",
        "CB_CF_ACCOUNT_ID": "account",
    }


def test_the_week_against_the_ceos_numbers(raw_key: str, tmp_path: Path) -> None:
    (tmp_path / "targets.yaml").write_text(TARGETS)
    api, site = api_with(every_route())
    out = tmp_path / "out"
    code = weekly.main(
        ["--out", str(out), "--targets", str(tmp_path / "targets.yaml")], env=keys(raw_key), api=api, now=NOW
    )
    assert code == 0
    data = json.loads((out / "report.json").read_text())
    assert data["day"] == "2026-11-02"
    assert data["google"]["week"] == {
        "start": "2026-10-24",
        "end": "2026-10-30",
        "clicks": 40,
        "impressions": 3000,
        "position": 7.4,
    }
    assert data["google"]["month_brazil"]["impressions"] == 8600
    # "p2 bar pituba" and "bar do lula" sit at 10 or better; "cerveja barata salvador" is brand.
    assert data["google"]["nonbrand_top10"] == 2
    assert [row["key"] for row in data["google"]["pages"]] == [
        "/",
        "/salvador/bares/p2-bar",
        "/preco-por-litro",
    ]
    assert data["bing"]["week"]["clicks"] == 2 and data["bing"]["month"]["impressions"] == 40
    assert data["bing"]["in_index"] == 41 and data["bing"]["crawled_week"] == 50
    assert data["bing"]["sitemap_urls"] == 664 and data["bing"]["week"]["position"] == 6.0
    assert data["visits"] == {"status": "ok", "note": "", "week": 300, "month": 1100}

    verdicts = {check["name"]: (check["now"], check["verdict"]) for check in data["checks"]}
    assert verdicts["8 Jan floor"] == ("1,100", "behind: 44 % of it, 67 days left")
    assert verdicts["30 Oct impressions"] == ("9,000", "met")
    # The 10 Dec target counts Brazil only.
    assert verdicts["10 Dec impressions"] == ("8,600", "met")
    assert verdicts["10 Dec non-brand"] == ("2", "behind: 67 % of it, 38 days left")
    # Of the gate's three pages, "/" and "/salvador" are on Google.
    assert verdicts["30 Oct pages on Google"] == ("2 of 3", "passes")
    assert verdicts["1 Dec pages Bing lists"] == ("41", "due in 29 days, 9 short")
    assert verdicts["30 Oct crawlers"] == ("n/a", "not read weekly")
    assert [check["target"] for check in data["checks"] if check["name"] == "30 Oct crawlers"] == [
        "4 of 6 on 30 Oct"
    ]
    assert verdicts["/salvador/bares/p2-bar"] == (
        "1.2 % (1,000 impressions, 28 days)",
        "up (re-read 06 Nov)",
    )
    assert verdicts["/preco-por-litro"][1] == "no data"

    text = (out / "message.txt").read_text()
    assert text.splitlines()[0] == "Weekly search report · 2026-11-02"
    assert "Google, 7 days: 3,000 impressions, 40 clicks, CTR 1.3 %, position 7.4" in text
    assert "key pages indexed: 3 of 25" in text
    assert "Bing, 7 days: 20 impressions, 2 clicks, CTR 10.0 %, position 6.0 · 41 pages in its index" in text
    assert "Visits: 300 in 7 days, 1,100 in 28 days" in text
    assert "• 8 Jan floor: 1,100 for 2,500 by 08 Jan 2027 → behind: 44 % of it, 67 days left" in text
    doc = (out / "report.md").read_text()
    for heading in (
        "# Weekly search report, 2026-11-02",
        "## Against the CEO's numbers",
        "## Google",
        "## Bing",
    ):
        assert heading in doc
    assert "| p2 bar pituba | 5 | 300 | 1.7 % | 4.0 |" in doc
    assert "**The snippet test (PR #790):**" in doc
    # No key ever reaches the report.
    for secret in ("bing-test-key", "cf-test-token", "PRIVATE KEY", "ya29."):
        assert secret not in doc and secret not in text and secret not in (out / "report.json").read_text()
    assert any(request.url.path.endswith("/GetPageStats") for request in site.requests)


def test_missing_keys_are_said_and_nothing_is_guessed(tmp_path: Path) -> None:
    (tmp_path / "targets.yaml").write_text(TARGETS)
    api, site = api_with({})
    out = tmp_path / "out"
    assert (
        weekly.main(
            ["--out", str(out), "--targets", str(tmp_path / "targets.yaml")], env={}, api=api, now=NOW
        )
        == 0
    )
    text = (out / "message.txt").read_text()
    assert "Google: no key: set the secret CB_GSC_SA_JSON (issue #805)" in text
    assert "Bing: no key: set the secret BING_WEBMASTER_API_KEY (issue #805)" in text
    assert "Visits: no token: set the secret CB_CF_ANALYTICS_TOKEN" in text
    data = json.loads((out / "report.json").read_text())
    assert {check["verdict"] for check in data["checks"] if check["kind"] != "ctr"} == {
        "no data",
        "not read weekly",
    }
    assert site.requests == []


def test_changes_against_last_week(raw_key: str, tmp_path: Path) -> None:
    api, _ = api_with(every_route())
    previous = tmp_path / "previous.json"
    previous.write_text(
        json.dumps(
            {
                "google": {"week": {"clicks": 32, "impressions": 2500, "position": 8.0}},
                "bing": {"week": {"clicks": 0, "impressions": 0}},
                "visits": {"week": 250},
            }
        )
    )
    out = tmp_path / "out"
    weekly.main(["--out", str(out), "--previous", str(previous)], env=keys(raw_key), api=api, now=NOW)
    text = (out / "message.txt").read_text()
    assert "3,000 impressions (+20 %), 40 clicks (+25 %)" in text
    assert "20 impressions (new), 2 clicks (new)" in text
    assert "Visits: 300 in 7 days (+20 %)" in text
    assert "no targets file given" in text


def test_gate_and_goal_verdicts() -> None:
    today = date(2026, 11, 2)
    entry = {"name": "floor", "by": "2027-01-08", "value": 2500}
    assert weekly._goal("traffic", entry, 2600, today).verdict == "met"
    assert (
        weekly._goal("traffic", {**entry, "by": "2026-10-30"}, 1000, today).verdict == "missed (40 % of it)"
    )
    assert weekly._goal("traffic", entry, None, today).verdict == "no data"
    # The targets file's convention: a row whose note calls it a line is not a floor.
    line = {**entry, "value": 1000, "note": "a line, not a target"}
    assert weekly._goal("traffic", line, 170, today).verdict == "under the line: 17 % of it, 67 days left"
    assert weekly._goal("traffic", line, 1200, today).verdict == "over the line"
    gate = {"name": "Bing", "date": "2026-10-30", "value": 30}
    assert weekly._gate(gate, 31, today, None).verdict == "passes"
    assert weekly._gate(gate, 12, today, None).verdict == "failed on 30 Oct"
    assert weekly._gate({**gate, "date": "2026-11-09"}, 12, today, None).verdict == "due in 7 days, 18 short"


def test_a_gate_without_its_pages_is_not_guessed(raw_key: str) -> None:
    api, _ = api_with(every_route())
    targets = {
        "gates": [
            {
                "name": "requested",
                "date": "2026-10-30",
                "metric": "google_indexed_of_requested",
                "value": 12,
                "of": 15,
            }
        ]
    }
    report = weekly.run(api, keys(raw_key), NOW, targets, "")
    assert [(check.now, check.verdict) for check in report.checks] == [("n/a", "no data")]


def test_an_unreadable_targets_file(tmp_path: Path) -> None:
    broken = tmp_path / "targets.yaml"
    broken.write_text("traffic: [unclosed")
    assert weekly.load_targets(broken)[1].startswith("the targets file couldn't be read")
    assert weekly.load_targets(None)[1].startswith("no targets file given")
