import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers.adapters import precodahora
from cb_scrapers.adapters.osm_venues import OVERPASS
from cb_scrapers.adapters.precodahora import (
    SEARCH_URL,
    PrecoDaHora,
    display_name,
    shop_category,
    tidy_address,
    tidy_name,
)
from cb_scrapers.cnpj import MINHA_RECEITA, CnpjRegister, Company
from cb_scrapers.core import BlockedError, Fetched, PoliteClient, Task
from cb_scrapers.core.catalog import committed_catalog
from cb_scrapers.models import IngestRequest
from cb_scrapers.venues import ReviewRow
from tests.fakes import FakeSite

ZE, SABORES, GELADAS, MEI, MERCADO = (
    "11222333000181",
    "22333444000190",
    "33444555000109",
    "44555666000172",
    "55",
)

OVERPASS_RESPONSE = {
    "elements": [
        {
            "type": "node",
            "id": 1,
            "lat": -13.0122,
            "lon": -38.4908,
            "tags": {"amenity": "bar", "name": "Boteco do Zé"},
        },
        {
            "type": "node",
            "id": 2,
            "lat": -13.0105,
            "lon": -38.4860,
            "tags": {"amenity": "bar", "name": "Bar da Maria"},
        },
    ]
}


def sale(description: str, price: float, shop: dict[str, Any], **product: Any) -> dict[str, Any]:
    return {
        "produto": {
            "descricao": description,
            "ncm": 22030000,
            "unidade": "UN",
            "precoUnitario": price,
            "data": "2026-10-05 01:30:00-00:00",  # 22:30 on the 4th in Salvador
            **product,
        },
        "estabelecimento": shop,
    }


def shop(name: str, cnpj: str, lat: float, lng: float, **fields: Any) -> dict[str, Any]:
    return {"nomeEstabelecimento": name, "cnpj": cnpj, "latitude": lat, "longitude": lng, **fields}


ZE_SHOP = shop("BAR E RESTAURANTE DO ZE LTDA", ZE, -13.0124, -38.4911)
SABORES_SHOP = shop(
    "SABORES COMERCIO DE ALIMENTOS LTDA",
    SABORES,
    -13.0105,
    -38.4855,
    endLogradouro="RUA DA PAZ",
    endNumero="0012",
)
MERCADO_SHOP = shop("SUPERMERCADO BOM DIA LTDA", MERCADO * 7, -12.9985, -38.456)
SEARCH = {
    "totalRegistros": 9,
    "totalPaginas": 1,
    "resultado": [
        sale("CHOPP BRAHMA 300ML", 9.9, ZE_SHOP),
        sale("HEINEKEN LONG NECK", 14.0, ZE_SHOP),
        sale("HEINEKEN ZERO LN", 12.0, ZE_SHOP, ncm=22029100),
        sale("CERVEJA ORIGINAL 600ML", 18.9, SABORES_SHOP),
        sale("CHOPP DA CASA 500ML", 12.0, SABORES_SHOP),
        sale("CERV SKOL LT 350ML", 4.29, MERCADO_SHOP),
        sale("CX CERV HEINEKEN 12X350ML", 89.9, MERCADO_SHOP, unidade="CX"),
        sale("HEINEKEN 600ML", 12.5, shop("CERVEJAS GELADAS LTDA", GELADAS, -12.9985, -38.456)),
        sale("SKOL 600ML", 11.0, shop("FULANO DE TAL 12345678900", MEI, -12.993, -38.4675)),
    ],
}


def register_body(
    cnpj: str, cnae: int, trade_name: str, nature: int = 2062, *secondary: int, **fields: Any
) -> str:
    return json.dumps(
        {
            "cnpj": cnpj,
            "razao_social": "WHATEVER LTDA",
            "nome_fantasia": trade_name,
            "cnae_fiscal": cnae,
            "situacao_cadastral": 2,
            "codigo_natureza_juridica": nature,
            "cnaes_secundarios": [{"codigo": code} for code in secondary],
            **fields,
        }
    )


REGISTER = {
    MINHA_RECEITA.format(cnpj=ZE): register_body(ZE, 5611204, "BOTECO DO ZE"),
    MINHA_RECEITA.format(cnpj=SABORES): register_body(SABORES, 5611201, "CASA DOS SABORES"),
    MINHA_RECEITA.format(cnpj=GELADAS): register_body(GELADAS, 4723700, "CERVEJAS GELADAS"),
    MINHA_RECEITA.format(cnpj=MEI): register_body(MEI, 5611204, "", nature=2135),
    # A churrascaria registered as a butcher's.
    MINHA_RECEITA.format(cnpj="33444555000166"): register_body(
        "33444555000166", 4722901, "O BAIANO CHURRASQUEIRO"
    ),
}


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CB_CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def register_site() -> FakeSite:
    return FakeSite(REGISTER)


