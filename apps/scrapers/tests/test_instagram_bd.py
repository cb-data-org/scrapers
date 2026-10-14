import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers.adapters import instagram_bd
from cb_scrapers.adapters.instagram_bd import InstagramBD, branch_of, venues_ref
from cb_scrapers.ai import gemini as gemini_module
from cb_scrapers.ai.gemini import RETRY_WAITS_S, Gemini
from cb_scrapers.ai.workers_ai import JSON_ONLY, Extraction
from cb_scrapers.core import Item, PoliteClient
from cb_scrapers.core.runner import RunReport, run_adapter
from cb_scrapers.models import DealDraft, IngestRequest
from tests.fakes import FakeSite

SITE = "https://cervejabarata.com.br"
GRAPH = f"{instagram_bd.GRAPH}/1784"
NOW = datetime.now(UTC)
Polite = Callable[..., PoliteClient]


def draft(**fields: Any) -> DealDraft:
    base = {
        "venue_name": None,
        "venue_ref": None,
        "beer": "Brahma",
        "brand": "Brahma",
        "format": "chopp",
        "volume_ml": 300,
        "units": 1,
        "packaging": "single",
        "on_site": None,
        "price": 5.9,
        "currency": "BRL",
        "kind": "promo",
        "open_bar_minutes": None,
        "schedules": [],
        "conditions": None,
        "service_charge_pct": None,
        "couvert": None,
        "valid_from": None,
        "valid_until": None,
        "observed_on": None,
        "confidence": 0.9,
        "notes": None,
    }
    return DealDraft.model_validate({**base, **fields})


def post(post_id: str, days_ago: int, caption: str) -> dict[str, Any]:
    when = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    return {
        "id": post_id,
        "timestamp": when,
        "caption": caption,
        "media_type": "IMAGE",
        "media_url": "https://cdn.test/1.jpg",
        "permalink": f"https://www.instagram.com/p/{post_id}/",
    }


@pytest.fixture(autouse=True)
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CB_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CB_META_TOKEN", "token")
    monkeypatch.setenv("CB_IG_USER_ID", "1784")
    monkeypatch.setenv("CB_GEMINI_KEY", "key")
    monkeypatch.delenv("CB_IG_BUDGET_MIN", raising=False)
    handles = tmp_path / "handles.csv"
    handles.write_text("city,venue,instagram,note\nsalvador,zaccaria-bar,zaccariabar,\n", "utf-8")
    monkeypatch.setattr(instagram_bd, "HANDLES_FILE", handles)


VENUES = [
    {"id": 1119, "slug": "o-bar-do-bahia", "name": "O Bar do Bahia", "instagram": "obardobahia"},
    {"id": 1672, "slug": "zaccaria-bar", "name": "Zaccaria Bar", "instagram": None},
]
POSTS = {
    "obardobahia": [
        post("1", 2, "Chopp Brahma por apenas R$ 6,99 durante todo o sábado! 🍺"),
        post("2", 3, "Feliz aniversário, equipe!"),  # no beer: not read
        post("3", 40, "Chopp Brahma R$ 5,99"),  # too old
    ],
    "zaccariabar": [post("4", 1, "")],  # a flyer without words: read
}


def site(
    venues: Sequence[dict[str, Any]] = VENUES, posts: dict[str, list[dict[str, Any]]] = POSTS
) -> FakeSite:
    def graph(request: httpx.Request) -> httpx.Response:
        handle = request.url.params["fields"].split("username(")[1].split(")")[0]
        media = {"data": posts[handle]}
        return httpx.Response(
            200, json={"business_discovery": {"username": handle, "name": f"@{handle}", "media": media}}
        )

    return FakeSite(
        {
            f"{SITE}/robots.txt": "",
            f"{SITE}/api/v1/salvador/venues-all.json": json.dumps({"items": list(venues)}),
            f"{SITE}/api/v1/lauro-de-freitas/venues-all.json": json.dumps({"items": []}),
            GRAPH: graph,
            "https://cdn.test/1.jpg": b"\xff\xd8jpeg",
        }
    )


def run(adapter: InstagramBD) -> list[Any]:
    items = []
    for task in adapter.tasks():
        items += list(adapter.parse(adapter.fetch(task)))
    return items


