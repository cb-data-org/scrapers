"""The open-data snapshot and its mirrors (cb_scrapers.opendata). The site's files were recorded on 15 Oct
2026 and trimmed (Salvador only, a few rows each); Zenodo's answers follow its documented deposit API (a token
is needed to record real ones); Hugging Face and Kaggle are stood in for by fakes of their client and CLI."""

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from cb_scrapers.core import PoliteClient
from cb_scrapers.opendata import cards, publish, snapshot
from cb_scrapers.opendata.__main__ import main
from cb_scrapers.visibility.http import Api
from tests.fakes import FakeClock, FakeSite

FIXTURES = Path(__file__).parent / "fixtures" / "opendata"
SITE = "https://cervejabarata.com.br"
BUILD = "2026-10-15T03:01:42.538Z"
ZENODO_API = "https://zenodo.org/api/deposit/depositions"

Polite = Callable[..., PoliteClient]


def site_routes() -> dict[str, Any]:
    """The site's /api/v1 files as recorded, and a robots.txt that allows them."""
    routes: dict[str, Any] = {f"{SITE}/robots.txt": "User-agent: *\nAllow: /\nDisallow: /api/admin/\n"}
    for path in (FIXTURES / "api").rglob("*"):
        if path.is_file():
            routes[f"{SITE}/{path.relative_to(FIXTURES).as_posix()}"] = path.read_bytes()
    return routes


@pytest.fixture
def snap(tmp_path: Path, polite: Polite) -> tuple[Path, snapshot.Manifest]:
    manifest = snapshot.take(polite(FakeSite(site_routes())), tmp_path)
    cards.write(tmp_path, manifest)
    return tmp_path, manifest


def test_snapshot_files_and_manifest(snap: tuple[Path, snapshot.Manifest]) -> None:
    out, manifest = snap
    assert manifest.data_updated_at == BUILD and manifest.license == "ODbL-1.0"
    assert "Preço da Hora Bahia" in manifest.attribution and "OpenStreetMap" in manifest.attribution
    city = {"slug": "salvador", "name": "Salvador", "venue_count": 3, "deal_count": 3, "state": "BA"}
    assert manifest.cities == [city]
    names = [f.name for f in manifest.files]
    assert names[:2] == ["catalog.json", "cities.json"] and len(names) == 11
    assert "salvador-deals.csv" in names and "salvador-neighbourhoods.geojson" in names
    rows = {f.name: f.rows for f in manifest.files}
    assert rows["salvador-deals.csv"] == rows["salvador-deals.json"] == 3
    assert (rows["salvador-venues.csv"], rows["salvador-venues-all.json"]) == (2, 3)
    assert (rows["salvador-price-index.csv"], rows["salvador-neighbourhoods.geojson"]) == (0, 2)
    assert (rows["cities.json"], rows["catalog.json"]) == (1, None)
    deals = manifest.file("deals", "salvador", "csv")
    assert deals is not None and deals.source == f"{SITE}/api/v1/salvador/deals.csv"
    body = (out / "files" / "salvador-deals.csv").read_bytes()
    assert deals.sha256 == hashlib.sha256(body).hexdigest() and deals.bytes == len(body)
    saved = snapshot.Manifest.load(out / "files" / "manifest.json")
    assert saved == manifest


def test_a_deploy_during_the_download_starts_it_again(tmp_path: Path, polite: Polite) -> None:
    routes = site_routes()
    builds = {"now": BUILD}
    calls = {"index": 0}

    def restamp(path: str) -> Callable[[httpx.Request], httpx.Response]:
        def answer(request: httpx.Request) -> httpx.Response:
            data = json.loads(routes[path])
            data["updated_at"] = builds["now"]
            return httpx.Response(200, json=data)

        return answer

    def index(request: httpx.Request) -> httpx.Response:
        calls["index"] += 1
        if calls["index"] == 2:
            builds["now"] = "2026-10-15T09:30:00.000Z"
        data = json.loads(routes[f"{SITE}/api/v1/index.json"])
        data["updated_at"] = builds["now"]
        return httpx.Response(200, json=data)

    site = dict(routes)
    for path in routes:
        if path.endswith((".json", ".geojson")) and path != f"{SITE}/api/v1/index.json":
            site[path] = restamp(path)
    site[f"{SITE}/api/v1/index.json"] = index
    manifest = snapshot.take(polite(FakeSite(site)), tmp_path)
    assert calls["index"] == 4
    assert manifest.data_updated_at == "2026-10-15T09:30:00.000Z"


