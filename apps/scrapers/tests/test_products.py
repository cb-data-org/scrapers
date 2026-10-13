import pytest

from cb_scrapers.core.catalog import committed_catalog
from cb_scrapers.core.products import NotAServing, ServingReader, plain, read_serving, words

READER = ServingReader(committed_catalog())


@pytest.mark.parametrize(
    ("description", "brand", "beer", "code", "volume", "units", "packaging"),
    [
        ("CHOPP BRAHMA 300ML", "brahma", None, "chopp_300", 300, 1, "single"),
        ("CHOPP BRAHMA 300", "brahma", None, "chopp_300", 300, 1, "single"),
        ("CHOPP HEINEKEN 0,5L", "heineken", None, "chopp_500", 500, 1, "single"),
        # Brahma Chopp is a beer; "600" makes it a bottle, not a draught.
        ("BRAHMA CHOPP 600ML", "brahma", "brahma-chopp", "garrafa_600", 600, 1, "single"),
        ("CERVEJA ORIGINAL 600ML", "original", "antarctica-original", "garrafa_600", 600, 1, "single"),
        ("CERV ORIG 600", "original", "antarctica-original", "garrafa_600", 600, 1, "single"),
        ("ORIGINAL 600", "original", None, "garrafa_600", 600, 1, "single"),
        ("SKOL 600 ML", "skol", None, "garrafa_600", 600, 1, "single"),
        ("SKOL LT 350ML", "skol", None, "lata_350", 350, 1, "single"),
        ("SPATEN 350", "spaten", None, "lata_350", 350, 1, "single"),
        ("AMSTEL LATA 269ML", "amstel", None, "lata_269", 269, 1, "single"),
        ("LATAO SKOL 473ML", "skol", None, "lata_473", 473, 1, "single"),
        ("SKOL LATAO", "skol", None, "lata_473", 473, 1, "single"),
        ("CERV HEINEK LN 330", "heineken", None, "long_neck_330", 330, 1, "single"),
        ("HEINEKEN 330", "heineken", None, "long_neck_330", 330, 1, "single"),
        ("STELLA ARTOIS 275ML", "stella-artois", None, "long_neck_275", 275, 1, "single"),
        ("CORONA EXTRA LN 330ML", "corona", "corona-extra", "long_neck_330", 330, 1, "single"),
        ("CERVEJA SPATEN LN 355ML", "spaten", None, "long_neck_355", 355, 1, "single"),
        # 350 ml chopps and long necks are common in Brazil: their own formats (bare 350 stays a can).
        ("CHOPP BRAHMA 350ML", "brahma", None, "chopp_350", 350, 1, "single"),
        ("CHOPP BRAHMA 350", "brahma", None, "chopp_350", 350, 1, "single"),
        ("HEINEKEN LN 350ML", "heineken", None, "long_neck_350", 350, 1, "single"),
        ("BRAHMA DUPLO MALTE LATA 350ML", "brahma", "brahma-duplo-malte", "lata_350", 350, 1, "single"),
        ("BRAHMA 1L", "brahma", None, "litrao_1000", 1000, 1, "single"),
        # The returnable 300 ml bottle (litrinho), not a 300 ml chopp (#313).
        ("CERV BRAHMA GF 300ML", "brahma", None, "garrafa_300", 300, 1, "single"),
        (
            "BRAHMA CHOPP (300ML) (APENAS O LIQUIDO)",
            "brahma",
            "brahma-chopp",
            "garrafa_300",
            300,
            1,
            "single",
        ),
        ("SKOL PILSEN 300ML", "skol", "skol-pilsen", "garrafa_300", 300, 1, "single"),
        ("CERV BRAHMA LITRINHO", "brahma", None, "garrafa_300", 300, 1, "single"),
        # Craft chopps (#313).
        ("CHOPP DOGMA PILSEN 330ML", "dogma", None, "chopp_330", 330, 1, "single"),
        ("CHOPP CROMA RESIDENT IPA 473ML", "croma", None, "chopp_473", 473, 1, "single"),
        ("CHOPP IPA PROA 300ML", "proa", None, "chopp_300", 300, 1, "single"),
        ("CHOPP DOKTOR BRAU PSICOTIPA 500ML", "doktor-brau", None, "chopp_500", 500, 1, "single"),
        ("CHOPP MOONDRI YEAST MAGIC 2 330ML", "moondri", None, "chopp_330", 330, 1, "single"),
        ("BRAHMA GARRAFA 1L", "brahma", None, "litrao_1000", 1000, 1, "single"),
        ("TORRE CHOPP BRAHMA 2,5L", "brahma", None, "torre_2500", 2500, 1, "torre"),
        # The brand's only long neck size.
        ("HEINEKEN LONG NECK", "heineken", None, "long_neck_330", 330, 1, "single"),
        ("BALDE C/5 HEINEKEN LN", "heineken", None, "long_neck_330", 330, 5, "balde"),
        ("SPATEN LONGNECK", "spaten", None, "long_neck_355", 355, 1, "single"),
        # No size written: the default one (a long neck is 355 ml) is assumed and volume stays None.
        ("STELLA ARTOIS LONG NECK", "stella-artois", None, "long_neck_355", None, 1, "single"),
        ("CHOPP HEINEKEN", "heineken", None, "chopp_300", None, 1, "single"),
        ("BALDE C/5 STELLA ARTOIS LN", "stella-artois", None, "long_neck_355", None, 5, "balde"),
        ("BUDWEISER LONG NECK", "budweiser", None, "long_neck_330", 330, 1, "single"),
        ("BALDE HEINEKEN LN 330ML 6UN", "heineken", None, "long_neck_330", 330, 6, "balde"),
        # No named brand, but a written size: listed as just "Cerveja".
        ("CHOPP PILSEN 300ML", None, None, "chopp_300", 300, 1, "single"),
        ("CHOPP DA CASA 500ML", None, None, "chopp_500", 500, 1, "single"),
        ("CERVEJA 600ML", None, None, "garrafa_600", 600, 1, "single"),
        ("CERV LATA 350", None, None, "lata_350", 350, 1, "single"),
        # Alcohol-free: the brand's alcohol-free beer, never its lager (owner, 12 Oct 2026).
        ("CERV LN HEINEKEN Z ALC 330ML", "heineken", "heineken-zero", "long_neck_330", 330, 1, "single"),
        ("HEINEKEN 0,0 LN", "heineken", "heineken-zero", "long_neck_330", 330, 1, "single"),
        ("HEINEKEN 0.0 LT 350ML", "heineken", "heineken-zero", "lata_350", 350, 1, "single"),
        ("BUD ZERO 330ML", "budweiser", "budweiser-zero", "long_neck_330", 330, 1, "single"),
        ("CERV BRAHMA CHOPP ZERO LT 350ML", "brahma", "brahma-zero", "lata_350", 350, 1, "single"),
        ("ITAIPAVA 0% LATA 350ML", "itaipava", "itaipava-zero", "lata_350", 350, 1, "single"),
        ("CORONA CERO LN 330ML", "corona", "corona-cero", "long_neck_330", 330, 1, "single"),
        ("CERV AMSTEL S/ALCOOL LT 350ML", "amstel", "amstel-zero", "lata_350", 350, 1, "single"),
        ("CERVEJA BAVARIA SEM ALCOOL 350ML", "bavaria", "bavaria-zero", "lata_350", 350, 1, "single"),
        # Antarctica's Sub Zero and Stella's Pure Gold have alcohol.
        ("CERV ANTARCTICA SUB ZERO LT 350ML", "antarctica", None, "lata_350", 350, 1, "single"),
        ("STELLA ARTOIS PURE GOLD LN 330ML", "stella-artois", None, "long_neck_330", 330, 1, "single"),
    ],
)
def test_reads_servings(
    description: str,
    brand: str | None,
    beer: str | None,
    code: str,
    volume: int | None,
    units: int,
    packaging: str,
) -> None:
    serving = READER.read(description)
    slugs = (serving.brand.slug if serving.brand else None, serving.beer.slug if serving.beer else None)
    assert slugs == (brand, beer)
    assert (serving.format_code, serving.volume_ml, serving.units, serving.packaging) == (
        code,
        volume,
        units,
        packaging,
    )


