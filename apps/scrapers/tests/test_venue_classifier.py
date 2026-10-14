import json
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers import classify_probe
from cb_scrapers.adapters.venue_classify import VenueClassify
from cb_scrapers.ai import WorkersAI
from cb_scrapers.ai import gemini as gemini_module
from cb_scrapers.ai.gemini import Gemini
from cb_scrapers.ai.workers_ai import TextReply
from cb_scrapers.cnae import code_text, describe
from cb_scrapers.cnpj import Company
from cb_scrapers.core import PoliteClient
from cb_scrapers.models import ClassifierResults
from cb_scrapers.models.contract import ClassifierVote
from cb_scrapers.places import GooglePlaces, PlaceHit
from cb_scrapers.venue_classifier import (
    EVIDENCE,
    RETAIL_REASON,
    VENUE_KINDS,
    VenueClassifier,
    VenueEvidence,
    Vote,
    Voter,
    build_prompt,
    category,
    combine,
    evidence_lines,
    parse_vote,
    retail_verdict,
    settle_retail,
)
from cb_scrapers.venue_evidence import (
    ClassifierApi,
    EvidenceBuilder,
    OvertureIndex,
    osm_query,
    overture_record,
)
from cb_scrapers.venues import ReviewRow, ShopRecord
from tests.fakes import FakeSite

API = "https://cervejabarata.test"


def company(**fields: Any) -> Company:
    base: dict[str, Any] = {
        "cnpj": "33894100000168",
        "cnae": 4723700,
        "active": True,
        "trade_name": "DEPOSITO ALTAS HORAS",
        "legal_name": "ALTAS HORAS COMERCIO DE BEBIDAS LTDA",
        "individual": False,
        "checked_on": "2026-10-12",
        "secondary": (5611204, 4729602),
        "mei": False,
    }
    return Company(**(base | fields))


def evidence(**fields: Any) -> VenueEvidence:
    base: dict[str, Any] = {
        "venue_id": 1,
        "name": "Bar Altas Horas",
        "city": "salvador",
        "lat": -12.95,
        "lng": -38.41,
        "bairro": "Pituaçu",
    }
    return VenueEvidence(**(base | fields))


def vote(kind: str = "deposito", on_site: bool = True, confidence: float = 0.9, model: str = "m1") -> Vote:
    return Vote(model, kind, on_site, confidence, (f"{model} thinks {kind}",), ("cnpj",))


def answer(kind: str = "deposito", on_site: bool = True, confidence: float = 0.9, **extra: Any) -> str:
    return json.dumps(
        {
            "kind": kind,
            "on_site": on_site,
            "confidence": confidence,
            "reasons": ["CNPJ main activity 4723-7/00 drinks retail"],
            "evidence_used": ["cnpj", "name"],
        }
        | extra
    )


# Evidence and prompt ----------------------------------------------------------------------------------------


def test_the_kinds_and_sources_match_the_contract() -> None:
    assert typing.get_args(ClassifierVote.model_fields["kind"].annotation) == VENUE_KINDS
    item = ClassifierResults.model_fields["items"].annotation
    result = typing.get_args(item)[0]
    evidence_type = typing.get_args(result.model_fields["evidence"].annotation)[0]
    assert typing.get_args(evidence_type) == EVIDENCE


def test_cnae_codes_read_like_the_register() -> None:
    assert code_text(5611204) == "5611-2/04"
    assert describe(4723700) == "4723-7/00 Comércio varejista de bebidas"
    assert describe(1234567) == "1234-5/67"


def test_category_follows_the_domain_rule() -> None:
    assert category("deposito", True) == category("deposito", False) == "deposito"
    assert category("outro", False) == "mercado"
    assert category("restaurante", True) == category("outro", True) == "bar"


def test_evidence_lines_show_the_register_maps_prices_and_receipts() -> None:
    receipts = ShopRecord(
        packs=["a", "b"], ratios={"x": 1.1, "y": 1.3, "z": 0.9}, samples=["HEINEKEN 600ML R$ 6,99"]
    )
    e = evidence(
        address="Rua A, 1",
        company=company(),
        osm_tags={"amenity": "bar", "name": "Altas Horas", "contact:phone": "123", "shop": "alcohol"},
        overture={"name": "Altas Horas", "primary": "liquor_store", "alternate": ["bar"]},
        prices={"n": 2, "on_site": 0, "takeaway": 1, "packs": 1, "median_ppn_minor": 1165, "samples": ["s1"]},
        medians={"bar": 2500, "shop": 1100},
        receipts=receipts,
    )
    text = "\n".join(evidence_lines(e))
    assert "Where: Rua A, 1, Pituaçu, Salvador" in text
    assert 'trade name (nome fantasia) "DEPOSITO ALTAS HORAS"' in text
    assert "Main activity: 4723-7/00 Comércio varejista de bebidas" in text
    assert "4729-6/02 Comércio varejista de mercadorias em lojas de conveniência" in text
    assert "OpenStreetMap tags: amenity=bar, shop=alcohol" in text
    assert "contact:phone" not in text
    assert "category liquor_store; alternate categories: bar" in text
    assert "median R$ 11.65 per litre" in text and "bars R$ 25.00, shops R$ 11.00" in text
    assert "sold beer by the box, pack or keg 2 times" in text and "1.10x" in text
    assert "Google Places" not in text
    assert e.sources() == ["name", "address", "cnpj", "osm", "overture", "prices", "receipts"]
    assert not e.weak()


