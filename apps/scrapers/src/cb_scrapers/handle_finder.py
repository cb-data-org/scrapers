"""Finding bars' Instagram handles: which method works? A one-off test (owner, 11 Oct 2026), run by
hand from the public mirror's GitHub Actions. Nothing is sent anywhere.

    uv run python -m cb_scrapers.handle_finder --overture places.geojsonseq --out DIR

The answer key: Pituba's places whose handle we already know (our map and Overture), each confirmed by
Instagram's Business Discovery. Each method is then asked for each place by name and place only, and
scored against the key:

- places: Google Places Text Search (New), the place's website; an Instagram link is the answer, an
  ordinary site is read once (its home page, robots.txt obeyed) for a link to Instagram. Only the website
  is used, then dropped (the terms let us keep only Google's place id).
- gemini: Gemini, from what it knows (the free tier has no Google Search grounding).
- guess: usernames made from the name ("zaccariabar", "barzaccaria"…), checked against Instagram.

A wrong answer is also put to the check a real run would make (the account exists and its name is like
the place's), to see whether verification would have caught it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .adapters.osm_venues import instagram_handle
from .core import PoliteClient, normalize
from .matching import similarity
from .menu_instagram import GRAPH, our_pituba
from .menu_poc import bars

log = logging.getLogger(__name__)

PLACES = "https://places.googleapis.com/v1/places:searchText"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
IG_LINK = re.compile(r"instagram\.com/([A-Za-z0-9._]{1,30})", re.I)
NOT_HANDLES = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "share"}
STOP = {
    "bar",
    "restaurante",
    "restaurant",
    "the",
    "de",
    "do",
    "da",
    "dos",
    "das",
    "e",
    "o",
    "a",
    "pituba",
    "salvador",
}


class Instagram:
    def __init__(self, client: httpx.Client, token: str, ig_user: str) -> None:
        self.client, self.token, self.ig_user = client, token, ig_user
        self.calls = 0

    def account(self, handle: str) -> dict[str, Any] | None:
        self.calls += 1
        time.sleep(1.0)
        response = self.client.get(
            f"{GRAPH}/{self.ig_user}",
            params={
                "fields": f"business_discovery.username({handle}){{username,name,biography,website}}",
                "access_token": self.token,
            },
        )
        return response.json().get("business_discovery") if response.status_code == 200 else None


def handle_in(text: str) -> str | None:
    for match in IG_LINK.finditer(text or ""):
        handle = match.group(1).lower().rstrip(".")
        if handle not in NOT_HANDLES:
            return handle
    return None


def by_places(
    client: httpx.Client, polite: PoliteClient, key: str, place: dict[str, Any]
) -> tuple[str | None, str]:
    body = {
        "textQuery": f"{place['name']}, Salvador BA",
        "maxResultCount": 1,
        "locationBias": {
            "circle": {"center": {"latitude": place["lat"], "longitude": place["lng"]}, "radius": 800}
        },
    }
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id,places.displayName,places.websiteUri"}
    found = client.post(PLACES, json=body, headers=headers).json().get("places") or []
    website = (found[0].get("websiteUri") if found else None) or ""
    if not website:
        return None, "no website"
    if "instagram.com" in website:
        return handle_in(website), "website is Instagram"
    try:
        return handle_in(polite.get(website).text), "on its site"
    except Exception:
        return None, "site unreadable"


def by_gemini(client: httpx.Client, key: str, place: dict[str, Any]) -> str | None:
    prompt = (
        f"What is the Instagram username of '{place['name']}', a bar or restaurant in Pituba, Salvador, "
        "Bahia, Brazil? Answer only with the username, or NONE if you don't know it."
    )
    time.sleep(5.0)  # the free tier's requests per minute
    response = client.post(
        GEMINI, json={"contents": [{"parts": [{"text": prompt}]}]}, headers={"x-goog-api-key": key}
    )
    try:
        text = "".join(p.get("text", "") for p in response.json()["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, ValueError):
        return None
    handle = text.strip().strip("@").split()[0].lower() if text.strip() else ""
    return (
        None
        if not handle or handle.upper() == "NONE" or not re.fullmatch(r"[a-z0-9._]{1,30}", handle)
        else handle
    )


def guesses(name: str) -> list[str]:
    words = [w for w in normalize(name).split() if w not in STOP and w.isalnum()]
    if not words:
        return []
    joined = "".join(words)
    return list(
        dict.fromkeys(
            [
                joined,
                f"{joined}bar",
                f"bar{joined}",
                f"{joined}ssa",
                f"{joined}salvador",
                ".".join(words),
                "_".join(words),
                f"{joined}oficial",
            ]
        )
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[args.index("--out") + 1])
    overture = Path(args[args.index("--overture") + 1])
    out.mkdir(parents=True, exist_ok=True)
    places_key, gemini_key = os.environ["CB_GOOGLE_PLACES_KEY"], os.environ["CB_GEMINI_KEY"]
    lines = ["# Finding bars' Instagram handles: which method works (Pituba)", ""]
    with (
        httpx.Client(
            timeout=60, follow_redirects=True, headers={"User-Agent": "CervejaBarataBot/1.0"}
        ) as client,
        PoliteClient(delay=4.0, retries=1, timeout=30) as polite,
    ):
        instagram = Instagram(client, os.environ["CB_META_TOKEN"], os.environ["CB_IG_USER_ID"])
        ours, _ = our_pituba(client)
        known: dict[str, dict[str, Any]] = {}
        for venue in ours:
            if venue.data.get("instagram"):
                known.setdefault(
                    venue.data["instagram"].lower(), {"name": venue.name, "lat": venue.lat, "lng": venue.lng}
                )
        for bar in bars(overture):
            handle = instagram_handle({"instagram": bar["instagram"]}) if bar.get("instagram") else None
            if handle:
                known.setdefault(handle, {"name": bar["name"], "lat": bar["lat"], "lng": bar["lng"]})
        key = {handle: place for handle, place in known.items() if instagram.account(handle)}
        lines.append(
            f"Answer key: {len(key)} Pituba places with a handle Instagram confirms (of {len(known)} known)."
        )
        score = {
            m: {"answered": 0, "right": 0, "wrong": 0, "wrong caught": 0}
            for m in ("places", "gemini", "guess")
        }
        notes: list[str] = []
        places_how: dict[str, int] = {}
        for truth, place in sorted(key.items()):
            answers: dict[str, str | None] = {}
            answers["places"], how = by_places(client, polite, places_key, place)
            places_how[how] = places_how.get(how, 0) + 1
            answers["gemini"] = by_gemini(client, gemini_key, place)
            answers["guess"] = truth if truth in guesses(place["name"]) else None
            for method, answer in answers.items():
                if not answer:
                    continue
                score[method]["answered"] += 1
                if answer == truth:
                    score[method]["right"] += 1
                    continue
                score[method]["wrong"] += 1
                account = instagram.account(answer)
                caught = not account or similarity(account.get("name") or "", place["name"]) < 0.5
                score[method]["wrong caught"] += caught
            notes.append(
                f"- {place['name']} → @{truth}: places {answers['places'] or '—'} ({how}), "
                f"gemini {answers['gemini'] or '—'}, guess {'yes' if answers['guess'] else 'no'}"
            )
        lines += [
            "",
            "| Method | Answered | Right | Wrong | Wrong ones verification catches |",
            "|---|---|---|---|---|",
        ]
        for method, s in score.items():
            lines.append(
                f"| {method} | {s['answered']} | {s['right']} | {s['wrong']} | {s['wrong caught']} |"
            )
        lines += [
            "",
            "Places: " + ", ".join(f"{k} {v}" for k, v in sorted(places_how.items(), key=lambda kv: -kv[1])),
            f"Instagram calls: {instagram.calls}.",
            "",
            *notes,
        ]
    text = "\n".join(lines)
    print(text)
    (out / "report.md").write_text(text + "\n")
    (out / "key.json").write_text(json.dumps(sorted(key), indent=0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
