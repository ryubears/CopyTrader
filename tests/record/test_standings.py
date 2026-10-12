"""
Tests for recording the leaderboards and following the traders at their top, with stand ins for the venues.
"""

from copytrader.api import hyperliquid, polymarket
from copytrader.db import database
from copytrader.record import standings

NOW = 1791849600.0      # 2026-10-13 00:00 UTC.


def pm_board(category, period, order, limit):
    """
    Stands in for polymarket.leaderboard(): the same three traders on every board, best first.
    """
    return [{"rank": str(i), "proxyWallet": f"0x{name.upper()}", "userName": name, "pnl": 300.0 - 100 * i, "vol": 1000.0}
            for i, name in enumerate(("aa", "bb", "cc"), 1)][:limit]


def hl_row(address, pnl, month_volume=1e6):
    """
    A Hyperliquid leaderboard row with the same PnL in every window, and the month's volume.
    """
    return {"ethAddress": address, "accountValue": "50000.0",
            "windowPerformances": [[w, {"pnl": str(pnl), "roi": "0.1", "vlm": str(month_volume if w == "month" else 1e6)}]
                                   for w in ("day", "week", "month", "allTime")]}


def test_today_is_the_utc_day():
    assert standings.today(NOW) == "2026-10-13" and standings.today(NOW - 1) == "2026-10-12"


def test_polymarket_records_every_board_and_follows_the_top_of_the_pnl_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(polymarket, "leaderboard", pm_board)
    monkeypatch.setattr(standings, "FOLLOW_TOP", 2)
    conn = database.connect(tmp_path / "copytrader.sqlite")
    assert standings.record_polymarket(conn, NOW) == (16 * 3, 2)        # 2 categories, 4 periods, 2 orders, 3 traders a board.
    followed = database.traders(conn, "polymarket")
    assert [(t.address, t.name, t.reason) for t in followed] == [("0xaa", "aa", "SPORTS/WEEK/PNL #1 on 2026-10-13"),
                                                                  ("0xbb", "bb", "SPORTS/WEEK/PNL #2 on 2026-10-13")]
    assert database.last_day(conn, "polymarket") == "2026-10-13"
    assert standings.record_polymarket(conn, NOW + 3600) == (16 * 3, 0)     # Recorded again, the same traders are not followed anew.


def test_hyperliquid_leaves_out_vaults_and_idle_accounts_and_follows_the_top(tmp_path, monkeypatch):
    rows = [hl_row("0xVAULT", 900), hl_row("0xA", 500), hl_row("0xB", 300), hl_row("0xC", 100), hl_row("0xIDLE", 800, month_volume=0)]
    monkeypatch.setattr(hyperliquid, "leaderboard", lambda: rows)
    monkeypatch.setattr(hyperliquid, "vaults", lambda: [{"summary": {"vaultAddress": "0xvault"}}])
    monkeypatch.setattr(standings, "FOLLOW_TOP", 2)
    conn = database.connect(tmp_path / "copytrader.sqlite")
    assert standings.record_hyperliquid(conn, NOW) == (3 * 4, 2)        # 0xa, 0xb, and 0xc, a standing for each window.
    followed = database.traders(conn, "hyperliquid")
    assert [(t.address, t.reason, t.role) for t in followed] == [("0xa", "week #1 on 2026-10-13", None), ("0xb", "week #2 on 2026-10-13", None)]
    stored = {row[0] for row in conn.execute("SELECT DISTINCT address FROM standings")}
    assert stored == {"0xa", "0xb", "0xc"}
