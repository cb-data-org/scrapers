"""Preço da Hora Bahia (#89): what bars charge for beer, from the state's electronic receipts (SEFAZ-BA and
PRODEB).

How the site works: a GET of /produtos/ sets the session cookies and carries a CSRF token; each search is a
POST to /produtos/ with a term, a place (latitude, longitude and a radius in km), the last `horas` hours of
sales, a page and an order, and answers JSON: 25 sales a page, each with the product (description, NCM, unit
price, date) and the shop (name, CNPJ, address, coordinates).

Once a day the adapter:
1. Reads the bars, pubs and restaurants OpenStreetMap has around the searched places (one Overpass query), to
   recognise shops that are already on the map.
2. Searches each beer brand (and "chopp") around each place, every page, more gently than the polite client
   alone: one search at a time, SEARCH_DELAY seconds apart, at most MAX_SEARCHES a run, and a stop at the
   first sign that the site doesn't want us (403/429, a CAPTCHA switched on, a page without its CSRF token,
   an answer that isn't the expected JSON). It never works around any of these. The searches wait in a queue
   that each run resumes; chopp around the MVP jumps it about every other day, as the site only keeps 72
   hours of sales.
3. Keeps beer (NCM 2203) sold by the unit whose description reads as a catalog serving (core/products.py).
4. Keeps only places to sit and drink, conservatively (venues.py, #330): the main activity (CNAE, from the
   CNPJ register) must be a bar, a restaurant or a brewery's taproom, and the evidence must not say "shop":
   secondary activities, beer sold by the box, prices like the supermarkets' in the same
   receipts, a micro-entrepreneur OpenStreetMap doesn't know. Doubtful shops are sent with their doubts
   (VenueDraft.held_back) and wait in the queue for the AI reviewer, which keeps or hides each by the
   review threshold (#416); their prices wait with them. The owner's decisions in data/seed/venue-review.csv
   still come first; each run's GitHub summary lists the held-back shops with their evidence.
5. Sends each shop as a venue: the OpenStreetMap one when name and place match (with its CNPJ, so the map
   keeps one pin), else a new one with the trade name from the CNPJ register, the receipt's address and
   coordinates. A one-person business without a trade name is left out: its legal name is its owner's name.
6. Sends each beer as a deal at that venue: the latest unit price, the sale's local date, and the receipt's
   description in the notes.
"""

import json
import logging
import math
import os
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from ..cnpj import CnpjRegister, Company, default_cache_file
from ..core import (
    DAILY,
    Adapter,
    BlockedError,
    Fetched,
    Item,
    PoliteClient,
    Task,
    normalize,
    register,
)
from ..core.cache import cache_dir
from ..core.products import NotAServing, Serving, ServingReader
from ..geo import BairroIndex
from ..geo.bairros import Bairro
from ..matching import Candidate, best_match
from ..venues import (
    BAR_PRICE_RATIO,
    SCOPE,
    SHOP_PRICE_MIN,
    SUPERMARKETS,
    TAKEAWAY_CNAE,
    Evidence,
    ReviewRow,
    ShopBook,
    ShopPrices,
    ShopRecord,
    Verdict,
    judge,
    load_review,
    shop_category,
    summary_table,
)
from .osm_venues import OsmVenues, venue_kind

log = logging.getLogger(__name__)