@pytest.fixture
def adapter(polite: Callable[..., PoliteClient], register_site: FakeSite) -> PrecoDaHora:
    adapter = PrecoDaHora(
        polite(FakeSite({})), cnpjs=CnpjRegister(polite(register_site), None, date(2026, 10, 6)), review={}
    )
    adapter.catalog = committed_catalog()
    return adapter


def search(adapter: PrecoDaHora, body: dict[str, Any], key: str = "chopp @ rio-vermelho p1") -> list[Any]:
    adapter.parse(Fetched(Task("osm"), json.dumps(OVERPASS_RESPONSE).encode()))
    adapter.searching[key] = ("chopp", "rio-vermelho")
    return list(adapter.parse(Fetched(Task(key, SEARCH_URL), json.dumps(body).encode())))


def test_bars_beers_become_venues_and_deals(adapter: PrecoDaHora, register_site: FakeSite) -> None:
    items = search(adapter, SEARCH)
    assert [(item.kind, item.source_ref) for item in items] == [
        ("venue", f"cnpj:{ZE}"),
        ("deal", "osm:node/1|brand:brahma|chopp_300|1|single"),
        ("deal", "osm:node/1|brand:heineken|long_neck_330|1|single"),
        ("venue", f"cnpj:{SABORES}"),
        ("deal", f"cnpj:{SABORES}|beer:antarctica-original|garrafa_600|1|single"),
        ("deal", f"cnpj:{SABORES}|generic|chopp_500|1|single"),
    ]
    ze, chopp, heineken, sabores, original, house = (dict(item.data) for item in items)
    # Known to OpenStreetMap: that venue, with the CNPJ, so ingest fills it in instead of adding a pin.
    assert (ze["osm_ref"], ze["name"], ze["cnpj"]) == ("node/1", "Boteco do Zé", ZE)
    assert (chopp["venue_ref"], chopp["brand"], chopp["format"], chopp["volume_ml"], chopp["price"]) == (
        "osm:node/1",
        "brahma",
        "chopp",
        300,
        9.9,
    )
    assert chopp["observed_on"] == "2026-10-04"
    assert chopp["notes"] == "Preço da Hora (NFC-e): CHOPP BRAHMA 300ML"
    assert heineken["volume_ml"] == 330  # Heineken's only long neck
    # A new venue from the register's trade name and the receipt's address.
    assert sabores == {
        "city": "salvador",
        "name": "Casa dos Sabores",
        "lat": -13.0105,
        "lng": -38.4855,
        "kind": "restaurante",
        "on_site": True,
        "address": "Rua da Paz, 12",
        "neighbourhood": "rio-vermelho",
        "osm_ref": None,
        "cnpj": SABORES,
        "instagram": None,
        "cardapioweb_slug": None,
        "opening_hours": None,
        "attributes": [],
    }
    assert (original["venue_ref"], original["beer"], original["brand"]) == (
        f"cnpj:{SABORES}",
        "cerveja original",
        "antarctica original",
    )
    # The supermarket cost no lookup; the drinks shop and the one-person business were looked up and left out.
    looked_up = {path.strip("/") for path in register_site.paths() if path != "/robots.txt"}
    assert looked_up == {ZE, SABORES, GELADAS, MEI}
    assert adapter.skipped["shop: retail by name"] == 1
    assert adapter.skipped["shop: not a bar, restaurant or brewery (CNAE)"] == 1
    assert adapter.book.get(MEI).reasons == ["no trade name (one-person business)"]
    assert adapter.held == {MEI}
    # The house chopp names no brand but says its size: listed as just "Cerveja".
    assert (house["brand"], house["beer"], house["format"], house["volume_ml"]) == (None, None, "chopp", 500)


def test_every_item_fits_the_contract(adapter: PrecoDaHora) -> None:
    items = [item.to_json() for item in search(adapter, SEARCH)]
    IngestRequest.model_validate({"source": "precodahora", "items": items})


def test_each_venue_is_sent_once_and_each_shop_looked_up_once(
    adapter: PrecoDaHora, register_site: FakeSite
) -> None:
    search(adapter, SEARCH)
    again = search(adapter, SEARCH, key="heineken @ rio-vermelho p1")
    assert [item.kind for item in again] == ["deal", "deal", "deal", "deal"]
    assert len([path for path in register_site.paths() if path != "/robots.txt"]) == 4


# The checks (venues.py) at work on a page.


def a_bar(polite: Callable[..., PoliteClient], body: str, **adapter_args: Any) -> PrecoDaHora:
    """An adapter whose one bar, Sabores, has `body` in the CNPJ register."""
    register = FakeSite({MINHA_RECEITA.format(cnpj=SABORES): body})
    adapter = PrecoDaHora(
        polite(FakeSite({})), cnpjs=CnpjRegister(polite(register), None, date(2026, 10, 6)), **adapter_args
    )
    adapter.catalog = committed_catalog()
    return adapter


def page(*sales: dict[str, Any]) -> dict[str, Any]:
    return {"totalRegistros": len(sales), "totalPaginas": 1, "resultado": list(sales)}


