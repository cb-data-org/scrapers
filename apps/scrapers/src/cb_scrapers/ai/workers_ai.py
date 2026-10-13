"""Workers AI over REST: one menu or flyer image in, beer deal drafts out (#45).

The model is the plan's free vision model. A request asks for every beer price in the image as a list of
drafts in the contract's deal_draft shape, first with `response_format` set to the JSON schema and, when the
model or the API won't do that, with the schema in the prompt instead. Every draft is validated with the
generated `DealDraft` model; invalid ones are reported, never fixed silently.

Neurons are what the free plan counts (10,000 a day, shared with the Worker): Cloudflare prices this model at
$0.10 per million input tokens and $0.30 per million output tokens, and a neuron costs $0.011 / 1,000. The
client adds up what its calls spent in `spent`; an adapter that holds it as `ai` gets that total on its run
report (`ai_neurons`), and the 09:00 digest counts it with the Worker's own (#213).
"""

import base64
import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import ValidationError

from ..models import DealDraft

MODEL = "@cf/google/gemma-4-26b-a4b-it"
API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
SCHEMA_FILE = Path(__file__).resolve().parents[5] / "packages" / "contracts" / "deal_draft.schema.json"
#: USD per million tokens (developers.cloudflare.com, October 2026) and USD per neuron.
INPUT_USD_PER_M, OUTPUT_USD_PER_M, USD_PER_NEURON = 0.10, 0.30, 0.011 / 1000

Mode = Literal["json_schema", "json_prompt"]

PROMPT = """You read photos and screenshots of Brazilian bar menus and drink promotions (Salvador, Bahia).
List every BEER price you can see as a draft: one draft per beer, format, size and price, e.g.
"Heineken long neck R$ 14,90". A beer sold in several sizes (columns such as 300ml and 500ml) is one
draft per size, each with its volume_ml. A line naming several beers at one price
("AMSTEL E ORIGINAL R$ 14,90", "Skol/Brahma") is one draft per beer at that price. A size in a section's
heading ("CERVEJAS 600ML") applies to every beer under it. Write a beer's usual name when the menu misspells
it ("Stela" is Stella Artois). Skip lines that are crossed out or marked as sold out. Ignore food, soft
drinks, water, juices, spirits, wine and cocktails. Alcohol-free beers are beers too: keep "0.0", "Zero" or
"sem álcool" in the beer's name ("Heineken 0.0"). If there is no beer price, return no drafts.

For each draft:
- beer: the beer as written (e.g. "Brahma Duplo Malte"); brand: just the brand ("Brahma") when clear,
  else null.
- format: lata (can), long_neck (small bottle), garrafa (600 ml or 1 l bottle), litrao (1 l returnable),
  chopp (draught: copo, tulipa, caneca, pint), torre (beer tower) or other.
  volume_ml when written (e.g. 350, 600), else null.
- price: the number in reais (14.90 for "R$ 14,90"); currency "BRL".
- units: how many beers the price buys (a bucket of 5 is units 5); packaging: single, balde, torre, combo,
  or dobro ("em dobro", "2 por 1": units 2).
- kind: regular, happy_hour, promo, combo or open_bar; open_bar_minutes only for open bars.
- schedules: when a price only applies on some days or hours: days 0 = Sunday … 6 = Saturday, start and end
  as HH:MM (end earlier than start when it runs past midnight). Empty when it always applies.
- venue_name: the bar's name if the image shows it, else null. venue_ref, valid_from, valid_until,
  observed_on, conditions, service_charge_pct ("+10%" is 10) and couvert: null unless written.
- confidence: 0 to 1, how sure you are of this draft; notes: anything unclear, else null."""

JSON_ONLY = """

Answer with JSON only, no prose and no code fences, matching this JSON schema:
"""


def neurons(prompt_tokens: int, completion_tokens: int) -> float:
    usd = prompt_tokens * INPUT_USD_PER_M / 1e6 + completion_tokens * OUTPUT_USD_PER_M / 1e6
    return usd / USD_PER_NEURON


@cache
def drafts_schema() -> dict[str, Any]:
    """`{"drafts": [deal_draft, …]}`: a menu lists many prices, and the contract describes one."""
    item = {
        key: value
        for key, value in json.loads(SCHEMA_FILE.read_text("utf-8")).items()
        if key not in ("$schema", "$id", "title")
    }
    return {
        "type": "object",
        "properties": {"drafts": {"type": "array", "items": item}},
        "required": ["drafts"],
        "additionalProperties": False,
    }


