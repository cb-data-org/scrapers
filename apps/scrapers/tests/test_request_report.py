"""`cb requests report`: what the request logs say about Preço da Hora."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers.adapters import precodahora
from cb_scrapers.cli import main
from cb_scrapers.core import PoliteClient
from cb_scrapers.request_report import HOST, SEARCH_PATH, ReportError, figures, read, render, run_report
from tests.fakes import FakeSite

Polite = Callable[..., PoliteClient]
DAY = datetime(2026, 10, 28, 9, tzinfo=UTC)


def at(hour: int, minute: int = 0, second: float = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute) + timedelta(seconds=second)


def entry(moment: datetime, method: str, path: str, **fields: Any) -> dict[str, Any]:
    """A line of the log, as the client writes it, with what the report doesn't read left plain."""
    return {
        "ts": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "run": "100",
        "host": HOST,
        "method": method,
        "path": path,
        "params": [],
        "label": None,
        "attempt": 1,
        "status": 200,
        "kind": "json",
        "content_type": None,
        "bytes": 10,
        "latency_ms": 100,
        "retry_after": None,
        "rate_headers": {},
        "title": None,
        "body_sha256": None,
        "error": None,
        **fields,
    }


def session(moment: datetime, run: str) -> dict[str, Any]:
    """The page a run opens its session with: a GET of the search address that answers HTML, as it should."""
    return entry(
        moment, "GET", SEARCH_PATH, kind="html", run=run, content_type="text/html", title="Preço da Hora"
    )


def robots(moment: datetime, run: str, **fields: Any) -> dict[str, Any]:
    return entry(moment, "GET", "/robots.txt", kind="robots", run=run, **fields)


def search(moment: datetime, run: str, **fields: Any) -> dict[str, Any]:
    return entry(moment, "POST", SEARCH_PATH, run=run, **fields)


def synthetic_log() -> list[dict[str, Any]]:
    """Five runs, 32 requests to the site and 3 to others (35 lines)."""
    rows = [robots(at(9), "100"), session(at(9, 0, 20), "100")]
    rows += [search(at(9, 0, 40 + 20 * k), "100") for k in range(12)]  # a search every 20 s
    rows += [robots(at(10), "200"), session(at(10, 0, 2), "200")]
    rows += [search(at(10, 0, 5 + 5 * k), "200") for k in range(7)]  # a burst, one every 5 s
    rows.append(
        search(
            at(10, 0, 40),
            "200",
            status=429,
            kind="blocked",
            content_type="text/html",
            retry_after="600",
            title="Too Many Requests",
            body_sha256="deadbeefdeadbeef",
        )
    )
    # A search answered with a page instead of JSON.
    rows += [robots(at(12), "300"), session(at(12, 0, 2), "300"), search(at(12, 0, 10), "300")]
    rows.append(
        search(
            at(12, 0, 30),
            "300",
            kind="html",
            content_type="text/html",
            title="Verificação de segurança",
            body_sha256="0123456789abcdef",
        )
    )
    # A robots.txt that says no (and when to come back), then a run whose search times out.
    rows.append(
        robots(
            at(13),
            "400",
            status=403,
            retry_after="120",
            content_type="text/html",
            title="Forbidden",
            body_sha256="feedfacefeedface",
        )
    )
    rows += [robots(at(14), "500"), session(at(14, 0, 2), "500")]
    rows.append(search(at(14, 0, 5), "500", status=None, kind="error", error="ReadTimeout"))
    # Other hosts: none of the report's business, a 429 and a Retry-After among them.
    register = {"host": "minhareceita.org", "path": "/00000000000191"}
    rows.append(entry(at(10, 0, 1), "GET", status=429, kind="blocked", retry_after="9", **register))
    rows.append(entry(at(10, 0, 3), "GET", **register))
    rows.append(session(at(11), "x") | {"host": "example.org", "title": "Elsewhere"})
    return rows


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    """What `gh run download` leaves: a folder per artifact, the lines in reverse order of time across the
    two, two lines that aren't log lines, and a file that isn't a log."""
    shuffled = [json.dumps(row) for row in reversed(synthetic_log())]
    folder = tmp_path / "downloads"
    for number, rows in enumerate(
        ([*shuffled[::2], "not json at all"], [*shuffled[1::2], '{"ts": "yesterday"}', ""])
    ):
        artifact = folder / f"precodahora-requests-{number}"
        artifact.mkdir(parents=True)
        (artifact / "requests.jsonl").write_text("\n".join(rows) + "\n", "utf-8")
    (folder / "notes.txt").write_text("not a log", "utf-8")
    return folder


