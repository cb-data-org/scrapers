"""Bars' beer prices and promotions from their public Instagram posts (#101), daily.

Instagram's official Graph API only: Business Discovery, through the brand's own Meta app, Page and
professional account (#102), which reads public business and creator accounts' recent posts. No other way of
reading Instagram.

1. The accounts: our venues' `instagram` (the site's venues-all datasets) and the owner's list
   (data/seed/instagram-handles.csv: city, venue slug, handle), up to MAX_ACCOUNTS a run, in turns.
2. Each account's latest posts; those from the last RECENT_DAYS that mention beer (or show only an image,
   since a flyer says it in the picture), each read once.
3. Each post's caption and first images go to Gemini (ai/gemini.py, free tier, public content only), which
   returns deal drafts dated from the post ("hoje", "toda quinta").
4. Each draft is sent as a price at that venue (venue:<id>). The bar's own account is its own word: ingest
   approves its prices inside the hard bounds at once (as ai:instagram; the cron approves the rest), and the
   others go to the AI reviewer, which publishes or rejects them by the owner's threshold.

Without CB_META_TOKEN, CB_IG_USER_ID and CB_GEMINI_KEY the adapter does nothing.
"""

import csv
import json
import logging
import os
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ..ai.gemini import Gemini
from ..core import DAILY, Adapter, Fetched, Item, PoliteClient, Task, normalize, register
from ..core.cache import cache_dir

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
CITIES = ("salvador", "lauro-de-freitas")
HANDLES_FILE = Path(__file__).resolve().parents[5] / "data" / "seed" / "instagram-handles.csv"
SEEN_FILE = "instagram-seen.json"
CURSOR_FILE = "instagram-cursor.json"
#: Business Discovery allows about 200 calls an hour; the run takes a share and the next runs the rest.
MAX_ACCOUNTS = 60
POSTS = 10
RECENT_DAYS = 14
#: Gemini calls a run (its free tier allows about a thousand a day) and images per post.
MAX_EXTRACTIONS = 60
IMAGES = 3
BEER = re.compile(
    r"cerve[jz]|chop+e?|beer|heineken|brahma|skol|amstel|stella|budweiser|spaten|corona|original|"
    r"devassa|eisenbahn|imp[eé]rio|itaipava|long ?neck|litr[aã]o|happy|🍺|🍻",
    re.I,
)
FIELDS = (
    "business_discovery.username({handle}){{username,name,media.limit({posts})"
    "{{id,timestamp,caption,media_type,media_url,permalink,children{{media_url,media_type}}}}}}"
)


