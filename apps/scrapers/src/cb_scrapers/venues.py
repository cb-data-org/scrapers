"""Is this shop a place to sit and drink? Conservative checks for shops found on receipts (#330).

The map shows bars, pubs, botecos, beach barracas, brewery taprooms and sit-down restaurants; never
depósitos, convenience stores, bakeries, snack bars, supermarkets, nightclubs, tiny or fake bars. Each shop
gets a verdict from its evidence:

- reject: its main registered activity isn't a bar, a restaurant or a brewery, or the evidence says "shop"
  twice over (two signs, or one sign and a shop word in its name);
- review: one sign of a shop, prices like a shop's, a micro-entrepreneur (MEI) that OpenStreetMap doesn't map
  as a bar, or a place OpenStreetMap maps as a café. It is sent with these doubts and waits in the queue for
  the AI reviewer, which keeps or hides it by the review threshold (#416); nobody reviews it by hand;
- show: everything else, whether OpenStreetMap knows the place or not.

Signs of a shop: a secondary activity that makes a shop of a place (convenience store, supermarket, bakery,
butcher, fuel station, drinks wholesale), and beer sold by the box, pack or keg. Drinks retail, other food
retail, food wholesale and tobacconist are not signs: real bars register them too. Prices are compared with
the supermarkets' in the same receipts. Shop words in a name
("Armazém e Bar", "24h") never decide on their own. The owner's decisions in data/seed/venue-review.csv
(keep, hide, pending, or merge into an OpenStreetMap venue) come before everything else.
"""

import csv
import json
import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from .core import normalize

REVIEW_FILE = Path(__file__).resolve().parents[4] / "data" / "seed" / "venue-review.csv"
REVIEW_FIELDS = ("cnpj", "decision", "merge_into", "name", "note")
DECISIONS = ("keep", "hide", "pending", "merge")

#: Main activities in scope, as the venue kind they become.
SCOPE = {
    5611201: "restaurante",  # Restaurantes e similares
    5611202: "bar",  # Bares e outros estabelecimentos especializados em servir bebidas (before CNAE 2.3)
    5611204: "bar",  # Bares …, sem entretenimento
    5611205: "bar",  # Bares …, com entretenimento
    1113502: "cervejaria",  # Fabricação de cervejas e chopes: taprooms
}
#: Secondary activities that make a shop of a place, by family; each family found is one sign. Not signs, as
#: real bars register them too: drinks retail (4723-7/00, takeaway), other food retail (4729-6/99), food
#: wholesale (4639-7/01, 4691-5/00) and tobacconist (4729-6/01, cigarettes at the counter).
SHOP_ACTIVITIES = {
    "convenience store": (4729602,),
    "supermarket": (4711301, 4711302, 4712100),
    "bakery": (1091101, 1091102, 4721102),
    "butcher": (4722901, 4722902),
    "fuel station": (4731800, 4732600),
    "drinks wholesale": (4635401, 4635402, 4635403, 4635499),
}
#: Main activities of supermarkets: their prices are the shop prices bars are compared with. Depósitos and
#: convenience stores charge nearly bar prices for cold single beers, so they don't count.
SUPERMARKETS = frozenset({4711301, 4711302, 4712100})
#: Words that make a name sound like a shop: never a reason on their own.
SHOP_WORDS = re.compile(
    r"\b(24 ?h(?:rs?)?|24 horas|conveniencia|deposito|distribuidora|mercado|mercadinho|minimercado"
    r"|supermercado|mercearia|padaria|panificadora|confeitaria|delicatessen|posto|atacad\w*|atacarejo|adega"
    r"|emporio|armazem|comercio de bebidas)\b"
)
#: Kinds of OpenStreetMap venue that mean "a bar".
BAR_KINDS = frozenset({"bar", "pub", "boteco", "barraca", "cervejaria"})
#: Beers at most this many times the median shop price for the same beer and size: prices like a shop's.
SHOP_PRICE_RATIO = 1.3
#: Shop prices count for this long, and a median needs this many of them.
SHOP_PRICE_DAYS = 60
SHOP_PRICE_MIN = 3

