"""The site the monitor looks at: its key pages and its sitemap."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .http import Api, ApiError, check, describe

SITE = "https://cervejabarata.com.br"
HOST = "cervejabarata.com.br"
SITEMAP_URL = f"{SITE}/sitemap.xml"

#: The pages Search Console (URL Inspection) and Bing (URL info) are asked about every month: the 15 pages
#: the owner asked Google to index first (docs/RUNBOOK.md §15b), then the press, data and English hubs. A page
#: that isn't in the sitemap yet (the price index before its first month) is skipped until it is.
KEY_PATHS: tuple[str, ...] = (
    "/",
    "/salvador",
    "/salvador/mercados",
    "/salvador/depositos",
    "/lauro-de-freitas",
    "/salvador/pituba",
    "/salvador/rio-vermelho",
    "/salvador/cerveja/heineken",
    "/salvador/formato/chopp",
    "/salvador/carnaval",
    "/salvador/preco-da-cerveja",
    "/en/salvador/beer-prices",
    "/en/salvador",
    "/salvador/reveillon",
    "/dados",
    "/en",
    "/imprensa",
    "/metodologia",
    "/desenvolvedores",
    "/salvador/barra",
    "/salvador/formato/lata",
    "/salvador/cerveja/brahma",
    "/en/data",
    "/en/salvador/carnival",
    "/salvador/indice",
)

#: The pages fetched as each AI crawler; the first bar page in the sitemap joins them.
PROBE_PATHS: tuple[str, ...] = ("/", "/salvador", "/salvador/preco-da-cerveja", "/dados")

#: The pages the Wayback Machine is asked to capture each month (the index and the press pages).
ARCHIVE_PATHS: tuple[str, ...] = (
    "/",
    "/salvador",
    "/salvador/preco-da-cerveja",
    "/salvador/indice",
    "/imprensa",
    "/dados",
)

#: A single bar's or shop's page (pt and en); every other page in the sitemap is a hub.
DETAIL = re.compile(r"^/(?:en/)?[^/]+/(?:bares|mercados|depositos|bars|supermarkets|depots)/[^/]+$")
BAR = re.compile(r"^/[^/]+/bares/[^/]+$")
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def url(path: str) -> str:
    return f"{SITE}{path}"


def path_of(address: str) -> str:
    return urlsplit(address).path or "/"


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """The page URLs of a urlset, or the child sitemaps of a sitemap index (each list in document order)."""
    root = ET.fromstring(xml)
    locs = [(loc.text or "").strip() for loc in root.iter(f"{NS}loc")]
    if root.tag == f"{NS}sitemapindex":
        return [], [loc for loc in locs if loc]
    return [loc for loc in locs if loc], []


@dataclass(slots=True)
class Sitemap:
    status: str = "ok"
    note: str = ""
    urls: list[str] = field(default_factory=list)

    @property
    def paths(self) -> set[str]:
        return {path_of(address) for address in self.urls}

    def hubs(self) -> list[str]:
        """Every page that isn't a single bar's or shop's: home, cities, bairros, brands, formats, guides."""
        return [address for address in self.urls if not DETAIL.match(path_of(address))]

    def first_bar(self) -> str | None:
        return next((address for address in self.urls if BAR.match(path_of(address))), None)

    def listed(self, paths: tuple[str, ...]) -> tuple[list[str], list[str]]:
        """The paths the sitemap lists, and those it doesn't (yet); without a sitemap every path counts."""
        if self.status != "ok":
            return list(paths), []
        known = self.paths
        return [p for p in paths if p in known], [p for p in paths if p not in known]


def fetch_sitemap(api: Api) -> Sitemap:
    """Our sitemap's pages (a sitemap index is followed one level down)."""
    try:
        urls, children = parse_sitemap(check(api.get(SITEMAP_URL)).text)
        for child in children[:20]:
            more, _ = parse_sitemap(check(api.get(child)).text)
            urls += more
    except ET.ParseError:
        return Sitemap(status="error", note="the sitemap isn't valid XML")
    except (ApiError, httpx.HTTPError) as error:
        return Sitemap(status="error", note=describe(error))
    return Sitemap(urls=list(dict.fromkeys(urls)))
