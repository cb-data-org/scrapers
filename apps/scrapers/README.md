# Scrapers

Python 3.13 + uv. Polite source adapters that send venues and prices to the ingest API. Nothing here publishes
anything: every price goes through the Worker's publish policy, and anything uncertain waits in the queue.

## Run

```sh
uv sync
uv run cb list                         # adapters and cadences
uv run cb run cardapioweb --dry-run    # print items as JSON lines, send nothing
uv run cb run --due                    # what GitHub Actions runs
uv run pytest && uv run ruff check && uv run ruff format --check
```

Sending needs two environment variables (GitHub secrets in CI, never committed):

| Variable | Value |
|---|---|
| `CB_API_URL` | site origin, or the staging workers.dev origin |
| `CB_INGEST_TOKEN` | an ingest-scoped admin token |

## Add an adapter

1. Create `src/cb_scrapers/adapters/<name>.py`. Subclass `Adapter` and set `name` and `cadence`; raise `delay` if
   the site needs more than 3 s between requests.
2. Implement `tasks()` and `parse()`. Override `fetch()` for sessions, CSRF tokens or POST searches.
3. Decorate the class with `@register` and import the module in `adapters/__init__.py`.
4. Test on recorded fixtures in `tests/`. Tests never touch the network.

## House rules

- Identify as `CervejaBarataBot/1.0 (+https://cervejabarata.com.br/bot)`, follow robots.txt and Crawl-delay,
  and leave at least 3 s between requests to one host.
- A 403, a 429 or a CAPTCHA raises `BlockedError` and the source stops for the run. Never bypass a block.
- `robots=False` is only for official APIs called with our own credentials.
- Idempotency:
  - `listing_hash`: one offer at one venue, per source.
  - `obs_hash`: one price per listing per local day.
  - `content_hash`: spots pages that didn't change.

  To change how they're computed, bump the `v1` prefixes; otherwise every listing would be duplicated.
