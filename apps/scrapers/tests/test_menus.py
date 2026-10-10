import csv
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pypdfium2 as pdfium
import pytest
from PIL import Image

from cb_scrapers.adapters.osm_venues import OVERPASS
from cb_scrapers.core import PoliteClient
from cb_scrapers.geo import BairroIndex
from cb_scrapers.menus import (
    Finder,
    Limits,
    PageParser,
    Site,
    embedded_assets,
    find_menus,
    largest_in_srcset,
    normalize_url,
    sites_from_overpass,
    sniff,
)
from tests.fakes import FakeSite

RIO_VERMELHO = (-13.0115, -38.4896)
SAO_PAULO = (-23.55, -46.63)
#: Thresholds that let tiny generated files through; the size rules have their own test.
LOOSE = Limits(min_bytes=1)


def image_bytes(width: int, height: int, kind: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, kind)
    return buffer.getvalue()


def pdf_bytes(pages: int) -> bytes:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(595, 842)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


def node(id: int, point: tuple[float, float], **tags: str) -> dict[str, Any]:
    return {"type": "node", "id": id, "lat": point[0], "lon": point[1], "tags": tags}


@pytest.fixture(scope="module")
def bairros() -> BairroIndex:
    return BairroIndex.from_files()


def test_normalize_url() -> None:
    assert normalize_url("bardoze.com.br") == "https://bardoze.com.br"
    assert normalize_url(" http://a.test/menu ; https://b.test ") == "http://a.test/menu"
    assert normalize_url("not a site") is None


def test_sites_from_overpass_keeps_own_websites_inside_the_cities(bairros: BairroIndex) -> None:
    elements = [
        node(1, RIO_VERMELHO, name="Bar do Zé", amenity="bar", website="bardoze.com.br"),
        node(2, RIO_VERMELHO, name="Bar do Zé 2", amenity="bar", website="https://bardoze.com.br/"),
        node(3, RIO_VERMELHO, name="Insta Bar", amenity="bar", website="https://instagram.com/instabar"),
        node(
            4, RIO_VERMELHO, name="McDonald's", amenity="restaurant", website="https://www.mcdonalds.com.br"
        ),
        node(5, SAO_PAULO, name="Longe", amenity="bar", website="longe.com.br"),
        {
            "type": "way",
            "id": 6,
            "center": {"lat": RIO_VERMELHO[0], "lon": RIO_VERMELHO[1]},
            "tags": {"name": "Boteco", "amenity": "pub", "contact:website": "https://boteco.test/menu"},
        },
        node(7, RIO_VERMELHO, name="Sem Site", amenity="bar"),
    ]
    sites = sites_from_overpass(elements, bairros)
    assert [(site.url, site.venues, site.amenity) for site in sites] == [
        ("https://bardoze.com.br", ["Bar do Zé", "Bar do Zé 2"], "bar"),
        ("https://boteco.test/menu", ["Boteco"], "pub"),
    ]
    assert sites[0].bairro.startswith("salvador/")


def test_page_parser_and_embedded_assets() -> None:
    page = PageParser()
    page.feed(
        "<html><title>Cardápio</title>"
        '<a href="/bebidas">Nossas <b>bebidas</b></a>'
        '<img srcset="s.jpg 300w, l.jpg 1200w" src="m.jpg" alt="menu">'
        '<img data-src="lazy.png"><img src="data:image/png;base64,AAAA">'
        '<iframe src="/files/carta.pdf"></iframe></html>'
    )
    assert page.title == "Cardápio"
    assert page.links == [["/bebidas", "Nossas bebidas"]]
    assert page.images == [("l.jpg", "menu"), ("lazy.png", "")]
    assert page.frames == ["/files/carta.pdf"]
    assert largest_in_srcset("a.jpg 2x, b.jpg 1x") == "a.jpg"
    assert embedded_assets('{"src":"https:\\/\\/cdn.test\\/m\\/promo.webp","x":1}') == [
        "https://cdn.test/m/promo.webp"
    ]
    assert sniff(pdf_bytes(1), "text/html") == "pdf"
    assert sniff(image_bytes(10, 10, "PNG"), "") == "image"
    assert sniff(b"<!DOCTYPE html><html>", "") == "html"


def bar_site() -> FakeSite:
    return FakeSite(
        {
            "https://bar.test/": (
                "<html><title>Bar Teste</title>"
                '<a href="/cardapio">Cardápio</a><a href="/sobre">Sobre nós</a>'
                '<a href="https://app.cardapioweb.com/barteste">Peça online</a></html>'
            ),
            "https://bar.test/cardapio": (
                '<html><img src="/menu.jpg" alt="Cardápio"><img src="/logo.png">'
                '<a href="/bebidas.pdf">Bebidas</a>'
                '<script>var promo = "https:\\/\\/cdn.test\\/media\\/promo.webp";</script></html>'
            ),
            "https://bar.test/menu.jpg": image_bytes(800, 1100),
            "https://bar.test/logo.png": image_bytes(64, 64, "PNG"),
            "https://bar.test/bebidas.pdf": pdf_bytes(2),
            "https://cdn.test/media/promo.webp": image_bytes(600, 900, "WEBP"),
        }
    )


