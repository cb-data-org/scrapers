"""The monthly visibility monitor (off-page audit N12, docs/seo/2026-10-14-offpage.md §10): are we found by
search engines and AI assistants, and does every crawler still get in? It runs on the public mirror's free
Actions minutes (tools/public-scrapers/workflows/visibility.yml) and writes one report a month in the run's
summary, headed by one line, so the dated gates of docs/business-case.md need a minute of the owner's time
rather than four dashboards (the owner's setup: docs/RUNBOOK.md §15d).

Each source is a module with a `collect` that never raises: a missing key, a refusal or an outage is a status
with the reason, and the rest of the report still goes out.
"""
