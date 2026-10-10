"""Company facts from the CNPJ register (Receita Federal's open data): what a business mainly does (its CNAE),
to tell bars and restaurants from supermarkets, and the trade name it goes by.

Looked up on minhareceita.org, else on CNPJá's open API (a few a minute); BrasilAPI is not used because
its robots.txt disallows /api/ for every bot. Answers are kept in a JSON file between runs (CB_CACHE_DIR; the
daily workflow caches the folder) and asked again after REFRESH_DAYS. Only what the scrapers need is kept,
and never a one-person business's legal name, which is its owner's name.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .core import BlockedError, DisallowedError, PoliteClient
from .core.cache import cache_dir

log = logging.getLogger(__name__)

MINHA_RECEITA = "https://minhareceita.org/{cnpj}"
CNPJA = "https://open.cnpja.com/office/{cnpj}"
#: Seconds between two lookups on each service (CNPJá's open API allows a handful a minute).
DELAYS = {MINHA_RECEITA: 3.0, CNPJA: 13.0}
REFRESH_DAYS = 90
#: Legal natures of one-person businesses (Receita Federal's table): empresário individual, EIRELI, …
INDIVIDUAL = {2135, 2305, 2313, 2321, 4014, 4081, 4090, 4111, 4120}
ACTIVE = 2


@dataclass(frozen=True, slots=True)
class Company:
    cnpj: str
    #: Main activity, e.g. 5611204 (bars without entertainment).
    cnae: int | None
    active: bool
    trade_name: str | None
    #: None for one-person businesses.
    legal_name: str | None
    individual: bool
    checked_on: str
    #: Secondary activities (CNAE codes).
    secondary: tuple[int, ...] = ()
    #: A micro-entrepreneur (MEI): a very small one-person business.
    mei: bool = False


def default_cache_file() -> Path:
    return cache_dir() / "cnpj.json"


def _name(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text if text.strip("*-. ") else None


def _codes(activities: Any, key: str) -> tuple[int, ...]:
    return tuple(
        int(activity[key]) for activity in activities or [] if str(activity.get(key) or "").isdigit()
    )


def from_minha_receita(cnpj: str, body: dict[str, Any], today: date) -> Company:
    nature = int(body.get("codigo_natureza_juridica") or 0)
    mei = bool(body.get("opcao_pelo_mei"))
    individual = nature in INDIVIDUAL or mei
    return Company(
        cnpj=cnpj,
        cnae=int(body["cnae_fiscal"]) if body.get("cnae_fiscal") else None,
        active=body.get("situacao_cadastral") == ACTIVE,
        trade_name=_name(body.get("nome_fantasia")),
        legal_name=None if individual else _name(body.get("razao_social")),
        individual=individual,
        checked_on=today.isoformat(),
        secondary=_codes(body.get("cnaes_secundarios"), "codigo"),
        mei=mei,
    )


def from_cnpja(cnpj: str, body: dict[str, Any], today: date) -> Company:
    company = body.get("company") or {}
    nature = int((company.get("nature") or {}).get("id") or 0)
    mei = bool((company.get("simei") or {}).get("optant"))
    individual = nature in INDIVIDUAL or mei
    activity = (body.get("mainActivity") or {}).get("id")
    return Company(
        cnpj=cnpj,
        cnae=int(activity) if activity else None,
        active=(body.get("status") or {}).get("id") == ACTIVE,
        trade_name=_name(body.get("alias")),
        legal_name=None if individual else _name(company.get("name")),
        individual=individual,
        checked_on=today.isoformat(),
        secondary=_codes(body.get("sideActivities"), "id"),
        mei=mei,
    )


SERVICES = ((MINHA_RECEITA, from_minha_receita), (CNPJA, from_cnpja))


#: Failures (refusals, timeouts, garbage) after which a service isn't asked again in the run.
MAX_FAILURES = 3


class CnpjRegister:
    """CNPJ → Company, from the cache file when fresh, else from the first service that answers. A service
    that keeps failing isn't asked again in the run."""

    def __init__(self, http: PoliteClient, cache_file: Path | None = None, today: date | None = None) -> None:
        self.http = http
        self.cache_file = cache_file
        self.today = today or date.today()
        self.known = self._load()
        self.failures: dict[str, int] = {}
        self.lookups = 0

    def lookup(self, cnpj: str) -> Company | None:
        cached = self.known.get(cnpj)
        if cached and (self.today - date.fromisoformat(cached.checked_on)).days < REFRESH_DAYS:
            return cached
        for template, read in SERVICES:
            if self.failures.get(template, 0) >= MAX_FAILURES:
                continue
            url = template.format(cnpj=cnpj)
            try:
                self.lookups += 1
                response = self.http.get(url, delay=DELAYS[template], retries=1, timeout=30.0)
                company = read(cnpj, response.json(), self.today)
            except httpx.HTTPStatusError as error:
                if error.response.status_code in (400, 404):
                    # The register doesn't know it: no point asking another service.
                    return None
                self._give_up(template, error)
            except (BlockedError, DisallowedError, httpx.HTTPError, ValueError) as error:
                self._give_up(template, error)
            else:
                self.known[cnpj] = company
                self._save()
                return company
        # Stale beats nothing when every service is down.
        return cached

    def _give_up(self, template: str, error: Exception) -> None:
        failures = self.failures[template] = self.failures.get(template, 0) + 1
        last = " (not asked again this run)" if failures >= MAX_FAILURES else ""
        log.warning("%s: %s%s", urlsplit(template).netloc, error, last)

    def _load(self) -> dict[str, Company]:
        if self.cache_file is None or not self.cache_file.exists():
            return {}
        try:
            rows = json.loads(self.cache_file.read_text("utf-8"))
            # Rows from before secondary activities were kept are asked again.
            return {
                cnpj: Company(**{**row, "secondary": tuple(row["secondary"])})
                for cnpj, row in rows.items()
                if "secondary" in row
            }
        except (ValueError, TypeError) as error:
            log.warning("ignoring the CNPJ cache %s: %s", self.cache_file, error)
            return {}

    def _save(self) -> None:
        if self.cache_file is None:
            return
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        rows = {cnpj: asdict(company) for cnpj, company in sorted(self.known.items())}
        self.cache_file.write_text(json.dumps(rows, ensure_ascii=False, indent=0), "utf-8")