class FakeGemini:
    """Gemini's REST endpoint. Each request takes the next answer of the script (an HTTP status, or "ok"),
    then "ok"; an "ok" answers one draft for the post. Remembers the captions it was asked about."""

    def __init__(self, *script: int | str, minutes: float = 0, clock: list[float] | None = None) -> None:
        self.script = list(script)
        self.requests = 0
        self.captions: list[str] = []
        self.minutes, self.clock = minutes, clock

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.clock is not None:
            self.clock[0] += self.minutes * 60
        answer = self.script.pop(0) if self.script else "ok"
        if answer == 503:
            message = "This model is currently experiencing high demand. Please try again later."
            return httpx.Response(503, json={"error": {"code": 503, "message": message}})
        if answer != "ok":
            return httpx.Response(int(answer), json={"error": {"message": "quota exceeded"}})
        text = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        self.captions.append(text.split("Caption:\n", 1)[1].split(JSON_ONLY, 1)[0])
        reply = json.dumps({"drafts": [draft(price=6.99).model_dump(mode="json")]})
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": reply}]}}], "usageMetadata": {}}
        )


class Sink:
    """Records what a run sends: its batches (source refs) and its report."""

    def __init__(self) -> None:
        self.batches: list[list[Item]] = []
        self.reports: list[RunReport] = []

    def send(self, source: str, items: Sequence[Item]) -> int:
        self.batches.append(list(items))
        return len(items)

    def report(self, report: RunReport) -> None:
        self.reports.append(report)

    def catalog(self) -> None:
        return None

    def last_success(self) -> dict[str, datetime]:
        return {}

    def refs(self) -> list[str]:
        return [item.source_ref for batch in self.batches for item in batch]


class Killed(BaseException):
    """The job's time limit: the process ends mid-run, without a chance to save."""


def adapter_with(fake: FakeGemini, clock: list[float] | None = None) -> type[InstagramBD]:
    """InstagramBD with Gemini's endpoint faked (the real client: its pacing, retries and giving up), and its
    clock when given."""

    class Faked(InstagramBD):
        def __init__(self, http: PoliteClient) -> None:
            super().__init__(http)
            self.gemini = Gemini("key", httpx.Client(transport=httpx.MockTransport(fake)))
            if clock is not None:
                self.clock = lambda: clock[0]

    return Faked


def run_job(
    polite: Polite, fake: FakeGemini, fake_site: FakeSite, clock: list[float] | None = None
) -> tuple[Sink, RunReport]:
    """One run through the runner, as the workflow does it."""
    sink = Sink()
    report = run_adapter(
        adapter_with(fake, clock), sink, client_factory=lambda delay: polite(fake_site, delay=delay)
    )
    return sink, report


def saved(tmp_path: Path) -> tuple[str, set[str]]:
    """The saved cursor's account and the posts saved as read."""
    cursor = json.loads((tmp_path / instagram_bd.CURSOR_FILE).read_text("utf-8"))
    return cursor["handle"], set(json.loads((tmp_path / instagram_bd.SEEN_FILE).read_text("utf-8")))


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Gemini's pauses and retry waits, without waiting."""
    slept: list[float] = []
    monkeypatch.setattr(gemini_module.time, "sleep", slept.append)
    return slept