def test_files_from_two_builds_are_refused(tmp_path: Path, polite: Polite) -> None:
    routes = site_routes()
    stale = json.loads(routes[f"{SITE}/api/v1/salvador/venues.json"])
    stale["updated_at"] = "2026-10-14T03:00:00.000Z"
    routes[f"{SITE}/api/v1/salvador/venues.json"] = json.dumps(stale)
    with pytest.raises(snapshot.Changed, match=r"venues\.json is from 2026-10-14"):
        snapshot.take(polite(FakeSite(routes)), tmp_path)


def test_rows_in() -> None:
    assert snapshot.rows_in("a.csv", '﻿id,name\r\n1,"Bar\r\nda Esquina"\r\n2,x\r\n'.encode()) == 2
    assert snapshot.rows_in("a.csv", "﻿id,name\r\n".encode()) == 0
    assert snapshot.rows_in("a.json", b'{"items": [1, 2]}') == 2
    assert snapshot.rows_in("a.geojson", b'{"features": [{}]}') == 1
    assert snapshot.rows_in("a.json", b'{"brands": []}') is None


# Cards ----------------------------------------------------------------------------------------------------


def test_dataset_card(snap: tuple[Path, snapshot.Manifest]) -> None:
    yaml = pytest.importorskip("yaml")
    out, _ = snap
    card = (out / "README.md").read_text()
    _, header, body = card.split("---\n", 2)
    meta = yaml.safe_load(header)
    assert meta["license"] == "odbl" and meta["language"] == ["pt", "en"]
    assert meta["pretty_name"] == "Cerveja Barata: preços de cerveja (dados abertos)"
    assert meta["size_categories"] == ["n<1K"]
    tables = {config["config_name"]: config for config in meta["configs"]}
    # The price index has no rows yet: no table for it.
    assert list(tables) == ["deals", "venues", "venues_all"] and tables["deals"]["default"] is True
    assert tables["deals"]["data_files"] == [{"split": "salvador", "path": "salvador-deals.csv"}]
    # Both languages, the link back, the citation with the data's date, the licence and every source.
    assert body.startswith("# Cerveja Barata: preços de cerveja (dados abertos)")
    assert "## English" in body and "https://cervejabarata.com.br/dados" in body
    assert (
        "`Cerveja Barata (cervejabarata.com.br), dados de 15 de outubro de 2026, licença ODbL 1.0.`" in body
    )
    assert "`Cerveja Barata (cervejabarata.com.br), data from 15 October 2026, ODbL 1.0 licence.`" in body
    for credit in (
        "Preço da Hora Bahia",
        "SEFAZ-BA",
        "OpenStreetMap",
        "Overture Maps",
        "GeoSalvador",
        "IBGE",
    ):
        assert body.count(credit) >= 2, credit
    assert "| `salvador-deals.csv` |" in body and "| 3 |" in body
    assert "Nenhum dado pessoal é publicado" in body and "No personal data is published" in body
    # A frozen copy, with its coverage; the house warning and a contact in each language.
    assert (
        "Esta é uma cópia congelada dos dados abertos do site, como estavam em 15 de outubro de 2026." in body
    )
    assert "Cobertura: Salvador (Bahia)." in body and "Coverage: Salvador (Bahia), Brazil." in body
    assert "mensal" not in body.split("## English")[0].split("| Arquivo")[0]
    assert "Contato: contato@cervejabarata.com.br" in body and "Contact: contato@cervejabarata.com.br" in body
    assert "*Beba com moderação · Se beber, não dirija · Proibido para menores de 18 anos*" in body
    assert "*Drink responsibly · Don't drink and drive · Not for anyone under 18*" in body


def test_kaggle_metadata(snap: tuple[Path, snapshot.Manifest]) -> None:
    out, manifest = snap
    meta = json.loads((out / "dataset-metadata.json").read_text())
    assert 6 <= len(meta["title"]) <= 50 and 20 <= len(meta["subtitle"]) <= 80
    assert meta["subtitle"] == "Beer prices per litre in bars and shops in Salvador, Bahia"
    assert meta["id"] == "cervejabarata/precos-de-cerveja"
    assert meta["licenses"] == [{"name": "ODbL-1.0"}]
    assert [r["path"] for r in meta["resources"]] == [f.name for f in manifest.files]
    assert meta["description"] == cards.body(manifest)
    assert meta["userSpecifiedSources"] == manifest.attribution
    assert cards.kaggle_metadata(manifest, "someone/other")["id"] == "someone/other"


