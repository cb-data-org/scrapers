"""Online-menu platforms of Pituba's bars: how many beer prices each would give us. A one-off proof of concept
(owner, 11 Oct 2026), run by hand from the public mirror's GitHub Actions, never from anyone's own connection.
Nothing is sent anywhere.

    uv run python -m cb_scrapers.menu_platforms --overture places.geojsonseq --out DIR

1. Every link Overture Maps lists for the bars and restaurants in and around Pituba (websites and socials).
   Short links and link pages (bit.ly, Linktree, taplink, beacons…) are followed one step; each bar's own site
   is read once (its home page) for links to a platform.
2. Each platform page found (Brendi, Goomer, Cardápio Web, Anota AI, Takeat, Menudino, bigdim…) is read once,
   politely (robots.txt, a pause between requests to a host, a stop at 403/429), and its menu taken from what
   the page carries: JSON state embedded in the page (Next.js, Nuxt, window.__STATE__…), schema.org Menu
   markup, or its visible text.
3. Each item read with the receipts' serving parser (a bar's "Heineken 600ml 18,90"); beer prices counted per
   platform and per bar.

iFood is counted, not read: its terms forbid it. Instagram and WhatsApp are counted, not read.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .core import BlockedError, DisallowedError, PoliteClient
from .core.catalog import committed_catalog
from .core.products import NotAServing, ServingReader
from .menu_poc import bars, page_text, prices

log = logging.getLogger(__name__)

#: Online-menu and ordering platforms by host (the finder's, and those seen around Pituba).
PLATFORMS = {
    "cardapioweb.com": "cardapioweb",
    "takeat.app": "takeat",
    "goomer.app": "goomer",
    "goomer.com.br": "goomer",
    "menudino.com": "menudino",
    "anota.ai": "anotaai",
    "dguests.com": "dguests",
    "brendi.com.br": "brendi",
    "bigdim.com.br": "bigdim",
    "lojazap.com": "lojazap",
    "olaclick.com": "olaclick",
    "ola.click": "olaclick",
    "saipos.com": "saipos",
    "cardapio.ai": "cardapioai",
    "deliverydireto.com.br": "deliverydireto",
    "instadelivery.com.br": "instadelivery",
    "menu.app": "menuapp",
    "neemo.com.br": "neemo",
    "placeweb.site": "placeweb",
    "balcao.online": "balcao",
    "pedidos.app": "pedidosapp",
    "consumer.com.br": "consumer",
    "menuvem.com.br": "menuvem",
    "comandaon.com.br": "comandaon",
    "ifood.com.br": "ifood",
}
#: Link pages and short links: followed one step to what they point at.
HUBS = ("linktr.ee", "taplink.cc", "beacons.ai", "bio.site", "linkr.bio", "bit.ly", "lnk.bio", "linkin.bio")
NOT_READ = {"ifood"}
SOCIAL = ("instagram.com", "facebook.com", "wa.me", "whatsapp.com", "api.whatsapp.com", "tiktok.com")
PRICE_KEYS = re.compile(
    r"^(price|preco|preço|valor|value|amount|unit_?price|sale_?price|price_?value)$", re.I
)
NAME_KEYS = re.compile(r"^(name|nome|title|titulo|título|description|descricao|descrição|label)$", re.I)


def host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def platform_of(url: str) -> str | None:
    found = host(url)
    return next(
        (name for domain, name in PLATFORMS.items() if found == domain or found.endswith("." + domain)), None
    )


def is_hub(url: str) -> bool:
    found = host(url)
    return any(found == hub or found.endswith("." + hub) for hub in HUBS)


def links_in(html: str, base: str) -> list[str]:
    return [urljoin(base, href) for href in re.findall(r"""href=["']([^"'#]+)["']""", html, re.I)]


def embedded_json(html: str) -> list[Any]:
    """JSON the page carries for its own script: Next.js/Nuxt state, JSON-LD, window.__X__ = {...}."""
    found: list[Any] = []
    for body in re.findall(
        r"(?is)<script[^>]*type=[\"'](?:application/(?:ld\+)?json)[\"'][^>]*>(.*?)</script>", html
    ):
        try:
            found.append(json.loads(body))
        except ValueError:
            continue
    for body in re.findall(r"(?s)window\.__[A-Z_]+__\s*=\s*(\{.*?\});?\s*</script>", html):
        try:
            found.append(json.loads(body))
        except ValueError:
            continue
    return found


def items_in(data: Any) -> list[tuple[str, float]]:
    """(name, price) pairs from any JSON: objects with a name-like and a price-like field."""
    out: list[tuple[str, float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name = next(
                (str(v) for k, v in node.items() if NAME_KEYS.match(str(k)) and isinstance(v, str)), None
            )
            price = None
            for key, value in node.items():
                if PRICE_KEYS.match(str(key)):
                    if isinstance(value, int | float):
                        price = float(value)
                    elif isinstance(value, str) and re.fullmatch(r"\d{1,4}(?:[.,]\d{1,2})?", value.strip()):
                        price = float(value.strip().replace(",", "."))
                    elif isinstance(value, dict):
                        inner = value.get("price") or value.get("value")
                        price = float(inner) if isinstance(inner, int | float) else price
            if name and price:
                # Prices in cents: 1890 for R$ 18,90.
                out.append((name, price / 100 if price >= 500 and float(price).is_integer() else price))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return out


def beers(pairs: list[tuple[str, float]], reader: ServingReader) -> list[dict[str, Any]]:
    found = []
    for name, price in pairs:
        try:
            serving = reader.read(name, assume_bottle=True)
        except NotAServing:
            continue
        if 2 <= price <= 300:
            found.append(
                {"line": " ".join(name.split())[:70], "format": serving.format_code, "price": round(price, 2)}
            )
    return found


def fetch(client: PoliteClient, url: str, stats: Counter[str], name: str) -> httpx.Response | None:
    try:
        return client.get(url)
    except DisallowedError:
        stats[f"{name}: robots.txt disallows"] += 1
    except BlockedError as error:
        stats[f"{name}: blocked ({error.reason})"] += 1
    except httpx.HTTPError as error:
        stats[f"{name}: {type(error).__name__}"] += 1
    return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1])
    overture = Path(args[args.index("--overture") + 1])
    out.mkdir(parents=True, exist_ok=True)
    reader = ServingReader(committed_catalog())
    places = bars(overture)
    stats: Counter[str] = Counter()
    menus: dict[str, set[str]] = defaultdict(set)  # platform URL → bars
    social: Counter[str] = Counter()
    with PoliteClient(delay=4.0, retries=1, timeout=30) as client:
        # 1. Platform links, from Overture's links, link pages and each bar's home page.
        for bar in places:
            links = [link for link in (bar.get("links") or []) if link]
            for link in links:
                url = link if link.startswith("http") else f"https://{link}"
                if any(s in host(url) for s in SOCIAL):
                    social[next(s for s in SOCIAL if s in host(url))] += 1
                    continue
                if platform_of(url):
                    menus[url].add(bar["name"])
                    continue
                response = fetch(client, url, stats, "site or hub")
                if response is None:
                    continue
                final = str(response.url)
                if platform_of(final):
                    menus[final].add(bar["name"])
                    continue
                for target in links_in(response.text, final):
                    if platform_of(target):
                        menus[target].add(bar["name"])
                    elif is_hub(url) and is_hub(target):
                        continue
        # 2. Each platform page, read once.
        per_platform: dict[str, Counter[str]] = defaultdict(Counter)
        lines = ["# Online-menu platforms of Pituba's bars", ""]
        details: list[str] = []
        for url, names in sorted(menus.items(), key=lambda pair: platform_of(pair[0]) or ""):
            name = platform_of(url) or "?"
            counts = per_platform[name]
            counts["bars"] += len(names)
            if name in NOT_READ:
                counts["not read (terms)"] += 1
                continue
            response = fetch(client, url, stats, name)
            if response is None:
                counts["unreadable"] += 1
                continue
            counts["pages"] += 1
            pairs = [pair for data in embedded_json(response.text) for pair in items_in(data)]
            found = beers(pairs, reader)
            source = "page JSON"
            if not found:
                found = prices(page_text(client, str(response.url)), reader)
                source = "page text"
            counts["menu items"] += len(pairs)
            counts["beer prices"] += len(found)
            counts["with beer prices"] += bool(found)
            (out / f"{name}-{len(details):03d}.html").write_text(response.text[:400_000], "utf-8")
            details.append(
                f"- {name} {url} ({', '.join(sorted(names))}): {len(pairs)} items in page JSON, "
                f"{len(found)} beer prices ({source})"
            )
            for beer in found[:5]:
                details.append(f"    - `{beer['line']}` → {beer['format']} R$ {beer['price']:.2f}")
        lines.append(
            f"{len(places)} bars and restaurants around Pituba on Overture; {len(menus)} platform pages "
            f"linked from them; social links: {', '.join(f'{k} {v}' for k, v in social.most_common())}."
        )
        lines += [
            "",
            "| Platform | Bars | Pages read | Unreadable | Menu items | Beer prices | Pages with beer |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, counts in sorted(per_platform.items(), key=lambda pair: -pair[1]["beer prices"]):
            unreadable = counts["unreadable"] + counts["not read (terms)"]
            lines.append(
                f"| {name} | {counts['bars']} | {counts['pages']} | {unreadable} "
                f"| {counts['menu items']} | {counts['beer prices']} | {counts['with beer prices']} |"
            )
        lines += [
            "",
            "Fetch problems: " + (", ".join(f"{k} {v}" for k, v in stats.most_common()) or "none"),
            "",
        ]
        lines += details
    text = "\n".join(lines)
    print(text)
    (out / "report.md").write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
