"""Two one-off, read-only checks (owner, 11 Oct 2026), run by hand from GitHub Actions (never from anyone's
own connection):

- `venues`: are well-known Salvador bars in OpenStreetMap, and in Overture Maps' places (open data, CDLA
  Permissive 2.0, built from Meta, Microsoft and other sources)? The Overture file is downloaded beforehand by
  the workflow (`overturemaps download --type=place`) and passed with --overture.
- `pdh-api`: can Preço da Hora's search return more per page? Its page script is read for the parameters it
  sends, then one search is tried with each likely page-size parameter, a wider radius and more hours.

    uv run python -m cb_scrapers.source_probe venues --overture places.geojsonseq --out DIR
    uv run python -m cb_scrapers.source_probe pdh-api --out DIR

Same manners as the adapters: robots.txt obeyed, our bot's user agent, 20 s between Preço da Hora calls, and a
stop at the first 403/429 or CAPTCHA.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .core import BlockedError, DisallowedError, PoliteClient, normalize

log = logging.getLogger(__name__)

#: Salvador and Lauro de Freitas (south, west, north, east).
BBOX = (-13.02, -38.70, -12.80, -38.25)
WANTED = {
    "The Bunker": r"bunker",
    "Preto": r"\bpreto\b",
    "Zaccaria": r"zac+aria",
    "La Bottega": r"bottega",
    "Ponte Aérea": r"ponte aerea",
    "Baiano Churrasqueiro": r"baiano churrasqueiro|churrasqueiro",
    "Bar Piauí": r"piaui",
    "SP20": r"\bsp ?20\b",
}
OVERPASS = "https://overpass-api.de/api/interpreter"
#: What Overture calls a place to drink (categories.primary or basic_category).
DRINK = re.compile(r"bar|pub|brew|beer|boteco|tavern|lounge|night_?club|gastropub")

PDH = "https://precodahora.ba.gov.br"
PDH_SEARCH = f"{PDH}/produtos/"
#: A busy spot (Rio Vermelho) and a common beer, so a full page is likely.
PDH_BASE = {
    "termo": "heineken",
    "horas": 72,
    "latitude": -13.0105,
    "longitude": -38.4855,
    "raio": 2,
    "pagina": 1,
    "ordenar": "preco.asc",
}
PAGE_SIZE_NAMES = (
    "registrosporPagina",
    "registrosPorPagina",
    "quantidade",
    "qtd",
    "limite",
    "limit",
    "tamanho",
    "itensPorPagina",
    "porPagina",
    "por_pagina",
    "registros",
    "size",
    "per_page",
    "page_size",
)


def matches(name: str) -> list[str]:
    plain = normalize(name)
    return [label for label, pattern in WANTED.items() if re.search(pattern, plain)]


def osm_places(client: PoliteClient) -> list[dict[str, Any]]:
    south, west, north, east = BBOX
    # Overpass matches case but not accents: "." stands in for an accented letter.
    pattern = "bunker|preto|zac+aria|bottega|ponte a.rea|churrasqueiro|piau.|sp ?20"
    query = (
        f'[out:json][timeout:90];nwr["name"~"{pattern}",i]({south},{west},{north},{east});out tags center;'
    )
    answer = client.post(OVERPASS, data={"data": query}).json()
    return [
        {
            "name": element["tags"].get("name"),
            "tags": element["tags"],
            "id": f"{element['type']}/{element['id']}",
        }
        for element in answer.get("elements", [])
    ]


def osm_bar_count(client: PoliteClient) -> int:
    south, west, north, east = BBOX
    box = f"({south},{west},{north},{east})"
    query = f'[out:json][timeout:90];nwr["amenity"~"^(bar|pub|biergarten)$"]{box};out count;'
    answer = client.post(OVERPASS, data={"data": query}).json()
    tags = (answer.get("elements") or [{}])[0].get("tags", {})
    return int(tags.get("total") or 0)


def overture_places(path: Path) -> tuple[list[dict[str, Any]], Counter[str], int]:
    """The wanted bars, the drinking places' categories, and how many places in all."""
    hits: list[dict[str, Any]] = []
    drink: Counter[str] = Counter()
    total = 0
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            props = feature.get("properties", feature)
            total += 1
            names = props.get("names") or {}
            name = names.get("primary") if isinstance(names, dict) else None
            categories = props.get("categories") or {}
            category = (categories.get("primary") if isinstance(categories, dict) else None) or props.get(
                "basic_category"
            )
            if category and DRINK.search(str(category)):
                drink[str(category)] += 1
            if name and matches(name):
                hits.append(
                    {
                        "name": name,
                        "category": category,
                        "confidence": props.get("confidence"),
                        "sources": sorted({s.get("dataset") for s in props.get("sources") or []} - {None}),
                        "address": ((props.get("addresses") or [{}])[0] or {}).get("freeform"),
                        "wanted": matches(name),
                    }
                )
    return hits, drink, total