@register
class InstagramBD(Adapter):
    name = "instagram_bd"
    cadence = DAILY
    delay = 1.0

    def __init__(self, http: PoliteClient) -> None:
        super().__init__(http)
        self.token = os.environ.get("CB_META_TOKEN", "")
        self.ig_user = os.environ.get("CB_IG_USER_ID", "")
        self.site = os.environ.get("CB_API_URL", "https://cervejabarata.com.br").rstrip("/")
        key = os.environ.get("CB_GEMINI_KEY", "")
        self.gemini = Gemini(key, httpx.Client(timeout=120)) if key else None
        self.venues: dict[str, dict[str, Any]] = {}  # city:slug → venue
        self.accounts: dict[str, dict[str, Any]] = {}  # handle → venue
        self.seen_file = cache_dir() / SEEN_FILE
        self.cursor_file = cache_dir() / CURSOR_FILE
        try:
            self.seen: dict[str, str] = json.loads(self.seen_file.read_text("utf-8"))
        except (OSError, ValueError):
            self.seen = {}
        self.extractions = 0
        self.today = datetime.now(UTC).date()
        self.counts: Counter[str] = Counter()

    def configured(self) -> bool:
        return bool(self.token and self.ig_user and self.gemini)

    def tasks(self) -> Iterator[Task]:
        if not self.configured():
            log.info("instagram_bd: no Meta token, Instagram account or Gemini key; nothing to do")
            return
        for city in CITIES:
            yield Task(f"venues:{city}", f"{self.site}/api/v1/{city}/venues-all.json")
        self.accounts.update(self.owner_list())
        handles = sorted(self.accounts)
        if not handles:
            return
        try:
            start = int(json.loads(self.cursor_file.read_text("utf-8"))["next"]) % len(handles)
        except (OSError, ValueError, KeyError, TypeError):
            start = 0
        turn = (handles[start:] + handles[:start])[:MAX_ACCOUNTS]
        try:
            for handle in turn:
                yield Task(
                    f"ig:{handle}",
                    f"{GRAPH}/{self.ig_user}",
                    {"fields": FIELDS.format(handle=handle, posts=POSTS), "access_token": self.token},
                )
        finally:
            self.cursor_file.parent.mkdir(parents=True, exist_ok=True)
            self.cursor_file.write_text(json.dumps({"next": (start + len(turn)) % len(handles)}), "utf-8")
            cutoff = (self.today - timedelta(days=60)).isoformat()
            self.seen = {post: day for post, day in self.seen.items() if day >= cutoff}
            self.seen_file.write_text(json.dumps(self.seen), "utf-8")
            log.info(
                "instagram_bd: %d accounts, %s",
                len(turn),
                ", ".join(f"{k} {v}" for k, v in self.counts.most_common()),
            )

    def owner_list(self) -> dict[str, dict[str, Any]]:
        """The owner's handles, joined to our venues by city and slug."""
        found: dict[str, dict[str, Any]] = {}
        try:
            with HANDLES_FILE.open(encoding="utf-8") as rows:
                for row in csv.DictReader(rows):
                    venue = self.venues.get(f"{row['city']}:{row['venue']}")
                    handle = (row.get("instagram") or "").strip().lstrip("@").lower()
                    if venue and handle:
                        found[handle] = venue
                    else:
                        self.counts["owner list: venue or handle not found"] += 1
        except OSError:
            pass
        return found

    def fetch(self, task: Task) -> Fetched:
        # Our own datasets, and Meta's official API (robots.txt is for crawlers, not API clients).
        response = self.http.get(task.url, params=dict(task.params), robots=not task.key.startswith("ig:"))
        return Fetched(task, response.content, response.headers.get("content-type", ""))

    def parse(self, fetched: Fetched) -> Iterable[Item]:
        key = fetched.task.key
        if key.startswith("venues:"):
            city = key.split(":", 1)[1]
            for venue in fetched.json().get("items", []):
                venue["city"] = city
                self.venues[f"{city}:{venue['slug']}"] = venue
                handle = (venue.get("instagram") or "").lower()
                if handle:
                    self.accounts.setdefault(handle, venue)
            return []
        handle = key.split(":", 1)[1]
        account = fetched.json().get("business_discovery")
        if not account:
            self.counts["account not readable"] += 1
            return []
        self.counts["accounts read"] += 1
        return list(self.posts(handle, account))

    def posts(self, handle: str, account: dict[str, Any]) -> Iterator[Item]:
        venue = self.accounts[handle]
        for post in (account.get("media") or {}).get("data", []):
            posted = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z").date()
            if (self.today - posted).days > RECENT_DAYS or post["id"] in self.seen:
                continue
            caption = post.get("caption") or ""
            if caption and not BEER.search(normalize(caption) + caption):
                self.counts["post without beer"] += 1
                continue
            if self.extractions >= MAX_EXTRACTIONS:
                self.counts["post left for the next run"] += 1
                continue
            self.extractions += 1
            self.seen[post["id"]] = posted.isoformat()
            yield from self.drafts(handle, venue, post, posted, caption)

    def drafts(
        self, handle: str, venue: dict[str, Any], post: dict[str, Any], posted: date, caption: str
    ) -> Iterator[Item]:
        urls = [post.get("media_url")] if post.get("media_type") == "IMAGE" else []
        urls += [
            child.get("media_url")
            for child in (post.get("children") or {}).get("data", [])
            if child.get("media_type") == "IMAGE"
        ]
        images = []
        for url in [u for u in urls if u][:IMAGES]:
            try:
                response = self.http.get(url, robots=False)
            except httpx.HTTPError:
                continue
            kind = response.headers.get("content-type", "image/jpeg").split(";")[0]
            if kind.startswith("image/"):
                images.append((response.content, kind))
        assert self.gemini is not None
        extraction = self.gemini.extract_post(caption, images, venue["name"], posted)
        if extraction.error:
            self.counts["Gemini error"] += 1
            log.warning("instagram_bd: @%s %s: %s", handle, post.get("permalink"), extraction.error)
            return
        self.counts["posts read"] += 1
        self.counts["drafts"] += len(extraction.drafts)
        self.counts["invalid drafts"] += len(extraction.invalid)
        for index, draft in enumerate(extraction.drafts):
            data = draft.model_dump(mode="json")
            data["venue_ref"] = f"venue:{venue['id']}"
            data["venue_name"] = venue["name"]
            data["observed_on"] = data.get("observed_on") or posted.isoformat()
            data["notes"] = (
                f"Instagram @{handle} {post.get('permalink') or ''} {data.get('notes') or ''}".strip()[:500]
            )
            yield Item("deal", f"instagram:{post['id']}:{index}", data)
