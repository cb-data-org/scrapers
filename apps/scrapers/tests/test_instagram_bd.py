import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cb_scrapers.adapters import instagram_bd
from cb_scrapers.adapters.instagram_bd import InstagramBD
from cb_scrapers.ai.workers_ai import Extraction
from cb_scrapers.core import PoliteClient
from cb_scrapers.models import DealDraft, IngestRequest
from tests.fakes import FakeSite

SITE = "https://cervejabarata.com.br"
GRAPH = f"{instagram_bd.GRAPH}/1784"
NOW = datetime.now(UTC)


def draft(**fields: Any) -> DealDraft:
    base = {
        "venue_name": None,
        "venue_ref": None,
        "beer": "Brahma",
        "brand": "Brahma",
        "format": "chopp",
        "volume_ml": 300,
        "units": 1,
        "packaging": "single",
        "on_site": None,
        "price": 5.9,
        "currency": "BRL",
        "kind": "promo",
        "open_bar_minutes": None,
        "schedules": [],
        "conditions": None,
        "service_charge_pct": None,
        "couvert": None,
        "valid_from": None,
        "valid_until": None,
        "observed_on": None,
        "confidence": 0.9,
        "notes": None,
    }
    return DealDraft.model_validate({**base, **fields})


def post(post_id: str, days_ago: int, caption: str) -> dict[str, Any]:
    when = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    return {
        "id": post_id,
        "timestamp": when,
        "caption": caption,
        "media_type": "IMAGE",
        "media_url": "https://cdn.test/1.jpg",
        "permalink": f"https://www.instagram.com/p/{post_id}/",
    }


@pytest.fixture(autouse=True)
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CB_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CB_META_TOKEN", "token")
    monkeypatch.setenv("CB_IG_USER_ID", "1784")
    monkeypatch.setenv("CB_GEMINI_KEY", "key")
    handles = tmp_path / "handles.csv"
    handles.write_text("city,venue,instagram,note\nsalvador,zaccaria-bar,zaccariabar,\n", "utf-8")
    monkeypatch.setattr(instagram_bd, "HANDLES_FILE", handles)


def site() -> FakeSite:
    venues = {
        "items": [
            {"id": 1119, "slug": "o-bar-do-bahia", "name": "O Bar do Bahia", "instagram": "obardobahia"},
            {"id": 1672, "slug": "zaccaria-bar", "name": "Zaccaria Bar", "instagram": None},
        ]
    }
    posts = {
        "obardobahia": [
            post("1", 2, "Chopp Brahma por apenas R$ 6,99 durante todo o sábado! 🍺"),
            post("2", 3, "Feliz aniversário, equipe!"),  # no beer: not read
            post("3", 40, "Chopp Brahma R$ 5,99"),  # too old
        ],
        "zaccariabar": [post("4", 1, "")],  # a flyer without words: read
    }

    def graph(request: Any) -> Any:
        import httpx

        handle = request.url.params["fields"].split("username(")[1].split(")")[0]
        media = {"data": posts[handle]}
        return httpx.Response(
            200, json={"business_discovery": {"username": handle, "name": handle, "media": media}}
        )

    return FakeSite(
        {
            f"{SITE}/robots.txt": "",
            f"{SITE}/api/v1/salvador/venues-all.json": json.dumps(venues),
            f"{SITE}/api/v1/lauro-de-freitas/venues-all.json": json.dumps({"items": []}),
            GRAPH: graph,
            "https://cdn.test/1.jpg": b"\xff\xd8jpeg",
        }
    )


def run(adapter: InstagramBD) -> list[Any]:
    items = []
    for task in adapter.tasks():
        items += list(adapter.parse(adapter.fetch(task)))
    return items


def test_recent_beer_posts_become_drafts_at_their_venue(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def extract(self: Any, caption: str, images: list[Any], venue: str, posted: Any) -> Extraction:
        calls.append((venue, caption))
        extraction = Extraction("json_prompt", "{}")
        extraction.drafts = [draft(price=6.99)] if caption else []
        return extraction

    monkeypatch.setattr(instagram_bd.Gemini, "extract_post", extract)
    adapter = InstagramBD(polite(site()))
    items = run(adapter)
    assert calls == [
        ("O Bar do Bahia", "Chopp Brahma por apenas R$ 6,99 durante todo o sábado! 🍺"),
        ("Zaccaria Bar", ""),
    ]
    assert [(i.data["venue_ref"], i.data["price"], i.source_ref) for i in items] == [
        ("venue:1119", 6.99, "instagram:1:0")
    ]
    assert items[0].data["notes"].startswith("Instagram @obardobahia https://www.instagram.com/p/1/")
    IngestRequest.model_validate({"source": "instagram_bd", "items": [i.to_json() for i in items]})
    # Each post is read once.
    again = InstagramBD(polite(site()))
    assert run(again) == [] and len(calls) == 2


def test_does_nothing_without_its_keys(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CB_GEMINI_KEY")
    assert list(InstagramBD(polite(site())).tasks()) == []
