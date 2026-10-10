"""Beer servings in product descriptions, as bars and shops write them on receipts and menus.

"CERV BRAHMA LT 350ML" → Brahma, lata 350 ml · "BALDE C/5 HEINEKEN LN" → Heineken, a bucket of 5 long necks
(size not written) · "CHOPP HEINEKEN 500ML" → Heineken, chopp 500 ml · "BRAHMA CHOPP 600" → the beer Brahma
Chopp in a 600 ml bottle.

The keywords follow parseServing in @cb/domain (packages/domain/src/parse.ts), which reads the same kind of
line in the Worker. This reader is stricter because what it accepts may publish by itself: retail packs and
kegs, zero-alcohol beers, two brands in one line, promotions, buckets of unknown size and sizes the catalog
doesn't have come back as a reason (NotAServing) instead of a guess.
"""

import re
import unicodedata
from dataclasses import dataclass

from ..models import Catalog, CatalogBeer, CatalogBrand


class NotAServing(ValueError):
    """Why a description can't become a deal: "pack", "zero alcohol", "no brand", "no size"…"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Serving:
    brand: CatalogBrand
    #: The catalog alias found, sent as the deal's brand so the ingest API matches it exactly.
    brand_alias: str
    beer: CatalogBeer | None
    beer_alias: str | None
    #: Format type: lata, long_neck, garrafa, litrao, chopp, torre, pint…
    format: str
    #: The catalog format, the assumed size included.
    format_code: str
    #: Volume of one unit; None when the description doesn't say and the format's usual size is assumed.
    volume_ml: int | None
    units: int = 1
    packaging: str = "single"

    @property
    def key(self) -> str:
        """What is sold, for keys: "brand:heineken|long_neck_330|1|single"."""
        product = f"beer:{self.beer.slug}" if self.beer else f"brand:{self.brand.slug}"
        return f"{product}|{self.format_code}|{self.units}|{self.packaging}"


#: How receipts shorten things, as whole words: "CERV HEINEK LN" → "cerveja heineken ln".
ABBREVIATIONS = {
    "cerv": "cerveja",
    "cervej": "cerveja",
    "crv": "cerveja",
    "heinek": "heineken",
    "heinekem": "heineken",
    "heineke": "heineken",
    "budw": "budweiser",
    "budweis": "budweiser",
    "stell": "stella",
    "orig": "original",
    "origin": "original",
    "antarct": "antarctica",
    "antart": "antarctica",
    "brahm": "brahma",
    "amst": "amstel",
    "eisenb": "eisenbahn",
    "bohem": "bohemia",
    "itaip": "itaipava",
}
#: Format keywords, strongest first (a "torre de chopp" is a torre), as in @cb/domain.
TYPE_KEYWORDS = (
    ("torre", re.compile(r" torres? ")),
    ("chopp", re.compile(r" (chopps?|chopes?|choppe|chop|tulipa|caneca|draught|draft) ")),
    ("litrao", re.compile(r" litrao ")),
    ("long_neck", re.compile(r" (long ?necks?|longnecks?|lneck|lnk|ln|l n|long) ")),
    ("garrafa", re.compile(r" (garrafas?|garraf|gfa|grf|gf|litrinhos?) ")),
    ("lata", re.compile(r" (latao|latinhas?|latas?|lta|lt|cans?) ")),
    ("pint", re.compile(r" pints? ")),
)
#: Types sold in one standard size, so the volume isn't a guess (as in @cb/domain).
FIXED_VOLUME = {"garrafa": 600, "litrao": 1000}
#: Brands whose long neck comes in one size in Brazil, so "HEINEKEN LONG NECK" says 330 ml without writing it.
LONG_NECK_ML = {
    "heineken": 330,
    "budweiser": 330,
    "corona": 330,
    "michelob-ultra": 330,
    "spaten": 355,
    "eisenbahn": 355,
}
#: Sizes that name their type on their own ("SKOL 600"); chopp and pint sizes need their word. A bare 300 ml
#: is the returnable "litrinho" bottle ("SKOL PILSEN 300ML"), as in @cb/domain: draught always says chopp.
TYPE_OF_BARE_VOLUME = {269: "lata", 275: "long_neck", 300: "garrafa", 330: "long_neck", 350: "lata"}
TYPE_OF_BARE_VOLUME |= {355: "long_neck", 473: "lata", 600: "garrafa", 1000: "litrao"}

_VOLUME = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?) ?(ml|l|lt|lts|ltr|litros?)(?![a-z])")
_BARE_NUMBER = re.compile(r"(?<![\d.,])(\d{3,4})(?![\d.,]| ?(?:x|un|und|unid)\b)")
_ZERO = re.compile(r" (zero|0 0|00|sem alcool|s alc\w*|z alc\w*|n alc\w*|s alcool) ")
_PROMOTION = re.compile(
    r" (promo\w*|happy|hh|dobro|dobradinha|2x1|2 x 1|2 por 1|leve|compre|combo|cortesia) "
)
_RETAIL = re.compile(
    r" (cx|cxa|caixa|pack|pck|fardo|fd|pct|pacote|kit|engradado|growler|refil|barril|keg|display|bag) "
)
_COUNT = re.compile(r" (?:c|com|cx|x) ?(\d{1,2}) | (\d{1,2}) ?(?:x|un|und|unid|unidades|garrafas|latas) ")
_MULTIPLY = re.compile(r"(?<![\d.,])\d{1,2} ?x ?\d{2,4}")
_BALDE_COUNT = re.compile(
    r"balde (?:c/ ?|com |de |c )?(\d{1,2})\b"
    r"|\b(\d{1,2}) ?(?:un|und|unid|unidades|cervejas|long ?necks?|ln|latas?|garrafas?)\b"
)


def plain(text: str) -> str:
    """Lowercase and accent-free, keeping digits and the separators sizes need: "2,5L" → "2,5l"."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9.,/]+", " ", stripped).split())