def test_thin_evidence_is_weak_and_the_prompt_carries_definitions_schema_and_evidence() -> None:
    e = evidence(cnpj="33894100000168")
    assert e.weak()
    prompt = build_prompt(e)
    assert "deposito: a depósito de bebidas" in prompt
    assert (
        '"enum": ["bar", "boteco", "pub", "barraca", "restaurante", "deposito", "cervejaria", "outro"]'
        in prompt
    )
    assert "CNPJ register: not found" in prompt
    assert "Published prices: none" in prompt
    assert "among: name, address." in prompt
    # The venue's current kind is never shown: the votes stay independent of it.
    assert "Listed" not in prompt


def test_places_show_in_the_prompt_but_only_the_id_leaves() -> None:
    hit = PlaceHit(
        "ChIJxyz", "Depósito Altas Horas", "liquor_store", ("liquor_store", "store"), "OPERATIONAL"
    )
    e = evidence(places=hit)
    assert 'Google Places: "Depósito Altas Horas"; primary type liquor_store' in "\n".join(evidence_lines(e))
    assert "google_places" in e.sources()


# Votes ------------------------------------------------------------------------------------------------------


def test_parses_a_vote_and_refuses_anything_else() -> None:
    parsed = parse_vote("```json\n" + answer(confidence=1.4, evidence_used=["cnpj", "tarot"]) + "\n```", "m")
    assert parsed == Vote(
        "m", "deposito", True, 1.0, ("CNPJ main activity 4723-7/00 drinks retail",), ("cnpj",)
    )
    assert parse_vote(answer(kind="boate"), "m") is None
    assert parse_vote(answer(on_site="yes"), "m") is None
    assert parse_vote(answer(confidence="high"), "m") is None
    assert parse_vote("not json", "m") is None
    assert parse_vote("[]", "m") is None


def test_agreeing_sure_votes_are_certain() -> None:
    result = combine(1, [vote(), vote(model="m2", confidence=0.85)])
    assert (result.kind, result.on_site, result.certain, result.confidence) == ("deposito", True, True, 0.85)
    assert result.reasons == ["m1 thinks deposito", "m2 thinks deposito"]
    assert result.to_json()["votes"][1] == {
        "model": "m2",
        "kind": "deposito",
        "on_site": True,
        "confidence": 0.85,
    }


def test_unsure_or_disagreeing_votes_are_uncertain() -> None:
    low = combine(1, [vote(), vote(model="m2", confidence=0.6)], threshold=0.8)
    assert not low.certain and low.reasons[0].startswith(
        "the models agree but aren't sure enough (0.60 < 0.80)"
    )
    split = combine(1, [vote("bar", True), vote("outro", False, model="m2", confidence=0.95)])
    assert not split.certain
    assert split.kind == "outro"
    assert split.reasons[0] == "the models disagree: m1 says bar (on site), m2 says outro (take away)"
    # Same category, but one says depósito with tables, the other without.
    assert not combine(1, [vote("deposito", True), vote("deposito", False, model="m2")]).certain


def test_one_missing_vote_is_never_certain() -> None:
    assert not combine(1, [vote(confidence=0.99), None]).certain
    nothing = combine(1, [None, None], current_kind="pub")
    assert (nothing.kind, nothing.certain, nothing.reasons) == ("pub", False, ["no model answered"])


def test_a_kind_disagreement_inside_a_category_keeps_the_venue_kind() -> None:
    votes = [vote("bar", True, 0.9), vote("boteco", True, 0.95, model="m2")]
    assert combine(1, votes, current_kind="pub").kind == "pub"
    assert combine(1, votes, current_kind="deposito").kind == "boteco"
    assert combine(1, votes).kind == "boteco"
    assert combine(1, votes).certain


class FakeVoter:
    def __init__(self, model: str, replies: list[str | None]) -> None:
        self.model, self.replies, self.prompts = model, list(replies), []

    def __call__(self, prompt: str) -> TextReply:
        self.prompts.append(prompt)
        text = self.replies.pop(0)
        return TextReply(self.model, error="HTTP 429: quota") if text is None else TextReply(self.model, text)


class FakePlaces:
    def __init__(self, hit: PlaceHit | None) -> None:
        self.hit, self.calls, self.exhausted = hit, 0, False

    def search(self, name: str, city: str, lat: float, lng: float) -> PlaceHit | None:
        self.calls += 1
        return self.hit


HIT = PlaceHit("ChIJabc", "Casagrande", "convenience_store", ("convenience_store",), "OPERATIONAL")


def classifier(
    a: FakeVoter, b: FakeVoter, places: FakePlaces | None = None, **kwargs: Any
) -> VenueClassifier:
    return VenueClassifier([Voter(a.model, a), Voter(b.model, b)], places, **kwargs)  # type: ignore[arg-type]