def test_a_bakery_and_convenience_store_never_reaches_the_map(polite: Callable[..., PoliteClient]) -> None:
    body = register_body(SABORES, 5611201, "", 2062, 1091102, 4729699, 4729602)
    adapter = a_bar(polite, body, review={})
    assert search(adapter, page(sale("CERVEJA ORIGINAL 600ML", 18.9, SABORES_SHOP))) == []
    record = adapter.book.get(SABORES)
    assert (record.decision, record.reasons) == (
        "reject",
        ["convenience store (4729602)", "bakery (1091102)"],
    )


def test_a_bar_selling_beer_by_the_box_is_held_back_and_listed(
    polite: Callable[..., PoliteClient], cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = cache / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    adapter = a_bar(polite, register_body(SABORES, 5611201, "CASA DOS SABORES"), review={})
    sales = page(
        sale("CX CERV HEINEKEN 12X350ML", 89.9, SABORES_SHOP, unidade="CX"),
        sale("CERVEJA ORIGINAL 600ML", 18.9, SABORES_SHOP),
    )
    assert search(adapter, sales) == []
    adapter.log_summary()
    text = summary.read_text()
    assert "Preço da Hora: bars held back this run" in text
    assert f"| {SABORES} | Casa dos Sabores | rio-vermelho | review | no |" in text
    assert "sells beer by the box, pack or keg (1x)" in text


def test_a_bar_charging_shop_prices_is_held_back(polite: Callable[..., PoliteClient]) -> None:
    adapter = a_bar(polite, register_body(SABORES, 5611201, "CASA DOS SABORES"), review={})
    shops = [sale("CERVEJA ORIGINAL 600ML", price, MERCADO_SHOP) for price in (8.99, 9.49, 9.99)]
    assert search(adapter, page(*shops, sale("CERVEJA ORIGINAL 600ML", 10.5, SABORES_SHOP))) == []
    record = adapter.book.get(SABORES)
    assert record.reasons == ["prices like a shop's (1.11x the median shop price)"]
    assert record.samples == ["CERVEJA ORIGINAL 600ML R$ 10.50 (1.1x)"]


def test_a_bar_charging_bar_prices_shows(polite: Callable[..., PoliteClient]) -> None:
    adapter = a_bar(polite, register_body(SABORES, 5611201, "CASA DOS SABORES"), review={})
    shops = [sale("CERVEJA ORIGINAL 600ML", price, MERCADO_SHOP) for price in (8.99, 9.49, 9.99)]
    items = search(adapter, page(*shops, sale("CERVEJA ORIGINAL 600ML", 18.9, SABORES_SHOP)))
    assert [item.kind for item in items] == ["venue", "deal"]
    assert adapter.book.get(SABORES).ratios == {"beer:antarctica-original|garrafa_600": 1.99}


def test_the_owners_keep_overrides_the_checks(polite: Callable[..., PoliteClient]) -> None:
    body = register_body(SABORES, 5611201, "CASA DOS SABORES", 2062, 4722901)
    review = {SABORES: ReviewRow(SABORES, "keep")}
    adapter = a_bar(polite, body, review=review)
    assert [
        item.kind for item in search(adapter, page(sale("CERVEJA ORIGINAL 600ML", 18.9, SABORES_SHOP)))
    ] == [
        "venue",
        "deal",
    ]
    hidden = a_bar(polite, register_body(SABORES, 5611201, "CASA DOS SABORES"), review={})
    hidden.review = {SABORES: ReviewRow(SABORES, "hide")}
    assert search(hidden, page(sale("CERVEJA ORIGINAL 600ML", 18.9, SABORES_SHOP))) == []


def test_a_micro_entrepreneur_shows_only_where_openstreetmap_has_a_bar(
    polite: Callable[..., PoliteClient],
) -> None:
    body = register_body(SABORES, 5611204, "BAR DA MARIA", 2062, opcao_pelo_mei=True)
    adapter = a_bar(polite, body, review={})
    # Sabores sits 50 m from OpenStreetMap's "Bar da Maria": a MEI known there as a bar shows.
    near = dict(SABORES_SHOP, latitude=-13.0105, longitude=-38.4860)
    assert [item.kind for item in search(adapter, page(sale("CERVEJA ORIGINAL 600ML", 18.9, near)))] == [
        "venue",
        "deal",
    ]
    unknown = a_bar(polite, register_body(SABORES, 5611204, "CASA DOS SABORES", 2062, opcao_pelo_mei=True))
    unknown.review = {}
    assert search(unknown, page(sale("CERVEJA ORIGINAL 600ML", 18.9, SABORES_SHOP))) == []
    assert unknown.book.get(SABORES).reasons == [
        "micro-entrepreneur (MEI) that OpenStreetMap doesn't map as a bar"
    ]


def test_a_search_without_sales_is_an_empty_page(adapter: PrecoDaHora) -> None:
    assert search(adapter, {"codigo": 50, "descricao": "Nenhum resultado encontrado!"}) == []
    assert adapter.pages[("chopp", "rio-vermelho")] == 0
    assert search(adapter, {"codigo": 80, "totalRegistros": 0, "totalPaginas": 0}) == []


def test_an_unexpected_answer_stops_the_run_and_says_what_came(adapter: PrecoDaHora) -> None:
    with pytest.raises(
        BlockedError, match=re.escape('unexpected answer: {codigo=99, mensagem="Tente mais tarde"}')
    ):
        search(adapter, {"codigo": 99, "mensagem": "Tente mais tarde"})


def test_names_and_addresses() -> None:
    assert tidy_name("BAR E RESTAURANTE DO ZE LTDA") == "Bar e Restaurante do Ze"
    assert tidy_name("FULANO DE TAL 12345678900") == "Fulano de Tal"
    assert tidy_name("ESPETINHO DOS AMIGOS LIMITAD") == "Espetinho dos Amigos"
    assert tidy_address({"endLogradouro": "AV OCEANICA", "endNumero": "S/N"}) == "Av Oceanica"
    one_person = Company("1", 5611204, True, None, None, True, "2026-10-06")
    assert display_name(one_person, "FULANO DE TAL 12345678900") is None
    company = Company("2", 5611204, True, None, "BAR X LTDA", False, "2026-10-06")
    assert display_name(company, "BAR X LTDA") == "Bar X"


# The site: a page with the CSRF token, then JSON searches.

PAGE = '<meta id="validate" data-id="token-1"><meta id="rc" data-id="">'


def site(pages: int = 1, page: str = PAGE, answer: Any = None) -> FakeSite:
    """Every search has `pages` pages of no results, unless `answer` says otherwise."""

    def produtos(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page)
        if answer is not None:
            return answer(request) if callable(answer) else answer
        # Some sales (not over the cap), so a circle isn't put off as empty.
        body = {"totalRegistros": pages * 20, "totalPaginas": pages, "resultado": []}
        return httpx.Response(200, json=body)

    return FakeSite(
        {
            f"{precodahora.BASE}/robots.txt": "",
            SEARCH_URL: produtos,
            OVERPASS[0]: json.dumps(OVERPASS_RESPONSE),
        }
    )


def form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


def run_tasks(adapter: PrecoDaHora) -> list[str]:
    keys = []
    for task in adapter.tasks():
        keys.append(task.key)
        adapter.parse(adapter.fetch(task))
    return keys


def offline(
    polite: Callable[..., PoliteClient], fake: FakeSite, register: FakeSite | None = None
) -> PrecoDaHora:
    adapter = PrecoDaHora(polite(fake), cnpjs=CnpjRegister(polite(register or FakeSite({})), None))
    adapter.catalog = committed_catalog()
    return adapter


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """A small search plan: terms over the MVP circles (Pituba and Rio Vermelho by default), nothing else."""

    def use(
        terms: tuple[str, ...],
        mvp: tuple[str, ...] = ("pituba", "rio-vermelho"),
        smaller: dict[str, dict[str, tuple[float, float, int]]] | None = None,
    ) -> None:
        circles = {name: precodahora.MVP[name] for name in mvp}
        smaller = smaller or {}
        monkeypatch.setattr(precodahora, "TERMS", terms)
        monkeypatch.setattr(precodahora, "CITY_TERMS", ())
        monkeypatch.setattr(precodahora, "MVP", circles)
        monkeypatch.setattr(precodahora, "SMALLER", smaller)
        monkeypatch.setattr(precodahora, "CITY", {})
        places = {**circles, **{name: c for children in smaller.values() for name, c in children.items()}}
        monkeypatch.setattr(precodahora, "PLACES", places)

    return use


def test_searches_each_term_and_place_page_by_page(
    polite: Callable[..., PoliteClient], plan: Callable[..., None]
) -> None:
    plan(("chopp", "heineken"))
    fake = site(pages=2)
    assert run_tasks(offline(polite, fake)) == [
        "osm",
        "chopp @ pituba p1",
        "chopp @ pituba p2",
        "chopp @ rio-vermelho p1",
        "chopp @ rio-vermelho p2",
        "heineken @ pituba p1",
        "heineken @ pituba p2",
        "heineken @ rio-vermelho p1",
        "heineken @ rio-vermelho p2",
    ]
    posts = [
        request for request in fake.requests if request.method == "POST" and "produtos" in request.url.path
    ]
    assert form(posts[0]) == {
        "termo": "chopp",
        "horas": "72",
        "latitude": "-12.99928",
        "longitude": "-38.46068",
        "raio": "2",
        "pagina": "1",
        "ordenar": "preco.desc",
    }
    assert posts[0].headers["X-CSRFToken"] == "token-1"
    assert posts[0].headers["X-Requested-With"] == "XMLHttpRequest"
    # One session for the run.
    assert [r.method for r in fake.requests if r.url.path == "/produtos/"].count("GET") == 1


def test_stops_paging_once_the_bars_are_behind(
    polite: Callable[..., PoliteClient], plan: Callable[..., None], register_site: FakeSite
) -> None:
    plan(("chopp",))

    def pages(request: httpx.Request) -> httpx.Response:
        # Bars on page 1 around Pituba; nothing after; nothing at all around Rio Vermelho.
        bars = form(request)["pagina"] == "1" and form(request)["latitude"] == "-12.99928"
        return httpx.Response(200, json={"totalPaginas": 4, "resultado": SEARCH["resultado"] if bars else []})

    assert run_tasks(offline(polite, site(answer=pages), register_site)) == [
        "osm",
        "chopp @ pituba p1",
        "chopp @ pituba p2",  # no bars after some: done
        "chopp @ rio-vermelho p1",
        "chopp @ rio-vermelho p2",  # two pages without bars: done
    ]


def test_a_term_over_the_cap_is_searched_again_in_smaller_circles(
    polite: Callable[..., PoliteClient], plan: Callable[..., None]
) -> None:
    plan(
        ("heineken",),
        mvp=("pituba",),
        smaller={"pituba": {"p-1": (-13.0, -38.46, 1), "p-2": (-12.99, -38.47, 1)}},
    )

    def answer(request: httpx.Request) -> httpx.Response:
        crowded = form(request)["raio"] == "2"
        body = {
            "totalRegistros": 160 if crowded else 30,
            "totalPaginas": 4 if crowded else 2,
            "resultado": [],
        }
        return httpx.Response(200, json=body)

    assert run_tasks(offline(polite, site(answer=answer))) == [
        "osm",
        "heineken @ pituba p1",
        "heineken @ p-1 p1",
        "heineken @ p-1 p2",
        "heineken @ p-2 p1",
        "heineken @ p-2 p2",
    ]


def test_a_crowded_circle_goes_straight_to_its_smaller_ones_next_round(
    polite: Callable[..., PoliteClient], plan: Callable[..., None]
) -> None:
    plan(
        ("heineken",),
        mvp=("pituba",),
        smaller={"pituba": {"p-1": (-13.0, -38.46, 1)}},
    )

    def answer(request: httpx.Request) -> httpx.Response:
        crowded = form(request)["raio"] == "2"
        body = {
            "totalRegistros": 160 if crowded else 30,
            "totalPaginas": 4 if crowded else 1,
            "resultado": [],
        }
        return httpx.Response(200, json=body)

    assert run_tasks(offline(polite, site(answer=answer))) == [
        "osm",
        "heineken @ pituba p1",
        "heineken @ p-1 p1",
    ]
    # The next round remembers: no search of the big circle.
    assert run_tasks(offline(polite, site(answer=answer))) == ["heineken @ p-1 p1"]


def test_a_2km_circle_without_hand_picked_ones_splits_into_seven() -> None:
    ring = precodahora.children("grid-1")
    assert len(ring) == 7 and all(radius == 1 for _, _, radius in ring.values())
    assert ring["grid-1/0"][:2] == precodahora.GRID["grid-1"][:2]
    assert precodahora.circle("grid-1/4") == ring["grid-1/4"]
    assert precodahora.children("barra") == {}  # already 1 km: the site takes whole kilometres


def test_a_crowded_1km_circle_is_read_from_both_ends(
    polite: Callable[..., PoliteClient], plan: Callable[..., None]
) -> None:
    plan(("heineken",), mvp=("pituba",))
    precodahora.PLACES["pituba"] = (-12.99928, -38.46068, 1)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalRegistros": 160, "totalPaginas": 4, "resultado": []})

    adapter = offline(polite, site(answer=answer))
    assert run_tasks(adapter) == [
        "osm",
        "heineken @ pituba p1",
        "heineken @ pituba p2",
        "heineken @ pituba cheap",  # cheapest first: 60 sales the dearest-first pages couldn't show
        "heineken @ pituba a2",
        "heineken @ pituba a3",
    ]
    assert adapter.capped == {}


def test_a_circle_where_a_term_sold_nothing_waits_two_weeks(
    polite: Callable[..., PoliteClient], plan: Callable[..., None]
) -> None:
    plan(("devassa",), mvp=("pituba",))

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"codigo": 50, "descricao": "Nenhum resultado encontrado!"})

    assert run_tasks(offline(polite, site(answer=answer))) == ["osm", "devassa @ pituba p1"]
    adapter = offline(polite, site(answer=answer))
    assert run_tasks(adapter) == []
    assert adapter.skipped["search: nothing sold there lately"] == 1


