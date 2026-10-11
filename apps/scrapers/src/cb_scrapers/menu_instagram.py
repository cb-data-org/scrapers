"""Beer prices in Pituba's bars' Instagram posts: a one-off proof of concept (owner, 11 Oct 2026), run by hand
from the public mirror's GitHub Actions. Nothing is sent anywhere.

    CB_META_TOKEN=… CB_IG_USER_ID=… uv run python -m cb_scrapers.menu_instagram \\
        --overture places.geojsonseq --out DIR

Instagram's official Graph API only (Business Discovery, through the brand's own app, Page and professional
account): public posts of public business and creator accounts. No other way of reading Instagram.

1. The handles: Pituba's venues on our map (their `instagram`) and the bars and restaurants Overture lists
   around Pituba with an Instagram link.
2. Each account's latest posts (Business Discovery), kept only when at most MAX_AGE_DAYS old.
3. Beer prices from each post's caption, and from its images by Tesseract OCR (Portuguese), read by the
   receipts' serving parser; compared with what the site already lists for that bar.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .adapters.osm_venues import instagram_handle
from .core.catalog import committed_catalog
from .core.products import ServingReader
from .matching import Candidate, best_match
from .menu_poc import BOX, bars, ocr, prices

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"
SITE = "https://cervejabarata.com.br"
MAX_AGE_DAYS = 180
POSTS = 25
#: Images read by OCR per account (most recent first), to keep the run short.
IMAGES = 8
FIELDS = (
    "business_discovery.username({handle}){{username,name,followers_count,media_count,"
    "media.limit({posts}){{timestamp,caption,media_type,media_url,permalink,children{{media_url,media_type}}}}}}"
)


def our_pituba(client: httpx.Client) -> tuple[list[Candidate], dict[int, list[dict[str, Any]]]]:
    venues = client.get(f"{SITE}/api/v1/salvador/venues-all.json").json()["items"]
    deals = client.get(f"{SITE}/api/v1/salvador/deals.json").json()["items"]
    by_venue: dict[int, list[dict[str, Any]]] = {}
    for deal in deals:
        by_venue.setdefault(int(deal["venue_id"]), []).append(deal)
    south, west, north, east = BOX
    near = [v for v in venues if south <= v["lat"] <= north and west <= v["lng"] <= east]
    return [Candidate(v["name"], v["lat"], v["lng"], v) for v in near], by_venue


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1])
    overture = Path(args[args.index("--overture") + 1])
    out.mkdir(parents=True, exist_ok=True)
    token, ig_user = os.environ["CB_META_TOKEN"], os.environ["CB_IG_USER_ID"]
    reader = ServingReader(committed_catalog())
    now = datetime.now(UTC)
    lines = ["# Beer prices in Pituba's bars' Instagram posts (proof of concept)", ""]
    with httpx.Client(
        timeout=40, follow_redirects=True, headers={"User-Agent": "CervejaBarataBot/1.0"}
    ) as client:
        ours, our_deals = our_pituba(client)
        handles: dict[str, dict[str, Any]] = {}
        for venue in ours:
            handle = venue.data.get("instagram")
            if handle:
                handles.setdefault(handle.lower(), {"name": venue.name, "lat": venue.lat, "lng": venue.lng})
        for bar in bars(overture):
            handle = instagram_handle({"instagram": bar["instagram"]}) if bar.get("instagram") else None
            if handle:
                handles.setdefault(handle, {"name": bar["name"], "lat": bar["lat"], "lng": bar["lng"]})
        totals = {"accounts": 0, "found": 0, "recent posts": 0, "caption prices": 0, "image prices": 0}
        totals |= {"equal": 0, "different": 0}
        details: list[str] = []
        for handle, place in sorted(handles.items()):
            totals["accounts"] += 1
            fields = FIELDS.format(handle=handle, posts=POSTS)
            response = client.get(f"{GRAPH}/{ig_user}", params={"fields": fields, "access_token": token})
            time.sleep(1.0)
            account = response.json().get("business_discovery") if response.status_code == 200 else None
            if not account:
                error = response.json().get("error", {}).get("message", response.status_code)
                details.append(f"- @{handle} ({place['name']}): not readable ({str(error)[:60]})")
                continue
            totals["found"] += 1
            posts = []
            for post in (account.get("media") or {}).get("data", []):
                when = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
                if (now - when).days <= MAX_AGE_DAYS:
                    posts.append((when, post))
            totals["recent posts"] += len(posts)
            found: list[dict[str, Any]] = []
            images = 0
            for when, post in posts:
                for item in prices(post.get("caption") or "", reader):
                    found.append({**item, "where": "caption", "when": when.date().isoformat()})
                    totals["caption prices"] += 1
                urls = [post.get("media_url")] if post.get("media_type") == "IMAGE" else []
                urls += [
                    c.get("media_url")
                    for c in (post.get("children") or {}).get("data", [])
                    if c.get("media_type") == "IMAGE"
                ]
                for url in [u for u in urls if u]:
                    if images >= IMAGES:
                        break
                    images += 1
                    try:
                        body = client.get(url).content
                    except httpx.HTTPError:
                        continue
                    with tempfile.NamedTemporaryFile(suffix=".jpg") as file:
                        file.write(body)
                        file.flush()
                        for item in prices(ocr(Path(file.name)), reader):
                            found.append({**item, "where": "image", "when": when.date().isoformat()})
                            totals["image prices"] += 1
            match = best_match(
                [place["name"], account.get("name") or ""], place["lat"], place["lng"], ours, max_m=200
            )
            listed = our_deals.get(int(match.data["id"]), []) if match else []
            for beer in found:
                same = [d for d in listed if d["format_code"] == beer["format"]]
                if same:
                    equal = any(abs(d["price_minor"] / 100 - beer["price"]) < 0.01 for d in same)
                    totals["equal" if equal else "different"] += 1
            details.append(
                f"- @{handle} {account.get('name')} ({account.get('followers_count')} followers): "
                f"{len(posts)} posts in {MAX_AGE_DAYS} days, {images} images read, {len(found)} beer prices"
                + (f"; on our map as {match.name} with {len(listed)} prices" if match else "")
            )
            for beer in found[:6]:
                details.append(
                    f"    - {beer['when']} {beer['where']}: `{beer['line']}` → {beer['format']} "
                    f"R$ {beer['price']:.2f}"
                )
        lines.append(
            f"{totals['accounts']} Instagram handles for Pituba's bars and restaurants; "
            f"{totals['found']} readable (public business or creator accounts); "
            f"{totals['recent posts']} posts in the last {MAX_AGE_DAYS} days; beer prices: "
            f"{totals['caption prices']} in captions, {totals['image prices']} in images; "
            f"against our listed prices: {totals['equal']} equal, {totals['different']} different."
        )
        lines += ["", *details]
    text = "\n".join(lines)
    print(text)
    (out / "report.md").write_text(text + "\n")
    (out / "handles.json").write_text(json.dumps(sorted(handles), indent=0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
