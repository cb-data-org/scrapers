# Cerveja Barata scrapers

The data collectors behind [cervejabarata.com.br](https://cervejabarata.com.br), the free site that shows where
beer is cheapest in Salvador and Lauro de Freitas (Bahia), ranked by price per litre: bars, supermarkets and
depósitos.

This repository is a read-only mirror of the scrapers, published so they can run on GitHub Actions. The site itself
lives elsewhere; issues and pull requests here aren't watched. Contact: contato@cervejabarata.com.br.

## What runs here

- **Preço da Hora Bahia** (SEFAZ-BA): beer sales from electronic receipts (NFC-e), searched by beer around the city,
  every hour, one search every 20 seconds and at most 30 a run, stopping at the first sign the site doesn't want us
  (HTTP 403/429, a CAPTCHA, an unexpected page). It never works around a CAPTCHA.
- **OpenStreetMap**: the bars, pubs and restaurants of the covered cities, once a week.
- **Weekly carousels** (`tools/social`): every Friday morning, the week's cheapest beers in Salvador and its
  bairros as Instagram images, rendered from the site's public data and sent to the site's admins for review. This
  repository never posts anything: a person approves each post first.

Every request carries `CervejaBarataBot/1.0 (+https://cervejabarata.com.br/bot)` and obeys robots.txt. Prices are
published with their source and date. Nothing about buyers is collected, and one-person businesses without a trade
name are left out.

## Layout

- `apps/scrapers`: the Python package (`uv run cb …`), its tests and contract models.
- `data/seed`: cities, bairro outlines and the beer catalog the scrapers read.
- `tools/social`: the carousel generator, one generated file (`social.mjs`, don't edit) and its `package.json`.
- `.github/workflows`: the hourly run, the weekly carousels and the tests.

Run the tests with `uv run pytest` and `uv run ruff check` in `apps/scrapers` (Python 3.13, [uv](https://docs.astral.sh/uv/)).
