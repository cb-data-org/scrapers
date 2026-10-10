"""The vision spike (#45): the free model against real menus whose beer prices a person read first.

    uv run cb spike vision --manifest spikes/vision-menus.json [--report FILE]

The manifest lists public menu images and PDF pages that the menu finder found (#305), each with the beer
prices read on it. The spike fetches each one politely (it is never stored), reads it in overlapping pieces
when it is big (the model works at a fixed resolution), sends each piece to Workers AI, merges the drafts and
scores them:
- schema-valid: the reply parses and at least one draft passes the contract's DealDraft validation;
- found: the share of the expected prices that some draft has (same price, brand in the beer's name, and the
  same format when the manifest gives one);
- neurons: what the free plan counts, from the tokens used.
JSON schema mode goes first; the schema-in-the-prompt mode is the fallback when it errors or doesn't parse.
"""

import io
import json
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from ..core import BlockedError, DisallowedError, PoliteClient
from ..menus import jpeg_bytes, pdf_pages, sniff
from ..models import DealDraft
from .workers_ai import WorkersAI

MAX_SIDE = 2400
#: Images wider or taller than this many pixels are read in two overlapping halves on that side.
TILE_OVER = 900
TILE_SHARE = 0.625
FREE_NEURONS_PER_DAY = 10_000


@dataclass
class Expected:
    brand: str
    price: float
    format: str | None = None


@dataclass
class Menu:
    id: str
    venue: str
    url: str
    expected: list[Expected]
    page: int | None = None
    notes: str = ""


@dataclass
class Result:
    id: str
    venue: str
    mode: str = ""
    valid: bool = False
    drafts: int = 0
    invalid: int = 0
    found: int = 0
    expected: int = 0
    neurons: float = 0.0
    error: str | None = None
    pieces: int = 0
    attempts: list[str] = field(default_factory=list)
    replies: list[dict[str, Any]] = field(default_factory=list)


def load_manifest(path: Path) -> list[Menu]:
    data = json.loads(path.read_text("utf-8"))
    return [
        Menu(
            id=item["id"],
            venue=item["venue"],
            url=item["url"],
            page=item.get("page"),
            notes=item.get("notes", ""),
            expected=[Expected(**expected) for expected in item["expected"]],
        )
        for item in data["menus"]
    ]


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def matches(expected: Expected, draft: DealDraft) -> bool:
    words = _plain(f"{draft.brand or ''} {draft.beer or ''}")
    return (
        abs(draft.price - expected.price) < 0.011
        and _plain(expected.brand) in words
        and (expected.format is None or draft.format == expected.format)
    )


def image_for(client: PoliteClient, menu: Menu) -> Image.Image:
    """The menu as an image: the image itself, or the page of the PDF (at most MAX_SIDE pixels)."""
    body = client.get(menu.url).content
    kind = sniff(body, "")
    if kind == "pdf":
        pages = pdf_pages(body, menu.page or 1)
        if len(pages) < (menu.page or 1):
            raise ValueError(f"the PDF has no page {menu.page}")
        image = pages[(menu.page or 1) - 1]
    elif kind == "image":
        with Image.open(io.BytesIO(body)) as opened:
            image = opened.convert("RGB")
    else:
        raise ValueError(f"not an image or a PDF ({len(body)} bytes)")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def _spans(size: int) -> list[tuple[int, int]]:
    if size <= TILE_OVER:
        return [(0, size)]
    part = round(size * TILE_SHARE)
    return [(0, part), (size - part, size)]


def tiles(image: Image.Image) -> list[Image.Image]:
    """The model reads a fixed-size image, so a wide or tall menu goes in overlapping halves (a dense page in
    quarters). Each half covers 62.5% of the side: the 25% overlap keeps a beer and its price together."""
    width, height = image.size
    return [image.crop((x0, y0, x1, y1)) for y0, y1 in _spans(height) for x0, x1 in _spans(width)]


def _key(draft: DealDraft) -> tuple[str, float, str | None, int | None]:
    return (_plain(draft.beer or draft.brand or ""), round(draft.price, 2), draft.format, draft.volume_ml)


def merge(drafts: list[DealDraft]) -> list[DealDraft]:
    """One draft per beer, price, format and volume: overlapping pieces read the same lines twice."""
    seen: dict[tuple[str, float, str | None, int | None], DealDraft] = {}
    for draft in drafts:
        seen.setdefault(_key(draft), draft)
    return list(seen.values())


