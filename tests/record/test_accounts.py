"""
Tests for reading the followed Hyperliquid accounts on their schedules, with stand ins for Hyperliquid.
"""

import pytest
import time
from copytrader.api import hyperliquid
from copytrader.api.http import RequestFailed
from copytrader.db import database
from copytrader.db.models import Trader
from copytrader.record import accounts

NOW = 1791849600.0      # 2026-10-13 00:00 UTC.
STATE = {"marginSummary": {"accountValue": "50000.0", "totalNtlPos": "80000.0", "totalMarginUsed": "8000.0"}, "withdrawable": "42000.0",
         "time": 1791826690000, "assetPositions": [{"position": {"coin": "BTC", "szi": "1.0", "leverage": {"type": "cross", "value": 10},
                                                                 "entryPx": "80000.0", "positionValue": "80000.0", "unrealizedPnl": "0.0",
                                                                 "liquidationPx": "40000.0", "marginUsed": "8000.0"}}]}


def follow(conn, *addresses, role="user"):
    """
    Follow Hyperliquid accounts, of the role given.
    """
    database.store(conn, [Trader("hyperliquid", a, None, "month #1 on 2026-10-13", 1.0, role) for a in addresses])


def fill(time):
    """
    A fill as Hyperliquid sends it, at a time in milliseconds since 1970, its trade id the time.
    """
    return {"coin": "BTC", "px": "80000.0", "sz": "0.1", "side": "B", "time": time, "tid": time, "oid": time, "dir": "Open Long",
            "startPosition": "0.0", "closedPnl": "0.0", "fee": "1.0", "feeToken": "USDC", "crossed": True, "hash": "0xh", "twapId": None}


def down(*args):
    """
    Stands in for a call that fails.
    """
    raise RequestFailed(500, "down", "https://api.hyperliquid.xyz/info")


def test_roles_are_stored_and_only_a_users_own_account_is_read(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    follow(conn, "0xa", "0xs", role=None)
    monkeypatch.setattr(hyperliquid, "role", lambda address: {"role": "subAccount" if address == "0xs" else "user"})
    assert accounts.check_roles(conn, print) == 2
    assert database.due(conn, "hyperliquid", "fills", time.time()) == ["0xa"]


def test_a_role_that_fails_is_logged_and_asked_again(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    follow(conn, "0xa", role=None)
    monkeypatch.setattr(hyperliquid, "role", down)
    logs = []
    assert accounts.check_roles(conn, logs.append) == 0
    assert "the role of 0xa failed" in logs[0] and database.unchecked(conn, "hyperliquid", 10) == ["0xa"]


def test_fills_are_read_from_a_year_back_then_from_the_latest_stored(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    year_ago = int((NOW - 365 * 86400) * 1000)
    starts = []

    def fills(address, start):
        starts.append(start)
        return [fill(t) for t in (year_ago + 100, year_ago + 200) if t >= start]

    monkeypatch.setattr(hyperliquid, "fills", fills)
    assert accounts.read_fills(conn, "0xa", NOW) == (2, True)
    assert accounts.read_fills(conn, "0xa", NOW) == (0, True)       # The fill at the latest time, read again, is skipped.
    assert starts == [year_ago, year_ago + 200]


def test_a_long_history_is_read_in_pieces_after_the_accounts_never_read(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ROWS_PER_READ", 3)
    monkeypatch.setattr(accounts, "CHUNK", 2)
    conn = database.connect(tmp_path / "copytrader.sqlite")
    follow(conn, "0xa", "0xb")
    base = int(time.time() * 1000) - 5000
    histories = {"0xa": [fill(base + i) for i in range(1, 6)], "0xb": [fill(base + 9)]}
    monkeypatch.setattr(hyperliquid, "fills", lambda address, start: [f for f in histories[address] if f["time"] >= start])
    for address in ("0xa", "0xb"):
        for kind in ("positions", "funding", "ledger"):
            database.synced(conn, "hyperliquid", kind, address, time.time())      # Only the fills are due.
    reads = []
    for _ in range(5):
        due = database.due(conn, "hyperliquid", "fills", time.time() - accounts.EVERY["fills"], 1)
        reads.append((due, accounts.read_due(conn, time.time(), print, limit=1)["fills"]))
    # 0xa stops at 3 fills, 0xb, never read, goes next, then 0xa goes on from its third fill's time, read again and skipped.
    assert reads == [(["0xa"], (1, 3)), (["0xb"], (1, 1)), (["0xa"], (1, 2)), (["0xa"], (1, 0)), ([], (0, 0))]
    assert database.last_time(conn, "hl_fills", "0xa") == base + 5


def test_a_read_that_fails_partway_keeps_what_it_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "CHUNK", 2)
    conn = database.connect(tmp_path / "copytrader.sqlite")

    def fills(address, start):
        yield from (fill(1), fill(2), fill(3))
        down()

    monkeypatch.setattr(hyperliquid, "fills", fills)
    with pytest.raises(RequestFailed):
        accounts.read_fills(conn, "0xa", NOW)
    assert database.last_time(conn, "hl_fills", "0xa") == 2         # The first chunk was stored, the third fill was not.


def test_positions_store_the_account_and_each_position(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    monkeypatch.setattr(hyperliquid, "positions", lambda address: STATE)
    assert accounts.read_positions(conn, "0xa", NOW) == (2, True)
    assert conn.execute("SELECT coin, size, leverage, liquidation_price FROM hl_positions").fetchall() == [("BTC", 1.0, 10.0, 40000.0)]
    assert conn.execute("SELECT account_value FROM hl_accounts").fetchall() == [(50000.0,)]


def test_each_kind_is_read_when_due_and_a_failed_read_waits_its_turn(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    follow(conn, "0xa")
    monkeypatch.setattr(hyperliquid, "fills", lambda address, start: [fill(100)])
    monkeypatch.setattr(hyperliquid, "positions", lambda address: STATE)
    monkeypatch.setattr(hyperliquid, "funding_payments", down)
    monkeypatch.setattr(hyperliquid, "ledger", lambda address, start: [])
    logs = []
    assert accounts.read_due(conn, time.time(), logs.append) == {"fills": (1, 1), "positions": (1, 2), "funding": (1, 0), "ledger": (1, 0)}
    assert len(logs) == 1 and "the funding of 0xa failed" in logs[0]
    assert accounts.read_due(conn, time.time() + 60, logs.append) == {kind: (0, 0) for kind in accounts.READERS}   # None due yet.
    later = time.time() + accounts.EVERY["positions"] + 60
    assert accounts.read_due(conn, later, logs.append)["positions"] == (1, 0)      # Positions come due again first. The same reading, so nothing new.
