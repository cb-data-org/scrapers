"""The reviewed list of doubtful venues (#331): an audit of what is on the map, and the owner's decisions
applied in production through the admin API.

`cb venues audit` judges every live venue that has a CNPJ with the checks for sit-down drinking places
(venues.py): the CNPJ register, what the Preço da Hora runs learnt (sales by the box, prices against shop
prices) and the venue's published prices. It suggests a decision for each (hide, pending, keep, or merge into
the OpenStreetMap venue it duplicates), adds the shops the daily runs held back for review and never sent
(pending, #340), and writes data/seed/venue-review.csv for the owner to review in a PR; decisions already
there are kept.

`cb venues apply` then does what the merged list says: it hides the venues marked hide or pending (which
takes their prices off the map), puts back those marked keep, merges the duplicates, and stops counting
OpenStreetMap nightclubs as bars. Dry run by default. The API writes the audit rows and marks the site for
republishing.
"""

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from .cnpj import CnpjRegister
from .core import USER_AGENT
from .matching import Candidate, best_match
from .venues import BAR_KINDS, Evidence, ReviewRow, ShopBook, ShopPrices, judge

log = logging.getLogger(__name__)

SUGGESTION = {"reject": "hide", "review": "pending", "show": "keep"}


class AdminClient:
    """The admin API with an admin-scoped token: list, change and merge venues."""

    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=30.0,
            transport=transport or httpx.HTTPTransport(retries=2),
        )

    def venues(self, **filters: str) -> Iterator[dict[str, Any]]:
        after: int | None = None
        while True:
            params: dict[str, Any] = {"limit": 100, **filters, **({"after": after} if after else {})}
            response = self._client.get("/api/admin/venues", params=params)
            response.raise_for_status()
            page = response.json()
            yield from page["items"]
            after = page.get("next")
            if not after:
                return

    def patch(self, venue_id: int, **changes: Any) -> None:
        self._client.patch(f"/api/admin/venues/{venue_id}", json=changes).raise_for_status()

    def merge(self, venue_id: int, into: int) -> None:
        self._client.post(f"/api/admin/venues/{venue_id}/merge", json={"into": into}).raise_for_status()

    def published_deals(self, city: str) -> list[dict[str, Any]]:
        response = self._client.get(f"/api/v1/{city}/deals.json")
        response.raise_for_status()
        return list(response.json()["items"])


def deal_key(deal: dict[str, Any]) -> str | None:
    """A published single serving as the shop-price key: "brand:amstel|garrafa_600"."""
    if deal.get("units") != 1 or deal.get("packaging") != "single":
        return None
    product = f"beer:{deal['beer']}" if deal.get("beer") else f"brand:{deal.get('brand')}"
    return f"{product}|{deal['format_code']}"


@dataclass(frozen=True, slots=True)
class Finding:
    venue: dict[str, Any]
    row: ReviewRow


