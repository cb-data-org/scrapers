import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers.cnpj import MINHA_RECEITA, CnpjRegister
from cb_scrapers.core import PoliteClient
from cb_scrapers.review import AdminClient, apply, audit
from cb_scrapers.venues import ReviewRow, ShopBook, ShopPrices
from tests.fakes import FakeSite

BAKERY, DEPOSITO_BAR, BAR, TWIN = "11222333000181", "22333444000190", "33444555000109", "44555666000172"


def venue(id: int, name: str, cnpj: str | None, osm_ref: str | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "cnpj": cnpj,
        "osm_ref": osm_ref,
        "kind": "bar",
        "status": "active",
        "lat": -13.0105,
        "lng": -38.4855,
        **fields,
    }


def register(cnpj: str, cnae: int, trade_name: str, *secondary: int) -> str:
    return json.dumps(
        {
            "cnpj": cnpj,
            "razao_social": f"{trade_name} LTDA",
            "nome_fantasia": trade_name,
            "cnae_fiscal": cnae,
            "situacao_cadastral": 2,
            "codigo_natureza_juridica": 2062,
            "cnaes_secundarios": [{"codigo": code} for code in secondary],
        }
    )


LIVE = [
    venue(1, "Completa", BAKERY, kind="restaurante", lat=-12.98, lng=-38.47),
    venue(2, "Altas Horas", DEPOSITO_BAR, lat=-12.97, lng=-38.41),
    venue(3, "Bar do Zé", BAR, "node/3", lat=-13.0122, lng=-38.4908),
    # A second pin, 10 m from OpenStreetMap's "Boteco do Caranguejo".
    venue(4, "Boteco do Caranguejo Itaigara", TWIN, lat=-12.99008, lng=-38.46),
    venue(5, "Boteco do Caranguejo", None, "way/5", lat=-12.99, lng=-38.46),
    venue(6, "Bar sem CNPJ", None, "node/6"),
]
DEALS = [
    {
        "venue_id": 2,
        "brand": "amstel",
        "beer": None,
        "format_code": "garrafa_600",
        "units": 1,
        "packaging": "single",
        "price_minor": 800,
    },
    {
        "venue_id": 3,
        "brand": "amstel",
        "beer": None,
        "format_code": "garrafa_600",
        "units": 1,
        "packaging": "single",
        "price_minor": 1690,
    },
]


@pytest.fixture
def cnpjs(polite: Callable[..., PoliteClient]) -> CnpjRegister:
    site = FakeSite(
        {
            MINHA_RECEITA.format(cnpj=BAKERY): register(BAKERY, 5611201, "COMPLETA", 1091102, 4729602),
            MINHA_RECEITA.format(cnpj=DEPOSITO_BAR): register(DEPOSITO_BAR, 5611205, "ALTAS HORAS"),
            MINHA_RECEITA.format(cnpj=BAR): register(BAR, 5611204, "BAR DO ZE"),
            MINHA_RECEITA.format(cnpj=TWIN): register(TWIN, 5611205, "BOTECO DO CARANGUEJO ITAIGARA"),
        }
    )
    return CnpjRegister(polite(site), None, date(2026, 10, 7))


@pytest.fixture
def shop_prices(tmp_path: Path) -> ShopPrices:
    prices = ShopPrices(tmp_path / "prices.json", date(2026, 10, 7))
    for price in (6.99, 7.19, 7.49):
        prices.add("brand:amstel|garrafa_600", price, "2026-10-06")
    return prices


def test_the_audit_suggests_a_decision_for_each_live_venue_with_a_cnpj(
    cnpjs: CnpjRegister, shop_prices: ShopPrices, tmp_path: Path
) -> None:
    findings = audit(LIVE, DEALS, cnpjs, ShopBook(None), shop_prices, review={})
    rows = {finding.row.cnpj: finding.row for finding in findings}
    # The doubtful one (shop prices) gets no row: the AI reviewer decides it from the daily runs (#416).
    assert sorted(rows) == sorted([BAKERY, BAR, TWIN])
    assert rows[BAKERY].decision == "hide"
    assert rows[BAKERY].note.startswith(
        "convenience store (4729602); bakery (1091102); main activity 5611201"
    )
    assert rows[BAR].decision == "keep"
    assert (rows[TWIN].decision, rows[TWIN].merge_into) == ("merge", "osm:way/5")
    assert rows[TWIN].note.startswith("second pin of “Boteco do Caranguejo”")