def venues(args: list[str]) -> list[str]:
    overture = Path(args[args.index("--overture") + 1]) if "--overture" in args else None
    lines = ["# Venue sources: OpenStreetMap vs Overture Maps", ""]
    with PoliteClient(delay=5.0, retries=1, timeout=120) as client:
        try:
            osm = osm_places(client)
            bars = osm_bar_count(client)
            lines.append(f"OpenStreetMap: {bars} bars/pubs (amenity) in Salvador + Lauro de Freitas.")
            for label in WANTED:
                found = [p for p in osm if label in matches(p["name"] or "")]
                lines.append(
                    f"- OSM **{label}**: "
                    + (
                        "; ".join(
                            f"{p['name']} ({p['tags'].get('amenity') or p['tags'].get('shop') or '?'})"
                            for p in found
                        )
                        or "not found"
                    )
                )
        except (BlockedError, DisallowedError, httpx.HTTPError, ValueError) as error:
            lines.append(f"OpenStreetMap: stopped ({error})"[:200])
    lines.append("")
    if overture and overture.exists():
        hits, drink, total = overture_places(overture)
        lines.append(
            f"Overture Maps: {total} places in the box; {sum(drink.values())} drinking places "
            f"({', '.join(f'{c} {n}' for c, n in drink.most_common(8))})."
        )
        for label in WANTED:
            found = [hit for hit in hits if label in hit["wanted"]]
            lines.append(
                f"- Overture **{label}**: "
                + (
                    "; ".join(
                        f"{h['name']} ({h['category']}, conf {h['confidence']}, {'/'.join(h['sources'])}"
                        f"{', ' + h['address'] if h['address'] else ''})"
                        for h in found[:4]
                    )
                    or "not found"
                )
            )
    else:
        lines.append("Overture Maps: no file.")
    return lines


def pdh_api(args: list[str]) -> list[str]:
    lines = ["# Preço da Hora: can a search return more?", ""]
    with PoliteClient(delay=20.0, retries=0, timeout=30) as client:
        try:
            page = client.get(PDH_SEARCH)
            token = re.search(r'id="validate"\s+data-id="([^"]+)"', page.text)
            if re.search(r'id="rc"\s+data-id="[^"]+"', page.text) or not token:
                raise BlockedError(PDH_SEARCH, "CAPTCHA or no CSRF token")
            # What the page's own scripts send.
            scripts = [
                urljoin(PDH_SEARCH, src)
                for src in re.findall(r'<script[^>]+src="([^"]+)"', page.text)
                if "precodahora" in urljoin(PDH_SEARCH, src) and not re.search(r"jquery|bootstrap|maps", src)
            ]
            lines.append(f"Page scripts: {', '.join(scripts) or 'none'}")
            for script in scripts[:4]:
                text = client.get(script).text
                for match in list(re.finditer(r"pagina|ordenar|raio", text))[:6]:
                    snippet = " ".join(text[max(0, match.start() - 160) : match.end() + 160].split())
                    lines.append(f"  - `{snippet}`")
            headers = {
                "X-CSRFToken": token.group(1),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": PDH_SEARCH,
                "Origin": PDH,
            }

            def search(label: str, **extra: Any) -> tuple[int, Any]:
                answer = client.post(PDH_SEARCH, data={**PDH_BASE, **extra}, headers=headers).json()
                rows = answer.get("resultado") if isinstance(answer, dict) else None
                count = len(rows or [])
                total = answer.get("totalPaginas") if isinstance(answer, dict) else None
                fields = ("totalRegistros", "registrosporPagina", "registrosdaPagina")
                counts = {key: answer.get(key) for key in fields} if isinstance(answer, dict) else answer
                lines.append(f"- {label}: {count} results, totalPaginas {total}, {counts}")
                return count, total

            base, _ = search("baseline (raio 2, horas 72)")
            for name in PAGE_SIZE_NAMES:
                count, _ = search(f"{name}=100", **{name: 100})
                if count > base:
                    lines.append(f"  **{name} gives more per page**")
                    break
            search("raio 10", raio=10)
            search("raio 0.5", raio=0.5)
            search("pagina 5", pagina=5)
            search("horas 168", horas=168)
            search("pagina 2", pagina=2)
        except (BlockedError, DisallowedError, httpx.HTTPError, ValueError) as error:
            lines.append(f"Stopped: {error}"[:200])
    return lines


#: Bars seen on receipts around Pituba (bar finder, 11 Oct 2026), searched by name.
NAMES = ("baiano churrasqueiro", "quintal da esquina", "boteco do caranguejo", "bar piaui")
PITUBA = {"latitude": -12.9990, "longitude": -38.4590, "raio": 2, "horas": 72, "pagina": 1}


def short(value: Any) -> str:
    return " ".join(str(value).split())[:60]


