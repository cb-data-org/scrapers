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
from .matching import distance_m

log = logging.getLogger(__name__)

BASE = "https://precodahora.ba.gov.br"
URL = f"{BASE}/produtos/"
#: Pituba's nightlife streets, 1 km wide (the site's smallest), around the bars below.
PLACE = (-12.9990, -38.4590, 1)
#: Generic words first, so a bar shows whatever brand it sells.
TERMS = (
    "cerveja",
    "chopp",
    "chope",
    "heineken",
    "brahma",
    "stella",
    "budweiser",
    "corona",
    "amstel",
    "spaten",
    "original",
    "eisenbahn",
    "patagonia",
    "michelob",
    "devassa",
    "imperio",
)
#: The site shows at most 4 pages (100 sales) a search, dearest first; the cheapest-first order shows the rest
#: when a search has more.
PAGES = 4
#: The bars on our map (owner, 11 Oct 2026), to find on receipts by where the shop is, whatever its name.
TARGETS = {
    "Ponte Aérea": (-13.003996, -38.456766),
    "La Bottega": (-13.002969, -38.457814),
    "Zaccaria": (-12.993547, -38.455438),
    "Bar Preto": (-12.991626, -38.460708),
    "SP20 (R. São Paulo, 20)": (-13.0040, -38.4590),
    "Bar Piauí (control)": (-13.006676, -38.464122),
}
NEAR_M = 150.0
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
    searched: list[str] = []
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
                total = 0
                for order in ("preco.desc", "preco.asc"):
                    # Cheapest first only for what the dearest-first pages couldn't show.
                    pages = PAGES if order == "preco.desc" else min(PAGES, -(-(total - PAGES * 25) // 25))
                    for number in range(1, pages + 1):
                        data = {
                            "termo": term,
                            "horas": 72,
                            "latitude": lat,
                            "longitude": lng,
                            "raio": radius,
                            "pagina": number,
                            "ordenar": order,
                        }
                        answer = client.post(URL, data=data, headers=headers, retries=0).json()
                        rows = answer.get("resultado") if isinstance(answer, dict) else None
                        if not rows:
                            break
                        total = int(answer.get("totalRegistros") or 0)
                        searched.append(f"{term} {order} p{number}: {total}")
                        for row in rows:
                            shop, product = row.get("estabelecimento") or {}, row.get("produto") or {}
                            cnpj = re.sub(r"\D", "", str(shop.get("cnpj") or ""))
                            entry = shops.setdefault(
                                cnpj,
                                {
                                    "receipt": shop.get("nomeEstabelecimento"),
                                    "terms": set(),
                                    "sales": 0,
                                    "lat": shop.get("latitude"),
                                    "lng": shop.get("longitude"),
                                    "address": " ".join(
                                        str(shop.get(key) or "") for key in ("endLogradouro", "endNumero")
                                    ).strip(),
                                    "samples": [],
                                },
                            )
                            entry["terms"].add(term)
                            entry["sales"] += 1
                            if len(entry["samples"]) < 4:
                                entry["samples"].append(
                                    f"{product.get('descricao')} R$ {product.get('precoUnitario')}"
                                )
                        if number >= int(answer.get("totalPaginas") or 0):
                            break
        except (BlockedError, DisallowedError, httpx.HTTPError) as error:
            status = f"stopped: {error}"[:200]
    # The register at its own, quicker pace (as the adapter looks it up), not Preço da Hora's 20 s.
    with PoliteClient(delay=3.0, retries=1, timeout=30) as lookups:
        register = CnpjRegister(lookups, default_cache_file(), date.today())
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
                "sample": "; ".join(entry["samples"]),
            }
            for entry in shops.values()
            if normalize(wanted) in normalize(f"{entry['receipt']} {entry['trade'] or ''}")
        ]
        for wanted in WANTED
    }
    lines = [
        "# Bar finder",
        f"Status: {status}. {len(shops)} shops sold beer around Pituba (1 km, last 72 h)."
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
    lines += ["", "## Shops on receipts near each bar (by place, any name)"]
    for label, (lat, lng) in TARGETS.items():
        near = sorted(
            (
                (distance_m(lat, lng, float(entry["lat"]), float(entry["lng"])), entry)
                for entry in shops.values()
                if entry.get("lat") not in (None, "") and entry.get("lng") not in (None, "")
            ),
            key=lambda pair: pair[0],
        )
        close = [(metres, entry) for metres, entry in near if metres <= NEAR_M]
        lines.append(
            f"- **{label}**: "
            + (
                "; ".join(
                    f"{entry['trade'] or entry['receipt']} ({round(metres)} m, CNAE {entry['cnae']}, "
                    f"{entry['address']}, {entry['sales']} sales: {'; '.join(entry['samples'])})"
                    for metres, entry in close
                )
                or (
                    f"nothing within {round(NEAR_M)} m; nearest: "
                    + ", ".join(f"{e['trade'] or e['receipt']} {round(m)} m" for m, e in near[:3])
                )
            )
        )
    lines += ["", "Searches (total sales each): " + " · ".join(searched)]
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
