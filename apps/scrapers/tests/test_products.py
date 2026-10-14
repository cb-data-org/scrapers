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
        (
            "CERV ANTARCTICA SUB ZERO LT 350ML",
            "antarctica",
            "antarctica-subzero",
            "lata_350",
            350,
            1,
            "single",
        ),
        ("STELLA ARTOIS PURE GOLD LN 330ML", "stella-artois", None, "long_neck_330", 330, 1, "single"),
        # Shops' lines for the brands they sell most (Preço da Hora, 6 Oct 2026, and their usual forms).
        ("CERV SKOL LT 350ML", "skol", None, "lata_350", 350, 1, "single"),
        ("SKOL PILSEN LATA 269ML", "skol", "skol-pilsen", "lata_269", 269, 1, "single"),
        ("CERVEJA PILSEN SKOL GRF 300ML OW", "skol", None, "garrafa_300", 300, 1, "single"),
        (
            "CERV ANTARCTICA SUBZERO LT 350ML",
            "antarctica",
            "antarctica-subzero",
            "lata_350",
            350,
            1,
            "single",
        ),
        ("CERV ANTARC SUBZERO LT 473ML", "antarctica", "antarctica-subzero", "lata_473", 473, 1, "single"),
        ("CERV ANTARCTICA PILSEN 600ML", "antarctica", "antarctica-pilsen", "garrafa_600", 600, 1, "single"),
        # Original is Antarctica's: its maker's name, in either order, is no second brand.
        (
            "CERVEJA ORIGINAL ANTARCTICA 600ML",
            "original",
            "antarctica-original",
            "garrafa_600",
            600,
            1,
            "single",
        ),
        ("ORIGINAL ANTARCTICA 600ML", "original", None, "garrafa_600", 600, 1, "single"),
        ("CERV ANTARCTICA ORIGINAL 600ML", "original", None, "garrafa_600", 600, 1, "single"),
        # Itaipava's 550 ml tall can.
        ("CERV LT ITAIPAVA PIL 550ML", "itaipava", None, "lata_550", 550, 1, "single"),
        ("CERVEJA ITAIPAVA 550ML", "itaipava", None, "lata_550", 550, 1, "single"),
        (
            "CERV ITAIPAVA 100% MALTE LT 350ML",
            "itaipava",
            "itaipava-puro-malte",
            "lata_350",
            350,
            1,
            "single",
        ),
        ("CERV CRYSTAL LT 350ML", "crystal", None, "lata_350", 350, 1, "single"),
        ("CERV PROIBIDA LT 269ML", "proibida", None, "lata_269", 269, 1, "single"),
        # The litrão: the returnable 1 litre bottle, as receipts write it (the word itself is rare).
        ("CERV SKOL 1L", "skol", None, "litrao_1000", 1000, 1, "single"),
        ("CERV SKOL RET 1000ML", "skol", None, "litrao_1000", 1000, 1, "single"),
        ("CERVEJA SKOL GARRAFA 1L RETORNAVEL", "skol", None, "litrao_1000", 1000, 1, "single"),
        ("LITRAO SKOL", "skol", None, "litrao_1000", 1000, 1, "single"),
        ("ANTARCTICA SUBZERO 1L RET", "antarctica", "antarctica-subzero", "litrao_1000", 1000, 1, "single"),
        ("CERV ITAIPAVA 1 LITRO", "itaipava", None, "litrao_1000", 1000, 1, "single"),
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
        # The beer brands' other drinks, filed under beer's NCM by some shops (6 Oct 2026).
        ("REFRIGERANTE LATA 350ML", "not beer"),
        ("REFRI ANTARCTICA GUARANA PET 2LT", "not beer"),
        ("GUARANA ANTARCTICA LT 350ML", "not beer"),
        ("AGUA TONICA ANTARCTICA LT 350ML", "not beer"),
        ("SKOL BEATS SENSES LT 269ML", "not beer"),
        ("ANTARCTICA SUBZERO ORIGINAL 600ML", "two brands"),
        ("HEINEKEN", "no size"),
        ("BALDE HEINEKEN", "bucket size"),
        ("HEINEKEN AMSTEL BALDE C/6", "two brands"),
    ],
)
def test_explains_what_it_leaves_out(description: str, reason: str) -> None:
    with pytest.raises(NotAServing) as error:
        READER.read(description)
    assert error.value.reason == reason


# Receipt lines as Salvador's shops and bars write them (the scrapers' logs, Oct 2026). `draught`: a bar's
# line (True), or a shop's (False): shops pour no draught.
BAR, SHOP = True, False