@dataclass
class Extraction:
    mode: Mode
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None
    drafts: list[DealDraft] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    reasoning: str = ""

    @property
    def neurons(self) -> float:
        return neurons(self.prompt_tokens, self.completion_tokens)


@dataclass
class TextReply:
    """A text-only completion (the venue classifier's votes): the reply, or why there is none."""

    model: str
    text: str = ""
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def neurons(self) -> float:
        return neurons(self.prompt_tokens, self.completion_tokens)


def _json_text(text: str) -> str:
    """The JSON in a reply: without code fences or prose around it."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = fenced.group(1) if fenced else text
    start = min((index for index in (body.find("{"), body.find("[")) if index >= 0), default=-1)
    return body[start:] if start >= 0 else body


def parse_drafts(text: str) -> tuple[list[DealDraft], list[str]]:
    """Valid drafts and the problems with the rest (or with the whole reply)."""
    try:
        data = json.loads(_json_text(text))
    except json.JSONDecodeError as error:
        return [], [f"not JSON: {error.msg}"]
    items = data.get("drafts") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [], ["no drafts list"]
    drafts, invalid = [], []
    for index, item in enumerate(items):
        try:
            drafts.append(DealDraft.model_validate(item))
        except ValidationError as error:
            fields = ", ".join(".".join(str(part) for part in issue["loc"]) for issue in error.errors()[:4])
            invalid.append(f"draft {index + 1}: {fields}")
    return drafts, invalid


class WorkersAI:
    def __init__(self, account: str, token: str, client: httpx.Client, model: str = MODEL) -> None:
        self.model = model
        self.url = API.format(account=account, model=model)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = client
        #: Neurons this client's calls spent so far (failed calls cost nothing).
        self.spent = 0.0

    def extract(self, image: bytes, media_type: str, mode: Mode = "json_schema") -> Extraction:
        prompt = PROMPT if mode == "json_schema" else PROMPT + JSON_ONLY + json.dumps(drafts_schema())
        data_url = f"data:{media_type};base64,{base64.b64encode(image).decode()}"
        body: dict[str, Any] = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_completion_tokens": 4000,
            # Gemma 4 reasons before answering by default, and the reasoning alone used the whole budget.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if mode == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": drafts_schema()}
        try:
            response = self.client.post(self.url, json=body, headers=self.headers, timeout=120.0)
        except httpx.HTTPError as error:
            return Extraction(mode, "", error=f"{type(error).__name__}: {error}")
        try:
            envelope = response.json()
        except ValueError:
            envelope = {}
        if response.status_code >= 400 or not envelope.get("success", False):
            errors = (
                "; ".join(str(e.get("message", e)) for e in envelope.get("errors", [])) or response.text[:300]
            )
            return Extraction(mode, "", error=f"HTTP {response.status_code}: {errors}")
        result = envelope.get("result") or {}
        # Older models answer in `response`; this one answers like chat completions, in choices[0].message.
        message = (result.get("choices") or [{}])[0].get("message") or {}
        answer = result.get("response") or message.get("content")
        text = answer if isinstance(answer, str) else json.dumps(answer) if answer is not None else ""
        usage = result.get("usage") or {}
        extraction = Extraction(
            mode, text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        )
        self.spent += extraction.neurons
        extraction.reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "")
        extraction.drafts, extraction.invalid = parse_drafts(text)
        return extraction

    def complete(self, prompt: str, schema: dict[str, Any] | None = None, max_tokens: int = 800) -> TextReply:
        """One text prompt in, the model's reply out: JSON by `schema` (response_format) when given."""
        body: dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_completion_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": schema}
        try:
            response = self.client.post(self.url, json=body, headers=self.headers, timeout=120.0)
        except httpx.HTTPError as error:
            return TextReply(self.model, error=f"{type(error).__name__}: {error}")
        try:
            envelope = response.json()
        except ValueError:
            envelope = {}
        if response.status_code >= 400 or not envelope.get("success", False):
            errors = "; ".join(str(e.get("message", e)) for e in envelope.get("errors", []))
            return TextReply(self.model, error=f"HTTP {response.status_code}: {errors or 'no answer'}"[:300])
        result = envelope.get("result") or {}
        message = (result.get("choices") or [{}])[0].get("message") or {}
        answer = result.get("response") or message.get("content")
        text = answer if isinstance(answer, str) else json.dumps(answer) if answer is not None else ""
        usage = result.get("usage") or {}
        reply = TextReply(
            self.model,
            text,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )
        self.spent += reply.neurons
        return reply
