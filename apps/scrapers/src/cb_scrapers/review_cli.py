"""`cb venues audit | apply`: review.py wired to production, the scrapers' run cache and Overpass."""

import logging
import os
from collections import Counter
from pathlib import Path

import httpx

from .adapters.osm_venues import OsmVenues
from .adapters.precodahora import SHOP_BOOK_FILE, SHOP_PRICES_FILE
from .cnpj import CnpjRegister, default_cache_file
from .core import BlockedError, PoliteClient, Task
from .core.cache import cache_dir
from .geo import BairroIndex
from .review import AdminClient, apply, audit, held
from .venues import REVIEW_FILE, ShopBook, ShopPrices, load_review, write_review

log = logging.getLogger(__name__)

NIGHTCLUBS = '[out:json][timeout:90];nwr["amenity"="nightclub"]({south},{west},{north},{east});out ids;'


def nightclub_refs(http: PoliteClient, bairros: BairroIndex) -> list[str]:
    """OpenStreetMap's nightclubs in the covered cities, as osm_refs ("node/1")."""
    west, south, east, north = bairros.bounds
    query = NIGHTCLUBS.format(south=south, west=west, north=north, east=east)
    fetched = OsmVenues(http, bairros).fetch(Task("nightclubs", params={"data": query}))
    return [f"{element['type']}/{element['id']}" for element in fetched.json().get("elements", [])]


def summarize(title: str, lines: list[str]) -> str:
    text = (
        f"### {title}\n\n" + ("\n".join(f"- {line}" for line in lines) if lines else "Nothing to do.") + "\n"
    )
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write(text)
    return text


def run_venues(action: str, url: str, token: str, *, apply_changes: bool = False) -> str:
    admin = AdminClient(url, token)
    bairros = BairroIndex.from_files()
    if action == "audit":
        review = load_review()
        cities = sorted({bairro.city for bairro in bairros.bairros})
        deals = [deal for city in cities for deal in admin.published_deals(city)]
        book = ShopBook(cache_dir() / SHOP_BOOK_FILE)
        live = list(admin.venues())
        with PoliteClient(delay=3.0) as http:
            findings = audit(
                live,
                deals,
                CnpjRegister(http, default_cache_file()),
                book,
                ShopPrices(cache_dir() / SHOP_PRICES_FILE),
                review,
            )
        rows = {**review, **{finding.row.cnpj: finding.row for finding in findings}}
        # The shops the runs held back and never sent: pending, for the owner (#340).
        rows |= {row.cnpj: row for row in held(book, rows, (v["cnpj"] for v in live if v.get("cnpj")))}
        write_review(rows.values(), REVIEW_FILE)
        counts = Counter(row.decision for row in rows.values())
        lines = [
            f"{row.decision}: {row.name} ({row.cnpj}) — {row.note}"
            for row in rows.values()
            if row.decision != "keep"
        ]
        title = f"Venue review audit: {dict(counts)} in {REVIEW_FILE.name}"
        return summarize(title, lines)
    skipped = []
    try:
        with PoliteClient(delay=5.0) as http:
            nightclubs = nightclub_refs(http, bairros)
    except (BlockedError, RuntimeError, httpx.HTTPError) as error:
        # The review list doesn't depend on Overpass; the nightclubs wait for the next run.
        log.warning("nightclubs skipped: %s", error)
        nightclubs, skipped = [], ["nightclubs skipped: Overpass didn't answer (run apply again later)"]
    done = apply(admin, load_review().values(), nightclubs, dry_run=not apply_changes)
    return summarize("Venue review: " + ("applied" if apply_changes else "dry run"), done + skipped)
