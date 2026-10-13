"""
Tests for simulating copies on Polymarket US: who is copied, and what a copy of their trades makes, on made up traders,
markets, and prices.
"""

import numpy as np
import pytest
from copytrader.analysis import copy
from copytrader.analysis.match import Match
from copytrader.db.models import Activity, Market, UsMarket, UsPrice

DAY = copy.DAY


def history(*daily):
    """
    A PnL chart with a point a day from day 0, whose PnL changes by each of daily in turn.
    """
    return [(i * DAY, float(sum(daily[:i])), None) for i in range(len(daily) + 1)]


def act(timestamp, side="BUY", size=200.0, price=0.5, outcome=0, kind="TRADE", condition_id="0xm"):
    """
    One Activity of trader 0xa.
    """
    return Activity("0xa", timestamp, kind, condition_id, "1", None, outcome, side if kind == "TRADE" else "", size, size * price, price,
                    f"0x{timestamp}{kind}", None, None, 0)


def market(closed_time="1970-01-01T00:33:20Z", prices='["1", "0"]'):
    """
    A two outcome Market that resolved to its first outcome at 2,000 seconds since 1970, unless told otherwise.
    """
    return Market("0xm", "1", "m", "?", "e", "sports", None, "moneyline", False, None, None, None, True, closed_time, '["A", "B"]',
                  '["1", "2"]', prices, "resolved", None, 0.0)


def test_sharpe_ranks_steady_gains_above_bigger_but_wilder_ones():
    steady, wild = history(*[10, 12] * 42), history(*[500, -400] * 42)
    assert copy.sharpe(steady, 84 * DAY) > copy.sharpe(wild, 84 * DAY) > 0
    assert copy.sharpe(steady, 50 * DAY) is None                         # Its chart begins after the lookback does.
    assert copy.sharpe(history(*[0] * 84), 84 * DAY) is None
    assert copy.ranked({"0xs": steady, "0xw": wild, "0xout": steady}, {"0xs", "0xw"}, 84 * DAY) == ["0xs", "0xw"]


def test_a_trader_is_copyable_if_slow_enough_and_lately_in_sports_or_politics():
    trader = copy.Trader("0xa", 20.0, [50 * DAY], 100.0)
    assert copy.copyable(trader, 84 * DAY) and not copy.copyable(trader, 200 * DAY)
    assert not copy.copyable(copy.Trader("0xa", 150.0, [50 * DAY], 100.0), 84 * DAY)
    assert not copy.copyable(None, 84 * DAY)


def test_a_traders_summary_keeps_their_pace_their_median_buy_and_their_rows_in_matched_markets():
    rows = [act(10), act(20, price=0.25), act(DAY + 5, "SELL", 50), act(30, condition_id="0xother")]
    trader = copy.summary("0xa", rows, {"0xm": market()}, {"0xm": None})
    assert (trader.pace, trader.median_buy, len(trader.rows)) == (2.0, 100.0, 3)        # Buys of $100, $50, and $100.
    assert trader.category_times == [10, 20, DAY + 5]                                   # 0xother's market is not stored.


def test_signals_copy_buys_only_while_copied_and_sales_as_a_share_of_the_holding():
    rows = [act(10), act(20), act(30, "SELL", 100), act(40, kind="MERGE", size=150), act(50, "SELL", 10, outcome=1)]
    trader = copy.Trader("0xa", 1.0, [], 100.0, rows)
    found = copy.signals({"0xa": trader}, {"0xa": [(0, 15)]}, {"0xm": market()})
    # The buy at 20 is past the copied period; the sale sells 100 of 400; the merge 150 of the 300 left; nothing of outcome 1 is held.
    assert [(t, r.side, r.outcome_index, fraction) for t, _, r, fraction in found] == [(10, "BUY", 0, None), (30, "SELL", 0, 0.25),
                                                                                        (40, "SELL", 0, 0.5)]
    assert found[2][2].price == 0.5


def test_the_fee_is_rounded_half_to_even_to_the_cent_and_prices_go_stale():
    assert copy.fee(0.0695, 100, 0.5) == 1.74 and copy.fee(None, 100, 0.5) == 1.74 and copy.fee(0.1, 100, 0.5) == 2.5
    prices = copy.prices_of([UsPrice("s", 100, 0.4, 0.65), UsPrice("s", 160, None, 0.6)])
    assert copy.price_after(prices, 90) == (0.4, 0.65) and copy.price_after(prices, 150) is None
    assert copy.price_after(prices, 100 - copy.STALE - 1) is None and copy.price_after(prices, 200) is None
    assert copy.ask((0.4, 0.65), "long") == 0.4 and copy.ask((0.4, 0.65), "short") == 0.65
    assert copy.bid((0.4, 0.65), "long") == pytest.approx(0.35) and copy.bid((0.4, 0.65), "short") == pytest.approx(0.6)


