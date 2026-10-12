"""What kind of place is this venue? Two LLM votes over its evidence (owner, 12 Oct 2026: "we need a good
LLM powered service to do this for us").

Each venue's evidence (VenueEvidence: the CNPJ register, OpenStreetMap tags, Overture categories, its
prices, its receipts, Google Places and Instagram when needed) becomes one prompt with the definitions of
each kind and Brazilian naming cues. Two independent models answer it in strict JSON (kind, on_site,
confidence, reasons, evidence used): Gemini's free tier and Workers AI. `combine` turns the votes into one
result:

- certain: both put the venue in the same category (bar, mercado, depósito) with the same on_site, each at
  `threshold` or more. Within a category, a disagreement on the kind keeps the venue's kind when it is in that
  category, else takes the surer vote's;
- uncertain: anything else (a disagreement, low confidence, a model that didn't answer). Nothing changes; the
  venue is tried again on a later run, with Google Places as extra evidence when the budget allows.

Google Places is looked up when the free evidence is thin (no register, no map listing) or the votes don't
agree; its answer stays in memory (only the place id is kept: Google's terms).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .ai.workers_ai import TextReply
from .cnae import describe
from .cnpj import Company
from .places import GooglePlaces, PlaceHit
from .venues import ShopRecord

log = logging.getLogger(__name__)

#: VENUE_KINDS in @cb/domain (a test keeps them in step with the generated contract models).
VENUE_KINDS = ("bar", "boteco", "pub", "barraca", "restaurante", "deposito", "cervejaria", "outro")
EVIDENCE = (
    "name",
    "address",
    "cnpj",
    "osm",
    "overture",
    "prices",
    "receipts",
    "google_places",
    "instagram",
)
Category = Literal["bar", "mercado", "deposito"]
#: Both votes at least this sure, and in agreement, for a result to count as certain.
THRESHOLD = 0.8
#: OpenStreetMap tags worth showing the models (the rest are names, contacts and addresses).
OSM_KEYS = re.compile(
    r"^(amenity|shop|craft|cuisine|microbrewery|brewery|bar|pub|drink(:.*)?|takeaway|outdoor_seating|"
    r"live_music|leisure|club|sport|tourism|opening_hours|description)$"
)


def category(kind: str, on_site: bool) -> Category:
    """venueCategory in @cb/domain: a depósito is one whatever on_site says; else on site is a bar."""
    if kind == "deposito":
        return "deposito"
    return "bar" if on_site else "mercado"


@dataclass(slots=True)
class VenueEvidence:
    """Everything known about one venue for a vote. `places` lives in memory only (Google's terms)."""

    venue_id: int
    name: str
    city: str
    lat: float
    lng: float
    bairro: str | None = None
    address: str | None = None
    cnpj: str | None = None
    company: Company | None = None
    osm_tags: Mapping[str, str] | None = None
    #: {"name", "primary", "alternate": [...]} of the matching Overture place.
    overture: Mapping[str, Any] | None = None
    #: The candidates endpoint's ClassifierPrices, and the city's bar and shop medians per litre (centavos).
    prices: Mapping[str, Any] | None = None
    medians: Mapping[str, Any] | None = None
    receipts: ShopRecord | None = None
    places: PlaceHit | None = None
    #: {"name", "biography"} of the venue's Instagram account (Business Discovery).
    instagram: Mapping[str, Any] | None = None

    def sources(self) -> list[str]:
        found = ["name"]
        checks = {
            "address": self.address or self.bairro,
            "cnpj": self.company,
            "osm": self.osm_tags,
            "overture": self.overture,
            "prices": self.prices and self.prices.get("n"),
            "receipts": self.receipts
            and (self.receipts.packs or self.receipts.ratios or self.receipts.samples),
            "google_places": self.places,
            "instagram": self.instagram,
        }
        return found + [name for name, value in checks.items() if value]

    def weak(self) -> bool:
        """No register, no map listing: the name and prices alone."""
        return not (self.company or self.osm_tags or self.overture)

    def price_levels(self) -> list[PriceLevel]:
        """The venue's median per litre per format type against the city's bar and shop medians."""
        types = ((self.medians or {}).get("types") or {}) if self.medians else {}
        levels = []
        for row in (self.prices or {}).get("by_type") or []:
            city = types.get(row["type"]) or {}
            bar, shop = city.get("bar"), city.get("shop")
            if not bar and not shop:
                continue
            levels.append(PriceLevel(row["type"], int(row["n"]), int(row["median_ppn_minor"]), bar, shop))
        return levels

    def shop_level_prices(self) -> bool:
        """Prices too low for a bar (owner, 12 Oct 2026): at or below the shops' median, or at most
        SHOP_LEVEL_BAR_RATIO of the bars', for SHOP_LEVEL_MIN prices or more and most comparable ones."""
        levels = self.price_levels()
        low = sum(level.n for level in levels if level.shop_level)
        return low >= SHOP_LEVEL_MIN and 2 * low >= sum(level.n for level in levels)


#: A format type's median at most this share of the bars' median is a shop's price.
SHOP_LEVEL_BAR_RATIO = 0.7
SHOP_LEVEL_MIN = 2
#: Registered bars and restaurants: shop-level prices alone don't make them a shop (VenueClassifier).
BAR_CNAE = frozenset({5611201, 5611204, 5611205})
#: Both votes must be this sure to make a registered bar a shop on shop-level prices.
BAR_CNAE_THRESHOLD = 0.9
TYPE_NAMES = {
    "lata": "cans",
    "long_neck": "long necks",
    "garrafa": "bottles (600 ml, 1 l)",
    "litrao": "litrões",
    "chopp": "draught",
    "torre": "towers",
}


@dataclass(frozen=True, slots=True)
class PriceLevel:
    type: str
    n: int
    median: int
    bar: int | None
    shop: int | None

    @property
    def shop_level(self) -> bool:
        return bool(
            (self.shop and self.median <= self.shop)
            or (self.bar and self.median <= SHOP_LEVEL_BAR_RATIO * self.bar)
        )

    def text(self) -> str:
        parts = [f"{TYPE_NAMES.get(self.type, self.type)} ({self.n}) {_money(self.median)}/L"]
        if self.bar:
            parts.append(f"{self.median / self.bar:.2f}x the bar median")
        if self.shop:
            parts.append(f"{self.median / self.shop:.2f}x the shop median")
        return " = ".join(parts[:2]) + (f", {parts[2]}" if len(parts) > 2 else "")


def _money(minor: Any) -> str:
    return f"R$ {int(minor) / 100:.2f}"


def evidence_lines(evidence: VenueEvidence) -> list[str]:
    """The evidence as the prompt shows it."""
    e = evidence
    lines = [f"Name we know: {e.name}"]
    where = ", ".join(part for part in (e.address, e.bairro, e.city.replace("-", " ").title()) if part)
    lines.append(f"Where: {where}")
    if e.company:
        c = e.company
        facts = [
            f"status {'active' if c.active else 'not active'}",
            f'trade name (nome fantasia) "{c.trade_name}"' if c.trade_name else "no trade name",
        ]
        if c.legal_name:
            facts.append(f'legal name "{c.legal_name}"')
        if c.mei:
            facts.append("a micro-entrepreneur (MEI)")
        lines.append("CNPJ register (Receita Federal): " + "; ".join(facts))
        lines.append(f"  Main activity: {describe(c.cnae) if c.cnae else 'unknown'}")
        if c.secondary:
            lines.append("  Secondary activities: " + "; ".join(describe(code) for code in c.secondary[:12]))
    elif e.cnpj:
        lines.append("CNPJ register: not found")
    else:
        lines.append("CNPJ: unknown")
    if e.osm_tags:
        tags = [f"{k}={v}" for k, v in sorted(e.osm_tags.items()) if OSM_KEYS.match(k)]
        lines.append("OpenStreetMap tags: " + (", ".join(tags) or "none of interest"))
    if e.overture:
        alternate = ", ".join(e.overture.get("alternate") or []) or "none"
        name, primary = e.overture.get("name") or "?", e.overture.get("primary") or "?"
        lines.append(f'Overture Maps place "{name}": category {primary}; alternate categories: {alternate}')
    if e.places:
        p = e.places
        lines.append(
            f'Google Places: "{p.name}"; primary type {p.primary_type or "none"}; types: '
            f"{', '.join(p.types) or 'none'}; status {p.business_status or 'unknown'}"
        )
    if e.instagram:
        bio = " ".join(str(e.instagram.get("biography") or "").split())[:300]
        lines.append(f'Instagram account: name "{e.instagram.get("name") or "?"}"; bio: "{bio}"')
    prices = e.prices or {}
    if prices.get("n"):
        text = (
            f"Published prices: {prices['n']} ({prices.get('on_site', 0)} marked as drunk there, "
            f"{prices.get('takeaway', 0)} as taken away, {prices.get('packs', 0)} for boxes, packs, buckets)"
        )
        if prices.get("median_ppn_minor"):
            text += f"; median {_money(prices['median_ppn_minor'])} per litre for single beers"
        medians = e.medians or {}
        if medians.get("bar") or medians.get("shop"):
            text += (
                f" (city medians per litre: bars {_money(medians['bar']) if medians.get('bar') else '?'}, "
                f"shops {_money(medians['shop']) if medians.get('shop') else '?'})"
            )
        lines.append(text)
        levels = e.price_levels()
        if levels:
            lines.append(
                "  Per format type against the city's medians per litre: "
                + "; ".join(level.text() for level in levels)
            )
            lines.append(
                "  Shop-level prices: "
                + (
                    "YES (too low for a bar: strong evidence of a depósito or a store)"
                    if e.shop_level_prices()
                    else "no"
                )
            )
        if prices.get("samples"):
            lines.append("  e.g. " + "; ".join(prices["samples"][:6]))
    else:
        lines.append("Published prices: none")
    r = e.receipts
    if r and (r.packs or r.ratios or r.samples):
        facts = []
        if r.packs:
            facts.append(f"sold beer by the box, pack or keg {len(r.packs)} times")
        if r.ratios:
            ratios = sorted(r.ratios.values())
            facts.append(f"single beers at {ratios[len(ratios) // 2]:.2f}x the supermarkets' median price")
        if r.samples:
            facts.append("e.g. " + "; ".join(r.samples[:3]))
        lines.append("Electronic receipts (SEFAZ-BA Preço da Hora): " + "; ".join(facts))
    return lines


PROMPT = """You classify places that sell beer in Salvador and Lauro de Freitas (Bahia, Brazil) for a site
that compares beer prices. Decide what this place IS from the evidence below.

Kinds:
- bar: a bar where people sit and drink (bar, choperia, chopperia, lounge, gastrobar, bar with live music).
- boteco: a simple neighbourhood bar (boteco, buteco, botequim, "bar do Fulano", espetinho with tables).
- pub: a pub.
- barraca: a beach barraca on the sand.
- restaurante: a place mainly for meals where people also drink: restaurante, pizzaria, churrascaria,
  galeteria, cantina, petiscaria with a kitchen, a social, sports or yacht club with a restaurant (clube,
  iate clube, associação), a hotel's restaurant.
- cervejaria: a brewery or brewpub with a taproom.
- deposito: a depósito de bebidas, distribuidora, adega or "comércio varejista de bebidas": a drinks shop that
  sells beer to take away (by the box, crate or fardo), even when it has a few tables outside or a name with
  "bar" in it.
- outro: anything else. Stores: supermercado, mercado, mercadinho, minimercado, mercearia, armazém, atacado,
  atacarejo, conveniência (also a fuel station's shop), delicatessen, empório, padaria, açougue, hortifruti,
  tabacaria. Also nightclubs (boate, casa de shows), cafés, snack bars (lanchonete) and event venues.

on_site: true when customers normally drink at the place (bars, botecos, pubs, barracas, restaurants,
taprooms, nightclubs, cafés); false for stores and anything that only sells to take away. A depósito is kind
"deposito" either way; its on_site is true only when the evidence shows people drink there.

Weighing the evidence:
- The CNPJ register is the strongest single source: the main activity (CNAE) is what the business registered
  as. 4723-7/00 is drinks retail (a depósito, unless other sources clearly show a bar); 4711-3/01, 4711-3/02,
  4712-1/00 and 4729-6/02 are stores; 5611-2/01 is a restaurant; 5611-2/04 and 5611-2/05 are bars; 5611-2/03
  is a snack bar; 9312-3/00 is a club. Many real bars also register drinks retail as a secondary activity:
  secondary activities are only hints.
- Names: "Depósito", "Distribuidora", "Adega", "Conveniência", "Mercado", "Mercadinho", "Supermercado",
  "Mercearia", "Delicatessen", "Empório", "Atacado", "Padaria" point to shops; "Pizzaria", "Restaurante",
  "Churrascaria", "Clube" to restaurants; "Bar", "Boteco", "Pub", "Choperia" to bars. The trade name in the
  register beats the name we know (a place we know as "Bar X" registered as "Depósito X" is a depósito).
- Map listings (OpenStreetMap tags, Overture categories, Google Places types) show how the public sees it.
- Prices are strong evidence: bars can't sell at shop prices. "Shop-level prices: YES" (the venue's median per
  litre at or below the shops' median, or at most 0.7x the bars', for the same format type) means a depósito
  (when the name or register says depósito, distribuidora, adega or bebidas) or else a store, even when
  OpenStreetMap or Overture tag it as a bar. A place registered as a bar or restaurant (5611-2/01, /04, /05)
  with shop-level prices is doubtful: keep your confidence below 0.9 unless other sources also say shop.
  Boxes, packs and fardos mean take-away.
- When the sources conflict or are thin, lower your confidence instead of guessing.

confidence: 0 to 1, how sure you are of BOTH kind and on_site. 0.9 or more only when independent sources
agree, or the register is unambiguous and nothing contradicts it.
reasons: 1 to 3 short sentences in English, each naming its source ("CNPJ main activity 4723-7/00 drinks
retail", "trade name says Conveniência"). Never copy Google Places' text: write "Google Places agrees" or
"Google Places lists it as a shop".
evidence_used: the sources you relied on, among: {sources}.

Answer with JSON only, no prose and no code fences, matching this JSON schema:
{schema}

The place:
{evidence}
"""


def vote_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(VENUE_KINDS)},
            "on_site": {"type": "boolean"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "evidence_used": {"type": "array", "items": {"type": "string", "enum": list(EVIDENCE)}},
        },
        "required": ["kind", "on_site", "confidence", "reasons", "evidence_used"],
        "additionalProperties": False,
    }


