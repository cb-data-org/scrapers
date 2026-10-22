"""Pushing a snapshot to each mirror, each with its own brand-account token (never a personal one):

- Hugging Face: one commit to the dataset repository (created public on the first push) with the files and
  the card, files gone from the snapshot removed, and a tag per snapshot (huggingface_hub, installed by the
  workflow only).
- Kaggle: a new version of the dataset, or the dataset itself the first time (the kaggle CLI, installed by the
  workflow only), from the files and dataset-metadata.json.
- Zenodo: a new version of the last published deposition that links back to /dados (or a first deposition),
  its files replaced, the metadata of .zenodo.json, then published, which mints the version's DOI. Its REST
  API, through the monitor's HTTP client.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from ..visibility.http import Api, check
from .cards import DADOS
from .snapshot import Manifest

ZENODO = "https://zenodo.org"
#: Kept out of a mirror's data folder (each platform gets only its own metadata).
METADATA = ("README.md", "dataset-metadata.json", ".zenodo.json")


def stage(out: Path, name: str, extra: Sequence[str]) -> Path:
    """A folder with the snapshot's files and the given metadata files from `out`."""
    folder = out / "stage" / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for path in sorted((out / "files").iterdir()):
        shutil.copy2(path, folder / path.name)
    for metadata in extra:
        shutil.copy2(out / metadata, folder / metadata)
    return folder


class HubApi(Protocol):
    def create_repo(self, repo_id: str, **kwargs: Any) -> Any: ...
    def upload_folder(self, **kwargs: Any) -> Any: ...
    def create_tag(self, repo_id: str, **kwargs: Any) -> Any: ...


def huggingface(out: Path, manifest: Manifest, repo_id: str, token: str, api: HubApi | None = None) -> str:
    if api is None:
        from huggingface_hub import HfApi  # the workflow installs it: not a dependency of the scrapers

        api = HfApi(token=token)
    folder = stage(out, "huggingface", ["README.md"])
    day = manifest.data_updated_at[:10]
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(folder),
        commit_message=f"Snapshot of the open data, {day}",
        delete_patterns=["*.csv", "*.json", "*.geojson"],
    )
    api.create_tag(repo_id, tag=day, repo_type="dataset", exist_ok=True)
    return f"https://huggingface.co/datasets/{repo_id}"


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _said(done: subprocess.CompletedProcess[str]) -> str:
    return f"{done.stdout}\n{done.stderr}".lower()


def kaggle(
    out: Path,
    manifest: Manifest,
    dataset_id: str,
    cli: Sequence[str] = ("kaggle",),
    run: Runner = subprocess.run,
) -> str:
    """A new version of the dataset, or the dataset itself (public) the first time."""
    folder = stage(out, "kaggle", ["dataset-metadata.json"])
    message = f"Snapshot of the open data, {manifest.data_updated_at[:10]}"
    commands = {
        "version": [*cli, "datasets", "version", "-p", str(folder), "-m", message, "-r", "skip"],
        "create": [*cli, "datasets", "create", "-p", str(folder), "-u", "-r", "skip"],
    }
    status = run([*cli, "datasets", "status", dataset_id], capture_output=True, text=True)
    action = "create" if status.returncode != 0 or "404" in _said(status) else "version"
    done = run(commands[action], capture_output=True, text=True)
    if done.returncode != 0 and action == "version" and "not found" in _said(done):
        action = "create"
        done = run(commands[action], capture_output=True, text=True)
    if done.returncode != 0:
        # The CLI's own words, never the environment (the token lives there).
        raise RuntimeError(f"kaggle {action} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return f"https://www.kaggle.com/datasets/{dataset_id}"


def _ours(deposition: dict[str, Any]) -> bool:
    related = deposition.get("metadata", {}).get("related_identifiers", [])
    return any(item.get("identifier") == DADOS for item in related if isinstance(item, dict))


def zenodo(
    api: Api, out: Path, token: str, base: str = ZENODO, deposition: str | None = None
) -> dict[str, str | None]:
    """The new version's DOI and page (the concept DOI too, which always points at the newest version)."""
    auth = {"Authorization": f"Bearer {token}"}
    depositions = f"{base.rstrip('/')}/api/deposit/depositions"
    metadata = json.loads((out / ".zenodo.json").read_text())
    latest = deposition
    if not latest:
        mine = check(api.get(depositions, params={"size": "100"}, headers=auth)).json()
        published = [d for d in mine if isinstance(d, dict) and d.get("submitted") and _ours(d)]
        latest = str(max(published, key=lambda d: int(d["id"]))["id"]) if published else None
    if latest:
        parent = check(api.post(f"{depositions}/{latest}/actions/newversion", headers=auth)).json()
        draft = check(api.get(parent["links"]["latest_draft"], headers=auth)).json()
        # A new version starts with the last one's files; a failed run's draft, with what it uploaded.
        for item in draft.get("files", []):
            check(api.delete(f"{depositions}/{draft['id']}/files/{item['id']}", headers=auth))
    else:
        draft = check(api.post(depositions, json={}, headers=auth)).json()
    bucket = draft["links"]["bucket"]
    folder = stage(out, "zenodo", ["README.md"])
    for path in sorted(folder.iterdir()):
        headers = {**auth, "Content-Type": "application/octet-stream"}
        check(api.put(f"{bucket}/{path.name}", content=path.read_bytes(), headers=headers, timeout=300.0))
    check(api.put(f"{depositions}/{draft['id']}", json={"metadata": metadata}, headers=auth))
    record = check(api.post(f"{depositions}/{draft['id']}/actions/publish", headers=auth)).json()
    return {
        "doi": record.get("doi"),
        "concept_doi": record.get("conceptdoi"),
        "url": record.get("links", {}).get("html") or record.get("record_url"),
    }
