"""`cb requests report`: what the request log (CB_REQUEST_LOG, core/http.py) says about Preço da Hora, to
learn what makes the site refuse us and how far under its limit we stay.

The Scrapers workflow keeps each run's log as an artifact (`precodahora-requests-<run id>`); `gh run
download` puts each one in a folder of its own. This reads the JSON-lines files it is given, or every .jsonl
under a folder, puts all their lines in order of time and reads off the ones to the site. It only reads
files: no network.

A refusal here is a 401, 403 or 429 (a robots.txt too), an HTML answer to a search, or no answer at all (a
timeout or another failure of the transport). The session page is HTML too, but it is a GET: only a POST to
the search address counts as a search. The requests counted "before" a refusal are every request to the
site, from the session page to robots.txt, from exactly that long before it up to it."""

import json
from bisect import bisect_left
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

from .core.http import BLOCK_STATUSES, REQUEST_LOG_HOST

#: The host the log keeps, and so the host the report is about (older or merged logs may hold others).
HOST = REQUEST_LOG_HOST
#: The search is a POST to this path; a GET of it is the session page.
SEARCH_PATH = "/produtos/"
#: The windows the busiest stretch is looked for in: seconds and how it is worded.
WINDOWS = {60: "1 minute", 600: "10 minutes", 3600: "60 minutes"}
#: How far back each refusal counts the requests before it: seconds, the key it goes under, how it is worded.
BEFORE = (
    (3600, "requests_60_minutes_before", "60 minutes"),
    (86400, "requests_24_hours_before", "24 hours"),
)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICROSECOND = timedelta(microseconds=1)


class ReportError(Exception):
    """Something wrong with what the report was asked to read (a path that isn't there, no log in it)."""


@dataclass(frozen=True, slots=True)
class Row:
    """A line of the request log, as far as the report reads it."""

    at: datetime
    ts: str
    run: str | None
    host: str
    method: str
    path: str
    label: str | None
    status: int | None
    kind: str
    content_type: str | None
    retry_after: str | None
    title: str | None
    body_sha256: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class Lines:
    """Everything read: the requests in order of time, and how many files and lines it took."""

    rows: list[Row]
    files: int
    lines: int
    unreadable: int


def _text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return str(value)


def _row(record: Any) -> Row | None:
    """A line's row, or None when it isn't a JSON object with a time."""
    if not isinstance(record, dict) or not isinstance(record.get("ts"), str):
        return None
    ts = record["ts"]
    try:
        at = datetime.fromisoformat(ts)
    except ValueError:
        return None
    status = record.get("status")
    return Row(
        at=at if at.tzinfo else at.replace(tzinfo=UTC),
        ts=ts,
        run=_text(record.get("run")),
        host=(_text(record.get("host")) or "").lower(),
        method=(_text(record.get("method")) or "").upper(),
        path=_text(record.get("path")) or "",
        label=_text(record.get("label")),
        status=status if isinstance(status, int) and not isinstance(status, bool) else None,
        kind=_text(record.get("kind")) or "other",
        content_type=_text(record.get("content_type")),
        retry_after=_text(record.get("retry_after")),
        title=_text(record.get("title")),
        body_sha256=_text(record.get("body_sha256")),
        error=_text(record.get("error")),
    )


def _files(paths: Sequence[Path]) -> list[Path]:
    found: dict[Path, None] = {}
    for path in paths:
        if path.is_dir():
            found.update(
                dict.fromkeys(file.resolve() for file in sorted(path.rglob("*.jsonl")) if file.is_file())
            )
        elif path.is_file():
            found[path.resolve()] = None
        else:
            raise ReportError(f"no such file or folder: {path}")
    if not found:
        raise ReportError(f"no .jsonl files in {', '.join(str(path) for path in paths)}")
    return list(found)


