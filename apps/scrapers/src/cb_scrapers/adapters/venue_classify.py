"""What kind of place each venue is (bar, boteco, restaurant, depósito, store…), daily, by two LLM
votes (venue_classifier.py) over free evidence (venue_evidence.py). Owner, 12 Oct 2026: nobody should have to
confirm by hand whether a venue is a depósito, a bar, a restaurant or a store.

1. The admin API hands out a batch (venue-classifier/candidates): never-checked venues first (newest first),
   uncertain ones again after a few days, then checks older than 90 days; and the mode (off, suggest, apply).
2. Venues in data/seed/venue-review.csv (the owner's decisions) and venues the API marks as locked (a person
   decided what they are) are skipped: the owner always wins.
3. Each venue's evidence goes to Gemini and Workers AI; Google Places is asked when the free evidence is thin
   or the votes disagree (at most MAX_PLACES a run; only the place id is kept). A retail main activity (CNAE
   division 47) then settles it whatever the votes say: never on site, a store or a depósito.
4. The results go back (venue-classifier/results, 25 a request). In `suggest` mode (the default until the
   evaluation probe reaches 95 %) the API only records them; in `apply` it changes the venues the votes agree
   on (or the register settles), with an audit row by ai:classifier.

Needs CB_API_URL, CB_INGEST_TOKEN, CB_GEMINI_KEY, CB_CF_ACCOUNT_ID and CB_CF_AI_TOKEN; CB_GOOGLE_PLACES_KEY
and CB_META_TOKEN + CB_IG_USER_ID add evidence. CB_CLASSIFY_LIMIT (venues a run, default 30),
CB_CLASSIFY_THRESHOLD (default 0.8) and CB_OVERTURE_FILE (a downloaded Overture GeoJSON-seq instead of a pass
over the release) tune it.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx

from ..ai.gemini import Gemini
from ..ai.workers_ai import WorkersAI
from ..cnpj import CnpjRegister, default_cache_file
from ..core import DAILY, Adapter, Fetched, Item, PoliteClient, Task, register
from ..core.cache import cache_dir
from ..places import MAX_CALLS as MAX_PLACES
from ..places import GooglePlaces
from ..venue_classifier import THRESHOLD, Result, VenueClassifier, Vote, Voter, vote_schema
from ..venue_evidence import ClassifierApi, EvidenceBuilder, Instagram
from ..venues import ShopBook, load_review
from .precodahora import SHOP_BOOK_FILE

log = logging.getLogger(__name__)

LIMIT = 30
#: Model calls a run: two votes a venue, a second round for some (Gemini's free tier and the shared neurons).
MAX_CALLS = 120
#: Workers AI's vote: the same model the Worker's AI reviewer uses (cheap in neurons, JSON schema mode).
CF_MODEL = "@cf/google/gemma-4-26b-a4b-it"


def voters_from_env(client: httpx.Client) -> tuple[list[Voter], WorkersAI | None]:
    voters: list[Voter] = []
    workers: WorkersAI | None = None
    if key := os.environ.get("CB_GEMINI_KEY"):
        gemini = Gemini(key, client)
        voters.append(Voter(gemini.model, gemini.generate))
    account, token = os.environ.get("CB_CF_ACCOUNT_ID"), os.environ.get("CB_CF_AI_TOKEN")
    if account and token:
        workers = WorkersAI(account, token, client, os.environ.get("CB_CLASSIFIER_CF_MODEL") or CF_MODEL)
        schema = vote_schema()
        voters.append(Voter(workers.model, lambda prompt, w=workers: w.complete(prompt, schema)))
    return voters, workers


def summary_line(venue: dict[str, Any], result: Result) -> str:
    was = f"{venue['kind']}/{'on site' if venue['on_site'] else 'take away'}"
    now = f"{result.kind}/{'on site' if result.on_site else 'take away'}"
    verdict = "certain" if result.certain else "uncertain"
    return (
        f"| {venue['id']} | {venue['name']} | {was} | {now} | {result.confidence:.2f} {verdict} | "
        f"{'; '.join(result.reasons) or '—'} |"
    )


@register
class VenueClassify(Adapter):
    name = "venue_classify"
    cadence = DAILY
    delay = 3.0

    def __init__(self, http: PoliteClient, api: ClassifierApi | None = None) -> None:
        super().__init__(http)
        url, token = os.environ.get("CB_API_URL"), os.environ.get("CB_INGEST_TOKEN")
        self.api = api or (ClassifierApi(url, token) if url and token else None)
        self.dry_run = os.environ.get("CB_DRY_RUN") == "1"
        self.limit = int(os.environ.get("CB_CLASSIFY_LIMIT") or LIMIT)
        self.client = httpx.Client(timeout=120)
        voters, workers = voters_from_env(self.client)
        self.ai = workers
        places_key = os.environ.get("CB_GOOGLE_PLACES_KEY")
        self.classifier = VenueClassifier(
            voters,
            GooglePlaces(places_key, self.client, MAX_PLACES) if places_key else None,
            threshold=float(os.environ.get("CB_CLASSIFY_THRESHOLD") or THRESHOLD),
            max_calls=int(os.environ.get("CB_CLASSIFY_MAX_CALLS") or MAX_CALLS),
        )
        meta, ig_user = os.environ.get("CB_META_TOKEN"), os.environ.get("CB_IG_USER_ID")
        overture_file = os.environ.get("CB_OVERTURE_FILE")
        self.evidence = EvidenceBuilder(
            http,
            CnpjRegister(http, default_cache_file()),
            ShopBook(cache_dir() / SHOP_BOOK_FILE),
            instagram=Instagram(self.client, meta, ig_user) if meta and ig_user else None,
            overture_file=Path(overture_file) if overture_file else None,
            overture_release=os.environ.get("CB_CLASSIFY_OVERTURE", "1") != "0",
        )
        self.mode = "off"
        self.medians: dict[str, Any] = {}
        self.venues: dict[int, dict[str, Any]] = {}
        self.pending: list[Result] = []
        self.counts: Counter[str] = Counter()
        self.lines: list[str] = []

    def configured(self) -> bool:
        return self.api is not None and len(self.classifier.voters) >= 2

    def tasks(self) -> Iterator[Task]:
        if not self.configured():
            log.info(
                "venue_classify: needs CB_API_URL, CB_INGEST_TOKEN, CB_GEMINI_KEY, CB_CF_ACCOUNT_ID and "
                "CB_CF_AI_TOKEN; nothing to do"
            )
            return
        try:
            yield Task("candidates")
            if self.mode == "off":
                return
            for venue_id in list(self.venues):
                if self.classifier.exhausted:
                    log.info("venue_classify: model call budget spent; the rest waits for the next run")
                    break
                yield Task(f"venue:{venue_id}", params={"id": venue_id})
        finally:
            self.flush()
            self.report()

    def fetch(self, task: Task) -> Fetched:
        if task.key == "candidates":
            assert self.api is not None
            batch = self.api.candidates(limit=self.limit)
            return Fetched(task, json.dumps(batch).encode(), "application/json")
        venue = self.venues[int(task.params["id"])]
        evidence = self.evidence.build(venue, self.medians.get(venue["city"]))
        result = self.classifier.classify(evidence, current_kind=venue["kind"])
        self.lines.append(summary_line(venue, result))
        log.info(
            "venue %s %s: %s/%s → %s/%s (%.2f, %s) — %s",
            venue["id"],
            venue["name"],
            venue["kind"],
            venue["on_site"],
            result.kind,
            result.on_site,
            result.confidence,
            "certain" if result.certain else "uncertain",
            "; ".join(result.reasons),
        )
        return Fetched(task, json.dumps(result.to_json()).encode(), "application/json")

    def parse(self, fetched: Fetched) -> Iterable[Item]:
        if fetched.task.key == "candidates":
            self.take(fetched.json())
            return []
        data = fetched.json()
        self.pending.append(_result_from_json(data))
        self.counts["certain" if data["certain"] else "uncertain"] += 1
        if len(self.pending) >= 25:
            self.flush()
        return []

    def take(self, batch: dict[str, Any]) -> None:
        """The API's batch, without the venues an owner decided."""
        self.mode = str(batch.get("mode") or "off")
        self.medians = dict(batch.get("medians") or {})
        if self.mode == "off":
            log.info("venue_classify: the classifier is off (/classifier in Telegram)")
            return
        review = load_review()
        for venue in batch.get("items") or []:
            if venue.get("locked"):
                self.counts["skipped: owner decided"] += 1
            elif venue.get("cnpj") and venue["cnpj"] in review:
                self.counts["skipped: in the review list"] += 1
            else:
                self.venues[int(venue["id"])] = venue
        self.evidence.prepare(list(self.venues.values()))

    def flush(self) -> None:
        if not self.pending:
            return
        items = [result.to_json() for result in self.pending]
        self.pending = []
        if self.dry_run or self.api is None:
            for item in items:
                print(json.dumps({"classification": item}, ensure_ascii=False))
            return
        try:
            outcome = self.api.results(items)
        except httpx.HTTPError as error:
            log.warning("venue_classify: results not stored (%s)", error)
            self.counts["not stored"] += len(items)
            return
        for key in ("applied", "suggested", "same", "uncertain", "locked"):
            self.counts[f"stored: {key}"] += int(outcome.get(key) or 0)

    def report(self) -> None:
        counts = ", ".join(f"{key} {value}" for key, value in sorted(self.counts.items())) or "nothing"
        places = self.classifier.places.calls if self.classifier.places else 0
        log.info(
            "venue_classify: mode %s · %s · %d model calls · %d Google Places lookups",
            self.mode,
            counts,
            self.classifier.calls,
            places,
        )
        if self.lines and (path := os.environ.get("GITHUB_STEP_SUMMARY")):
            with Path(path).open("a", encoding="utf-8") as summary:
                summary.write(
                    f"### Venue classifier ({self.mode})\n\n{counts}\n\n"
                    "| Venue | Name | Was | Votes say | Confidence | Why |\n|---|---|---|---|---|---|\n"
                    + "\n".join(self.lines)
                    + "\n"
                )


def _result_from_json(data: dict[str, Any]) -> Result:
    return Result(
        venue_id=int(data["venue_id"]),
        kind=data["kind"],
        on_site=bool(data["on_site"]),
        confidence=float(data["confidence"]),
        certain=bool(data["certain"]),
        reasons=list(data.get("reasons") or []),
        evidence=list(data.get("evidence") or []),
        votes=[
            Vote(v["model"], v["kind"], bool(v["on_site"]), float(v["confidence"]))
            for v in data.get("votes") or []
        ],
        google_place_id=data.get("google_place_id"),
    )
