"""Which bairro a venue is in, from the polygons in data/seed/bairros (built by `cb bairros build`)."""

import json
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

SEED_DIR = Path(__file__).resolve().parents[5] / "data" / "seed" / "bairros"
METERS_PER_DEGREE = 111_320.0


def slugify(text: str) -> str:
    """Same rule as `slugify` in @cb/domain: "Caixa D'Água" → "caixa-d-agua" (at most 80 characters)."""
    stripped = re.sub(r"[̀-ͯ]", "", unicodedata.normalize("NFD", text)).lower()
    kept = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$£.,/ ]+", " ", stripped)).strip()
    return re.sub(r"[^a-z0-9]+", "-", kept)[:80].strip("-")


@dataclass(frozen=True)
class Bairro:
    city: str
    slug: str
    name: str
    official_code: str | None = None
    aliases: tuple[str, ...] = ()


class LocalPlane:
    """Equirectangular metres around a reference latitude: accurate to well under 1 % across a city."""

    def __init__(self, latitude: float) -> None:
        self.scale = np.array([METERS_PER_DEGREE * math.cos(math.radians(latitude)), METERS_PER_DEGREE])

    def to_metres(self, geometry: BaseGeometry) -> BaseGeometry:
        return shapely.transform(geometry, lambda coords: coords * self.scale)

    def to_degrees(self, geometry: BaseGeometry) -> BaseGeometry:
        return shapely.transform(geometry, lambda coords: coords / self.scale)


class BairroIndex:
    """Point-in-polygon over bairro polygons (lng/lat GeoJSON features), with a nearest-bairro fallback."""

    def __init__(self, features: Iterable[dict[str, Any]]) -> None:
        features = list(features)
        if not features:
            raise ValueError("no bairro features")
        geometries = [shape(feature["geometry"]) for feature in features]
        bounds = shapely.total_bounds(geometries)
        self.bounds: tuple[float, float, float, float] = tuple(float(value) for value in bounds)  # type: ignore[assignment]
        """(west, south, east, north) of every bairro, in degrees."""
        self._plane = LocalPlane((bounds[1] + bounds[3]) / 2)
        self._bairros = [
            Bairro(
                city=feature["properties"]["city"],
                slug=feature["properties"]["slug"],
                name=feature["properties"]["name"],
                official_code=feature["properties"].get("official_code"),
                aliases=tuple(feature["properties"].get("aliases", [])),
            )
            for feature in features
        ]
        self._tree = shapely.STRtree([self._plane.to_metres(geometry) for geometry in geometries])

    @classmethod
    def from_files(cls, paths: Iterable[Path] | None = None) -> "BairroIndex":
        """Every city in data/seed/bairros unless paths are given."""
        files = sorted(SEED_DIR.glob("*.geojson")) if paths is None else list(paths)
        return cls(feature for path in files for feature in json.loads(path.read_text("utf-8"))["features"])

    @property
    def bairros(self) -> list[Bairro]:
        return list(self._bairros)

    def locate(self, lat: float, lng: float, max_distance_m: float = 300.0) -> Bairro | None:
        """The bairro containing the point; else the nearest within `max_distance_m` (beach barracas sit on
        the sand, outside the official polygons); else None (outside the covered cities)."""
        point = self._plane.to_metres(Point(lng, lat))
        inside = self._tree.query(point, predicate="covered_by")
        if len(inside):
            return self._bairros[int(min(inside))]
        nearest = self._tree.query_nearest(point, max_distance=max_distance_m)
        return self._bairros[int(min(nearest))] if len(nearest) else None