def read(paths: Sequence[Path]) -> Lines:
    """Every line of the files (and of the .jsonl files under the folders), in order of time. A line that
    isn't a JSON object with a time is counted and left out."""
    files = _files(paths)
    rows: list[Row] = []
    lines = unreadable = 0
    for file in files:
        try:
            text = file.read_text("utf-8", errors="replace")
        except OSError as error:
            raise ReportError(f"cannot read {file}: {error.strerror or error}") from error
        for line in text.split("\n"):
            if not line.strip():
                continue
            lines += 1
            try:
                row = _row(json.loads(line))
            except (ValueError, RecursionError):
                row = None
            if row is None:
                unreadable += 1
            else:
                rows.append(row)
    rows.sort(key=lambda row: row.at)
    return Lines(rows, len(files), lines, unreadable)


def _is_refusal(row: Row) -> bool:
    return (
        row.status is None
        or row.status in BLOCK_STATUSES
        or (row.kind == "html" and row.method == "POST" and row.path == SEARCH_PATH)
    )


def _runs(rows: list[Row]) -> list[dict[str, Any]]:
    runs: dict[str | None, list[Row]] = {}
    for row in rows:
        runs.setdefault(row.run, []).append(row)
    return [
        {"run": run, "first": found[0].ts, "last": found[-1].ts, "requests": len(found)}
        for run, found in runs.items()
    ]


def _refusals(rows: list[Row], times: list[int]) -> list[dict[str, Any]]:
    refusals = []
    for index, row in enumerate(rows):
        if not _is_refusal(row):
            continue
        refusals.append(
            {
                "ts": row.ts,
                "status": row.status,
                "kind": row.kind,
                "method": row.method,
                "path": row.path,
                "label": row.label,
                "run": row.run,
                "content_type": row.content_type,
                "retry_after": row.retry_after,
                "title": row.title,
                "body_sha256": row.body_sha256,
                "error": row.error,
                # The rows from `seconds` before this one up to it: it never counts itself.
                **{
                    key: index - bisect_left(times, times[index] - seconds * 1_000_000)
                    for seconds, key, _ in BEFORE
                },
            }
        )
    return refusals


def _busiest(rows: list[Row], times: list[int], seconds: int) -> dict[str, Any]:
    """The most requests in a window of `seconds` that starts at a request (the first such window)."""
    width = seconds * 1_000_000
    best = first = last = end = 0
    for start, begun in enumerate(times):
        while end < len(times) and times[end] < begun + width:
            end += 1
        if end - start > best:
            best, first, last = end - start, start, end - 1
    return {
        "window_seconds": seconds,
        "requests": best,
        "first": rows[first].ts if best else None,
        "last": rows[last].ts if best else None,
    }


def _gaps(rows: list[Row], times: list[int]) -> dict[str, Any]:
    """The gaps in seconds between one search and the next, whichever run they belong to."""
    searches = [
        at for row, at in zip(rows, times, strict=True) if row.method == "POST" and row.path == SEARCH_PATH
    ]
    gaps = [(later - earlier) / 1_000_000 for earlier, later in pairwise(searches)]
    return {
        "searches": len(searches),
        "median_seconds": round(median(gaps), 3) if gaps else None,
        "smallest_seconds": round(min(gaps), 3) if gaps else None,
    }


