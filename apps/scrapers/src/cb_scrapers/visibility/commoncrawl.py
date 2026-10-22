"""Is the site in Common Crawl's newest crawl? Common Crawl feeds most open training sets and takes no
submissions: its levers are the sitemap in robots.txt and links from pages it already crawls.

One documented index-server query a month (index.commoncrawl.org, the CDX API), the way Common Crawl tells
site owners to look themselves up. Its robots.txt keeps crawlers off the index's pages; this is not a crawl.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from .http import Api, ApiError, check, describe
from .site import HOST

COLLECTIONS = "https://index.commoncrawl.org/collinfo.json"
#: Enough for every page of the site many times over; a capture beyond it changes nothing in the report.
LIMIT = 10_000


@dataclass(slots=True)
class CommonCrawl:
    status: str = "ok"
    note: str = ""
    crawl: str = ""
    crawl_name: str = ""
    captures: int = 0
    #: Captures that got a 200 (the others are redirects, 404s…).
    ok_captures: int = 0


def collect(api: Api) -> CommonCrawl:
    result = CommonCrawl()
    try:
        collections = check(api.get(COLLECTIONS)).json()
        newest = collections[0]
        result.crawl, result.crawl_name = str(newest["id"]), str(newest.get("name", ""))
        response = api.get(
            str(newest["cdx-api"]),
            params={
                "url": HOST,
                "matchType": "domain",
                "output": "json",
                "fl": "status",
                "limit": str(LIMIT),
            },
            timeout=120.0,
        )
        if response.status_code == 404 and "No Captures found" in response.text:
            return result
        check(response)
    except (ApiError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
        result.status, result.note = "error", describe(error)
        return result
    for line in response.text.splitlines():
        if not line.strip():
            continue
        result.captures += 1
        try:
            result.ok_captures += json.loads(line).get("status") == "200"
        except (json.JSONDecodeError, AttributeError):
            continue
    return result
