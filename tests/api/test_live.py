"""
Smoke tests against the real APIs, skipped unless asked for with `python3 -m pytest -m network`.
"""

import itertools
import json
import pytest
import time
from copytrader.api import hyperliquid, polymarket

pytestmark = pytest.mark.network

HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"     # Hyperliquid's own market making vault.


def test_hyperliquid_lists_its_own_dex_and_a_hip3_one():
    assert "BTC" in {coin["name"] for coin in hyperliquid.meta()["universe"]}
    assert all(coin["name"].startswith("xyz:") for coin in hyperliquid.meta("xyz")["universe"])


def test_hlp_is_a_vault_with_live_positions():
    assert hyperliquid.role(HLP)["role"] == "vault"
    assert {"marginSummary", "assetPositions"} <= hyperliquid.positions(HLP).keys()


def test_fills_over_time_agree_with_the_latest_fills():
    trader = hyperliquid.recent_trades("BTC")[0]["users"][0]
    newest = hyperliquid.recent_fills(trader)[0]["time"]        # Not the clock's time, since some accounts' fills lag.
    times = [fill["time"] for fill in hyperliquid.fills(trader, newest - 3_600_000)]
    assert times == sorted(times) and times[0] >= newest - 3_600_000 and times[-1] == newest


def test_funding_rates_come_hourly():
    week_ago = int(time.time() * 1000) - 7 * 86_400_000
    assert 7 * 24 - 2 <= len(list(hyperliquid.funding_rates("BTC", week_ago))) <= 7 * 24 + 2


def test_the_sports_leaderboard_gives_proxy_wallets():
    rows = list(polymarket.leaderboard("SPORTS", "MONTH", limit=60))
    assert len(rows) == 60 and rows[0]["proxyWallet"].startswith("0x")


def test_a_top_politics_traders_activity_filters_by_type():
    (top,) = polymarket.leaderboard("POLITICS", "MONTH", limit=1)
    month_ago = int(time.time()) - 30 * 86_400         # The month the ranking covers, so the trader traded in it.
    rows = list(itertools.islice(polymarket.activity(top["proxyWallet"], types="TRADE", start=month_ago), 5))
    assert rows and all(row["type"] == "TRADE" and row["timestamp"] >= month_ago for row in rows)


def test_markets_filter_by_the_sports_and_politics_tags():
    for tag_id, slug in ((polymarket.SPORTS_TAG, "sports"), (polymarket.POLITICS_TAG, "politics")):
        assert polymarket.tag(slug)["id"] == str(tag_id)
        for market in itertools.islice(polymarket.markets(tag_id=tag_id, closed=True, include_tag=True), 3):
            assert slug in {t["slug"] for t in market["tags"]}


def test_markets_are_found_by_a_list_of_condition_ids():
    sample = list(itertools.islice(polymarket.markets(closed=True), 2))
    found = polymarket.markets(condition_ids=[m["conditionId"] for m in sample], closed=True)
    assert {m["id"] for m in found} == {m["id"] for m in sample}


def test_the_busiest_markets_book_and_midpoint():
    market = next(polymarket.markets(closed=False, active=True, order="volume24hr", ascending=False))
    token_id = json.loads(market["clobTokenIds"])[0]
    assert polymarket.book(token_id)["asset_id"] == token_id and 0 <= polymarket.midpoint(token_id) <= 1