def _retry_after(rows: list[Row]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.retry_after is None:
            continue
        entry = seen.setdefault(
            row.retry_after, {"value": row.retry_after, "count": 0, "first": row.ts, "statuses": []}
        )
        entry["count"] += 1
        if row.status not in entry["statuses"]:
            entry["statuses"].append(row.status)
    return list(seen.values())


def figures(log: Lines) -> dict[str, Any]:
    """The report as data: what `--json` prints and `render` words."""
    rows = [row for row in log.rows if row.host == HOST]
    times = [(row.at - EPOCH) // MICROSECOND for row in rows]
    kinds = Counter(row.kind for row in rows)
    statuses = Counter(row.status for row in rows)
    titles = Counter(row.title for row in rows if row.title)
    return {
        "host": HOST,
        "files": log.files,
        "lines": log.lines,
        "unreadable_lines": log.unreadable,
        "requests": len(rows),
        "first": rows[0].ts if rows else None,
        "last": rows[-1].ts if rows else None,
        "runs": _runs(rows),
        "by_kind": dict(sorted(kinds.items(), key=lambda item: (-item[1], item[0]))),
        "by_status": {
            "none" if status is None else str(status): count
            for status, count in sorted(statuses.items(), key=lambda item: (item[0] is None, item[0] or 0))
        },
        "refusals": _refusals(rows, times),
        "busiest": [_busiest(rows, times, seconds) for seconds in WINDOWS],
        "search_gaps": _gaps(rows, times),
        "retry_after": _retry_after(rows),
        "titles": [
            {"title": title, "count": count}
            for title, count in sorted(titles.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _columns(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return [
        "  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:g} s"


def _refusal_lines(refusal: dict[str, Any]) -> list[str]:
    status = refusal["status"]
    answer = f"no answer ({refusal['error']})" if status is None else str(status)
    title = f'"{refusal["title"]}"' if refusal["title"] else "-"
    what = (
        f"{refusal['method']} {refusal['path']}  {refusal['content_type'] or '-'}  {title}  "
        f"sha256 {refusal['body_sha256'] or '-'}"
    )
    if refusal["retry_after"]:
        what += f"  Retry-After {refusal['retry_after']}"
    counted = ", ".join(f"{refusal[key]} in {words}" for _, key, words in BEFORE)
    parts = (f"run {refusal['run']}" if refusal["run"] else None, refusal["label"])
    where = ", ".join(part for part in parts if part)
    return [
        f"  {refusal['ts']}  {answer}  {what}",
        f"      requests before it: {counted}" + (f" ({where})" if where else ""),
    ]


def render(data: dict[str, Any]) -> str:
    """The report in plain text: no colours, nothing to parse."""
    host = data["host"]
    out = [
        f"Requests to {host}",
        f"Read {data['files']} file(s): {data['lines']} lines, {data['unreadable_lines']} unreadable; "
        f"{data['requests']} are requests to {host}.",
    ]
    if not data["requests"]:
        return "\n".join(out)
    out.append(f"From {data['first']} to {data['last']}.")

    out += ["", f"Runs ({len(data['runs'])})"]
    out += _columns(
        [
            ("run", "first request", "last request", "requests"),
            *(
                (run["run"] or "(no run id)", run["first"], run["last"], str(run["requests"]))
                for run in data["runs"]
            ),
        ]
    )

    out += ["", "By kind"]
    out += [f"  {count:>6}  {kind}" for kind, count in data["by_kind"].items()]
    out += ["", "By status"]
    out += [f"  {count:>6}  {status}" for status, count in data["by_status"].items()]

    out += [
        "",
        f"Refusals ({len(data['refusals'])}): a 401, 403 or 429, an HTML answer to a search, no answer",
    ]
    for refusal in data["refusals"]:
        out += _refusal_lines(refusal)
    if not data["refusals"]:
        out.append("  none")

    out += ["", "Busiest windows (every request to the site)"]
    for window in data["busiest"]:
        name = WINDOWS[window["window_seconds"]]
        span = f", from {window['first']} to {window['last']}" if window["requests"] else ""
        out.append(f"  {name:<10}  {window['requests']} requests{span}")

    gaps = data["search_gaps"]
    out += [
        "",
        f"Gaps between searches (POST {SEARCH_PATH}): {gaps['searches']} searches, "
        f"median {_seconds(gaps['median_seconds'])}, smallest {_seconds(gaps['smallest_seconds'])}",
    ]

    out += ["", "Retry-After values"]
    out += [
        f"  {entry['value']}  x{entry['count']}, first at {entry['first']}, status "
        + ", ".join("none" if status is None else str(status) for status in entry["statuses"])
        for entry in data["retry_after"]
    ] or ["  none"]

    out += ["", "Titles of answers that aren't JSON"]
    out += [f"  {entry['count']:>6}  {entry['title']}" for entry in data["titles"]] or ["  none"]
    return "\n".join(out)


def run_report(paths: Sequence[Path], *, as_json: bool = False) -> str:
    data = figures(read(paths))
    return json.dumps(data, ensure_ascii=False, indent=2) if as_json else render(data)
