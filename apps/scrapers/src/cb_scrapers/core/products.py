"""Beer servings in product descriptions, as bars and shops write them on receipts and menus.

"CERV BRAHMA LT 350ML" → Brahma, lata 350 ml · "BALDE C/5 HEINEKEN LN" → Heineken, a bucket of 5 long necks
(size not written) · "CHOPP HEINEKEN 500ML" → Heineken, chopp 500 ml · "BRAHMA CHOPP 600" → the beer Brahma
Chopp in a 600 ml bottle · "CHOPP PILSEN 300ML" → a chopp 300 ml of no named brand (only with a written size).

The keywords follow parseServing in @cb/domain (packages/domain/src/parse.ts), which reads the same kind of
line in the Worker. This reader is stricter because what it accepts may publish by itself: retail packs and
kegs, two brands in one line, promotions, buckets of unknown size and sizes the catalog doesn't have come
back as a reason (NotAServing) instead of a guess, and so do the beer brands' other drinks that some shops
file under beer ("REFRI ANTARCTICA GUARANA PET 2LT", "SKOL BEATS SENSES LT 269ML"). Alcohol-free beers
("HEINEKEN 0.0 LN", "CERV BRAHMA ZERO LT", "ITAIPAVA 0% 350ML") are their brand's alcohol-free beer in the
catalog (non_alcoholic), never its lager; one the catalog lacks comes back as "zero alcohol: not in the
catalog".
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
    #: None for a beer of no named brand ("CHOPP 300ML", "CERVEJA 600ML"), listed as just "Cerveja".
    brand: CatalogBrand | None
    #: The catalog alias found, sent as the deal's brand so the ingest API matches it exactly.
    brand_alias: str | None
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
        product = (
            f"beer:{self.beer.slug}" if self.beer else f"brand:{self.brand.slug}" if self.brand else "generic"
        )
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
    "antarc": "antarctica",
    "antarct": "antarctica",
    "antarctic": "antarctica",
    "antart": "antarctica",
    "antartic": "antarctica",
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
#: 550 ml is Itaipava's tall can ("CERVEJA ITAIPAVA 550ML"), 1 litre the returnable litrão ("CERV SKOL 1L").
TYPE_OF_BARE_VOLUME = {269: "lata", 275: "long_neck", 300: "garrafa", 330: "long_neck", 350: "lata"}
TYPE_OF_BARE_VOLUME |= {355: "long_neck", 473: "lata", 550: "lata", 600: "garrafa", 1000: "litrao"}

_VOLUME = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?) ?(ml|l|lt|lts|ltr|litros?)(?![a-z])")
_BARE_NUMBER = re.compile(r"(?<![\d.,])(\d{3,4})(?![\d.,]| ?(?:x|un|und|unid)\b)")
#: A beer without a brand: only these words, with a written size, make a serving of no named brand.
_GENERIC = re.compile(r" (cervejas?|chopps?|chopes?|choppe|chop) ")
#: Alcohol-free, as receipts write it (in words): "0 0", "zero", "cero", "sem alcool", "s alc"…
#: Antarctica's "Sub Zero" has alcohol. isAlcoholFree in @cb/domain (packages/domain/src/parse.ts) reads the
#: same words.
_ZERO = re.compile(r"(?<! sub) (zero|cero|0 0|00|sem alc\w*|s alc\w*|z alc\w*|n alc\w*|nao alc\w*) ")
#: "0,0" and "0.0" in plain text, left out when reading the size.
_ZERO_MARK = re.compile(r"(?<![\d.,])0[.,]0(?![\d.,]*\d)")
#: "0%" and "0,0%", before the % sign is lost.
_ZERO_PERCENT = re.compile(r"(?<![\d.,])0(?:[.,]0)? ?%")
_PROMOTION = re.compile(
    r" (promo\w*|happy|hh|dobro|dobradinha|2x1|2 x 1|2 por 1|leve|compre|combo|cortesia) "
)
#: The beer brands' other drinks, which some shops file under beer's NCM and menus list next to beers:
#: Antarctica's soft drinks ("REFRI ANTARCTICA GUARANA PET 2LT", Soda, Tônica) and Skol Beats (flavoured
#: drinks). Antarctica's Subzero is beer.
_NOT_BEER = re.compile(r" (guarana|refri|refrigerantes?|soda|tonica|beats) ")
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
        # A brand whose own alias names another brand ("antarctica original") carries its maker's name, which
        # is no second brand on its lines, in either order ("CERVEJA ORIGINAL ANTARCTICA 600ML" is Original).
        self.makers = {
            brand.slug: {
                other.slug
                for other in catalog.brands
                for name in other.aliases
                for own in brand.aliases
                if other.slug != brand.slug and words(name) in words(own)
            }
            for brand in catalog.brands
        }

    def read(self, description: str, assume_bottle: bool = False) -> Serving:
        """The serving a description sells, or NotAServing with the reason. With `assume_bottle` (a bar's
        receipt), a named beer with neither size nor type ("HEINEKEN", "CERVEJA AMSTEL") is the 600 ml bottle
        bars sell by default, with its volume left as assumed (None), so the price waits for review."""
        text = plain(description)
        padded = words(text)
        zero = bool(_ZERO.search(padded) or _ZERO_PERCENT.search(description))
        if _NOT_BEER.search(padded):
            raise NotAServing("not beer")
        if _PROMOTION.search(padded):
            raise NotAServing("promotion")
        if _RETAIL.search(padded):
            raise NotAServing("pack")

        # "HEINEKEN 0,0 LT 350ML": the 0,0 is no size.
        volume, rest = _volume(_ZERO_MARK.sub(" ", text) if zero else text)
        brand, brand_alias, beer, beer_alias, rest_words = self._brand(words(rest), generic=True)
        if zero and (beer is None or not beer.non_alcoholic):
            beer, beer_alias = self._alcohol_free(brand)

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
        assumed = False
        if kind is None and assume_bottle and brand is not None:
            kind, assumed = "garrafa", True
        if kind is None:
            raise NotAServing("no size")
        if brand is None and volume is None:
            # A beer of no named brand needs its size written: nothing about it is in the catalog.
            raise NotAServing("no brand")
        if kind == "torre":
            packaging = "torre"
        if volume is None and not assumed:
            volume = LONG_NECK_ML.get(brand.slug) if kind == "long_neck" and brand else FIXED_VOLUME.get(kind)

        size = volume or getattr(self.catalog.default_volume_ml, kind, None)
        code = next(
            (fmt.code for fmt in self.catalog.formats if fmt.type == kind and fmt.volume_ml == size), None
        )
        if code is None:
            raise NotAServing("size not in the catalog")
        return Serving(brand, brand_alias, beer, beer_alias, kind, code, volume, units, packaging)

    def _alcohol_free(self, brand: CatalogBrand | None) -> tuple[CatalogBeer, str]:
        """The brand's one alcohol-free beer and its first alias ("BRAHMA CHOPP ZERO" is Brahma Zero)."""
        if brand is None:
            raise NotAServing("zero alcohol: no brand")
        found = [beer for beer in self.catalog.beers if beer.brand == brand.slug and beer.non_alcoholic]
        if len(found) != 1:
            raise NotAServing("zero alcohol: not in the catalog")
        return found[0], found[0].aliases[0]

    def _brand(
        self, text: str, generic: bool = False
    ) -> tuple[CatalogBrand | None, str | None, CatalogBeer | None, str | None, str]:
        """The one brand (and beer, when its name is there) in `text`, and `text` without them. With
        `generic`, a line that only says cerveja or chopp has no brand (None) instead of raising."""
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
        named = {beer.brand} if beer is not None else set(found)
        makers = {maker for slug in named for maker in self.makers.get(slug, ())}
        found = {slug: pair for slug, pair in found.items() if slug not in makers}
        if beer is not None:
            if set(found) - {beer.brand}:
                raise NotAServing("two brands")
            own = self.brand_by_slug[beer.brand]
            brand, brand_alias = found.get(beer.brand, (own, own.aliases[0]))
            return brand, brand_alias, beer, beer_alias, text
        if len(found) > 1:
            raise NotAServing("two brands")
        if not found:
            if generic and _GENERIC.search(text):
                return None, None, None, None, text
            raise NotAServing("no brand")
        [(brand, brand_alias)] = found.values()
        return brand, brand_alias, None, None, text


def read_serving(description: str, catalog: Catalog) -> Serving:
    """One description against a catalog; build a ServingReader to read many."""
    return ServingReader(catalog).read(description)