def test_zenodo_metadata(snap: tuple[Path, snapshot.Manifest]) -> None:
    out, _ = snap
    meta = json.loads((out / ".zenodo.json").read_text())
    assert meta["upload_type"] == "dataset" and meta["access_right"] == "open"
    assert meta["license"] == "odbl-1.0" and meta["creators"] == [{"name": "Cerveja Barata"}]
    assert (meta["version"], meta["publication_date"], meta["language"]) == (
        "2026-10-15",
        "2026-10-15",
        "por",
    )
    assert {
        "identifier": "https://cervejabarata.com.br/dados",
        "relation": "isDerivedFrom",
        "resource_type": "dataset",
    } in meta["related_identifiers"]
    assert "Preço da Hora Bahia" in meta["description"] and "&amp;" not in meta["title"]
    assert '<a href="https://cervejabarata.com.br/dados">' in meta["description"]


def test_bahia_dates() -> None:
    manifest = snapshot.Manifest(taken_at="", data_updated_at="2026-11-01T02:30:00Z")
    # 23:30 on 31 October in Salvador.
    assert cards.date_pt(cards.data_date(manifest)) == "31 de outubro de 2026"
    assert cards.date_en(cards.data_date(manifest)) == "31 October 2026"
    first = snapshot.Manifest(taken_at="", data_updated_at="2026-11-01T12:00:00Z")
    assert cards.date_pt(cards.data_date(first)) == "1º de novembro de 2026"


def test_coverage_and_subtitle() -> None:
    two = snapshot.Manifest(
        taken_at="",
        data_updated_at=BUILD,
        cities=[
            {"slug": "salvador", "name": "Salvador", "state": "BA"},
            {"slug": "lauro-de-freitas", "name": "Lauro de Freitas", "state": "BA"},
        ],
    )
    assert cards.coverage(two, "pt") == "Salvador e Lauro de Freitas (Bahia)"
    assert cards.coverage(two, "en") == "Salvador and Lauro de Freitas (Bahia)"
    assert (
        cards.subtitle(two)
        == "Beer prices per litre in bars and shops in Salvador and Lauro de Freitas, Bahia"
    )
    many = snapshot.Manifest(
        taken_at="",
        data_updated_at=BUILD,
        cities=[*two.cities, {"slug": "recife", "name": "Recife", "state": "PE"}],
    )
    assert cards.coverage(many, "pt") == "Salvador, Lauro de Freitas e Recife (Bahia e PE)"
    # Too long for Kaggle's 80 characters: the national one.
    assert cards.subtitle(many) == cards.SUBTITLE_FALLBACK


def test_zenodo_mentions_the_index_once_it_has_a_month(snap: tuple[Path, snapshot.Manifest]) -> None:
    _, manifest = snap
    assert "index" not in cards.zenodo_metadata(manifest)["description"]
    cell = next(f for f in manifest.files if f.dataset == "price_index" and f.format == "csv")
    cell.rows = 12
    description = cards.zenodo_metadata(manifest)["description"]
    assert "a monthly index of median prices per litre in bars" in description
    assert "um índice mensal do preço mediano por litro nos bares" in description


# Publishing -----------------------------------------------------------------------------------------------


class FakeHub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.uploaded: list[str] = []

    def create_repo(self, repo_id: str, **kwargs: Any) -> None:
        self.calls.append(("create_repo", {"repo_id": repo_id, **kwargs}))

    def upload_folder(self, **kwargs: Any) -> None:
        self.uploaded = sorted(path.name for path in Path(kwargs["folder_path"]).iterdir())
        self.calls.append(("upload_folder", kwargs))

    def create_tag(self, repo_id: str, **kwargs: Any) -> None:
        self.calls.append(("create_tag", {"repo_id": repo_id, **kwargs}))


def test_hugging_face(snap: tuple[Path, snapshot.Manifest]) -> None:
    out, manifest = snap
    hub = FakeHub()
    url = publish.huggingface(out, manifest, "cervejabarata/precos-de-cerveja", "hf-token", api=hub)
    assert url == "https://huggingface.co/datasets/cervejabarata/precos-de-cerveja"
    assert [name for name, _ in hub.calls] == ["create_repo", "upload_folder", "create_tag"]
    assert hub.calls[0][1] == {
        "repo_id": "cervejabarata/precos-de-cerveja",
        "repo_type": "dataset",
        "exist_ok": True,
    }
    upload = hub.calls[1][1]
    assert (
        upload["repo_type"] == "dataset"
        and upload["commit_message"] == "Snapshot of the open data, 2026-10-15"
    )
    assert upload["delete_patterns"] == ["*.csv", "*.json", "*.geojson"]
    assert (
        "README.md" in hub.uploaded
        and "manifest.json" in hub.uploaded
        and "salvador-deals.csv" in hub.uploaded
    )
    assert "dataset-metadata.json" not in hub.uploaded and ".zenodo.json" not in hub.uploaded
    assert hub.calls[2][1] == {
        "repo_id": "cervejabarata/precos-de-cerveja",
        "tag": "2026-10-15",
        "repo_type": "dataset",
        "exist_ok": True,
    }


