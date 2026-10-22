# Cerveja Barata scrapers

The data collectors behind [cervejabarata.com.br](https://cervejabarata.com.br), the free site that shows where
beer is cheapest in Salvador and Lauro de Freitas (Bahia), ranked by price per litre: bars, supermarkets and
depósitos.

This repository is a read-only mirror of the scrapers, published so they can run on GitHub Actions. The site itself
lives elsewhere; issues and pull requests here aren't watched. Contact: contato@cervejabarata.com.br.

## What runs here

- **Preço da Hora Bahia** (SEFAZ-BA): beer sales from electronic receipts (NFC-e), searched by beer around the city,
  in runs of at most 30 searches at least 20 seconds apart (about ten minutes), at most one run an hour, day and night
  (720 searches a day at most). A run ends at the first sign the site doesn't want us (HTTP 403/429, a CAPTCHA, an
  unexpected answer, a robots.txt that shuts us out), and no run starts for the next six hours. The schedule tries
  three times an hour; `cb run` keeps the pace in the Actions cache, so a run started by hand keeps it too. It never
  works around a CAPTCHA.
- **OpenStreetMap**: the bars, pubs and restaurants of the covered cities, once a week.
- **Venue classifier**: once a day, a few dozen listed venues are sorted into bars, restaurants, depósitos and stores
  by two AI models (Gemini and Workers AI) reading public facts about each one: the company register (CNPJ), map
  listings (OpenStreetMap, Overture Maps, Google Places, of which only Google's place id is kept) and the prices
  we publish. It reads; the site decides what to change.
- **Weekly carousels** (`tools/social`): every Friday morning, the week's cheapest beers in Salvador and its
  bairros as Instagram images, rendered from the site's public data and sent to the site's admins for review. This
  repository never posts anything: a person approves each post first. The same run keeps the Friday's top 10 in
  `data/sextou`, the only file it commits.
- **Visibility check** (`cb_scrapers.visibility`): on the 1st of each month, whether search engines and AI
  assistants find the site. It reads Search Console, Bing Webmaster Tools and Cloudflare Web Analytics with the
  site's own keys, asks the official MCP Registry, Smithery, Glama and Common Crawl's index about the site, asks the
  Wayback Machine to capture a few of its pages, and fetches five of the site's own pages with each search and AI
  crawler's user agent, to see that they still get in (that check also runs alone every Monday). Its report holds
  counts and the site's own addresses, never search queries.
- **Open data** (`cb_scrapers.opendata`): on the 2nd of each month, a frozen snapshot of the site's open data
  (ODbL 1.0) for its mirrors on Hugging Face and Kaggle, and every quarter on Zenodo, from the brand's own accounts.

The scrapers' requests carry `CervejaBarataBot/1.0 (+https://cervejabarata.com.br/bot)` and obey robots.txt; the
visibility check calls the APIs above as their documentation describes, and its crawler probe, which only reads
cervejabarata.com.br, sends each crawler's user agent instead. Prices are published with their source and date.
Nothing about buyers is collected, and one-person businesses without a trade name are left out.

## Layout

- `apps/scrapers`: the Python package (`uv run cb …`), its tests and contract models.
- `data/seed`: cities, bairro outlines and the beer catalog the scrapers read.
- `tools/social`: the carousel generator, one generated file (`social.mjs`, don't edit) and its `package.json`.
- `data/sextou`: each Friday's top 10, frozen by the carousel job on the day it posts (newest first): the archive behind
  the site's Sextou page (cervejabarata.com.br/salvador/sextou). Open data under the ODbL; the workflow writes it.
- `.github/workflows`: the Preço da Hora runs, the weekly carousels, the monthly visibility check, the open-data
  snapshot and the tests.

Run the tests with `uv run pytest` and `uv run ruff check` in `apps/scrapers` (Python 3.13, [uv](https://docs.astral.sh/uv/)).
