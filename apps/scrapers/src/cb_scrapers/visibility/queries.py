"""Search queries, brand against the rest. Query texts never leave the run: the report and its one line carry
counts only (the summary of a public repository's run is public)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

#: The brand is also the generic phrase ("cerveja barata"), so every query holding it counts as brand, as the
#: off-page audit's split does (docs/seo/2026-10-14-offpage.md §5.1): "cerveja barata salvador" is brand here.
BRAND = re.compile(r"cerveja\W*barata", re.I)


def is_brand(query: str) -> bool:
    return bool(BRAND.search(query))


@dataclass(slots=True)
class Totals:
    clicks: int = 0
    impressions: int = 0

    def add(self, clicks: float, impressions: float) -> None:
        self.clicks += round(clicks)
        self.impressions += round(impressions)


@dataclass(slots=True)
class QuerySplit:
    brand: Totals = field(default_factory=Totals)
    nonbrand: Totals = field(default_factory=Totals)
    #: Distinct non-brand queries, and how many of them average position 10 or better (a dated gate reads it).
    nonbrand_queries: int = 0
    nonbrand_top10: int = 0

    @property
    def brand_click_share(self) -> float | None:
        clicks = self.brand.clicks + self.nonbrand.clicks
        return self.brand.clicks / clicks if clicks else None


def split_queries(rows: Iterable[tuple[str, float, float, float | None]]) -> QuerySplit:
    """Rows of (query, clicks, impressions, average position) → brand and non-brand totals."""
    split = QuerySplit()
    for query, clicks, impressions, position in rows:
        if is_brand(query):
            split.brand.add(clicks, impressions)
            continue
        split.nonbrand.add(clicks, impressions)
        split.nonbrand_queries += 1
        if position is not None and 0 < position <= 10:
            split.nonbrand_top10 += 1
    return split
