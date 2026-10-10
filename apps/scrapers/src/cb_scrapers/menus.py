"""Menu finder (#305): menu images and PDFs on the websites OpenStreetMap lists for the covered venues.

    uv run cb menus find --out DIR [--limit N]

The venues come from the osm_venues query (inside the bairros, on the same bot-friendly Overpass instances);
only those that tag a website are crawled. Each site gets the polite client's manners (robots.txt and
Crawl-delay, a pause between requests to a host, a stop at the first 403/429), at most two links deep and 20
pages. From the home page the finder follows links that look like a menu (cardápio, menu, bebidas, carta,
preços, happy hour, PDFs) and writes to DIR:
- candidates/: images from those pages big enough to be a menu, and PDF pages as JPEGs;
- candidates.csv: where each one came from;
- platforms.csv: links to online-menu platforms (Cardápio Web, Takeat, Goomer…) for their adapters;
- sites.csv: what happened on each site.
Nothing is sent anywhere. Social networks, national chains and platforms that refuse bots are skipped.
"""

import csv
import io
import logging
import re
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
import pypdfium2 as pdfium
from PIL import Image

from .adapters.osm_venues import OVERPASS, OsmVenues
from .core import BlockedError, DisallowedError, PoliteClient
from .geo import BairroIndex
from .geo.bairros import slugify

log = logging.getLogger(__name__)

MENU_WORDS = re.compile(r"card[aá]pio|menu|bebida|drink|carta|pre[cç]o|happy|chopp|cerveja", re.I)
#: Image and PDF URLs written into scripts and JSON (site builders such as Wix render the page in JavaScript).
EMBEDDED_ASSET = re.compile(r"https?:(?:\\?/){2}[^\s\"'<>()]+?\.(?:jpe?g|png|webp|pdf)\b", re.I)
WEBSITE_TAGS = ("website", "contact:website", "website:menu", "menu:url", "url")
#: Not the venue's own site: social networks, link shorteners, delivery apps, Google.
SKIP_HOSTS = (
    "instagram.com",
    "facebook.com",
    "fb.com",
    "wa.me",
    "whatsapp.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "bit.ly",
    "google.com",
    "goo.gl",
    "negocio.site",
    "tripadvisor.com",
    "tripadvisor.com.br",
)
#: National chains: their menus are national, not the city's bar prices.
CHAINS = (
    "mcdonalds.com.br",
    "bobs.com.br",
    "burgerking.com.br",
    "pizzahut.com.br",
    "dominos.com.br",
    "subway.com",
    "habibs.com.br",
    "giraffas.com.br",
    "spoleto.com.br",
    "kfc.com.br",
    "starbucks.com.br",
    "outback.com.br",
)
#: Online-menu and ordering platforms. The finder only lists their links; adapters read them.
PLATFORMS = {
    "cardapioweb.com": "cardapioweb",
    "takeat.app": "takeat",
    "goomer.app": "goomer",
    "menudino.com": "menudino",
    "anota.ai": "anotaai",
    "dguests.com": "dguests",
    "ifood.com.br": "ifood",
}


@dataclass(frozen=True)
class Limits:
    pages: int = 20
    depth: int = 2
    images: int = 25
    pdfs: int = 3
    pdf_pages: int = 6
    #: The shorter side of an image, in pixels: smaller ones are logos, icons and thumbnails.
    min_side: int = 500
    min_bytes: int = 20_000
    max_bytes: int = 15_000_000


@dataclass
class Site:
    url: str
    venues: list[str]
    amenity: str
    bairro: str


@dataclass
class Candidate:
    file: str
    page_url: str
    asset_url: str
    kind: str
    width: int
    height: int
    bytes: int


