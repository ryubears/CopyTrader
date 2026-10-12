"""
Tests for storing and reading the traders followed and what they did.
"""

from copytrader.db import database
from copytrader.db.models import Fill, Standing, Trader
from dataclasses import replace


def trader(address, role=None, first_seen=1.0):
    """
    A followed Hyperliquid Trader, of the role given.
    """
    return Trader("hyperliquid", address, None, "month #1 on 2026-10-13", first_seen, role)


def fill(address, tid, time):
    """
    A Fill of an account at a time, in milliseconds since 1970.
    """
    return Fill(address, tid, tid + 1000, time, "BTC", "B", 80000.0, 0.1, "Open Long", 0.0, 0.0, 1.0, "USDC", True, "0xhash", None, None)


def test_connect_creates_every_table_and_finds_them_again(tmp_path):
    database.connect(tmp_path / "copytrader.sqlite")
    conn = database.connect(tmp_path / "copytrader.sqlite")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {table for table, _ in database.TABLES.values()}


def test_a_trader_followed_again_keeps_what_first_put_them_there(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    assert database.store(conn, [trader("0xa")]) == 1
    assert database.store(conn, [replace(trader("0xa"), reason="week #3 on 2026-10-14", first_seen=2.0)]) == 0
    assert database.traders(conn, "hyperliquid") == [trader("0xa")]


def test_a_standing_recorded_again_the_same_day_is_replaced(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    first = Standing("polymarket", "2026-10-13", "SPORTS/MONTH/PNL", "0xa", 1, 100.0, None, 1000.0, None, 1.0)
    database.store(conn, [first])
    database.store(conn, [replace(first, pnl=150.0)])
    assert conn.execute("SELECT pnl FROM standings").fetchall() == [(150.0,)]
    assert database.last_day(conn, "polymarket") == "2026-10-13" and database.last_day(conn, "hyperliquid") is None


def test_fills_read_again_are_skipped_and_the_next_read_starts_from_the_latest(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    assert database.last_time(conn, "hl_fills", "0xa") is None
    assert database.store(conn, [fill("0xa", 1, 100), fill("0xa", 2, 200)]) == 2
    assert database.store(conn, [fill("0xa", 2, 200), fill("0xa", 3, 300)]) == 1
    assert database.last_time(conn, "hl_fills", "0xa") == 300 and database.last_time(conn, "hl_fills", "0xb") is None


def test_only_users_are_due_those_never_read_first_then_those_read_longest_ago(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    database.store(conn, [trader("0xa", "user", 1.0), trader("0xb", "user", 2.0), trader("0xc", "user", 3.0), trader("0xs", "subAccount"),
                          trader("0xn")])
    database.synced(conn, "hyperliquid", "fills", "0xa", 50.0)
    database.synced(conn, "hyperliquid", "fills", "0xb", 10.0)
    assert database.due(conn, "hyperliquid", "fills", 100.0) == ["0xc", "0xb", "0xa"]
    assert database.due(conn, "hyperliquid", "fills", 20.0) == ["0xc", "0xb"]          # 0xa was read at 50.
    assert database.due(conn, "hyperliquid", "fills", 100.0, limit=1) == ["0xc"]
    assert database.due(conn, "hyperliquid", "positions", 100.0) == ["0xa", "0xb", "0xc"]     # Each kind of read has its own schedule.


def test_roles_not_known_are_asked_first_followed_first(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    database.store(conn, [trader("0xb", first_seen=2.0), trader("0xa", first_seen=1.0), trader("0xu", "user")])
    assert database.unchecked(conn, "hyperliquid", 10) == ["0xa", "0xb"]
    database.set_role(conn, "hyperliquid", "0xa", "vault")
    assert database.unchecked(conn, "hyperliquid", 10) == ["0xb"]
    assert database.traders(conn, "hyperliquid")[0].role == "vault"
