"""`cb bairros build`: rebuild data/seed/bairros from the official boundaries (by hand, when they change).

- Salvador: GeoSalvador (Prefeitura Municipal de Salvador), the 171 bairros of Lei 9.278/2017 with Decretos
  32.791/2020 and 38.776/2024; IBGE codes (Censo 2022) where the names match.
- Lauro de Freitas: IBGE, bairros of the 2022 Census.

All bairros are simplified together as one coverage, so neighbours keep meeting without gaps or overlaps:
Visvalingam-Whyatt with a 20 m tolerance (half the bairros move under 13 m, none more than ~35 m), then
rounded to 5 decimals (~1 m). Files hold one feature per line, sorted by slug, so a boundary change shows up
as a small diff.
"""

import io
import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import shapefile
import shapely
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

from ..core.http import PoliteClient
from .bairros import SEED_DIR, LocalPlane, slugify

log = logging.getLogger(__name__)

GEOSALVADOR = "https://geo.salvador.ba.gov.br/arcgis/rest/services/Bairros/MapServer/0/query"
IBGE_BAHIA = (
    "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/"
    "malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/bairros/shp/UF/BA_bairros_CD2022.zip"
)
SALVADOR, LAURO_DE_FREITAS = "2927408", "2919207"
TOLERANCE_M = 20.0
PRECISION = 5

SOURCES = {
    "salvador": {
        "source": "GeoSalvador, Prefeitura Municipal de Salvador",
        "url": GEOSALVADOR,
        "notes": (
            "Lei 9.278/2017, Decretos 32.791/2020 e 38.776/2024; "
            "códigos IBGE (Censo 2022) quando o nome coincide"
        ),
    },
    "lauro-de-freitas": {"source": "IBGE, Censo Demográfico 2022 (bairros)", "url": IBGE_BAHIA},
}

# Other names people write for a bairro: spellings, and landmarks inside it. "A/B" names also get both parts.
ALIASES: dict[tuple[str, str], list[str]] = {
    ("salvador", "barra"): ["Porto da Barra", "Farol da Barra"],
    ("salvador", "centro-administrativo-da-bahia"): ["CAB"],
    ("salvador", "centro-historico"): ["Pelourinho", "Pelô"],
    ("salvador", "dois-de-julho"): ["2 de Julho"],
    ("salvador", "itapua"): ["Itapoã", "Itapoan"],
    ("salvador", "jardim-armacao"): ["Armação"],
    ("salvador", "rio-vermelho"): ["Largo da Mariquita", "Largo de Santana"],
    ("salvador", "santo-antonio"): ["Santo Antônio Além do Carmo"],
    ("salvador", "sete-de-abril"): ["7 de Abril"],
    ("salvador", "stella-maris"): ["Stela Maris"],
    ("salvador", "trobogy"): ["Trobogi"],
    ("lauro-de-freitas", "centro"): ["Centro de Lauro de Freitas"],
    ("lauro-de-freitas", "vilas-do-atlantico"): ["Vilas"],
}


# IBGE spellings of Salvador bairros that differ from the municipal names (slug → slug).
IBGE_NAMES = {
    "centro-administrativo-da-bahia-cab": "centro-administrativo-da-bahia",
    "ilha-bom-jesus-dos-passos": "ilha-de-bom-jesus-dos-passos",
    "nordeste-do-amaralina": "nordeste-de-amaralina",
}


def clean_name(raw: str) -> str:
    """Official names as people read them: an acute accent standing in for an apostrophe becomes one, and
    "A\\B" becomes "A/B" like the other double names."""
    return " ".join(raw.replace("\u00b4", "'").replace("\\", "/").split())


def aliases_for(city: str, slug: str, name: str) -> list[str]:
    parts = [part.strip() for part in name.split("/")] if "/" in name else []
    return list(dict.fromkeys(parts + ALIASES.get((city, slug), [])))


def _rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, PRECISION)
    if isinstance(value, list | tuple):
        return [_rounded(item) for item in value]
    return value


def _without_slivers(geometry: BaseGeometry, min_area_m2: float = 1_000.0) -> BaseGeometry:
    if geometry.geom_type != "MultiPolygon":
        return geometry
    parts = [part for part in geometry.geoms if part.area >= min_area_m2]
    return parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)


def simplify_coverage(
    salvador: list[BaseGeometry], lauro: list[BaseGeometry], plane: LocalPlane
) -> tuple[list[BaseGeometry], list[BaseGeometry]]:
    """Simplify both cities' bairros as one coverage, so every shared border (the city line too) is simplified
    once. The two sources disagree slightly along the city line; Salvador's own boundaries win there."""
    ssa = [plane.to_metres(shapely.make_valid(geometry)) for geometry in salvador]
    city = shapely.union_all(ssa)
    ldf = [
        _without_slivers(plane.to_metres(shapely.make_valid(geometry)).difference(city)) for geometry in lauro
    ]
    simplified = [
        plane.to_degrees(geometry) for geometry in shapely.coverage_simplify(ssa + ldf, TOLERANCE_M)
    ]
    return simplified[: len(ssa)], simplified[len(ssa) :]


