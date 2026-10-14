"""Gemini over REST: an Instagram post (its caption and images) in, beer deal drafts out (#101).

The same prompt, schema and validation as the Workers AI client (workers_ai.py), with what an Instagram post
adds: the date it was posted, so "hoje", "nesta quinta" or "o dia todo" become dates and days. Gemini's free
tier (owner's key, 11 Oct 2026) only ever sees public content: bars' public posts. JSON comes from the
response MIME type plus the schema in the prompt; every draft is validated with the generated `DealDraft`.

When Gemini is overloaded ("high demand", 503) or the day's quota is spent (429), each call costs a minute or
more of retries for nothing: after UNAVAILABLE_AFTER such calls in a row the client stops asking for the rest
of the run (`unavailable`), so a bad afternoon can't hold the scrapers' job past its time limit (14 Oct 2026).
"""

import base64
import json
import time
from datetime import date
from typing import Any

import httpx

from .workers_ai import JSON_ONLY, PROMPT, Extraction, TextReply, drafts_schema, parse_drafts

MODEL = "gemini-3.1-flash-lite"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
#: The free tier allows 15 requests a minute: a little over 4 s apart.
PAUSE_S = 4.5
#: "Experiencing high demand" (503) and other server errors pass: wait and ask again, twice. A 429 is the
#: daily quota and is not retried.
RETRY_WAITS_S = (15, 45)
#: Calls in a row that end overloaded, out of quota or unanswered (each after its retries) before the client
#: gives up for the rest of the run.
UNAVAILABLE_AFTER = 2
UNAVAILABLE = "Gemini is unavailable for the rest of the run (overloaded or out of quota)"

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
        #: Calls in a row that ended overloaded (5xx), out of quota (429) or without an answer.
        self.failures = 0

    @property
    def unavailable(self) -> bool:
        """Gemini gave up on us for this run: every call now returns an error without asking."""
        return self.failures >= UNAVAILABLE_AFTER

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
        data, error = self.ask(
            {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                    "maxOutputTokens": 4000,
                },
            }
        )
        if error:
            return Extraction("json_prompt", "", error=error)
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

    def generate(self, prompt: str, max_tokens: int = 800) -> TextReply:
        """One text prompt in, a JSON reply out (the venue classifier's votes); same pacing and retries."""
        data, error = self.ask(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                    "maxOutputTokens": max_tokens,
                },
            }
        )
        if error:
            return TextReply(self.model, error=error)
        try:
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            return TextReply(self.model, error="no answer (blocked or empty)")
        usage = data.get("usageMetadata") or {}
        return TextReply(
            self.model,
            text,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
        )

    def ask(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """One call, paced and retried: Gemini's JSON answer, or why there is none."""
        if self.unavailable:
            return {}, UNAVAILABLE
        if self.calls:
            time.sleep(PAUSE_S)
        self.calls += 1
        for wait in (*RETRY_WAITS_S, None):
            try:
                response = self.client.post(
                    API.format(model=self.model),
                    json=payload,
                    headers={"x-goog-api-key": self.key},
                    timeout=120,
                )
            except httpx.HTTPError as error:
                # No answer at all (a timeout, a dropped connection): as bad a sign as a 503.
                self.failures += 1
                return {}, f"{type(error).__name__}: {error}"[:300]
            if response.status_code < 500 or wait is None:
                break
            time.sleep(wait)
        status = response.status_code
        self.failures = self.failures + 1 if status == 429 or status >= 500 else 0
        try:
            data = response.json()
        except ValueError as error:
            return {}, f"HTTP {status}: {type(error).__name__}: {error}"[:300]
        if not isinstance(data, dict):
            data = {}
        if status != 200:
            problem = data.get("error")
            message = problem.get("message") if isinstance(problem, dict) else None
            return {}, f"HTTP {status}: {message or status}"[:300]
        return data, None