def test_classifies_without_google_when_the_free_evidence_agrees() -> None:
    a, b, places = FakeVoter("g", [answer()]), FakeVoter("w", [answer()]), FakePlaces(HIT)
    result = classifier(a, b, places).classify(evidence(company=company()))
    assert result.certain and result.google_place_id is None and places.calls == 0
    assert a.prompts == b.prompts


def test_asks_google_first_when_the_evidence_is_thin_and_keeps_only_the_place_id() -> None:
    a = FakeVoter("g", [answer("outro", False)])
    b = FakeVoter("w", [answer("outro", False)])
    places = FakePlaces(HIT)
    result = classifier(a, b, places).classify(evidence(name="Casagrande Conveniencias"))
    assert places.calls == 1
    assert "Google Places" in a.prompts[0]
    assert result.google_place_id == "ChIJabc" and "google_places" in result.evidence
    assert "Casagrande" not in json.dumps(result.to_json()["reasons"])


#: Registered as a bar (not retail): the votes decide, the register's retail rule stays out of it.
BAR_REGISTER = company(cnae=5611204, trade_name="BAR ALTAS HORAS")


def test_votes_again_with_google_when_the_first_votes_disagree() -> None:
    a = FakeVoter("g", [answer("bar", True), answer("deposito", True)])
    b = FakeVoter("w", [answer("deposito", True), answer("deposito", True)])
    places = FakePlaces(HIT)
    run = classifier(a, b, places)
    result = run.classify(evidence(company=BAR_REGISTER))
    assert result.certain and result.kind == "deposito" and run.calls == 4 and places.calls == 1
    # Without Google, a disagreement stays uncertain.
    a2, b2 = FakeVoter("g", [answer("bar", True)]), FakeVoter("w", [answer("deposito", True)])
    assert not classifier(a2, b2, None).classify(evidence(company=BAR_REGISTER)).certain


def test_a_failed_model_call_is_an_uncertain_result_and_counts_against_the_budget() -> None:
    a, b = FakeVoter("g", [None]), FakeVoter("w", [answer()])
    run = classifier(a, b, None, max_calls=3)
    result = run.classify(evidence(company=BAR_REGISTER))
    assert not result.certain and run.errors == ["g: HTTP 429: quota"]
    assert run.exhausted
    # A retail register settles the venue all the same: the rule doesn't need both votes.
    a, b = FakeVoter("g", [None]), FakeVoter("w", [answer("bar", True)])
    settled = classifier(a, b).classify(evidence(company=company(cnae=4712100, trade_name="MERCADINHO X")))
    assert (settled.kind, settled.on_site, settled.certain) == ("outro", False, True)
    assert "only one model answered" in settled.reasons


# Model clients and Google Places ----------------------------------------------------------------------------


def test_workers_ai_completes_a_text_prompt_with_a_json_schema() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        return httpx.Response(200, json={"success": True, "result": {"response": answer(), "usage": usage}})

    ai = WorkersAI("acct", "tok", httpx.Client(transport=httpx.MockTransport(handler)))
    reply = ai.complete("hello", {"type": "object"})
    assert reply.text == answer() and reply.error is None and ai.spent == pytest.approx(reply.neurons)
    assert bodies[0]["response_format"] == {"type": "json_schema", "json_schema": {"type": "object"}}
    assert bodies[0]["messages"] == [{"role": "user", "content": "hello"}]
    failing = WorkersAI(
        "acct", "tok", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    assert failing.complete("hello").error.startswith("HTTP 500")


def test_gemini_generates_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_module.time, "sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "key"
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        parts = [{"text": answer()}]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}}], "usageMetadata": {}})

    gemini = Gemini("key", httpx.Client(transport=httpx.MockTransport(handler)))
    assert parse_vote(gemini.generate("p").text, gemini.model) is not None
    quota = Gemini("key", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={}))))
    assert quota.generate("p").error == "HTTP 429: 429"