@pytest.mark.parametrize(
    ("description", "draught", "brand", "beer", "code", "volume"),
    [
        # Brahma Chopp is the beer named after draught: with a can or bottle word, or at a shop, it is packed.
        ("CERV BRAHMA CHOPP LT 350ML", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("CERVEJA BRAHMA CHOPP LATA 350ML", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("BRAHMA CHOPP 473ML", SHOP, "brahma", "brahma-chopp", "lata_473", 473),
        ("CERV BRAHMA CHOPP LATAO 473ML", SHOP, "brahma", "brahma-chopp", "lata_473", 473),
        ("CHOPP BRAHMA LT 350ML", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("CHOPP BRAHMA LT 350ML", BAR, "brahma", "brahma-chopp", "lata_350", 350),
        ("CERV CHOPP BRAHMA LATA 350ML", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("CERV BRAHMA CHOPE LT 350ML", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("CERV BRAHMA CHOP LT 269ML", SHOP, "brahma", "brahma-chopp", "lata_269", 269),
        ("CERV BRAHMA PILSEN CHOPP LT 350ML", BAR, "brahma", "brahma-chopp", "lata_350", 350),
        ("BRAHMA CHOPP LN 355ML", BAR, "brahma", "brahma-chopp", "long_neck_355", 355),
        ("CHOPP BRAHMA 350ML", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("CHOPP BRAHMA 473ML", SHOP, "brahma", "brahma-chopp", "lata_473", 473),
        ("CHOPP BRAHMA 300ML", SHOP, "brahma", "brahma-chopp", "garrafa_300", 300),
        ("CHOPP BRAHMA 350 LT", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("CERV.BRAHMA CHOPP LT.350ML", SHOP, "brahma", "brahma-chopp", "lata_350", 350),
        ("CHOPP BRAHMA ZERO 350ML", SHOP, "brahma", "brahma-zero", "lata_350", 350),
        # Bars pour draught.
        ("CHOPP 300ML", BAR, None, None, "chopp_300", 300),
        ("CHOPP BRAHMA 500ML", BAR, "brahma", None, "chopp_500", 500),
        ("CHOPP BRAHMA 350ML", BAR, "brahma", None, "chopp_350", 350),
        ("CHOPP BRAHMA 473ML", BAR, "brahma", None, "chopp_473", 473),
        ("TORRE CHOPP BRAHMA 2,5L", BAR, "brahma", None, "torre_2500", 2500),
        # A bottle of a long neck's size is that long neck; a garrafa of a litre is a litrão.
        ("CERV AMSTEL ULTRA GF 330ML", SHOP, "amstel", None, "long_neck_330", 330),
        ("CERV AMSTEL LAG ULTRA 330ML GFA", SHOP, "amstel", None, "long_neck_330", 330),
        ("CERVEJA PILSEN CORONA GARRAFA 330ML", SHOP, "corona", None, "long_neck_330", 330),
        ("CERVEJA IMPERIO LAGER 330ML GARRAFA", SHOP, "imperio", "imperio-lager", "long_neck_330", 330),
        ("CERVEJA HEINEKEN GARRAFA 330", BAR, "heineken", None, "long_neck_330", 330),
        ("BRAHMA GARRAFA 1000", BAR, "brahma", None, "litrao_1000", 1000),
        # The small long necks: Heineken's 250 ml, Império Gold's and Coronita's 210 ml.
        ("CERVEJA HEINEKEN LN 250ML", SHOP, "heineken", None, "long_neck_250", 250),
        ("CERVEJA HEINEKEN L NECK 250ML", SHOP, "heineken", None, "long_neck_250", 250),
        ("CERV HEINEKEN L.NECK 250ML", SHOP, "heineken", None, "long_neck_250", 250),
        ("HEINEKEN 250ML", BAR, "heineken", None, "long_neck_250", 250),
        ("CERV IMPERIO LN 210ML", SHOP, "imperio", None, "long_neck_210", 210),
        ("CERV IMPERIO PILSEN GOLD 210ML", SHOP, "imperio", None, "long_neck_210", 210),
        ("CERV CORONA EX 210ML", SHOP, "corona", None, "long_neck_210", 210),
        # Malzbier is its brand's Malzbier, whatever the word order, never the brand's lager.
        ("BRAHMA MALZBIER LN 355ML", SHOP, "brahma", "brahma-malzbier", "long_neck_355", 355),
        ("CERVEJA MALZBIER GF BRAHMA 355ML", SHOP, "brahma", "brahma-malzbier", "long_neck_355", 355),
        ("CERV BRAHMA MALZ LT 350ML", BAR, "brahma", "brahma-malzbier", "lata_350", 350),
        (
            "CERVEJA MALZBIER ITAIPAVA GRF 330ML",
            SHOP,
            "itaipava",
            "itaipava-malzbier",
            "long_neck_330",
            330,
        ),
        # Words run together with dots, and "350 LT" (a 350 ml can: no beer comes in 350 litres).
        ("CERV.SKOL LT.350ML", SHOP, "skol", None, "lata_350", 350),
        ("CERV.SKOL LT.269ML", SHOP, "skol", None, "lata_269", 269),
        ("CERVEJA AMSTEL LAGER 350 LT", SHOP, "amstel", "amstel-lager", "lata_350", 350),
        ("CERV AMSTEL LAGER 350LT SLEEK", SHOP, "amstel", "amstel-lager", "lata_350", 350),
    ],
)
def test_reads_receipt_lines_where_they_were_sold(
    description: str, draught: bool, brand: str | None, beer: str | None, code: str, volume: int
) -> None:
    serving = READER.read(description, draught=draught)
    slugs = (serving.brand.slug if serving.brand else None, serving.beer.slug if serving.beer else None)
    assert (slugs, serving.format_code, serving.volume_ml) == ((brand, beer), code, volume)


@pytest.mark.parametrize(
    ("description", "draught", "reason"),
    [
        # A shop's draught is no draught, and no beer either unless chopp is its name.
        ("CHOPP 300ML", SHOP, "draught at a shop"),
        ("CHOPP AMSTEL 300ML", SHOP, "draught at a shop"),
        ("CANECA DE CHOPP BRAHMA 500ML", SHOP, "draught at a shop"),
        ("TORRE CHOPP BRAHMA 2,5L", SHOP, "draught at a shop"),
        ("CHOPP BRAHMA 500ML", SHOP, "size without a type"),
        # Wine "chopp", filed under beer by some shops.
        ("CHOPP DE VINHO PINK MOON PET 600ML", BAR, "not beer"),
        ("CHOPP VINHO STEMPEL 600 ML", SHOP, "not beer"),
        # Brahma Malzbier's long neck is 355 ml: a "335ML" is left to the barcode, never guessed.
        ("BRAHMA MALZBIER LN 335ML", SHOP, "size not in the catalog"),
        ("CERV SCHIN MALZBIER LT 350ML", SHOP, "malzbier: not in the catalog"),
        ("CERVEJA MALZBIER 355ML", BAR, "malzbier: not in the catalog"),
        # Packs, however they write it.
        ("CERV HEINEKEN PIL 350LT SLEEKDES12UNPB", SHOP, "pack"),
        ("CERV IMPERIO LAGER GFA 330ML CXC/12 DESC", SHOP, "pack"),
    ],
)
def test_leaves_out_receipt_lines_it_cannot_tell(description: str, draught: bool, reason: str) -> None:
    with pytest.raises(NotAServing) as error:
        READER.read(description, draught=draught)
    assert error.value.reason == reason


def test_names_brahma_chopp_and_malzbier_as_the_ingest_api_knows_them() -> None:
    can = READER.read("CHOPP BRAHMA LT 350ML", draught=False)
    malzbier = READER.read("CERVEJA MALZBIER GF BRAHMA 355ML", draught=False)
    assert (can.brand_alias, can.beer_alias) == ("brahma", "brahma chopp")
    assert (malzbier.brand_alias, malzbier.beer_alias) == ("brahma", "brahma malzbier")
    assert can.key == "beer:brahma-chopp|lata_350|1|single"


def test_a_catalog_without_the_new_sizes_and_beers_leaves_their_lines_unread() -> None:
    """Until the production seed adds them, the live catalog the scrapers read first lacks the small long
    necks and the Malzbiers: their lines stay unread rather than become another size or the brand's lager."""
    catalog = committed_catalog()
    older = ServingReader(
        catalog.model_copy(
            update={
                "formats": [f for f in catalog.formats if f.code not in ("long_neck_210", "long_neck_250")],
                "beers": [beer for beer in catalog.beers if beer.style != "malzbier"],
            }
        )
    )
    for description, reason in (
        ("CERVEJA HEINEKEN LN 250ML", "size not in the catalog"),
        ("CERV IMPERIO PILSEN GOLD 210ML", "size not in the catalog"),
        ("BRAHMA MALZBIER LN 355ML", "malzbier: not in the catalog"),
    ):
        with pytest.raises(NotAServing) as error:
            older.read(description, draught=False)
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
    assert plain("CERV.SKOL LT.350ML, HEINEKEN 0.0") == "cerv skol lt 350ml heineken 0.0"
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


def test_antarctica_subzero_is_beer_with_alcohol() -> None:
    for description in ("CERV ANTARCTICA SUBZERO LT 350ML", "CERVEJA ANTARCTICA SUB ZERO LATA 350ML"):
        serving = READER.read(description)
        assert serving.beer is not None and serving.beer.slug == "antarctica-subzero"
        assert serving.beer.non_alcoholic is False
    # A bar's Skol Beats isn't Skol's 600 ml bottle either.
    with pytest.raises(NotAServing) as error:
        READER.read("SKOL BEATS", assume_bottle=True)
    assert error.value.reason == "not beer"
