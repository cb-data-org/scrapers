"""One build's open-data files, downloaded together: /api/v1/index.json says which files each city has, and
every JSON file names the build that wrote it (`updated_at`). If a deploy lands while the files download,
the snapshot starts again, so the files always come from a single build.

Files are flat, `<city>-<file>` (salvador-deals.csv), because Kaggle and Zenodo have no folders; manifest.json
records each file's source, size, SHA-256 and row count.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core import PoliteClient

SITE = "https://cervejabarata.com.br"
INDEX = "/api/v1/index.json"
CITIES = "/api/v1/cities.json"
LICENSE = "ODbL-1.0"
ATTEMPTS = 2


class Changed(Exception):
    """The site published a new build while the files were downloading."""


@dataclass(slots=True)
class File:
    name: str
    source: str
    #: deals, venues, venues_all, price_index, neighbourhoods, catalog or cities.
    dataset: str
    city: str | None
    format: str
    bytes: int = 0
    sha256: str = ""
    rows: int | None = None


@dataclass(slots=True)
class Manifest:
    #: When the snapshot was taken (UTC) and when the site's build wrote the data.
    taken_at: str
    data_updated_at: str
    site: str = SITE
    license: str = LICENSE
    attribution: str = ""
    cities: list[dict[str, Any]] = field(default_factory=list)
    files: list[File] = field(default_factory=list)

    def file(self, dataset: str, city: str | None, fmt: str) -> File | None:
        return next(
            (f for f in self.files if f.dataset == dataset and f.city == city and f.format == fmt),
            None,
        )

    @staticmethod
    def load(path: Path) -> Manifest:
        data = json.loads(path.read_text())
        files = [File(**entry) for entry in data.pop("files")]
        return Manifest(**data, files=files)


def rows_in(name: str, body: bytes) -> int | None:
    """Data rows: CSV records after the header, a JSON envelope's items, a GeoJSON's features."""
    if name.endswith(".csv"):
        records = list(csv.reader(io.StringIO(body.decode("utf-8-sig"), newline="")))
        return max(len(records) - 1, 0)
    data = json.loads(body)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return len(data["items"])
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        return len(data["features"])
    return None


def _format(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def _dataset(key: str) -> str:
    return key.removesuffix("_csv")


def planned(index: dict[str, Any]) -> list[File]:
    """Every file the index lists, plus the catalog and the cities, in a stable order."""
    files = [
        File(
            name="catalog.json",
            source=str(index.get("catalog") or "/api/v1/catalog.json"),
            dataset="catalog",
            city=None,
            format="json",
        ),
        File(name="cities.json", source=CITIES, dataset="cities", city=None, format="json"),
    ]
    for city in index.get("cities", []):
        slug = str(city["slug"])
        for key, path in sorted(city.get("datasets", {}).items()):
            basename = urlsplit(str(path)).path.rsplit("/", 1)[-1]
            files.append(
                File(
                    name=f"{slug}-{basename}",
                    source=str(path),
                    dataset=_dataset(key),
                    city=slug,
                    format=_format(basename),
                )
            )
    return files


def _download(client: PoliteClient, out: Path, now: datetime) -> Manifest:
    index = client.get(f"{SITE}{INDEX}").json()
    build = str(index["updated_at"])
    manifest = Manifest(
        taken_at=now.isoformat(timespec="seconds"),
        data_updated_at=build,
        attribution=str(index.get("attribution", "")),
        cities=[
            {key: city.get(key) for key in ("slug", "name", "venue_count", "deal_count")}
            for city in index.get("cities", [])
        ],
    )
    for entry in planned(index):
        body = client.get(f"{SITE}{entry.source}").content
        if entry.format == "json":
            data = json.loads(body)
            stamp = data.get("updated_at")
            if stamp is not None and stamp != build:
                raise Changed(f"{entry.source} is from {stamp}, the index from {build}")
            if entry.dataset == "cities":
                # Each city's state (BA), for the cards' coverage line.
                states = {item.get("slug"): item.get("state") for item in data.get("items", [])}
                for city in manifest.cities:
                    city["state"] = states.get(city["slug"])
        entry.source = f"{SITE}{entry.source}"
        entry.bytes = len(body)
        entry.sha256 = hashlib.sha256(body).hexdigest()
        entry.rows = rows_in(entry.name, body)
        (out / entry.name).write_bytes(body)
        manifest.files.append(entry)
    if client.get(f"{SITE}{INDEX}").json().get("updated_at") != build:
        raise Changed("a new build went live during the download")
    return manifest


def take(client: PoliteClient, out: Path, now: datetime | None = None) -> Manifest:
    """The snapshot in out/files with its manifest.json; retried once if a deploy lands in the middle."""
    files = out / "files"
    for attempt in range(ATTEMPTS):
        if files.exists():
            shutil.rmtree(files)
        files.mkdir(parents=True)
        try:
            manifest = _download(client, files, now or datetime.now(UTC))
            break
        except Changed:
            if attempt == ATTEMPTS - 1:
                raise
    (files / "manifest.json").write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n")
    return manifest