def build_prompt(evidence: VenueEvidence) -> str:
    return PROMPT.format(
        sources=", ".join(evidence.sources()),
        schema=json.dumps(vote_schema()),
        evidence="\n".join(evidence_lines(evidence)),
    )


@dataclass(frozen=True, slots=True)
class Vote:
    model: str
    kind: str
    on_site: bool
    confidence: float
    reasons: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def category(self) -> Category:
        return category(self.kind, self.on_site)

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model[:80],
            "kind": self.kind,
            "on_site": self.on_site,
            "confidence": round(self.confidence, 3),
        }


def _json_object(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = fenced.group(1) if fenced else text
    start = body.find("{")
    end = body.rfind("}")
    return json.loads(body[start : end + 1] if start >= 0 and end > start else body)


def parse_vote(text: str, model: str) -> Vote | None:
    """A model's reply as a Vote; None when it isn't valid JSON of the schema (it is never repaired)."""
    try:
        data = _json_object(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    kind, on_site, confidence = data.get("kind"), data.get("on_site"), data.get("confidence")
    if kind not in VENUE_KINDS or not isinstance(on_site, bool):
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        return None
    reasons = tuple(
        " ".join(str(reason).split())[:200] for reason in data.get("reasons") or [] if str(reason).strip()
    )[:3]
    used = tuple(source for source in data.get("evidence_used") or [] if source in EVIDENCE)
    return Vote(
        model, kind, on_site, min(1.0, max(0.0, float(confidence))), reasons, tuple(dict.fromkeys(used))
    )


@dataclass(slots=True)
class Result:
    """One venue's classification: the ClassifierResult the API takes, plus the votes for the log."""

    venue_id: int
    kind: str
    on_site: bool
    confidence: float
    certain: bool
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    votes: list[Vote] = field(default_factory=list)
    google_place_id: str | None = None

    @property
    def category(self) -> Category:
        return category(self.kind, self.on_site)

    def to_json(self) -> dict[str, Any]:
        return {
            "venue_id": self.venue_id,
            "kind": self.kind,
            "on_site": self.on_site,
            "confidence": round(min(1.0, max(0.0, self.confidence)), 3),
            "certain": self.certain,
            "reasons": [reason[:200] for reason in self.reasons if reason.strip()][:5],
            "evidence": [source for source in dict.fromkeys(self.evidence) if source in EVIDENCE],
            "votes": [vote.to_json() for vote in self.votes[:4]],
            "google_place_id": self.google_place_id,
        }


def _said(vote: Vote) -> str:
    return f"{vote.kind} ({'on site' if vote.on_site else 'take away'})"


def combine(
    venue_id: int,
    votes: Sequence[Vote | None],
    *,
    current_kind: str | None = None,
    threshold: float = THRESHOLD,
) -> Result:
    """The votes as one result (see the module's note)."""
    valid = [vote for vote in votes if vote is not None]
    if not valid:
        return Result(venue_id, current_kind or "bar", True, 0.0, False, ["no model answered"])
    evidence = list(dict.fromkeys(source for vote in valid for source in vote.evidence))
    if len(valid) < 2:
        only = valid[0]
        return Result(
            venue_id,
            only.kind,
            only.on_site,
            only.confidence,
            False,
            ["only one model answered", *only.reasons[:2]],
            evidence,
            valid,
        )
    first, second = valid[0], valid[1]
    sure = max(valid, key=lambda vote: vote.confidence)
    confidence = min(first.confidence, second.confidence)
    if first.category != second.category or first.on_site != second.on_site:
        reasons = [
            f"the models disagree: {first.model} says {_said(first)}, {second.model} says {_said(second)}",
            *sure.reasons[:1],
        ]
        return Result(venue_id, sure.kind, sure.on_site, confidence, False, reasons, evidence, valid)
    if first.kind == second.kind:
        kind = first.kind
    elif current_kind and category(current_kind, first.on_site) == first.category:
        # Same category, different kinds (bar or boteco): the venue's own kind stays.
        kind = current_kind
    else:
        kind = sure.kind
    other = second if sure is first else first
    reasons = list(dict.fromkeys([*sure.reasons, *other.reasons]))[:3]
    certain = confidence >= threshold
    if not certain:
        reasons = [
            f"the models agree but aren't sure enough ({confidence:.2f} < {threshold:.2f})",
            *reasons[:2],
        ]
    return Result(venue_id, kind, first.on_site, confidence, certain, reasons, evidence, valid)


@dataclass(frozen=True, slots=True)
class Voter:
    """One model: its name and how to ask it (a prompt in, a TextReply out)."""

    model: str
    ask: Callable[[str], TextReply]


class VenueClassifier:
    """Votes on one venue at a time, within a budget of model calls; asks Google Places when it helps."""

    def __init__(
        self,
        voters: Sequence[Voter],
        places: GooglePlaces | None = None,
        *,
        threshold: float = THRESHOLD,
        max_calls: int = 200,
    ) -> None:
        self.voters = list(voters)
        self.places = places
        self.threshold = threshold
        self.max_calls = max_calls
        self.calls = 0
        self.errors: list[str] = []

    @property
    def exhausted(self) -> bool:
        return self.calls + len(self.voters) > self.max_calls

    def vote(self, evidence: VenueEvidence) -> list[Vote | None]:
        prompt = build_prompt(evidence)
        votes: list[Vote | None] = []
        for voter in self.voters:
            self.calls += 1
            reply = voter.ask(prompt)
            vote = parse_vote(reply.text, voter.model) if not reply.error else None
            if vote is None:
                problem = reply.error or "not a valid answer"
                self.errors.append(f"{voter.model}: {problem}"[:300])
                log.warning("venue %s: %s gave no vote (%s)", evidence.venue_id, voter.model, problem)
            votes.append(vote)
        return votes

    def look_up_place(self, evidence: VenueEvidence) -> bool:
        if self.places is None or evidence.places is not None or self.places.exhausted:
            return False
        evidence.places = self.places.search(evidence.name, evidence.city, evidence.lat, evidence.lng)
        return evidence.places is not None

    def classify(self, evidence: VenueEvidence, current_kind: str | None = None) -> Result:
        if evidence.weak():
            self.look_up_place(evidence)
        result = combine(
            evidence.venue_id, self.vote(evidence), current_kind=current_kind, threshold=self.threshold
        )
        if not result.certain and not self.exhausted and self.look_up_place(evidence):
            # A second round with what Google knows of the place.
            result = combine(
                evidence.venue_id, self.vote(evidence), current_kind=current_kind, threshold=self.threshold
            )
        if (
            result.certain
            and result.category != "bar"
            and evidence.company is not None
            and evidence.company.cnae in BAR_CNAE
            and evidence.shop_level_prices()
            and result.confidence < BAR_CNAE_THRESHOLD
        ):
            # Shop-level prices alone don't overrule a register that says bar or restaurant.
            result.certain = False
            result.reasons = [
                f"registered as a bar or restaurant: shop-level prices need both models at "
                f"{BAR_CNAE_THRESHOLD:.1f} or more",
                *result.reasons[:2],
            ]
        if evidence.places is not None:
            result.google_place_id = evidence.places.place_id or None
            if "google_places" not in result.evidence:
                result.evidence.append("google_places")
        return result