class FakeCli:
    def __init__(self, exists: bool, fail: bool = False) -> None:
        self.exists = exists
        self.fail = fail
        self.commands: list[list[str]] = []
        self.staged: list[str] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if "status" in command:
            return subprocess.CompletedProcess(
                command, 0 if self.exists else 1, "ready" if self.exists else "", ""
            )
        self.staged = sorted(path.name for path in Path(command[command.index("-p") + 1]).iterdir())
        if self.fail:
            return subprocess.CompletedProcess(command, 1, "", "401 Unauthorized: invalid token\n")
        return subprocess.CompletedProcess(command, 0, "Dataset version is being created", "")


def test_kaggle_new_version(snap: tuple[Path, snapshot.Manifest]) -> None:
    out, manifest = snap
    cli = FakeCli(exists=True)
    url = publish.kaggle(out, manifest, "cervejabarata/precos-de-cerveja", ("kaggle",), run=cli)
    assert url == "https://www.kaggle.com/datasets/cervejabarata/precos-de-cerveja"
    assert cli.commands[0] == ["kaggle", "datasets", "status", "cervejabarata/precos-de-cerveja"]
    version = cli.commands[1]
    assert version[:3] == ["kaggle", "datasets", "version"]
    assert version[version.index("-m") + 1] == "Snapshot of the open data, 2026-10-15"
    assert version[version.index("-r") + 1] == "skip"
    assert "dataset-metadata.json" in cli.staged and "README.md" not in cli.staged


def test_kaggle_first_time_and_failure(snap: tuple[Path, snapshot.Manifest]) -> None:
    out, manifest = snap
    cli = FakeCli(exists=False)
    publish.kaggle(
        out,
        manifest,
        "cervejabarata/precos-de-cerveja",
        ("uvx", "--from", "kaggle==2.2.4", "kaggle"),
        run=cli,
    )
    assert cli.commands[0][:6] == ["uvx", "--from", "kaggle==2.2.4", "kaggle", "datasets", "status"]
    assert cli.commands[1][4:6] == ["datasets", "create"] and "-u" in cli.commands[1]
    with pytest.raises(RuntimeError, match="kaggle version failed: 401 Unauthorized"):
        publish.kaggle(out, manifest, "x/y", run=FakeCli(exists=True, fail=True))


