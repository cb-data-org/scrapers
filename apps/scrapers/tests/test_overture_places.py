import json
import struct

from cb_scrapers.adapters.overture_places import OverturePlaces, place_record, point
from cb_scrapers.core import Fetched, Task

PITUBA = (-13.0010, -38.4600)


def wkb(lat: float, lng: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, lng, lat)


def place(name: str, lat: float, lng: float, **overrides: object) -> dict[str, object]:
    return {
        "id": f"08f{sum(map(ord, name)):012d}",
        "geometry": wkb(lat, lng),
        "names": {"primary": name},
        "categories": {"primary": "bar", "alternate": ["pub"]},
        "confidence": 0.95,
        "operating_status": "open",
        "addresses": [{"freeform": "Rua Piauí, 16"}],
        "socials": ["https://www.facebook.com/x", "https://www.instagram.com/Bar.Piaui/"],
        **overrides,
    }


def test_reads_points_and_keeps_only_confident_open_places_to_drink() -> None:
    assert point(wkb(*PITUBA)) == PITUBA
    assert point({"type": "Point", "coordinates": [PITUBA[1], PITUBA[0]]}) == PITUBA
    record = place_record(place("Bar Piauí", *PITUBA))
    assert record is not None
    assert (record["kind"], record["instagram"], record["address"]) == ("bar", "bar.piaui", "Rua Piauí, 16")
    assert place_record(place("Barbearia Preto", *PITUBA, categories={"primary": "barber"})) is None
    assert place_record(place("Bar Velho", *PITUBA, confidence=0.5)) is None
    assert place_record(place("Bar Fechado", *PITUBA, operating_status="permanently_closed")) is None


def run(adapter: OverturePlaces, key: str, body: object) -> list:
    return list(adapter.parse(Fetched(Task(key), json.dumps(body).encode())))


def test_new_bars_come_with_their_id_and_known_ones_fill_it_in() -> None:
    adapter = OverturePlaces(http=None)  # type: ignore[arg-type]
    ours = [
        {"name": "Zaccaria Bar", "lat": -13.0050, "lng": -38.4700, "osm_ref": "node/5"},
        {"name": "Preto Bar", "lat": -13.0020, "lng": -38.4610, "osm_ref": None},
    ]
    assert run(adapter, "ours:salvador", {"items": ours}) == []
    elements = [
        {
            "type": "node",
            "id": 7,
            "lat": -13.0030,
            "lon": -38.4650,
            "tags": {"amenity": "bar", "name": "Ponte Aérea"},
        }
    ]
    assert run(adapter, "overpass", {"elements": elements}) == []
    records = [
        place_record(place("Zaccaria Bar", -13.0051, -38.4701)),
        place_record(place("Preto Bar - Pituba", -13.0021, -38.4611)),
        place_record(place("Ponte Aérea Bar", -13.0031, -38.4651)),
        place_record(place("Bar Piauí", *PITUBA)),
        place_record(place("Bar em Recife", -8.05, -34.9)),
    ]
    items = run(adapter, "overture", records)
    assert [(item.data["name"], item.data["osm_ref"]) for item in items] == [
        ("Zaccaria Bar", "node/5"),
        ("Ponte Aérea Bar", "node/7"),
        ("Bar Piauí", None),
    ]
    assert all(item.data["overture_id"] for item in items)
    assert items[-1].data["neighbourhood"] and items[-1].source_ref.startswith("overture:")
    assert adapter.left_out == 1
