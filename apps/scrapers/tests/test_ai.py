import base64
import io
import json
from collections.abc import Callable
from typing import Any

import httpx
import pypdfium2 as pdfium
import pytest
from PIL import Image

from cb_scrapers.ai import WorkersAI, drafts_schema, neurons, parse_drafts
from cb_scrapers.ai.spike import Expected, Menu, merge, run_spike, summary, tiles
from cb_scrapers.core import PoliteClient
from cb_scrapers.models import DealDraft
from tests.fakes import FakeSite

DRAFT: dict[str, Any] = {
    "venue_name": None,
    "venue_ref": None,
    "beer": "Heineken Long Neck",
    "brand": "Heineken",
    "format": "long_neck",
    "volume_ml": 330,
    "units": 1,
    "packaging": "single",
    "price": 14.9,
    "currency": "BRL",
    "kind": "regular",
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


def reply(
    drafts: list[dict[str, Any]] | str, prompt_tokens: int = 1000, completion_tokens: int = 500
) -> dict:
    response = drafts if isinstance(drafts, str) else {"drafts": drafts}
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    return {"success": True, "result": {"response": response, "usage": usage}}


class FakeAI:
    """Workers AI's REST endpoint: answers by mode (response_format present or not) and keeps the bodies."""

    def __init__(self, by_mode: dict[str, tuple[int, dict[str, Any]]]) -> None:
        self.by_mode = by_mode
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        status, payload = self.by_mode["json_schema" if "response_format" in body else "json_prompt"]
        return httpx.Response(status, json=payload)


def workers_ai(fake: FakeAI) -> WorkersAI:
    return WorkersAI("acc", "token", httpx.Client(transport=httpx.MockTransport(fake)))


def test_drafts_schema_wraps_the_contract() -> None:
    schema = drafts_schema()
    item = schema["properties"]["drafts"]["items"]
    assert schema["required"] == ["drafts"]
    assert "price" in item["required"] and "$schema" not in item


def test_parse_drafts_keeps_valid_ones_and_reports_the_rest() -> None:
    text = "```json\n" + json.dumps({"drafts": [DRAFT, {**DRAFT, "price": None}]}) + "\n```"
    drafts, invalid = parse_drafts(text)
    assert [draft.price for draft in drafts] == [14.9]
    assert invalid == ["draft 2: price"]
    assert parse_drafts("Sorry, I can't read it.") == ([], ["not JSON: Expecting value"])


def test_extract_sends_the_image_and_the_schema_and_counts_neurons() -> None:
    fake = FakeAI({"json_schema": (200, reply([DRAFT]))})
    extraction = workers_ai(fake).extract(b"\xff\xd8jpeg", "image/jpeg")
    body = fake.bodies[0]
    content = body["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["response_format"]["json_schema"] == drafts_schema()
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert (extraction.error, len(extraction.drafts)) == (None, 1)
    # (1,000 x $0.10 + 500 x $0.30) per million tokens, at $0.011 per 1,000 neurons.
    assert round(extraction.neurons, 1) == round(neurons(1000, 500), 1) == 22.7


def test_the_client_adds_up_the_neurons_its_calls_spent() -> None:
    fake = FakeAI({"json_schema": (200, reply([DRAFT]))})
    ai = workers_ai(fake)
    ai.extract(b"\xff\xd8jpeg", "image/jpeg")
    ai.extract(b"\xff\xd8jpeg", "image/jpeg")
    assert round(ai.spent, 1) == round(2 * neurons(1000, 500), 1)


def test_extract_reports_api_errors_and_the_prompt_mode_carries_the_schema() -> None:
    fake = FakeAI(
        {
            "json_schema": (
                400,
                {"success": False, "errors": [{"message": "response_format not supported"}]},
            ),
            "json_prompt": (200, reply(json.dumps({"drafts": [DRAFT]}))),
        }
    )
    ai = workers_ai(fake)
    failed = ai.extract(b"jpeg", "image/jpeg", "json_schema")
    assert failed.error == "HTTP 400: response_format not supported"
    prompted = ai.extract(b"jpeg", "image/jpeg", "json_prompt")
    assert "response_format" not in fake.bodies[1]
    assert '"drafts"' in fake.bodies[1]["messages"][0]["content"][0]["text"]
    assert len(prompted.drafts) == 1


def jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "JPEG")
    return buffer.getvalue()


def pdf(pages: int) -> bytes:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(595, 842)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def menus_site() -> FakeSite:
    return FakeSite({"https://bar.test/menu.jpg": jpeg(2400, 3200), "https://bar.test/carta.pdf": pdf(2)})


def test_run_spike_falls_back_scores_and_summarises(
    polite: Callable[..., PoliteClient], menus_site: FakeSite
) -> None:
    fake = FakeAI(
        {
            "json_schema": (200, reply("not json at all")),
            "json_prompt": (
                200,
                reply([DRAFT, {**DRAFT, "brand": "Brahma", "beer": "Brahma", "price": 12.0}]),
            ),
        }
    )
    menus = [
        Menu("01", "Bar Teste", "https://bar.test/menu.jpg", [Expected("Heineken", 14.9, "long_neck")]),
        Menu(
            "02",
            "Bar Teste",
            "https://bar.test/carta.pdf",
            [Expected("brahma", 12.0), Expected("Skol", 9.0)],
            page=2,
        ),
        Menu("03", "Sumiu", "https://bar.test/gone.jpg", [Expected("Skol", 9.0)]),
    ]
    with polite(menus_site) as client:
        results = run_spike(client, workers_ai(fake), menus)
    assert [(r.id, r.mode, r.valid, r.found, r.expected) for r in results] == [
        ("01", "json_prompt", True, 1, 1),
        ("02", "json_prompt", True, 1, 2),
        ("03", "", False, 0, 1),
    ]
    assert results[2].error == "fetch: HTTP 404"
    # Both menus went in four pieces, each read twice (the schema mode failed): 16 calls, and the
    # overlapping pieces' identical drafts merged into two per menu.
    assert len(fake.bodies) == 16
    assert [(r.pieces, r.drafts) for r in results[:2]] == [(4, 2), (4, 2)]
    # The 2400 x 3200 photo was shrunk to 1800 x 2400 and its first quarter is 62.5% of each side.
    sent = fake.bodies[0]["messages"][0]["content"][1]["image_url"]["url"]
    with Image.open(io.BytesIO(base64.b64decode(sent.removeprefix("data:image/jpeg;base64,")))) as image:
        assert image.size == (1125, 1500)
    text = summary(results)
    assert "**Schema-valid drafts: 2 of 3 menus** (target: 7 of 10)." in text
    assert "| 03 | Sumiu | - | no | 0 | 0/1 |" in text
    assert "**Expected prices found: 2 of 4** (50%)." in text
    assert "**Fallback used:** 2 of 3." in text


def test_extract_reads_chat_completion_replies_and_keeps_the_reasoning() -> None:
    message = {"content": json.dumps({"drafts": [DRAFT]}), "reasoning_content": "The menu lists…"}
    payload = {
        "success": True,
        "result": {"choices": [{"message": message}], "usage": {"completion_tokens": 9}},
    }
    extraction = workers_ai(FakeAI({"json_schema": (200, payload)})).extract(b"jpeg", "image/jpeg")
    assert (len(extraction.drafts), extraction.reasoning) == (1, "The menu lists…")


def test_tiles_split_big_images_into_overlapping_halves() -> None:
    assert [tile.size for tile in tiles(Image.new("RGB", (800, 600)))] == [(800, 600)]
    assert [tile.size for tile in tiles(Image.new("RGB", (1920, 900)))] == [(1200, 900), (1200, 900)]
    quarters = tiles(Image.new("RGB", (1190, 1684)))
    assert [tile.size for tile in quarters] == [(744, 1052)] * 4


def test_merge_keeps_one_draft_per_beer_price_format_and_volume() -> None:
    heineken = DealDraft.model_validate(DRAFT)
    same = DealDraft.model_validate({**DRAFT, "beer": "HEINEKEN LONG NECK", "confidence": 0.5})
    bigger = DealDraft.model_validate({**DRAFT, "volume_ml": 600, "format": "garrafa"})
    assert merge([heineken, same, bigger]) == [heineken, bigger]