def test_the_report_finds_the_busiest_windows_the_refusals_and_the_retry_afters(logs: Path) -> None:
    data = figures(read([logs]))
    assert (data["files"], data["lines"], data["unreadable_lines"], data["requests"]) == (2, 37, 2, 32)
    assert (data["first"], data["last"]) == ("2026-10-28T09:00:00.000Z", "2026-10-28T14:00:05.000Z")

    # The most requests in any minute, ten minutes and hour, and when it began: the burst of run 200 (robots,
    # the session page, seven searches and a 429); run 100 whole; run 100's searches with all of run 200.
    assert [(w["window_seconds"], w["requests"], w["first"], w["last"]) for w in data["busiest"]] == [
        (60, 10, "2026-10-28T10:00:00.000Z", "2026-10-28T10:00:40.000Z"),
        (600, 14, "2026-10-28T09:00:00.000Z", "2026-10-28T09:04:20.000Z"),
        (3600, 21, "2026-10-28T09:00:40.000Z", "2026-10-28T10:00:35.000Z"),
    ]

    # Each refusal, in order of time, with the requests before it: the session pages (HTML GETs) aren't any.
    assert [
        (r["ts"], r["status"], r["title"], r["requests_60_minutes_before"], r["requests_24_hours_before"])
        for r in data["refusals"]
    ] == [
        ("2026-10-28T10:00:40.000Z", 429, "Too Many Requests", 21, 23),
        ("2026-10-28T12:00:30.000Z", 200, "Verificação de segurança", 3, 27),
        ("2026-10-28T13:00:00.000Z", 403, "Forbidden", 4, 28),  # robots.txt exactly an hour after run 300's
        ("2026-10-28T14:00:05.000Z", None, None, 2, 31),
    ]
    first, html, forbidden, timed_out = data["refusals"]
    assert (first["content_type"], first["body_sha256"], first["retry_after"]) == (
        "text/html",
        "deadbeefdeadbeef",
        "600",
    )
    assert (html["method"], html["path"], html["body_sha256"]) == ("POST", "/produtos/", "0123456789abcdef")
    assert (forbidden["kind"], forbidden["path"]) == ("robots", "/robots.txt")
    assert (timed_out["error"], timed_out["run"]) == ("ReadTimeout", "500")

    assert data["search_gaps"] == {"searches": 23, "median_seconds": 20.0, "smallest_seconds": 5.0}
    # Every Retry-After of the site, not the other host's (9).
    assert [(a["value"], a["count"], a["first"], a["statuses"]) for a in data["retry_after"]] == [
        ("600", 1, "2026-10-28T10:00:40.000Z", [429]),
        ("120", 1, "2026-10-28T13:00:00.000Z", [403]),
    ]


def test_the_report_counts_what_came_back(logs: Path) -> None:
    data = figures(read([logs]))
    assert [(run["run"], run["first"], run["last"], run["requests"]) for run in data["runs"]] == [
        ("100", "2026-10-28T09:00:00.000Z", "2026-10-28T09:04:20.000Z", 14),
        ("200", "2026-10-28T10:00:00.000Z", "2026-10-28T10:00:40.000Z", 10),
        ("300", "2026-10-28T12:00:00.000Z", "2026-10-28T12:00:30.000Z", 4),
        ("400", "2026-10-28T13:00:00.000Z", "2026-10-28T13:00:00.000Z", 1),
        ("500", "2026-10-28T14:00:00.000Z", "2026-10-28T14:00:05.000Z", 3),
    ]
    assert list(data["by_kind"].items()) == [
        ("json", 20),
        ("html", 5),
        ("robots", 5),
        ("blocked", 1),
        ("error", 1),
    ]
    assert list(data["by_status"].items()) == [("200", 29), ("403", 1), ("429", 1), ("none", 1)]
    assert [(t["title"], t["count"]) for t in data["titles"]] == [
        ("Preço da Hora", 4),
        ("Forbidden", 1),
        ("Too Many Requests", 1),
        ("Verificação de segurança", 1),
    ]


