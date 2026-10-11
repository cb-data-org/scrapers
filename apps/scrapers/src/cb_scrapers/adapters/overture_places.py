"""Overture Maps' bars in every covered city, weekly (owner, 11 Oct 2026: OpenStreetMap misses many of the
city's best-known bars; Overture's places, built mostly from Meta's business pages, list 7 of 8 we checked).

Overture publishes open data (places: CDLA Permissive 2.0) as Parquet files in public cloud buckets; the
`overturemaps` package reads the latest release for a bounding box. Only places to drink are kept (bar, pub,
brewery and the like, at a confidence of CONFIDENCE or more, not closed), each in a covered bairro.

To avoid a second pin for a bar we already list, each place is matched by name and distance against our own
venues (the public venues-all.json) and OpenStreetMap's: a match sends the place with that venue's key, so
ingest fills in the Overture id instead of adding a pin, and a match with neither key is left out. The rest
become venues with their Overture id (ingest creates them, as it does OpenStreetMap's).
"""

from __future__ import annotations

import json
import logging
import re
import struct
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from ..core import WEEKLY, Adapter, Fetched, Item, PoliteClient, Task, register
from ..geo import BairroIndex
from ..matching import Candidate, best_match
from .osm_venues import OsmVenues, instagram_handle

log = logging.getLogger(__name__)

SITE = "https://cervejabarata.com.br"
#: Overture's estimate that the place exists; below it, old or doubtful listings.
CONFIDENCE = 0.8
#: Overture categories of places to drink, as our venue kinds.
CATEGORY_KINDS = {
    "bar": "bar",
    "beer_bar": "bar",
    "sports_bar": "bar",
    "dive_bar": "bar",
    "cocktail_bar": "bar",
    "wine_bar": "bar",
    "lounge": "bar",
    "beer_garden": "bar",
    "pub": "pub",
    "irish_pub": "pub",
    "gastropub": "pub",
    "brewery": "cervejaria",
    "brewpub": "cervejaria",
    "bar_and_grill_restaurant": "restaurante",
}
#: The same place twice: names alike within this distance.
MATCH_M = 150.0
_WORDS = {
    "barraca": re.compile(r"\bbarraca\b", re.I),
    "boteco": re.compile(r"\b(boteco|buteco|botequim)\b", re.I),
}


def point(geometry: Any) -> tuple[float, float] | None:
    """(lat, lng) of a WKB point, or of a GeoJSON one."""
    if isinstance(geometry, Mapping):
        coordinates = geometry.get("coordinates") or []
        return (float(coordinates[1]), float(coordinates[0])) if len(coordinates) >= 2 else None
    if isinstance(geometry, bytes | bytearray) and len(geometry) >= 21:
        order = "<" if geometry[0] == 1 else ">"
        (kind,) = struct.unpack(f"{order}I", geometry[1:5])
        if kind % 1000 != 1:
            return None
        lng, lat = struct.unpack(f"{order}dd", geometry[5:21])
        return lat, lng
    return None


def category(place: Mapping[str, Any]) -> str | None:
    categories = place.get("categories") or {}
    primary = categories.get("primary") if isinstance(categories, Mapping) else None
    return primary or place.get("basic_category")


def place_record(place: Mapping[str, Any]) -> dict[str, Any] | None:
    """What we keep of an Overture place to drink (plain JSON), or None."""
    kind = CATEGORY_KINDS.get(str(category(place)))
    names = place.get("names") or {}
    name = " ".join(str(names.get("primary") or "").split()) if isinstance(names, Mapping) else ""
    located = point(place.get("geometry"))
    confidence = float(place.get("confidence") or 0)
    if not kind or not name or located is None or confidence < CONFIDENCE:
        return None
    if str(place.get("operating_status") or "open") != "open":
        return None
    addresses = place.get("addresses") or []
    address = (addresses[0] or {}).get("freeform") if addresses else None
    socials = [str(url) for url in place.get("socials") or [] if "instagram" in str(url)]
    return {
        "id": str(place["id"]),
        "name": name,
        "lat": located[0],
        "lng": located[1],
        "kind": kind,
        "address": address,
        "instagram": instagram_handle({"instagram": socials[0]}) if socials else None,
        "confidence": round(confidence, 3),
    }


