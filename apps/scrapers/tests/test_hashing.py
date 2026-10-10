from datetime import date

import pytest

from cb_scrapers.core import content_hash, listing_hash, normalize, obs_hash, schedule_signature, stable_hash

BRAHMA = {"source": "precodahora", "venue_ref": "cnpj:12345678000199", "product": "CERV BRAHMA LT 350ML"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Cerveja  BRAHMA\tLata ", "cerveja brahma lata"),
        ("Pé na Areia — Açaí", "pe na areia — acai"),
        (f"R${chr(0xA0)}9,99", "r$ 9,99"),  # non-breaking space
        (350, "350"),
        (None, ""),
    ],
)
def test_normalize(raw: object, expected: str) -> None:
    assert normalize(raw) == expected


def test_stable_hash_is_pinned() -> None:
    # Changing the hashing scheme would duplicate every listing in D1: bump the "v1" prefixes instead.
    assert stable_hash("listing/v1", "a", 1) == "f53814c6d9845095547eedf9afbf1b18"


def test_listing_hash_ignores_cosmetic_differences() -> None:
    noisy = {**BRAHMA, "product": "  cerv  Brahma lt 350ml "}
    assert listing_hash(**BRAHMA) == listing_hash(**noisy)


@pytest.mark.parametrize(
    "change",
    [
        {"source": "cardapioweb"},
        {"venue_ref": "cnpj:99999999000199"},
        {"product": "CERV SKOL LT 350ML"},
        {"format_code": "lata_473"},
        {"units": 12, "packaging": "balde"},
        {"kind": "happy_hour"},
        {"schedule": "5:1320-1560"},
    ],
)
def test_listing_hash_changes_with_the_offer(change: dict[str, object]) -> None:
    assert listing_hash(**BRAHMA) != listing_hash(**{**BRAHMA, **change})


def test_obs_hash_is_one_fact_per_listing_price_and_day() -> None:
    listing = listing_hash(**BRAHMA)
    today = obs_hash(listing=listing, price_minor=499, observed_on=date(2026, 10, 9))
    assert today == obs_hash(listing=listing, price_minor=499, observed_on=date(2026, 10, 9))
    assert today != obs_hash(listing=listing, price_minor=459, observed_on=date(2026, 10, 9))
    assert today != obs_hash(listing=listing, price_minor=499, observed_on=date(2026, 10, 10))


def test_schedule_signature_is_sorted_and_deduplicated() -> None:
    windows = [(5, 1320, 1560), (1, 1020, 1200), (1, 1020, 1200)]
    assert schedule_signature(windows) == "1:1020-1200,5:1320-1560"


def test_content_hash_is_full_sha256() -> None:
    assert content_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
