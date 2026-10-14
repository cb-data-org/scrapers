"""Runs one adapter end to end: tasks → fetch → parse → de-duplicate → send in batches → report."""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from ..models import Catalog
from .adapter import Adapter, Item
from .catalog import load_catalog
from .http import BlockedError, PoliteClient

log = logging.getLogger(__name__)

#: The Worker accepts at most 25 items per ingest request.
BATCH_SIZE = 25
MAX_ERRORS_KEPT = 20
#: The contract caps each error message (ScrapeRunReport.errors).
MAX_ERROR_LENGTH = 1000

Status = Literal["ok", "partial", "blocked", "failed"]


@dataclass(slots=True)
class RunReport:
    adapter: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Status = "ok"
    tasks: int = 0
    fetched: int = 0
    items: int = 0
    sent: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    #: Workers AI neurons the adapter's AI client spent (the free 10,000 a day are shared with the Worker).
    ai_neurons: int = 0

    def add_error(self, message: str) -> None:
        self.error_count += 1
        if len(self.errors) < MAX_ERRORS_KEPT:
            self.errors.append(message[:MAX_ERROR_LENGTH])

    def to_json(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "tasks": self.tasks,
            "fetched": self.fetched,
            "items": self.items,
            "sent": self.sent,
            "error_count": self.error_count,
            "errors": self.errors,
            "ai_neurons": self.ai_neurons,
        }


class Sink(Protocol):
    """Where items and run reports go: the ingest API, or stdout for dry runs."""

    def send(self, source: str, items: Sequence[Item]) -> int: ...

    def report(self, report: RunReport) -> None: ...

    def last_success(self) -> dict[str, datetime]: ...

    def catalog(self) -> Catalog | None: ...


def _polite_client(delay: float) -> PoliteClient:
    return PoliteClient(delay=delay)


def run_adapter(
    adapter_cls: type[Adapter],
    sink: Sink,
    *,
    client_factory: Callable[[float], PoliteClient] = _polite_client,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    limit: int | None = None,
) -> RunReport:
    """One bad page doesn't sink the run (status "partial"); a block stops the source ("blocked"); a crash or
    a failed send stops everything ("failed"). Items found before a block are still sent. `limit` stops after
    that many tasks (a trial run). The adapter's `problems` go on the report as errors (a "partial" run)."""
    name = adapter_cls.name
    report = RunReport(adapter=name, started_at=now())
    seen: set[str] = set()
    batch: list[Item] = []
    adapter: Adapter | None = None

    def flush() -> None:
        if batch:
            report.sent += sink.send(name, list(batch))
            batch.clear()

    try:
        with client_factory(adapter_cls.delay) as http:
            adapter = adapter_cls(http)
            # Fetched per run, so aliases admins add reach the matching on the next run.
            adapter.catalog = load_catalog(sink)
            for task in adapter.tasks():
                if limit is not None and report.tasks >= limit:
                    log.info("%s: stopping after %d tasks (limit)", name, limit)
                    break
                report.tasks += 1
                try:
                    fetched = adapter.fetch(task)
                    report.fetched += 1
                    parsed = list(adapter.parse(fetched))
                except BlockedError as error:
                    report.status = "blocked"
                    report.add_error(f"{task.key}: {error}")
                    log.warning("%s: blocked (%s); stopping this source for the run", name, error)
                    break
                except Exception as error:
                    report.add_error(f"{task.key}: {type(error).__name__}: {error}")
                    log.warning("%s: task %s failed: %s", name, task.key, error)
                    continue
                for item in parsed:
                    if item.key in seen:
                        continue
                    seen.add(item.key)
                    batch.append(item)
                    if len(batch) >= BATCH_SIZE:
                        flush()
                if adapter.flush_each_task:
                    flush()
            flush()
            for problem in adapter.problems:
                report.add_error(problem)
    except Exception as error:
        report.status = "failed"
        report.add_error(f"run: {type(error).__name__}: {error}")
        log.exception("%s: run failed", name)
    else:
        if report.status == "ok" and report.error_count:
            report.status = "partial" if report.fetched else "failed"
    report.items = len(seen)
    if adapter is not None and adapter.ai is not None:
        report.ai_neurons = round(adapter.ai.spent)
    report.finished_at = now()
    try:
        sink.report(report)
    except Exception:
        log.exception("%s: could not report the run", name)
    return report


def due_adapters(
    adapters: Mapping[str, type[Adapter]], last_success: Mapping[str, datetime], now: datetime
) -> list[type[Adapter]]:
    return [cls for name, cls in adapters.items() if cls.cadence.is_due(last_success.get(name), now)]
