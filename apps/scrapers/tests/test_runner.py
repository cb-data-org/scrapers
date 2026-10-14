import io
import json
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from cb_scrapers.core import (
    DAILY,
    MONTHLY,
    TWICE_DAILY,
    WEEKLY,
    Adapter,
    Cadence,
    Fetched,
    Item,
    PoliteClient,
    Task,
)
from cb_scrapers.core.ingest import DryRunSink, IngestClient
from cb_scrapers.core.runner import RunReport, due_adapters, run_adapter
from tests.fakes import FakeSite, Route

Polite = Callable[..., PoliteClient]
NOW = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


def bar(**pages: Route) -> FakeSite:
    """bar.test with an empty robots.txt and /<name>.json pages."""
    return FakeSite(
        {
            "https://bar.test/robots.txt": "",
            **{f"https://bar.test/{name}.json": body for name, body in pages.items()},
        }
    )


def deals(*ids: int) -> str:
    return json.dumps([{"id": id_, "price_minor": 500 + id_} for id_ in ids])


class Menus(Adapter):
    """Reads JSON menus from bar.test; each entry becomes one deal item."""

    name = "menus"
    cadence = DAILY
    pages = ("a", "b")

    def tasks(self) -> Iterable[Task]:
        return [Task(page, f"https://bar.test/{page}.json") for page in self.pages]

    def parse(self, fetched: Fetched) -> Iterable[Item]:
        for deal in fetched.json():
            yield Item("deal", f"menu:{deal['id']}", deal, obs_hash=f"obs-{deal['id']}")


class RecordingSink:
    def __init__(self, fail_send: bool = False) -> None:
        self.batches: list[list[str]] = []
        self.reports: list[RunReport] = []
        self.fail_send = fail_send

    def send(self, source: str, items: Sequence[Item]) -> int:
        if self.fail_send:
            raise httpx.ConnectError("ingest API down")
        self.batches.append([item.source_ref for item in items])
        return len(items)

    def report(self, report: RunReport) -> None:
        self.reports.append(report)

    def catalog(self) -> None:
        return None

    def last_success(self) -> dict[str, datetime]:
        return {}


def run(site: FakeSite, polite: Polite, sink: RecordingSink, adapter: type[Adapter] = Menus) -> RunReport:
    return run_adapter(adapter, sink, client_factory=lambda delay: polite(site, delay=delay), now=lambda: NOW)


def test_runs_tasks_deduplicates_and_reports(polite: Polite) -> None:
    site = bar(a=deals(1, 2), b=deals(2, 3))
    sink = RecordingSink()
    report = run(site, polite, sink)
    assert sink.batches == [["menu:1", "menu:2", "menu:3"]]
    assert (report.status, report.tasks, report.fetched, report.items, report.sent) == ("ok", 2, 2, 3, 3)
    assert sink.reports == [report]
    assert report.finished_at == NOW


def test_the_ai_clients_neurons_go_on_the_run_report(polite: Polite) -> None:
    class Counter:
        spent = 0.0

    class ReadsWithAI(Menus):
        def __init__(self, http: PoliteClient) -> None:
            super().__init__(http)
            self.ai = Counter()

        def parse(self, fetched: Fetched) -> Iterable[Item]:
            assert self.ai is not None
            self.ai.spent += 22.7
            return super().parse(fetched)

    sink = RecordingSink()
    report = run(bar(a=deals(1), b=deals(2)), polite, sink, ReadsWithAI)
    assert report.ai_neurons == 45
    assert report.to_json()["ai_neurons"] == 45
    assert run(bar(a=deals(1), b=deals(2)), polite, RecordingSink()).ai_neurons == 0


def test_a_limit_stops_after_that_many_tasks(polite: Polite) -> None:
    sink = RecordingSink()
    report = run_adapter(
        Menus,
        sink,
        client_factory=lambda delay: polite(bar(a=deals(1), b=deals(2)), delay=delay),
        now=lambda: NOW,
        limit=1,
    )
    assert sink.batches == [["menu:1"]]
    assert (report.status, report.tasks, report.fetched) == ("ok", 1, 1)


def test_sends_in_batches_of_25(polite: Polite) -> None:
    site = bar(a=deals(*range(60)), b="[]")
    sink = RecordingSink()
    report = run(site, polite, sink)
    assert [len(batch) for batch in sink.batches] == [25, 25, 10]
    assert report.sent == 60