def audit(
    live: Iterable[dict[str, Any]],
    deals: Iterable[dict[str, Any]],
    register: CnpjRegister,
    book: ShopBook,
    shop_prices: ShopPrices,
    review: dict[str, ReviewRow],
) -> list[Finding]:
    """A suggestion for every live venue with a CNPJ; the owner's decisions already in the list stay."""
    venues = list(live)
    osm = [
        Candidate(v["name"], v["lat"], v["lng"], v)
        for v in venues
        if v.get("osm_ref") and v["status"] == "active"
    ]
    prices: dict[int, list[tuple[str, float]]] = {}
    for deal in deals:
        key = deal_key(deal)
        if key:
            prices.setdefault(int(deal["venue_id"]), []).append((key, deal["price_minor"] / 100))
    findings = []
    for venue in venues:
        cnpj = venue.get("cnpj")
        if not cnpj or venue["status"] not in ("active", "hidden"):
            continue
        company, record = register.lookup(cnpj), book.records.get(cnpj)
        ratios = dict(record.ratios) if record else {}
        samples = []
        for key, price in prices.get(int(venue["id"]), []):
            median = shop_prices.median(key)
            if median:
                ratios[key] = round(price / median, 2)
            samples.append(
                f"{key.split('|')[1]} R$ {price:.2f}" + (f" ({price / median:.1f}x)" if median else "")
            )
        evidence = Evidence(
            cnpj=cnpj,
            main=company.cnae if company else None,
            secondary=company.secondary if company else (),
            mei=company.mei if company else False,
            names=tuple(
                n
                for n in (company and company.trade_name, company and company.legal_name, venue["name"])
                if n
            ),
            osm_kind=venue["kind"] if venue.get("osm_ref") else None,
            packs=len(record.packs) if record else 0,
            ratios=list(ratios.values()),
        )
        owner = review.get(cnpj)
        if owner and owner.decision != "pending":
            findings.append(Finding(venue, owner))
            continue
        verdict = judge(evidence)
        decision, merge_into = SUGGESTION[verdict.decision], ""
        if not venue.get("osm_ref") and decision == "keep":
            twin = best_match(
                [venue["name"]], venue["lat"], venue["lng"], [c for c in osm if c.data["id"] != venue["id"]]
            )
            if twin:
                decision, merge_into = "merge", f"osm:{twin.data['osm_ref']}"
        activity = f"main activity {evidence.main}" if evidence.main else "not in the CNPJ register"
        note = "; ".join([*verdict.reasons, activity, *samples[:3]])
        if merge_into:
            note = f"second pin of “{twin.data['name']}”; {note}"
        findings.append(Finding(venue, ReviewRow(cnpj, decision, merge_into, venue["name"], note[:300])))
    return findings


def held(book: ShopBook, review: dict[str, ReviewRow], live: Iterable[str] = ()) -> list[ReviewRow]:
    """The shops the daily runs held back for the owner and never sent, as pending rows with their evidence.
    Shops rejected on two signs stay out; shops already in the list, or live on the map, are covered there."""
    covered = set(review) | set(live)
    rows = []
    for cnpj, record in book.records.items():
        if record.decision != "review" or record.sent or cnpj in covered:
            continue
        place = f"bairro {record.bairro}" if record.bairro else "no bairro"
        osm = f"OpenStreetMap {record.osm}" if record.osm else "not on OpenStreetMap"
        note = "; ".join(["held back: " + ", ".join(record.reasons), place, osm, *record.samples[:3]])
        rows.append(ReviewRow(cnpj, "pending", "", record.name or cnpj, note[:300]))
    return rows


def apply(
    admin: AdminClient, review: Iterable[ReviewRow], nightclubs: Iterable[str] = (), *, dry_run: bool = True
) -> list[str]:
    """What the list says, done in production (or only described, on a dry run)."""
    done = []

    def act(text: str, change: Any) -> None:
        done.append(("would " if dry_run else "") + text)
        if not dry_run:
            change()

    for row in review:
        found = list(admin.venues(cnpj=row.cnpj))
        venue = next((v for v in found if v["status"] != "merged"), None)
        if venue is None:
            continue
        label = f"{venue['name']} ({row.cnpj}, venue {venue['id']})"
        if row.decision in ("hide", "pending") and venue["status"] == "active":
            act(f"hide {label}", lambda v=venue: admin.patch(v["id"], status="hidden"))
        elif row.decision == "keep" and venue["status"] == "hidden":
            act(f"show again {label}", lambda v=venue: admin.patch(v["id"], status="active"))
        elif row.decision == "merge" and row.merge_into.startswith("osm:"):
            target = next(iter(admin.venues(osm_ref=row.merge_into.removeprefix("osm:"))), None)
            if target and target["id"] != venue["id"]:
                act(
                    f"merge {label} into {target['name']} (venue {target['id']})",
                    lambda v=venue, t=target: admin.merge(v["id"], t["id"]),
                )
    for ref in nightclubs:
        for venue in admin.venues(osm_ref=ref):
            if venue["kind"] in BAR_KINDS and venue["status"] == "active":
                act(
                    f"stop counting the nightclub {venue['name']} (venue {venue['id']}) as a bar",
                    lambda v=venue: admin.patch(v["id"], kind="outro"),
                )
    return done