def test_the_text_report_words_the_same_figures(logs: Path) -> None:
    text = run_report([logs])
    assert text == render(figures(read([logs])))
    assert "\x1b" not in text  # no colours
    for expected in (
        "Read 2 file(s): 37 lines, 2 unreadable; 32 are requests to precodahora.ba.gov.br.",
        "Runs (5)",
        "Refusals (4): a 401, 403 or 429, an HTML answer to a search, no answer",
        '2026-10-28T10:00:40.000Z  429  POST /produtos/  text/html  "Too Many Requests"  '
        "sha256 deadbeefdeadbeef  Retry-After 600",
        "requests before it: 21 in 60 minutes, 23 in 24 hours (run 200)",
        "2026-10-28T14:00:05.000Z  no answer (ReadTimeout)  POST /produtos/",
        "1 minute    10 requests, from 2026-10-28T10:00:00.000Z to 2026-10-28T10:00:40.000Z",
        "10 minutes  14 requests, from 2026-10-28T09:00:00.000Z to 2026-10-28T09:04:20.000Z",
        "60 minutes  21 requests, from 2026-10-28T09:00:40.000Z to 2026-10-28T10:00:35.000Z",
        "Gaps between searches (POST /produtos/): 23 searches, median 20 s, smallest 5 s",
        "600  x1, first at 2026-10-28T10:00:40.000Z, status 429",
        "120  x1, first at 2026-10-28T13:00:00.000Z, status 403",
        "     4  Preço da Hora",
    ):
        assert expected in text, expected


def test_the_json_flag_prints_the_same_figures_as_one_object(
    logs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["requests", "report", "--json", str(logs)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == figures(read([logs]))
    assert (
        main(
            ["requests", "report", str(logs / "precodahora-requests-0"), str(logs / "precodahora-requests-1")]
        )
        == 0
    )
    assert capsys.readouterr().out.rstrip("\n") == run_report([logs])


def test_the_files_and_folders_named_are_read_once_each(logs: Path) -> None:
    one = logs / "precodahora-requests-0" / "requests.jsonl"
    assert read([logs, logs, one]).files == 2


def test_nothing_to_the_site(tmp_path: Path) -> None:
    file = tmp_path / "other.jsonl"
    file.write_text(json.dumps(entry(at(9), "GET", "/x", host="example.org")) + "\n", "utf-8")
    assert run_report([file]).splitlines() == [
        "Requests to precodahora.ba.gov.br",
        "Read 1 file(s): 1 lines, 0 unreadable; 0 are requests to precodahora.ba.gov.br.",
    ]
    assert json.loads(run_report([file], as_json=True))["requests"] == 0


@pytest.mark.parametrize(
    ("path", "message"),
    [("nowhere.jsonl", "no such file or folder: nowhere.jsonl"), (".", "no .jsonl files in .")],
)
def test_a_path_with_no_log_is_a_usage_error(
    path: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)  # a folder without a .jsonl in it
    with pytest.raises(SystemExit) as exit_info:
        main(["requests", "report", path])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


def test_a_path_that_isnt_there_says_so(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="no such file or folder"):
        read([tmp_path / "nowhere.jsonl"])
    with pytest.raises(ReportError, match=r"no \.jsonl files"):
        read([tmp_path])


def test_the_report_reads_the_search_address_the_adapter_searches() -> None:
    assert f"https://{HOST}{SEARCH_PATH}" == precodahora.SEARCH_URL


def test_the_report_reads_what_the_client_writes(
    polite: Polite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writer and reader agree on the format: a run through the real client, then the report on its file."""
    log = tmp_path / "requests.jsonl"
    monkeypatch.setenv("CB_REQUEST_LOG", str(log))
    monkeypatch.setenv("GITHUB_RUN_ID", "31337")
    site = FakeSite(
        {
            f"https://{HOST}/robots.txt": "",
            f"https://{HOST}{SEARCH_PATH}": lambda request: (
                httpx.Response(
                    200, headers={"Content-Type": "text/html"}, text="<title>Preço da Hora</title>"
                )
                if request.method == "GET"
                else httpx.Response(200, json={"resultado": []})
            ),
        }
    )
    client = polite(site, retries=0)
    client.get(f"https://{HOST}{SEARCH_PATH}", label="brahma @ grid-2/4 p1")
    for page in (1, 2):
        client.post(f"https://{HOST}{SEARCH_PATH}", data={"termo": "brahma", "pagina": page}, retries=0)
    data = figures(read([log]))
    assert data["requests"] == 4
    assert data["runs"][0]["run"] == "31337"
    assert dict(data["by_kind"]) == {"json": 2, "html": 1, "robots": 1}
    assert data["refusals"] == []  # the session page is HTML, but it is a GET
    assert data["titles"] == [{"title": "Preço da Hora", "count": 1}]
    assert data["search_gaps"]["searches"] == 2