def test_search_sizes_are_forgotten_every_three_months(tmp_path: Path) -> None:
    path = tmp_path / "density.json"
    old = precodahora.Density(path, date(2026, 7, 1))
    old.set("heineken", "pituba", 160)
    old.save()
    assert precodahora.Density(path, date(2026, 9, 28)).get("heineken", "pituba") == (160, date(2026, 7, 1))
    assert precodahora.Density(path, date(2026, 9, 29)).get("heineken", "pituba") is None


def test_stops_at_the_search_cap(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch, plan: Callable[..., None]
) -> None:
    plan(("chopp", "heineken"))
    monkeypatch.setattr(precodahora, "MAX_SEARCHES", 3)
    assert run_tasks(offline(polite, site(pages=4))) == [
        "osm",
        "chopp @ pituba p1",
        "chopp @ pituba p2",
        "chopp @ rio-vermelho p1",
    ]


def test_each_run_resumes_the_queue_and_reads_overpass_once_a_day(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch, plan: Callable[..., None]
) -> None:
    plan(("a", "b", "c"), mvp=("pituba",))
    monkeypatch.setattr(precodahora, "MAX_SEARCHES", 2)
    runs = [run_tasks(offline(polite, site())) for _ in range(3)]
    assert runs == [
        ["osm", "a @ pituba p1", "b @ pituba p1"],
        ["c @ pituba p1"],
        ["a @ pituba p1", "b @ pituba p1"],  # a new round
    ]


