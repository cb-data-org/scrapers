# Cerveja Barata scrapers

The data collectors behind [cervejabarata.com.br](https://cervejabarata.com.br), the free site that shows where
beer is cheapest in Salvador and Lauro de Freitas (Bahia), ranked by price per litre: bars, supermarkets and
depósitos.

This repository is a read-only mirror of the scrapers, published so they can run on GitHub Actions. The site itself
lives elsewhere; issues and pull requests here aren't watched. Contact: contato@cervejabarata.com.br.

## What runs here

- **Preço da Hora Bahia** (SEFAZ-BA): beer sales from electronic receipts (NFC-e), searched by beer around the city,
  in runs of at most 30 searches at least 20 seconds apart (about ten minutes), eight minutes between runs, about
  three runs an hour, day and night. A run ends at the first sign the site doesn't want us (HTTP 403/429, a CAPTCHA,
  an unexpected page); the next scheduled run tries again. It never works around a CAPTCHA.
- **OpenStreetMap**: the bars, pubs and restaurants of the covered cities, once a week.
- **Venue classifier**: once a day, a few dozen listed venues are sorted into bars, restaurants, depósitos and stores
  by two AI models (Gemini and Workers AI) reading public facts about each one: the company register (CNPJ), map
  listings (OpenStreetMap, Overture Maps, Google Places, of which only Google's place id is kept) and the prices
  we publish. It reads; the site decides what to change.
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
- `.github/workflows`: the Preço da Hora runs, the weekly carousels and the tests.

Run the tests with `uv run pytest` and `uv run ruff check` in `apps/scrapers` (Python 3.13, [uv](https://docs.astral.sh/uv/)).