def test_sends_each_tasks_items_before_the_next_task_when_the_adapter_saves_per_task(polite: Polite) -> None:
    sent_before: list[list[list[str]]] = []

    class SavesPerTask(Menus):
        flush_each_task = True

        def tasks(self) -> Iterable[Task]:
            for task in super().tasks():
                yield task
                sent_before.append([list(batch) for batch in sink.batches])

    sink = RecordingSink()
    report = run(bar(a=deals(1, 2), b=deals(3)), polite, sink, SavesPerTask)
    assert sink.batches == [["menu:1", "menu:2"], ["menu:3"]]
    # Back in tasks() after a task, its items have gone out.
    assert sent_before == [[["menu:1", "menu:2"]], [["menu:1", "menu:2"], ["menu:3"]]]
    assert report.status == "ok"


def test_the_adapters_problems_make_the_run_partial(polite: Polite) -> None:
    class GaveUp(Menus):
        def tasks(self) -> Iterable[Task]:
            yield from super().tasks()
            self.problems.append("the model gave up; the rest waits for the next run")

    report = run(bar(a=deals(1), b=deals(2)), polite, RecordingSink(), GaveUp)
    assert report.status == "partial"
    assert report.errors == ["the model gave up; the rest waits for the next run"]


def test_a_bad_page_makes_the_run_partial(polite: Polite) -> None:
    site = bar(a="not json", b=deals(7))
    sink = RecordingSink()
    report = run(site, polite, sink)
    assert report.status == "partial"
    assert report.errors[0].startswith("a: JSONDecodeError")
    assert sink.batches == [["menu:7"]]


def test_a_block_stops_the_source_but_keeps_what_was_found(polite: Polite) -> None:
    class ThreePages(Menus):
        pages = ("a", "b", "c")

    site = bar(a=deals(1), b=429, c=deals(3))
    sink = RecordingSink()
    report = run(site, polite, sink, ThreePages)
    assert report.status == "blocked"
    assert "/c.json" not in site.paths()
    assert sink.batches == [["menu:1"]]


def test_nothing_fetched_is_a_failure(polite: Polite) -> None:
    site = bar(a=404, b=404)
    report = run(site, polite, RecordingSink())
    assert (report.status, report.error_count) == ("failed", 2)


def test_a_crash_while_listing_tasks_is_a_failure(polite: Polite) -> None:
    class Broken(Menus):
        def tasks(self) -> Iterable[Task]:
            raise RuntimeError("config missing")

    report = run(FakeSite({}), polite, RecordingSink(), Broken)
    assert report.status == "failed"
    assert report.errors == ["run: RuntimeError: config missing"]


def test_a_failed_send_fails_the_run_and_still_reports(polite: Polite) -> None:
    site = bar(a=deals(1), b="[]")
    sink = RecordingSink(fail_send=True)
    report = run(site, polite, sink)
    assert report.status == "failed"
    assert sink.reports == [report]


def test_keeps_only_the_first_errors(polite: Polite) -> None:
    class ManyPages(Menus):
        pages = tuple(f"p{n}" for n in range(30))

    site = bar()
    report = run(site, polite, RecordingSink(), ManyPages)
    assert (report.error_count, len(report.errors)) == (30, 20)


@pytest.mark.parametrize(
    ("cadence", "since_last", "due"),
    [
        (DAILY, None, True),
        (DAILY, timedelta(hours=2), False),
        (DAILY, timedelta(hours=21), True),  # GitHub started yesterday's run 3 h late
        (TWICE_DAILY, timedelta(hours=3, minutes=30), True),
        (TWICE_DAILY, timedelta(hours=2), False),
        (WEEKLY, timedelta(days=6), False),
        (WEEKLY, timedelta(days=6, hours=13), True),
        (MONTHLY, timedelta(days=27), True),
        (MONTHLY, timedelta(days=20), False),
    ],
)
def test_cadence(cadence: Cadence, since_last: timedelta | None, due: bool) -> None:
    last = None if since_last is None else NOW - since_last
    assert cadence.is_due(last, NOW) is due


