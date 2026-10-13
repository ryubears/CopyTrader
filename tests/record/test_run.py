"""
Tests for the recording process, with stand ins for the standings and the accounts' reads.
"""

import time
from copytrader.api.http import RequestFailed
from copytrader.db import database
from copytrader.db.models import Standing
from copytrader.record import accounts, run, standings


def test_standings_are_recorded_once_a_day_and_a_failure_tried_again_after_a_wait(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    calls = []

    def works(conn, now):
        calls.append("polymarket")
        database.store(conn, [Standing("polymarket", standings.today(now), "SPORTS/DAY/PNL", "0xa", 1, 1.0, None, 1.0, None, now)])
        return 1, 0

    def fails(conn, now):
        calls.append("hyperliquid")
        raise RequestFailed(503, "down", "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")

    monkeypatch.setattr(run, "RECORDERS", {"polymarket": works, "hyperliquid": fails})
    tried, logs = {}, []
    now = 1791849600.0 + 3600       # 2026-10-13 01:00 UTC.
    assert run.record_standings(conn, now, tried, logs.append)
    assert not run.record_standings(conn, now + 60, tried, logs.append)    # Polymarket is done for the day, and Hyperliquid waits.
    assert run.record_standings(conn, now + run.RETRY_SECONDS, tried, logs.append)
    assert calls == ["polymarket", "hyperliquid", "hyperliquid"]
    assert "1 standings recorded for 2026-10-13" in logs[0] and "hyperliquid's standings failed" in logs[1]


def test_record_passes_until_its_time_is_up_then_says_what_it_stored(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    monkeypatch.setattr(run, "IDLE_SECONDS", 0.01)
    monkeypatch.setattr(run, "RECORDERS", {"polymarket": lambda conn, now: (0, 0)})
    monkeypatch.setattr(accounts, "check_roles", lambda conn, log: 0)
    passes = []

    def read_due(conn, now, log):
        passes.append(now)
        return {"fills": (1, 5) if len(passes) == 1 else (0, 0)}

    monkeypatch.setattr(accounts, "read_due", read_due)
    monkeypatch.setattr(run, "code_version", lambda: "cd286cd-dirty")
    logs = []
    started = time.time()
    run.record(conn, seconds=0.2, log=logs.append)
    assert 0.2 <= time.time() - started < 1 and len(passes) > 2
    assert logs[0] == "recording, 0 Hyperliquid and 0 Polymarket traders followed so far, with code cd286cd-dirty"
    assert logs[-1].startswith("since the start: 0 roles stored, 5 fills rows")


def test_the_code_version_names_this_checkouts_commit():
    assert run.code_version() != "unknown"