def label_point(geometry: BaseGeometry, plane: LocalPlane) -> tuple[float, float]:
    """(lat, lng) well inside the bairro, for labels and searches: the pole of inaccessibility of its largest
    part."""
    metres = plane.to_metres(geometry)
    largest = max(metres.geoms, key=lambda part: part.area) if metres.geom_type == "MultiPolygon" else metres
    point = plane.to_degrees(polylabel(largest, tolerance=5.0))
    return round(point.y, PRECISION), round(point.x, PRECISION)


def feature(
    city: str, name: str, code: str | None, simple: BaseGeometry, plane: LocalPlane
) -> dict[str, Any]:
    """A simplified bairro as a GeoJSON feature with the columns of the neighbourhoods table."""
    slug = slugify(name)
    if simple.is_empty or simple.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"{city}/{slug}: simplification left a {simple.geom_type}")
    geometry = {"type": simple.geom_type, "coordinates": _rounded(mapping(simple)["coordinates"])}
    if not shape(geometry).is_valid:
        raise ValueError(f"{city}/{slug}: invalid after rounding")
    lat, lng = label_point(simple, plane)
    return {
        "type": "Feature",
        "properties": {
            "city": city,
            "slug": slug,
            "name": name,
            "official_code": code,
            "aliases": aliases_for(city, slug, name),
            "centroid_lat": lat,
            "centroid_lng": lng,
            "bbox": _rounded(list(simple.bounds)),
        },
        "geometry": geometry,
    }


def write(path: Path, city: str, features: list[dict[str, Any]], retrieved: str) -> None:
    features = sorted(features, key=lambda item: item["properties"]["slug"])
    slugs = [item["properties"]["slug"] for item in features]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ValueError(f"duplicate slugs in {city}: {duplicates}")
    metadata = {**SOURCES[city], "retrieved": retrieved, "simplified_m": TOLERANCE_M, "decimals": PRECISION}
    head = {"type": "FeatureCollection", "name": city, "metadata": metadata}
    lines = ",\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in features)
    path.write_text(f'{json.dumps(head, ensure_ascii=False)[:-1]}, "features": [\n{lines}\n]}}\n', "utf-8")


def fetch_salvador(client: PoliteClient) -> list[tuple[str, BaseGeometry]]:
    params = {"where": "1=1", "outFields": "NOME_BAIRRO", "outSR": "4326", "f": "geojson"}
    data = client.get(GEOSALVADOR, params=params).json()
    if data.get("exceededTransferLimit"):
        raise ValueError("GeoSalvador returned a partial result")
    return [
        (clean_name(item["properties"]["NOME_BAIRRO"]), shape(item["geometry"])) for item in data["features"]
    ]


def fetch_ibge(client: PoliteClient) -> list[tuple[dict[str, Any], BaseGeometry]]:
    """Every bairro in Bahia from IBGE's 2022 shapefile (SIRGAS 2000, the same as WGS 84 at this scale)."""
    with zipfile.ZipFile(io.BytesIO(client.get(IBGE_BAHIA).content)) as archive:
        base = next(name[:-4] for name in archive.namelist() if name.lower().endswith(".shp"))
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{base}.shp")),
            shx=io.BytesIO(archive.read(f"{base}.shx")),
            dbf=io.BytesIO(archive.read(f"{base}.dbf")),
            encoding="utf-8",
        )
        return [
            (row.record.as_dict(), shape(row.shape.__geo_interface__)) for row in reader.iterShapeRecords()
        ]


def build(client: PoliteClient, out_dir: Path = SEED_DIR, today: str | None = None) -> dict[str, int]:
    retrieved = today or datetime.now(UTC).date().isoformat()
    plane = LocalPlane(-12.95)
    ibge = fetch_ibge(client)
    codes = {
        IBGE_NAMES.get(slug, slug): row["CD_BAIRRO"]
        for row, _ in ibge
        if row["CD_MUN"] == SALVADOR and (slug := slugify(clean_name(row["NM_BAIRRO"])))
    }
    ssa = fetch_salvador(client)
    ldf = [
        (clean_name(row["NM_BAIRRO"]), row["CD_BAIRRO"], geometry)
        for row, geometry in ibge
        if row["CD_MUN"] == LAURO_DE_FREITAS
    ]
    ssa_shapes, ldf_shapes = simplify_coverage(
        [geometry for _, geometry in ssa], [item[2] for item in ldf], plane
    )
    salvador = [
        feature("salvador", name, codes.get(slugify(name)), simple, plane)
        for (name, _), simple in zip(ssa, ssa_shapes, strict=True)
    ]
    lauro = [
        feature("lauro-de-freitas", name, code, simple, plane)
        for (name, code, _), simple in zip(ldf, ldf_shapes, strict=True)
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    write(out_dir / "salvador.geojson", "salvador", salvador, retrieved)
    write(out_dir / "lauro-de-freitas.geojson", "lauro-de-freitas", lauro, retrieved)
    matched = sum(1 for item in salvador if item["properties"]["official_code"])
    log.info(
        "salvador: %d bairros (%d with IBGE codes) · lauro-de-freitas: %d", len(salvador), matched, len(lauro)
    )
    return {"salvador": len(salvador), "salvador_codes": matched, "lauro-de-freitas": len(lauro)}
