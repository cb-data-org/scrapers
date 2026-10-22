import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cb_scrapers.core import Pace
from cb_scrapers.core.pace import PACE_FILE, PaceBook

PACE = Pace(timedelta(hours=1), timedelta(hours=6))
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def test_keeps_each_start_and_block_for_the_next_run(tmp_path: Path) -> None:
    book = PaceBook(tmp_path / PACE_FILE)
    assert book.hold("precodahora", PACE, NOW) is None
    book.started("precodahora", NOW)
    book.blocked("precodahora", NOW + timedelta(minutes=3))
    assert json.loads((tmp_path / PACE_FILE).read_text("utf-8")) == {
        "precodahora": {"blocked": "2026-10-15T12:03:00+00:00", "started": "2026-10-15T12:00:00+00:00"}
    }
    assert [path.name for path in tmp_path.iterdir()] == [PACE_FILE]  # nothing half-written left behind
    reread = PaceBook(tmp_path / PACE_FILE)
    assert reread.times == book.times
    assert reread.hold("osm_venues", PACE, NOW) is None


def test_an_hour_after_the_last_start(tmp_path: Path) -> None:
    book = PaceBook(tmp_path / PACE_FILE)
    book.started("precodahora", NOW)
    hold = book.hold("precodahora", PACE, NOW + timedelta(minutes=59, seconds=59))
    assert hold is not None
    assert hold.until == NOW + timedelta(hours=1)
    assert hold.reason == "last run started at 15 Oct 12:00 UTC: the next from 15 Oct 13:00 UTC"
    assert book.hold("precodahora", PACE, NOW + timedelta(hours=1)) is None


def test_six_hours_after_a_refusal_whatever_ran_since(tmp_path: Path) -> None:
    book = PaceBook(tmp_path / PACE_FILE)
    book.started("precodahora", NOW)
    book.blocked("precodahora", NOW + timedelta(minutes=2))
    later = NOW + timedelta(hours=5, minutes=59)
    hold = book.hold("precodahora", PACE, later)
    assert hold is not None
    assert hold.reason == "refused at 15 Oct 12:02 UTC: no run before 15 Oct 18:02 UTC"
    assert book.hold("precodahora", PACE, NOW + timedelta(hours=6, minutes=2)) is None


def test_a_time_ahead_of_the_clock_never_holds_longer_than_the_pace(tmp_path: Path) -> None:
    book = PaceBook(tmp_path / PACE_FILE)
    book.blocked("precodahora", NOW + timedelta(days=30))
    hold = book.hold("precodahora", PACE, NOW)
    assert hold is not None and hold.until == NOW + timedelta(hours=6)


def test_reads_times_without_a_zone_as_utc_and_survives_a_broken_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / PACE_FILE
    path.write_text(json.dumps({"precodahora": {"started": "2026-10-15T12:00:00", "blocked": "soon"}}))
    assert PaceBook(path).times == {"precodahora": {"started": NOW}}
    path.write_text("{not json")
    assert PaceBook(path).times == {}
    assert "unreadable" in caplog.text
