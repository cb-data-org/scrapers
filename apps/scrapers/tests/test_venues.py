from datetime import date
from pathlib import Path

import pytest

from cb_scrapers.venues import (
    Evidence,
    ReviewRow,
    ShopBook,
    ShopPrices,
    judge,
    load_review,
    summary_table,
    write_review,
)

BAR, RESTAURANT, SNACK_BAR, NIGHTCLUB, BREWERY = 5611205, 5611201, 5611203, 9329801, 1113502
DRINKS_RETAIL, FOOD_RETAIL, FOOD_WHOLESALE = 4723700, 4729699, 4639701
BAKERY, CONVENIENCE, BUTCHER, TOBACCONIST = 1091102, 4729602, 4722901, 4729601


def evidence(main: int = BAR, *secondary: int, **fields: object) -> Evidence:
    return Evidence(cnpj="11222333000181", main=main, secondary=secondary, **fields)  # type: ignore[arg-type]


# Shaped on the shops the first Preço da Hora runs found (7 Oct 2026).
@pytest.mark.parametrize(
    ("shop", "decision"),
    [
        # A bakery and convenience store registered as a restaurant (Completa Alimentos).
        (
            evidence(RESTAURANT, BAKERY, FOOD_RETAIL, CONVENIENCE, names=("COMPLETA ALIMENTOS LTDA",)),
            "reject",
        ),
        # A 24-hour drinks shop registered as a bar (Pinguim 24h).
        (
            evidence(
                BAR, BUTCHER, TOBACCONIST, names=("PINGUIM 24H", "PINGUIM 24H COMERCIO DE BEBIDAS LTDA")
            ),
            "reject",
        ),
        # Real bars registering drinks and food retail, or food wholesale, for takeaway (Astro, De Passagem).
        (
            evidence(BAR, DRINKS_RETAIL, FOOD_RETAIL, names=("ASTRO GASTRONOMIA BAR E RESTAURANTE LTDA",)),
            "show",
        ),
        (evidence(5611204, FOOD_WHOLESALE, DRINKS_RETAIL, FOOD_RETAIL), "show"),
        # A shop word alone doesn't decide ("Armazém e Bar"); with a sign, it does.
        (evidence(BAR, DRINKS_RETAIL, names=("ARMAZEM E BAR CSB LTDA",)), "show"),
        (evidence(BAR, BUTCHER, names=("ARMAZEM E BAR CSB LTDA",)), "reject"),
        # One sign: the owner decides.
        (evidence(BAR, BUTCHER), "review"),
        # Not signs: cigarettes at the counter (A Marujada), general food wholesale (De Passagem).
        (evidence(BAR, TOBACCONIST), "show"),
        (evidence(5611204, 4691500, FOOD_WHOLESALE, DRINKS_RETAIL), "show"),
        (evidence(BAR, packs=1), "review"),
        (evidence(BAR, CONVENIENCE, packs=2), "reject"),
        # Prices like a shop's: the owner decides.
        (evidence(BAR, ratios=[1.0, 1.1, 1.2]), "review"),
        (evidence(BAR, ratios=[1.8, 2.4]), "show"),
        # Micro-entrepreneurs: only when OpenStreetMap maps them as a bar.
        (evidence(BAR, mei=True), "review"),
        (evidence(BAR, mei=True, osm_kind="boteco"), "show"),
        (evidence(BAR, mei=True, osm_kind="restaurante"), "review"),
        # A place OpenStreetMap maps as a café.
        (evidence(RESTAURANT, osm_kind="outro"), "review"),
        # Out of scope by the main activity, whatever else.
        (evidence(SNACK_BAR), "reject"),
        (evidence(NIGHTCLUB), "reject"),
        (evidence(4723700), "reject"),
        (evidence(None), "reject"),
        # A brewery's taproom and a restaurant are in.
        (evidence(BREWERY), "show"),
        (evidence(RESTAURANT), "show"),
    ],
)
def test_verdicts(shop: Evidence, decision: str) -> None:
    assert judge(shop).decision == decision


