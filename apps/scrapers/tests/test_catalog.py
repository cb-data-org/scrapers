from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

import httpx

from cb_scrapers.core import WEEKLY, Adapter, Fetched, Item, Task
from cb_scrapers.core.catalog import committed_catalog, load_catalog
from cb_scrapers.core.ingest import IngestClient
from cb_scrapers.core.runner import RunReport, run_adapter
from cb_scrapers.models import Catalog


def test_the_committed_export_is_a_valid_catalog() -> None:
    catalog = committed_catalog()
    assert catalog.market == "br"
    assert "heiniken" in next(brand for brand in catalog.brands if brand.slug == "heineken").aliases
    assert catalog.bounds.garrafa.hard_min == 600
    assert catalog.type_by_volume_ml["600"] == "garrafa"


class Source:
    def __init__(self, result: Catalog | None = None, error: Exception | None = None) -> None:
        self.result, self.error = result, error

    def catalog(self) -> Catalog | None:
        if self.error:
            raise self.error
        return self.result


def test_prefers_the_live_catalog_and_falls_back_to_the_export() -> None:
    live = committed_catalog().model_copy(update={"brands": []})
    assert load_catalog(Source(live)).brands == []
    assert load_catalog(Source(None)) == committed_catalog()
    assert load_catalog(Source(error=httpx.ConnectError("offline"))) == committed_catalog()


def test_ingest_client_reads_the_live_catalog() -> None:
    body = committed_catalog().model_dump(mode="json")

    def api(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/admin/catalog"
        return httpx.Response(200, json=body)

    assert (
        IngestClient("https://api.test", "t", transport=httpx.MockTransport(api)).catalog()
        == committed_catalog()
    )
    missing = IngestClient(
        "https://api.test", "t", transport=httpx.MockTransport(lambda _: httpx.Response(404))
    )
    assert missing.catalog() is None


class Peek(Adapter):
    name = "peek"
    cadence = WEEKLY
    seen: ClassVar[list[Catalog | None]] = []

    def tasks(self) -> Iterable[Task]:
        Peek.seen.append(self.catalog)
        return []

    def parse(self, fetched: Fetched) -> Iterable[Item]:
        return []


class Sink:
    def send(self, source: str, items: list[Item]) -> int:
        return len(items)

    def report(self, report: RunReport) -> None: ...

    def last_success(self) -> dict[str, datetime]:
        return {}

    def catalog(self) -> Catalog | None:
        return None


def test_every_run_starts_with_a_catalog() -> None:
    report = run_adapter(Peek, Sink())
    assert report.status == "ok"
    assert Peek.seen == [committed_catalog()]
