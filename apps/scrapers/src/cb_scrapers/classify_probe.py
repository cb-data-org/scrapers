"""How good is the venue classifier? An evaluation against the owner's decisions, run by hand on the public
mirror's GitHub Actions (Source probe, probe=classify) before the classifier may change venues by itself.

    uv run python -m cb_scrapers.classify_probe --out DIR [--overture FILE] [--cnae-sample 40]

Read-only: it asks the admin API for the venues (the ingest token's candidates endpoint) and never sends a
result. The same pipeline as the daily adapter (venue_evidence.py, venue_classifier.py), Google Places
included when CB_GOOGLE_PLACES_KEY is set. The labelled set:

- the review list (data/seed/venue-review.csv): keep → a place to drink (the bar category), hide → not one
  (a store or a depósito); merges are skipped;
- OWNER_LABELS, the owner's own labels of 12 Oct 2026;
- a sample of venues whose main registered activity says plainly what they are (bars, restaurants,
  supermarkets, convenience stores): reported apart, as the register is part of the evidence.

The gate is the owner-labelled groups: at least GATE of the certain answers right. The report (report.md,
results.json) lists accuracy per class, coverage (certain answers), each model alone, and every miss.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .adapters.precodahora import SHOP_BOOK_FILE
from .adapters.venue_classify import voters_from_env
from .cnpj import CnpjRegister, default_cache_file
from .core import PoliteClient, normalize
from .core.cache import cache_dir
from .places import GooglePlaces
from .venue_classifier import THRESHOLD, Result, VenueClassifier, Vote, category
from .venue_evidence import ClassifierApi, EvidenceBuilder, Instagram
from .venues import ShopBook, load_review

log = logging.getLogger(__name__)

GATE = 0.95
BARISH = frozenset({"bar", "boteco", "pub", "barraca", "cervejaria"})
#: The owner's labels (12 Oct 2026): name, CNPJ when known, expected category and kinds.
OWNER_LABELS: tuple[tuple[str, str | None, str, frozenset[str]], ...] = (
    ("Bar Altas Horas", "33894100000168", "deposito", frozenset({"deposito"})),
    ("Depósito Altas Horas", "33894100000168", "deposito", frozenset({"deposito"})),
    ("Casagrande Conveniencias", None, "mercado", frozenset({"outro"})),
    ("Delicatessen Selecty", None, "mercado", frozenset({"outro"})),
    ("Paulistana Pizza", None, "bar", frozenset({"restaurante"})),
    ("Iate Clube da Bahia", None, "bar", frozenset({"restaurante"})),
    ("Djalma's Drinks", None, "bar", BARISH),
)
#: Main activities that say plainly what a place is.
CNAE_LABELS: dict[int, tuple[str, frozenset[str]]] = {
    5611201: ("bar", frozenset({"restaurante"})),
    5611202: ("bar", BARISH),
    5611204: ("bar", BARISH),
    5611205: ("bar", BARISH),
    4711301: ("mercado", frozenset({"outro"})),
    4711302: ("mercado", frozenset({"outro"})),
    4712100: ("mercado", frozenset({"outro"})),
    4729602: ("mercado", frozenset({"outro"})),
}
GROUPS = {
    "review": "Review list (keep/hide)",
    "owner": "Owner's labels (12 Oct 2026)",
    "cnae": "Main activity (CNAE) labels",
}


@dataclass(frozen=True, slots=True)
class Label:
    group: str
    #: "bar", "mercado", "deposito", or "not_bar" (a store or a depósito: the review list's hide).
    expected: str
    kinds: frozenset[str] | None = None


def right(label: Label, kind: str, on_site: bool) -> bool:
    found = category(kind, on_site)
    return found in ("mercado", "deposito") if label.expected == "not_bar" else found == label.expected


def score(rows: list[tuple[dict[str, Any], Label, Result]]) -> dict[str, Any]:
    """Accuracy of the certain answers, coverage, each model alone, per expected class."""
    certain = [(v, label, r) for v, label, r in rows if r.certain]
    correct = [row for row in certain if right(row[1], row[2].kind, row[2].on_site)]
    kinds = [row for row in correct if row[1].kinds]
    models: dict[str, list[bool]] = defaultdict(list)
    for _, label, result in rows:
        for vote in result.votes:
            models[vote.model].append(right(label, vote.kind, vote.on_site))
    classes: dict[str, Counter[str]] = defaultdict(Counter)
    for _, label, result in rows:
        counts = classes[label.expected]
        counts["n"] += 1
        counts["certain"] += result.certain
        counts["right"] += result.certain and right(label, result.kind, result.on_site)
    return {
        "n": len(rows),
        "certain": len(certain),
        "right": len(correct),
        "accuracy": len(correct) / len(certain) if certain else None,
        "coverage": len(certain) / len(rows) if rows else None,
        "kind_right": sum(1 for _, label, r in kinds if label.kinds and r.kind in label.kinds),
        "kind_n": len(kinds),
        "models": {model: (sum(marks), len(marks)) for model, marks in models.items()},
        "classes": {name: dict(counts) for name, counts in sorted(classes.items())},
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f} %"


def _votes(votes: list[Vote]) -> str:
    return "; ".join(f"{v.model}: {v.kind}/{'on' if v.on_site else 'off'} {v.confidence:.2f}" for v in votes)


def report(rows: list[tuple[dict[str, Any], Label, Result]], missing: list[str], extra: list[str]) -> str:
    lines = ["# Venue classifier evaluation", ""]
    gated = [row for row in rows if row[1].group in ("review", "owner")]
    gate = score(gated)
    ready = gate["accuracy"] is not None and gate["accuracy"] >= GATE and gate["certain"] >= 20
    lines += [
        f"**Gate (owner-labelled, certain answers): {_pct(gate['accuracy'])}** "
        f"({gate['right']} of {gate['certain']} right; coverage {_pct(gate['coverage'])} of {gate['n']}). "
        + (
            "Ready: `/classifier apply` in Telegram lets it change venues."
            if ready
            else f"Not ready (needs ≥ {GATE * 100:.0f} % on at least 20 certain answers): keep `suggest`."
        ),
        "",
        "| Group | Venues | Certain | Right | Accuracy | Coverage | Kind right | Each model alone |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for group, title in GROUPS.items():
        s = score([row for row in rows if row[1].group == group])
        alone = ", ".join(f"{m} {r}/{n}" for m, (r, n) in s["models"].items()) or "—"
        lines.append(
            f"| {title} | {s['n']} | {s['certain']} | {s['right']} | {_pct(s['accuracy'])} | "
            f"{_pct(s['coverage'])} | {s['kind_right']}/{s['kind_n']} | {alone} |"
        )
    lines += [
        "",
        "Per expected class (all groups):",
        "",
        "| Class | Venues | Certain | Right |",
        "|---|---|---|---|",
    ]
    for name, counts in score(rows)["classes"].items():
        lines.append(
            f"| {name} | {counts.get('n', 0)} | {counts.get('certain', 0)} | {counts.get('right', 0)} |"
        )
    misses = [
        (venue, label, result)
        for venue, label, result in rows
        if not result.certain or not right(label, result.kind, result.on_site)
    ]
    lines += ["", f"## Misses and uncertain answers ({len(misses)})", ""]
    for venue, label, result in misses:
        verdict = "uncertain" if not result.certain else "WRONG"
        lines.append(
            f"- {verdict} · {venue['name']} (venue {venue['id']}, {label.group}): expected {label.expected}, "
            f"got {result.kind}/{'on site' if result.on_site else 'take away'} {result.confidence:.2f} · "
            f"{_votes(result.votes)} · {'; '.join(result.reasons)}"
        )
    if missing:
        lines += ["", "Labels not found among the venues: " + ", ".join(missing)]
    lines += ["", *extra]
    return "\n".join(lines)


def labels_for(venues: list[dict[str, Any]], review: dict[str, Any]) -> tuple[dict[int, Label], list[str]]:
    labels: dict[int, Label] = {}
    for venue in venues:
        row = review.get(venue.get("cnpj") or "")
        if row and row.decision in ("keep", "hide"):
            labels[int(venue["id"])] = Label("review", "bar" if row.decision == "keep" else "not_bar")
    found_names: set[str] = set()
    for name, cnpj, expected, kinds in OWNER_LABELS:
        for venue in venues:
            if (cnpj and venue.get("cnpj") == cnpj) or normalize(venue["name"]) == normalize(name):
                labels[int(venue["id"])] = Label("owner", expected, kinds)
                found_names.add(cnpj or name)
    missing = sorted(
        {cnpj or name for name, cnpj, _, _ in OWNER_LABELS} - found_names,
    )
    return labels, missing


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(prog="classify_probe")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--overture", type=Path, help="Overture places as GeoJSON-seq (else none)")
    parser.add_argument("--cnae-sample", type=int, default=40, help="scheduled venues to try for CNAE labels")
    parser.add_argument("--max-calls", type=int, default=300, help="model calls at most")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    api = ClassifierApi(os.environ["CB_API_URL"], os.environ["CB_INGEST_TOKEN"])
    review = load_review()
    cnpjs = sorted(cnpj for cnpj, row in review.items() if row.decision in ("keep", "hide"))
    cnpjs += [cnpj for _, cnpj, _, _ in OWNER_LABELS if cnpj]
    venues: dict[int, dict[str, Any]] = {}
    medians: dict[str, Any] = {}

    def take(batch: dict[str, Any]) -> None:
        medians.update(batch.get("medians") or {})
        for venue in batch.get("items") or []:
            venues.setdefault(int(venue["id"]), venue)

    for start in range(0, len(cnpjs), 100):
        take(api.candidates(cnpjs=cnpjs[start : start + 100]))
    take(api.candidates(names=[name for name, _, _, _ in OWNER_LABELS]))
    labels, missing = labels_for(list(venues.values()), review)
    sample = (
        api.candidates(limit=min(args.cnae_sample, 50), with_cnpj=True)["items"] if args.cnae_sample else []
    )
    sample = [venue for venue in sample if int(venue["id"]) not in venues]
    with (
        httpx.Client(timeout=120) as client,
        PoliteClient(delay=3.0) as http,
    ):
        voters, _ = voters_from_env(client)
        if not voters:
            parser.error("set CB_GEMINI_KEY, or CB_CF_ACCOUNT_ID and CB_CF_AI_TOKEN")
        if len(voters) < 2:
            # One model still measures that model alone (the per-model table); the combined result needs two
            # votes, so every answer counts as uncertain until the second key exists.
            log.warning("only one model is set: the per-model accuracy is the figure to read")
        places_key = os.environ.get("CB_GOOGLE_PLACES_KEY")
        classifier = VenueClassifier(
            voters,
            GooglePlaces(places_key, client) if places_key else None,
            threshold=float(os.environ.get("CB_CLASSIFY_THRESHOLD") or THRESHOLD),
            max_calls=args.max_calls,
        )
        meta, ig_user = os.environ.get("CB_META_TOKEN"), os.environ.get("CB_IG_USER_ID")
        builder = EvidenceBuilder(
            http,
            CnpjRegister(http, default_cache_file()),
            ShopBook(cache_dir() / SHOP_BOOK_FILE),
            instagram=Instagram(client, meta, ig_user) if meta and ig_user else None,
            overture_file=args.overture,
            overture_release=False,
        )
        everything = [venues[i] for i in labels] + sample
        builder.prepare(everything)
        rows: list[tuple[dict[str, Any], Label, Result]] = []
        for venue in everything:
            if classifier.exhausted:
                log.warning("model call budget spent: the evaluation stops here")
                break
            evidence = builder.build(venue, medians.get(venue["city"]))
            label = labels.get(int(venue["id"]))
            if label is None:
                plain = (
                    CNAE_LABELS.get(evidence.company.cnae)
                    if evidence.company and evidence.company.cnae
                    else None
                )
                if plain is None:
                    continue
                label = Label("cnae", plain[0], plain[1])
            result = classifier.classify(evidence, current_kind=None)
            rows.append((venue, label, result))
            log.info(
                "%s: expected %s, got %s/%s %.2f %s",
                venue["name"],
                label.expected,
                result.kind,
                result.on_site,
                result.confidence,
                "certain" if result.certain else "uncertain",
            )
        extra = [
            f"Model calls: {classifier.calls}; Google Places lookups: "
            f"{classifier.places.calls if classifier.places else 0}; errors: {len(classifier.errors)}.",
            "The venues' current kinds weren't shown to the models.",
        ]
    text = report(rows, missing, extra)
    print(text)
    (args.out / "report.md").write_text(text + "\n", "utf-8")
    (args.out / "results.json").write_text(
        json.dumps(
            [
                {"venue": v["id"], "name": v["name"], "group": label.group, "expected": label.expected}
                | r.to_json()
                for v, label, r in rows
            ],
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
