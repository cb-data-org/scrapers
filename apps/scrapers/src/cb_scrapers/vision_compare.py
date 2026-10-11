"""Which free vision model reads bars' Instagram flyers and menus best? A one-off comparison (owner, 11
Oct 2026, by Telegram: try Cloudflare's free vision AI on menus, especially Instagram's), run by hand from
the public mirror's GitHub Actions. Nothing is sent anywhere.

    uv run python -m cb_scrapers.vision_compare --out DIR

The images: recent posts (last 60 days) of the bars whose Instagram we know (the site's venues-all),
through Instagram's official Business Discovery; images whose caption mentions beer or prices, or that have
no caption, up to IMAGES. Each image is read by every model with the same deal-draft prompt
(ai/workers_ai.py):

- gemini: Gemini Flash-Lite (free tier; the Instagram source's model);
- gemma: Workers AI's Gemma 4 (free 10,000 neurons a day; the menu-photo page's model);
- llama4: Workers AI's Llama 4 Scout (its fallback).

The report counts, per model, the images with beer prices, the prices, the invalid drafts, the errors and the
Workers AI neurons, and shows each image's prices side by side.
"""

import logging
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from .ai.gemini import Gemini
from .ai.workers_ai import WorkersAI

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
SITE = "https://cervejabarata.com.br"
IMAGES = 25
DAYS = 60
PRICEY = re.compile(r"cerve|chop|beer|heineken|brahma|amstel|stella|r\$|\d+,\d\d|promo|happy|🍺|🍻", re.I)
MODELS = {"gemma": "@cf/google/gemma-4-26b-a4b-it", "llama4": "@cf/meta/llama-4-scout-17b-16e-instruct"}


def images(client: httpx.Client, token: str, ig_user: str) -> list[dict[str, Any]]:
    venues = client.get(f"{SITE}/api/v1/salvador/venues-all.json").json()["items"]
    handles = sorted({(v["instagram"], v["name"]) for v in venues if v.get("instagram")})
    found: list[dict[str, Any]] = []
    for handle, name in handles:
        if len(found) >= IMAGES:
            break
        fields = (
            f"business_discovery.username({handle}){{media.limit(12)"
            "{timestamp,caption,media_type,media_url,permalink,children{media_url,media_type}}}"
        )
        response = client.get(f"{GRAPH}/{ig_user}", params={"fields": fields, "access_token": token})
        media = ((response.json().get("business_discovery") or {}).get("media") or {}).get("data", [])
        for post in media:
            posted = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
            caption = post.get("caption") or ""
            if (datetime.now(UTC) - posted).days > DAYS or (caption and not PRICEY.search(caption)):
                continue
            urls = [post.get("media_url")] if post.get("media_type") == "IMAGE" else []
            urls += [
                c.get("media_url")
                for c in (post.get("children") or {}).get("data", [])
                if c.get("media_type") == "IMAGE"
            ]
            for url in [u for u in urls if u][:2]:
                found.append(
                    {
                        "handle": handle,
                        "name": name,
                        "url": url,
                        "permalink": post.get("permalink"),
                        "posted": posted.date(),
                        "caption": caption,
                    }
                )
                if len(found) >= IMAGES:
                    break
            if len(found) >= IMAGES:
                break
    return found


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1])
    out.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=120, follow_redirects=True)
    gemini = Gemini(os.environ["CB_GEMINI_KEY"], client)
    workers = {
        name: WorkersAI(os.environ["CB_CF_ACCOUNT_ID"], os.environ["CB_CF_AI_TOKEN"], client, model)
        for name, model in MODELS.items()
    }
    picked = images(client, os.environ["CB_META_TOKEN"], os.environ["CB_IG_USER_ID"])
    score = {
        m: {"images with prices": 0, "prices": 0, "invalid": 0, "errors": 0, "neurons": 0.0}
        for m in ("gemini", *MODELS)
    }
    details: list[str] = []
    for item in picked:
        body = client.get(item["url"]).content
        results: dict[str, Any] = {}
        # The image alone, so the models are compared on what they read in it (captions are read separately).
        results["gemini"] = gemini.extract_post(
            "", [(body, "image/jpeg")], item["name"], item["posted"] or date.today()
        )
        for name, model in workers.items():
            results[name] = model.extract(body, "image/jpeg", "json_prompt")
        line = [f"- @{item['handle']} {item['permalink']} ({item['posted']}):"]
        for name, extraction in results.items():
            s = score[name]
            if extraction.error:
                s["errors"] += 1
                line.append(f"    - {name}: error {extraction.error[:80]}")
                continue
            s["prices"] += len(extraction.drafts)
            s["images with prices"] += bool(extraction.drafts)
            s["invalid"] += len(extraction.invalid)
            if name != "gemini":
                s["neurons"] += extraction.neurons
            shown = "; ".join(
                f"{d.beer} {d.format} {d.volume_ml or ''} R$ {d.price:.2f}" for d in extraction.drafts[:4]
            )
            line.append(f"    - {name}: {len(extraction.drafts)} prices {shown}")
        details += line
    lines = [
        "# Free vision models on bars' Instagram images",
        "",
        f"{len(picked)} images from recent posts (last {DAYS} days) that mention beer or prices, "
        "or have no caption.",
        "",
        "| Model | Images with beer prices | Prices | Invalid drafts | Errors | Workers AI neurons |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in score.items():
        neurons = f"{s['neurons']:.0f}" if name != "gemini" else "—"
        row = [name, s["images with prices"], s["prices"], s["invalid"], s["errors"], neurons]
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    text = "\n".join([*lines, "", *details])
    print(text)
    (out / "report.md").write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
