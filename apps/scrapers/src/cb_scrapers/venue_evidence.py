"""Gathering the venue classifier's evidence (venue_classifier.py), all from free sources, in GitHub Actions:

- the admin API's candidates (ingest token): the venue, its published prices, the city medians, owner locks;
- the CNPJ register (cnpj.py: minhareceita.org, else CNPJá; cached 90 days);
- OpenStreetMap tags of the venues with an osm_ref (one Overpass query a batch, on instances that allow bots);
- Overture Maps' categories: the place with the venue's overture_id, else the one with a like name within
  MATCH_M (a downloaded GeoJSON-seq file, or one pass over Overture's latest release for the batch's area);
- what the Preço da Hora runs learnt from the receipts (the shop book in the run cache);
- the venue's Instagram account name and bio (Business Discovery, the brand's token), when it has a handle.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from .cnpj import CnpjRegister
from .core import USER_AGENT, PoliteClient, Task
from .matching import Candidate, best_match
from .models import ClassifierCandidates, ClassifierOutcomeCounts, ClassifierResults
from .venue_classifier import VenueEvidence
from .venues import ShopBook

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
#: An Overture place this close with a like name is the venue.
MATCH_M = 150.0
#: Degrees around the batch's venues an Overture pass keeps (about 300 m).
MARGIN = 0.003
MAX_INSTAGRAM = 50


class ClassifierApi:
    """The admin API's venue-classifier endpoints (ingest scope)."""

    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=60.0,
            transport=transport or httpx.HTTPTransport(retries=2),
        )

    def candidates(self, **request: Any) -> dict[str, Any]:
        response = self._client.post("/api/admin/venue-classifier/candidates", json=request)
        response.raise_for_status()
        return ClassifierCandidates.model_validate(response.json()).model_dump(mode="json")

    def results(self, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        totals: dict[str, Any] = {}
        for start in range(0, len(items), 25):
            # Through the contract's model: a bad result fails here, not as a 400 from the Worker.
            request = ClassifierResults.model_validate({"items": list(items[start : start + 25])})
            response = self._client.post(
                "/api/admin/venue-classifier/results", json=request.model_dump(mode="json")
            )
            response.raise_for_status()
            outcome = ClassifierOutcomeCounts.model_validate(response.json())
            for key, value in outcome.model_dump(mode="json").items():
                totals[key] = totals.get(key, 0) + value if isinstance(value, int) else value
        return totals


def overture_record(place: Mapping[str, Any]) -> dict[str, Any] | None:
    """An Overture place (a Parquet row or a GeoJSON feature) as {id, name, lat, lng, primary, alternate}."""
    # Imported here: the adapters package imports the venue_classify adapter, which imports this module.
    from .adapters.overture_places import category as overture_category
    from .adapters.overture_places import point

    props = place.get("properties") if isinstance(place.get("properties"), Mapping) else place
    names = props.get("names") or {}
    name = " ".join(str(names.get("primary") or "").split()) if isinstance(names, Mapping) else ""
    located = point(place.get("geometry"))
    if not name or located is None:
        return None
    categories = props.get("categories") or {}
    alternate = categories.get("alternate") if isinstance(categories, Mapping) else None
    return {
        "id": str(props.get("id") or place.get("id") or ""),
        "name": name,
        "lat": located[0],
        "lng": located[1],
        "primary": overture_category(props),
        "alternate": [str(a) for a in alternate or []][:6],
    }


class OvertureIndex:
    """Overture places near the batch's venues, by id and by name and distance."""

    def __init__(self, records: Iterable[Mapping[str, Any]]) -> None:
        self.by_id: dict[str, Mapping[str, Any]] = {}
        self.candidates: list[Candidate] = []
        for record in records:
            if record.get("id"):
                self.by_id[str(record["id"])] = record
            self.candidates.append(Candidate(str(record["name"]), record["lat"], record["lng"], record))

    @staticmethod
    def near(places: Iterable[Mapping[str, Any]], venues: Sequence[Mapping[str, Any]]) -> Iterator[dict]:
        """The places near any of the venues (or with one's overture_id)."""
        ids = {str(v["overture_id"]) for v in venues if v.get("overture_id")}
        for place in places:
            record = overture_record(place)
            if record is None:
                continue
            if record["id"] in ids or any(
                abs(record["lat"] - v["lat"]) <= MARGIN and abs(record["lng"] - v["lng"]) <= MARGIN
                for v in venues
            ):
                yield record

    @classmethod
    def from_file(cls, path: Path, venues: Sequence[Mapping[str, Any]]) -> OvertureIndex:
        def features() -> Iterator[dict[str, Any]]:
            with path.open(encoding="utf-8") as lines:
                for line in lines:
                    line = line.strip().lstrip("\x1e")
                    if line:
                        yield json.loads(line)

        return cls(cls.near(features(), venues))

    @classmethod
    def from_release(cls, venues: Sequence[Mapping[str, Any]]) -> OvertureIndex:
        """One pass over Overture's latest release, inside the batch's box."""
        from overturemaps import core

        west = min(v["lng"] for v in venues) - MARGIN
        east = max(v["lng"] for v in venues) + MARGIN
        south = min(v["lat"] for v in venues) - MARGIN
        north = max(v["lat"] for v in venues) + MARGIN
        reader = core.record_batch_reader("place", bbox=(west, south, east, north))
        if reader is None:
            raise RuntimeError("Overture: no release found")

        def rows() -> Iterator[dict[str, Any]]:
            for batch in reader:
                yield from batch.to_pylist()

        return cls(cls.near(rows(), venues))

    def find(self, venue: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if venue.get("overture_id") and str(venue["overture_id"]) in self.by_id:
            return self.by_id[str(venue["overture_id"])]
        match = best_match([str(venue["name"])], venue["lat"], venue["lng"], self.candidates, max_m=MATCH_M)
        return match.data if match else None


def osm_query(refs: Iterable[str]) -> str | None:
    """One Overpass query for the tags of these elements ("node/1", "way/2")."""
    groups: dict[str, list[str]] = {}
    for ref in refs:
        kind, _, number = ref.partition("/")
        if kind in ("node", "way", "relation") and number.isdigit():
            groups.setdefault(kind, []).append(number)
    if not groups:
        return None
    parts = "".join(f"{kind}(id:{','.join(sorted(ids))});" for kind, ids in sorted(groups.items()))
    return f"[out:json][timeout:60];({parts});out tags;"


def osm_tags(http: PoliteClient, refs: Iterable[str]) -> dict[str, dict[str, str]]:
    """Tags by osm_ref; empty when Overpass doesn't answer (the votes go on without them)."""
    from .adapters.osm_venues import OsmVenues

    query = osm_query(refs)
    if query is None:
        return {}
    try:
        # OsmVenues knows the instances that allow bots and how to wait for a busy one.
        fetched = OsmVenues(http).fetch(Task("overpass", params={"data": query}))
        elements = fetched.json().get("elements", [])
    except Exception as error:
        log.warning("venue evidence: OpenStreetMap tags skipped (%s)", error)
        return {}
    return {
        f"{e['type']}/{e['id']}": dict(e.get("tags") or {}) for e in elements if "type" in e and "id" in e
    }


class Instagram:
    """An account's name and bio through Business Discovery (the brand's own token), a few a run."""

    def __init__(
        self, client: httpx.Client, token: str, ig_user: str, max_calls: int = MAX_INSTAGRAM
    ) -> None:
        self.client, self.token, self.ig_user, self.max_calls = client, token, ig_user, max_calls
        self.calls = 0

    def account(self, handle: str) -> dict[str, Any] | None:
        if self.calls >= self.max_calls:
            return None
        self.calls += 1
        try:
            response = self.client.get(
                f"{GRAPH}/{self.ig_user}",
                params={
                    "fields": f"business_discovery.username({handle}){{name,biography}}",
                    "access_token": self.token,
                },
                timeout=30,
            )
            found = response.json().get("business_discovery") if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return {"name": found.get("name"), "biography": found.get("biography")} if found else None


class EvidenceBuilder:
    """Evidence for a batch of candidates: `prepare` fetches what is asked once a batch, `build` a venue."""

    def __init__(
        self,
        http: PoliteClient,
        register: CnpjRegister | None,
        book: ShopBook | None = None,
        *,
        instagram: Instagram | None = None,
        overture_file: Path | None = None,
        overture_release: bool = True,
    ) -> None:
        self.http = http
        self.register = register
        self.book = book
        self.instagram = instagram
        self.overture_file = overture_file
        self.overture_release = overture_release
        self.overture: OvertureIndex | None = None
        self.tags: dict[str, dict[str, str]] = {}

    def prepare(self, venues: Sequence[Mapping[str, Any]]) -> None:
        if not venues:
            return
        self.tags = osm_tags(self.http, [str(v["osm_ref"]) for v in venues if v.get("osm_ref")])
        try:
            if self.overture_file is not None:
                self.overture = OvertureIndex.from_file(self.overture_file, venues)
            elif self.overture_release:
                self.overture = OvertureIndex.from_release(venues)
        except Exception as error:
            log.warning("venue evidence: Overture skipped (%s)", error)
            self.overture = None

    def build(self, venue: Mapping[str, Any], medians: Mapping[str, Any] | None = None) -> VenueEvidence:
        cnpj = venue.get("cnpj")
        company = self.register.lookup(cnpj) if cnpj and self.register else None
        overture = self.overture.find(venue) if self.overture else None
        receipts = self.book.records.get(cnpj) if cnpj and self.book else None
        handle = venue.get("instagram")
        return VenueEvidence(
            venue_id=int(venue["id"]),
            name=str(venue["name"]),
            city=str(venue.get("city") or "salvador"),
            lat=float(venue["lat"]),
            lng=float(venue["lng"]),
            bairro=venue.get("neighbourhood"),
            address=venue.get("address"),
            cnpj=cnpj,
            company=company,
            osm_tags=self.tags.get(str(venue.get("osm_ref"))) if venue.get("osm_ref") else None,
            overture=overture,
            prices=venue.get("prices"),
            medians=medians,
            receipts=receipts,
            instagram=self.instagram.account(handle) if handle and self.instagram else None,
        )