START = datetime(2026, 10, 8, 8, tzinfo=UTC)


def run_at(polite: Callable[..., PoliteClient], hours: float, fake: FakeSite | None = None) -> list[str]:
    """A run that starts `hours` after START."""
    adapter = offline(polite, fake or site())
    adapter.now = START + timedelta(hours=hours)
    return run_tasks(adapter)


def test_chopp_around_the_mvp_comes_round_ahead_of_the_queue_within_72_hours(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch, plan: Callable[..., None]
) -> None:
    plan(("chopp", "a", "b", "c"), mvp=("pituba",))
    monkeypatch.setattr(precodahora, "MAX_SEARCHES", 2)
    # Two weeks of daily runs, each starting up to 8 hours late.
    starts = [24 * day + late for day, late in enumerate([0, 7, 1, 8, 0, 3, 8, 0, 6, 2, 8, 1, 0, 5])]
    runs = {hours: run_at(polite, hours) for hours in starts}
    assert runs[0] == ["osm", "chopp @ pituba p1", "a @ pituba p1"]
    assert runs[31] == ["b @ pituba p1", "c @ pituba p1"]  # the round, in its order
    assert runs[49] == ["chopp @ pituba p1", "a @ pituba p1"]  # 49 hours on: first again
    swept = [hours for hours, keys in runs.items() if "chopp @ pituba p1" in keys]
    assert all(later - earlier <= 72 for earlier, later in pairwise(swept))
    assert len(swept) == 7  # every other run, not every run
    # Never in the round itself.
    assert all(keys.count("chopp @ pituba p1") <= 1 for keys in runs.values())