def test_due_adapters_uses_the_last_successful_run() -> None:
    class Weekly(Menus):
        name = "weekly"
        cadence = WEEKLY

    adapters = {"menus": Menus, "weekly": Weekly}
    last = {"menus": NOW - timedelta(hours=1), "weekly": NOW - timedelta(days=8)}
    assert due_adapters(adapters, last, NOW) == [Weekly]
    assert due_adapters(adapters, {}, NOW) == [Menus, Weekly]


def venue_data(n: int) -> dict[str, Any]:
    """A VenueDraft as an adapter builds it: every field present, null when unknown."""
    return {
        "city": "salvador",
        "name": f"Bar {n}",
        "lat": -13.0,
        "lng": -38.5,
        "kind": "bar",
        "on_site": True,
        "address": None,
        "neighbourhood": "Rio Vermelho",
        "osm_ref": f"node/{n + 1}",
        "cnpj": None,
        "instagram": None,
        "cardapioweb_slug": None,
        "opening_hours": None,
        "attributes": [],
    }


OUTCOME = {"accepted": 1, "queued": 0, "rejected": 0, "duplicates": 0}


def test_ingest_client_batches_and_authenticates() -> None:
    received: list[dict[str, Any]] = []

    def api(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret-token"
        received.append(json.loads(request.content))
        return httpx.Response(200, json=OUTCOME)

    client = IngestClient("https://api.test/", "secret-token", transport=httpx.MockTransport(api))
    items = [Item("venue", f"osm:node/{n + 1}", venue_data(n)) for n in range(30)]
    items[0] = Item("venue", "osm:node/1", venue_data(0), listing_hash="a" * 32)
    assert client.send("osm_venues", items) == 30
    assert [len(payload["items"]) for payload in received] == [25, 5]
    assert received[0]["source"] == "osm_venues"
    # Explicit nulls stay (drafts list every field); hashes appear only when the adapter set them.
    assert received[0]["items"][0] == {
        "kind": "venue",
        "source_ref": "osm:node/1",
        "data": venue_data(0),
        "listing_hash": "a" * 32,
    }
    assert "listing_hash" not in received[0]["items"][1]


def test_ingest_client_refuses_items_outside_the_contract() -> None:
    calls: list[httpx.Request] = []
    client = IngestClient(
        "https://api.test",
        "t",
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)),
    )
    with pytest.raises(ValidationError):
        client.send("osm_venues", [Item("venue", "osm:node/1", {**venue_data(0), "lat": 120})])
    assert calls == []


def test_ingest_client_reports_runs_in_the_contract_shape() -> None:
    received: list[dict[str, Any]] = []

    def api(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    report = RunReport(adapter="osm_venues", started_at=datetime(2026, 10, 4, 16, 6, 25, 538835, tzinfo=UTC))
    report.add_error("x" * 5000)
    IngestClient("https://api.test", "t", transport=httpx.MockTransport(api)).report(report)
    assert received[0]["started_at"].startswith("2026-10-04T16:06:25.538835")
    assert received[0]["finished_at"] is None
    assert len(received[0]["errors"][0]) == 1000
    assert received[0]["ai_neurons"] == 0


def test_ingest_client_reads_last_successful_runs() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/admin/scrape-runs/latest"
        return httpx.Response(200, json={"menus": "2026-10-09T09:00:00+00:00", "weekly": None})

    client = IngestClient("https://api.test", "t", transport=httpx.MockTransport(api))
    assert client.last_success() == {"menus": datetime(2026, 10, 9, 9, 0, tzinfo=UTC)}


def test_ingest_client_treats_a_missing_endpoint_as_never_run() -> None:
    client = IngestClient(
        "https://api.test", "t", transport=httpx.MockTransport(lambda _: httpx.Response(404))
    )
    assert client.last_success() == {}


def test_dry_run_prints_json_lines() -> None:
    out = io.StringIO()
    sink = DryRunSink(out)
    sink.send("menus", [Item("deal", "menu:1", {"price_minor": 499}, obs_hash="abc")])
    sink.report(RunReport(adapter="menus", started_at=NOW, finished_at=NOW))
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first == {
        "source": "menus",
        "kind": "deal",
        "source_ref": "menu:1",
        "data": {"price_minor": 499},
        "obs_hash": "abc",
    }
    assert second["run"]["adapter"] == "menus"
