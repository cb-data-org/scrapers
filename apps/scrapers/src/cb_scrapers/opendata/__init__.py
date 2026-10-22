"""Frozen snapshots of the open data for the mirrors the off-page audit lists (L5, #667): Hugging Face and
Kaggle every month, Zenodo (a DOI) every quarter. The data is the site's own static files under /api/v1
(ODbL 1.0, docs on /dados); nothing here reads the database or publishes anything the site doesn't already.

    uv run python -m cb_scrapers.opendata snapshot --out DIR
    uv run python -m cb_scrapers.opendata publish huggingface|kaggle|zenodo --dir DIR

Each publisher needs its own token (HF_TOKEN, KAGGLE_API_TOKEN, ZENODO_TOKEN) on a brand-named account; the
mirror's Open data (monthly) workflow skips a platform whose token isn't set (docs/RUNBOOK.md §16b).
"""