def test_a_sweep_over_the_cap_searches_the_smaller_circles_and_finishes_first(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch, plan: Callable[..., None]
) -> None:
    plan(
        ("chopp", "a"),
        mvp=("pituba",),
        smaller={"pituba": {"p-1": (-13.0, -38.46, 1), "p-2": (-12.99, -38.47, 1)}},
    )
    monkeypatch.setattr(precodahora, "MAX_SEARCHES", 2)

    def answer(request: httpx.Request) -> httpx.Response:
        crowded = form(request)["termo"] == "chopp" and form(request)["raio"] == "2"
        body = {"totalRegistros": 160 if crowded else 3, "totalPaginas": 4 if crowded else 1, "resultado": []}
        return httpx.Response(200, json=body)

    assert [run_at(polite, hours, site(answer=answer)) for hours in (0, 24)] == [
        ["osm", "chopp @ pituba p1", "chopp @ p-1 p1"],
        ["chopp @ p-2 p1", "a @ pituba p1"],  # not due, but the sweep finishes before the round goes on
    ]


def test_a_new_sweep_replaces_what_is_left_of_the_last_one(
    polite: Callable[..., PoliteClient], plan: Callable[..., None], cache: Path
) -> None:
    plan(("chopp", "a"), mvp=("pituba",), smaller={"pituba": {"p-1": (-13.0, -38.46, 1)}})
    last = (START - timedelta(hours=41)).isoformat()
    saved = {"terms": ["chopp", "a"], "jobs": [["chopp", "p-1"], ["a", "pituba"]], "swept": last}
    (cache / "precodahora-queue.json").write_text(json.dumps(saved))
    assert run_at(polite, 0) == ["osm", "chopp @ pituba p1", "a @ pituba p1"]


def test_a_queue_saved_before_the_sweeps_starts_one(
    polite: Callable[..., PoliteClient], plan: Callable[..., None], cache: Path
) -> None:
    # Production's round on 7 Oct 2026: chopp around the MVP among the other searches, no sweep yet.
    plan(("chopp", "a"))
    jobs = [["a", "pituba"], ["chopp", "rio-vermelho"], ["a", "rio-vermelho"]]
    (cache / "precodahora-queue.json").write_text(json.dumps({"terms": ["chopp", "a"], "jobs": jobs}))
    assert run_at(polite, 0) == [
        "osm",
        "chopp @ pituba p1",
        "chopp @ rio-vermelho p1",
        "a @ pituba p1",
        "a @ rio-vermelho p1",
    ]
    assert json.loads((cache / "precodahora-queue.json").read_text())["swept"] == START.isoformat()


