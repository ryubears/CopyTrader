"""
Tests for reading the followed Polymarket traders' activity and its markets, with stand ins for Polymarket.
"""

from copytrader.api import polymarket
from copytrader.db import database
from copytrader.history import polymarket as history

NOW = 1791849600.0      # 2026-10-13 00:00 UTC.
DAY = history.DAY


def row(timestamp, condition_id="0xc1", tx="0xt"):
    """
    One trade of a trader's activity as Polymarket sends it.
    """
    return {"proxyWallet": "0xa", "timestamp": timestamp, "conditionId": condition_id, "type": "TRADE", "size": 10.0, "usdcSize": 5.0,
            "transactionHash": f"{tx}{timestamp}", "price": 0.5, "asset": "11", "side": "BUY", "outcomeIndex": 0, "outcome": "Yes"}


def test_a_slow_trader_is_read_back_the_whole_window_and_a_fast_one_as_far_as_the_rows_reach(monkeypatch):
    monkeypatch.setattr(history, "ACTIVITY_PAGE", 4)
    monkeypatch.setattr(history, "ROWS_PER_TRADER", 40)
    monkeypatch.setattr(polymarket, "latest_activity", lambda address, limit: [row(NOW - i * 3600) for i in range(3)])
    assert history.first_start("0xa", NOW, 180) == NOW - 180 * DAY          # Fewer rows than a page: read it all.
    monkeypatch.setattr(polymarket, "latest_activity", lambda address, limit: [row(NOW - i * 3600) for i in range(4)])
    assert history.first_start("0xa", NOW, 180) == NOW - 30 * 3600          # 4 rows in 3 hours, so 40 rows reach 30 hours back.


def test_a_traders_activity_is_stored_in_chunks_and_read_again_from_the_last(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "CHUNK", 2)
    monkeypatch.setattr(polymarket, "latest_activity", lambda address, limit: [])
    rows = [row(int(NOW) - 300 + i) for i in range(5)]
    starts = []

    def activity(address, start=None):
        starts.append(start)
        return [r for r in rows if r["timestamp"] >= start]

    monkeypatch.setattr(polymarket, "activity", activity)
    conn = database.connect(tmp_path / "copytrader.sqlite")
    assert history.read_trader(conn, "0xa", NOW, 180) == 5
    assert history.read_trader(conn, "0xa", NOW, 180) == 0      # The last row, read again, is skipped.
    assert starts == [int(NOW - 180 * DAY), int(NOW) - 296]


def test_only_traders_not_read_lately_are_read_and_a_failure_is_left_for_the_next_run(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    database.synced(conn, "polymarket", "activity", "0xfresh", NOW - 3600)
    read = []

    def read_trader(conn, address, now, days):
        read.append(address)
        if address == "0xbad":
            raise ValueError("down")
        return 1

    monkeypatch.setattr(history, "read_trader", read_trader)
    logs = []
    assert history.read_traders(conn, ["0xa", "0xbad", "0xfresh"], NOW, log=logs.append) == 1
    assert read == ["0xa", "0xbad"] and database.synced_since(conn, "polymarket", "activity", 0) == {"0xa", "0xfresh"}


def test_markets_named_by_activity_are_read_closed_and_open_and_resolved_ones_not_again(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "MARKETS_BATCH", 2)
    conn = database.connect(tmp_path / "copytrader.sqlite")
    database.store(conn, list(polymarket.parse_activity([row(1, "0xc1"), row(2, "0xc2"), row(3, "0xc3"), {**row(4, ""), "type": "MAKER_REBATE"}])))
    asked = []

    def markets(condition_ids, closed, include_tag):
        asked.append((condition_ids, closed))
        found = {"0xc1": "resolved", "0xc2": None} if closed else {"0xc3": None}
        return [{"id": c, "conditionId": c, "question": "?", "closed": closed, "umaResolutionStatus": status, "outcomes": "[]",
                 "clobTokenIds": "[]"} for c, status in found.items() if c in condition_ids]

    monkeypatch.setattr(polymarket, "markets", markets)
    assert history.read_markets(conn, NOW) == 3
    assert asked == [(["0xc1", "0xc2"], True), (["0xc1", "0xc2"], False), (["0xc3"], True), (["0xc3"], False)]
    assert database.markets_wanted(conn) == ["0xc2", "0xc3"]        # Not resolved yet, so read again next time.