class FakeZenodo:
    """Zenodo's deposit API: the list, a new version or a first deposition, files, metadata, publish."""

    def __init__(self, depositions: list[Any]) -> None:
        self.depositions = depositions
        self.requests: list[httpx.Request] = []
        self.uploads: dict[str, bytes] = {}

    @staticmethod
    def recorded(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer zenodo-token"
        path, method = request.url.path, request.method
        if path == "/api/deposit/depositions" and method == "GET":
            return httpx.Response(200, json=self.depositions)
        if path == "/api/deposit/depositions" and method == "POST":
            return httpx.Response(201, json=self.recorded("zenodo-created.json"))
        if path.endswith("/actions/newversion"):
            return httpx.Response(201, json=self.recorded("zenodo-newversion.json"))
        if path == "/api/deposit/depositions/1240" and method == "GET":
            return httpx.Response(200, json=self.recorded("zenodo-draft.json"))
        if "/files/" in path and method == "DELETE":
            return httpx.Response(204)
        if path.startswith("/api/files/bucket-") and method == "PUT":
            assert request.headers["content-type"] == "application/octet-stream"
            self.uploads[path.rsplit("/", 1)[-1]] = request.content
            return httpx.Response(201, json={"key": path.rsplit("/", 1)[-1]})
        if method == "PUT":
            return httpx.Response(200, json={"id": int(path.rsplit("/", 1)[-1])})
        if path.endswith("/actions/publish"):
            return httpx.Response(202, json=self.recorded("zenodo-published.json"))
        return httpx.Response(404, json={"status": 404, "message": "PID does not exist."})


def zenodo_api(fake: FakeZenodo, clock: FakeClock) -> Api:
    return Api(transport=httpx.MockTransport(fake), clock=clock, sleep=clock.sleep)


def test_zenodo_new_version(snap: tuple[Path, snapshot.Manifest], clock: FakeClock) -> None:
    out, manifest = snap
    fake = FakeZenodo(FakeZenodo.recorded("zenodo-depositions.json"))
    record = publish.zenodo(zenodo_api(fake, clock), out, "zenodo-token")
    assert record == {
        "doi": "10.5281/zenodo.1240",
        "concept_doi": "10.5281/zenodo.1233",
        "url": "https://zenodo.org/records/1240",
    }
    steps = [(r.method, r.url.path) for r in fake.requests]
    # The newest published deposition that links back to /dados (1234), not the unrelated or the draft one.
    assert steps[:3] == [
        ("GET", "/api/deposit/depositions"),
        ("POST", "/api/deposit/depositions/1234/actions/newversion"),
        ("GET", "/api/deposit/depositions/1240"),
    ]
    assert ("DELETE", "/api/deposit/depositions/1240/files/f-deals") in steps
    assert ("DELETE", "/api/deposit/depositions/1240/files/f-manifest") in steps
    assert sorted(fake.uploads) == sorted([f.name for f in manifest.files] + ["manifest.json", "README.md"])
    assert fake.uploads["salvador-deals.csv"] == (out / "files" / "salvador-deals.csv").read_bytes()
    metadata = next(
        r for r in fake.requests if r.method == "PUT" and r.url.path == "/api/deposit/depositions/1240"
    )
    assert json.loads(metadata.content) == {"metadata": json.loads((out / ".zenodo.json").read_text())}
    assert steps[-1] == ("POST", "/api/deposit/depositions/1240/actions/publish")


def test_zenodo_first_deposition(snap: tuple[Path, snapshot.Manifest], clock: FakeClock) -> None:
    out, _ = snap
    fake = FakeZenodo([])
    publish.zenodo(zenodo_api(fake, clock), out, "zenodo-token")
    steps = [(r.method, r.url.path) for r in fake.requests]
    assert steps[:2] == [("GET", "/api/deposit/depositions"), ("POST", "/api/deposit/depositions")]
    assert all("newversion" not in path for _, path in steps)
    assert steps[-1] == ("POST", "/api/deposit/depositions/1300/actions/publish")


def test_zenodo_given_deposition_skips_the_search(
    snap: tuple[Path, snapshot.Manifest], clock: FakeClock
) -> None:
    out, _ = snap
    fake = FakeZenodo([])
    publish.zenodo(zenodo_api(fake, clock), out, "zenodo-token", "https://zenodo.org", deposition="1234")
    assert (fake.requests[0].method, fake.requests[0].url.path) == (
        "POST",
        "/api/deposit/depositions/1234/actions/newversion",
    )


# The command ----------------------------------------------------------------------------------------------


def test_the_snapshot_command(tmp_path: Path, polite: Polite, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["snapshot", "--out", str(tmp_path)],
        env={"CB_KAGGLE_DATASET": "brand/beer"},
        client=polite(FakeSite(site_routes())),
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == f"Snapshot of the build of {BUILD}: 11 files, 3 prices."
    assert json.loads((tmp_path / "dataset-metadata.json").read_text())["id"] == "brand/beer"
    assert (tmp_path / "README.md").is_file() and (tmp_path / ".zenodo.json").is_file()


@pytest.mark.parametrize(
    ("platform", "secret"),
    [("huggingface", "HF_TOKEN"), ("kaggle", "KAGGLE_API_TOKEN"), ("zenodo", "ZENODO_TOKEN")],
)
def test_publishing_without_a_token_is_a_notice(
    tmp_path: Path, platform: str, secret: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["publish", platform, "--dir", str(tmp_path)], env={}) == 0
    assert (
        capsys.readouterr().out.strip()
        == f"::notice::{platform} skipped: set the secret {secret} (docs/RUNBOOK.md §16b)"
    )


def test_the_zenodo_command(
    snap: tuple[Path, snapshot.Manifest], clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _ = snap
    fake = FakeZenodo(FakeZenodo.recorded("zenodo-depositions.json"))
    env = {"ZENODO_TOKEN": "zenodo-token", "CB_ZENODO_URL": "https://zenodo.org"}
    assert main(["publish", "zenodo", "--dir", str(out)], env=env, api=zenodo_api(fake, clock)) == 0
    assert capsys.readouterr().out.strip() == (
        "zenodo: https://zenodo.org/records/1240 (DOI 10.5281/zenodo.1240; all versions: 10.5281/zenodo.1233)"
    )
