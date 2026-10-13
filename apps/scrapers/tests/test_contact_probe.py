import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from cb_scrapers import contact_probe
from cb_scrapers.contact_probe import (
    Result,
    contact_link,
    extract_emails,
    parse_urls,
    plausible,
    probe,
    report,
    summary_line,
)
from cb_scrapers.core import PoliteClient
from tests.fakes import FakeClock, FakeSite

Polite = Callable[..., PoliteClient]
FIXTURES = Path(__file__).parent / "fixtures" / "contacts"
HOME = (FIXTURES / "bar_home.html").read_text()
CONTACT = (FIXTURES / "bar_contact.html").read_text()
LINKPAGE = (FIXTURES / "linkpage.html").read_text()
CHALLENGE = (FIXTURES / "challenge.html").read_text()
SITE = "https://bardoteste.com.br"


def test_extracts_mailto_and_json_ld_and_drops_junk() -> None:
    assert extract_emails(HOME) == ["contato@bardoteste.com.br", "reservas@bardoteste.com.br"]


def test_extracts_obfuscated_addresses() -> None:
    assert extract_emails(CONTACT) == ["eventos@bardoteste.com.br", "financeiro@bardoteste.com.br"]


def test_link_page_mailto() -> None:
    assert extract_emails(LINKPAGE) == ["bardoteste@gmail.com"]


@pytest.mark.parametrize(
    ("email", "ok"),
    [
        ("contato@bar.com.br", True),
        ("bar@gmail.com", True),
        ("logo@2x.png", False),
        ("icon@3x.webp", False),
        ("abc123def456abc123def456abc12345@o123.ingest.sentry.io", False),
        ("dev@sentry-next.wixpress.com", False),
        ("info@example.com", False),
        ("seuemail@gmail.com", False),
        ("nome@dominio.com.br", False),
    ],
)
def test_plausible(email: str, ok: bool) -> None:
    assert plausible(email) is ok


def test_contact_link_same_site_only() -> None:
    assert contact_link(HOME, f"{SITE}/") == f"{SITE}/fale-conosco"
    html = '<a href="https://other.com/contato">x</a><a href="/sobre-nos">Quem somos</a>'
    assert contact_link(html, "https://www.bar.com.br/") == "https://www.bar.com.br/sobre-nos"
    assert contact_link('<a href="https://bar.com.br/contato">x</a>', "https://www.bar.com.br/") == (
        "https://bar.com.br/contato"
    )
    assert contact_link('<a href="/cardapio">Cardápio</a>', SITE) is None


def test_contact_link_stays_under_a_shared_hosts_path() -> None:
    assert contact_link(LINKPAGE, "https://linktr.ee/bardoteste") is None
    html = '<a href="/sobre">Cozify</a><a href="/marreco/contato">Contato</a>'
    assert contact_link(html, "https://cozify.com.br/marreco") == "https://cozify.com.br/marreco/contato"


def test_parse_urls() -> None:
    raw = " bardoteste.com.br, linktr.ee/bar\nhttps://x.com.br ,, bardoteste.com.br"
    assert parse_urls(raw) == ["https://bardoteste.com.br", "https://linktr.ee/bar", "https://x.com.br"]
    assert parse_urls("") == []


def test_probe_follows_one_contact_page_politely(polite: Polite, clock: FakeClock) -> None:
    site = FakeSite(
        {
            f"{SITE}/robots.txt": "User-agent: *\nAllow: /",
            f"{SITE}/": HOME,
            f"{SITE}/fale-conosco": CONTACT,
        }
    )
    result = probe(polite(site), f"{SITE}/")
    assert result.status == "found"
    assert result.emails == [
        "contato@bardoteste.com.br",
        "reservas@bardoteste.com.br",
        "eventos@bardoteste.com.br",
        "financeiro@bardoteste.com.br",
    ]
    assert site.paths() == ["/robots.txt", "/", "/fale-conosco"]
    assert {request.headers["user-agent"].split(" ")[0] for request in site.requests} == {
        "CervejaBarataBot/1.0"
    }
    assert clock.sleeps == [3.0, 3.0]


def test_probe_respects_robots_txt(polite: Polite) -> None:
    site = FakeSite({f"{SITE}/robots.txt": "User-agent: *\nDisallow: /", f"{SITE}/": HOME})
    result = probe(polite(site), f"{SITE}/")
    assert (result.status, result.emails) == ("disallowed", [])
    assert site.paths() == ["/robots.txt"]


def test_probe_skips_a_disallowed_contact_page(polite: Polite) -> None:
    site = FakeSite({f"{SITE}/robots.txt": "User-agent: *\nDisallow: /fale", f"{SITE}/": HOME})
    result = probe(polite(site), f"{SITE}/")
    assert result.status == "found"
    assert len(result.emails) == 2
    assert "contact page skipped" in result.note
    assert "/fale-conosco" not in site.paths()


@pytest.mark.parametrize("status", [403, 429])
def test_probe_stops_when_refused(polite: Polite, status: int) -> None:
    site = FakeSite({f"{SITE}/robots.txt": "", f"{SITE}/": status})
    result = probe(polite(site), f"{SITE}/")
    assert result.status == "blocked"
    assert f"HTTP {status}" in result.note


def test_probe_stops_at_a_bot_check(polite: Polite) -> None:
    site = FakeSite({f"{SITE}/robots.txt": "", f"{SITE}/": CHALLENGE})
    result = probe(polite(site), f"{SITE}/")
    assert (result.status, result.emails, result.pages) == ("blocked", [], [])


def test_probe_reports_errors(polite: Polite) -> None:
    site = FakeSite({f"{SITE}/robots.txt": ""})
    assert probe(polite(site), f"{SITE}/missing").note == "HTTP 404"


def test_public_output_never_holds_an_address() -> None:
    results = [
        Result(
            "https://bardoteste.com.br",
            "found",
            ["contato@bardoteste.com.br"],
            ["https://bardoteste.com.br/"],
        ),
        Result("https://linktr.ee/bar", "blocked", note="HTTP 403"),
    ]
    text = report(results) + "\n".join(summary_line(result) for result in results)
    assert "@" not in text.replace("Linktree", "")
    assert summary_line(results[0]) == "https://bardoteste.com.br: found 1, 1 page"
    assert summary_line(results[1]) == "https://linktr.ee/bar: blocked, 0 pages (HTTP 403)"


def test_main_writes_the_private_results_and_the_public_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], clock: FakeClock
) -> None:
    site = FakeSite({f"{SITE}/robots.txt": "", f"{SITE}/": HOME, f"{SITE}/fale-conosco": CONTACT})

    def client(**kwargs: object) -> PoliteClient:
        return PoliteClient(transport=httpx.MockTransport(site), clock=clock, sleep=clock.sleep)

    monkeypatch.setattr(contact_probe, "PoliteClient", client)
    out, results = tmp_path / "probe", tmp_path / "private" / "contacts.json"
    assert (
        contact_probe.main(["--urls", "bardoteste.com.br", "--out", str(out), "--results", str(results)]) == 0
    )
    assert "@" not in capsys.readouterr().out
    assert "@" not in (out / "report.md").read_text()
    saved = json.loads(results.read_text())
    assert saved[0]["status"] == "found" and len(saved[0]["emails"]) == 4


def test_main_needs_urls(tmp_path: Path) -> None:
    assert (
        contact_probe.main(["--urls", " ", "--out", str(tmp_path), "--results", str(tmp_path / "r.json")])
        == 2
    )