def test_a_saved_queue_loses_dropped_terms_and_circles_but_not_its_place(
    polite: Callable[..., PoliteClient], plan: Callable[..., None], cache: Path
) -> None:
    plan(("a", "c"), mvp=("pituba",))
    saved = {"terms": ["a", "b", "c"], "jobs": [["b", "pituba"], ["a", "gone"], ["c", "pituba"]]}
    (cache / "precodahora-queue.json").write_text(json.dumps(saved))
    assert run_tasks(offline(polite, site())) == ["osm", "c @ pituba p1"]


def test_terms_added_since_join_the_round_mvp_first(
    polite: Callable[..., PoliteClient], plan: Callable[..., None], cache: Path
) -> None:
    plan(("a", "b", "new"), mvp=("pituba",))
    saved = {"terms": ["a", "b"], "city_terms": [], "jobs": [["b", "pituba"]]}
    (cache / "precodahora-queue.json").write_text(json.dumps(saved))
    assert run_tasks(offline(polite, site())) == ["osm", "new @ pituba p1", "b @ pituba p1"]


def test_city_circles_added_since_join_the_round_last(
    polite: Callable[..., PoliteClient],
    monkeypatch: pytest.MonkeyPatch,
    plan: Callable[..., None],
    cache: Path,
) -> None:
    plan(("a",), mvp=("pituba",))
    city = {"old": (-12.97, -38.51, 1), "new": (-12.98, -38.50, 1)}
    monkeypatch.setattr(precodahora, "CITY", city)
    monkeypatch.setattr(precodahora, "CITY_TERMS", ("a",))
    monkeypatch.setattr(precodahora, "PLACES", {**precodahora.PLACES, **city})
    saved = {"terms": ["a"], "city_terms": ["a"], "city_places": ["old"], "jobs": [["a", "old"]]}
    (cache / "precodahora-queue.json").write_text(json.dumps(saved))
    assert run_tasks(offline(polite, site())) == ["osm", "a @ old p1", "a @ new p1"]


def test_a_queue_saved_before_rounds_kept_their_terms(
    polite: Callable[..., PoliteClient], plan: Callable[..., None], cache: Path
) -> None:
    # Such a round had every term but the last two ("original" and "litrao" in production).
    plan(("a", "b", "new", "newer"), mvp=("pituba",))
    (cache / "precodahora-queue.json").write_text(json.dumps({"jobs": [["b", "pituba"]]}))
    assert run_tasks(offline(polite, site())) == [
        "osm",
        "new @ pituba p1",
        "newer @ pituba p1",
        "b @ pituba p1",
    ]


def test_matches_against_an_older_overpass_answer_when_it_is_down(
    polite: Callable[..., PoliteClient], plan: Callable[..., None], cache: Path
) -> None:
    plan(("heineken",))
    run_tasks(offline(polite, site()))
    saved = cache / "precodahora-osm.json"
    saved.write_text(saved.read_text().replace(date.today().isoformat(), "2026-10-01"))
    down = site()
    down.routes |= dict.fromkeys(OVERPASS, 503)
    adapter = offline(polite, down)
    keys = []
    for task in adapter.tasks():
        keys.append(task.key)
        try:
            adapter.parse(adapter.fetch(task))
        except RuntimeError:
            assert task.key == "osm"
    assert keys == ["osm", "heineken @ pituba p1", "heineken @ rio-vermelho p1"]
    assert [candidate.name for candidate in adapter.osm or []] == ["Boteco do Zé", "Bar da Maria"]


def test_no_searches_without_openstreetmap(polite: Callable[..., PoliteClient]) -> None:
    fake = site()
    fake.routes |= dict.fromkeys(OVERPASS, 503)
    adapter = PrecoDaHora(polite(fake), cnpjs=CnpjRegister(polite(FakeSite({})), None))
    tasks = adapter.tasks()
    osm = next(tasks)
    with pytest.raises(RuntimeError):
        adapter.fetch(osm)
    assert list(tasks) == []


@pytest.mark.parametrize(
    ("page", "answer", "reason"),
    [
        ('<meta id="validate" data-id="t"><meta id="rc" data-id="6Lc-key">', None, "CAPTCHA"),
        ("<html>maintenance</html>", None, "no CSRF token"),
        (PAGE, httpx.Response(200, text="<html>login</html>"), "expected JSON"),
        (PAGE, httpx.Response(429), "HTTP 429"),
    ],
)
def test_stops_at_the_first_refusal(
    polite: Callable[..., PoliteClient], page: str, answer: httpx.Response | None, reason: str
) -> None:
    adapter = PrecoDaHora(
        polite(site(page=page, answer=answer)), cnpjs=CnpjRegister(polite(FakeSite({})), None)
    )
    with pytest.raises(BlockedError, match=reason):
        adapter.fetch(Task("chopp @ pituba p1", SEARCH_URL, {"termo": "chopp", "pagina": 1}))