@pytest.mark.parametrize(
    ("description", "reason"),
    [
        ("CERV SKOL ZERO LT 350ML", "zero alcohol: not in the catalog"),
        ("CERVEJA ZERO 350ML", "zero alcohol: no brand"),
        ("HEINEKEN LN 330ML HAPPY HOUR", "promotion"),
        ("CX CERV SKOL LT 350ML C/12", "pack"),
        ("CERV HEINEKEN 12X350ML", "pack"),
        ("SKOL LATA 350ML 6 UN", "pack"),
        ("HEINEKEN CHOPE 5L", "size not in the catalog"),
        # A litre of chopp is likely a growler to take away: skipped on purpose (#313).
        ("CHOPP HOP MUNDI CATHEDRAL COVE 1L", "size not in the catalog"),
        ("BUDWEISER 343ML", "size without a type"),
        # No brand and no written size: nothing to go on.
        ("CHOPP PILSEN", "no brand"),
        ("CERVEJA GARRAFA", "no brand"),
        ("REFRIGERANTE LATA 350ML", "no brand"),
        ("HEINEKEN", "no size"),
        ("BALDE HEINEKEN", "bucket size"),
        ("HEINEKEN AMSTEL BALDE C/6", "two brands"),
    ],
)
def test_explains_what_it_leaves_out(description: str, reason: str) -> None:
    with pytest.raises(NotAServing) as error:
        READER.read(description)
    assert error.value.reason == reason


def test_sends_aliases_the_ingest_api_knows() -> None:
    serving = read_serving("CERV ORIG 600", committed_catalog())
    catalog = committed_catalog()
    assert (
        serving.beer_alias in next(beer for beer in catalog.beers if beer.slug == serving.beer.slug).aliases
    )
    assert serving.brand_alias in next(b for b in catalog.brands if b.slug == serving.brand.slug).aliases
    assert serving.key == "beer:antarctica-original|garrafa_600|1|single"


def test_a_beer_of_no_named_brand_sends_none() -> None:
    serving = READER.read("CHOPP PILSEN 300ML")
    assert (serving.brand_alias, serving.beer_alias) == (None, None)
    assert serving.key == "generic|chopp_300|1|single"


def test_text_forms() -> None:
    assert plain("  Cerveja  BRAHMA Lata 2,5L! ") == "cerveja brahma lata 2,5l"
    assert words("cerv.heinek l.n.") == " cerveja heineken l n "


def test_a_bars_beer_without_a_size_is_its_600_ml_bottle_assumed() -> None:
    """Bars write "HEINEKEN" for the bottle they sell by default (O Baiano Churrasqueiro, 11 Oct 2026)."""
    for description in ("HEINEKEN", "CERVEJA AMSTEL"):
        serving = READER.read(description, assume_bottle=True)
        assert (serving.format_code, serving.volume_ml) == ("garrafa_600", None)  # None: assumed, reviewed
    with pytest.raises(NotAServing):
        READER.read("HEINEKEN")  # a shop's line: unread
    with pytest.raises(NotAServing):
        READER.read("CERVEJA PILSEN", assume_bottle=True)  # no brand, no size
    assert READER.read("HEINEKEN LONG NECK", assume_bottle=True).format_code == "long_neck_330"
