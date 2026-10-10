from datetime import UTC, datetime

from cb_scrapers.state_probe import Result, distance_km, geohash, report, summarise


def test_geohash_matches_the_reference_encoding():
    assert geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"
    assert geohash(-25.4284, -49.2733, 6).startswith("6g")


def test_summarise_counts_beer_shops_ages_and_distance():
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    rows = [
        {
            "produto": {"ncm": "22030000", "data": "2026-10-10 10:00:00+00:00", "precoUnitario": 3.5},
            "estabelecimento": {
                "cnpj": "1",
                "nomeEstabelecimento": "SUPERMERCADO X",
                "latitude": -12.98,
                "longitude": -38.45,
            },
        },
        {
            "produto": {"ncm": "22030000", "data": "2026-10-09 12:00:00+00:00", "precoUnitario": 6.0},
            "estabelecimento": {
                "cnpj": "2",
                "nomeEstabelecimento": "BAR DO ZE",
                "latitude": -13.5,
                "longitude": -38.45,
            },
        },
        {"produto": {"ncm": "21069090"}, "estabelecimento": {"cnpj": "3"}},
    ]
    result = Result("BA", "Salvador", "heineken", "ok")
    summarise(result, rows, ("BA", "Salvador", -12.9855, -38.4569), now)
    assert (result.sales, result.beer, result.shops, result.retail_shops, result.bar_shops) == (3, 2, 2, 1, 1)
    assert (result.newest_hours, result.oldest_hours, result.median_price) == (2.0, 24.0, 4.75)
    assert result.near_share == 0.5


def test_distance_and_report():
    assert round(distance_km(-12.9855, -38.4569, -12.9855, -38.4569), 3) == 0
    text = report([Result("PB", "João Pessoa", "brahma", "unreachable", note="timeout")])
    assert "| PB | João Pessoa | brahma | unreachable |" in text
    assert "Menor Preço Brasil" in text
