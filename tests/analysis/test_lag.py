"""
Tests for measuring Polymarket US's lag behind polymarket.com, on made up books.
"""

import json
import pytest
from collections import defaultdict
from copytrader.analysis import lag
from copytrader.db.models import LagBook, LagPair, Market, UsPrice

FEE = 0.0695


def book(venue, time, bids, asks):
    """
    A LagBook of levels of (price, contracts), best first.
    """
    return LagBook(venue, "k", time, None, json.dumps(bids), json.dumps(asks))


def com(time, bid, ask):
    return book("polymarket", time, [[bid, 1000]], [[ask, 1000]])


def us(time, bid, ask, size=100):
    return book("polymarket_us", time, [[bid, size]], [[ask, size]])


def test_each_moment_has_the_latest_book_of_each_venue_unless_it_is_too_old():
    line = lag.timeline([com(0, 0.43, 0.47), com(100, 0.43, 0.47)], [us(1, 0.39, 0.51)])
    assert [(t, c is not None, u is not None) for t, c, u in line] == [(0, True, False), (1, True, True), (100, True, False)]
    assert line[1][2] == ([(0.39, 100)], [(0.51, 100)]) and lag.mid(line[1][1]) == pytest.approx(0.45)
    assert lag.mid(line[1][2]) is None                     # A spread of 12 cents gives no price.


def test_a_move_on_one_venue_is_timed_until_the_other_has_moved_half_as_far():
    rows_com = [com(0, 0.49, 0.51), com(5, 0.54, 0.56), com(30, 0.54, 0.56)]
    rows_us = [us(1, 0.49, 0.51), us(10, 0.50, 0.52), us(20, 0.52, 0.54), us(40, 0.53, 0.55)]
    line = lag.timeline(rows_com, rows_us)
    assert lag.follows(line, 1) == [(5, 15)]               # polymarket.com up 5 cents at 5s; Polymarket US up 2.5 by 20s.
    assert lag.follows(line, 2) == []                      # Polymarket US never moves 3 cents within 10 seconds.
    stuck = lag.timeline(rows_com, [us(1, 0.49, 0.51), us(60, 0.49, 0.51)])
    assert lag.follows(stuck, 1) == [(5, None)]


def test_a_cheap_stretch_counts_every_dollar_offered_below_polymarket_coms_bid_fee_included():
    asks = [[0.40, 100], [0.42, 50], [0.47, 100]]
    line = lag.timeline([com(0, 0.46, 0.48), com(30, 0.46, 0.48)], [book("polymarket_us", 1, [[0.30, 10]], asks), us(20, 0.45, 0.50)])
    (stretch,) = lag.cheap_stretches(line, FEE)
    assert (stretch["side"], stretch["start"], stretch["end"], stretch["ask"]) == ("long", 1, 20, 0.40)
    assert stretch["dollars"] == pytest.approx(0.40 * 100 + 0.42 * 50) and stretch["cents"] == pytest.approx((0.46 - 0.40 - FEE * 0.24) * 100)
    short = lag.timeline([com(0, 0.40, 0.45)], [us(1, 0.60, 0.65, 10)])          # Short asks at 0.40, polymarket.com's short bid 0.55.
    assert [(s["side"], s["ask"], s["dollars"]) for s in lag.cheap_stretches(short, FEE)] == [("short", pytest.approx(0.40), pytest.approx(4.0))]


def test_the_price_history_is_set_beside_the_book_recorded_at_the_end_of_its_minute():
    prices = [UsPrice("s", 45, 0.45, 0.60), UsPrice("s", 60, 0.40, 0.60), UsPrice("s", 500, 0.40, 0.60), UsPrice("s", 30, 0.45, 0.60)]
    # Stamped 45, its minute ends after the book at 100 was read; stamped 30, it ends before, with no book yet.
    assert lag.history_check(prices, [us(100, 0.40, 0.45)]) == (2, 1, 1)


def market(paid='["1", "0"]'):
    return Market("0x1", "1", "m", "?", "e", "sports", None, None, False, None, None, None, True, None, '["A", "B"]', '["tok", "other"]', paid,
                  "resolved", None, 0.0)


PAIR = LagPair("aec-x", "tok", "0x1", "A", 7, "1970-01-01T00:00:10Z", "moneyline", FEE, 0.0)


def test_a_contract_bought_cheap_is_paid_by_how_its_side_resolved():
    assert lag.paid({"side": "long", "ask": 0.4}, PAIR, market()) == pytest.approx(0.6 - FEE * 0.24)
    assert lag.paid({"side": "short", "ask": 0.4}, PAIR, market()) == pytest.approx(-0.4 - FEE * 0.24)
    assert lag.paid({"side": "long", "ask": 0.4}, PAIR, None) is None


def test_a_pair_is_studied_before_and_after_its_games_start_and_reported():
    found = defaultdict(lag.Findings)
    rows_com = [com(0, 0.49, 0.51), com(12, 0.55, 0.57), com(40, 0.55, 0.57)]
    rows_us = [us(1, 0.49, 0.51), us(13, 0.48, 0.50), us(30, 0.54, 0.56)]
    lag.study(PAIR, rows_com, rows_us, [UsPrice("aec-x", 13 - 60, 0.50, 0.52)], market(), found)     # The book at 13, in play.
    before, playing = found[("moneyline", False)], found[("moneyline", True)]
    assert (before.pairs, playing.pairs, playing.com_lags, len(playing.cheap)) == (1, 1, [18], 1)
    # Cheap from 12s, when polymarket.com's bid rose to 0.55 and Polymarket US still asked 0.51, until its ask rose at 30s.
    assert playing.made == [pytest.approx(1 - 0.51 - FEE * 0.51 * 0.49)] and playing.history == (1, 1, 0)
    lines = lag.report(found)
    assert lines[0].startswith("moneyline, before the start: 1 markets") and "1 resolved, made +" in "\n".join(lines)
    lag.study(PAIR, rows_com, rows_us, [], market(), found, "nhl moneyline")
    assert found[("nhl moneyline", True)].com_lags == [18]