def test_google_places_search_is_capped_and_logs_nothing_it_returns(caplog: pytest.LogCaptureFixture) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        place = {
            "id": "ChIJ1",
            "displayName": {"text": "Depósito Altas Horas"},
            "primaryType": "liquor_store",
            "types": ["liquor_store", "store"],
            "businessStatus": "OPERATIONAL",
        }
        return httpx.Response(200, json={"places": [place]})

    places = GooglePlaces("key", httpx.Client(transport=httpx.MockTransport(handler)), max_calls=1)
    hit = places.search("Bar Altas Horas", "salvador", -12.95, -38.41)
    assert hit == PlaceHit(
        "ChIJ1", "Depósito Altas Horas", "liquor_store", ("liquor_store", "store"), "OPERATIONAL"
    )
    assert requests[0].headers["X-Goog-FieldMask"].startswith("places.id,")
    assert places.search("Other", "salvador", 0, 0) is None and len(requests) == 1
    broken = GooglePlaces("key", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    assert broken.search("Bar", "salvador", 0, 0) is None
    assert "Altas" not in caplog.text


# Evidence from the sources ----------------------------------------------------------------------------------


def test_one_overpass_query_for_a_batch() -> None:
    assert osm_query(["node/2", "way/5", "node/1", "nonsense", "relation/x"]) == (
        "[out:json][timeout:60];(node(id:1,2);way(id:5););out tags;"
    )
    assert osm_query([]) is None


def feature(place_id: str, name: str, lat: float, lng: float, primary: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "properties": {
            "id": place_id,
            "names": {"primary": name},
            "categories": {"primary": primary, "alternate": ["bar"]},
        },
    }


def test_overture_places_near_the_batch_by_id_or_by_name(tmp_path: Path) -> None:
    near = feature("ov-1", "Depósito Altas Horas", -12.9501, -38.4101, "liquor_store")
    far = feature("ov-2", "Bar Longe", -12.80, -38.30, "bar")
    by_id = feature("ov-3", "Outro Nome", -12.70, -38.20, "restaurant")
    path = tmp_path / "places.geojsonseq"
    path.write_text("\n".join("\x1e" + json.dumps(f) for f in (near, far, by_id)), "utf-8")
    venues = [
        {"id": 1, "name": "Altas Horas", "lat": -12.95, "lng": -38.41},
        {"id": 2, "name": "Zé", "lat": -12.0, "lng": -38.0, "overture_id": "ov-3"},
    ]
    index = OvertureIndex.from_file(path, venues)
    assert sorted(index.by_id) == ["ov-1", "ov-3"]
    assert index.find(venues[0])["primary"] == "liquor_store"
    assert index.find(venues[1])["primary"] == "restaurant"
    assert index.find({"id": 3, "name": "Nada A Ver", "lat": -12.95, "lng": -38.41}) is None
    assert overture_record({"properties": {"names": {}}, "geometry": None}) is None


class FakeRegister:
    def __init__(self, known: dict[str, Company]) -> None:
        self.known, self.asked = known, []

    def lookup(self, cnpj: str) -> Company | None:
        self.asked.append(cnpj)
        return self.known.get(cnpj)


def candidate(venue_id: int, **fields: Any) -> dict[str, Any]:
    base = {
        "id": venue_id,
        "name": f"Venue {venue_id}",
        "kind": "bar",
        "on_site": True,
        "status": "active",
        "city": "salvador",
        "neighbourhood": "Pituba",
        "address": None,
        "lat": -12.99,
        "lng": -38.46,
        "cnpj": None,
        "osm_ref": None,
        "overture_id": None,
        "instagram": None,
        "created_at": 1,
        "classified_at": None,
        "google_place_id": None,
        "locked": False,
        "prices": {
            "n": 0,
            "on_site": 0,
            "takeaway": 0,
            "packs": 0,
            "median_ppn_minor": None,
            "samples": [],
            "by_type": [],
        },
    }
    return base | fields


def test_the_builder_joins_register_receipts_and_prices(polite: Callable[..., PoliteClient]) -> None:
    register = FakeRegister({"33894100000168": company()})
    book = type("Book", (), {"records": {"33894100000168": ShopRecord(packs=["x"])}})()
    builder = EvidenceBuilder(polite(FakeSite({})), register, book, overture_release=False)  # type: ignore[arg-type]
    builder.prepare([candidate(1, cnpj="33894100000168")])
    built = builder.build(candidate(1, cnpj="33894100000168"), {"bar": 2000, "shop": 1000})
    assert built.company and built.company.cnae == 4723700
    assert built.receipts and built.receipts.packs == ["x"]
    assert built.medians == {"bar": 2000, "shop": 1000}
    assert built.bairro == "Pituba" and built.osm_tags is None


# The adapter ------------------------------------------------------------------------------------------------


class FakeApi:
    """The admin API's classifier endpoints, as an httpx MockTransport."""

    def __init__(self, mode: str, items: list[dict[str, Any]]) -> None:
        self.mode, self.items = mode, items
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if request.url.path.endswith("/candidates"):
            items = [] if self.mode == "off" else self.items
            return httpx.Response(200, json={"mode": self.mode, "medians": {}, "items": items})
        counts = {
            "mode": self.mode,
            "stored": len(body["items"]),
            "applied": 0,
            "suggested": len(body["items"]),
        }
        return httpx.Response(200, json=counts | {"same": 0, "uncertain": 0, "locked": 0, "unknown": 0})


def run(adapter: VenueClassify) -> None:
    """What the runner does with an adapter's tasks."""
    for task in adapter.tasks():
        list(adapter.parse(adapter.fetch(task)))


@pytest.fixture
def adapter_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "CB_GEMINI_KEY",
        "CB_CF_ACCOUNT_ID",
        "CB_CF_AI_TOKEN",
        "CB_GOOGLE_PLACES_KEY",
        "CB_META_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CB_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CB_CLASSIFY_OVERTURE", "0")
    monkeypatch.delenv("CB_DRY_RUN", raising=False)


def make_adapter(polite: Callable[..., PoliteClient], api: FakeApi, voters: list[FakeVoter]) -> VenueClassify:
    client = ClassifierApi(API, "cb_token", transport=httpx.MockTransport(api))
    adapter = VenueClassify(polite(FakeSite({})), api=client)
    adapter.classifier = VenueClassifier([Voter(v.model, v) for v in voters])  # type: ignore[arg-type]
    adapter.evidence.register = FakeRegister({})  # type: ignore[assignment]
    return adapter


