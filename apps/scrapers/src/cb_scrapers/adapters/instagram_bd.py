"""Bars' beer prices and promotions from their public Instagram posts (#101), daily.

Instagram's official Graph API only: Business Discovery, through the brand's own Meta app, Page and
professional account (#102), which reads public business and creator accounts' recent posts. No other way of
reading Instagram.

1. The accounts: our venues' `instagram` (the site's venues-all datasets) and the owner's list
   (data/seed/instagram-handles.csv: city, venue slug, handle), up to MAX_ACCOUNTS a run, in turns.
2. Each account's latest posts; those from the last RECENT_DAYS that mention beer (or show only an image,
   since a flyer says it in the picture). A post counts as read once Gemini has answered for it: one it
   couldn't read (overloaded, out of quota) is tried again.
3. Each post's caption and first images go to Gemini (ai/gemini.py, free tier, public content only), which
   returns deal drafts dated from the post ("hoje", "toda quinta").
4. Each draft is sent as a price at that venue (venue:<id>). The bar's own account is its own word: ingest
   approves its prices inside the hard bounds at once (as ai:instagram; the cron approves the rest), and the
   others go to the AI reviewer, which publishes or rejects them by the owner's threshold.
5. An account several venues share (a chain's: Bão, Casa Chálabi) credits a post to the one branch whose
   bairro or name the caption alone names. When it names none or several, the drafts list every branch
   (venues:<id>,<id>): ingest never approves them, and a person picks the branch.

It never holds up the job it runs in (Preço da Hora, then the start of the next run, inside the job's time
limit): it stops after CB_IG_BUDGET_MIN minutes (default BUDGET_MIN) or when Gemini is unavailable for the run
(two calls in a row overloaded or out of quota, ai/gemini.py), and the next run resumes at the account it
stopped in. The cursor and the posts read are saved after each account, once its drafts are sent, so a run cut
short keeps everything it finished.

Without CB_META_TOKEN, CB_IG_USER_ID and CB_GEMINI_KEY the adapter does nothing.
"""

import csv
import json
import logging
import os
import re
import time
from bisect import bisect_left
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
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
#: Minutes a run may spend (CB_IG_BUDGET_MIN): the job's limit also covers Preço da Hora and the next run.
BUDGET_MIN = 15.0
BEER = re.compile(
    r"cerve[jz]|chop+e?|beer|heineken|brahma|skol|amstel|stella|budweiser|spaten|corona|original|"
    r"devassa|eisenbahn|imp[eé]rio|itaipava|long ?neck|litr[aã]o|happy|🍺|🍻",
    re.I,
)
FIELDS = (
    "business_discovery.username({handle}){{username,name,media.limit({posts})"
    "{{id,timestamp,caption,media_type,media_url,permalink,children{{media_url,media_type}}}}}}"
)
#: Bairros whose names are everyday words in a post ("de graça", "saúde!", "barris de chopp"): they never pick
#: a branch on their own.
COMMON_BAIRROS = {
    "barris",
    "boa viagem",
    "bonfim",
    "calcada",
    "canela",
    "centro",
    "cidade nova",
    "comercio",
    "dois de julho",
    "graca",
    "liberdade",
    "mangueira",
    "mares",
    "nova esperanca",
    "novo horizonte",
    "palestina",
    "portao",
    "praia grande",
    "resgate",
    "retiro",
    "roma",
    "santa cruz",
    "santa luzia",
    "santo antonio",
    "saude",
    "sete de abril",
    "uruguai",
    "vida nova",
    "vista alegre",
    "vitoria",
}
#: Words of a venue's name that never tell its branches apart.
GENERIC = {"bar", "boteco", "pub", "restaurante", "bistro", "cafe", "lounge", "grill", "beach", "the", "and"}
#: "venues:<id>,<id>": a draft from a shared account that names no branch (the contract's venue_ref).
MAX_REF = 120


def budget_minutes() -> float:
    """CB_IG_BUDGET_MIN, else BUDGET_MIN (empty or not a positive number: the default)."""
    try:
        minutes = float(os.environ.get("CB_IG_BUDGET_MIN") or BUDGET_MIN)
    except ValueError:
        return BUDGET_MIN
    return minutes if minutes > 0 else BUDGET_MIN


def words(text: object) -> list[str]:
    """Lowercase words without accents: "BÃO - Stiep" → ["bao", "stiep"]."""
    return re.findall(r"[a-z0-9]+", normalize(text))


def branch_terms(venue: dict[str, Any], venues: list[dict[str, Any]]) -> set[str]:
    """What names this branch among an account's venues: its bairro (unless an everyday word) and each run of
    the words of its name that no other branch has ("BÃO - Stiep" next to "BÃO - Pituba": "stiep")."""
    terms: set[str] = set()
    bairro = " ".join(words(venue.get("neighbourhood_name") or venue.get("neighbourhood") or ""))
    if bairro and bairro not in COMMON_BAIRROS:
        terms.add(bairro)
    others = {word for other in venues if other is not venue for word in words(other.get("name"))}
    run: list[str] = []
    for word in [*words(venue.get("name")), ""]:
        if word and word not in others:
            run.append(word)
            continue
        # A run naming the branch needs a real word, and one word alone mustn't be a beer ("Stella Maris").
        meaningful = [w for w in run if len(w) >= 3 and w not in GENERIC]
        if meaningful and not (len(run) == 1 and BEER.search(run[0])):
            terms.add(" ".join(run))
        run = []
    return terms