def test_renews_an_expired_session_once(polite: Callable[..., PoliteClient]) -> None:
    answers = iter([httpx.Response(400), httpx.Response(200, json={"resultado": []}), httpx.Response(400)])
    fake = site(answer=lambda request: next(answers))
    adapter = PrecoDaHora(polite(fake), cnpjs=CnpjRegister(polite(FakeSite({})), None))
    task = Task("chopp @ pituba p1", SEARCH_URL, {"termo": "chopp", "pagina": 1})
    assert adapter.fetch(task).json() == {"resultado": []}
    with pytest.raises(httpx.HTTPStatusError):
        adapter.fetch(task)
    assert [r.method for r in fake.requests if r.url.path == "/produtos/"] == [
        "GET",
        "POST",
        "GET",
        "POST",
        "POST",
    ]


def test_supermarkets_and_depositos_single_beers_become_their_own_venues(adapter: PrecoDaHora) -> None:
    """With CB_STORES on (#438), a shop's single beers list in its category; its packs still don't."""
    adapter.stores = True
    store = shop("SUPERMERCADO BOM PRECO LTDA", "11222333000144", -13.0110, -38.4890)
    deposito = shop("DEPOSITO DE BEBIDAS RIO VERMELHO", "55666777000188", -13.0105, -38.4895)
    items = search(
        adapter,
        page(
            sale("CERV BRAHMA CHOPP LT 350ML", 2.99, store),
            sale("CERV BRAHMA CHOPP LT 350ML C/12", 32.0, store, unidade="FD"),
            sale("CERV BRAHMA CHOPP LT 350ML", 3.19, deposito),
        ),
        key="chopp @ rio-vermelho cheap",
    )
    venues = {item.source_ref: item.data for item in items if item.kind == "venue"}
    assert venues["cnpj:11222333000144"]["kind"] == "outro"
    assert venues["cnpj:11222333000144"]["on_site"] is False
    assert venues["cnpj:55666777000188"]["kind"] == "deposito"
    deals = [item.data for item in items if item.kind == "deal"]
    assert sorted(deal["price"] for deal in deals) == [2.99, 3.19]
    assert adapter.store_refs == {"cnpj:11222333000144", "cnpj:55666777000188"}


def test_beer_at_bar_prices_lists_as_a_bar_price_whatever_the_shop(adapter: PrecoDaHora) -> None:
    """A venue can be a shop and a bar (owner, 11 Oct 2026): a depósito's beer at three times the
    supermarkets' is drunk there; a butcher's that charges like a bar becomes a restaurant."""
    adapter.stores = True
    store = shop("SUPERMERCADO BOM PRECO LTDA", "11222333000144", -13.0110, -38.4890)
    deposito = shop("DEPOSITO DE BEBIDAS RIO VERMELHO", "55666777000188", -13.0105, -38.4895)
    butcher = shop("O BAIANO CHURRASQUEIRO", "33444555000166", -13.0100, -38.4880)
    items = search(
        adapter,
        page(
            *(sale("CERV BRAHMA CHOPP LT 350ML", price, store) for price in (2.99, 3.09, 3.19)),
            sale("CERV BRAHMA CHOPP LT 350ML", 3.29, deposito),
            sale("CERV BRAHMA CHOPP LT 350ML", 8.0, deposito),
            sale("CERV BRAHMA CHOPP LT 350ML", 9.0, butcher),
            sale("CERV BRAHMA CHOPP LT 350ML", 3.5, butcher),
        ),
        key="chopp @ rio-vermelho cheap",
    )
    deals = {(item.data["venue_ref"], item.data["price"]): item for item in items if item.kind == "deal"}
    assert deals[("cnpj:55666777000188", 3.29)].data["on_site"] is None  # to take away, as the depósito
    table = deals[("cnpj:55666777000188", 8.0)]
    assert table.data["on_site"] is True and table.source_ref.endswith("|on_site")
    venues = {item.source_ref: item.data for item in items if item.kind == "venue"}
    assert (venues["cnpj:33444555000166"]["kind"], venues["cnpj:33444555000166"]["on_site"]) == (
        "restaurante",
        True,
    )
    assert venues["cnpj:33444555000166"]["name"] == "O Baiano Churrasqueiro"
    assert ("cnpj:33444555000166", 9.0) in deals and ("cnpj:33444555000166", 3.5) not in deals


def test_shop_category() -> None:
    assert shop_category("SUPERMERCADO X", None) == "mercado"
    assert shop_category("DISTRIBUIDORA DE BEBIDAS Y", None) == "deposito"
    assert shop_category("LOJA Z", 4723700) == "deposito"
    assert shop_category("FARMACIA W", None) is None
