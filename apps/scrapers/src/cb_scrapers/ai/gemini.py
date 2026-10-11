"""Gemini over REST: an Instagram post (its caption and images) in, beer deal drafts out (#101).

The same prompt, schema and validation as the Workers AI client (workers_ai.py), with what an Instagram post
adds: the date it was posted, so "hoje", "nesta quinta" or "o dia todo" become dates and days. Gemini's free
tier (owner's key, 11 Oct 2026) only ever sees public content: bars' public posts. JSON comes from the
response MIME type plus the schema in the prompt; every draft is validated with the generated `DealDraft`.
"""

import base64
import json
import time
from datetime import date
from typing import Any

import httpx

from .workers_ai import JSON_ONLY, PROMPT, Extraction, drafts_schema, parse_drafts

MODEL = "gemini-3.1-flash-lite"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
#: The free tier allows 15 requests a minute: a little over 4 s apart.
PAUSE_S = 4.5

POST = """

This is one Instagram post by the bar "{venue}" (Salvador, Bahia), posted on {posted} ({weekday}).
Its caption and images may announce prices or promotions. Relative dates count from the post's date:
"hoje" is {posted}; a promotion for one day has valid_from and valid_until on that day; "toda quinta" is a
weekly schedule. Set observed_on to {posted} and venue_name to "{venue}".
Caption:
{caption}"""

WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


class Gemini:
    def __init__(self, key: str, client: httpx.Client, model: str = MODEL) -> None:
        self.key, self.client, self.model = key, client, model
        self.calls = 0

    def extract_post(
        self, caption: str, images: list[tuple[bytes, str]], venue: str, posted: date
    ) -> Extraction:
        """Drafts for one post: its caption and up to a few images (bytes, MIME type)."""
        text = (
            PROMPT
            + POST.format(
                venue=venue,
                posted=posted.isoformat(),
                weekday=WEEKDAYS[posted.weekday()],
                caption=caption or "(none)",
            )
            + JSON_ONLY
            + json.dumps(drafts_schema(), ensure_ascii=False)
        )
        parts: list[dict[str, Any]] = [{"text": text}]
        for body, media_type in images:
            parts.append({"inline_data": {"mime_type": media_type, "data": base64.b64encode(body).decode()}})
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "maxOutputTokens": 4000,
            },
        }
        if self.calls:
            time.sleep(PAUSE_S)
        self.calls += 1
        try:
            response = self.client.post(
                API.format(model=self.model), json=payload, headers={"x-goog-api-key": self.key}, timeout=120
            )
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            return Extraction("json_prompt", "", error=f"{type(error).__name__}: {error}")
        if response.status_code != 200:
            message = (data.get("error") or {}).get("message", str(response.status_code))
            return Extraction("json_prompt", "", error=str(message)[:300])
        try:
            reply = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            return Extraction("json_prompt", "", error="no answer (blocked or empty)")
        usage = data.get("usageMetadata") or {}
        extraction = Extraction(
            "json_prompt",
            reply,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
        )
        extraction.drafts, extraction.invalid = parse_drafts(reply)
        return extraction
