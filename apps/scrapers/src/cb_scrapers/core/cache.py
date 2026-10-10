"""Where scrapers keep answers between runs: CB_CACHE_DIR (the daily workflow caches that folder), else
~/.cache/cervejabarata."""

import os
from pathlib import Path


def cache_dir() -> Path:
    return Path(os.environ.get("CB_CACHE_DIR") or Path.home() / ".cache" / "cervejabarata")
