"""Monthly captures in the Internet Archive's Wayback Machine (Save Page Now, anonymous): the index and press
pages, so journalists and readers can cite a dated copy. Slow on purpose, one page every 20 seconds, and best
effort: a refusal or a timeout is reported and the rest waits for next month."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .http import Api
from .site import ARCHIVE_PATHS, Sitemap, url

SAVE = "https://web.archive.org/save/"
DELAY = 20.0


@dataclass(slots=True)
class Archive:
    status: str = "ok"
    note: str = ""
    #: Page → HTTP status of the capture request (0: no answer in time).
    captures: dict[str, int] = field(default_factory=dict)

    @property
    def saved(self) -> int:
        return sum(200 <= status < 400 for status in self.captures.values())


def collect(api: Api, sitemap: Sitemap) -> Archive:
    result = Archive()
    paths, _ = sitemap.listed(ARCHIVE_PATHS)
    for path in paths:
        try:
            response = api.get(SAVE + url(path), delay=DELAY, retries=0, timeout=120.0)
            result.captures[path] = response.status_code
        except httpx.HTTPError:
            result.captures[path] = 0
            continue
        if response.status_code == 429:
            result.note = "the Wayback Machine asked us to slow down; the rest waits for next month"
            break
    if result.captures and not result.saved:
        result.status = "error"
        result.note = result.note or "no capture went through"
    return result