@register
class OverturePlaces(Adapter):
    name = "overture_places"
    cadence = WEEKLY
    delay = 3.0

    def __init__(self, http: PoliteClient, bairros: BairroIndex | None = None) -> None:
        super().__init__(http)
        self.bairros = bairros or BairroIndex.from_files()
        self.osm_venues = OsmVenues(http, self.bairros)
        self.ours: list[Candidate] = []
        self.osm: list[Candidate] = []
        self.left_out = 0

    def tasks(self) -> Iterator[Task]:
        for city in sorted({bairro.city for bairro in self.bairros.bairros}):
            yield Task(f"ours:{city}", f"{SITE}/api/v1/{city}/venues-all.json")
        yield from self.osm_venues.tasks()
        yield Task("overture")

    def fetch(self, task: Task) -> Fetched:
        if task.key == "overture":
            return Fetched(task, json.dumps(list(self.places())).encode(), "application/json")
        if task.key == "overpass":
            return self.osm_venues.fetch(task)
        return super().fetch(task)

    def places(self) -> Iterator[dict[str, Any]]:
        """Overture's places to drink in the covered box, from its latest release."""
        from overturemaps import core

        west, south, east, north = self.bairros.bounds
        reader = core.record_batch_reader("place", bbox=(west, south, east, north))
        if reader is None:
            raise RuntimeError("Overture: no release found")
        for batch in reader:
            for place in batch.to_pylist():
                record = place_record(place)
                if record:
                    yield record

    def parse(self, fetched: Fetched) -> Iterable[Item]:
        key = fetched.task.key
        if key.startswith("ours:"):
            for venue in fetched.json().get("items", []):
                self.ours.append(Candidate(venue["name"], venue["lat"], venue["lng"], venue))
            return []
        if key == "overpass":
            for element in fetched.json().get("elements", []):
                item = self.osm_venues.venue(element)
                if item:
                    data = dict(item.data)
                    self.osm.append(Candidate(data["name"], data["lat"], data["lng"], data))
            return []
        items = [item for record in fetched.json() if (item := self.venue(record))]
        log.info(
            "overture: %d venues sent, %d already listed without a key to fill", len(items), self.left_out
        )
        return items

    def venue(self, record: Mapping[str, Any]) -> Item | None:
        lat, lng = float(record["lat"]), float(record["lng"])
        bairro = self.bairros.locate(lat, lng)
        if bairro is None:
            return None
        name = str(record["name"])
        osm_ref = None
        known = best_match([name], lat, lng, self.ours, max_m=MATCH_M)
        if known is not None:
            osm_ref = known.data.get("osm_ref")
            if not osm_ref:
                self.left_out += 1
                return None
        else:
            mapped = best_match([name], lat, lng, self.osm, max_m=MATCH_M)
            osm_ref = mapped.data.get("osm_ref") if mapped else None
        kind = record["kind"]
        if _WORDS["barraca"].search(name):
            kind = "barraca"
        elif _WORDS["boteco"].search(name):
            kind = "boteco"
        data = {
            "city": bairro.city,
            "name": name[:120],
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "kind": kind,
            "on_site": True,
            "address": (record.get("address") or None) and str(record["address"])[:200],
            "neighbourhood": bairro.slug,
            "osm_ref": osm_ref,
            "cnpj": None,
            "instagram": record.get("instagram"),
            "cardapioweb_slug": None,
            "overture_id": record["id"],
            "opening_hours": None,
            "attributes": [],
        }
        return Item("venue", f"overture:{record['id']}", data)