def branch_of(venues: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    """The venue of an account's several that the text alone names; None when it names none or several."""
    if len(venues) == 1:
        return venues[0]
    padded = f" {' '.join(words(text))} "
    named = [venue for venue in venues if any(f" {term} " in padded for term in branch_terms(venue, venues))]
    return named[0] if len(named) == 1 else None


def venues_ref(venues: list[dict[str, Any]]) -> str:
    """The reference to every branch, for a person to pick from: "venues:973,1337" (at most MAX_REF long)."""
    ref = "venues:"
    for index, venue in enumerate(sorted(venues, key=lambda v: int(v["id"]))):
        part = f"{',' if index else ''}{int(venue['id'])}"
        if len(ref) + len(part) > MAX_REF:
            break
        ref += part
    return ref


def write_json(path: Path, value: Any) -> None:
    """Written beside and renamed over: a run killed mid-write leaves the old file whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value), "utf-8")
    temporary.replace(path)


@register
class InstagramBD(Adapter):
    name = "instagram_bd"
    cadence = DAILY
    delay = 1.0
    #: The cursor and the posts read are saved after each account: its drafts must be sent by then.
    flush_each_task = True

    def __init__(self, http: PoliteClient) -> None:
        super().__init__(http)
        self.token = os.environ.get("CB_META_TOKEN", "")
        self.ig_user = os.environ.get("CB_IG_USER_ID", "")
        self.site = os.environ.get("CB_API_URL", "https://cervejabarata.com.br").rstrip("/")
        key = os.environ.get("CB_GEMINI_KEY", "")
        self.gemini = Gemini(key, httpx.Client(timeout=120)) if key else None
        self.venues: dict[str, dict[str, Any]] = {}  # city:slug → venue
        self.accounts: dict[str, list[dict[str, Any]]] = {}  # handle → its venues (several: a chain's)
        self.seen_file = cache_dir() / SEEN_FILE
        self.cursor_file = cache_dir() / CURSOR_FILE
        try:
            self.seen: dict[str, str] = json.loads(self.seen_file.read_text("utf-8"))
        except (OSError, ValueError):
            self.seen = {}
        self.extractions = 0
        self.today = datetime.now(UTC).date()
        self.counts: Counter[str] = Counter()
        self.budget_s = budget_minutes() * 60
        self.clock: Callable[[], float] = time.monotonic
        self.started: float | None = None
        #: The account being read: the posts Gemini answered for (id → day), and whether a stop left some.
        self.read: dict[str, str] = {}
        self.left = False
        self.last_error = ""

    def configured(self) -> bool:
        return bool(self.token and self.ig_user and self.gemini)

    def stop(self) -> str | None:
        """Why no more posts are read this run, if so."""
        if self.gemini is not None and self.gemini.unavailable:
            return "Gemini unavailable"
        if self.started is not None and self.clock() - self.started >= self.budget_s:
            return f"{self.budget_s / 60:g}-minute budget spent"
        if self.extractions >= MAX_EXTRACTIONS:
            return f"{MAX_EXTRACTIONS} Gemini reads done"
        return None

    def tasks(self) -> Iterator[Task]:
        if not self.configured():
            log.info("instagram_bd: no Meta token, Instagram account or Gemini key; nothing to do")
            return
        for city in CITIES:
            yield Task(f"venues:{city}", f"{self.site}/api/v1/{city}/venues-all.json")
        for handle, venue in self.owner_list():
            self.add(handle, venue)
        handles = sorted(self.accounts)
        if not handles:
            return
        start = self.cursor(handles)
        turn = (handles[start:] + handles[:start])[:MAX_ACCOUNTS]
        self.started = self.clock()
        done = 0
        try:
            for handle in turn:
                if self.stop():
                    break
                self.read, self.left = {}, False
                yield Task(
                    f"ig:{handle}",
                    f"{GRAPH}/{self.ig_user}",
                    {"fields": FIELDS.format(handle=handle, posts=POSTS), "access_token": self.token},
                )
                # Back here, the runner has sent the account's drafts (flush_each_task): its posts count as
                # read. An account a stop cut short is where the next run starts.
                self.seen.update(self.read)
                done += 0 if self.left else 1
                self.save(handles, (start + done) % len(handles))
                if self.left:
                    break
        finally:
            reason = self.stop()
            if reason == "Gemini unavailable":
                self.problems.append(
                    f"Gemini unavailable ({self.last_error}); stopped after {done} of {len(turn)} accounts, "
                    "the rest waits for the next run"
                )
            log.info(
                "instagram_bd: %d of %d accounts done%s; %s",
                done,
                len(turn),
                f" (stopped: {reason})" if reason else "",
                ", ".join(f"{k} {v}" for k, v in self.counts.most_common()),
            )

    def add(self, handle: str, venue: dict[str, Any]) -> None:
        venues = self.accounts.setdefault(handle, [])
        if all(known["id"] != venue["id"] for known in venues):
            venues.append(venue)
            venues.sort(key=lambda known: int(known["id"]))

    def owner_list(self) -> list[tuple[str, dict[str, Any]]]:
        """The owner's handles, joined to our venues by city and slug (a chain's account on each branch)."""
        found: list[tuple[str, dict[str, Any]]] = []
        try:
            with HANDLES_FILE.open(encoding="utf-8") as rows:
                for row in csv.DictReader(rows):
                    venue = self.venues.get(f"{row['city']}:{row['venue']}")
                    handle = (row.get("instagram") or "").strip().lstrip("@").lower()
                    if venue and handle:
                        found.append((handle, venue))
                    else:
                        self.counts["owner list: venue or handle not found"] += 1
        except OSError:
            pass
        return found

    def cursor(self, handles: list[str]) -> int:
        """Where this run starts: the account saved last (or the next one, alphabetically, if it's gone)."""
        try:
            saved = json.loads(self.cursor_file.read_text("utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(saved, dict):
            return 0
        if isinstance(saved.get("handle"), str):
            return bisect_left(handles, saved["handle"]) % len(handles)
        try:
            return int(saved["next"]) % len(handles)
        except (KeyError, TypeError, ValueError):
            return 0

    def save(self, handles: list[str], index: int) -> None:
        """The progress: the next account to read, and the posts read in the last 60 days."""
        cutoff = (self.today - timedelta(days=60)).isoformat()
        self.seen = {post: day for post, day in self.seen.items() if day >= cutoff}
        write_json(self.cursor_file, {"next": index, "handle": handles[index]})
        write_json(self.seen_file, self.seen)

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
                    self.add(handle, venue)
            return []
        handle = key.split(":", 1)[1]
        account = fetched.json().get("business_discovery")
        if not account:
            self.counts["account not readable"] += 1
            return []
        self.counts["accounts read"] += 1
        read: dict[str, str] = {}
        items = list(self.posts(handle, account, read))
        # Only once every post went through: tasks() saves them as read when the runner has sent their drafts.
        self.read.update(read)
        return items

    def posts(self, handle: str, account: dict[str, Any], read: dict[str, str]) -> Iterator[Item]:
        for post in (account.get("media") or {}).get("data", []):
            posted = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z").date()
            if (self.today - posted).days > RECENT_DAYS or post["id"] in self.seen:
                continue
            caption = post.get("caption") or ""
            if caption and not BEER.search(normalize(caption) + caption):
                self.counts["post without beer"] += 1
                continue
            if self.stop():
                self.counts["post left for the next run"] += 1
                self.left = True
                continue
            self.extractions += 1
            drafts = self.drafts(handle, account, post, posted, caption)
            if drafts is None:
                # Not read: tried again by a later run; the next one when Gemini has just given up.
                self.left = self.left or bool(self.gemini and self.gemini.unavailable)
                continue
            read[post["id"]] = posted.isoformat()
            yield from drafts

    def drafts(
        self, handle: str, account: dict[str, Any], post: dict[str, Any], posted: date, caption: str
    ) -> list[Item] | None:
        """The post's drafts; None when Gemini didn't answer."""
        venues = self.accounts[handle]
        venue = branch_of(venues, caption)
        name = (venue["name"] if venue else account.get("name") or venues[0]["name"])[:120]
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
        extraction = self.gemini.extract_post(caption, images, name, posted)
        if extraction.error:
            self.counts["Gemini error"] += 1
            self.last_error = extraction.error
            log.warning("instagram_bd: @%s %s: %s", handle, post.get("permalink"), extraction.error)
            return None
        self.counts["posts read"] += 1
        self.counts["drafts"] += len(extraction.drafts)
        self.counts["invalid drafts"] += len(extraction.invalid)
        note = f"Instagram @{handle} {post.get('permalink') or ''}"
        if venue is None:
            # A chain's account: the post names no single branch, so a person picks one of these.
            self.counts["drafts for a person to place (shared account)"] += len(extraction.drafts)
            branches = ", ".join(
                f"#{v['id']} {v['name']}"
                + (f" ({v.get('neighbourhood_name')})" if v.get("neighbourhood_name") else "")
                for v in venues
            )
            note += f" · shared by {branches}: the post names no branch"
        elif len(venues) > 1:
            self.counts["posts placed at a branch (shared account)"] += 1
            note += " · shared account: the caption names this branch"
        items = []
        for index, draft in enumerate(extraction.drafts):
            data = draft.model_dump(mode="json")
            data["venue_ref"] = f"venue:{venue['id']}" if venue else venues_ref(venues)
            data["venue_name"] = name
            data["observed_on"] = data.get("observed_on") or posted.isoformat()
            data["notes"] = f"{note} {data.get('notes') or ''}".strip()[:500]
            items.append(Item("deal", f"instagram:{post['id']}:{index}", data))
        return items