def words(text: str) -> str:
    """Whole words, padded for searches, abbreviations spelt out: "l.n. heinek" → " l n heineken "."""
    tokens = [ABBREVIATIONS.get(token, token) for token in re.findall(r"[a-z0-9]+", text)]
    return f" {' '.join(tokens)} "


def _volume(text: str) -> tuple[int | None, str]:
    """The written size in ml ("350ML", "2,5L", "1 LT") and the text without it."""
    match = _VOLUME.search(text)
    if not match:
        return None, text
    value = float(match.group(1).replace(",", "."))
    volume = round(value if match.group(2) == "ml" else value * 1000)
    return volume, text[: match.start()] + " " + text[match.end() :]


class ServingReader:
    """Reads descriptions against one catalog (its aliases sorted once)."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        # Longest first, so "brahma duplo malte" beats "brahma" and "stella artois" beats "stella".
        self.beers = sorted(
            ((words(alias), beer) for beer in catalog.beers for alias in beer.aliases),
            key=lambda pair: -len(pair[0]),
        )
        self.brands = sorted(
            ((words(alias), brand) for brand in catalog.brands for alias in brand.aliases),
            key=lambda pair: -len(pair[0]),
        )
        self.brand_by_slug = {brand.slug: brand for brand in catalog.brands}

    def read(self, description: str) -> Serving:
        """The serving a description sells, or NotAServing with the reason."""
        text = plain(description)
        padded = words(text)
        if _ZERO.search(padded):
            raise NotAServing("zero alcohol")
        if _PROMOTION.search(padded):
            raise NotAServing("promotion")
        if _RETAIL.search(padded):
            raise NotAServing("pack")

        volume, rest = _volume(text)
        brand, brand_alias, beer, beer_alias, rest_words = self._brand(words(rest))
        if beer is not None and beer.non_alcoholic:
            raise NotAServing("zero alcohol")

        units, packaging = 1, "single"
        if " balde " in padded:
            count = _BALDE_COUNT.search(text)
            units = int(next(group for group in count.groups() if group)) if count else 0
            if not 2 <= units <= 12:
                raise NotAServing("bucket size")
            packaging = "balde"
        elif _COUNT.search(padded) or _MULTIPLY.search(text):
            raise NotAServing("pack")

        kind = next((name for name, pattern in TYPE_KEYWORDS if pattern.search(rest_words)), None)
        if volume is None:
            sizes = (
                {fmt.volume_ml for fmt in self.catalog.formats if fmt.type == kind}
                if kind
                else set(TYPE_OF_BARE_VOLUME)
            )
            volume = next((int(n) for n in _BARE_NUMBER.findall(rest) if int(n) in sizes), None)
        if kind == "lata" and volume is None and " latao " in rest_words:
            volume = 473
        if kind == "garrafa" and volume is None and re.search(r" litrinhos? ", rest_words):
            volume = 300
        if kind == "garrafa" and volume == 1000:
            kind = "litrao"
        if kind is None and volume is not None:
            kind = TYPE_OF_BARE_VOLUME.get(volume)
            if kind is None:
                raise NotAServing("size without a type")
        if kind is None:
            raise NotAServing("no size")
        if kind == "torre":
            packaging = "torre"
        if volume is None:
            volume = LONG_NECK_ML.get(brand.slug) if kind == "long_neck" else FIXED_VOLUME.get(kind)

        size = volume or getattr(self.catalog.default_volume_ml, kind, None)
        code = next(
            (fmt.code for fmt in self.catalog.formats if fmt.type == kind and fmt.volume_ml == size), None
        )
        if code is None:
            raise NotAServing("size not in the catalog")
        return Serving(brand, brand_alias, beer, beer_alias, kind, code, volume, units, packaging)

    def _brand(self, text: str) -> tuple[CatalogBrand, str, CatalogBeer | None, str | None, str]:
        """The one brand (and beer, when its name is there) in `text`, and `text` without them."""
        beer, beer_alias = None, None
        for alias, candidate in self.beers:
            if alias in text:
                beer, beer_alias = candidate, alias.strip()
                text = text.replace(alias, " ", 1)
                break
        found: dict[str, tuple[CatalogBrand, str]] = {}
        for alias, brand in self.brands:
            if alias in text:
                found.setdefault(brand.slug, (brand, alias.strip()))
                text = text.replace(alias, " ")
        if beer is not None:
            if set(found) - {beer.brand}:
                raise NotAServing("two brands")
            own = self.brand_by_slug[beer.brand]
            brand, brand_alias = found.get(beer.brand, (own, own.aliases[0]))
            return brand, brand_alias, beer, beer_alias, text
        if len(found) > 1:
            raise NotAServing("two brands")
        if not found:
            raise NotAServing("no brand")
        [(brand, brand_alias)] = found.values()
        return brand, brand_alias, None, None, text


def read_serving(description: str, catalog: Catalog) -> Serving:
    """One description against a catalog; build a ServingReader to read many."""
    return ServingReader(catalog).read(description)
