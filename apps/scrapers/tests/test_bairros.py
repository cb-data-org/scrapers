import json

import pytest
from shapely.geometry import shape

from cb_scrapers.geo import SEED_DIR, BairroIndex, slugify


def square(city: str, slug: str, west: float, south: float, size: float = 0.01) -> dict:
    ring = [
        [west, south],
        [west + size, south],
        [west + size, south + size],
        [west, south + size],
        [west, south],
    ]
    return {
        "type": "Feature",
        "properties": {"city": city, "slug": slug, "name": slug.title(), "aliases": [slug.upper()]},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


@pytest.fixture
def index() -> BairroIndex:
    # Two neighbours sharing the line lng = -38.49, about 1.1 km square each.
    return BairroIndex(
        [square("salvador", "oeste", -38.50, -13.01), square("salvador", "leste", -38.49, -13.01)]
    )


def test_finds_the_bairro_containing_a_point(index: BairroIndex) -> None:
    assert index.locate(-13.005, -38.495).slug == "oeste"
    assert index.locate(-13.005, -38.485).slug == "leste"
    assert index.locate(-13.005, -38.495).aliases == ("OESTE",)


def test_points_on_a_shared_border_get_one_answer(index: BairroIndex) -> None:
    assert index.locate(-13.005, -38.49).slug in {"oeste", "leste"}
    assert index.locate(-13.005, -38.49) == index.locate(-13.005, -38.49)


def test_points_just_outside_fall_back_to_the_nearest_bairro(index: BairroIndex) -> None:
    # ~110 m south of the polygons, like a barraca on the sand below the official line.
    assert index.locate(-13.011, -38.485).slug == "leste"
    assert index.locate(-13.011, -38.485, max_distance_m=50) is None
    assert index.locate(-13.2, -38.2) is None


def test_slugs_follow_the_domain_rule() -> None:
    # Same cases as packages/domain/test/parse.test.ts.
    assert slugify("Bar do Zé (Rio Vermelho)") == "bar-do-ze-rio-vermelho"
    assert slugify("  Boteco £1 / Chopp R$ 9,99!  ") == "boteco-1-chopp-r-9-99"
    assert slugify(f"{'x' * 79} yz") == "x" * 79
    assert slugify("Caixa D'Água") == "caixa-d-agua"


def committed(city: str) -> dict:
    return json.loads((SEED_DIR / f"{city}.geojson").read_text("utf-8"))


@pytest.mark.parametrize(("city", "count"), [("salvador", 171), ("lauro-de-freitas", 19)])
def test_committed_bairros_are_complete_and_valid(city: str, count: int) -> None:
    data = committed(city)
    features = data["features"]
    assert len(features) == count
    slugs = [feature["properties"]["slug"] for feature in features]
    assert slugs == sorted(set(slugs))
    for feature in features:
        properties = feature["properties"]
        assert properties["city"] == city
        assert properties["slug"] == slugify(properties["name"])
        geometry = shape(feature["geometry"])
        assert geometry.is_valid
        assert geometry.covers(
            shape({"type": "Point", "coordinates": [properties["centroid_lng"], properties["centroid_lat"]]})
        )
    assert data["metadata"]["source"]


def test_committed_bairros_place_known_spots() -> None:
    index = BairroIndex.from_files()
    spots = {
        (-13.0104, -38.5327): "salvador/barra",  # Farol da Barra
        (-13.0122, -38.4908): "salvador/rio-vermelho",  # Largo da Mariquita
        (-12.9714, -38.5097): "salvador/centro-historico",  # Pelourinho
        (-12.9575, -38.3542): "salvador/itapua",  # Farol de Itapuã
        (-12.8890, -38.2968): "lauro-de-freitas/vilas-do-atlantico",
    }
    for (lat, lng), expected in spots.items():
        bairro = index.locate(lat, lng)
        assert bairro is not None and f"{bairro.city}/{bairro.slug}" == expected
    assert index.locate(-13.2, -38.2) is None
