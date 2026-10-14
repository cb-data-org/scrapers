import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from cb_scrapers.cli import main
from cb_scrapers.core import DAILY, Adapter, BlockedError, Fetched, Item, Task, register


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


def test_names_the_blocked_adapters_for_the_workflow(
    offline_adapters: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @register
    class Walled(Adapter):
        name = "walled"
        cadence = DAILY

        def tasks(self) -> Iterable[Task]:
            return [Task("search")]

        def fetch(self, task: Task) -> Fetched:
            raise BlockedError("https://site.test/", "HTTP 429")

        def parse(self, fetched: Fetched) -> Iterable[Item]:
            return []

    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    # A block is no failure: the job goes on, and "Start the next run" reads the output.
    assert main(["run", "canned", "walled", "--dry-run"]) == 0
    assert output.read_text("utf-8") == "blocked=walled\n"
    output.unlink()
    assert main(["run", "canned", "--dry-run"]) == 0
    assert not output.exists()
