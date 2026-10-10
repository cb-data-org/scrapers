"""State price tools probe (#434): can we read beer prices from other states' receipt (NFC-e) tools, and how
fresh are they? A one-off, read-only proof of concept, run by hand from GitHub Actions (never from anyone's
own connection):

- Bahia (Preço da Hora, our adapter's site): one search per city and beer around capitals and big cities
  beyond Salvador, to see how many sales come back, from how many shops, and how old they are.
- Paraíba (Preço da Hora's original): whether the site answers from outside the state, and whether it works
  like Bahia's (same session, CSRF token and search).
- Paraná (Menor Preço, Nota Paraná): whether the public JSON answers, and whether its shops really are near
  the searched place (a first look suggested scrambled data).

The rest isn't probed at all, because reading it would break our rules: Amazonas (reCAPTCHA), Mato Grosso
(login) and the Menor Preço Brasil app (gov.br login with a personal CPF) for RJ, PE, CE, DF, RS, SC, ES and
others.

Each site gets a handful of requests, 20 s apart, robots.txt obeyed, our bot's user agent, and the probe
stops at the first 403/429 or CAPTCHA. It prints a Markdown report and writes it, with the raw counts, to
--out.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .core import BlockedError, DisallowedError, PoliteClient

log = logging.getLogger(__name__)

DELAY = 20.0
BEER_NCM = "2203"
TERMS = ("heineken", "brahma")
RETAIL = re.compile(
    r"\b(supermerc|mercad|atacad|atakarejo|assai|hiper|gbarbosa|mercantil|farmac|drogar|distrib|deposito|"
    r"conveni|comercial|emporio|mini ?box|minibox|padaria|panific|acougue)",
    re.IGNORECASE,
)
BAR = re.compile(
    r"\b(bar|boteco|buteco|restaurante|pub|choperia|chopperia|cervejaria|barraca|espetinho)\b", re.I
)

# Places: (state, city, latitude, longitude). Radius 5 km around the centre.
BAHIA = [
    ("BA", "Salvador (control)", -12.9855, -38.4569),
    ("BA", "Feira de Santana", -12.2664, -38.9663),
    ("BA", "Vitória da Conquista", -14.8615, -40.8442),
    ("BA", "Camaçari", -12.6996, -38.3263),
    ("BA", "Itabuna", -14.7876, -39.2781),
    ("BA", "Juazeiro", -9.4116, -40.4986),
]
PARAIBA = [("PB", "João Pessoa", -7.1195, -34.8450), ("PB", "Campina Grande", -7.2307, -35.8817)]
PARANA = [("PR", "Curitiba", -25.4284, -49.2733), ("PR", "Londrina", -23.3045, -51.1696)]

NOT_PROBED = [
    ("AM", "Busca Preço AM", "reCAPTCHA on the search form: we never bypass it"),
    ("MT", "Menor Preço (Nota MT)", "needs a Nota MT login"),
    (
        "RJ, PE, CE, DF, RS, SC, ES, AL, SE, PI, RN, PA, AC, AP, RO, RR, TO",
        "Menor Preço Brasil (Sefaz-RS/PROCERGS)",
        "app only, behind a personal gov.br login: needs a formal data request instead",
    ),
    ("SP, MG, GO, MS", "none for groceries", "no public receipt price tool (MG and GO cover fuel only)"),
]


@dataclass
class Result:
    state: str
    city: str
    term: str
    status: str
    total: int | None = None
    sales: int = 0
    beer: int = 0
    shops: int = 0
    retail_shops: int = 0
    bar_shops: int = 0
    newest_hours: float | None = None
    oldest_hours: float | None = None
    median_hours: float | None = None
    median_price: float | None = None
    near_share: float | None = None
    note: str = ""
    sample_keys: list[str] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def hours_ago(value: Any, now: datetime) -> float | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return round((now - moment).total_seconds() / 3600, 1)


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    rad = math.radians
    a = (
        math.sin(rad(lat2 - lat1) / 2) ** 2
        + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(rad(lng2 - lng1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(a))


def geohash(lat: float, lng: float, precision: int = 6) -> str:
    alphabet = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_range, lng_range = [-90.0, 90.0], [-180.0, 180.0]
    bits, value, even, out = 0, 0, True, []
    while len(out) < precision:
        target, span = (lng, lng_range) if even else (lat, lat_range)
        middle = (span[0] + span[1]) / 2
        value <<= 1
        if target >= middle:
            value |= 1
            span[0] = middle
        else:
            span[1] = middle
        even = not even
        bits += 1
        if bits == 5:
            out.append(alphabet[value])
            bits, value = 0, 0
    return "".join(out)


def summarise(
    result: Result, rows: list[dict[str, Any]], place: tuple[str, str, float, float], now: datetime
) -> None:
    """Counts for a Preço da Hora page of sales: beer, shops, retail vs bars, ages and prices."""
    _, _, lat, lng = place
    result.sales = len(rows)
    shops: dict[str, str] = {}
    ages, prices, near = [], [], []
    for row in rows:
        product, shop = row.get("produto") or {}, row.get("estabelecimento") or {}
        if not str(product.get("ncm") or "").startswith(BEER_NCM):
            continue
        result.beer += 1
        shops[str(shop.get("cnpj") or shop.get("nomeEstabelecimento"))] = str(
            shop.get("nomeEstabelecimento") or ""
        )
        age = hours_ago(product.get("data"), now)
        if age is not None:
            ages.append(age)
        if isinstance(product.get("precoUnitario"), int | float):
            prices.append(float(product["precoUnitario"]))
        with contextlib.suppress(KeyError, TypeError, ValueError):
            near.append(distance_km(lat, lng, float(shop["latitude"]), float(shop["longitude"])) <= 10)
    result.shops = len(shops)
    result.retail_shops = sum(bool(RETAIL.search(name)) for name in shops.values())
    result.bar_shops = sum(bool(BAR.search(name)) for name in shops.values())
    if ages:
        result.newest_hours, result.oldest_hours = min(ages), max(ages)
        result.median_hours = statistics.median(ages)
    if prices:
        result.median_price = statistics.median(prices)
    if near:
        result.near_share = round(sum(near) / len(near), 2)


def preco_da_hora(
    client: PoliteClient, base: str, places: list[tuple[str, str, float, float]]
) -> list[Result]:
    """Bahia's and Paraíba's sites: one session, then one search per place and term (first page only)."""
    url = f"{base}/produtos/"
    results: list[Result] = []
    now = datetime.now(UTC)
    try:
        page = client.get(url)
    except (BlockedError, DisallowedError, httpx.HTTPError) as error:
        return [Result(places[0][0], "(site)", "-", "unreachable", note=str(error)[:160])]
    if re.search(r'id="rc"\s+data-id="[^"]+"', page.text):
        return [
            Result(places[0][0], "(site)", "-", "captcha", note="the search page has a CAPTCHA switched on")
        ]
    token = re.search(r'id="validate"\s+data-id="([^"]+)"', page.text)
    if not token:
        return [
            Result(
                places[0][0],
                "(site)",
                "-",
                "different",
                note=f"no CSRF token as in Bahia ({len(page.text)} B page)",
            )
        ]
    headers = {
        "X-CSRFToken": token.group(1),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": url,
        "Origin": base,
    }
    for place in places:
        state, city, lat, lng = place
        for term in TERMS:
            result = Result(state, city, term, "ok")
            data = {
                "termo": term,
                "horas": 72,
                "latitude": lat,
                "longitude": lng,
                "raio": 5,
                "pagina": 1,
                "ordenar": "preco.asc",
            }
            try:
                answer = client.post(url, data=data, headers=headers, retries=0).json()
            except (BlockedError, DisallowedError) as error:
                result.status, result.note = "blocked", str(error)[:160]
                results.append(result)
                return results
            except (httpx.HTTPError, json.JSONDecodeError) as error:
                result.status, result.note = "error", str(error)[:160]
                results.append(result)
                continue
            rows = answer.get("resultado") if isinstance(answer, dict) else None
            result.total = int(answer.get("totalRegistros") or 0) if isinstance(answer, dict) else None
            if isinstance(rows, list):
                summarise(result, rows, place, now)
                if rows:
                    result.sample_keys = sorted(rows[0].get("produto", {}).keys())[:12]
            else:
                result.status = "empty" if result.total == 0 else "unexpected"
                result.note = str(list(answer)[:8]) if isinstance(answer, dict) else type(answer).__name__
            results.append(result)
    return results


def menor_preco_parana(client: PoliteClient) -> list[Result]:
    """Paraná's public JSON: one search per place for beer, then a check that the shops are near the place."""
    results: list[Result] = []
    now = datetime.now(UTC)
    base = "https://menorpreco.notaparana.pr.gov.br/api/v1/produtos"
    for place in PARANA:
        state, city, lat, lng = place
        for term in TERMS:
            result = Result(state, city, term, "ok")
            params = {"local": geohash(lat, lng), "termo": term, "raio": 5, "offset": 0}
            try:
                answer = client.get(base, params=params, retries=0).json()
            except (BlockedError, DisallowedError) as error:
                result.status, result.note = "blocked", str(error)[:160]
                results.append(result)
                return results
            except (httpx.HTTPError, json.JSONDecodeError) as error:
                result.status, result.note = "error", str(error)[:160]
                results.append(result)
                continue
            rows = answer.get("produtos") if isinstance(answer, dict) else answer
            if not isinstance(rows, list):
                result.status = "unexpected"
                result.note = str(list(answer)[:8]) if isinstance(answer, dict) else type(answer).__name__
                results.append(result)
                continue
            result.total, result.sales = len(rows), len(rows)
            ages, prices, near, names = [], [], [], set()
            for row in rows:
                shop = row.get("estabelecimento") or {}
                names.add(str(shop.get("nm_fan") or shop.get("nm_emp") or shop.get("nome") or ""))
                age = hours_ago(row.get("datahora") or row.get("data"), now)
                if age is not None:
                    ages.append(age)
                price = row.get("valor") or row.get("preco")
                with contextlib.suppress(TypeError, ValueError):
                    prices.append(float(price))
                with contextlib.suppress(KeyError, TypeError, ValueError):
                    near.append(distance_km(lat, lng, float(shop["lat"]), float(shop["lng"])) <= 10)
            result.shops = len(names)
            result.beer = len(rows)
            result.retail_shops = sum(bool(RETAIL.search(name)) for name in names)
            result.bar_shops = sum(bool(BAR.search(name)) for name in names)
            if ages:
                result.newest_hours, result.oldest_hours, result.median_hours = (
                    min(ages),
                    max(ages),
                    statistics.median(ages),
                )
            if prices:
                result.median_price = statistics.median(prices)
            if near:
                result.near_share = round(sum(near) / len(near), 2)
            if rows:
                result.sample_keys = sorted(rows[0].keys())[:12]
                # A few rows as the API gives them, to judge whether the shops and products are real.
                result.samples = [
                    {
                        "desc": row.get("desc"),
                        "valor": row.get("valor"),
                        "distkm": row.get("distkm"),
                        "datahora": row.get("datahora"),
                        "local": row.get("local"),
                        "estabelecimento": row.get("estabelecimento"),
                    }
                    for row in rows[:6]
                ]
            results.append(result)
    return results


