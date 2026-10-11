"""Do these bars sell beer on Preço da Hora's receipts at all? A one-off, read-only check (owner,
11 Oct 2026), run by hand from GitHub Actions (never from anyone's own connection): every page of a few
beers around a place, each shop's trade name from the CNPJ register, the wanted names matched against them.

    uv run python -m cb_scrapers.bar_finder --out DIR

Same manners as the adapter: 20 s between searches, robots.txt obeyed, our bot's user agent, and a stop at the
first 403/429 or CAPTCHA.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .cnpj import CnpjRegister, default_cache_file
from .core import BlockedError, DisallowedError, PoliteClient, normalize

log = logging.getLogger(__name__)

BASE = "https://precodahora.ba.gov.br"
URL = f"{BASE}/produtos/"
#: Pituba and its nightlife streets (where Ponte Aérea, La Bottega, Bar Preto and Bar Piauí are on the map).
PLACE = (-13.0010, -38.4600, 2)
TERMS = ("chopp", "heineken", "brahma", "original", "ipa", "stella", "amstel", "spaten")
PAGES = 4
WANTED = (
    "bunker",
    "preto",
    "zaccaria",
    "bottega",
    "ponte aerea",
    "baiano churrasqueiro",
    "piaui",
    "sp20",
    "sp 20",
)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else None
    shops: dict[str, dict[str, Any]] = {}
    status = "ok"
    with PoliteClient(delay=20.0, retries=1, timeout=30) as client:
        try:
            page = client.get(URL)
            token = re.search(r'id="validate"\s+data-id="([^"]+)"', page.text)
            if re.search(r'id="rc"\s+data-id="[^"]+"', page.text) or not token:
                raise BlockedError(URL, "CAPTCHA or no CSRF token")
            headers = {
                "X-CSRFToken": token.group(1),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": URL,
                "Origin": BASE,
            }
            lat, lng, radius = PLACE
            for term in TERMS:
                for number in range(1, PAGES + 1):
                    data = {
                        "termo": term,
                        "horas": 72,
                        "latitude": lat,
                        "longitude": lng,
                        "raio": radius,
                        "pagina": number,
                        "ordenar": "preco.desc",
                    }
                    answer = client.post(URL, data=data, headers=headers, retries=0).json()
                    rows = answer.get("resultado") if isinstance(answer, dict) else None
                    if not rows:
                        break
                    for row in rows:
                        shop, product = row.get("estabelecimento") or {}, row.get("produto") or {}
                        cnpj = re.sub(r"\D", "", str(shop.get("cnpj") or ""))
                        entry = shops.setdefault(
                            cnpj, {"receipt": shop.get("nomeEstabelecimento"), "terms": set(), "sales": 0}
                        )
                        entry["terms"].add(term)
                        entry["sales"] += 1
                        entry.setdefault("sample", product.get("descricao"))
                    if number >= int(answer.get("totalPaginas") or 0):
                        break
        except (BlockedError, DisallowedError, httpx.HTTPError) as error:
            status = f"stopped: {error}"[:200]
        register = CnpjRegister(client, default_cache_file(), date.today())
        for cnpj, entry in shops.items():
            company = register.lookup(cnpj) if len(cnpj) == 14 else None
            entry["trade"] = company.trade_name if company else None
            entry["cnae"] = company.cnae if company else None
    found = {
        wanted: [
            {
                "receipt": entry["receipt"],
                "trade": entry["trade"],
                "cnae": entry["cnae"],
                "sales": entry["sales"],
                "terms": sorted(entry["terms"]),
                "sample": entry["sample"],
            }
            for entry in shops.values()
            if normalize(wanted) in normalize(f"{entry['receipt']} {entry['trade'] or ''}")
        ]
        for wanted in WANTED
    }
    lines = [
        "# Bar finder",
        f"Status: {status}. {len(shops)} shops sold beer around Pituba (2 km, last 72 h)."
        f" Terms: {', '.join(TERMS)}.",
        "",
    ]
    for wanted, hits in found.items():
        lines.append(
            f"- **{wanted}**: "
            + (
                "; ".join(
                    f"{hit['trade'] or hit['receipt']} (CNAE {hit['cnae']}, {hit['sales']} sales, "
                    f"{', '.join(hit['terms'])}; e.g. {hit['sample']})"
                    for hit in hits
                )
                or "not on these receipts"
            )
        )
    bars = sorted(
        (entry["trade"] or entry["receipt"] or "?")
        for entry in shops.values()
        if entry["cnae"] and str(entry["cnae"]).startswith("5611")
    )
    lines += ["", f"Bars and restaurants (CNAE 5611) on these receipts ({len(bars)}): " + ", ".join(bars)]
    text = "\n".join(lines)
    print(text)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
