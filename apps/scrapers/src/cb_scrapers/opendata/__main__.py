"""`python -m cb_scrapers.opendata`: the open-data snapshot and its mirrors.

    uv run python -m cb_scrapers.opendata snapshot --out DIR
    uv run python -m cb_scrapers.opendata publish huggingface|kaggle|zenodo --dir DIR

snapshot writes DIR/files (the data and manifest.json), DIR/README.md (the Hugging Face card),
DIR/dataset-metadata.json (Kaggle) and DIR/.zenodo.json (Zenodo). publish needs the platform's token in the
environment (HF_TOKEN, KAGGLE_API_TOKEN, ZENODO_TOKEN) and says so, without failing, when it isn't there.
Optional: CB_HF_DATASET and CB_KAGGLE_DATASET (owner/name), CB_KAGGLE_CLI (how to run the kaggle CLI),
CB_ZENODO_URL (https://sandbox.zenodo.org to try it out) and CB_ZENODO_DEPOSITION (the deposition to version).
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path

from ..core import PoliteClient
from ..visibility.http import Api
from . import cards, publish, snapshot

log = logging.getLogger("cb.opendata")
TOKENS = {"huggingface": "HF_TOKEN", "kaggle": "KAGGLE_API_TOKEN", "zenodo": "ZENODO_TOKEN"}


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    client: PoliteClient | None = None,
    api: Api | None = None,
) -> int:
    """The command; tests pass their own env and clients (on recorded answers)."""
    parser = argparse.ArgumentParser(prog="python -m cb_scrapers.opendata", description="Open-data snapshots")
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser("snapshot", help="download one build's open data and write the cards")
    take.add_argument("--out", type=Path, required=True)
    push = commands.add_parser("publish", help="push a snapshot to one mirror")
    push.add_argument("platform", choices=sorted(TOKENS))
    push.add_argument("--dir", type=Path, required=True, help="a snapshot's folder")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    env = env if env is not None else dict(os.environ)

    if args.command == "snapshot":
        args.out.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            client = client or stack.enter_context(PoliteClient(delay=1.0))
            manifest = snapshot.take(client, args.out)
        cards.write(args.out, manifest, env.get("CB_KAGGLE_DATASET") or cards.KAGGLE_DATASET)
        rows = sum(f.rows or 0 for f in manifest.files if f.dataset == "deals" and f.format == "csv")
        files = len(manifest.files)
        print(f"Snapshot of the build of {manifest.data_updated_at}: {files} files, {rows} prices.")
        return 0

    token = env.get(TOKENS[args.platform])
    if not token:
        secret = TOKENS[args.platform]
        print(f"::notice::{args.platform} skipped: set the secret {secret} (docs/RUNBOOK.md §16b)")
        return 0
    manifest = snapshot.Manifest.load(args.dir / "files" / "manifest.json")
    if args.platform == "huggingface":
        where = publish.huggingface(args.dir, manifest, env.get("CB_HF_DATASET") or cards.HF_DATASET, token)
    elif args.platform == "kaggle":
        cli = shlex.split(env.get("CB_KAGGLE_CLI") or "kaggle")
        where = publish.kaggle(args.dir, manifest, env.get("CB_KAGGLE_DATASET") or cards.KAGGLE_DATASET, cli)
    else:
        with ExitStack() as stack:
            api = api or stack.enter_context(Api(delay=1.0))
            record = publish.zenodo(
                api,
                args.dir,
                token,
                env.get("CB_ZENODO_URL") or publish.ZENODO,
                env.get("CB_ZENODO_DEPOSITION") or None,
            )
        where = f"{record['url']} (DOI {record['doi']}; all versions: {record['concept_doi']})"
    print(f"{args.platform}: {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
