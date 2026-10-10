from collections.abc import Callable
from typing import Any

import httpx
import pytest

from cb_scrapers.core import PoliteClient, registry
from tests.fakes import FakeClock, FakeSite


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def polite(clock: FakeClock) -> Callable[..., PoliteClient]:
    def make(site: FakeSite, **kwargs: Any) -> PoliteClient:
        return PoliteClient(transport=httpx.MockTransport(site), clock=clock, sleep=clock.sleep, **kwargs)

    return make


@pytest.fixture
def empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_ADAPTERS", {})


@pytest.fixture(autouse=True)
def _no_stores_by_default(monkeypatch):
    """The Preço da Hora adapter lists shops too in production; tests of the bar logic run without it."""
    monkeypatch.setenv("CB_STORES", "0")
