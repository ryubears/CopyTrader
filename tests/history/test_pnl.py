"""
Tests for reading the candidate traders' PnL histories, with stand ins for the venues.
"""

from copytrader.api import hyperliquid, polymarket
from copytrader.db import database
from copytrader.db.models import Standing, Trader
from copytrader.history import pnl

NOW = 1791849600.0      # 2026-10-13 00:00 UTC.


def test_polymarket_candidates_are_everyone_on_the_latest_boards_and_everyone_followed(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    database.store(conn, [Standing("polymarket", day, "SPORTS/DAY/PNL", address, 1, 1.0, None, 1.0, None, NOW)
                          for day, address in (("2026-10-12", "0xold"), ("2026-10-13", "0xb"), ("2026-10-13", "0xa"))])
    database.store(conn, [Trader("polymarket", "0xf", None, "SPORTS/ALL/PNL #1 on 2026-10-12", NOW, None)])
    assert pnl.polymarket_candidates(conn) == ["0xa", "0xb", "0xf"]


def test_hyperliquid_candidates_traded_enough_and_are_not_vaults_or_sub_accounts(tmp_path, monkeypatch):
    def row(address, volume):
        return {"ethAddress": address, "windowPerformances": [["allTime", {"pnl": "0", "roi": "0", "vlm": str(volume)}]]}

    monkeypatch.setattr(hyperliquid, "leaderboard", lambda: [row("0xBIG", 2e8), row("0xsmall", 1e6), row("0xvault", 5e8)])
    monkeypatch.setattr(hyperliquid, "vaults", lambda: [{"summary": {"vaultAddress": "0xVAULT"}}])
    conn = database.connect(tmp_path / "copytrader.sqlite")
    database.store(conn, [Trader("hyperliquid", a, None, "week #1 on 2026-10-12", NOW, role) for a, role in (("0xf", "user"), ("0xs", "subAccount"))])
    assert pnl.hyperliquid_candidates(conn) == ["0xbig", "0xf"]


def test_a_backfill_skips_those_read_lately_and_retries_those_that_failed(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    asked = []

    def pnl_history(address):
        asked.append(address)
        if address == "0xbad":
            raise ValueError("no chart")
        return [{"t": 100, "p": 1.0}, {"t": 200, "p": 3.0}]

    monkeypatch.setattr(polymarket, "pnl_history", pnl_history)
    database.synced(conn, "polymarket", "pnl", "0xfresh", NOW - 86400)
    logs = []
    assert pnl.backfill(conn, "polymarket", ["0xa", "0xbad", "0xfresh"], NOW, logs.append) == 1
    assert asked == ["0xa", "0xbad"] and any("PnL of 0xbad failed" in line for line in logs)
    assert conn.execute("SELECT address, time, pnl FROM pnl_history").fetchall() == [("0xa", 100, 1.0), ("0xa", 200, 3.0)]
    asked.clear()
    pnl.backfill(conn, "polymarket", ["0xa", "0xbad", "0xfresh"], NOW, logs.append)
    assert asked == ["0xbad"]       # Read again, as its read failed.
