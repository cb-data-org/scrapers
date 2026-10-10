import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from cb_scrapers.adapters.osm_venues import OVERPASS, OsmVenues, instagram_handle
from cb_scrapers.core import BlockedError, Fetched, PoliteClient, Task
from cb_scrapers.models import IngestRequest
from tests.fakes import FakeClock, FakeSite


def node(id: int, lat: float, lon: float, **tags: str) -> dict[str, Any]:
    return {"type": "node", "id": id, "lat": lat, "lon": lon, "tags": tags}


OVERPASS_RESPONSE = {
    "version": 0.6,
    "elements": [
        node(
            1,
            -13.0122,
            -38.4908,
            amenity="bar",
            name="Boteco  do Zé",
            **{
                "contact:instagram": "https://www.instagram.com/Boteco.Do_Ze/?hl=pt",
                "opening_hours": "Mo-Su 17:00-02:00",
                "addr:street": "Rua da Paciência",
                "addr:housenumber": "12",
                "outdoor_seating": "yes",
                "live_music": "yes",
                "ref:vatin": "BR12.345.678/0001-99",
            },
        ),
        {
            "type": "way",
            "id": 22,
            "center": {"lat": -13.0104, "lon": -38.5327},
            "tags": {"amenity": "pub", "name": "The Pub", "instagram": "@thepub"},
        },
        node(3, -12.9714, -38.5097, amenity="restaurant", name="Restaurante do Pelô"),
        node(4, -12.8890, -38.2968, amenity="cafe", name="Café das Vilas"),
        node(5, -13.0047, -38.4555, amenity="bar", name="Barraca do Lôro"),
        node(6, -13.0122, -38.4908, amenity="nightclub", name="Bierbaum", craft="brewery"),
        node(7, -13.0122, -38.4908, amenity="bar"),  # no name
        node(8, -12.27, -38.97, amenity="bar", name="Far away"),  # Feira de Santana
        node(9, -13.0122, -38.4908, amenity="bank", name="Banco"),
        {"type": "way", "id": 10, "tags": {"amenity": "bar", "name": "No centre"}},
    ],
}


@pytest.fixture(scope="module")
def adapter() -> OsmVenues:
    return OsmVenues(PoliteClient())


def parsed(adapter: OsmVenues) -> dict[str, dict[str, Any]]:
    fetched = Fetched(Task("overpass"), json.dumps(OVERPASS_RESPONSE).encode())
    return {item.source_ref: dict(item.data) for item in adapter.parse(fetched)}


def test_maps_osm_tags_to_venue_drafts(adapter: OsmVenues) -> None:
    venues = parsed(adapter)
    assert list(venues) == [
        "osm:node/1",
        "osm:way/22",
        "osm:node/3",
        "osm:node/4",
        "osm:node/5",
        "osm:node/6",
    ]
    assert venues["osm:node/1"] == {
        "city": "salvador",
        "name": "Boteco do Zé",
        "lat": -13.0122,
        "lng": -38.4908,
        "kind": "boteco",
        "on_site": True,
        "address": "Rua da Paciência, 12",
        "neighbourhood": "rio-vermelho",
        "osm_ref": "node/1",
        "cnpj": "12345678000199",
        "instagram": "boteco.do_ze",
        "cardapioweb_slug": None,
        "opening_hours": "Mo-Su 17:00-02:00",
        "attributes": ["musica_ao_vivo", "area_externa"],
    }
    assert (venues["osm:way/22"]["osm_ref"], venues["osm:way/22"]["kind"]) == ("way/22", "pub")
    assert venues["osm:way/22"]["neighbourhood"] == "barra"
    assert venues["osm:way/22"]["instagram"] == "thepub"
    assert (venues["osm:node/3"]["kind"], venues["osm:node/3"]["neighbourhood"]) == (
        "restaurante",
        "centro-historico",
    )
    assert (venues["osm:node/4"]["city"], venues["osm:node/4"]["kind"]) == ("lauro-de-freitas", "outro")
    assert venues["osm:node/5"]["kind"] == "barraca"
    assert venues["osm:node/6"]["kind"] == "cervejaria"


def test_every_draft_fits_the_contract(adapter: OsmVenues) -> None:
    items = [{"kind": "venue", "source_ref": ref, "data": data} for ref, data in parsed(adapter).items()]
    IngestRequest.model_validate({"source": "osm_venues", "items": items})


@pytest.mark.parametrize(
    ("value", "handle"),
    [
        ("https://instagram.com/Bar.Do_Ze", "bar.do_ze"),
        ("instagram.com/bardoze/", "bardoze"),
        ("@bardoze", "bardoze"),
        ("bardoze", "bardoze"),
        ("https://www.facebook.com/bardoze", None),
        ("", None),
    ],
)
def test_instagram_handles(value: str, handle: str | None) -> None:
    assert instagram_handle({"contact:instagram": value}) == handle


def test_queries_the_cities_bounding_box(adapter: OsmVenues) -> None:
    [task] = adapter.tasks()
    query = task.params["data"]
    assert '"amenity"~"^(bar|pub|biergarten|nightclub|restaurant|cafe)$"' in query
    assert "out center tags;" in query
    south, west, north, east = map(float, query.split('["name"](')[1].split(")")[0].split(","))
    assert south < -12.98 < north and west < -38.5 < east


def overpass_calls(site: FakeSite) -> list[str]:
    return [str(request.url) for request in site.requests if request.url.path.endswith("interpreter")]


def test_asks_a_busy_instance_again_then_the_next(
    polite: Callable[..., PoliteClient], clock: FakeClock
) -> None:
    site = FakeSite({OVERPASS[0]: 504, OVERPASS[1]: json.dumps(OVERPASS_RESPONSE)})
    adapter = OsmVenues(polite(site))
    [task] = adapter.tasks()
    assert adapter.fetch(task).json()["elements"]
    assert overpass_calls(site) == [OVERPASS[0]] * 3 + [OVERPASS[1]]
    # The busy one is asked again 20 s later each time.
    assert clock.sleeps == [3.0, 20.0, 20.0, 3.0]


def test_a_silent_instance_passes_the_query_on_at_once(polite: Callable[..., PoliteClient]) -> None:
    def silent(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    site = FakeSite({OVERPASS[0]: silent, OVERPASS[1]: json.dumps(OVERPASS_RESPONSE)})
    adapter = OsmVenues(polite(site))
    [task] = adapter.tasks()
    assert adapter.fetch(task).json()["elements"]
    assert overpass_calls(site) == list(OVERPASS[:2])


def test_reports_a_block_when_every_instance_refuses(polite: Callable[..., PoliteClient]) -> None:
    adapter = OsmVenues(polite(FakeSite(dict.fromkeys(OVERPASS, 429))))
    [task] = adapter.tasks()
    with pytest.raises(BlockedError):
        adapter.fetch(task)


def test_fails_when_every_instance_is_down(polite: Callable[..., PoliteClient]) -> None:
    adapter = OsmVenues(polite(FakeSite(dict.fromkeys(OVERPASS, 503))))
    [task] = adapter.tasks()
    with pytest.raises(RuntimeError, match="every Overpass instance failed"):
        adapter.fetch(task)


def test_nightclubs_are_not_bars(adapter: OsmVenues) -> None:
    item = adapter.venue(node(11, -13.0122, -38.4908, amenity="nightclub", name="Clube da Noite"))
    assert item is not None and item.data["kind"] == "outro"
