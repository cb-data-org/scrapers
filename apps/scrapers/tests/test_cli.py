import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cb_scrapers.cli import main
from cb_scrapers.core import DAILY, Adapter, BlockedError, Fetched, Item, Pace, Task, register
from cb_scrapers.core.pace import PACE_FILE


@pytest.fixture
def offline_adapters(empty_registry: None) -> None:
    """Two adapters that never touch the network."""

    @register
    class Canned(Adapter):
        name = "canned"
        cadence = DAILY

        def tasks(self) -> Iterable[Task]:
            return [Task("only")]

        def fetch(self, task: Task) -> Fetched:
            return Fetched(task, b'{"price_minor": 499}')

        def parse(self, fetched: Fetched) -> Iterable[Item]:
            yield Item("deal", "canned:1", fetched.json())

    @register
    class Crashing(Canned):
        name = "crashing"

        def tasks(self) -> Iterable[Task]:
            raise RuntimeError("boom")


def test_list(offline_adapters: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "canned\tdaily\t3s between requests",
        "crashing\tdaily\t3s between requests",
    ]


def test_dry_run_prints_items(offline_adapters: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "canned", "--dry-run"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0] == {
        "source": "canned",
        "kind": "deal",
        "source_ref": "canned:1",
        "data": {"price_minor": 499},
    }
    assert lines[1]["run"]["status"] == "ok"


def test_due_runs_everything_never_run_before(
    offline_adapters: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--due", "--dry-run"]) == 1  # "crashing" fails, so the job fails
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    runs = [line["run"] for line in lines if "run" in line]
    assert [(run["adapter"], run["status"]) for run in runs] == [("canned", "ok"), ("crashing", "failed")]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["run"], "name the adapters to run or pass --due"),
        (["run", "nope", "--dry-run"], "unknown adapter(s): nope"),
        (["run", "canned"], "set CB_API_URL and CB_INGEST_TOKEN"),
    ],
)
def test_usage_errors(
    offline_adapters: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    message: str,
) -> None:
    monkeypatch.delenv("CB_API_URL", raising=False)
    monkeypatch.delenv("CB_INGEST_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


@pytest.fixture
def paced(offline_adapters: None) -> None:
    """Two adapters with Preço da Hora's pace: one that runs, one the site refuses."""

    @register
    class Hourly(Adapter):
        name = "hourly"
        cadence = DAILY
        pace = Pace(timedelta(hours=1), timedelta(hours=6))

        def tasks(self) -> Iterable[Task]:
            return [Task("search")]

        def fetch(self, task: Task) -> Fetched:
            return Fetched(task, b"[]")

        def parse(self, fetched: Fetched) -> Iterable[Item]:
            return []

    @register
    class Walled(Hourly):
        name = "walled"

        def fetch(self, task: Task) -> Fetched:
            raise BlockedError("https://site.test/", "HTTP 429")


@pytest.fixture
def book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The pace book, in the cache folder the workflow keeps between runs."""
    monkeypatch.setenv("CB_CACHE_DIR", str(tmp_path))
    return tmp_path / PACE_FILE


def ran(capsys: pytest.CaptureFixture[str]) -> list[str]:
    """The adapters the last dry runs ran, from the run reports they printed."""
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    return [line["run"]["adapter"] for line in lines if "run" in line]


def move_back(book: Path, name: str, what: str, by: timedelta) -> None:
    saved = json.loads(book.read_text("utf-8"))
    saved[name][what] = (datetime.fromisoformat(saved[name][what]) - by).isoformat()
    book.write_text(json.dumps(saved), "utf-8")


def test_a_paced_adapter_starts_at_most_once_an_hour_however_it_is_asked(
    paced: None, book: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "hourly", "--dry-run"]) == 0
    assert ran(capsys) == ["hourly"]
    assert json.loads(book.read_text("utf-8")).keys() == {"hourly"}
    # Within the hour it is held back, named or due, and the others still run.
    assert main(["run", "hourly", "canned", "--dry-run"]) == 0
    assert ran(capsys) == ["canned"]
    move_back(book, "hourly", "started", timedelta(minutes=59))
    assert main(["run", "--due", "--dry-run"]) == 1  # "crashing" fails, as always
    assert ran(capsys) == ["canned", "crashing", "walled"]
    # An hour after the last start, it goes again.
    move_back(book, "hourly", "started", timedelta(minutes=1))
    assert main(["run", "hourly", "--dry-run"]) == 0
    assert ran(capsys) == ["hourly"]


def test_after_a_refusal_a_paced_adapter_rests_six_hours(
    paced: None, book: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    # A block is no failure: the job goes on, and the book keeps when it came.
    assert main(["run", "walled", "--dry-run"]) == 0
    assert ran(capsys) == ["walled"]
    assert json.loads(book.read_text("utf-8"))["walled"].keys() == {"started", "blocked"}
    assert "walled: refused at" in caplog.text
    # The hour has passed but not the six: neither a person nor the schedule starts it.
    move_back(book, "walled", "started", timedelta(hours=2))
    move_back(book, "walled", "blocked", timedelta(hours=5, minutes=59))
    assert main(["run", "walled", "canned", "--dry-run"]) == 0
    assert ran(capsys) == ["canned"]
    assert main(["run", "--due", "--dry-run"]) == 1
    assert "walled" not in ran(capsys)
    move_back(book, "walled", "blocked", timedelta(minutes=1))
    assert main(["run", "walled", "--dry-run"]) == 0
    assert ran(capsys) == ["walled"]