Decision = Literal["show", "review", "reject"]


@dataclass(slots=True)
class Evidence:
    """What is known about one shop: from the CNPJ register, its receipts and OpenStreetMap."""

    cnpj: str
    main: int | None = None
    secondary: tuple[int, ...] = ()
    mei: bool = False
    names: tuple[str, ...] = ()
    #: The kind of the OpenStreetMap venue it matched, if any.
    osm_kind: str | None = None
    #: Beer sold by the box, pack or keg.
    packs: int = 0
    #: Each comparable beer's price over the median shop price for the same beer and size.
    ratios: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    reasons: tuple[str, ...] = ()


def shop_signs(evidence: Evidence) -> list[str]:
    signs = [
        f"{family} ({next(code for code in evidence.secondary if code in codes)})"
        for family, codes in SHOP_ACTIVITIES.items()
        if any(code in codes for code in evidence.secondary)
    ]
    if evidence.packs:
        signs.append(f"sells beer by the box, pack or keg ({evidence.packs}x)")
    return signs


def shop_words(evidence: Evidence) -> list[str]:
    found = {match.group(1) for name in evidence.names for match in SHOP_WORDS.finditer(normalize(name))}
    return sorted(found)


def judge(evidence: Evidence, decision: str | None = None) -> Verdict:
    """The verdict for a shop; `decision` is the owner's, from the review list."""
    if decision in ("keep", "merge"):
        return Verdict("show", ("kept in the review list",))
    if decision == "hide":
        return Verdict("reject", ("hidden in the review list",))
    if evidence.main not in SCOPE:
        return Verdict("reject", (f"main activity {evidence.main} isn't a bar, restaurant or brewery",))
    signs, words = shop_signs(evidence), shop_words(evidence)
    if len(signs) >= 2 or (signs and words):
        return Verdict("reject", (*signs, *(f"shop word in its name: {word}" for word in words)))
    reasons = list(signs)
    if evidence.ratios:
        ratio = statistics.median(evidence.ratios)
        if ratio <= SHOP_PRICE_RATIO:
            reasons.append(f"prices like a shop's ({ratio:.2f}x the median shop price)")
    if evidence.mei and evidence.osm_kind not in BAR_KINDS:
        reasons.append("micro-entrepreneur (MEI) that OpenStreetMap doesn't map as a bar")
    if evidence.osm_kind == "outro":
        reasons.append("OpenStreetMap maps it as a café")
    if decision == "pending":
        reasons.append("pending in the review list")
    return Verdict("review", tuple(reasons)) if reasons else Verdict("show")


@dataclass(frozen=True, slots=True)
class ReviewRow:
    cnpj: str
    decision: str
    merge_into: str = ""
    name: str = ""
    note: str = ""