BASE = "https://precodahora.ba.gov.br"
SEARCH_URL = f"{BASE}/produtos/"
#: Seconds between two searches. The site answered 429 once searches came 7 a minute, so at most 3 a minute.
SEARCH_DELAY = 20.0
#: Searches a run, under the 33rd search that the site once refused.
MAX_SEARCHES = 30
#: The site returns at most 4 pages (100 sales) per search, whatever the radius (the rest only shows from the
#: other end: cheapest first). It ignores any page-size parameter (probed 11 Oct 2026).
MAX_PAGES = 4
PAGE_SIZE = 25
CAP = MAX_PAGES * PAGE_SIZE
#: What each search found, by term and circle (owner, 11 Oct 2026: every sale, without hammering the site),
#: so the next rounds start at the right size: a crowded 2 km circle goes straight to its 1 km circles, a
#: crowded 1 km circle is read from both ends, and a circle where a term sold nothing waits EMPTY_WAIT.
#: Forgotten every DENSITY_DAYS, so new bars and busier streets come back into view.
DENSITY_FILE = "precodahora-density.json"
DENSITY_DAYS = 90
EMPTY_WAIT = timedelta(days=14)
#: A 2 km circle's 1 km circles: its centre and six around it, RING_KM away (together they cover it).
RING_KM = 1.5
#: Sales from the last 72 hours, as the site offers.
HOURS = 72
#: The MVP: circles (latitude, longitude, radius in km) around Pituba (covering Itaigara) and Rio Vermelho.
MVP = {
    "pituba": (-12.99928, -38.46068, 2),
    "rio-vermelho": (-13.00994, -38.48995, 2),
}
#: 1 km circles covering Pituba, Itaigara and Rio Vermelho, under the circle they sit in: a term with more
#: than 100 sales around a large circle is searched in its smaller ones, so fewer bars hide behind the cap.
SMALLER = {
    "pituba": {
        "pituba-1": (-13.01352, -38.45830, 1),
        "pituba-2": (-13.00072, -38.46588, 1),
        "pituba-3": (-13.00072, -38.45072, 1),
        "pituba-4": (-12.98792, -38.47347, 1),
        "pituba-5": (-12.98792, -38.45830, 1),
    },
    "rio-vermelho": {
        "rio-vermelho-1": (-13.01352, -38.50380, 1),
        "rio-vermelho-2": (-13.01352, -38.48864, 1),
        "rio-vermelho-3": (-13.01352, -38.47347, 1),
        "rio-vermelho-4": (-13.00072, -38.49622, 1),
        "rio-vermelho-5": (-13.00072, -38.48105, 1),
    },
}
#: The rest of the city: 1 km circles over the densest bar areas on OpenStreetMap, densest first.
CITY = {
    "barra": (-13.00541, -38.52426, 1),
    "nazare": (-12.97846, -38.51043, 1),
    "garcia": (-12.99193, -38.51965, 1),
    "itapua": (-12.95151, -38.36294, 1),
    "sao-cristovao": (-12.91108, -38.36294, 1),
    "stiep": (-12.98295, -38.45051, 1),
    "barbalho": (-12.96947, -38.50121, 1),
    "boca-do-rio": (-12.97397, -38.43207, 1),
    "stella-maris": (-12.93803, -38.33989, 1),
    "paripe": (-12.83473, -38.47816, 1),
    "bonfim": (-12.92905, -38.50121, 1),
    "pituacu": (-12.96947, -38.41364, 1),
    "itapua-2": (-12.93354, -38.36754, 1),
    "vilas-do-atlantico": (-12.88863, -38.29380, 1),
    "brotas": (-12.98295, -38.49199, 1),
    "jardim-armacao": (-12.98744, -38.44129, 1),
    "aeroporto": (-12.90659, -38.33528, 1),
    "luiz-anselmo": (-12.96947, -38.48277, 1),
    "beiru": (-12.94702, -38.44129, 1),
    "lobato": (-12.91557, -38.48738, 1),
    "pitangueiras": (-12.88413, -38.31223, 1),
    "federacao": (-12.99642, -38.50582, 1),
    "caminho-das-arvores": (-12.98295, -38.46434, 1),
    "trobogy": (-12.93354, -38.39981, 1),
    "coutos": (-12.84820, -38.47355, 1),
    "itapua-3": (-12.96049, -38.34911, 1),
    "stella-maris-2": (-12.93803, -38.32145, 1),
    "itapua-4": (-12.92905, -38.35833, 1),
    "saboeiro": (-12.96498, -38.44590, 1),
    "sao-rafael": (-12.93803, -38.42285, 1),
    "sao-cristovao-2": (-12.90659, -38.35372, 1),
    "patamares": (-12.96049, -38.39981, 1),
    "liberdade": (-12.94252, -38.49660, 1),
}
#: All of Salvador and Lauro de Freitas (owner, 10 Oct 2026): a hexagonal grid of circles over the
#: bairros, searched 2 km wide (the site takes whole kilometres), leaving out what the circles above
#: already cover. Generated from data/seed/bairros; they join the city's circles, after them.
GRID = {
    "grid-1": (-12.97053, -38.46544, 2),
    "grid-2": (-12.94712, -38.47744, 2),
    "grid-3": (-12.94712, -38.45343, 2),
    "grid-4": (-12.94712, -38.42942, 2),
    "grid-5": (-12.94712, -38.40541, 2),
    "grid-6": (-12.94712, -38.3814, 2),
    "grid-7": (-12.94712, -38.33338, 2),
    "grid-8": (-12.92372, -38.51346, 2),
    "grid-9": (-12.92372, -38.46544, 2),
    "grid-10": (-12.92372, -38.44143, 2),
    "grid-11": (-12.92372, -38.41742, 2),
    "grid-12": (-12.92372, -38.39341, 2),
    "grid-13": (-12.92372, -38.34539, 2),
    "grid-14": (-12.92372, -38.32138, 2),
    "grid-15": (-12.90031, -38.47744, 2),
    "grid-16": (-12.90031, -38.45343, 2),
    "grid-17": (-12.90031, -38.42942, 2),
    "grid-18": (-12.90031, -38.40541, 2),
    "grid-19": (-12.90031, -38.3814, 2),
    "grid-20": (-12.90031, -38.30937, 2),
    "grid-21": (-12.8769, -38.46544, 2),
    "grid-22": (-12.8769, -38.44143, 2),
    "grid-23": (-12.8769, -38.41742, 2),
    "grid-24": (-12.8769, -38.39341, 2),
    "grid-25": (-12.8769, -38.3694, 2),
    "grid-26": (-12.8769, -38.34539, 2),
    "grid-27": (-12.8769, -38.32138, 2),
    "grid-28": (-12.8769, -38.29737, 2),
    "grid-29": (-12.8769, -38.27336, 2),
    "grid-30": (-12.8535, -38.45343, 2),
    "grid-31": (-12.8535, -38.42942, 2),
    "grid-32": (-12.8535, -38.3814, 2),
    "grid-33": (-12.8535, -38.35739, 2),
    "grid-34": (-12.8535, -38.33338, 2),
    "grid-35": (-12.8535, -38.30937, 2),
    "grid-36": (-12.83009, -38.48945, 2),
    "grid-37": (-12.83009, -38.46544, 2),
    "grid-38": (-12.83009, -38.3694, 2),
    "grid-39": (-12.83009, -38.34539, 2),
    "grid-40": (-12.80669, -38.64551, 2),
    "grid-41": (-12.80669, -38.50145, 2),
    "grid-42": (-12.80669, -38.47744, 2),
    "grid-43": (-12.78328, -38.65752, 2),
    "grid-44": (-12.78328, -38.63351, 2),
    "grid-45": (-12.78328, -38.53747, 2),
    "grid-46": (-12.78328, -38.51346, 2),
    "grid-47": (-12.75987, -38.64551, 2),
    "grid-48": (-12.75987, -38.6215, 2),
    "grid-49": (-12.75987, -38.52546, 2),
}
CITY.update(GRID)
#: Barcode searches start wide (owner, 11 Oct 2026: every price, each search under the cap): one product
#: across both cities fits under 100 sales more often than not; a crowded one goes to the city's quarters,
#: then to the 2 km circles in each quarter, then to their 1 km circles (children()).
WIDE = {"wide": (-12.915, -38.45, 25)}
QUARTERS = {
    "wide-sw": (-12.985, -38.50, 12),
    "wide-se": (-12.965, -38.38, 12),
    "wide-nw": (-12.84, -38.50, 12),
    "wide-ne": (-12.87, -38.33, 12),
}
#: Places only searched with CB_FOCUS (owner, 12 Oct 2026: the founders campaign's second area). Salvador
#: Shopping (-12.9783, -38.4547) and Shopping da Bahia (-12.9812, -38.4650), 1.1 km apart: the circle sits
#: between them (each about 550 m from its centre), so 1 km covers both malls and the bars around them.
FOCUS_ONLY = {"salvador-shopping": (-12.97975, -38.45985, 1)}
PLACES = {
    **MVP,
    **{name: circle for circles in SMALLER.values() for name, circle in circles.items()},
    **CITY,
    **WIDE,
    **QUARTERS,
    **FOCUS_ONLY,
}
#: When a word still sells more than both ends of a 1 km circle show (200), it is searched again with these
#: added ("heineken lata"), as long as that narrows the search; a beer's barcode searches cover the rest.
NARROWER = ("lata", "long neck", "600", "garrafa", "chopp")
#: The barcode catalogue (what each code sells, learnt from the receipts) and how many codes a round searches.
GTIN_FILE = "precodahora-gtins.json"
GTIN_SEARCHES = 120
#: Search terms, best sellers in bars first. "original" matches other products too, but in 1 km circles the
#: bars' Original 600 ml shows. The brands sold mostly in shops come after the bars' (#438: supermarkets and
#: depósitos list too): dropped on 6 Oct 2026 (#320) while only bars counted, Skol, Itaipava and Antarctica
#: had no price at all, as barcodes are only learnt from the sales these words find. "antarctica" brings
#: mostly Guaraná Antarctica, left out by its NCM, for the beers' barcodes. "litrao" rarely matches, as
#: receipts write the litre ("CERV SKOL 1L"): litrões come with their brands' words and barcodes.
TERMS = (
    "chopp",
    "heineken",
    "brahma",
    "amstel",
    "stella",
    "budweiser",
    "spaten",
    "corona",
    "devassa",
    "eisenbahn",
    "petra",
    "bohemia",
    "imperio",
    "schin",
    "michelob",
    "becks",
    "sol premium",
    "colorado",
    "patagonia",
    "goose island",
    "estrella",
    "guinness",
    "chope",
    "skol",
    "itaipava",
    "antarctica",
    "crystal",
    "glacial",
    "proibida",
    "kaiser",
    "bavaria",
    "original",
    "litrao",
)
#: The terms that find bars, for the rest of the city.
CITY_TERMS = TERMS[:8]
#: Draught sells fastest and the site keeps 72 hours of sales, so "chopp" around the MVP doesn't wait for its
#: turn in a round of about ten days (#326): it comes round ahead of the queue whenever the last time started
#: this long ago, about every other daily run. Under 48 hours, as scheduled runs start hours late and the run
#: after next must come before the 72 hours are up. About four searches each time.
SWEEP_TERMS = ("chopp",)
SWEEP_EVERY = timedelta(hours=40)
#: Supermarket prices and what the runs learnt about each shop, in the run cache (v2: supermarkets only).
SHOP_PRICES_FILE = "precodahora-supermarket-prices.json"
SHOP_BOOK_FILE = "precodahora-shops-v2.json"
#: How long the last good Overpass answer stands in when Overpass is down.
OSM_CACHE_DAYS = 30
BAHIA = timezone(timedelta(hours=-3))  # no daylight saving time
BEER_NCM = "2203"
#: The site's answer code for a search without sales (80 when there are some).
NO_RESULTS = 50
#: Sold by the unit ("UN", "UND", "UN0001"…) or by the box, pack or bale ("CX", "FD", "PCT"…).
SINGLE_UNIT = re.compile(r"^(UN|UND|UNID|UNIDADE|U)\d*$")
PACK_UNIT = re.compile(r"^(CX|CXA|CXT|FD|FARDO|PCT|PK|PACK|EMB|DZ)")
#: Draught to take away: kegs of 5 litres or more, and barrels.
KEG = re.compile(r"\b(barril|keg)\b|\bchop+e?\b.*?\b([5-9]|[1-9]\d+) ?(l|lt|lts|litros?)\b")
#: Shops that never serve on site, told by name so they cost no CNPJ lookup.
RETAIL = re.compile(
    r"\b(super ?mercados?|hiper ?mercados?|mini ?mercados?|mercadinho|atacad\w*|atakarejo|atacarejo|assai"
    r"|gbarbosa|bompreco|carrefour|hiperideal|farmacias?|drogarias?|drogasil|combustive\w*|distribuidora"
    r"|deposito|padaria|panificadora)\b"
)


