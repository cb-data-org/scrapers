# Reference data

Loaded into D1 by `tools/scripts/src/seed.ts` after the migrations on every deploy. The upserts are keyed by slug,
so ids never change, and admin-owned fields are kept: `cities.active`, `neighbourhoods.pages_enabled`, brand owner
groups, recalibrated price bounds and aliases admins add or re-point.

| File | What | Source |
|---|---|---|
| `cities.json` | Cities with their time zone and centre | Hand-written |
| `beers.csv` | Beers by brand, with aliases (more specific than the brand's) | Hand-written |
| `catalog.json` | Generated: brands, beers, formats, aliases and price bounds for the scrapers | `pnpm exec tsx tools/scripts/src/catalog.ts --write` from `@cb/domain` + `beers.csv` |
| `bairros/salvador.geojson` | The 171 official bairros (Lei 9.278/2017, Decretos 32.791/2020 and 38.776/2024) | GeoSalvador, Prefeitura Municipal de Salvador; IBGE codes from the 2022 Census |
| `bairros/lauro-de-freitas.geojson` | The 19 bairros of the 2022 Census | IBGE, Censo Demográfico 2022 |
| `venues/comida-di-buteco-2026.csv` | The 62 Salvador and Lauro de Freitas participants of Comida di Buteco 2026, for review | comidadibuteco.com.br (names, addresses); points from OpenStreetMap |

## Venue lists (`venues/*.csv`)

Each file goes to the moderation queue as `new_venue` items, credited to the file name (`comida_di_buteco_2026`),
and read exactly like `POST /api/admin/import/csv` reads an upload: draft field names as headers, plus
`source_ref` (the row's stable id), `note` (shown on the Telegram card as a warning) and any extra column, kept
for the reviewer. A row is queued once: after it is approved or rejected, later deploys leave it out. Seed lists
need `lat`/`lng` on every row.

The Comida di Buteco list was copied from the contest's site (its robots.txt allows it) in October 2026. The site
lists only the current participants, so it is the 2026 edition rather than 2025. Points come from OpenStreetMap through the same
Overpass instances `osm_venues` uses: a venue with the same name nearby (`location` = `osm`, with its `osm_ref`),
an address point with the same number (`endereco`), the street inside the listed bairro (`rua`), the bairro's
centre (`bairro`) or, when nothing matched, the city centre (`cidade`). Every point short of `osm`/`endereco`
carries a note, so the reviewer checks the map link on the card before approving.

Rebuild the bairros with `uv run cb bairros build` (from `apps/scrapers`) when the official boundaries change. It
simplifies them together to about 20 m, and the files keep one feature per line, so diffs stay readable. Credit the
sources wherever the boundaries are shown or republished.
