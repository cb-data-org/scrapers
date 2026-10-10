"""The beer catalog adapters match against: the live one from the API when it answers, else the committed
export (data/seed/catalog.json, generated from @cb/domain), so a run never stops for want of a catalog."""

import logging
from pathlib import Path
from typing import Protocol

from ..models import Catalog

log = logging.getLogger(__name__)

CATALOG_FILE = Path(__file__).resolve().parents[5] / "data" / "seed" / "catalog.json"


class CatalogSource(Protocol):
    def catalog(self) -> Catalog | None: ...


def committed_catalog() -> Catalog:
    return Catalog.model_validate_json(CATALOG_FILE.read_text("utf-8"))


def load_catalog(source: CatalogSource) -> Catalog:
    try:
        live = source.catalog()
    except Exception as error:
        log.warning(
            "live catalog unavailable (%s: %s); using the committed export", type(error).__name__, error
        )
        live = None
    return live or committed_catalog()
