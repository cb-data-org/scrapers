"""Google Places Text Search (New), for the venue classifier: what Google calls a place (its primary type and
types: bar, restaurant, liquor_store, convenience_store, supermarket…).

Google's terms let us keep only the place id. Everything else a search returns (name, types, status) is used
in memory for one classification and then dropped: never written to a file, a log line, D1 or a report. Calls
are capped per run (MAX_CALLS by default) to stay inside the free monthly quota.
"""

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

SEARCH = "https://places.googleapis.com/v1/places:searchText"
FIELDS = "places.id,places.displayName,places.primaryType,places.types,places.businessStatus"
MAX_CALLS = 50
#: Metres around the venue the search prefers.
RADIUS_M = 300.0


@dataclass(frozen=True, slots=True)
class PlaceHit:
    """One search result, held only in memory (see the module's note)."""

    place_id: str
    name: str
    primary_type: str | None
    types: tuple[str, ...]
    business_status: str | None


class GooglePlaces:
    def __init__(self, key: str, client: httpx.Client, max_calls: int = MAX_CALLS) -> None:
        self.key, self.client, self.max_calls = key, client, max_calls
        self.calls = 0

    @property
    def exhausted(self) -> bool:
        return self.calls >= self.max_calls

    def search(self, name: str, city: str, lat: float, lng: float) -> PlaceHit | None:
        """The best match for a venue by name near its pin, or None (no calls left, no answer, none found)."""
        if self.exhausted:
            return None
        self.calls += 1
        body = {
            "textQuery": f"{name}, {city}, BA",
            "maxResultCount": 1,
            "languageCode": "pt-BR",
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": RADIUS_M}},
        }
        try:
            response = self.client.post(
                SEARCH,
                json=body,
                headers={"X-Goog-Api-Key": self.key, "X-Goog-FieldMask": FIELDS},
                timeout=30,
            )
            response.raise_for_status()
            places = response.json().get("places") or []
        except (httpx.HTTPError, ValueError) as error:
            # The error's text may echo the request: only its kind is logged.
            log.warning("google places: %s", type(error).__name__)
            return None
        if not places:
            return None
        place = places[0]
        return PlaceHit(
            place_id=str(place.get("id") or ""),
            name=str((place.get("displayName") or {}).get("text") or ""),
            primary_type=place.get("primaryType"),
            types=tuple(str(t) for t in place.get("types") or []),
            business_status=place.get("businessStatus"),
        )
