"""The API contract as pydantic models, generated from packages/contracts/openapi.json.

Never edit contract.py by hand: change the zod schemas in packages/contracts, run
`pnpm --filter @cb/contracts generate`, then `uv run datamodel-codegen` here.
"""

from .contract import (
    Catalog,
    CatalogBeer,
    CatalogBrand,
    CatalogFormat,
    DealDraft,
    IngestItem,
    IngestRequest,
    IngestResponse,
    LatestRuns,
    ScheduleDraft,
    ScrapeRunReport,
    VenueDraft,
)

__all__ = [
    "Catalog",
    "CatalogBeer",
    "CatalogBrand",
    "CatalogFormat",
    "DealDraft",
    "IngestItem",
    "IngestRequest",
    "IngestResponse",
    "LatestRuns",
    "ScheduleDraft",
    "ScrapeRunReport",
    "VenueDraft",
]