def test_the_audit_keeps_the_owners_decisions(cnpjs: CnpjRegister, shop_prices: ShopPrices) -> None:
    review = {BAKERY: ReviewRow(BAKERY, "keep", "", "Completa", "a real restaurant, checked")}
    findings = audit(LIVE, DEALS, cnpjs, ShopBook(None), shop_prices, review)
    assert next(f.row for f in findings if f.row.cnpj == BAKERY) == review[BAKERY]


class FakeAdmin:
    """The admin API's venue routes over a dict of venues; records every change."""

    def __init__(self, venues: list[dict[str, Any]]) -> None:
        self.venues = {v["id"]: dict(v) for v in venues}
        self.changes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer cb_admin"
        path = request.url.path
        if request.method == "GET" and path == "/api/admin/venues":
            filters = {k: v for k, v in request.url.params.items() if k not in ("limit", "after")}
            items = [
                v for v in self.venues.values() if all(str(v.get(k)) == value for k, value in filters.items())
            ]
            return httpx.Response(200, json={"items": items, "next": None})
        venue_id = int(path.split("/")[4])
        if request.method == "PATCH":
            changes = json.loads(request.content)
            self.venues[venue_id].update(changes)
            self.changes.append(f"patch {venue_id} {changes}")
            return httpx.Response(200, json=self.venues[venue_id])
        if request.method == "POST" and path.endswith("/merge"):
            into = json.loads(request.content)["into"]
            self.venues[venue_id].update(status="merged", merged_into=into)
            self.changes.append(f"merge {venue_id} into {into}")
            return httpx.Response(200, json=self.venues[venue_id])
        return httpx.Response(404)


REVIEW = [
    ReviewRow(BAKERY, "hide", "", "Completa"),
    ReviewRow(DEPOSITO_BAR, "pending", "", "Altas Horas"),
    ReviewRow(BAR, "keep", "", "Bar do Zé"),
    ReviewRow(TWIN, "merge", "osm:way/5", "Boteco do Caranguejo Itaigara"),
    ReviewRow("55666777000180", "hide", "", "Never sent"),
]


def admin_for(fake: FakeAdmin) -> AdminClient:
    return AdminClient("https://cb.test", "cb_admin", transport=httpx.MockTransport(fake))


def test_apply_does_what_the_list_says() -> None:
    fake = FakeAdmin([*LIVE, venue(7, "Clube da Noite", None, "node/7")])
    fake.venues[3]["status"] = "hidden"  # hidden earlier, kept now
    done = apply(admin_for(fake), REVIEW, nightclubs=["node/7"], dry_run=False)
    assert fake.changes == [
        "patch 1 {'status': 'hidden'}",
        "patch 3 {'status': 'active'}",
        "merge 4 into 5",
        "patch 7 {'kind': 'outro'}",
    ]
    assert done[0] == f"hide Completa ({BAKERY}, venue 1)"
    # Again: nothing left to do.
    assert apply(admin_for(fake), REVIEW, nightclubs=["node/7"], dry_run=False) == []


def test_a_dry_run_changes_nothing() -> None:
    fake = FakeAdmin(LIVE)
    done = apply(admin_for(fake), REVIEW, dry_run=True)
    assert fake.changes == []
    assert done[0] == f"would hide Completa ({BAKERY}, venue 1)"
    assert len(done) == 2  # hide, merge; the pending row is the AI reviewer's


def test_apply_goes_ahead_when_overpass_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from cb_scrapers import review_cli

    fake = FakeAdmin(LIVE)
    monkeypatch.setattr(review_cli, "AdminClient", lambda url, token: admin_for(fake))
    monkeypatch.setattr(review_cli, "load_review", lambda: {row.cnpj: row for row in REVIEW})

    def down(*args: object) -> list[str]:
        raise RuntimeError("every Overpass instance failed")

    monkeypatch.setattr(review_cli, "nightclub_refs", down)
    text = review_cli.run_venues("apply", "https://cb.test", "cb_admin", apply_changes=False)
    assert f"would hide Completa ({BAKERY}, venue 1)" in text
    assert "nightclubs skipped: Overpass didn't answer" in text
