"""Menus of Pituba's bars from their own websites: a one-off proof of concept (owner, 11 Oct 2026), run by
hand from the public mirror's GitHub Actions, never from anyone's own connection. Nothing is sent anywhere.

    uv run python -m cb_scrapers.menu_poc --overture places.geojsonseq --out DIR

1. The bars, pubs and restaurants Overture Maps places in and around Pituba, with the website and Instagram
   handle each lists.
2. Each website (social networks and link pages skipped) crawled by the menu finder (menus.py: robots.txt, a
   pause between requests, at most two links deep): menu images and PDF pages.
3. Each image read with Tesseract OCR (Portuguese), each line with a price read by the receipts' serving
   parser (a bar's "HEINEKEN 18,00" is its 600 ml bottle, assumed), and dated by the file's own date (the
   server's Last-Modified): older than MAX_AGE_DAYS doesn't count.
4. Each price compared with what the site already lists for that bar (Preço da Hora).

Instagram is counted, not read: its official API needs the brand's Meta app (owner task #102), and reading
profiles any other way breaks its terms.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .adapters.overture_places import category, point
from .core import PoliteClient
from .core.catalog import committed_catalog
from .core.products import NotAServing, ServingReader
from .matching import Candidate, best_match
from .menus import Finder, Limits, Site, normalize_url

log = logging.getLogger(__name__)

#: Pituba and its edges (Itaigara, Caminho das Árvores, Jardim Armação's end): south, west, north, east.
BOX = (-13.016, -38.478, -12.982, -38.440)
#: Places that serve beer at a table: bars first; no shops, fast food or cafés.
KINDS = re.compile(r"bar|pub|brew|beer|gastro|lounge|bistro|pizz|grill|steak|seafood|barbecue|restaurant")
NOT_KINDS = re.compile(r"store|fast_food|cafe|bakery|juice|ice_cream|dessert|vegan|health|coffee")
SOCIAL = re.compile(r"instagram|facebook|linktr\.ee|linkr\.bio|wa\.me|whatsapp|tiktok|ifood|goomer", re.I)
MAX_AGE_DAYS = 180
PRICE = re.compile(r"(?:r\$\s*)?(\d{1,3}(?:[.,]\d{2}))\b", re.I)
SITE = "https://cervejabarata.com.br"


def bars(path: Path) -> list[dict[str, Any]]:
    """Overture's places to eat or drink in the box, with their websites and Instagram handles."""
    found = []
    south, west, north, east = BOX
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            feature = json.loads(line)
            props = feature.get("properties", feature)
            located = point(feature.get("geometry") or props.get("geometry"))
            if located is None or not (south <= located[0] <= north and west <= located[1] <= east):
                continue
            kind = str(category(props) or "")
            if not KINDS.search(kind) or NOT_KINDS.search(kind) or float(props.get("confidence") or 0) < 0.5:
                continue
            names = props.get("names") or {}
            links = [str(url) for url in (props.get("websites") or []) + (props.get("socials") or [])]
            found.append(
                {
                    "name": names.get("primary") if isinstance(names, dict) else None,
                    "category": kind,
                    "lat": located[0],
                    "lng": located[1],
                    "website": next((u for u in links if not SOCIAL.search(u)), None),
                    "instagram": next((u for u in links if "instagram" in u.lower()), None),
                    "links": links,
                }
            )
    # Bars before restaurants: they are the ones the site is about.
    named = [bar for bar in found if bar["name"]]
    return sorted(named, key=lambda bar: (not re.search(r"bar|pub|brew|beer", bar["category"]), bar["name"]))