def test_crawl_follows_menu_links_and_keeps_menu_sized_images_and_pdf_pages(
    polite: Callable[..., PoliteClient], tmp_path: Path
) -> None:
    site = bar_site()
    with polite(site) as client:
        report = Finder(client, tmp_path, LOOSE).crawl(
            Site("https://bar.test/", ["Bar Teste"], "bar", "s/rv")
        )
    assert report.status == "ok"
    assert [(c.file, c.kind, c.width) for c in report.candidates] == [
        ("candidates/bar-teste/01-p1.jpg", "pdf-page", 1190),
        ("candidates/bar-teste/01-p2.jpg", "pdf-page", 1190),
        ("candidates/bar-teste/03.jpg", "image", 800),
        ("candidates/bar-teste/04.webp", "image", 600),
    ]
    assert (tmp_path / "candidates/bar-teste/01-p1.jpg").read_bytes().startswith(b"\xff\xd8")
    assert report.platforms == {("cardapioweb", "https://app.cardapioweb.com/barteste")}
    # "Sobre nós" isn't a menu link, and the logo is too small to keep.
    assert "/sobre" not in site.paths()
    assert report.pages == 2


def test_size_rules_drop_small_files(polite: Callable[..., PoliteClient], tmp_path: Path) -> None:
    with polite(bar_site()) as client:
        report = Finder(client, tmp_path, Limits()).crawl(Site("https://bar.test/", ["Bar"], "bar", "s/rv"))
    # Plain white test images compress far below 20 kB; the rendered PDF pages are kept.
    assert {c.kind for c in report.candidates} == {"pdf-page"}


def test_a_site_that_refuses_bots_is_left_alone(polite: Callable[..., PoliteClient], tmp_path: Path) -> None:
    refusing = FakeSite(
        {"https://blocked.test/": 403, "https://private.test/robots.txt": "User-agent: *\nDisallow: /"}
    )
    with polite(refusing) as client:
        finder = Finder(client, tmp_path, LOOSE)
        blocked = finder.crawl(Site("https://blocked.test/", ["A"], "bar", "s/rv"))
        private = finder.crawl(Site("https://private.test/", ["B"], "bar", "s/rv"))
    assert (blocked.status, private.status) == ("blocked (HTTP 403)", "robots.txt disallows")
    assert refusing.paths() == ["/robots.txt", "/", "/robots.txt"]


def test_find_menus_writes_the_reports(polite: Callable[..., PoliteClient], tmp_path: Path) -> None:
    overpass = {
        "elements": [
            node(1, RIO_VERMELHO, name="Bar Teste", amenity="bar", website="https://bar.test/"),
            node(
                2,
                RIO_VERMELHO,
                name="Na Plataforma",
                amenity="bar",
                website="https://takeat.app/naplataforma",
            ),
        ]
    }
    site = bar_site()
    site.routes[OVERPASS[0]] = json.dumps(overpass)
    with polite(site) as client:
        summary = find_menus(client, tmp_path, limits=LOOSE)
    assert summary.splitlines()[:2] == [
        "2 venue websites: 1 crawled, 1 on platforms",
        "4 candidates from 1 sites · 2 online-menu links",
    ]
    rows = list(csv.DictReader((tmp_path / "candidates.csv").open(encoding="utf-8")))
    assert [row["kind"] for row in rows] == ["pdf-page", "pdf-page", "image", "image"]
    assert rows[0]["asset_url"] == "https://bar.test/bebidas.pdf#page=1"
    platforms = list(csv.DictReader((tmp_path / "platforms.csv").open(encoding="utf-8")))
    assert {row["platform"] for row in platforms} == {"cardapioweb", "takeat"}
    sites = list(csv.DictReader((tmp_path / "sites.csv").open(encoding="utf-8")))
    assert [row["status"] for row in sites] == ["ok", "online-menu platform (listed, not crawled)"]


def test_find_menus_in_other_cities_takes_their_bars(
    polite: Callable[..., PoliteClient], tmp_path: Path
) -> None:
    overpass = {
        "elements": [node(1, SAO_PAULO, name="Bar Teste", amenity="bar", website="https://bar.test/")]
    }
    site = bar_site()
    site.routes[OVERPASS[0]] = json.dumps(overpass)
    with polite(site) as client:
        summary = find_menus(client, tmp_path, limit=5, limits=LOOSE, cities=["São Paulo"])
    assert summary.splitlines()[0] == "1 venue websites: 1 crawled, 0 on platforms"
    query = next(r for r in site.requests if r.method == "POST").content.decode()
    assert "S%C3%A3o+Paulo" in query and "biergarten" in query
    sites = list(csv.DictReader((tmp_path / "sites.csv").open(encoding="utf-8")))
    assert [(row["bairro"], row["status"]) for row in sites] == [("sao-paulo", "ok")]


def test_a_city_overpass_cannot_answer_is_skipped(
    polite: Callable[..., PoliteClient], tmp_path: Path
) -> None:
    overpass = {
        "elements": [node(1, SAO_PAULO, name="Bar Teste", amenity="bar", website="https://bar.test/")]
    }
    site = bar_site()

    def answer(request: httpx.Request) -> httpx.Response:
        if "Rio" in request.content.decode().replace("+", " "):
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=overpass)

    for url in OVERPASS:
        site.routes[url] = answer
    with polite(site) as client:
        summary = find_menus(client, tmp_path, limits=LOOSE, cities=["Rio de Janeiro", "São Paulo"])
    lines = summary.splitlines()
    assert lines[0] == "1 venue websites: 1 crawled, 0 on platforms"
    assert lines[-1] == "Overpass didn't answer in time for: Rio de Janeiro"
