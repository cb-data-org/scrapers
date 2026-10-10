import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers import cnpj
from cb_scrapers.cnpj import CNPJA, MINHA_RECEITA, CnpjRegister
from cb_scrapers.core import PoliteClient
from tests.fakes import FakeSite

BAR = "11222333000181"
MEI = "44555666000172"
TODAY = date(2026, 10, 6)


def minha_receita(**fields: Any) -> str:
    body = {
        "cnpj": BAR,
        "razao_social": "BOTECO DO ZE COMERCIO DE BEBIDAS LTDA",
        "nome_fantasia": "BOTECO DO ZE",
        "cnae_fiscal": 5611204,
        "situacao_cadastral": 2,
        "codigo_natureza_juridica": 2062,
        "opcao_pelo_mei": False,
        "qsa": [{"nome_socio": "SOMEONE"}],
        "email": "someone@example.com",
    }
    return json.dumps(body | fields)


def test_reads_the_register_and_keeps_only_what_it_needs(
    polite: Callable[..., PoliteClient], tmp_path: Path
) -> None:
    site = FakeSite({MINHA_RECEITA.format(cnpj=BAR): minha_receita()})
    cache = tmp_path / "cnpj.json"
    register = CnpjRegister(polite(site), cache, TODAY)
    company = register.lookup(BAR)
    assert company is not None
    assert (company.cnae, company.active, company.trade_name, company.legal_name, company.individual) == (
        5611204,
        True,
        "BOTECO DO ZE",
        "BOTECO DO ZE COMERCIO DE BEBIDAS LTDA",
        False,
    )
    stored = json.loads(cache.read_text())
    assert set(stored[BAR]) == {
        "cnpj",
        "cnae",
        "active",
        "trade_name",
        "legal_name",
        "individual",
        "checked_on",
        "secondary",
        "mei",
    }
    # The next run reads the file instead of asking again.
    again = CnpjRegister(polite(FakeSite({})), cache, TODAY)
    assert again.lookup(BAR) == company
    assert again.lookups == 0


def test_never_keeps_a_one_person_business_legal_name(polite: Callable[..., PoliteClient]) -> None:
    body = minha_receita(
        cnpj=MEI, razao_social="FULANO DE TAL 12345678900", nome_fantasia="", codigo_natureza_juridica=2135
    )
    register = CnpjRegister(polite(FakeSite({MINHA_RECEITA.format(cnpj=MEI): body})), None, TODAY)
    company = register.lookup(MEI)
    assert company is not None
    assert (company.trade_name, company.legal_name, company.individual) == (None, None, True)


def test_asks_again_after_three_months(polite: Callable[..., PoliteClient], tmp_path: Path) -> None:
    site = FakeSite({MINHA_RECEITA.format(cnpj=BAR): minha_receita()})
    cache = tmp_path / "cnpj.json"
    CnpjRegister(polite(site), cache, date(2026, 6, 1)).lookup(BAR)
    register = CnpjRegister(polite(site), cache, TODAY)
    register.lookup(BAR)
    assert register.lookups == 1


def test_unknown_cnpjs_are_none(polite: Callable[..., PoliteClient]) -> None:
    register = CnpjRegister(polite(FakeSite({MINHA_RECEITA.format(cnpj=BAR): 404})), None, TODAY)
    assert register.lookup(BAR) is None


def test_falls_back_to_cnpja_and_stops_asking_a_service_that_keeps_failing(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cnpj, "MAX_FAILURES", 1)
    cnpja = {
        "taxId": BAR,
        "alias": "Boteco do Zé",
        "company": {"name": "BOTECO DO ZE LTDA", "nature": {"id": 2062}, "simei": {"optant": False}},
        "status": {"id": 2, "text": "Ativa"},
        "mainActivity": {"id": 5611204},
    }
    site = FakeSite(
        {
            MINHA_RECEITA.format(cnpj=BAR): 429,
            MINHA_RECEITA.format(cnpj=MEI): 429,
            CNPJA.format(cnpj=BAR): json.dumps(cnpja),
            CNPJA.format(cnpj=MEI): json.dumps(cnpja | {"taxId": MEI}),
        }
    )
    register = CnpjRegister(polite(site), None, TODAY)
    company = register.lookup(BAR)
    assert company is not None
    assert (company.cnae, company.trade_name, company.legal_name) == (
        5611204,
        "Boteco do Zé",
        "BOTECO DO ZE LTDA",
    )
    register.lookup(MEI)
    asked = [request.url.path for request in site.requests if request.url.host == "minhareceita.org"]
    assert asked == ["/robots.txt", f"/{BAR}"]


def test_a_passing_failure_is_forgiven(polite: Callable[..., PoliteClient]) -> None:
    answers = iter([httpx.Response(503), httpx.Response(503), httpx.Response(200, text=minha_receita())])
    site = FakeSite(
        {MINHA_RECEITA.format(cnpj=BAR): lambda request: next(answers), CNPJA.format(cnpj=BAR): 404}
    )
    register = CnpjRegister(polite(site), None, TODAY)
    assert register.lookup(BAR) is None  # minhareceita failed (one retry), CNPJá doesn't know it
    company = register.lookup(BAR)
    assert company is not None and company.cnae == 5611204  # minhareceita is asked again


def test_keeps_secondary_activities_and_asks_again_for_rows_without_them(
    polite: Callable[..., PoliteClient], tmp_path: Path
) -> None:
    body = json.loads(minha_receita())
    body["cnaes_secundarios"] = [{"codigo": 4723700, "descricao": "Comércio varejista de bebidas"}]
    site = FakeSite({MINHA_RECEITA.format(cnpj=BAR): json.dumps(body)})
    cache = tmp_path / "cnpj.json"
    old_row = {"cnpj": BAR, "cnae": 5611204, "active": True, "trade_name": "X", "legal_name": None}
    cache.write_text(json.dumps({BAR: old_row | {"individual": False, "checked_on": TODAY.isoformat()}}))
    register = CnpjRegister(polite(site), cache, TODAY)
    company = register.lookup(BAR)
    assert company is not None
    assert (company.secondary, company.mei, register.lookups) == ((4723700,), False, 1)