def ocr(image: Path) -> str:
    try:
        result = subprocess.run(
            ["tesseract", str(image), "stdout", "-l", "por", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def prices(text: str, reader: ServingReader) -> list[dict[str, Any]]:
    """Beer lines with a price: "HEINEKEN 600ML ...... 18,00" → Heineken garrafa 600, R$ 18,00."""
    found = []
    for line in text.splitlines():
        match = list(PRICE.finditer(line))
        if not match:
            continue
        price = float(match[-1].group(1).replace(".", ",").replace(",", "."))
        description = PRICE.sub(" ", line)
        try:
            serving = reader.read(description, assume_bottle=True)
        except NotAServing:
            continue
        if 2 <= price <= 300:
            found.append(
                {
                    "line": " ".join(line.split())[:80],
                    "format": serving.format_code,
                    "brand": serving.brand.slug if serving.brand else None,
                    "assumed": serving.volume_ml is None,
                    "price": price,
                }
            )
    return found


def page_text(client: PoliteClient, url: str) -> str:
    """A page's visible text, a line per block: menus written into the page itself."""
    try:
        html = client.get(url).text
    except Exception:
        return ""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def dated(client: PoliteClient, url: str) -> datetime | None:
    """The file's own date: the server's Last-Modified (PDF pages share their PDF's)."""
    try:
        response = client.request("HEAD", url.split("#")[0])
        value = response.headers.get("last-modified")
        return parsedate_to_datetime(value) if value else None
    except Exception:
        return None


def listed(client: PoliteClient) -> tuple[list[Candidate], dict[int, list[dict[str, Any]]]]:
    """Our Salvador venues and their published prices (the site's own datasets)."""
    venues = client.get(f"{SITE}/api/v1/salvador/venues-all.json").json()["items"]
    deals = client.get(f"{SITE}/api/v1/salvador/deals.json").json()["items"]
    by_venue: dict[int, list[dict[str, Any]]] = {}
    for deal in deals:
        by_venue.setdefault(int(deal["venue_id"]), []).append(deal)
    return [Candidate(v["name"], v["lat"], v["lng"], v) for v in venues], by_venue


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1])
    overture = Path(args[args.index("--overture") + 1])
    out.mkdir(parents=True, exist_ok=True)
    places = bars(overture)
    reader = ServingReader(committed_catalog())
    now = datetime.now(UTC)
    lines = ["# Menus of Pituba's bars (proof of concept)", ""]
    with PoliteClient(delay=4.0, retries=1, timeout=30) as client:
        ours, our_deals = listed(client)
        finder = Finder(client, out, Limits(pages=12, images=12, pdfs=3, pdf_pages=4))
        with_site = [bar for bar in places if bar["website"] and normalize_url(bar["website"])]
        lines.append(
            f"{len(places)} places in and around Pituba on Overture; {len(with_site)} with a website, "
            f"{sum(1 for bar in places if bar['instagram'])} with an Instagram."
        )
        lines.append("")
        totals = {"crawled": 0, "menus": 0, "beers": 0, "fresh": 0, "match": 0, "differ": 0}
        platforms: dict[str, int] = {}
        statuses: dict[str, int] = {}
        for bar in with_site[:45]:
            url = normalize_url(bar["website"]) or ""
            report = finder.crawl(Site(url, [bar["name"]], "bar", "pituba"))
            totals["crawled"] += 1
            beers: list[dict[str, Any]] = []
            if report.status != "robots.txt disallows" and not report.status.startswith("blocked"):
                for item in prices(page_text(client, url), reader):
                    item["age_days"] = None
                    item["source"] = url
                    beers.append(item)
            for candidate in report.candidates:
                text = ocr(out / candidate.file)
                found = prices(text, reader)
                if found:
                    when = dated(client, candidate.asset_url)
                    age = (now - when).days if when else None
                    for item in found:
                        item["age_days"] = age
                        item["source"] = candidate.asset_url
                    beers.extend(found)
            totals["menus"] += bool(report.candidates)
            key = report.status.split(" (")[0].split(":")[0]
            statuses[key] = statuses.get(key, 0) + 1
            totals["beers"] += len(beers)
            fresh = [b for b in beers if b["age_days"] is not None and b["age_days"] <= MAX_AGE_DAYS]
            totals["fresh"] += len(fresh)
            ours_match = best_match([bar["name"]], bar["lat"], bar["lng"], ours, max_m=150)
            listed_prices = our_deals.get(int(ours_match.data["id"]), []) if ours_match else []
            compared = []
            for beer in beers:
                same = [d for d in listed_prices if d["format_code"] == beer["format"]]
                if same:
                    match = any(abs(d["price_minor"] / 100 - beer["price"]) < 0.01 for d in same)
                    totals["match" if match else "differ"] += 1
                    compared.append(
                        f"{beer['format']} menu R$ {beer['price']:.2f} vs listed "
                        f"{', '.join(f'R$ {d["price_minor"] / 100:.2f}' for d in same)}"
                    )
            lines.append(
                f"- **{bar['name']}** ({bar['category']}) {url}: {report.status}, {len(report.candidates)} "
                f"menu files, {len(beers)} beer prices"
                + (f" ({len(fresh)} dated ≤ {MAX_AGE_DAYS} d)" if beers else "")
                + (
                    f"; on our map as {ours_match.name}, {len(listed_prices)} prices listed"
                    if ours_match
                    else ""
                )
                + (
                    f"; platforms: {', '.join(sorted({n for n, _ in report.platforms}))}"
                    if report.platforms
                    else ""
                )
            )
            for name, _link in report.platforms:
                platforms[name] = platforms.get(name, 0) + 1
            for beer in beers[:6]:
                age = f"{beer['age_days']} d old" if beer["age_days"] is not None else "undated"
                lines.append(f"    - `{beer['line']}` → {beer['format']} R$ {beer['price']:.2f} ({age})")
            for line in compared[:4]:
                lines.append(f"    - compared: {line}")
        lines += [
            "",
            f"Totals: {totals['crawled']} sites crawled, {totals['menus']} with menu files, "
            f"{totals['beers']} beer prices read ({totals['fresh']} dated within {MAX_AGE_DAYS} days); "
            "against our listed prices: "
            f"{totals['match']} equal, {totals['differ']} different.",
            "Sites: "
            + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items(), key=lambda kv: -kv[1]))
            + ".",
            "Online-menu platforms linked: "
            + (", ".join(f"{k} {v}" for k, v in sorted(platforms.items(), key=lambda kv: -kv[1])) or "none")
            + ".",
            "",
            "Instagram: " + ", ".join(sorted({b["name"] for b in places if b["instagram"]})[:60]),
        ]
    text = "\n".join(lines)
    print(text)
    (out / "report.md").write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