def load_review(path: Path = REVIEW_FILE) -> dict[str, ReviewRow]:
    """The owner's decisions by CNPJ; rows with an unknown decision are ignored."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as file:
        rows = (
            ReviewRow(**{key: (row.get(key) or "").strip() for key in REVIEW_FIELDS})
            for row in csv.DictReader(file)
        )
        return {
            row.cnpj: row for row in rows if re.fullmatch(r"\d{14}", row.cnpj) and row.decision in DECISIONS
        }


def write_review(rows: Iterable[ReviewRow], path: Path = REVIEW_FILE) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(REVIEW_FIELDS)
        for row in sorted(rows, key=lambda row: (DECISIONS.index(row.decision), row.name.lower(), row.cnpj)):
            writer.writerow([row.cnpj, row.decision, row.merge_into, row.name, row.note])


class ShopPrices:
    """Median shop prices per beer and size, from the supermarkets in the same receipts, kept between runs
    (the last SHOP_PRICE_DAYS days)."""

    def __init__(self, path: Path | None, today: date | None = None) -> None:
        self.path = path
        self.today = today or date.today()
        self.prices: dict[str, list[tuple[str, float]]] = {}
        if path is not None and path.exists():
            try:
                saved = json.loads(path.read_text("utf-8"))
                self.prices = {
                    key: [(day, float(price)) for day, price in rows] for key, rows in saved.items()
                }
            except (ValueError, TypeError):
                self.prices = {}
        cutoff = (self.today - timedelta(days=SHOP_PRICE_DAYS)).isoformat()
        self.prices = {key: [row for row in rows if row[0] >= cutoff] for key, rows in self.prices.items()}

    def add(self, key: str, price: float, seen: str) -> None:
        rows = self.prices.setdefault(key, [])
        if (seen, price) not in rows:
            rows.append((seen, price))
            del rows[:-50]

    def median(self, key: str) -> float | None:
        rows = self.prices.get(key, [])
        return statistics.median(price for _, price in rows) if len(rows) >= SHOP_PRICE_MIN else None

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.prices, ensure_ascii=False), "utf-8")


@dataclass(slots=True)
class ShopRecord:
    """What the runs learnt about one shop, kept between runs for the next verdicts and the review."""

    name: str = ""
    bairro: str = ""
    osm: str = ""
    #: Distinct sales of beer by the box, pack or keg (their keys, at most 20).
    packs: list[str] = field(default_factory=list)
    #: Latest price over the median shop price, per beer and size.
    ratios: dict[str, float] = field(default_factory=dict)
    #: A few of its prices, for people: "HEINEKEN 600ML R$ 12,90 (1,4x)".
    samples: list[str] = field(default_factory=list)
    decision: str = ""
    reasons: list[str] = field(default_factory=list)
    #: Sent as a venue by some run, so possibly live on the map.
    sent: bool = False
    seen: str = ""

    def add_pack(self, key: str) -> None:
        if key not in self.packs:
            self.packs = [*self.packs, key][-20:]

    def add_sample(self, text: str) -> None:
        if text not in self.samples:
            self.samples = [*self.samples, text][-5:]


class ShopBook:
    """ShopRecords by CNPJ, in a JSON file between runs."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.records: dict[str, ShopRecord] = {}
        if path is not None and path.exists():
            try:
                saved = json.loads(path.read_text("utf-8"))
                self.records = {cnpj: ShopRecord(**row) for cnpj, row in saved.items()}
            except (ValueError, TypeError):
                self.records = {}

    def get(self, cnpj: str) -> ShopRecord:
        return self.records.setdefault(cnpj, ShopRecord())

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = {cnpj: _asdict(record) for cnpj, record in self.records.items()}
        self.path.write_text(json.dumps(rows, ensure_ascii=False), "utf-8")


def _asdict(record: ShopRecord) -> dict[str, object]:
    return {name: getattr(record, name) for name in ShopRecord.__slots__}


def summary_table(records: dict[str, ShopRecord], cnpjs: Iterable[str]) -> str:
    """Markdown for the run's GitHub summary: the shops held back, with their evidence."""
    rows = [
        f"| {cnpj} | {record.name or '—'} | {record.bairro or '—'} | {record.decision} | "
        f"{'yes' if record.sent else 'no'} | {record.osm or '—'} | {'; '.join(record.reasons) or '—'} | "
        f"{'; '.join(record.samples[:3]) or '—'} |"
        for cnpj in sorted(cnpjs, key=lambda cnpj: (records[cnpj].decision, records[cnpj].name))
        for record in [records[cnpj]]
    ]
    if not rows:
        return ""
    header = (
        "| CNPJ | Name | Bairro | Verdict | Sent before | OpenStreetMap | Why | Prices |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    return "\n".join([header, *rows])