def test_verdicts_say_why() -> None:
    verdict = judge(evidence(RESTAURANT, BAKERY, CONVENIENCE))
    assert verdict.reasons == ("convenience store (4729602)", "bakery (1091102)")
    verdict = judge(evidence(BAR, ratios=[1.0, 1.2]))
    assert verdict.reasons == ("prices like a shop's (1.10x the median shop price)",)


@pytest.mark.parametrize(
    ("decision", "clean", "doubtful"),
    [
        ("keep", "show", "show"),
        ("merge", "show", "show"),
        ("hide", "reject", "reject"),
        # Pending never shows: a clean shop waits, a rejected one stays rejected.
        ("pending", "review", "reject"),
    ],
)
def test_the_owners_decision_comes_first(decision: str, clean: str, doubtful: str) -> None:
    assert judge(evidence(BAR), decision).decision == clean
    assert judge(evidence(RESTAURANT, BAKERY, CONVENIENCE), decision).decision == doubtful


def test_shop_prices_need_three_recent_ones(tmp_path: Path) -> None:
    path = tmp_path / "prices.json"
    prices = ShopPrices(path, date(2026, 10, 7))
    for day, price in [("2026-10-05", 6.99), ("2026-10-06", 7.49)]:
        prices.add("brand:amstel|garrafa_600", price, day)
    assert prices.median("brand:amstel|garrafa_600") is None
    prices.add("brand:amstel|garrafa_600", 7.19, "2026-10-07")
    prices.add("brand:amstel|garrafa_600", 7.19, "2026-10-07")  # the same sale again
    assert prices.median("brand:amstel|garrafa_600") == 7.19
    prices.save()
    # Two months on, they no longer count.
    assert ShopPrices(path, date(2026, 10, 8)).median("brand:amstel|garrafa_600") == 7.19
    assert ShopPrices(path, date(2026, 12, 20)).median("brand:amstel|garrafa_600") is None


def test_the_review_list_round_trips_and_ignores_bad_rows(tmp_path: Path) -> None:
    path = tmp_path / "venue-review.csv"
    rows = [
        ReviewRow("11222333000181", "hide", "", "Pinguim 24h", "24h drinks shop"),
        ReviewRow("22333444000190", "keep", "", "Bar do Zé", ""),
        ReviewRow("33444555000109", "merge", "osm:way/1", "Boteco X", "second pin"),
    ]
    write_review(rows, path)
    text = path.read_text()
    assert text.splitlines()[0] == "cnpj,decision,merge_into,name,note"
    assert text.splitlines()[1].startswith("22333444000190,keep")  # keep first, then hide, pending, merge
    path.write_text(text + "123,keep,,Bad,\n44555666000172,maybe,,Unknown,\n")
    review = load_review(path)
    assert sorted(review) == ["11222333000181", "22333444000190", "33444555000109"]
    assert review["33444555000109"].merge_into == "osm:way/1"


def test_the_book_keeps_evidence_between_runs(tmp_path: Path) -> None:
    path = tmp_path / "shops.json"
    book = ShopBook(path)
    record = book.get("11222333000181")
    record.add_pack("sale-1")
    record.add_pack("sale-1")
    record.ratios["brand:amstel|garrafa_600"] = 1.03
    record.name, record.decision, record.reasons = "Pinguim 24h", "review", ["prices like a shop's"]
    book.save()
    again = ShopBook(path).get("11222333000181")
    assert (again.packs, again.ratios, again.decision) == (
        ["sale-1"],
        {"brand:amstel|garrafa_600": 1.03},
        "review",
    )
    table = summary_table(ShopBook(path).records, ["11222333000181"])
    assert "| 11222333000181 | Pinguim 24h | — | review | no | — | prices like a shop's | — |" in table
