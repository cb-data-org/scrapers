"""The request log (core/http.py): one JSON line per HTTP exchange, and never a secret in it."""

import contextlib
import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers.adapters import precodahora
from cb_scrapers.core import BlockedError, DisallowedError, PoliteClient
from cb_scrapers.core import http as http_module
from cb_scrapers.core.http import REQUEST_LOG_FIELDS, REQUEST_LOG_HOST
from tests.fakes import FakeSite

Polite = Callable[..., PoliteClient]
HOST = "precodahora.ba.gov.br"  # the one host the request log keeps
SITE = f"https://{HOST}"
ROBOTS = f"{SITE}/robots.txt"
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


@pytest.fixture
def request_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The file the test's clients log to (CB_REQUEST_LOG), in a folder that isn't there yet, as run 4242."""
    path = tmp_path / "logs" / "requests.jsonl"
    monkeypatch.setenv("CB_REQUEST_LOG", str(path))
    monkeypatch.delenv("CB_RUN_ID", raising=False)
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    return path


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("too slow", request=request)


JSON_BODY = b'{"price": 9.9, "note": "<title>not a title</title>"}'
# A title over 80 characters, with a tab, newlines, a bell and a terminal escape in it.
PAGE_BODY = (
    "<!doctype html><html><head>\n  <title>\n   Verificação\tde segurança\x07\x1b[31m "
    + "muito " * 20
    + "longo"
    "</title></head><body>Acesso negado para o seu endereço</body></html>"
).encode()
# What the line should say: the bell and the escape are dropped (not made spaces), the rest is one line.
LONG_TITLE = "Verificação de segurança[31m " + "muito " * 20 + "longo"


def every_kind_of_answer() -> FakeSite:
    return FakeSite(
        {
            ROBOTS: "User-agent: *\nAllow: /\n",
            f"{SITE}/api": lambda request: httpx.Response(
                200,
                content=JSON_BODY,
                headers={
                    "Content-Type": "application/json",
                    "X-RateLimit-Remaining": "7",
                    "RateLimit-Policy": "10;w=60",
                    "Server": "nginx",
                    "CF-Ray": "8f1c-GRU",
                    "Date": "Wed, 28 Oct 2026 12:00:00 GMT",
                    "Set-Cookie": "sid=not-kept",
                    "X-Request-Id": "not-kept",
                },
            ),
            f"{SITE}/page": lambda request: httpx.Response(
                200, content=PAGE_BODY, headers={"Content-Type": "text/html; charset=utf-8"}
            ),
            f"{SITE}/limited": lambda request: httpx.Response(
                429,
                content=b"<title>Calma</title>",
                headers={"Retry-After": "600", "Content-Type": "text/html"},
            ),
            f"{SITE}/slow": timeout,
        }
    )


def test_no_log_unless_asked_for(polite: Polite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CB_REQUEST_LOG", raising=False)
    monkeypatch.chdir(tmp_path)
    site = FakeSite({ROBOTS: "", f"{SITE}/a": "a"})
    assert polite(site).get(f"{SITE}/a").text == "a"
    assert list(tmp_path.iterdir()) == []


def test_the_host_kept_is_the_one_the_adapter_searches() -> None:
    assert REQUEST_LOG_HOST == HOST == httpx.URL(precodahora.SEARCH_URL).host


@pytest.mark.parametrize(
    "other",
    [
        "https://minhareceita.org",
        "https://sefaz.ba.gov.br",  # the same state, not the same host
        "https://notprecodahora.ba.gov.br",  # ends as ours does
    ],
)
def test_a_request_to_another_host_logs_nothing(polite: Polite, request_log: Path, other: str) -> None:
    site = FakeSite(
        {
            f"{other}/robots.txt": "",
            f"{other}/00000000000191": lambda request: httpx.Response(200, json={"cnpj": "SECRET-CNPJ"}),
            f"{other}/limited": lambda request: httpx.Response(429, headers={"Retry-After": "9"}),
            f"{other}/slow": timeout,
        }
    )
    client = polite(site, retries=0)
    # Answered as always, a refusal and a timeout raised as always, and no trace of it: not even a file.
    assert client.get(f"{other}/00000000000191", params={"fields": "x"}).json() == {"cnpj": "SECRET-CNPJ"}
    with pytest.raises(BlockedError):
        client.get(f"{other}/limited")
    with pytest.raises(httpx.ReadTimeout):
        client.get(f"{other}/slow")
    assert not request_log.exists()


def test_the_preco_da_hora_requests_still_log_among_other_hosts(polite: Polite, request_log: Path) -> None:
    other = "https://minhareceita.org"
    site = FakeSite(
        {
            ROBOTS: "",
            f"{SITE}/produtos/": lambda request: httpx.Response(200, json={"resultado": []}),
            f"{other}/robots.txt": "",
            f"{other}/00000000000191": lambda request: httpx.Response(200, json={"cnpj": "SECRET-CNPJ"}),
        }
    )
    client = polite(site)
    client.get(f"{other}/00000000000191")
    client.post(f"{SITE}/produtos/", data={"termo": "chopp"}, label="chopp @ pituba p1")
    client.get(f"{other}/00000000000191")
    assert [(line["host"], line["method"], line["path"], line["label"]) for line in lines(request_log)] == [
        (HOST, "GET", "/robots.txt", None),
        (HOST, "POST", "/produtos/", "chopp @ pituba p1"),
    ]
    assert all(list(line) == list(REQUEST_LOG_FIELDS) for line in lines(request_log))
    assert "minhareceita" not in request_log.read_text("utf-8")


def test_a_redirect_that_leaves_the_host_is_not_logged_nor_blamed_for_what_happens_there(
    polite: Polite, request_log: Path
) -> None:
    site = FakeSite(
        {
            ROBOTS: "",
            f"{SITE}/go": lambda request: httpx.Response(
                302, headers={"Location": "https://elsewhere.test/page"}
            ),
            "https://elsewhere.test/page": timeout,
        }
    )
    with pytest.raises(httpx.ReadTimeout):
        polite(site, retries=0).get(f"{SITE}/go")
    # Only the hop on our host is written, and it is no failure: the timeout was the other host's.
    assert [(line["path"], line["status"], line["kind"], line["error"]) for line in lines(request_log)] == [
        ("/robots.txt", 200, "robots", None),
        ("/go", 302, "other", None),
    ]


def test_one_line_per_exchange_with_exactly_the_fields(polite: Polite, request_log: Path) -> None:
    client = polite(every_kind_of_answer(), retries=0)
    client.get(f"{SITE}/api", params={"termo": "chopp"}, label="chopp @ pituba p1")
    client.get(f"{SITE}/page")
    with pytest.raises(BlockedError):
        client.get(f"{SITE}/limited")
    with pytest.raises(httpx.ReadTimeout):
        client.get(f"{SITE}/slow")

    found = lines(request_log)
    assert [(line["method"], line["path"], line["status"], line["kind"]) for line in found] == [
        ("GET", "/robots.txt", 200, "robots"),
        ("GET", "/api", 200, "json"),
        ("GET", "/page", 200, "html"),
        ("GET", "/limited", 429, "blocked"),
        ("GET", "/slow", None, "error"),
    ]
    assert [list(line) for line in found] == [list(REQUEST_LOG_FIELDS)] * 5
    for line in found:
        assert TIMESTAMP.match(line["ts"])
        assert (line["run"], line["host"], line["attempt"]) == ("4242", HOST, 1)
        assert isinstance(line["latency_ms"], int) and line["latency_ms"] >= 0
    robots, api, page, limited, slow = found

    # A robots.txt fetch: no label, nothing asked.
    assert (robots["label"], robots["params"], robots["bytes"]) == (
        None,
        [],
        len("User-agent: *\nAllow: /\n"),
    )
    # A JSON answer: the caller's label, the names of the parameters, the rate-limit headers and no more.
    assert (api["label"], api["params"], api["content_type"]) == (
        "chopp @ pituba p1",
        ["termo"],
        "application/json",
    )
    assert api["bytes"] == len(JSON_BODY)
    assert api["body_sha256"] == hashlib.sha256(JSON_BODY).hexdigest()[:16]
    assert api["title"] is None  # a title is for answers that aren't JSON
    assert api["rate_headers"] == {
        "x-ratelimit-remaining": "7",
        "ratelimit-policy": "10;w=60",
        "server": "nginx",
        "cf-ray": "8f1c-GRU",
        "date": "Wed, 28 Oct 2026 12:00:00 GMT",
    }
    # An HTML answer: its title on one line, 80 characters at most, without control characters.
    assert page["content_type"] == "text/html; charset=utf-8"
    assert page["title"] == LONG_TITLE[:80].rstrip()
    assert len(page["title"]) <= 80
    assert page["bytes"] == len(PAGE_BODY)
    assert page["rate_headers"] == {}
    # A refusal: how long to wait, the page's title, and what its start looks like.
    assert (limited["retry_after"], limited["title"], limited["content_type"]) == (
        "600",
        "Calma",
        "text/html",
    )
    assert limited["body_sha256"] == hashlib.sha256(b"<title>Calma</title>").hexdigest()[:16]
    # A timeout: no answer, the exception's name, and how long it took to fail.
    assert slow["error"] == "ReadTimeout"
    assert [slow[field] for field in ("content_type", "bytes", "retry_after", "title", "body_sha256")] == [
        None
    ] * 5
    assert slow["rate_headers"] == {}
    assert all(line["error"] is None for line in (robots, api, page, limited))
    assert "not a title" not in request_log.read_text("utf-8")


def test_nothing_secret_reaches_the_file(polite: Polite, request_log: Path) -> None:
    site = FakeSite(
        {
            ROBOTS: "",
            f"{SITE}/in": lambda request: httpx.Response(
                200,
                json={"secret": "SECRET-BODY"},
                headers={
                    "Set-Cookie": "sid=SECRET-SETCOOKIE",
                    "WWW-Authenticate": "Basic realm=SECRET-REALM",
                    "X-Token": "SECRET-HEADER",
                },
            ),
        }
    )
    client = polite(site)
    secrets = {
        "Authorization": "Bearer SECRET-AUTH",
        "Cookie": "sid=SECRET-COOKIE",
        "X-CSRFToken": "SECRET-CSRF",
    }
    client.get(f"{SITE}/in?utm=SECRET-QUERY", headers=secrets)
    client.get(f"{SITE}/in", params={"token": "SECRET-PARAM"}, headers=secrets)
    client.post(f"{SITE}/in", data={"termo": "SECRET-FORM", "pagina": 2}, headers=secrets)
    client.post(f"{SITE}/in", json={"key": "SECRET-JSON"}, headers=secrets)
    client.post(f"{SITE}/in", content=b"SECRET-RAW", headers=secrets)

    text = request_log.read_text("utf-8")
    assert "SECRET" not in text
    found = [line for line in lines(request_log) if line["path"] == "/in"]
    # The names of the query and form parameters, and only those; no query string in the path.
    assert [line["params"] for line in found] == [["utm"], ["token"], ["termo", "pagina"], [], []]
    assert all("?" not in line["path"] for line in found)


def test_every_attempt_of_a_retry_has_its_own_line(polite: Polite, request_log: Path) -> None:
    calls = iter([httpx.Response(503), httpx.ConnectError("reset"), httpx.Response(200, json={"ok": True})])

    def flaky(request: httpx.Request) -> httpx.Response:
        answer = next(calls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    site = FakeSite({ROBOTS: "", f"{SITE}/a": flaky})
    assert polite(site).get(f"{SITE}/a").json() == {"ok": True}
    assert [
        (line["path"], line["attempt"], line["status"], line["kind"], line["error"])
        for line in lines(request_log)
    ] == [
        ("/robots.txt", 1, 200, "robots", None),
        ("/a", 1, 503, "error", None),
        ("/a", 2, None, "error", "ConnectError"),
        ("/a", 3, 200, "json", None),
    ]


def test_a_followed_redirect_is_an_exchange_of_its_own(polite: Polite, request_log: Path) -> None:
    site = FakeSite(
        {
            ROBOTS: "",
            f"{SITE}/old": lambda request: httpx.Response(
                302,
                headers={"Location": "/new", "Content-Type": "text/html"},
                content=b"<title>Moved</title>",
            ),
            f"{SITE}/new": lambda request: httpx.Response(200, json={"ok": True}),
        }
    )
    polite(site).get(f"{SITE}/old", label="a search")
    assert [
        (line["path"], line["status"], line["kind"], line["title"], line["label"], line["attempt"])
        for line in lines(request_log)
    ] == [
        ("/robots.txt", 200, "robots", None, None, 1),
        ("/old", 302, "other", "Moved", "a search", 1),
        ("/new", 200, "json", None, "a search", 1),
    ]


@pytest.mark.parametrize("status", [403, 429])
def test_a_refusal_is_in_the_file_before_the_error_is_raised(
    polite: Polite, request_log: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    lines_when_raised: list[int] = []

    class Spy(BlockedError):
        def __init__(self, url: str, reason: str) -> None:
            lines_when_raised.append(len(lines(request_log)))
            super().__init__(url, reason)

    monkeypatch.setattr(http_module, "BlockedError", Spy)
    refused = httpx.Response(status, headers={"Retry-After": "600"})
    site = FakeSite({ROBOTS: "", f"{SITE}/a": lambda request: refused})
    with pytest.raises(BlockedError):
        polite(site).get(f"{SITE}/a")
    assert lines_when_raised == [2]  # robots.txt and the refusal were already written
    refusal = lines(request_log)[-1]
    assert (refusal["path"], refusal["status"], refusal["kind"], refusal["retry_after"]) == (
        "/a",
        status,
        "blocked",
        "600",
    )


@pytest.mark.parametrize("robots_status", [401, 403, 429, 500])
def test_a_robots_txt_that_refuses_is_in_the_file_before_the_error_is_raised(
    polite: Polite, request_log: Path, monkeypatch: pytest.MonkeyPatch, robots_status: int
) -> None:
    lines_when_raised: list[int] = []

    class Spy(DisallowedError):
        def __init__(self, *args: object) -> None:
            lines_when_raised.append(len(lines(request_log)))
            super().__init__(*args)

    monkeypatch.setattr(http_module, "DisallowedError", Spy)
    site = FakeSite({ROBOTS: robots_status, f"{SITE}/a": "never asked"})
    with pytest.raises(DisallowedError):
        polite(site).get(f"{SITE}/a")
    assert lines_when_raised == [1]
    assert [(line["path"], line["status"], line["kind"]) for line in lines(request_log)] == [
        ("/robots.txt", robots_status, "robots")
    ]


def test_a_robots_txt_that_times_out_is_in_the_file(polite: Polite, request_log: Path) -> None:
    site = FakeSite({ROBOTS: timeout, f"{SITE}/a": "never asked"})
    with pytest.raises(DisallowedError):
        polite(site).get(f"{SITE}/a")
    (line,) = lines(request_log)
    assert (line["path"], line["status"], line["kind"], line["error"]) == (
        "/robots.txt",
        None,
        "robots",
        "ReadTimeout",
    )


@pytest.mark.parametrize(
    ("status", "content_type", "kind"),
    [
        (200, "application/json", "json"),
        (200, "application/json; charset=utf-8", "json"),
        (200, "application/problem+json", "json"),
        (200, "text/html; charset=utf-8", "html"),
        (200, "text/plain", "other"),
        (200, None, "other"),
        (204, None, "other"),
        (400, "application/json", "error"),
        (404, "text/html", "error"),
        (500, "text/html", "error"),
        (401, "text/html", "blocked"),
        (403, "text/html", "blocked"),
        (429, "application/json", "blocked"),
    ],
)
def test_the_kind_of_an_answer(
    polite: Polite, request_log: Path, status: int, content_type: str | None, kind: str
) -> None:
    headers = {"Content-Type": content_type} if content_type else {}
    site = FakeSite({f"{SITE}/a": lambda request: httpx.Response(status, headers=headers, content=b"x")})
    with contextlib.suppress(httpx.HTTPStatusError, BlockedError):
        polite(site, retries=0).get(f"{SITE}/a", robots=False)
    (line,) = lines(request_log)
    assert (line["status"], line["kind"]) == (status, kind)


@pytest.mark.parametrize(
    ("body", "title"),
    [
        (b"<html><head><title>Preco</title></head>", "Preco"),
        (b"<HTML><TITLE lang=pt>Pre&ccedil;o &amp; Hora</TITLE>", "Preço & Hora"),
        (b"<title>\n  Two\n  lines\t</title>", "Two lines"),
        (b"<title>Bell\x07 and \x1b[31mescape\x7f</title>", "Bell and [31mescape"),
        (b"<title>Cut short", "Cut short"),
        (b"<title>" + b"x" * 200 + b"</title>", "x" * 80),
        (b"<title></title>", None),
        (b"<p>no title here</p>", None),
        (b" " * 4096 + b"<title>after the first 4096 bytes</title>", None),
        (b"<titles>not a title</titles>", None),
    ],
)
def test_the_title_of_a_page(polite: Polite, request_log: Path, body: bytes, title: str | None) -> None:
    site = FakeSite(
        {
            f"{SITE}/a": lambda request: httpx.Response(
                200, headers={"Content-Type": "text/html"}, content=body
            )
        }
    )
    polite(site).get(f"{SITE}/a", robots=False)
    assert lines(request_log)[-1]["title"] == title


def test_the_fingerprint_is_of_the_first_4096_bytes_only(polite: Polite, request_log: Path) -> None:
    prefix = b"<html>" + b"a" * 4090  # 4096 bytes
    pages = {"/same-a": prefix + b"x" * 1000, "/same-b": prefix + b"y" * 1000, "/other": b"b" + prefix[1:]}
    site = FakeSite(
        {
            f"{SITE}{path}": lambda request, body=body: httpx.Response(200, content=body)
            for path, body in pages.items()
        }
    )
    client = polite(site)
    for path in pages:
        client.get(f"{SITE}{path}", robots=False)
    first, second, other = lines(request_log)
    assert first["body_sha256"] == second["body_sha256"] != other["body_sha256"]
    assert (first["bytes"], second["bytes"]) == (5096, 5096)
    assert len(first["body_sha256"]) == 16


def test_lines_are_appended_to_what_the_file_holds(polite: Polite, request_log: Path) -> None:
    request_log.parent.mkdir(parents=True)
    request_log.write_text('{"earlier": true}\n', "utf-8")
    site = FakeSite({f"{SITE}/a": "a"})
    polite(site).get(f"{SITE}/a", robots=False)
    polite(site).get(f"{SITE}/a", robots=False)
    assert [next(iter(line)) for line in lines(request_log)] == ["earlier", "ts", "ts"]


def test_the_run_is_the_ci_run_id(polite: Polite, request_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = FakeSite({f"{SITE}/a": "a"})
    polite(site).get(f"{SITE}/a", robots=False)  # GITHUB_RUN_ID=4242, set by the fixture
    monkeypatch.setenv("CB_RUN_ID", "7")  # the workflow sets it too: the same number; it wins if set
    polite(site).get(f"{SITE}/a", robots=False)
    monkeypatch.delenv("CB_RUN_ID")
    monkeypatch.delenv("GITHUB_RUN_ID")
    polite(site).get(f"{SITE}/a", robots=False)  # a run by hand
    assert [line["run"] for line in lines(request_log)] == ["4242", "7", None]


def logger_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == http_module.log.name and record.levelno == logging.WARNING
    ]


def test_a_log_that_cannot_be_written_never_stops_a_request(
    polite: Polite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CB_REQUEST_LOG", str(tmp_path))  # a folder: it can't be opened for appending
    site = FakeSite({f"{SITE}/a": "ok"})
    with caplog.at_level(logging.WARNING):
        assert polite(site).get(f"{SITE}/a", robots=False).text == "ok"
    (warning,) = logger_warnings(caplog)
    assert "request log" in warning and "IsADirectoryError" in warning


def test_a_bug_in_the_log_never_stops_a_request_nor_hides_its_error(
    polite: Polite, request_log: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def bug(*args: object, **kwargs: object) -> None:
        raise RuntimeError("a bug in the log")

    site = FakeSite(
        {
            ROBOTS: "",
            f"{SITE}/fine": "fine",
            f"{SITE}/page": lambda request: httpx.Response(
                200, headers={"Content-Type": "text/html"}, content=b"<title>x</title>"
            ),
            f"{SITE}/slow": timeout,
        }
    )
    client = polite(site, retries=0)
    client.get(f"{SITE}/fine")  # robots.txt and this are written before the bugs come
    monkeypatch.setattr(http_module, "_title", bug)
    with caplog.at_level(logging.WARNING):
        assert client.get(f"{SITE}/page").status_code == 200  # the answer still comes
    assert len(logger_warnings(caplog)) == 1
    monkeypatch.setattr(http_module, "_kind", bug)
    with caplog.at_level(logging.WARNING), pytest.raises(httpx.ReadTimeout):  # the real error, not the bug
        client.get(f"{SITE}/slow")
    assert len(logger_warnings(caplog)) == 2
    # What was written before the bugs is still there; the two lines that failed are not half-written.
    assert [line["path"] for line in lines(request_log)] == ["/robots.txt", "/fine"]