@dataclass
class Report:
    site: Site
    status: str = ""
    pages: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    platforms: set[tuple[str, str]] = field(default_factory=set)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _on(host: str, domains: Iterable[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def platform_of(url: str) -> str | None:
    host = _host(url)
    return next((name for domain, name in PLATFORMS.items() if _on(host, [domain])), None)


def normalize_url(value: str) -> str | None:
    """`bardoze.com.br` → `https://bardoze.com.br`; the first of several; None when it isn't a web address."""
    first = re.split(r"[;\s]+", value.strip())[0] if value.strip() else ""
    if not first:
        return None
    url = first if re.match(r"^https?://", first, re.I) else f"https://{first.lstrip('/')}"
    return url if "." in _host(url) else None


def sites_from_overpass(
    elements: Iterable[Mapping[str, Any]], place: BairroIndex | Callable[[float, float], str | None]
) -> list[Site]:
    """Venues that tag their own website, one site per address. `place` names where a venue is: the bairro
    index (only the covered cities' venues, as "city/bairro"), or a function for a city outside it."""
    sites: dict[str, Site] = {}
    for element in elements:
        tags: Mapping[str, str] = element.get("tags", {})
        name = " ".join((tags.get("name") or "").split())
        point = element if element.get("type") == "node" else element.get("center", {})
        url = next((normalize_url(tags[key]) for key in WEBSITE_TAGS if tags.get(key)), None)
        if not name or not url or "lat" not in point or "lon" not in point:
            continue
        host = _host(url)
        if _on(host, SKIP_HOSTS) or _on(host, CHAINS):
            continue
        lat, lon = float(point["lat"]), float(point["lon"])
        if isinstance(place, BairroIndex):
            bairro = place.locate(lat, lon)
            where = f"{bairro.city}/{bairro.slug}" if bairro else None
        else:
            where = place(lat, lon)
        if where is None:
            continue
        key = f"{host}{urlsplit(url).path.rstrip('/')}"
        if key in sites:
            sites[key].venues.append(name)
        else:
            sites[key] = Site(url, [name], tags.get("amenity", ""), where)
    return list(sites.values())


#: Bars, pubs and beer gardens with a website in a city outside the bairro index (the vision spike's test set
#: needs more beer menus than Salvador's sites publish). The area is the city's municipal boundary.
CITY_QUERY = (
    "[out:json][timeout:180];"
    'area["name"="{city}"]["admin_level"="8"]["boundary"="administrative"]->.a;'
    '(nwr["amenity"~"^(bar|pub|biergarten)$"]["name"]["website"](area.a);'
    'nwr["amenity"~"^(bar|pub|biergarten)$"]["name"]["contact:website"](area.a););'
    "out center tags;"
)


def city_sites(client: PoliteClient, city: str) -> list[Site]:
    """The bars, pubs and beer gardens with a website in one city, labelled with the city's slug. A big city's
    area query can take minutes on the public instances, so each one gets more time than osm_venues' bbox."""
    label = slugify(city)
    problems = []
    for url in OVERPASS:
        try:
            response = client.post(url, data={"data": CITY_QUERY.format(city=city)}, timeout=200.0, retries=0)
        except (BlockedError, DisallowedError, httpx.HTTPError) as error:
            problems.append(f"{url}: {type(error).__name__}: {error}")
            continue
        return sites_from_overpass(response.json().get("elements", []), lambda _lat, _lon: label)
    raise RuntimeError(f"every Overpass instance failed for {city}: " + " · ".join(problems))


def largest_in_srcset(srcset: str) -> str | None:
    """`a.jpg 300w, b.jpg 1200w` → `b.jpg` (by width or density; the last one when neither is given)."""
    best: tuple[float, str] | None = None
    for position, part in enumerate(p.strip() for p in srcset.split(",") if p.strip()):
        bits = part.split()
        size = position / 1000
        if len(bits) > 1 and re.fullmatch(r"[\d.]+[wx]", bits[1]):
            size = float(bits[1][:-1])
        if best is None or size >= best[0]:
            best = (size, bits[0])
    return best[1] if best else None


class PageParser(HTMLParser):
    """Links (with their text), images (largest srcset candidate, alt text), frames and the page title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[list[str]] = []
        self.images: list[tuple[str, str]] = []
        self.frames: list[str] = []
        self.title = ""
        self._link: list[str] | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        if tag == "a" and a.get("href"):
            self._link = [a["href"], ""]
            self.links.append(self._link)
        elif tag in ("img", "source"):
            srcset = a.get("srcset") or a.get("data-srcset") or ""
            src = largest_in_srcset(srcset) if srcset else None
            src = src or a.get("data-src") or a.get("data-lazy-src") or a.get("src")
            if src and not src.startswith("data:"):
                self.images.append((src, a.get("alt", "")))
        elif tag in ("iframe", "embed") and a.get("src"):
            self.frames.append(a["src"])
        elif tag == "object" and a.get("data"):
            self.frames.append(a["data"])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._link = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._link is not None:
            self._link[1] += data
        if self._in_title:
            self.title += data


def embedded_assets(html: str) -> list[str]:
    """Asset URLs in scripts and JSON, unescaped: `https:\\/\\/cdn\\/a.jpg` and `\\u002F` become slashes."""
    text = html.replace("\\u002F", "/").replace("\\u002f", "/")
    return [match.replace("\\/", "/") for match in EMBEDDED_ASSET.findall(text)]


def sniff(body: bytes, content_type: str) -> str:
    """What a response holds, by its first bytes rather than its (often wrong) content type."""
    head = body[:2048].lower()
    if body.startswith(b"%PDF-"):
        return "pdf"
    if (
        body.startswith(b"\x89PNG\r\n\x1a\n")
        or body.startswith(b"\xff\xd8\xff")
        or (body[:4] == b"RIFF" and body[8:12] == b"WEBP")
        or body[:6] in (b"GIF87a", b"GIF89a")
    ):
        return "image"
    if "html" in content_type or b"<html" in head or b"<!doctype html" in head:
        return "html"
    return "other"


def _is_pdf(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


class Finder:
    def __init__(self, client: PoliteClient, out: Path, limits: Limits | None = None) -> None:
        self.client = client
        self.out = out
        self.limits = limits or Limits()

    def crawl(self, site: Site) -> Report:
        report = Report(site)
        home = _host(site.url)
        queue: deque[tuple[str, int, bool]] = deque([(site.url, 0, False)])
        seen: set[str] = set()
        assets: dict[str, tuple[str, str]] = {}
        while queue and report.pages < self.limits.pages:
            url, depth, menu_page = queue.popleft()
            url = urldefrag(url).url
            if url in seen:
                continue
            seen.add(url)
            try:
                response = self.client.get(url)
            except DisallowedError:
                if depth == 0:
                    report.status = "robots.txt disallows"
                    return report
                continue
            except BlockedError as error:
                report.status = f"blocked ({error.reason})"
                return report
            except httpx.HTTPError as error:
                if depth == 0:
                    report.status = f"error: {type(error).__name__}"
                    return report
                continue
            report.pages += 1
            here = str(response.url)
            kind = sniff(response.content, response.headers.get("content-type", ""))
            if kind in ("pdf", "image"):
                assets.setdefault(here, (here, kind))
                continue
            if kind != "html":
                continue
            page = PageParser()
            page.feed(response.text)
            menu_here = menu_page or bool(MENU_WORDS.search(here) or MENU_WORDS.search(page.title))
            for href, text in page.links:
                target = urldefrag(urljoin(here, href.strip())).url
                if not target.startswith(("http://", "https://")):
                    continue
                platform = platform_of(target)
                if platform:
                    report.platforms.add((platform, target))
                elif _is_pdf(target) and (menu_here or MENU_WORDS.search(f"{text} {target}")):
                    assets.setdefault(target, (here, "pdf"))
                elif (
                    _host(target) == home
                    and depth < self.limits.depth
                    and MENU_WORDS.search(f"{text} {target}")
                ):
                    queue.append((target, depth + 1, True))
            for src, alt in page.images:
                if menu_here or MENU_WORDS.search(f"{alt} {src}"):
                    assets.setdefault(urljoin(here, src), (here, "image"))
            for frame in page.frames:
                target = urljoin(here, frame)
                if platform_of(target):
                    report.platforms.add((platform_of(target) or "", target))
                elif _is_pdf(target):
                    assets.setdefault(target, (here, "pdf"))
                elif _host(target) == home and depth < self.limits.depth:
                    queue.append((target, depth + 1, menu_here))
            if menu_here:
                for target in embedded_assets(response.text):
                    assets.setdefault(target, (here, "pdf" if _is_pdf(target) else "image"))
        self._download(report, assets)
        report.status = report.status or ("ok" if report.candidates else "no menu found")
        return report

    def _download(self, report: Report, assets: Mapping[str, tuple[str, str]]) -> None:
        folder = self.out / "candidates" / (slugify(report.site.venues[0]) or _host(report.site.url))
        images = pdfs = 0
        for asset_url, (page_url, kind) in assets.items():
            if images >= self.limits.images and pdfs >= self.limits.pdfs:
                break
            if (kind == "image" and images >= self.limits.images) or (
                kind == "pdf" and pdfs >= self.limits.pdfs
            ):
                continue
            try:
                response = self.client.get(asset_url)
            except BlockedError:
                break
            except (DisallowedError, httpx.HTTPError):
                continue
            body = response.content
            if len(body) > self.limits.max_bytes:
                continue
            found = sniff(body, response.headers.get("content-type", ""))
            folder.mkdir(parents=True, exist_ok=True)
            if found == "image":
                saved = self._save_image(folder, len(report.candidates) + 1, body, page_url, asset_url)
                if saved:
                    report.candidates.append(saved)
                    images += 1
            elif found == "pdf":
                pages = self._save_pdf(folder, len(report.candidates) + 1, body, page_url, asset_url)
                report.candidates.extend(pages)
                pdfs += 1 if pages else 0

    def _save_image(
        self, folder: Path, number: int, body: bytes, page_url: str, asset_url: str
    ) -> Candidate | None:
        try:
            with Image.open(io.BytesIO(body)) as image:
                width, height, kind = image.width, image.height, image.format
        except (OSError, ValueError):
            return None
        extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(kind or "")
        if not extension or min(width, height) < self.limits.min_side or len(body) < self.limits.min_bytes:
            return None
        path = folder / f"{number:02d}.{extension}"
        path.write_bytes(body)
        return Candidate(
            str(path.relative_to(self.out)), page_url, asset_url, "image", width, height, len(body)
        )

    def _save_pdf(
        self, folder: Path, number: int, body: bytes, page_url: str, asset_url: str
    ) -> list[Candidate]:
        saved = []
        for index, image in enumerate(pdf_pages(body, self.limits.pdf_pages)):
            jpeg = jpeg_bytes(image)
            path = folder / f"{number:02d}-p{index + 1}.jpg"
            path.write_bytes(jpeg)
            saved.append(
                Candidate(
                    str(path.relative_to(self.out)),
                    page_url,
                    f"{asset_url}#page={index + 1}",
                    "pdf-page",
                    image.width,
                    image.height,
                    len(jpeg),
                )
            )
        return saved


def pdf_pages(body: bytes, count: int, scale: float = 2) -> list[Image.Image]:
    """The first `count` pages as images (scale 2 is about 144 dpi); none when it isn't a readable PDF."""
    try:
        document = pdfium.PdfDocument(body)
    except pdfium.PdfiumError:
        return []
    try:
        return [
            document[index].render(scale=scale).to_pil().convert("RGB")
            for index in range(min(len(document), count))
        ]
    finally:
        document.close()


def jpeg_bytes(image: Image.Image, max_side: int | None = None) -> bytes:
    """A JPEG (quality 85), shrunk to `max_side` pixels on its longer side when given."""
    if max_side and max(image.size) > max_side:
        image = image.copy()
        image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


def write_reports(out: Path, reports: list[Report]) -> None:
    with (out / "candidates.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "file",
                "venues",
                "amenity",
                "bairro",
                "website",
                "page_url",
                "asset_url",
                "kind",
                "width",
                "height",
                "bytes",
            ]
        )
        for report in reports:
            for c in report.candidates:
                writer.writerow(
                    [
                        c.file,
                        " | ".join(report.site.venues),
                        report.site.amenity,
                        report.site.bairro,
                        report.site.url,
                        c.page_url,
                        c.asset_url,
                        c.kind,
                        c.width,
                        c.height,
                        c.bytes,
                    ]
                )
    with (out / "platforms.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["venues", "website", "platform", "url"])
        for report in reports:
            for platform, url in sorted(report.platforms):
                writer.writerow([" | ".join(report.site.venues), report.site.url, platform, url])
    with (out / "sites.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["venues", "amenity", "bairro", "website", "status", "pages", "candidates"])
        for report in reports:
            writer.writerow(
                [
                    " | ".join(report.site.venues),
                    report.site.amenity,
                    report.site.bairro,
                    report.site.url,
                    report.status,
                    report.pages,
                    len(report.candidates),
                ]
            )


def find_menus(
    client: PoliteClient,
    out: Path,
    limit: int | None = None,
    limits: Limits | None = None,
    cities: Iterable[str] = (),
) -> str:
    """Run the whole pass and return a short summary (the CSVs hold the details). Without `cities`, the
    covered cities' venues; with them, those cities' bars instead (`limit` then applies to each city)."""
    bairros = BairroIndex.from_files()
    venues = OsmVenues(client, bairros)
    failed: list[str] = []
    if cities:
        sites, crawlable = [], []
        for city in cities:
            try:
                found = city_sites(client, city)
            except RuntimeError as error:
                # A city the public instances can't answer for in time shouldn't cost the others their crawl.
                log.warning("%s", error)
                failed.append(city)
                continue
            sites += found
            crawlable += [site for site in found if not platform_of(site.url)][:limit]
    else:
        fetched = venues.fetch(next(iter(venues.tasks())))
        sites = sites_from_overpass(fetched.json().get("elements", []), bairros)
        crawlable = [site for site in sites if not platform_of(site.url)][:limit]
    platform_sites = [site for site in sites if platform_of(site.url)]
    out.mkdir(parents=True, exist_ok=True)
    finder = Finder(client, out, limits)
    reports = []
    for number, site in enumerate(crawlable, 1):
        log.info("%d/%d %s", number, len(crawlable), site.url)
        reports.append(finder.crawl(site))
    for site in platform_sites:
        report = Report(site, status="online-menu platform (listed, not crawled)")
        report.platforms.add((platform_of(site.url) or "", site.url))
        reports.append(report)
    write_reports(out, reports)
    statuses: dict[str, int] = {}
    for report in reports:
        label = report.status.split(" (")[0] if report.status.startswith("blocked") else report.status
        statuses[label] = statuses.get(label, 0) + 1
    total = sum(len(report.candidates) for report in reports)
    with_menus = sum(1 for report in reports if report.candidates)
    platforms = sum(len(report.platforms) for report in reports)
    lines = [
        f"{len(sites)} venue websites: {len(crawlable)} crawled, {len(platform_sites)} on platforms",
        f"{total} candidates from {with_menus} sites · {platforms} online-menu links",
        "sites: " + ", ".join(f"{label} {count}" for label, count in sorted(statuses.items())),
    ]
    if failed:
        lines.append("Overpass didn't answer in time for: " + ", ".join(failed))
    return "\n".join(lines)
