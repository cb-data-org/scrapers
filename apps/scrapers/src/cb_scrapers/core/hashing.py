"""Stable keys that make scraping idempotent: running a scrape twice never duplicates a listing or a price."""

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from datetime import date

_SPACES = re.compile(r"[ \t\n\r\f\v]+")


def normalize(value: object) -> str:
    """Lowercase, no accents, single spaces: "  Cerveja  BRAHMA Lata " → "cerveja brahma lata"."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value).lower())
    text = "".join(char for char in text if not unicodedata.category(char).startswith("M"))
    return _SPACES.sub(" ", text).strip()


def stable_hash(*parts: object) -> str:
    """128-bit hex digest of the normalized parts, in order."""
    joined = "\x1f".join(normalize(part) for part in parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


def schedule_signature(windows: Iterable[tuple[int, int, int]]) -> str:
    """Canonical text for (dow, start_min, end_min) windows: "1:1020-1200,5:1320-1560"."""
    return ",".join(f"{dow}:{start}-{end}" for dow, start, end in sorted(set(windows)))


def listing_hash(
    *,
    source: str,
    venue_ref: str,
    product: str,
    format_code: str | None = None,
    units: int = 1,
    packaging: str = "single",
    kind: str = "regular",
    schedule: str = "",
) -> str:
    """One offer at one venue as one source sees it, so repeated scrapes update the same deal.

    `venue_ref` is the source's key for the venue (OSM id, CNPJ, Cardápio Web slug) and `product` the source's
    own product id or description, so the key never depends on our fuzzy matching.
    """
    return stable_hash(
        "listing/v1", source, venue_ref, product, format_code, units, packaging, kind, schedule
    )


def obs_hash(*, listing: str, price_minor: int, observed_on: date) -> str:
    """One price fact per listing, price and local day: scraping twice on the same day records it once."""
    return stable_hash("obs/v1", listing, price_minor, observed_on.isoformat())


def content_hash(body: bytes) -> str:
    """SHA-256 of a fetched body, to notice pages that didn't change."""
    return hashlib.sha256(body).hexdigest()