LEGAL_SUFFIX = re.compile(r"(\s+(ltda|limitada|limitad|me|epp|eireli|s/?a|s\.a\.?|cia)\.?)+$", re.IGNORECASE)
SMALL_WORDS = {"de", "da", "do", "das", "dos", "e", "em", "na", "no", "nas", "nos", "a", "o", "com", "por"}


@dataclass(frozen=True, slots=True)
class Venue:
    #: How deals name it: "osm:node/1" when matched to OpenStreetMap, else "cnpj:…" (always, when held back).
    ref: str
    name: str
    item: Item
    #: Sent with the checks' doubts (VenueDraft.held_back): it and its prices wait for the AI reviewer.
    held: bool = False


@dataclass(frozen=True, slots=True)
class Shop:
    """A shop on the receipts, as far as this run knows it."""

    cnpj: str
    receipt_name: str
    #: A supermarket: its prices are the shop prices bars' prices are compared with.
    retail: bool
    company: Company | None = None
    bairro: Bairro | None = None
    lat: float = 0.0
    lng: float = 0.0
    address: str | None = None
    match: Candidate | None = None
    #: "mercado" or "deposito": a shop whose single beers are listed in their own category (#438).
    category: str | None = None

    @property
    def candidate(self) -> bool:
        """Its main activity is in scope: a bar, a restaurant or a brewery."""
        return bool(self.company and self.company.active and self.company.cnae in SCOPE and self.bairro)


def tidy_name(name: str) -> str:
    """ "BAR DO ZE LTDA" → "Bar do Ze"; tax ids and other long numbers are dropped."""
    text = LEGAL_SUFFIX.sub("", re.sub(r"\d{6,}", " ", " ".join(name.split()))).strip(" -.,")
    words = text.lower().split()
    titled = [
        word if i and word in SMALL_WORDS else word[:1].upper() + word[1:] for i, word in enumerate(words)
    ]
    return " ".join(titled)[:120]


def tidy_address(shop: dict[str, Any]) -> str | None:
    street = tidy_name(str(shop.get("endLogradouro") or ""))
    number = str(shop.get("endNumero") or "").strip().lstrip("0")
    if not street:
        return None
    if not number.isdigit():
        return street[:200]
    return f"{street}, {number}"[:200]


def sale_date(value: Any) -> date | None:
    """ "2026-10-04 20:00:17-00:00" (UTC) → the local date in Salvador."""
    try:
        return datetime.fromisoformat(str(value)).astimezone(BAHIA).date()
    except ValueError:
        return None


def shape(data: Any) -> str:
    """An unexpected answer for the log: its keys and short values, never more than 300 characters."""
    if isinstance(data, dict):
        fields = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)[:40]}" for key, value in data.items()
        )
        return "{" + fields[:300] + "}"
    return json.dumps(data, ensure_ascii=False)[:300]


def sweep_job(term: str, place: str) -> bool:
    """A search of the MVP sweep: a sweep term around an MVP circle or one of its smaller circles."""
    return term in SWEEP_TERMS and (place in MVP or any(place in SMALLER.get(mvp, {}) for mvp in MVP))


def children(place: str) -> dict[str, tuple[float, float, int]]:
    """The 1 km circles to search instead of a crowded 2 km one: the MVP's hand-picked ones, else the circle's
    centre and six around it ("grid-5/0" … "grid-5/6"). None for a 1 km circle: the site takes whole km."""
    if place in SMALLER:
        return SMALLER[place]
    if place in WIDE:
        return dict(QUARTERS)
    if place in QUARTERS:
        # The 2 km circles nearest this quarter (each in one quarter only).
        def nearest(lat: float, lng: float) -> str:
            return min(QUARTERS, key=lambda q: (QUARTERS[q][0] - lat) ** 2 + (QUARTERS[q][1] - lng) ** 2)

        return {name: c for name, c in {**MVP, **GRID}.items() if c[2] == 2 and nearest(c[0], c[1]) == place}
    found = PLACES.get(place)
    if found is None or found[2] < 2:
        return {}
    lat, lng, _ = found
    ring = {f"{place}/0": (lat, lng, 1)}
    for k in range(6):
        angle = math.radians(60 * k)
        dlat = RING_KM * math.cos(angle) / 111.32
        dlng = RING_KM * math.sin(angle) / (111.32 * math.cos(math.radians(lat)))
        ring[f"{place}/{k + 1}"] = (round(lat + dlat, 5), round(lng + dlng, 5), 1)
    return ring


def circle(place: str) -> tuple[float, float, int] | None:
    """A place's circle: a named one, or a 1 km circle of a 2 km one ("grid-5/3")."""
    if place in PLACES:
        return PLACES[place]
    parent = place.rpartition("/")[0]
    return children(parent).get(place) if parent else None


