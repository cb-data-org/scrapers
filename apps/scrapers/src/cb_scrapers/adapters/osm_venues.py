"""OpenStreetMap venues in every covered city, weekly, from one Overpass query per run.

Bulk sources are off limits to bots: Geofabrik and openstreetmap.fr disallow `*.pbf` in robots.txt, and
overpass-api.de disallows `/api/`. The venues come from public Overpass instances that allow bots, as one
bounding-box query with `out center tags` (about 1 MB) tried on each in turn; the daily `cb run --due`
retries a failed week the next day. Venues outside the cities' bairros (beyond the 300 m beach margin) are
dropped, and each one carries its bairro slug, which ingest stores as neighbourhood_id.

Bars, pubs and biergartens are the map's pins; restaurants, cafés and nightclubs come along as venues that
only show up once they have a price.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from ..core import WEEKLY, Adapter, BlockedError, DisallowedError, Fetched, Item, PoliteClient, Task, register
from ..geo import BairroIndex

#: Public instances whose robots.txt lets bots query, tried in turn (overpass-api.de disallows bots).
OVERPASS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
#: Nightclubs aren't places to sit and drink (#329): like restaurants and cafés, they come along as venues
#: that only show up once they have a price.
AMENITY_KINDS = {
    "bar": "bar",
    "pub": "pub",
    "biergarten": "bar",
    "nightclub": "outro",
    "restaurant": "restaurante",
    "cafe": "outro",
}
#: Tries on an instance that answers "busy" (502-504), BUSY_PAUSE seconds apart.
BUSY_TRIES = 3
BUSY_PAUSE = 20.0
#: About 300 m around the bairros, so beach barracas on the sand are included.
MARGIN_DEGREES = 0.003
QUERY = (
    "[out:json][timeout:90];"
    'nwr["amenity"~"^({amenities})$"]["name"]({south},{west},{north},{east});'
    "out center tags;"
)

_INSTAGRAM = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:instagram\.com/|instagr\.am/)?@?([A-Za-z0-9._]{1,30})/?(?:[?#].*)?$"
)
_WORDS = {
    "barraca": re.compile(r"\bbarraca\b", re.I),
    "boteco": re.compile(r"\b(boteco|buteco|botequim)\b", re.I),
    "cervejaria": re.compile(r"\b(cervejaria|brewpub)\b", re.I),
}


def instagram_handle(tags: Mapping[str, str]) -> str | None:
    """`https://instagram.com/Bar.Do_Ze/`, `@bar.do_ze` or `bar.do_ze` → `bar.do_ze`."""
    value = (tags.get("contact:instagram") or tags.get("instagram") or "").strip()
    match = _INSTAGRAM.match(value)
    return match.group(1).lower() if match else None


def cnpj(tags: Mapping[str, str]) -> str | None:
    """Brazilian VAT ids are tagged `ref:vatin=BR12345678000199` (sometimes with punctuation)."""
    digits = re.sub(r"\D", "", tags.get("ref:vatin", ""))
    return digits if len(digits) == 14 else None


def venue_kind(amenity: str, name: str, tags: Mapping[str, str]) -> str:
    if _WORDS["barraca"].search(name):
        return "barraca"
    if _WORDS["boteco"].search(name):
        return "boteco"
    if (
        tags.get("craft") == "brewery"
        or tags.get("microbrewery") == "yes"
        or _WORDS["cervejaria"].search(name)
    ):
        return "cervejaria"
    return AMENITY_KINDS[amenity]


def address(tags: Mapping[str, str]) -> str | None:
    street, number = tags.get("addr:street"), tags.get("addr:housenumber")
    if not street:
        return None
    return (f"{street}, {number}" if number else street)[:200]


def attributes(tags: Mapping[str, str]) -> list[str]:
    found = []
    if tags.get("live_music") == "yes":
        found.append("musica_ao_vivo")
    if tags.get("outdoor_seating") == "yes":
        found.append("area_externa")
    return found


@register
class OsmVenues(Adapter):
    name = "osm_venues"
    cadence = WEEKLY
    delay = 5.0

    def __init__(self, http: PoliteClient, bairros: BairroIndex | None = None) -> None:
        super().__init__(http)
        self.bairros = bairros or BairroIndex.from_files()

    def tasks(self) -> Iterable[Task]:
        west, south, east, north = self.bairros.bounds
        query = QUERY.format(
            amenities="|".join(AMENITY_KINDS),
            south=round(south - MARGIN_DEGREES, 4),
            west=round(west - MARGIN_DEGREES, 4),
            north=round(north + MARGIN_DEGREES, 4),
            east=round(east + MARGIN_DEGREES, 4),
        )
        yield Task("overpass", params={"data": query})

    def fetch(self, task: Task) -> Fetched:
        """The first instance that answers wins; a refusing or silent one passes the query to the next."""
        problems: list[str] = []
        blocked = False
        for url in OVERPASS:
            for attempt in range(1, BUSY_TRIES + 1):
                try:
                    # Overpass answers within its 90 s server timeout or not at all, so a timeout moves on to
                    # the next instance; a busy one answers 502-504 at once and gets a few more tries.
                    response = self.http.post(
                        url,
                        data=dict(task.params),
                        timeout=100.0,
                        retries=0,
                        delay=BUSY_PAUSE if attempt > 1 else None,
                    )
                except httpx.HTTPStatusError as error:
                    problems.append(f"{url}: HTTP {error.response.status_code}")
                    if error.response.status_code in (502, 503, 504):
                        continue
                except BlockedError as error:
                    blocked = True
                    problems.append(f"{url}: {error}")
                except (DisallowedError, httpx.HTTPError) as error:
                    problems.append(f"{url}: {type(error).__name__}: {error}")
                else:
                    return Fetched(task, response.content, response.headers.get("content-type", ""))
                break
        message = "every Overpass instance failed: " + " · ".join(problems)
        raise BlockedError(task.key, message) if blocked else RuntimeError(message)

    def parse(self, fetched: Fetched) -> Iterable[Item]:
        for element in fetched.json().get("elements", []):
            item = self.venue(element)
            if item:
                yield item

    def venue(self, element: Mapping[str, Any]) -> Item | None:
        tags: Mapping[str, str] = element.get("tags", {})
        amenity, name = tags.get("amenity"), " ".join((tags.get("name") or "").split())
        point = element if element.get("type") == "node" else element.get("center", {})
        if amenity not in AMENITY_KINDS or not name or "lat" not in point or "lon" not in point:
            return None
        lat, lng = float(point["lat"]), float(point["lon"])
        bairro = self.bairros.locate(lat, lng)
        if bairro is None:
            return None
        osm_ref = f"{element['type']}/{element['id']}"
        hours = tags.get("opening_hours")
        data = {
            "city": bairro.city,
            "name": name[:120],
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "kind": venue_kind(amenity, name, tags),
            "on_site": True,
            "address": address(tags),
            "neighbourhood": bairro.slug,
            "osm_ref": osm_ref,
            "cnpj": cnpj(tags),
            "instagram": instagram_handle(tags),
            "cardapioweb_slug": None,
            "opening_hours": hours[:255] if hours else None,
            "attributes": attributes(tags),
        }
        return Item("venue", f"osm:{osm_ref}", data)