def pdh_search(args: list[str]) -> list[str]:
    """Owner, 11 Oct 2026: does the box's "nome do produto, marca ou o código de barras" work with barcodes,
    and with a bar's name? And does the site have a filter by shop?"""
    lines = ["# Preço da Hora: barcodes and names", ""]
    with PoliteClient(delay=20.0, retries=0, timeout=30) as client:
        try:
            page = client.get(PDH_SEARCH)
            token = re.search(r'id="validate"\s+data-id="([^"]+)"', page.text)
            if re.search(r'id="rc"\s+data-id="[^"]+"', page.text) or not token:
                raise BlockedError(PDH_SEARCH, "CAPTCHA or no CSRF token")
            headers = {
                "X-CSRFToken": token.group(1),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": PDH_SEARCH,
                "Origin": PDH,
            }
            # What the page's own script calls and sends.
            scripts = [
                urljoin(PDH_SEARCH, src)
                for src in re.findall(r'<script[^>]+src="([^"]+)"', page.text)
                if "/site/" in src or "app" in src
            ]
            for script in scripts[:2]:
                text = client.get(script).text
                urls = sorted(set(re.findall(r"""["'](/[a-z][a-z0-9_/-]{2,40}/?)["']""", text)))
                lines.append(f"Script {script}: paths {', '.join(urls[:40])}")
                fields = sorted(
                    set(
                        re.findall(
                            r"\b(gtin|ean|codigo\w*|barras?\w*|cnpj\w*|estabelecimento\w*|termo\w*)\b",
                            text,
                            re.I,
                        )
                    )
                )
                lines.append(f"  fields: {', '.join(fields[:40])}")
            form = re.findall(r'<(?:input|select)[^>]+name="([^"]+)"', page.text)
            lines.append(f"Form fields on the page: {', '.join(sorted(set(form)))}")

            def search(term: str, **extra: Any) -> list[dict[str, Any]]:
                data = {**PITUBA, "termo": term, "ordenar": "preco.desc", **extra}
                answer = client.post(PDH_SEARCH, data=data, headers=headers).json()
                rows = (answer.get("resultado") if isinstance(answer, dict) else None) or []
                total = answer.get("totalRegistros") if isinstance(answer, dict) else None
                shops = {short((row.get("estabelecimento") or {}).get("nomeEstabelecimento")) for row in rows}
                lines.append(f"- `{term}` {extra or ''}: {total} sales, {len(shops)} shops on page 1")
                return rows

            rows = search("heineken")
            if rows:
                product, shop = rows[0].get("produto") or {}, rows[0].get("estabelecimento") or {}
                lines.append(f"  produto fields: {', '.join(f'{k}={short(v)}' for k, v in product.items())}")
                lines.append(f"  estabelecimento fields: {', '.join(sorted(shop))}")
            # Barcodes: what the sales carry, how many shops share one, and a search by it.
            codes: dict[str, set[str]] = {}
            descriptions: dict[str, set[str]] = {}
            for row in rows:
                product = row.get("produto") or {}
                code = next(
                    (
                        str(product[k])
                        for k in ("gtin", "ean", "codigoBarras", "codBarras", "codigo")
                        if product.get(k)
                    ),
                    None,
                )
                if code:
                    shop = short((row.get("estabelecimento") or {}).get("cnpj"))
                    codes.setdefault(code, set()).add(shop)
                    descriptions.setdefault(code, set()).add(short(product.get("descricao")))
            lines.append(f"  codes on page 1: {len(codes)} distinct")
            for code, shops in sorted(codes.items(), key=lambda pair: -len(pair[1]))[:6]:
                lines.append(
                    f"    {code}: {len(shops)} shops, written as {' | '.join(sorted(descriptions[code])[:4])}"
                )
            for code in [
                c for c, _ in sorted(codes.items(), key=lambda pair: -len(pair[1]))[:2] if c.isdigit()
            ]:
                found = search(code)
                same = sum(
                    1 for row in found if code in {str(v) for v in (row.get("produto") or {}).values()}
                )
                lines.append(f"    searching {code}: {same} of {len(found)} rows carry that code")
            # Bar names.
            for name in NAMES:
                found = search(name)
                own = [
                    row
                    for row in found
                    if all(
                        word in normalize(str((row.get("estabelecimento") or {}).get("nomeEstabelecimento")))
                        for word in name.split()[:2]
                    )
                ]
                products = [r.get("produto") or {} for r in own[:4]]
                samples = "; ".join(
                    f"{short(p.get('descricao'))} R$ {p.get('precoUnitario')}" for p in products
                )
                suffix = f": {samples}" if samples else ""
                lines.append(f"    rows from a shop with that name: {len(own)} of {len(found)}{suffix}")
            # A filter by shop.
            cnpj = next(iter(next(iter(codes.values()), set())), "")
            if cnpj:
                for key in ("cnpj", "estabelecimento", "cnpjEstabelecimento"):
                    found = search("cerveja", **{key: cnpj})
                    mine = sum(short((r.get("estabelecimento") or {}).get("cnpj")) == cnpj for r in found)
                    lines.append(f"    {key}={cnpj}: {mine} of {len(found)} rows from that shop")
        except (BlockedError, DisallowedError, httpx.HTTPError, ValueError) as error:
            lines.append(f"Stopped: {error}"[:200])
    return lines


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else None
    mode = args[0] if args else ""
    lines = venues(args) if mode == "venues" else pdh_search(args) if mode == "pdh-search" else pdh_api(args)
    text = "\n".join(lines)
    print(text)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
