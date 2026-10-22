"""The pace book: when each paced source (Adapter.pace) last started a run and last refused us, kept in
the cache folder between runs. The scrapers workflow restores that folder before every run and saves it
after, even after a failure, so `cb run` holds every run to the pace: scheduled, by hand or a dry run.

    {"precodahora": {"started": "2026-10-15T10:07:31+00:00", "blocked": "2026-10-15T04:12:09+00:00"}}
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .adapter import Pace
from .cache import cache_dir

log = logging.getLogger(__name__)

PACE_FILE = "pace.json"


@dataclass(frozen=True, slots=True)
class Hold:
    """Why a paced source may not start a run yet, and from when it may."""

    until: datetime
    reason: str


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%d %b %H:%M UTC")


class PaceBook:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_dir() / PACE_FILE
        self.times: dict[str, dict[str, datetime]] = {}
        try:
            saved = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            log.warning("unreadable %s (%s): starting a new one", self.path, error)
            return
        for name, times in saved.items() if isinstance(saved, dict) else ():
            for what, value in times.items() if isinstance(times, dict) else ():
                try:
                    moment = datetime.fromisoformat(str(value))
                except ValueError:
                    continue
                self.times.setdefault(name, {})[what] = (
                    moment if moment.tzinfo else moment.replace(tzinfo=UTC)
                )

    def hold(self, name: str, pace: Pace, now: datetime) -> Hold | None:
        """Why `name` may not start a run at `now`, or None when it may. A time saved ahead of `now` (a wrong
        clock) counts as `now`, so no wait ever lasts longer than the pace itself."""
        times = self.times.get(name, {})
        holds = []
        if (blocked := times.get("blocked")) is not None:
            until = min(blocked, now) + pace.after_block
            holds.append(Hold(until, f"refused at {_when(blocked)}: no run before {_when(until)}"))
        if (started := times.get("started")) is not None:
            until = min(started, now) + pace.every
            holds.append(Hold(until, f"last run started at {_when(started)}: the next from {_when(until)}"))
        waiting = [hold for hold in holds if hold.until > now]
        return max(waiting, key=lambda hold: hold.until, default=None)

    def started(self, name: str, at: datetime) -> None:
        """Saved at once, before the first request: a run cut short still counts."""
        self._mark(name, "started", at)

    def blocked(self, name: str, at: datetime) -> None:
        self._mark(name, "blocked", at)

    def _mark(self, name: str, what: str, at: datetime) -> None:
        self.times.setdefault(name, {})[what] = at
        saved = {
            source: {mark: moment.isoformat(timespec="seconds") for mark, moment in sorted(marks.items())}
            for source, marks in sorted(self.times.items())
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written aside, then moved over the old file: a run killed mid-write never leaves half a book.
        partial = self.path.with_name(f"{self.path.name}.partial")
        partial.write_text(json.dumps(saved), "utf-8")
        partial.replace(self.path)
