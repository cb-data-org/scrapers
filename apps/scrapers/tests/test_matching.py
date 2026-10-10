import pytest

from cb_scrapers.matching import Candidate, best_match, contained, distance_m, distinctive_words, similarity


def test_distinctive_words_drop_legal_and_generic_words() -> None:
    assert distinctive_words("BAR E RESTAURANTE DO ZÉ LTDA") == {"ze"}
    assert distinctive_words("Zé do Caranguejo Comércio de Alimentos 12345678000199") == {"ze", "caranguejo"}
    assert distinctive_words("Boteco Rio Vermelho", ignore={"rio", "vermelho"}) == set()


@pytest.mark.parametrize(
    ("a", "b", "score"),
    [
        ("BOTECO DO ZE LTDA", "Boteco do Zé", 1.0),
        ("ZE DO CARANGUEJO COMERCIO DE ALIMENTOS LTDA", "Zé do Caranguejo", 1.0),
        ("CARANGUEIJO DO ZE", "Zé do Caranguejo", 1.0),  # a typo forgiven
        ("BAR DO ZE", "Zé do Caranguejo", 2 / 3),
        ("CASA DA PEIXADA", "Casa de Tereza", 0.5),
        ("BAR E RESTAURANTE", "Bar do Zé", 0.0),  # nothing distinctive to compare
    ],
)
def test_similarity(a: str, b: str, score: float) -> None:
    assert similarity(a, b) == pytest.approx(score)


def test_distance() -> None:
    # One thousandth of a degree of latitude is about 111 m.
    assert distance_m(-13.0, -38.5, -13.001, -38.5) == pytest.approx(111.2, abs=0.5)


def test_best_match_needs_a_close_name_and_a_close_place() -> None:
    near = Candidate("Boteco do Zé", -13.0122, -38.4908, {"osm_ref": "node/1"})
    far = Candidate("Boteco do Zé", -13.0300, -38.4908, {"osm_ref": "node/2"})  # 2 km south
    other = Candidate("Bar da Maria", -13.0123, -38.4909, {"osm_ref": "node/3"})
    shop = (-13.0124, -38.4911)
    assert best_match(["BOTECO DO ZE LTDA"], *shop, [far, other, near]) is near
    assert best_match(["BOTECO DO ZE LTDA"], *shop, [far, other]) is None
    # Any of the shop's names may be the one OpenStreetMap knows.
    assert best_match(["COMERCIAL XPTO LTDA", "BAR DA MARIA"], *shop, [near, other]) is other


def test_a_name_inside_the_other_matches_when_very_close() -> None:
    osm = Candidate("Boteco do Caranguejo", -12.99000, -38.46000, {"osm_ref": "node/1"})
    assert contained("BOTECO DO CARANGUEJO ITAIGARA", osm.name)
    assert best_match(["BOTECO DO CARANGUEJO ITAIGARA"], -12.99008, -38.46000, [osm]) is osm  # 9 m
    assert best_match(["BOTECO DO CARANGUEJO ITAIGARA"], -12.99090, -38.46000, [osm]) is None  # 100 m
    # Short words are too common to be enough on their own.
    assert not contained("DI GASTRONOMIA", "Di Janela Boteco")


def test_place_names_count_when_a_name_has_nothing_else() -> None:
    # "Barra" is a bairro, and the only distinctive word of "Espetinho da Barra".
    osm = Candidate("Espetinho da barra", -13.0, -38.52, {"osm_ref": "node/2"})
    assert best_match(["A.J.SOUZA ESPETINHO DA BARRA"], -13.0002, -38.52, [osm], ignore={"barra"}) is osm