def simulate(rows, prices, delay=60, capital=10_000, closed_time="1970-01-01T00:33:20Z"):
    """
    The copy of trader 0xa's rows in market 0xm, matched long for its first outcome, copied from 0 to 10,000 seconds.
    """
    trader = copy.Trader("0xa", 1.0, [], 100.0, rows)
    us = UsMarket("us", "1", "e", 1, "sports", "moneyline", None, "?", None, '["A","B"]', "A", None, True, None, None, None, 0.0695, 0.0)
    return copy.simulate([(0.0, 10_000.0, ["0xa"])], {"0xa": trader}, {"0xm": Match("0xm", "us", ("long", "short"), "slug")},
                         {"0xm": market(closed_time)}, {"us": us}, {"us": copy.prices_of(prices)}, capital, delay)


def test_a_copy_buys_late_at_the_ask_sells_the_same_share_at_the_bid_and_is_paid_at_resolution():
    out = simulate([act(100), act(500, "SELL", 100, 0.6)], [UsPrice("us", 160, 0.52, 0.5), UsPrice("us", 560, 0.62, 0.4)])
    # 2% of the $10,000 sleeve, fee included, buys 372.2 contracts at 0.52 for $6.46 of fee; half sell at 0.60 for $3.10.
    contracts = 200 / (0.52 * (1 + 0.0695 * 0.48))
    assert out["copied buys"] == 1 and out["copied sales"] == 1 and out["fees"] == pytest.approx(6.46 + 3.10)
    cost = contracts * 0.52 + 6.46
    assert out["pnl"] == pytest.approx(contracts / 2 * 0.6 - 3.10 + contracts / 2 - cost)
    # The ideal buys as many dollars at the trader's 0.50, sells half at their 0.60, and pays no fees.
    assert out["ideal pnl"] == pytest.approx(contracts * 0.52 / 0.5 * (0.6 / 2 + 1 / 2) - contracts * 0.52)
    assert out["median price above"] == pytest.approx(0.02) and out["still held, at cost"] == 0


def test_buys_without_a_fresh_price_or_after_the_close_are_skipped_and_what_is_held_counts_at_cost():
    out = simulate([act(100), act(5_000)], [UsPrice("us", 160 + copy.STALE + 1, 0.52, 0.5), UsPrice("us", 5_060, 0.52, 0.5)])
    assert (out["buys"], out["buys without a price"], out["buys after the market closed"], out["copied buys"]) == (2, 1, 1, 0)
    unresolved = simulate([act(100)], [UsPrice("us", 160, 0.52, 0.5)], closed_time=None)
    assert unresolved["pnl"] == 0 and unresolved["still held, at cost"] == pytest.approx(200, abs=0.01)


def test_a_buy_bigger_than_the_traders_median_spends_more_up_to_a_limit_and_never_more_than_the_cash():
    out = simulate([act(100, size=2000), act(200, size=2000)], [UsPrice("us", 160, 0.5, 0.5), UsPrice("us", 260, 0.5, 0.5)], capital=500)
    # Ten times the median buy spends twice 2% of the $500 sleeve, $20 a buy, fee included.
    assert out["copied buys"] == 2 and out["spent"] + out["fees"] == pytest.approx(40, abs=0.01)
    assert np.isclose(out["largest order"], out["median order"])


def test_an_order_walks_the_book_and_pays_its_average_beyond_the_best_price():
    levels = [(0.40, 100), (0.50, 100)]             # $40 at 0.40, then $50 at 0.50.
    assert copy.premium(levels, 20) == 0
    assert copy.premium(levels, 65) == pytest.approx(65 / (100 + 25 / 0.5) - 0.40)
    assert copy.premium(levels, 91) is None


def test_the_depth_model_takes_the_median_book_of_each_type_and_cuts_what_most_cannot_fill(monkeypatch):
    monkeypatch.setattr(copy, "SIZES", (10, 100))
    us = {s: UsMarket(s, "1", "e", 1, "sports", kind, None, "?", None, "[]", None, None, False, None, None, None, None, 0.0)
          for s, kind in (("a", "moneyline"), ("b", "moneyline"), ("t", "totals"))}
    deep, thin = [(0.5, 1000)], [(0.5, 30), (0.6, 1000)]
    books = {("a", 1): {"offer": deep, "bid": []}, ("b", 1): {"offer": thin, "bid": [(0.4, 1000)]}, ("t", 1): {"offer": [(0.5, 40)], "bid": []}}
    model = copy.depth(books, us)
    # Moneylines: three sides, deep, thin, and the deep bid, as buying 0.60 of the short side. The median pays nothing.
    assert model["moneyline"] == {10: 0, 100: 0} and model["totals"] == {10: 0, 100: None}
    assert copy.fillable(model, "moneyline") == 100 and copy.fillable(model, "totals") == 10 and copy.fillable(model, "spreads") == np.inf
    assert copy.slippage({"x": {10: 0.0, 100: 0.02}}, "x", 55) == pytest.approx(0.01) and copy.slippage(model, "spreads", 1e6) == 0
