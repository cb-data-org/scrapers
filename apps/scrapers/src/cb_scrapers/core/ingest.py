"""Sinks: the ingest API (Bearer ingest token) or stdout for dry runs.

Requests and responses go through the models generated from packages/contracts/openapi.json, so an item that
doesn't fit the contract fails here, in the adapter's run, instead of as a 400 from the Worker.
"""

import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import TextIO

import httpx

from ..models import Catalog, IngestRequest, IngestResponse, LatestRuns, ScrapeRunReport
from .adapter import Item
from .http import USER_AGENT
from .runner import BATCH_SIZE, RunReport

log = logging.getLogger(__name__)


class IngestClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=timeout,
            transport=transport or httpx.HTTPTransport(retries=2),
        )

    def send(self, source: str, items: Sequence[Item]) -> int:
        sent = 0
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start : start + BATCH_SIZE]
            request = IngestRequest.model_validate(
                {"source": source, "items": [item.to_json() for item in batch]}
            )
            # exclude_unset keeps the drafts' explicit nulls and leaves out hashes the adapter didn't set.
            body = request.model_dump(mode="json", exclude_unset=True)
            response = self._client.post("/api/admin/ingest", json=body)
            response.raise_for_status()
            outcome = IngestResponse.model_validate(response.json())
            log.info(
                "%s: %d accepted · %d queued · %d rejected · %d already known",
                source,
                outcome.accepted,
                outcome.queued,
                outcome.rejected,
                outcome.duplicates,
            )
            sent += len(batch)
        return sent

    def report(self, report: RunReport) -> None:
        run = ScrapeRunReport.model_validate(report.to_json())
        self._client.post("/api/admin/scrape-runs", json=run.model_dump(mode="json")).raise_for_status()

    def catalog(self) -> Catalog | None:
        """The live catalog, aliases admins added included; None when the API doesn't serve it yet."""
        response = self._client.get("/api/admin/catalog")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return Catalog.model_validate(response.json())

    def last_success(self) -> dict[str, datetime]:
        response = self._client.get("/api/admin/scrape-runs/latest")
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        latest = LatestRuns.model_validate(response.json()).root
        return {name: finished for name, finished in latest.items() if finished}


class DryRunSink:
    """Prints items and the run report as JSON lines instead of sending them; nothing has run before."""

    def __init__(self, out: TextIO | None = None) -> None:
        self.out = out or sys.stdout

    def send(self, source: str, items: Sequence[Item]) -> int:
        for item in items:
            print(json.dumps({"source": source, **item.to_json()}, ensure_ascii=False), file=self.out)
        return len(items)

    def report(self, report: RunReport) -> None:
        print(json.dumps({"run": report.to_json()}, ensure_ascii=False), file=self.out)

    def catalog(self) -> Catalog | None:
        return None

    def last_success(self) -> dict[str, datetime]:
        return {}
