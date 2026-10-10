from collections.abc import Callable

import httpx
import pytest

from cb_scrapers.core import USER_AGENT, BlockedError, DisallowedError, PoliteClient
from tests.fakes import FakeClock, FakeSite

Polite = Callable[..., PoliteClient]
ROBOTS = "https://bar.test/robots.txt"


def test_identifies_itself(polite: Polite) -> None:
    site = FakeSite({ROBOTS: "", "https://bar.test/menu": "ok"})
    polite(site).get("https://bar.test/menu")
    assert {request.headers["user-agent"] for request in site.requests} == {USER_AGENT}
    assert USER_AGENT.startswith("CervejaBarataBot/1.0 (+https://cervejabarata.com.br/bot)")


def test_follows_robots_txt(polite: Polite) -> None:
    site = FakeSite({ROBOTS: "User-agent: *\nDisallow: /privado", "https://bar.test/menu": "ok"})
    client = polite(site)
    assert client.get("https://bar.test/menu").text == "ok"
    with pytest.raises(DisallowedError):
        client.get("https://bar.test/privado/precos")
    assert site.paths().count("/robots.txt") == 1


def test_honours_rules_written_for_our_bot(polite: Polite) -> None:
    site = FakeSite({ROBOTS: "User-agent: CervejaBarataBot\nDisallow: /\n\nUser-agent: *\nAllow: /"})
    with pytest.raises(DisallowedError):
        polite(site).get("https://bar.test/menu")


@pytest.mark.parametrize(
    ("robots_status", "allowed"),
    [(404, True), (410, True), (401, False), (403, False), (429, False), (500, False)],
)
def test_robots_txt_errors(polite: Polite, robots_status: int, allowed: bool) -> None:
    site = FakeSite({ROBOTS: robots_status, "https://bar.test/menu": "ok"})
    client = polite(site)
    if allowed:
        assert client.get("https://bar.test/menu").text == "ok"
    else:
        with pytest.raises(DisallowedError):
            client.get("https://bar.test/menu")


def test_official_apis_can_skip_robots(polite: Polite) -> None:
    site = FakeSite({"https://graph.test/v1/media": "{}"})
    polite(site).get("https://graph.test/v1/media", robots=False)
    assert site.paths() == ["/v1/media"]


def test_spaces_requests_per_host(polite: Polite, clock: FakeClock) -> None:
    site = FakeSite(
        {
            ROBOTS: "",
            "https://bar.test/a": "a",
            "https://bar.test/b": "b",
            "https://other.test/robots.txt": "",
            "https://other.test/c": "c",
        }
    )
    client = polite(site, delay=3.0)
    client.get("https://bar.test/a")
    client.get("https://other.test/c")  # another host: no need to wait for bar.test
    client.get("https://bar.test/b")
    # robots.txt → a waits 3 s; other.test robots → c waits 3 s; b already waited out by c
    assert clock.sleeps == [3.0, 3.0]


def test_a_request_can_space_its_host_differently(polite: Polite, clock: FakeClock) -> None:
    site = FakeSite({ROBOTS: "", "https://bar.test/a": "a", "https://bar.test/b": "b"})
    client = polite(site, delay=8.0)
    client.get("https://bar.test/a", delay=2.0)
    client.get("https://bar.test/b", delay=2.0)
    assert clock.sleeps == [2.0, 2.0]


def test_crawl_delay_wins_when_longer(polite: Polite, clock: FakeClock) -> None:
    site = FakeSite({ROBOTS: "User-agent: *\nCrawl-delay: 10", "https://bar.test/a": "a"})
    polite(site, delay=3.0).get("https://bar.test/a")
    assert clock.sleeps == [10.0]


@pytest.mark.parametrize("status", [403, 429])
def test_stops_when_refused(polite: Polite, status: int) -> None:
    site = FakeSite({ROBOTS: "", "https://bar.test/a": status})
    with pytest.raises(BlockedError, match=f"HTTP {status}"):
        polite(site).get("https://bar.test/a")
    assert site.paths().count("/a") == 1  # never retried


def test_says_when_a_refusal_asks_to_come_back(polite: Polite) -> None:
    site = FakeSite(
        {
            ROBOTS: "",
            "https://bar.test/a": lambda request: httpx.Response(429, headers={"Retry-After": "600"}),
        }
    )
    with pytest.raises(BlockedError, match=r"HTTP 429 \(Retry-After: 600\)"):
        polite(site).get("https://bar.test/a")


def test_retries_server_errors_with_backoff(polite: Polite, clock: FakeClock) -> None:
    answers = iter([httpx.Response(503), httpx.Response(200, text="ok")])
    site = FakeSite({ROBOTS: "", "https://bar.test/a": lambda _: next(answers)})
    assert polite(site, delay=1.0).get("https://bar.test/a").text == "ok"
    assert site.paths().count("/a") == 2
    assert clock.sleeps == [1.0, 1.0]  # spacing after robots.txt, then backoff


def test_gives_up_after_retries(polite: Polite) -> None:
    site = FakeSite({ROBOTS: "", "https://bar.test/a": 500})
    with pytest.raises(httpx.HTTPStatusError):
        polite(site, retries=2).get("https://bar.test/a")
    assert site.paths().count("/a") == 3


def test_retries_network_errors(polite: Polite) -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(200, text="ok")

    site = FakeSite({ROBOTS: "", "https://bar.test/a": flaky})
    assert polite(site).get("https://bar.test/a").text == "ok"


def test_client_errors_are_not_retried(polite: Polite) -> None:
    site = FakeSite({ROBOTS: ""})
    with pytest.raises(httpx.HTTPStatusError):
        polite(site).get("https://bar.test/gone")
    assert site.paths().count("/gone") == 1