def run_spike(client: PoliteClient, ai: WorkersAI, menus: list[Menu]) -> list[Result]:
    results = []
    for menu in menus:
        result = Result(menu.id, menu.venue, expected=len(menu.expected))
        results.append(result)
        try:
            image = image_for(client, menu)
        except httpx.HTTPStatusError as error:
            result.error = f"fetch: HTTP {error.response.status_code}"
            continue
        except (BlockedError, DisallowedError) as error:
            result.error = f"fetch: {type(error).__name__}: {error}"
            continue
        except (httpx.HTTPError, ValueError, OSError) as error:
            result.error = f"fetch: {type(error).__name__}: {error}"
            continue
        drafts: list[DealDraft] = []
        modes: set[str] = set()
        failures = 0
        pieces = tiles(image)
        for number, piece in enumerate(pieces, 1):
            for mode in ("json_schema", "json_prompt"):
                extraction = ai.extract(jpeg_bytes(piece), "image/jpeg", mode)
                result.neurons += extraction.neurons
                outcome = (
                    extraction.error or f"{len(extraction.drafts)} drafts, {len(extraction.invalid)} invalid"
                )
                result.attempts.append(f"{number}/{len(pieces)} {mode}: {outcome}")
                result.replies.append(
                    {
                        "piece": number,
                        "mode": mode,
                        "error": extraction.error,
                        "drafts": [draft.model_dump(mode="json") for draft in extraction.drafts],
                        "invalid": extraction.invalid,
                        "tokens": [extraction.prompt_tokens, extraction.completion_tokens],
                        # The reply's start and end, to see a truncated or looping answer.
                        "text": extraction.text[:3000],
                        "text_end": extraction.text[-800:],
                        "reasoning": extraction.reasoning[:1500],
                    }
                )
                result.invalid += len(extraction.invalid)
                # A parsed reply, even with no drafts, is an answer: only errors and broken JSON fall back.
                parsed = extraction.error is None and not any(
                    problem.startswith(("not JSON", "no drafts list")) for problem in extraction.invalid
                )
                if parsed:
                    drafts += extraction.drafts
                    modes.add(mode)
                    break
            else:
                failures += 1
        merged = merge(drafts)
        result.mode = "+".join(sorted(modes))
        result.drafts = len(merged)
        result.valid = bool(merged) and failures == 0
        result.found = sum(1 for e in menu.expected if any(matches(e, d) for d in merged))
        result.error = f"{failures} of {len(pieces)} pieces unreadable" if failures else None
        result.pieces = len(pieces)
    return results


def summary(results: list[Result]) -> str:
    valid = [r for r in results if r.valid]
    expected = sum(r.expected for r in results)
    found = sum(r.found for r in results)
    spent = [r.neurons for r in results if r.neurons]
    mean = sum(spent) / len(spent) if spent else 0.0
    lines = [
        "| # | venue | mode | valid | drafts | found | neurons | note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        note = (r.error or "; ".join(r.attempts))[:120]
        drafts = f"{r.drafts} (+{r.invalid} invalid)" if r.invalid else str(r.drafts)
        ok = "yes" if r.valid else "no"
        lines.append(
            f"| {r.id} | {r.venue} | {r.mode or '-'} | {ok} | {drafts} | {r.found}/{r.expected}"
            f" | {r.neurons:.0f} | {note} |"
        )
    lines += [
        "",
        f"**Schema-valid drafts: {len(valid)} of {len(results)} menus** (target: 7 of 10).",
        f"**Expected prices found: {found} of {expected}** ({found / expected:.0%})." if expected else "",
        f"**Neurons per extraction:** mean {mean:.0f}, max {max(spent, default=0):.0f}; "
        f"{FREE_NEURONS_PER_DAY:,} free neurons a day ≈ {FREE_NEURONS_PER_DAY / mean:.0f} menus"
        if mean
        else "**Neurons:** none reported.",
        f"**Fallback used:** {sum(1 for r in results if r.mode == 'json_prompt')} of {len(results)}.",
    ]
    return "\n".join(line for line in lines if line != "")


def write_report(path: Path, results: list[Result]) -> None:
    path.write_text(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2), "utf-8")