@pytest.mark.usefixtures("adapter_env")
def test_the_adapter_skips_owner_decisions_and_sends_the_results(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    from cb_scrapers.adapters import venue_classify

    monkeypatch.setattr(
        venue_classify, "load_review", lambda: {"11111111000111": ReviewRow("11111111000111", "keep")}
    )
    items = [candidate(1), candidate(2, locked=True), candidate(3, cnpj="11111111000111"), candidate(4)]
    api = FakeApi("suggest", items)
    a = FakeVoter("g", [answer("outro", False), answer("bar", True)])
    b = FakeVoter("w", [answer("outro", False), answer("bar", True, 0.5)])
    adapter = make_adapter(polite, api, [a, b])
    run(adapter)
    sent = [body for path, body in api.requests if path.endswith("/results")]
    assert [item["venue_id"] for item in sent[0]["items"]] == [1, 4]
    assert [item["certain"] for item in sent[0]["items"]] == [True, False]
    assert adapter.counts["skipped: owner decided"] == 1
    assert adapter.counts["skipped: in the review list"] == 1
    assert adapter.counts["stored: suggested"] == 2


@pytest.mark.usefixtures("adapter_env")
def test_the_adapter_does_nothing_when_off_or_without_two_models(polite: Callable[..., PoliteClient]) -> None:
    api = FakeApi("off", [candidate(1)])
    adapter = make_adapter(polite, api, [FakeVoter("g", []), FakeVoter("w", [])])
    run(adapter)
    assert [path for path, _ in api.requests] == ["/api/admin/venue-classifier/candidates"]
    lonely = make_adapter(polite, FakeApi("apply", [candidate(1)]), [FakeVoter("g", [])])
    assert list(lonely.tasks()) == []


@pytest.mark.usefixtures("adapter_env")
def test_a_dry_run_prints_instead_of_sending(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CB_DRY_RUN", "1")
    api = FakeApi("apply", [candidate(1)])
    run(make_adapter(polite, api, [FakeVoter("g", [answer()]), FakeVoter("w", [answer()])]))
    assert not [path for path, _ in api.requests if path.endswith("/results")]
    assert json.loads(capsys.readouterr().out)["classification"]["kind"] == "deposito"


# The evaluation ---------------------------------------------------------------------------------------------


def test_the_evaluation_labels_and_scores() -> None:
    venues = [
        candidate(1, name="Bar Altas Horas", cnpj="33894100000168"),
        candidate(2, name="Paulistana Pizza"),
        candidate(3, name="Bar Bom", cnpj="22222222000122"),
        candidate(4, name="Mercadinho", cnpj="33333333000133"),
    ]
    review = {
        "22222222000122": ReviewRow("22222222000122", "keep"),
        "33333333000133": ReviewRow("33333333000133", "hide"),
    }
    labels, missing = classify_probe.labels_for(venues, review)
    assert labels[1] == classify_probe.Label("owner", "deposito", frozenset({"deposito"}))
    assert labels[2].expected == "bar" and labels[3].expected == "bar" and labels[4].expected == "not_bar"
    assert "Iate Clube da Bahia" in missing and "33894100000168" not in missing
    assert classify_probe.right(labels[4], "deposito", False) and classify_probe.right(
        labels[4], "outro", False
    )
    assert not classify_probe.right(labels[3], "outro", False)

    def result(venue_id: int, kind: str, on_site: bool, certain: bool = True):
        return combine(
            venue_id,
            [vote(kind, on_site), vote(kind, on_site, model="m2", confidence=0.9 if certain else 0.5)],
        )

    rows = [
        (venues[0], labels[1], result(1, "deposito", True)),
        (venues[1], labels[2], result(2, "restaurante", True)),
        (venues[2], labels[3], result(3, "outro", False)),
        (venues[3], labels[4], result(4, "outro", False, certain=False)),
    ]
    scored = classify_probe.score(rows)
    assert (scored["n"], scored["certain"], scored["right"]) == (4, 3, 2)
    assert scored["models"]["m1"] == (3, 4)
    text = classify_probe.report(rows, missing, [])
    assert "**Gate (owner-labelled, certain answers): 66.7 %**" in text
    assert "keep `suggest`" in text
    assert "- WRONG · Bar Bom (venue 3, review): expected bar, got outro/take away" in text
    assert "- uncertain · Mercadinho" in text


# Shop-level prices (owner, 12 Oct 2026) ---------------------------------------------------------------------

#: "Bar Altas Horas", officially "Depósito Altas Horas": cans at R$ 4.00, a 473 ml can at R$ 5.00.
ALTAS_HORAS_PRICES = {
    "n": 3,
    "on_site": 0,
    "takeaway": 0,
    "packs": 0,
    "median_ppn_minor": 1143,
    "samples": ["Amstel lata_350 R$ 4.00", "Brahma lata_350 R$ 4.00", "Brahma lata_473 R$ 5.00"],
    "by_type": [{"type": "lata", "n": 3, "median_ppn_minor": 1143}],
}
CITY = {"bar": 2500, "shop": 1200, "types": {"lata": {"bar": 2571, "shop": 1150}, "garrafa": {"bar": 2500}}}


def test_cans_at_shop_prices_are_flagged_and_shown_with_their_ratios() -> None:
    e = evidence(prices=ALTAS_HORAS_PRICES, medians=CITY)
    assert e.shop_level_prices()
    text = "\n".join(evidence_lines(e))
    assert "cans (3) R$ 11.43/L = 0.44x the bar median, 0.99x the shop median" in text
    assert "Shop-level prices: YES (too low for a bar" in text
    assert "Prices are strong evidence" in build_prompt(e)


def test_shop_level_needs_two_low_prices_and_most_of_them() -> None:
    one = {"n": 1, "by_type": [{"type": "lata", "n": 1, "median_ppn_minor": 1000}]}
    assert not evidence(prices=one, medians=CITY).shop_level_prices()
    # 0.7x the bar median counts when no shop median is known for the type.
    bottles = {"n": 2, "by_type": [{"type": "garrafa", "n": 2, "median_ppn_minor": 1700}]}
    assert evidence(prices=bottles, medians=CITY).shop_level_prices()
    mixed = {
        "n": 6,
        "by_type": [
            {"type": "lata", "n": 2, "median_ppn_minor": 1000},
            {"type": "garrafa", "n": 4, "median_ppn_minor": 2600},
        ],
    }
    assert not evidence(prices=mixed, medians=CITY).shop_level_prices()
    assert "Shop-level prices: no" in "\n".join(evidence_lines(evidence(prices=mixed, medians=CITY)))
    assert not evidence(prices=ALTAS_HORAS_PRICES, medians={"bar": 2500}).shop_level_prices()


def test_shop_prices_alone_dont_overrule_a_registered_bar_unless_both_models_are_very_sure() -> None:
    bar_register = company(cnae=5611204, trade_name="BAR X")
    e = evidence(company=bar_register, prices=ALTAS_HORAS_PRICES, medians=CITY)
    a, b = FakeVoter("g", [answer("deposito", True, 0.85)]), FakeVoter("w", [answer("deposito", True, 0.85)])
    result = classifier(a, b).classify(e)
    assert not result.certain and result.reasons[0].startswith("registered as a bar or restaurant")
    a, b = FakeVoter("g", [answer("deposito", True, 0.95)]), FakeVoter("w", [answer("deposito", True, 0.92)])
    assert (
        classifier(a, b)
        .classify(evidence(company=bar_register, prices=ALTAS_HORAS_PRICES, medians=CITY))
        .certain
    )
    # A drinks-retail register with shop prices is a depósito at the usual threshold.
    a, b = FakeVoter("g", [answer("deposito", True, 0.85)]), FakeVoter("w", [answer("deposito", True, 0.85)])
    assert (
        classifier(a, b)
        .classify(evidence(company=company(), prices=ALTAS_HORAS_PRICES, medians=CITY))
        .certain
    )


def test_altas_horas_is_in_the_owner_labelled_eval_set() -> None:
    labels = {name: (cnpj, expected) for name, cnpj, expected, _ in classify_probe.OWNER_LABELS}
    assert labels["Bar Altas Horas"] == ("33894100000168", "deposito")


# The register's retail rule (owner, 14 Oct 2026) ------------------------------------------------------------


def retail(cnae: int, trade_name: str, secondary: tuple[int, ...] = ()) -> Company:
    return company(cnae=cnae, trade_name=trade_name, legal_name=None, secondary=secondary)


#: Cans at 1.3x the city's shop median (0.58x the bars'): shop-level prices.
SHOP_PRICED = {"n": 2, "by_type": [{"type": "lata", "n": 2, "median_ppn_minor": 1500}]}


def test_a_registered_minimarket_is_a_store_even_when_both_models_say_on_site() -> None:
    """A miss of the 14 Oct probe (venue 2545): a minimarket with a snack bar; both models said on site."""
    e = evidence(
        venue_id=2545,
        name="Mercadinho e Lanchonete Cantinho da Economia",
        company=retail(4712100, "MERCADINHO E LANCHONETE CANTINHO DA ECONOMIA", (5611203,)),
        prices=SHOP_PRICED,
        medians=CITY,
    )
    assert e.shop_level_prices() and not e.bar_level_prices()
    a, b = FakeVoter("g", [answer("outro", True, 0.85)]), FakeVoter("w", [answer("outro", True, 0.85)])
    result = classifier(a, b).classify(e)
    assert (result.kind, result.on_site, result.certain, result.category) == ("outro", False, True, "mercado")
    assert result.confidence == 1.0
    assert result.reasons[:2] == [RETAIL_REASON, "main activity 4712-1/00: a store"]
    # The votes stay as they were: the record, and each model's own accuracy in the evaluation.
    assert [(vote.kind, vote.on_site) for vote in result.votes] == [("outro", True), ("outro", True)]
    assert "cnpj" in result.to_json()["evidence"]


def test_a_convenience_store_the_models_split_on_is_take_away_without_asking_google() -> None:
    """A miss of the 14 Oct probe (venue 2518): one model said on site, the other take away."""
    e = evidence(
        venue_id=2518,
        name="Conveniencia 29 de Marco",
        company=retail(4729602, "CONVENIENCIA 29"),
        prices=SHOP_PRICED,
        medians=CITY,
    )
    a, b = FakeVoter("g", [answer("outro", True, 0.9)]), FakeVoter("w", [answer("outro", False, 0.95)])
    places = FakePlaces(HIT)
    run = classifier(a, b, places)
    result = run.classify(e)
    assert (result.kind, result.on_site, result.certain) == ("outro", False, True)
    assert result.reasons[:3] == [
        RETAIL_REASON,
        "main activity 4729-6/02: a store",
        "the models disagree: g says outro (on site), w says outro (take away)",
    ]
    # The register settles it: no Google Places lookup and no second round of votes.
    assert places.calls == 0 and run.calls == 2 and result.google_place_id is None


def test_a_registered_bar_with_retail_secondary_activities_is_left_to_the_votes() -> None:
    bar = company(
        cnae=5611204, trade_name="BAR DO ZE", legal_name=None, secondary=(4723700, 4712100, 4729602)
    )
    e = evidence(name="Bar do Zé", company=bar)
    a, b = FakeVoter("g", [answer("bar", True, 0.9)]), FakeVoter("w", [answer("boteco", True, 0.92)])
    result = classifier(a, b).classify(e)
    assert (result.category, result.on_site, result.certain) == ("bar", True, True)
    assert RETAIL_REASON not in result.reasons
    # A split stays a split: the rule never makes a store of a registered bar.
    a, b = FakeVoter("g", [answer("bar", True, 0.9)]), FakeVoter("w", [answer("outro", False, 0.9)])
    split = classifier(a, b).classify(evidence(name="Bar do Zé", company=bar))
    assert not split.certain and RETAIL_REASON not in split.reasons
    # Restaurants, bars, snack bars (5611-2/01 to /05) are never retail, whatever their secondary activities.
    for code in (5611201, 5611202, 5611203, 5611204, 5611205):
        assert retail_verdict(evidence(company=company(cnae=code, secondary=(4723700, 4712100)))) is None


def test_drinks_retail_is_a_depósito_whatever_the_models_say() -> None:
    e = evidence(name="Bar Altas Horas", company=retail(4723700, "ALTAS HORAS"))
    a, b = FakeVoter("g", [answer("bar", True, 0.95)]), FakeVoter("w", [answer("bar", True, 0.95)])
    result = classifier(a, b).classify(e)
    assert (result.kind, result.on_site, result.certain) == ("deposito", False, True)
    assert result.reasons[:2] == [RETAIL_REASON, "main activity 4723-7/00: drinks retail, a depósito"]


def test_the_retail_rule_tells_a_depósito_by_its_name_but_never_a_supermarket() -> None:
    def kind(cnae: int | None, name: str, trade_name: str | None = None) -> str | None:
        # The legal name (company()'s) says "COMERCIO DE BEBIDAS", and drinks retail is a secondary activity.
        register = company(cnae=cnae, trade_name=trade_name, secondary=(4723700,))
        found = retail_verdict(evidence(name=name, company=register))
        return found[0] if found else None

    assert kind(4729602, "Conveniência e Depósito Boa Sorte") == "deposito"
    assert kind(4712100, "Mercadinho do Zé", "DISTRIBUIDORA DO ZE") == "deposito"
    # Assaí's company is "Sendas Distribuidora": a hypermarket is a store whatever its name says.
    assert kind(4711301, "Sendas Distribuidora") == "outro"
    # Neither the legal name ("… COMERCIO DE BEBIDAS LTDA") nor a drinks-retail secondary activity counts.
    assert kind(4712100, "Mercadinho Bom Preço", "MERCADINHO BOM PRECO") == "outro"
    assert kind(4721102, "Padaria Pão Quente", "PADARIA PAO QUENTE") == "outro"
    # Wholesale (division 46), no register, no main activity: the votes decide.
    assert kind(4635402, "Distribuidora de Bebidas X") is None
    assert kind(None, "Loja") is None and retail_verdict(evidence()) is None


#: O Baiano Churrasqueiro (owner, 11 Oct 2026): a butcher's selling Heineken 600 ml at R$ 19.90 at its tables,
#: which Preço da Hora lists as a restaurant. Bottles at 2.65x the city's shop median.
BAIANO_PRICES = {"n": 2, "by_type": [{"type": "garrafa", "n": 2, "median_ppn_minor": 3317}]}
BOTTLE_CITY = {
    "bar": 2500,
    "shop": 1200,
    "types": {"garrafa": {"bar": 2600, "shop": 1250}, "lata": {"bar": 2571, "shop": 1150}},
}


def test_a_shop_at_bar_prices_is_left_to_the_votes_as_preco_da_hora_lists_it_as_a_restaurant() -> None:
    butcher = retail(4722901, "O BAIANO CHURRASQUEIRO")
    e = evidence(name="O Baiano Churrasqueiro", company=butcher, prices=BAIANO_PRICES, medians=BOTTLE_CITY)
    assert e.bar_level_prices() and retail_verdict(e) is None
    a = FakeVoter("g", [answer("restaurante", True, 0.9)])
    b = FakeVoter("w", [answer("restaurante", True, 0.85)])
    result = classifier(a, b).classify(e)
    assert (result.kind, result.on_site, result.certain) == ("restaurante", True, True)
    assert RETAIL_REASON not in result.reasons
    # Shop-level prices, no prices, or none comparable with a shop median: the rule applies.
    garrafa_bars_only = {"bar": 2500, "types": {"garrafa": {"bar": 2600}}}
    for prices, medians in ((SHOP_PRICED, CITY), (None, None), (BAIANO_PRICES, garrafa_bars_only)):
        plain = evidence(name="O Baiano Churrasqueiro", company=butcher, prices=prices, medians=medians)
        assert retail_verdict(plain) == ("outro", "main activity 4722-9/01: a store")
    # Exactly twice the shop median counts; most of the prices must be that dear.
    twice = {"n": 1, "by_type": [{"type": "garrafa", "n": 1, "median_ppn_minor": 2500}]}
    assert evidence(prices=twice, medians=BOTTLE_CITY).bar_level_prices()
    mixed = {
        "n": 6,
        "by_type": [
            {"type": "garrafa", "n": 2, "median_ppn_minor": 3317},
            {"type": "lata", "n": 4, "median_ppn_minor": 1200},
        ],
    }
    assert not evidence(prices=mixed, medians=BOTTLE_CITY).bar_level_prices()
    # Never where Preço da Hora's rule doesn't reach: a supermarket's or a store's own prices, a depósito's,
    # and the dear beer of fuel stations, convenience stores and other food retail, still to take away.
    for cnae, name in (
        (4712100, "Mercadinho Bom Preço"),
        (4729602, "Conveniência Boa Sorte"),
        # Convenience stores (owner, 12 Oct 2026): by their activity, whatever the name, or by a plural name.
        (4729602, "Ponto Gelado"),
        (4789099, "Conveniências Boa Sorte"),
        (4723700, "Ponto Gelado"),
        (4731800, "Posto Bela Vista"),
        (4729699, "Ponto do Gelo"),
    ):
        priced = evidence(name=name, company=retail(cnae, name), prices=BAIANO_PRICES, medians=BOTTLE_CITY)
        assert retail_verdict(priced) is not None, name


@pytest.mark.usefixtures("adapter_env")
def test_the_adapter_sends_the_retail_verdict_but_never_over_an_owner_decision(
    polite: Callable[..., PoliteClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    from cb_scrapers.adapters import venue_classify

    monkeypatch.setattr(
        venue_classify, "load_review", lambda: {"33333333000133": ReviewRow("33333333000133", "keep")}
    )
    items = [
        candidate(1, name="Mercadinho Util", cnpj="11111111000111"),
        candidate(2, name="Mercadinho Travado", cnpj="22222222000122", locked=True),
        candidate(3, name="Mercadinho Mantido", cnpj="33333333000133"),
    ]
    api = FakeApi("apply", items)
    # Both models call it a bar: a certain answer the register overrules.
    adapter = make_adapter(
        polite, api, [FakeVoter("g", [answer("bar", True)]), FakeVoter("w", [answer("bar", True)])]
    )
    adapter.evidence.register = FakeRegister(  # type: ignore[assignment]
        {
            cnpj: retail(4712100, "MERCADINHO")
            for cnpj in ("11111111000111", "22222222000122", "33333333000133")
        }
    )
    run(adapter)
    sent = [item for path, body in api.requests if path.endswith("/results") for item in body["items"]]
    assert [item["venue_id"] for item in sent] == [1]
    assert (sent[0]["kind"], sent[0]["on_site"], sent[0]["certain"]) == ("outro", False, True)
    assert sent[0]["reasons"][0] == RETAIL_REASON
    # What the API's strict contract takes.
    ClassifierResults.model_validate({"items": sent})
    assert adapter.counts["skipped: owner decided"] == 1
    assert adapter.counts["skipped: in the review list"] == 1


def test_the_evaluation_counts_the_answers_the_retail_rule_settled() -> None:
    assert classify_probe.CNAE_LABELS[4723700] == ("deposito", frozenset({"deposito"}))
    venue = candidate(2545, name="Mercadinho e Lanchonete Cantinho da Economia")
    label = classify_probe.Label("cnae", "mercado", frozenset({"outro"}))
    voted = combine(2545, [vote("outro", True, 0.85), vote("outro", True, 0.85, model="m2")])
    assert voted.certain and not classify_probe.right(label, voted.kind, voted.on_site)
    settled = settle_retail(voted, "outro", "main activity 4712-1/00: a store")
    text = classify_probe.report([(venue, label, settled)], [], [])
    assert "Retail register rule (main activity in CNAE division 47: never on site): 1 of 1 answers" in text
    assert "## Misses and uncertain answers (0)" in text
    # Each model alone still shows the votes' miss.
    assert "m1 0/1, m2 0/1" in text