class Density:
    """Sales each search found (totalRegistros) by term and circle, with the day, in the run cache; started
    afresh every DENSITY_DAYS."""

    def __init__(self, path: Path, today: date) -> None:
        self.path, self.today = path, today
        try:
            saved = json.loads(path.read_text("utf-8"))
            started = date.fromisoformat(saved["started"])
            cells = dict(saved["cells"])
        except (OSError, ValueError, KeyError, TypeError):
            started, cells = today, {}
        if (today - started).days >= DENSITY_DAYS:
            log.info("search sizes from %s forgotten (every %d days)", started, DENSITY_DAYS)
            started, cells = today, {}
        self.started, self.cells = started, cells

    def get(self, term: str, place: str) -> tuple[int, date] | None:
        cell = self.cells.get(f"{term}|{place}")
        try:
            return int(cell["total"]), date.fromisoformat(cell["on"])
        except (TypeError, KeyError, ValueError):
            return None

    def set(self, term: str, place: str, total: int) -> None:
        self.cells[f"{term}|{place}"] = {"total": total, "on": self.today.isoformat()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        saved = {"started": self.started.isoformat(), "cells": self.cells}
        self.path.write_text(json.dumps(saved, ensure_ascii=False), "utf-8")


def gtin_of(product: dict[str, Any]) -> str | None:
    """A sale's barcode (GTIN/EAN, 8 to 14 digits), or None ("SEM GTIN", internal codes)."""
    code = re.sub(r"\D", "", str(product.get("gtin") or ""))
    return code if 8 <= len(code) <= 14 and code.strip("0") else None


class GtinBook:
    """What each barcode sells, learnt from receipts whose description reads clearly ("CERV HEINEKEN LT
    350ML"): a manufacturer's code is the same in every shop, however each shop writes the product (owner,
    11 Oct 2026).
    A code reads as its serving once two sales agree and three in four do."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.votes: dict[str, Counter[str]] = {}
        try:
            saved = json.loads(path.read_text("utf-8")) if path else {}
            self.votes = {code: Counter(keys) for code, keys in saved.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            self.votes = {}

    @staticmethod
    def key(serving: Serving) -> str | None:
        if serving.brand is None or serving.units != 1 or serving.packaging != "single":
            return None
        return f"{serving.brand.slug}|{serving.beer.slug if serving.beer else ''}|{serving.format_code}"

    def vote(self, code: str, serving: Serving) -> None:
        key = self.key(serving)
        if key:
            self.votes.setdefault(code, Counter())[key] += 1

    def majority(self, code: str) -> str | None:
        votes = self.votes.get(code)
        if not votes:
            return None
        key, count = votes.most_common(1)[0]
        return key if count >= 2 and count >= 0.75 * sum(votes.values()) else None

    def serving(self, code: str, catalog: Any) -> Serving | None:
        """The serving a code sells, rebuilt from the catalog, or None when not known well enough."""
        key = self.majority(code)
        if key is None or catalog is None:
            return None
        brand_slug, beer_slug, format_code = key.split("|")
        brand = next((b for b in catalog.brands if b.slug == brand_slug), None)
        beer = next((b for b in catalog.beers if b.slug == beer_slug), None) if beer_slug else None
        fmt = next((f for f in catalog.formats if f.code == format_code), None)
        if brand is None or fmt is None or (beer_slug and beer is None):
            return None
        return Serving(
            brand,
            brand.aliases[0],
            beer,
            beer.aliases[0] if beer else None,
            fmt.type,
            fmt.code,
            fmt.volume_ml,
        )

    def searchable(self, limit: int = GTIN_SEARCHES) -> list[str]:
        """The codes worth a search of their own: known, most sold first, but one code of every product (beer
        and size) before a second code of any, so the products the words have only just found (Skol's cans)
        aren't crowded out by the brands whose codes have been searched, and voted for, since the start."""
        products = {code: self.majority(code) for code in self.votes}
        known = sorted(
            (code for code, product in products.items() if product),
            key=lambda code: -sum(self.votes[code].values()),
        )
        seen: set[str | None] = set()
        first, more = [], []
        for code in known:
            (more if products[code] in seen else first).append(code)
            seen.add(products[code])
        return [*first, *more][:limit]

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({c: dict(v) for c, v in self.votes.items()}), "utf-8")


def serving_key(serving: Serving) -> str:
    """The beer and size, to compare a bar's price with shops': "brand:heineken|long_neck_330"."""
    return "|".join(serving.key.split("|")[:2])


def display_name(company: Company, receipt_name: str) -> str | None:
    """The trade name; else, for companies, the receipt's name; never a one-person business's legal name."""
    if company.trade_name:
        return tidy_name(company.trade_name) or None
    if company.individual:
        return None
    return tidy_name(receipt_name) or (tidy_name(company.legal_name) if company.legal_name else None) or None


@register
class PrecoDaHora(Adapter):
    name = "precodahora"
    cadence = DAILY
    delay = SEARCH_DELAY

    def __init__(
        self,
        http: PoliteClient,
        bairros: BairroIndex | None = None,
        cnpjs: CnpjRegister | None = None,
        osm_cache: Path | None = None,
        review: dict[str, ReviewRow] | None = None,
        stores: bool | None = None,
    ) -> None:
        super().__init__(http)
        #: Supermarkets' and depósitos' single beers too (#438): on unless CB_STORES=0.
        self.stores = os.environ.get("CB_STORES", "1") != "0" if stores is None else stores
        self.bairros = bairros or BairroIndex.from_files()
        self.cnpjs = cnpjs or CnpjRegister(http, default_cache_file())
        self.osm_cache = osm_cache or cache_dir() / "precodahora-osm.json"
        #: A full pass over one place, round after round (owner, 11 Oct 2026: the full potential for Pituba):
        #: CB_FOCUS=pituba searches every word and every known barcode there, reading every page, with its
        #: own queue; the city's round waits untouched until the variable is cleared. Several places, comma
        #: separated (owner, 12 Oct 2026: the second campaign's areas), share one queue, place after place.
        self.focus_places = [
            place
            for place in dict.fromkeys(os.environ.get("CB_FOCUS", "").replace(" ", "").split(","))
            if place in PLACES
        ]
        self.focus = "+".join(self.focus_places) or None
        self.focus_round = 0
        name = f"precodahora-queue-{self.focus}.json" if self.focus else "precodahora-queue.json"
        self.queue_file = cache_dir() / name
        self.now = datetime.now(UTC)
        #: When the last MVP sweep started, as saved with the queue.
        self.swept: str | None = None
        self.osm_venues = OsmVenues(http, self.bairros)
        self.csrf: str | None = None
        self.refreshed = False
        self.osm: list[Candidate] | None = None
        self.searching: dict[str, tuple[str, str]] = {}
        self.pages: dict[tuple[str, str], int] = {}
        self.totals: dict[tuple[str, str], int] = {}
        self.deals_per_page: dict[tuple[str, str], list[int]] = {}
        self.shops: dict[str, Shop | None] = {}
        self.venues: dict[str, Venue | None] = {}
        self.announced: set[str] = set()
        #: Supermarkets' and depósitos' refs this run: their deals don't count as bars'.
        self.store_refs: set[str] = set()
        self.held: set[str] = set()
        self.review = load_review() if review is None else review
        self.shop_prices = ShopPrices(cache_dir() / SHOP_PRICES_FILE)
        self.book = ShopBook(cache_dir() / SHOP_BOOK_FILE)
        self.density = Density(cache_dir() / DENSITY_FILE, self.now.date())
        self.gtins = GtinBook(cache_dir() / GTIN_FILE)
        #: Narrower words tried for a crowded search, by the search they narrow: (term, place) → its total.
        self.narrowing: dict[tuple[str, str], int] = {}
        #: Sales still out of reach after both ends of a 1 km circle, by search.
        self.capped: dict[tuple[str, str], int] = {}
        self.searches = 0
        self.skipped: Counter[str] = Counter()
        self.unread: Counter[str] = Counter()
        self._reader: ServingReader | None = None

    @property
    def reader(self) -> ServingReader:
        if self._reader is None:
            if self.catalog is None:
                raise RuntimeError("the runner sets the catalog before the first task")
            self._reader = ServingReader(self.catalog)
        return self._reader

    # Tasks: OpenStreetMap's venues once a day, then searches from a queue that each run resumes.

    def tasks(self) -> Iterator[Task]:
        queue = self.load_queue()
        try:
            self.osm = self.cached_osm(max_days=0)
            if self.osm is None:
                yield Task("osm", params={"data": self.osm_query()})
            if self.osm is None:
                self.osm = self.cached_osm(max_days=OSM_CACHE_DAYS)
                if self.osm is not None:
                    log.warning("Overpass is down: matching against an older OpenStreetMap answer")
            if self.osm is None:
                log.warning("no OpenStreetMap venues to match shops against; no searches this run")
                return
            while queue:
                term, place = queue[0]
                if self.searches >= MAX_SEARCHES:
                    log.info("%d searches done; %d jobs wait for the next runs", MAX_SEARCHES, len(queue))
                    return
                smaller = children(place)
                known = self.density.get(term, place)
                if known and known[0] > CAP and smaller:
                    # Crowded last time: straight to the smaller circles, without the big one's search.
                    queue[0:1] = [(term, child) for child in smaller]
                    continue
                if known and known[0] == 0 and self.now.date() - known[1] < EMPTY_WAIT:
                    self.skipped["search: nothing sold there lately"] += 1
                    queue.pop(0)
                    continue
                barcode = term.isdigit()
                parent = self.narrowing.get((term, place))
                # Shops' single beers come last in the dearest-first pages: one cheapest-first page for them.
                # (A full pass reads every page anyway: no need.)
                cheap_first = self.stores and not barcode and not self.focus
                if cheap_first:
                    yield self.search_task(term, place, 1, cheapest=True)
                split = useless = False
                for page in range(1, MAX_PAGES + 1):
                    if self.searches >= MAX_SEARCHES:
                        log.info("%d searches done; %d jobs wait for the next runs", MAX_SEARCHES, len(queue))
                        return
                    yield self.search_task(term, place, page)
                    if page == 1:
                        total = self.totals.get((term, place), 0)
                        self.density.set(term, place, total)
                        if total > CAP and smaller:
                            # Over the cap: the smaller circles, next.
                            queue[1:1] = [(term, child) for child in smaller]
                            split = True
                            break
                        if parent is not None and total >= parent:
                            # A word added didn't narrow it (the site matches any word): no more such tries.
                            self.skipped["search: a narrower word didn't narrow"] += 1
                            queue[1:] = [job for job in queue[1:] if job not in self.narrowing]
                            useless = True
                            break
                    # Every page of a barcode (shops' sales too); words stop once the bars are behind.
                    if page >= self.pages.get((term, place), 0) or (
                        not barcode and not self.focus and self.last_page(term, place, page)
                    ):
                        break
                if useless:
                    queue.pop(0)
                    continue
                total = self.totals.get((term, place), 0)
                if not split and total > CAP:
                    # Over the cap in a 1 km circle: what the dearest-first pages missed, cheapest first.
                    needed = min(MAX_PAGES, math.ceil((total - CAP) / PAGE_SIZE))
                    for page in range(2 if cheap_first else 1, needed + 1):
                        if self.searches >= MAX_SEARCHES:
                            log.info("%d searches done; %d jobs wait", MAX_SEARCHES, len(queue))
                            return
                        yield self.search_task(term, place, page, cheapest=True)
                    if total > 2 * CAP:
                        if term.isdigit() or parent is not None:
                            self.capped[(term, place)] = total - 2 * CAP
                        else:
                            # Still over both ends: the word narrowed, next (barcodes cover the shops).
                            narrower = [(f"{term} {word}", place) for word in NARROWER if word not in term]
                            for job in narrower:
                                self.narrowing[job] = total
                            queue[1:1] = narrower
                queue.pop(0)
        finally:
            self.save_queue(queue)
            self.density.save()
            self.gtins.save()
            self.shop_prices.save()
            self.book.save()
            self.log_summary()

    def first_queue(self) -> list[tuple[str, str]]:
        """A round: the terms around the MVP but the sweep's, the city's terms around the city, then each
        known barcode across both cities (split while over the cap)."""
        return (
            [(term, place) for term in TERMS for place in MVP if not sweep_job(term, place)]
            + [(term, place) for term in CITY_TERMS for place in CITY]
            + [(code, "wide") for code in self.gtins.searchable()]
        )

    def load_queue(self) -> list[tuple[str, str]]:
        """The searches left from the last runs, without terms or circles dropped since. Terms added since
        join the round (the MVP's first, the city's last); a new round starts when nothing is left. The MVP
        sweep goes in front when it is due."""
        terms = set(TERMS) | set(CITY_TERMS)
        if self.focus:
            return self.focus_queue()
        try:
            saved = json.loads(self.queue_file.read_text("utf-8"))
            jobs = [
                (term, place)
                for term, place in saved["jobs"]
                if (term in terms or term.isdigit()) and circle(place)
            ]
            # Rounds saved before they kept their terms (6 Oct 2026) had every term but the last two.
            planned = saved.get("terms", TERMS[:-2])
            planned_city = saved.get("city_terms", CITY_TERMS)
            # Rounds saved before they kept their city circles (7 Oct 2026) had the first twenty.
            planned_places = saved.get("city_places", list(CITY)[:20])
        except (OSError, ValueError, KeyError, TypeError):
            saved, jobs = {}, []
        if not jobs:
            return self.with_sweep(self.first_queue(), saved.get("swept"))
        added = [
            (term, place)
            for term in TERMS
            for place in MVP
            if term not in planned and not sweep_job(term, place)
        ]
        added_city = [
            (term, place)
            for term in CITY_TERMS
            for place in CITY
            if term not in planned_city or place not in planned_places
        ]
        return self.with_sweep(added + jobs + added_city, saved.get("swept"))

    def focus_queue(self) -> list[tuple[str, str]]:
        """The focus place's searches left, or a new round: every word, then every barcode known by now."""
        assert self.focus is not None
        try:
            saved = json.loads(self.queue_file.read_text("utf-8"))
            jobs = [(t, p) for t, p in saved["jobs"] if (t in TERMS or t.isdigit()) and circle(p)]
            self.focus_round = int(saved.get("round", 0))
        except (OSError, ValueError, KeyError, TypeError):
            jobs = []
        if not jobs:
            self.focus_round += 1
            codes = self.gtins.searchable()
            jobs = [(term, place) for place in self.focus_places for term in [*TERMS, *codes]]
            log.info("focus %s: round %d, %d searches planned", self.focus, self.focus_round, len(jobs))
        return jobs

    def with_sweep(self, queue: list[tuple[str, str]], swept: Any) -> list[tuple[str, str]]:
        """The queue with the MVP sweep in front when the last one started SWEEP_EVERY ago or more (or never),
        in place of any sweep searches still waiting. A sweep cut short by the search cap or a block stays
        in front and finishes first, due or not."""
        try:
            due = self.now - datetime.fromisoformat(str(swept)) >= SWEEP_EVERY
        except (TypeError, ValueError):
            due = True
        if not due:
            self.swept = str(swept)
            return queue
        self.swept = self.now.isoformat(timespec="seconds")
        sweep = [(term, place) for term in TERMS if term in SWEEP_TERMS for place in MVP]
        if sweep:
            log.info("chopp around the MVP first (last time: %s)", swept or "never")
        return sweep + [job for job in queue if not sweep_job(*job)]

    def save_queue(self, queue: list[tuple[str, str]]) -> None:
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        saved = {
            "terms": list(TERMS),
            "city_terms": list(CITY_TERMS),
            "city_places": list(CITY),
            "jobs": [list(job) for job in queue],
            "swept": self.swept,
            "round": self.focus_round,
        }
        self.queue_file.write_text(json.dumps(saved, ensure_ascii=False), "utf-8")

    def search_task(self, term: str, place: str, page: int, cheapest: bool = False) -> Task:
        """A page of sales; `cheapest` asks for the cheapest first, where the shops' single beers are."""
        lat, lng, radius = circle(place) or PLACES[place]
        suffix = ("cheap" if page == 1 else f"a{page}") if cheapest else f"p{page}"
        key = f"{term} @ {place} {suffix}"
        self.searching[key] = (term, place)
        params = {
            "termo": term,
            "horas": HOURS,
            "latitude": lat,
            "longitude": lng,
            "raio": radius,
            "pagina": page,
            "ordenar": "preco.asc" if cheapest else "preco.desc",
        }
        return Task(key, SEARCH_URL, params)

    def last_page(self, term: str, place: str, page: int) -> bool:
        """Results come dearest first: retail packs, then bars, then shops' single units. A page without bar
        deals after one with them, or two in a row without, means the bars are behind us."""
        if page >= self.pages.get((term, place), 0):
            return True
        deals = self.deals_per_page.get((term, place), [])
        return bool(deals) and deals[-1] == 0 and (any(deals[:-1]) or deals[-2:] == [0, 0])

    def osm_query(self) -> str:
        """Bars, pubs and restaurants in the covered cities: the same query as osm_venues."""
        return str(next(iter(self.osm_venues.tasks())).params["data"])

    # Fetching: Overpass for the venues; for searches, the site's session, then one POST per page.

    def fetch(self, task: Task) -> Fetched:
        if task.key == "osm":
            try:
                return self.osm_venues.fetch(task)
            except BlockedError as error:
                # Overpass refusing us is no reason to report Preço da Hora as blocking: a plain failure.
                raise RuntimeError(str(error)) from error
        self.searches += 1
        if self.csrf is None:
            self.csrf = self.open_session()
        try:
            response = self.post(task)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 400 or self.refreshed:
                raise
            # An expired session: one fresh page and one more try, once a run.
            self.refreshed = True
            self.csrf = self.open_session()
            response = self.post(task)
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            got = f"{content_type or 'nothing'} ({len(response.content)} B)"
            raise BlockedError(task.key, f"expected JSON, got {got}")
        return Fetched(task, response.content, content_type)

    def open_session(self) -> str:
        """The page's session cookies and CSRF token; a CAPTCHA key on the page means the run stops."""
        page = self.http.get(SEARCH_URL)
        if re.search(r'id="rc"\s+data-id="[^"]+"', page.text):
            raise BlockedError(SEARCH_URL, "the page has a CAPTCHA switched on")
        token = re.search(r'id="validate"\s+data-id="([^"]+)"', page.text)
        if not token:
            raise BlockedError(SEARCH_URL, "no CSRF token on the page")
        return token.group(1)

    def post(self, task: Task) -> httpx.Response:
        headers = {
            "X-CSRFToken": self.csrf or "",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": SEARCH_URL,
            "Origin": BASE,
        }
        return self.http.post(SEARCH_URL, data=dict(task.params), headers=headers, retries=0)

    # Parsing.

    def parse(self, fetched: Fetched) -> Iterable[Item]:
        if fetched.task.key == "osm":
            self.osm = self.candidates(fetched)
            log.info("%d OpenStreetMap venues around the searched places", len(self.osm))
            self.save_osm(self.osm)
            return []
        data = fetched.json()
        results = data.get("resultado") if isinstance(data, dict) else None
        if (
            results is None
            and isinstance(data, dict)
            and (data.get("codigo") == NO_RESULTS or data.get("totalRegistros") == 0)
        ):
            # No sales for the term around the place ("Nenhum resultado encontrado!").
            results = []
        if not isinstance(results, list):
            raise BlockedError(fetched.task.key, f"unexpected answer: {shape(data)}")
        search = self.searching.pop(fetched.task.key)
        items = list(self.items(results))
        if re.search(r" p\d+$", fetched.task.key):
            self.pages[search] = int(data.get("totalPaginas") or 0)
            self.totals[search] = int(data.get("totalRegistros") or 0)
            bars = sum(
                item.kind == "deal" and item.data.get("venue_ref") not in self.store_refs for item in items
            )
            self.deals_per_page.setdefault(search, []).append(bars)
        log.info(
            "%s: %d sales of %s · %d items",
            fetched.task.key,
            len(results),
            data.get("totalRegistros"),
            len(items),
        )
        return items

    def candidates(self, fetched: Fetched) -> list[Candidate]:
        found = []
        for element in fetched.json().get("elements", []):
            item = self.osm_venues.venue(element)
            if item:
                data = dict(item.data)
                found.append(Candidate(data["name"], data["lat"], data["lng"], data))
        return found

    def save_osm(self, candidates: list[Candidate]) -> None:
        saved = {
            "fetched_on": date.today().isoformat(),
            "query": self.osm_query(),
            "venues": [dict(candidate.data) for candidate in candidates],
        }
        self.osm_cache.parent.mkdir(parents=True, exist_ok=True)
        self.osm_cache.write_text(json.dumps(saved, ensure_ascii=False), "utf-8")

    def cached_osm(self, max_days: int) -> list[Candidate] | None:
        """The last good Overpass answer, if at most `max_days` old (0: from today)."""
        try:
            saved = json.loads(self.osm_cache.read_text("utf-8"))
            fetched_on = date.fromisoformat(saved["fetched_on"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if saved.get("query") != self.osm_query() or (date.today() - fetched_on).days > max_days:
            return None
        return [Candidate(venue["name"], venue["lat"], venue["lng"], venue) for venue in saved["venues"]]

    def items(self, results: Iterable[dict[str, Any]]) -> Iterator[Item]:
        """Two passes over a page: first what the page says about shops (shop prices, sales by the box), then
        a verdict for each bar and its beers."""
        page = []
        for result in results:
            product, receipt = result.get("produto") or {}, result.get("estabelecimento") or {}
            if not str(product.get("ncm") or "").startswith(BEER_NCM):
                self.skipped["not beer (NCM)"] += 1
                continue
            shop = self.shop(receipt)
            serving = self.serving(product, shop) if shop else None
            if shop is None or serving is None:
                continue
            if shop.retail and serving.units == 1:
                day = sale_date(product.get("data")) or date.today()
                self.shop_prices.add(serving_key(serving), float(product["precoUnitario"]), day.isoformat())
            if self.stores and shop.category and serving.units == 1:
                store = self.store_venue(shop)
                if store is not None:
                    if store.ref not in self.announced:
                        self.announced.add(store.ref)
                        yield store.item
                    served = self.bar_priced(shop, serving, product)
                    yield self.deal(store, serving, product, on_site=True if served else None)
            elif shop.candidate:
                page.append((shop, serving, product))
            elif self.bar_priced(shop, serving, product):
                venue = self.serving_venue(shop)
                if venue is not None:
                    if venue.ref not in self.announced:
                        self.announced.add(venue.ref)
                        yield venue.item
                    yield self.deal(venue, serving, product)
        for shop, serving, product in page:
            venue = self.venue(shop, serving, product)
            if venue is None or venue.held:
                self.held.add(shop.cnpj)
            if venue is None:
                continue
            # The venue goes first, so ingest knows it when its first deal arrives.
            if venue.ref not in self.announced:
                self.announced.add(venue.ref)
                if not venue.held:
                    self.book.get(shop.cnpj).sent = True
                yield venue.item
            yield self.deal(venue, serving, product)

    def serving(self, product: dict[str, Any], shop: Shop) -> Serving | None:
        """The beer a sale sells by the unit; a sale by the box, pack or keg is noted against a bar."""
        description = " ".join(str(product.get("descricao") or "").split())
        unit = str(product.get("unidade") or "").upper()
        takeaway = bool(PACK_UNIT.match(unit)) or bool(KEG.search(normalize(description)))
        if not takeaway and not SINGLE_UNIT.match(unit):
            self.skipped[f"sold by the {unit or '?'}"] += 1
            return None
        price = product.get("precoUnitario")
        if not isinstance(price, int | float) or not 0 < price <= 10_000:
            self.skipped["no price"] += 1
            return None
        serving = None
        code = gtin_of(product)
        try:
            # Bars write "HEINEKEN" for their 600 ml bottle; shops' lines without a size stay unread.
            on_site = shop.candidate or (
                shop.company is not None and shop.category is None and not shop.retail
            )
            serving = None if takeaway else self.reader.read(description, assume_bottle=on_site)
            if serving is not None and code and serving.volume_ml is not None:
                self.gtins.vote(code, serving)
        except NotAServing as error:
            unread = error.reason in ("no brand", "no size", "size without a type", "size not in the catalog")
            known = self.gtins.serving(code, self.catalog) if code and unread else None
            if known is not None:
                # The text doesn't say, the barcode does ("CERV HEINEKEN SLEEK" is the 350 ml can).
                serving = known
                self.skipped["read by its barcode"] += 1
            else:
                self.skipped[error.reason] += 1
                takeaway = error.reason == "pack"
                if unread:
                    self.unread[description] += 1
        if serving is not None and serving.volume_ml is None and code and not takeaway:
            # An assumed size (a bar's "HEINEKEN") gives way to what its barcode says.
            serving = self.gtins.serving(code, self.catalog) or serving
        if takeaway:
            if shop.candidate:
                fields = ("cod_nfce", "descricao", "data", "precoUnitario")
                self.book.get(shop.cnpj).add_pack("|".join(str(product.get(name) or "") for name in fields))
            self.skipped["sold by the box, pack or keg"] += 1
            return None
        return serving

    def shop(self, receipt: dict[str, Any]) -> Shop | None:
        """The shop behind a sale, once a run: retail by name (no lookup), or what the CNPJ register says."""
        cnpj = re.sub(r"\D", "", str(receipt.get("cnpj") or ""))
        if len(cnpj) != 14:
            self.skipped["shop without a CNPJ"] += 1
            return None
        if cnpj not in self.shops:
            self.shops[cnpj] = self.new_shop(cnpj, receipt)
        return self.shops[cnpj]

    def new_shop(self, cnpj: str, receipt: dict[str, Any]) -> Shop | None:
        receipt_name = " ".join(str(receipt.get("nomeEstabelecimento") or "").split())
        try:
            lat, lng = float(receipt["latitude"]), float(receipt["longitude"])
        except (KeyError, TypeError, ValueError):
            lat, lng = 0.0, 0.0
        bairro = self.bairros.locate(lat, lng) if lat and lng else None
        if RETAIL.search(normalize(receipt_name)):
            self.skipped["shop: retail by name"] += 1
            category = shop_category(receipt_name, None)
            return Shop(
                cnpj, receipt_name, True, None, bairro, lat, lng, tidy_address(receipt), category=category
            )
        company = self.cnpjs.lookup(cnpj)
        if company is None:
            self.skipped["shop: CNPJ not found"] += 1
            return None
        retail = company.cnae in SUPERMARKETS
        category = (
            shop_category(company.trade_name or receipt_name, company.cnae)
            if company.cnae not in SCOPE
            else None
        )
        shop = Shop(
            cnpj, receipt_name, retail, company, bairro, lat, lng, tidy_address(receipt), category=category
        )
        if not shop.candidate:
            self.skipped["shop: not a bar, restaurant or brewery (CNAE)"] += 1
            return shop
        assert bairro is not None
        names = [name for name in (company.trade_name, receipt_name, company.legal_name) if name]
        match = best_match(names, lat, lng, self.osm or [], ignore=self.place_words(bairro))
        return Shop(cnpj, receipt_name, retail, company, bairro, lat, lng, shop.address, match)

    def venue(self, shop: Shop, serving: Serving, product: dict[str, Any]) -> Venue | None:
        """The shop as a venue when its verdict is "show"; None (held back) otherwise. The verdict is taken
        again with each beer, since prices and sales by the box keep coming in."""
        company, record = shop.company, self.book.get(shop.cnpj)
        assert company is not None and shop.bairro is not None
        price = float(product["precoUnitario"])
        median = self.shop_prices.median(serving_key(serving)) if serving.units == 1 else None
        if median:
            record.ratios[serving_key(serving)] = round(price / median, 2)
        sample = f"{' '.join(str(product.get('descricao') or '').split())} R$ {price:.2f}"
        record.add_sample(f"{sample} ({price / median:.1f}x)" if median else sample)
        name = shop.match.data["name"] if shop.match else display_name(company, shop.receipt_name)
        record.name = str(name or company.trade_name or "")
        record.bairro = shop.bairro.slug
        record.osm = str(shop.match.data["osm_ref"]) if shop.match else ""
        record.seen = date.today().isoformat()
        evidence = Evidence(
            cnpj=shop.cnpj,
            main=company.cnae,
            secondary=company.secondary,
            mei=company.mei,
            names=tuple(name for name in (company.trade_name, company.legal_name, shop.receipt_name) if name),
            osm_kind=str(shop.match.data["kind"]) if shop.match else None,
            packs=len(record.packs),
            ratios=list(record.ratios.values()),
        )
        review = self.review.get(shop.cnpj)
        verdict = judge(evidence, review.decision if review else None)
        if verdict.decision == "show" and not name:
            verdict = Verdict("reject", ("no trade name (one-person business)",))
        record.decision, record.reasons = verdict.decision, list(verdict.reasons)
        if verdict.decision == "review":
            # The AI reviewer decides (#416): the venue goes with the doubts, and waits in the queue.
            self.skipped["shop: review (sent to the AI reviewer)"] += 1
            return self.held_venue(shop, str(name), verdict, record)
        if verdict.decision != "show":
            self.skipped[f"shop: {verdict.decision}"] += 1
            return None
        if shop.cnpj not in self.venues:
            self.venues[shop.cnpj] = self.venue_item(shop, str(name))
        return self.venues[shop.cnpj]

    def held_venue(self, shop: Shop, name: str, verdict: Verdict, record: ShopRecord) -> Venue:
        """A doubtful shop as a venue for the AI reviewer: the doubts and the evidence go with it, and its
        deals name it by its CNPJ, so they wait with it (and stay off an OpenStreetMap venue it matches)."""
        base = self.venue_item(shop, name)
        company = shop.company
        assert company is not None
        evidence = [
            *verdict.reasons,
            f"main activity {company.cnae} ({SCOPE.get(company.cnae or 0, '?')})",
            *(f"sold {sample}" for sample in record.samples[:3]),
        ]
        data = {**base.item.data, "held_back": [text[:200] for text in evidence][:8]}
        ref = f"cnpj:{shop.cnpj}"
        return Venue(ref, base.name, Item("venue", ref, data), held=True)

    def bar_priced(self, shop: Shop, serving: Serving, product: dict[str, Any]) -> bool:
        """At a bar's price: BAR_PRICE_RATIO times the supermarkets' or more (never a supermarket's own)."""
        company = shop.company
        if serving.units != 1 or shop.category == "mercado" or (company and company.cnae in TAKEAWAY_CNAE):
            return False
        price = float(product["precoUnitario"])
        median = self.shop_prices.median(serving_key(serving))
        if median:
            return price >= BAR_PRICE_RATIO * median
        # No shop sells that size (a bar's assumed 600 ml Heineken among shops' cans): compare per litre with
        # the shops' prices of the same brand, any size.
        litre = self.brand_litre_median(serving)
        volume = int(serving.format_code.rsplit("_", 1)[1])
        return bool(litre) and price / (volume / 1000) >= BAR_PRICE_RATIO * litre

    def brand_litre_median(self, serving: Serving) -> float | None:
        """The supermarkets' median price per litre for a brand, across its beers and sizes: its alcohol-free
        beers apart from the others, so neither moves the other's median."""
        if serving.brand is None or self.catalog is None:
            return None
        zero = bool(serving.beer and serving.beer.non_alcoholic)
        products = ({f"brand:{serving.brand.slug}"} if not zero else set()) | {
            f"beer:{beer.slug}"
            for beer in self.catalog.beers
            if beer.brand == serving.brand.slug and beer.non_alcoholic == zero
        }
        litres = [
            price / (int(key.rsplit("_", 1)[1]) / 1000)
            for key, rows in self.shop_prices.prices.items()
            if key.split("|")[0] in products and key.rsplit("_", 1)[-1].isdigit()
            for _, price in rows
        ]
        return statistics.median(litres) if len(litres) >= SHOP_PRICE_MIN else None

    def serving_venue(self, shop: Shop) -> Venue | None:
        """A shop outside the bars' activities that serves beer at bar prices, as a restaurant, once a run:
        the OpenStreetMap venue when name and place match. Left out when the owner hid it, or without a trade
        name."""
        if shop.cnpj in self.venues:
            return self.venues[shop.cnpj]
        review = self.review.get(shop.cnpj)
        company, bairro = shop.company, shop.bairro
        name = display_name(company, shop.receipt_name) if company else None
        venue = None
        if review and review.decision == "hide":
            self.skipped["serves at bar prices: hidden by the owner"] += 1
        elif not (company and company.active and bairro and name and shop.lat and shop.lng):
            self.skipped["serves at bar prices: no trade name or place"] += 1
        else:
            names = [n for n in (company.trade_name, shop.receipt_name, company.legal_name) if n]
            match = best_match(names, shop.lat, shop.lng, self.osm or [], ignore=self.place_words(bairro))
            if match:
                data = {**match.data, "cnpj": shop.cnpj}
                venue = Venue(
                    f"osm:{data['osm_ref']}", str(data["name"]), Item("venue", f"cnpj:{shop.cnpj}", data)
                )
            else:
                data = {
                    "city": bairro.city,
                    "name": name,
                    "lat": round(shop.lat, 6),
                    "lng": round(shop.lng, 6),
                    "kind": "restaurante",
                    "on_site": True,
                    "address": shop.address,
                    "neighbourhood": bairro.slug,
                    "osm_ref": None,
                    "cnpj": shop.cnpj,
                    "instagram": None,
                    "cardapioweb_slug": None,
                    "opening_hours": None,
                    "attributes": [],
                }
                venue = Venue(f"cnpj:{shop.cnpj}", name, Item("venue", f"cnpj:{shop.cnpj}", data))
            self.skipped["shop serving at bar prices (listed as a restaurant)"] += 1
            log.info("listed as a restaurant (bar prices): %s, %s", name, bairro.slug)
        self.venues[shop.cnpj] = venue
        return venue

    def store_venue(self, shop: Shop) -> Venue | None:
        """A supermarket or depósito as a venue for its own category (not for drinking on site), once a run.
        One-person businesses without a trade name are left out: their legal name is their owner's name."""
        if shop.cnpj in self.venues:
            return self.venues[shop.cnpj]
        venue = None
        name = display_name(shop.company, shop.receipt_name) if shop.company else tidy_name(shop.receipt_name)
        if name and shop.bairro and shop.lat and shop.lng:
            data = {
                "city": shop.bairro.city,
                "name": name,
                "lat": round(shop.lat, 6),
                "lng": round(shop.lng, 6),
                "kind": "deposito" if shop.category == "deposito" else "outro",
                "on_site": False,
                "address": shop.address,
                "neighbourhood": shop.bairro.slug,
                "osm_ref": None,
                "cnpj": shop.cnpj,
                "instagram": None,
                "cardapioweb_slug": None,
                "opening_hours": None,
                "attributes": [],
            }
            venue = Venue(f"cnpj:{shop.cnpj}", name, Item("venue", f"cnpj:{shop.cnpj}", data))
            self.store_refs.add(venue.ref)
        else:
            self.skipped["store: no name or place"] += 1
        self.venues[shop.cnpj] = venue
        return venue

    def venue_item(self, shop: Shop, name: str) -> Venue:
        company, bairro = shop.company, shop.bairro
        assert company is not None and bairro is not None
        if shop.match:
            data = {**shop.match.data, "cnpj": shop.cnpj}
            return Venue(
                f"osm:{data['osm_ref']}", str(data["name"]), Item("venue", f"cnpj:{shop.cnpj}", data)
            )
        kind = SCOPE[company.cnae or 0]
        data = {
            "city": bairro.city,
            "name": name,
            "lat": round(shop.lat, 6),
            "lng": round(shop.lng, 6),
            "kind": kind if kind != "bar" else venue_kind("bar", name, {}),
            "on_site": True,
            "address": shop.address,
            "neighbourhood": bairro.slug,
            "osm_ref": None,
            "cnpj": shop.cnpj,
            "instagram": None,
            "cardapioweb_slug": None,
            "opening_hours": None,
            "attributes": [],
        }
        return Venue(f"cnpj:{shop.cnpj}", name, Item("venue", f"cnpj:{shop.cnpj}", data))

    @staticmethod
    def place_words(bairro: Bairro) -> set[str]:
        """Place names in a shop's name say nothing about which shop it is ("Boteco X Rio Vermelho")."""
        names = [bairro.name, *PLACES, "salvador", "lauro de freitas"]
        return {word for name in names for word in re.findall(r"[a-z0-9]+", normalize(name))}

    def write_github_summary(self) -> None:
        """The bars held back this run, with their evidence, on the run's GitHub page (when run there)."""
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        table = summary_table(self.book.records, self.held)
        if not path or not table:
            return
        intro = (
            "### Preço da Hora: bars held back this run\n\n"
            "Verdict review: sent with its doubts to the AI reviewer, which keeps or hides it by the "
            "review threshold (its score and decision are in the audit log); its prices wait for it. "
            "Verdict reject: left out. `data/seed/venue-review.csv` still overrides both. "
            "“Sent before” means it may already be on the map.\n\n"
        )
        with Path(path).open("a", encoding="utf-8") as summary:
            summary.write(intro + table + "\n")

    def deal(
        self, venue: Venue, serving: Serving, product: dict[str, Any], on_site: bool | None = None
    ) -> Item:
        """A sale as a deal; `on_site` when it is drunk there unlike the venue (a depósito's table beer)."""
        description = " ".join(str(product.get("descricao") or "").split())
        observed = sale_date(product.get("data"))
        data = {
            "venue_name": venue.name,
            "venue_ref": venue.ref,
            "beer": serving.beer_alias,
            "brand": serving.brand_alias,
            "format": serving.format,
            "volume_ml": serving.volume_ml,
            "units": serving.units,
            "packaging": serving.packaging,
            "on_site": on_site,
            "price": round(float(product["precoUnitario"]), 2),
            "currency": "BRL",
            "kind": "regular",
            "open_bar_minutes": None,
            "schedules": [],
            "conditions": None,
            "service_charge_pct": None,
            "couvert": None,
            "valid_from": None,
            "valid_until": None,
            "observed_on": observed.isoformat() if observed else None,
            "confidence": 0.9,
            "notes": f"Preço da Hora (NFC-e): {description}"[:500],
        }
        place = "" if on_site is None else ("|on_site" if on_site else "|takeaway")
        return Item("deal", f"{venue.ref}|{serving.key}{place}", data)

    def log_summary(self) -> None:
        venues = [venue for venue in self.venues.values() if venue]
        on_osm = sum(venue.ref.startswith("osm:") for venue in venues)
        log.info(
            "%d searches · %d shops seen · %d venues (%d on OpenStreetMap) · %d held back · %d CNPJ lookups",
            self.searches,
            len(self.shops),
            len(venues),
            on_osm,
            len(self.held),
            self.cnpjs.lookups,
        )
        if self.capped:
            log.info(
                "still over the cap after both ends (sales out of reach): %s",
                ", ".join(
                    f"{term} @ {place} {count}" for (term, place), count in sorted(self.capped.items())
                ),
            )
        self.write_github_summary()
        for reason, count in self.skipped.most_common():
            log.info("skipped %d: %s", count, reason)
        for description, count in self.unread.most_common(60):
            log.info("unread (%d): %s", count, description)
