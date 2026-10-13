"""
Tests for rebuilding the followed traders' results and telling their kinds apart, on made up activity and fills.
"""

import json
import pytest
from copytrader.analysis import traders
from copytrader.db.models import Activity, Fill, Market

START = 1790000000          # 2026-09-21, seconds since 1970.


def market(condition_id, category="sports", prices='["1", "0"]', resolution="resolved", start_date="2026-09-22T00:00:00Z"):
    """
    A two outcome Market, resolved to its first outcome unless told otherwise.
    """
    return Market(condition_id, condition_id, None, "?", None, category, None, None, False, start_date, None, None, True, None,
                  '["Yes", "No"]', '["1", "2"]', prices, resolution, None, 0.0)


def act(timestamp, kind, size=0.0, usdc=0.0, side="", outcome=None, condition_id="0xm", price=0.0):
    """
    One Activity of trader 0xa.
    """
    return Activity("0xa", timestamp, kind, condition_id, "1" if outcome is not None else "", None, outcome, side, size, usdc, price,
                    f"0xt{timestamp}", None, None, 0)


def fill(time, coin="BTC", closed_pnl=0.0, fee=0.0, crossed=True, liquidation=None):
    """
    One Fill of account 0xa, at a time in milliseconds since 1970.
    """
    return Fill("0xa", time, time, time, coin, "B", 100.0, 1.0, None, None, closed_pnl, fee, "USDC", crossed, None, None,
                json.dumps(liquidation) if liquidation else None)


def test_a_markets_pnl_counts_cash_both_ways_and_the_shares_still_held():
    m = market("0xm")
    bought_and_held = [act(1, "TRADE", 100, 40, "BUY", 0), act(2, "TRADE", 20, 12, "SELL", 0)]
    assert traders.market_pnl(bought_and_held, m) == (pytest.approx(52.0), 40.0)          # -40 + 12 + 80 shares paid a dollar.
    redeemed = [act(1, "TRADE", 100, 40, "BUY", 0), act(9, "REDEEM", 100, 100)]
    assert traders.market_pnl(redeemed, m) == (pytest.approx(60.0), 40.0)
    split_then_sold_no = [act(1, "SPLIT", 10, 10), act(2, "TRADE", 10, 3, "SELL", 1)]
    assert traders.market_pnl(split_then_sold_no, m)[0] == pytest.approx(3.0)             # -10 + 3 + 10 Yes shares paid.
    assert traders.market_pnl(bought_and_held, market("0xm", prices='["0", "1"]'))[0] == pytest.approx(-28.0)


def test_only_resolved_sports_and_politics_markets_opened_after_the_first_row_count():
    begun = START
    assert traders.counted(market("0xm"), begun) and traders.counted(market("0xm", "politics"), begun)
    assert not traders.counted(market("0xm", "crypto"), begun)
    assert not traders.counted(market("0xm", resolution="proposed"), begun)
    assert not traders.counted(market("0xm", start_date="2026-09-01T00:00:00Z"), begun)       # Its first trades may be missing.
    assert not traders.counted(None, begun)


def test_a_polymarket_trader_is_rebuilt_from_their_activity_and_markets():
    rows = [act(START, "TRADE", 100, 40, "BUY", 0, "0xwin", 0.4), act(START + 10, "TRADE", 50, 47.5, "BUY", 1, "0xlose", 0.95),
            act(START + 20, "MAKER_REBATE", usdc=2.5), act(START + 30, "TRADE", 10, 5, "BUY", 0, "0xopen", 0.5)]
    markets = {"0xwin": market("0xwin"), "0xlose": market("0xlose"), "0xopen": market("0xopen", resolution=None)}
    found = traders.polymarket_result("0xa", rows, markets)
    assert (found.markets, found.trades, found.pnl, found.spent, found.income) == (2, 3, pytest.approx(12.5), 87.5, 2.5)
    assert (found.win_rate, found.top_share) == (0.5, 1.0)
    assert found.favorite_share == pytest.approx(47.5 / 92.5) and found.kind == "few"        # 2 markets are too few to judge.


def test_kinds_go_to_the_first_that_fits(monkeypatch):
    monkeypatch.setattr(traders, "MIN_MARKETS", 2)
    base = traders.Result("polymarket", "0xa", 5, 50, 10.0, 100.0, 1000.0, 0.6, 0.3, 0.2, None, 0.0, 0)
    assert traders.kind(base) == "directional"
    assert traders.kind(traders.Result(**{**base.__dict__, "markets": 1})) == "few"
    assert traders.kind(traders.Result(**{**base.__dict__, "trades_per_day": 150.0})) == "bot"
    assert traders.kind(traders.Result(**{**base.__dict__, "markets": 1, "trades_per_day": 150.0})) == "bot"      # The pace tells first.
    assert traders.kind(traders.Result(**{**base.__dict__, "top_share": 0.8})) == "one-hit"
    assert traders.kind(traders.Result(**{**base.__dict__, "favorite_share": 0.7})) == "favorites"


def test_a_hyperliquid_account_counts_crypto_perps_alone_less_fees_and_its_own_liquidations():
    fills = [fill(1, "BTC", 100.0, 1.0, crossed=False), fill(2, "ETH", -20.0, 1.0), fill(3, "xyz:AAPL", 500.0),
             fill(4, "ETH", 0.0, 0.5, liquidation={"liquidatedUser": "0xA", "markPx": "1", "method": "market"}),
             fill(5, "ETH", 0.0, 0.5, liquidation={"liquidatedUser": "0xother", "markPx": "1", "method": "market"})]
    found = traders.hyperliquid_result("0xa", fills)
    # 99 on BTC, less 21 and two fees of 0.5 on ETH. AAPL is a HIP-3 perp, and the second liquidation was another account's.
    assert (found.markets, found.trades, found.pnl, found.liquidations) == (2, 4, pytest.approx(77.0), 1)
    assert (found.maker_share, found.top_share, found.kind) == (0.25, 1.0, "few")


def test_the_report_counts_the_kinds_and_lists_the_best_directional_traders():
    found = [traders.Result("polymarket", f"0x{i}", 20, 40, 5.0, float(pnl), 1000.0, 0.5, 0.2, 0.1, None, 0.0, 0, kind)
             for i, (pnl, kind) in enumerate(((300, "directional"), (500, "directional"), (900, "bot")))]
    lines = traders.report(found, top=1)
    assert lines[0] == "3 traders: 2 directional, 0 favorites, 0 one-hit, 1 bot, 0 few"
    assert lines[-1].startswith("0x1 ") and "+500" in lines[-1]