def report(results: list[Result]) -> str:
    def fmt(value: Any, suffix: str = "") -> str:
        return "-" if value is None else f"{value}{suffix}"

    lines = [
        "# State price tools probe",
        "",
        f"Run at {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from GitHub Actions. One page (up to 25 sales) per"
        " city and beer, 5 km around the centre, last 72 h where the site takes it.",
        "",
        "| State | City | Beer | Status | Sales matched | Beer on page | Shops | Retail by name"
        " | Bars by name | Newest | Median age | Oldest | Median price | Shops within 10 km | Note |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        cells = [
            r.state,
            r.city,
            r.term,
            r.status,
            fmt(r.total),
            r.beer,
            r.shops,
            r.retail_shops,
            r.bar_shops,
            fmt(r.newest_hours, " h"),
            fmt(r.median_hours, " h"),
            fmt(r.oldest_hours, " h"),
            fmt(r.median_price and f"R$ {r.median_price:.2f}"),
            fmt(r.near_share and f"{r.near_share:.0%}"),
            r.note,
        ]
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    lines += ["", "Not probed (reading them would break our rules):", ""]
    lines += [f"- **{state}**: {tool}, {why}." for state, tool, why in NOT_PROBED]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else None
    results: list[Result] = []
    with PoliteClient(delay=DELAY, retries=1, timeout=30) as client:
        if "--only-pr" not in args:
            results += preco_da_hora(client, "https://precodahora.ba.gov.br", BAHIA)
            results += preco_da_hora(client, "https://precodahora.pb.gov.br", PARAIBA)
        results += menor_preco_parana(client)
    text = report(results)
    print(text)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(text + "\n")
        (out / "results.json").write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
