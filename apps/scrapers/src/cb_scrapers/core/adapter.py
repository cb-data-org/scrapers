"""The contract every source plugs into: list tasks, fetch each one politely, parse it into items."""

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, Literal

from ..models import Catalog
from .hashing import content_hash
from .http import PoliteClient


@dataclass(frozen=True, slots=True)
class Cadence:
    """How often a source runs. A run is due once `every - slack` passed since the last good run; the slack
    absorbs GitHub starting scheduled workflows late."""

    every: timedelta
    slack: timedelta
    label: str = ""

    def is_due(self, last_success: datetime | None, now: datetime) -> bool:
        return last_success is None or now - last_success >= self.every - self.slack


DAILY = Cadence(timedelta(days=1), timedelta(hours=3), "daily")
#: For sources checked late morning and afternoon (e.g. 11:00 and 16:00).
TWICE_DAILY = Cadence(timedelta(hours=4), timedelta(hours=1), "twice a day")
WEEKLY = Cadence(timedelta(days=7), timedelta(hours=12), "weekly")
MONTHLY = Cadence(timedelta(days=28), timedelta(days=1), "monthly")


@dataclass(frozen=True, slots=True)
class Task:
    """One unit of fetching, such as one menu or one search. `key` names it in logs and errors."""

    key: str
    url: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Fetched:
    task: Task
    body: bytes
    content_type: str = ""

    @property
    def content_hash(self) -> str:
        return content_hash(self.body)

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(frozen=True, slots=True)
class Item:
    """Something for the ingest API. `data` is a VenueDraft or DealDraft as a dict; the ingest client
    validates it against the contract models before sending."""

    kind: Literal["deal", "venue"]
    source_ref: str
    data: Mapping[str, Any]
    listing_hash: str | None = None
    obs_hash: str | None = None

    @property
    def key(self) -> str:
        """Identity for de-duplication within a run."""
        return self.obs_hash or self.listing_hash or f"{self.kind}:{self.source_ref}"

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "source_ref": self.source_ref, "data": dict(self.data)}
        if self.listing_hash:
            payload["listing_hash"] = self.listing_hash
        if self.obs_hash:
            payload["obs_hash"] = self.obs_hash
        return payload


class Adapter(ABC):
    """A source. Set `name` and `cadence`, implement `tasks` and `parse`, and decorate the class with
    `@register`. Override `fetch` for anything beyond a GET (sessions, CSRF tokens, POST searches).

    Raise `BlockedError` from `fetch` or `parse` on a CAPTCHA or any other refusal: the run stops this source
    and reports it. Never work around a block.
    """

    name: ClassVar[str]
    cadence: ClassVar[Cadence]
    #: Minimum seconds between two requests to the same host; a larger robots.txt Crawl-delay wins.
    delay: ClassVar[float] = 3.0
    #: Brands, beers, formats and bounds to match against; the runner sets it before the first task.
    catalog: Catalog | None = None

    def __init__(self, http: PoliteClient) -> None:
        self.http = http

    @abstractmethod
    def tasks(self) -> Iterable[Task]: ...

    def fetch(self, task: Task) -> Fetched:
        response = self.http.get(task.url, params=dict(task.params))
        return Fetched(task, response.content, response.headers.get("content-type", ""))

    @abstractmethod
    def parse(self, fetched: Fetched) -> Iterable[Item]: ...
