import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import shapefile
import shapely
from shapely.geometry import shape

from cb_scrapers.core import PoliteClient
from cb_scrapers.geo.build import GEOSALVADOR, IBGE_BAHIA, aliases_for, build, clean_name
from tests.fakes import FakeSite

ACUTE = chr(0xB4)  # an acute accent typed where an apostrophe belongs, as in the GeoSalvador data


def ring(west: float, south: float, size: float = 0.01) -> list[list[float]]:
    """A clockwise square (the shapefile convention for outer rings), about 1.1 km wide."""
    east, north = west + size, south + size
    return [[west, south], [west, north], [east, north], [east, south], [west, south]]


def geosalvador() -> str:
    names = {"Rio Vermelho": -38.50, f"Caixa D{ACUTE}Água": -38.49, "Nordeste de Amaralina": -38.48}
    features = [
        {
            "type": "Feature",
            "properties": {"NOME_BAIRRO": name},
            "geometry": {"type": "Polygon", "coordinates": [ring(west, -13.01)]},
        }
        for name, west in names.items()
    ]
    return json.dumps({"type": "FeatureCollection", "features": features})


def ibge_zip() -> bytes:
    rows = [
        ("2927408", "2927408001", "Caixa D'Água", ring(-38.49, -13.01)),
        ("2927408", "2927408002", "Nordeste do Amaralina", ring(-38.48, -13.01)),
        # Lauro de Freitas: one bairro next to Salvador, one that strays 0.005° into it.
        ("2919207", "2919207001", "Centro", ring(-38.47, -13.01)),
        ("2919207", "2919207005", "Vilas do Atlântico", ring(-38.475, -13.0, 0.01)),
        ("2905206", "2905206001", "Elsewhere", ring(-39.0, -12.0)),
    ]
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON, encoding="utf-8") as writer:
        writer.field("CD_MUN", "C", 7)
        writer.field("CD_BAIRRO", "C", 10)
        writer.field("NM_BAIRRO", "C", 100)
        for municipality, code, name, outer in rows:
            writer.poly([outer])
            writer.record(municipality, code, name)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as files:
        for extension, buffer in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            files.writestr(f"BA_bairros_CD2022.{extension}", buffer.getvalue())
    return archive.getvalue()


def test_names_and_aliases() -> None:
    assert clean_name(f"Caixa  D{ACUTE}Água ") == "Caixa D'Água"
    assert clean_name("Vila Ruy Barbosa\\Jardim Cruzeiro") == "Vila Ruy Barbosa/Jardim Cruzeiro"
    assert aliases_for("salvador", "beiru-tancredo-neves", "Beiru/Tancredo Neves") == [
        "Beiru",
        "Tancredo Neves",
    ]
    assert aliases_for("salvador", "itapua", "Itapuã") == ["Itapoã", "Itapoan"]


def test_build_writes_both_cities(polite: Callable[..., PoliteClient], tmp_path: Path) -> None:
    site = FakeSite(
        {
            "https://geo.salvador.ba.gov.br/robots.txt": "User-agent: *\nAllow: /\n",
            GEOSALVADOR: geosalvador(),
            IBGE_BAHIA: ibge_zip(),
        }
    )
    counts = build(polite(site), tmp_path, today="2026-10-04")
    assert counts == {"salvador": 3, "salvador_codes": 2, "lauro-de-freitas": 2}
    assert any(request.url.params.get("outSR") == "4326" for request in site.requests)

    salvador = json.loads((tmp_path / "salvador.geojson").read_text("utf-8"))
    assert salvador["metadata"]["retrieved"] == "2026-10-04"
    by_slug = {feature["properties"]["slug"]: feature["properties"] for feature in salvador["features"]}
    assert list(by_slug) == ["caixa-d-agua", "nordeste-de-amaralina", "rio-vermelho"]
    assert by_slug["caixa-d-agua"]["name"] == "Caixa D'Água"
    assert by_slug["caixa-d-agua"]["official_code"] == "2927408001"
    assert by_slug["nordeste-de-amaralina"]["official_code"] == "2927408002"
    assert by_slug["rio-vermelho"]["official_code"] is None
    assert "Largo da Mariquita" in by_slug["rio-vermelho"]["aliases"]

    lauro = json.loads((tmp_path / "lauro-de-freitas.geojson").read_text("utf-8"))
    assert [feature["properties"]["slug"] for feature in lauro["features"]] == [
        "centro",
        "vilas-do-atlantico",
    ]
    salvador_area = shapely.union_all([shape(feature["geometry"]) for feature in salvador["features"]])
    for feature in lauro["features"]:
        # Salvador's own boundaries win where the two sources overlap.
        assert shape(feature["geometry"]).intersection(salvador_area).area < 1e-9
    assert (tmp_path / "salvador.geojson").read_text("utf-8").count("\n") == 5