def test_recent_beer_posts_become_drafts_at_their_venue(
    polite: Polite, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    def extract(self: Any, caption: str, images: list[Any], venue: str, posted: Any) -> Extraction:
        calls.append((venue, caption))
        extraction = Extraction("json_prompt", "{}")
        extraction.drafts = [draft(price=6.99)] if caption else []
        return extraction

    monkeypatch.setattr(instagram_bd.Gemini, "extract_post", extract)
    adapter = InstagramBD(polite(site()))
    items = run(adapter)
    assert calls == [
        ("O Bar do Bahia", "Chopp Brahma por apenas R$ 6,99 durante todo o sábado! 🍺"),
        ("Zaccaria Bar", ""),
    ]
    assert [(i.data["venue_ref"], i.data["price"], i.source_ref) for i in items] == [
        ("venue:1119", 6.99, "instagram:1:0")
    ]
    assert items[0].data["notes"].startswith("Instagram @obardobahia https://www.instagram.com/p/1/")
    IngestRequest.model_validate({"source": "instagram_bd", "items": [i.to_json() for i in items]})
    # Each post is read once.
    again = InstagramBD(polite(site()))
    assert run(again) == [] and len(calls) == 2


def test_does_nothing_without_its_keys(polite: Polite, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CB_GEMINI_KEY")
    assert list(InstagramBD(polite(site())).tasks()) == []


def test_a_post_gemini_could_not_read_is_tried_again(
    polite: Polite, waits: list[float], tmp_path: Path
) -> None:
    # The first post's call is overloaded three times (its two retries included); the next one answers.
    fake = FakeGemini(503, 503, 503)
    sink, report = run_job(polite, fake, site())
    assert [wait for wait in waits if wait in RETRY_WAITS_S] == list(RETRY_WAITS_S)
    assert fake.requests == 4
    assert sink.refs() == ["instagram:4:0"]
    assert saved(tmp_path) == ("obardobahia", {"4"})
    assert report.status == "ok"
    # The next run reads it.
    again, _ = run_job(polite, FakeGemini(), site())
    assert again.refs() == ["instagram:1:0"]
    assert saved(tmp_path) == ("obardobahia", {"1", "4"})


THREE = [
    {"id": 1, "slug": "a", "name": "Bar A", "instagram": "bar_a"},
    {"id": 2, "slug": "b", "name": "Bar B", "instagram": "bar_b"},
    {"id": 3, "slug": "c", "name": "Bar C", "instagram": "bar_c"},
]
THREE_POSTS = {
    "bar_a": [post("a1", 1, "Chopp R$ 5"), post("a2", 2, "Cerveja R$ 6"), post("a3", 3, "Happy hour")],
    "bar_b": [post("b1", 1, "Chopp R$ 7")],
    "bar_c": [post("c1", 1, "Chopp R$ 8")],
}


def test_gemini_overloaded_twice_in_a_row_stops_the_run_where_the_next_one_resumes(
    polite: Polite, waits: list[float], tmp_path: Path
) -> None:
    fake = FakeGemini(*[503] * 6)
    fake_site = site(THREE, THREE_POSTS)
    sink, report = run_job(polite, fake, fake_site)
    # Two calls, each with its retries; no third call, and no other account read.
    assert fake.requests == 6
    assert sum(1 for request in fake_site.requests if request.url.path == "/v21.0/1784") == 1
    assert sink.refs() == []
    assert saved(tmp_path) == ("bar_a", set())
    assert report.status == "partial"
    assert report.errors == [
        "Gemini unavailable (HTTP 503: This model is currently experiencing high demand. Please try again "
        "later.); stopped after 0 of 3 accounts, the rest waits for the next run"
    ]
    # Gemini is back: the next run starts at the same account and loses nothing.
    again, report = run_job(polite, FakeGemini(), site(THREE, THREE_POSTS))
    assert again.refs() == [f"instagram:{post}:0" for post in ("a1", "a2", "a3", "b1", "c1")]
    assert report.status == "ok"


def test_out_of_quota_twice_stops_the_run_too(polite: Polite, waits: list[float], tmp_path: Path) -> None:
    fake = FakeGemini(429, 429)
    _, report = run_job(polite, fake, site(THREE, THREE_POSTS))
    # A 429 isn't retried: two calls.
    assert fake.requests == 2
    assert [wait for wait in waits if wait in RETRY_WAITS_S] == []
    assert saved(tmp_path) == ("bar_a", set())
    assert "Gemini unavailable (HTTP 429: quota exceeded)" in report.errors[0]


def test_a_run_stops_at_its_time_budget_and_the_next_one_finishes_the_account(
    polite: Polite, waits: list[float], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CB_IG_BUDGET_MIN", "10")
    clock = [0.0]
    # Each read takes 6 minutes: two fit in 10, the third waits for the next run.
    sink, report = run_job(polite, FakeGemini(minutes=6, clock=clock), site(THREE, THREE_POSTS), clock)
    assert sink.refs() == ["instagram:a1:0", "instagram:a2:0"]
    assert saved(tmp_path) == ("bar_a", {"a1", "a2"})
    assert report.status == "ok"
    again, _ = run_job(polite, FakeGemini(), site(THREE, THREE_POSTS))
    assert again.refs() == ["instagram:a3:0", "instagram:b1:0", "instagram:c1:0"]


def test_the_progress_is_saved_after_each_account(polite: Polite, waits: list[float], tmp_path: Path) -> None:
    class KilledAtB(FakeGemini):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if "Chopp R$ 7" in request.content.decode():
                raise Killed
            return super().__call__(request)

    fake = KilledAtB()
    sink = Sink()
    with pytest.raises(Killed):
        run_adapter(
            adapter_with(fake),
            sink,
            client_factory=lambda delay: polite(site(THREE, THREE_POSTS), delay=delay),
        )
    # The first account's drafts went out before the second one started, and it is saved as done.
    assert sink.refs() == ["instagram:a1:0", "instagram:a2:0", "instagram:a3:0"]
    assert saved(tmp_path) == ("bar_b", {"a1", "a2", "a3"})


def test_the_cursor_follows_the_account_when_the_list_changes(polite: Polite, waits: list[float]) -> None:
    (Path(instagram_bd.cache_dir()) / instagram_bd.CURSOR_FILE).write_text(
        json.dumps({"next": 0, "handle": "bar_b"}), "utf-8"
    )
    # bar_a is new and sorts first: the run still starts at bar_b.
    sink, _ = run_job(polite, FakeGemini(), site(THREE, THREE_POSTS))
    assert sink.refs()[0] == "instagram:b1:0"
    # A cursor saved by the version before (an index only) still works.
    (Path(instagram_bd.cache_dir()) / instagram_bd.CURSOR_FILE).write_text(json.dumps({"next": 2}), "utf-8")
    (Path(instagram_bd.cache_dir()) / instagram_bd.SEEN_FILE).write_text("{}", "utf-8")
    sink, _ = run_job(polite, FakeGemini(), site(THREE, THREE_POSTS))
    assert sink.refs()[0] == "instagram:c1:0"


BAO = [
    {
        "id": 973,
        "slug": "bao-stiep",
        "name": "BÃO - Stiep",
        "instagram": "baopetiscaria",
        "neighbourhood": "stiep",
        "neighbourhood_name": "STIEP",
    },
    {
        "id": 1337,
        "slug": "bao-pituba",
        "name": "BÃO - Pituba",
        "instagram": "baopetiscaria",
        "neighbourhood": "pituba",
        "neighbourhood_name": "Pituba",
    },
]


def test_a_shared_account_credits_only_the_branch_its_caption_names(
    polite: Polite, waits: list[float]
) -> None:
    posts = {
        "baopetiscaria": [
            post("s", 1, "Happy hour no BÃO-Stiep: chopp Amstel R$ 4,95, ter a sex 16h às 20h"),
            post("all", 2, "Chopp em dobro em todas as unidades! 🍻"),
            post("both", 3, "Hoje tem chopp no Stiep e na Pituba"),
        ]
    }
    fake = FakeGemini()
    sink, _ = run_job(polite, fake, site(BAO, posts))
    items = [item for batch in sink.batches for item in batch]
    assert [(item.source_ref, item.data["venue_ref"]) for item in items] == [
        ("instagram:s:0", "venue:973"),
        ("instagram:all:0", "venues:973,1337"),
        ("instagram:both:0", "venues:973,1337"),
    ]
    assert items[0].data["venue_name"] == "BÃO - Stiep"
    assert "the caption names this branch" in items[0].data["notes"]
    # The rest wait for a person, who picks the branch: the account's name, and every branch in the notes.
    assert items[1].data["venue_name"] == "@baopetiscaria"
    assert (
        "shared by #973 BÃO - Stiep (STIEP), #1337 BÃO - Pituba (Pituba): the post names no branch"
        in (items[1].data["notes"])
    )
    IngestRequest.model_validate({"source": "instagram_bd", "items": [i.to_json() for i in items]})


def test_the_owners_list_adds_a_branch_to_a_chains_account(
    polite: Polite, waits: list[float], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handles = tmp_path / "handles.csv"
    handles.write_text("city,venue,instagram,note\nsalvador,bao,baopetiscaria,the Barra branch\n", "utf-8")
    monkeypatch.setattr(instagram_bd, "HANDLES_FILE", handles)
    barra = {"id": 1408, "slug": "bao", "name": "Bão", "instagram": None, "neighbourhood_name": "Barra"}
    posts = {"baopetiscaria": [post("x", 1, "Chopp R$ 5 hoje"), post("y", 2, "Chopp R$ 5 na Barra")]}
    sink, _ = run_job(polite, FakeGemini(), site([*BAO, barra], posts))
    assert [item.data["venue_ref"] for batch in sink.batches for item in batch] == [
        "venues:973,1337,1408",
        "venue:1408",
    ]


@pytest.mark.parametrize(
    ("caption", "branch"),
    [
        ("Chopp de graça para a primeira mesa", None),  # "de graça" is no bairro
        ("Saúde! Chopp Stella R$ 9", None),  # a toast and a beer, no branch
        ("Domingo na Stella Maris: balde de Heineken", 365),
        ("Barraca do Lôro: caipirinha", 50),
        ("Unidade Graça: chopp R$ 6", None),  # Graça is too common a word to tell on its own
    ],
)
def test_a_branch_is_named_by_more_than_an_everyday_word(caption: str, branch: int | None) -> None:
    venues = [
        {"id": 50, "name": "Barraca do Lôro - Lounge Beach Bar e Restaurante", "neighbourhood_name": "Graça"},
        {"id": 365, "name": "Lôro - Stella Maris", "neighbourhood_name": "Stella Maris"},
    ]
    found = branch_of(venues, caption)
    assert (found["id"] if found else None) == branch


def test_a_shared_accounts_reference_fits_the_contract() -> None:
    many = [{"id": 10_000 + n, "name": f"Bar {n}"} for n in range(40)]
    ref = venues_ref(many)
    assert ref.startswith("venues:10000,10001,") and len(ref) <= 120
    assert venues_ref([{"id": 1337}, {"id": 973}]) == "venues:973,1337"
