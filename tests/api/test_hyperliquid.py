"""
Tests for the Hyperliquid client: the info requests, paging over time, the weight they spend, the stats files, and turning
its rows into models, with no network.
"""

import httpx
import json
import pytest
from copytrader.api import hyperliquid
from copytrader.db.models import Funding

# Rows as Hyperliquid sends them, cut down from real answers.
LEADERBOARD_ROW = {"ethAddress": "0x85ECF584F25DB6F146718B86D493E33C5AF72052", "accountValue": "60811517.1", "prize": 0, "displayName": None,
                   "windowPerformances": [["day", {"pnl": "-2097166.4", "roi": "-0.036", "vlm": "1537204163.0"}],
                                          ["week", {"pnl": "-2072765.6", "roi": "-0.034", "vlm": "7900542261.4"}],
                                          ["month", {"pnl": "-3242338.1", "roi": "-0.047", "vlm": "41279656603.9"}],
                                          ["allTime", {"pnl": "3149243.2", "roi": "0.026", "vlm": "225866356751.9"}]]}
LIQUIDATION_FILL = {"coin": "ETH", "px": "2504.2", "sz": "0.1894", "side": "B", "time": 1791826682189, "startPosition": "-10.6657",
                    "dir": "Close Short", "closedPnl": "0.058714", "hash": "0x1766", "oid": 572262306346, "crossed": False, "fee": "-0.009485",
                    "tid": 490577063205138, "cloid": "0x5c9d", "feeToken": "USDC", "twapId": None,
                    "liquidation": {"liquidatedUser": "0x0536", "markPx": "2504.3", "method": "market"}}
STATE = {"marginSummary": {"accountValue": "1723575.7", "totalNtlPos": "957575.8", "totalRawUsd": "1205945.1", "totalMarginUsed": "319191.9"},
         "withdrawable": "1404383.7", "time": 1791826690000,
         "assetPositions": [{"type": "oneWay", "position": {"coin": "BTC", "szi": "6.08739", "leverage": {"type": "cross", "value": 3},
                                                            "entryPx": "82415.8", "positionValue": "501911.3", "unrealizedPnl": "214.1",
                                                            "liquidationPx": None, "marginUsed": "167303.7", "maxLeverage": 40}},
                            {"type": "oneWay", "position": {"coin": "ETH", "szi": "-120.5", "leverage": {"type": "isolated", "value": 10},
                                                            "entryPx": "2510.0", "positionValue": "301760.1", "unrealizedPnl": "-450.0",
                                                            "liquidationPx": "2711.4", "marginUsed": "30176.0", "maxLeverage": 25}}]}


class Spent:
    """
    A stand in for the rate limiter that records the weight each request spends.
    """

    def __init__(self):
        self.weights = []

    def wait(self, cost=1):
        self.weights.append(cost)


def over_time(rows, page):
    """
    A handler answering an info call over time as Hyperliquid does: the rows from startTime to endTime, both included,
    oldest first, at most a page of them.
    """
    def answer(request):
        body = json.loads(request.content)
        found = [row for row in rows if body["startTime"] <= row["time"] <= body.get("endTime", float("inf"))]
        return httpx.Response(200, json=found[:page])
    return answer


def test_only_coins_on_hyperliquids_own_dex_are_crypto_perps():
    assert hyperliquid.is_crypto_perp("BTC") and hyperliquid.is_crypto_perp("kPEPE")
    assert not any(hyperliquid.is_crypto_perp(coin) for coin in ("xyz:AAPL", "@107", "PURR/USDC"))     # A HIP-3 perp and two spot pairs.


def test_an_info_request_posts_its_type_and_leaves_out_unset_fields(serve):
    sent = serve(lambda request: httpx.Response(200, json=[]))
    assert list(hyperliquid.fills("0xabc", 1000)) == []
    assert str(sent[0].url) == hyperliquid.INFO
    assert json.loads(sent[0].content) == {"type": "userFillsByTime", "user": "0xabc", "startTime": 1000}


def test_candles_nest_their_request(serve):
    sent = serve(lambda request: httpx.Response(200, json=[]))
    hyperliquid.candles("BTC", "1h", 5, 10)
    assert json.loads(sent[0].content) == {"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": 5, "endTime": 10}}


def test_fills_page_through_a_millisecond_split_between_pages_without_missing_or_repeating_a_fill(serve, monkeypatch):
    monkeypatch.setattr(hyperliquid, "FILLS_PAGE", 3)
    rows = [{"tid": tid, "time": t} for tid, t in enumerate([10, 15, 20, 20, 30, 40, 50])]
    sent = serve(over_time(rows, 3))
    assert list(hyperliquid.fills("0xabc")) == rows
    assert [json.loads(request.content)["startTime"] for request in sent] == [0, 20, 30, 50]    # The first page ends between the fills at 20.


def test_paging_stops_at_the_end_time(serve, monkeypatch):
    monkeypatch.setattr(hyperliquid, "FUNDING_PAGE", 2)
    rows = [{"time": t} for t in range(1, 7)]
    sent = serve(over_time(rows, 2))
    assert list(hyperliquid.funding_payments("0xabc", 2, 4)) == rows[1:4]
    assert all(json.loads(request.content)["endTime"] == 4 for request in sent)


def test_a_full_page_all_at_one_time_raises_rather_than_loops(serve, monkeypatch):
    monkeypatch.setattr(hyperliquid, "LEDGER_PAGE", 2)
    serve(over_time([{"time": 7}] * 3, 2))
    with pytest.raises(ValueError, match="all at 7"):
        list(hyperliquid.ledger("0xabc", 7))


def test_rows_spend_their_extra_weight_once_the_answer_is_in(serve, monkeypatch):
    spent = Spent()
    monkeypatch.setattr(hyperliquid, "LIMITER", spent)
    serve(lambda request: httpx.Response(200, json=[{"time": t} for t in range(45)]))
    hyperliquid.recent_fills("0xabc")
    hyperliquid.positions("0xabc")
    hyperliquid.role("0xabc")
    # 45 fills weigh 2 more. The same rows answering positions and role weigh nothing more, as those calls do not count rows.
    assert spent.weights == [hyperliquid.WEIGHT, 2, hyperliquid.LIGHT_WEIGHT, hyperliquid.ROLE_WEIGHT]


def test_the_leaderboard_and_vaults_come_from_the_stats_host(serve):
    files = {"/Mainnet/leaderboard": {"leaderboardRows": [{"ethAddress": "0x1"}]}, "/Mainnet/vaults": [{"summary": {"vaultAddress": "0x2"}}]}
    sent = serve(lambda request: httpx.Response(200, json=files[request.url.path]))
    assert hyperliquid.leaderboard() == [{"ethAddress": "0x1"}]
    assert hyperliquid.vaults() == [{"summary": {"vaultAddress": "0x2"}}]
    assert {request.url.host for request in sent} == {"stats-data.hyperliquid.xyz"}


def test_a_leaderboard_row_gives_a_standing_for_each_window():
    found = hyperliquid.parse_standings(LEADERBOARD_ROW, "2026-10-13", 1791849600.0)
    assert [s.board for s in found] == ["day", "week", "month", "allTime"]
    month = found[2]
    assert (month.venue, month.address, month.rank) == ("hyperliquid", "0x85ecf584f25db6f146718b86d493e33c5af72052", None)     # Lowercase.
    assert (month.pnl, month.roi, month.volume, month.account_value) == (-3242338.1, -0.047, 41279656603.9, 60811517.1)


def test_a_fill_keeps_its_liquidation():
    found = hyperliquid.parse_fill("0xa", LIQUIDATION_FILL)
    assert (found.tid, found.order_id, found.time, found.coin, found.side) == (490577063205138, 572262306346, 1791826682189, "ETH", "B")
    assert (found.price, found.size, found.start_position, found.closed_pnl, found.fee) == (2504.2, 0.1894, -10.6657, 0.058714, -0.009485)
    assert found.crossed is False and json.loads(found.liquidation)["liquidatedUser"] == "0x0536"
    assert hyperliquid.parse_fill("0xa", {**LIQUIDATION_FILL, "liquidation": None}).liquidation is None


def test_funding_and_ledger_rows_are_parsed():
    payment = {"time": 1771891200000, "hash": "0x0", "delta": {"type": "funding", "coin": "ETH", "usdc": "0.782347", "szi": "-5.77",
                                                               "fundingRate": "0.0000090922", "nSamples": 8}}
    assert hyperliquid.parse_funding("0xa", payment) == Funding("0xa", 1771891200000, "ETH", 0.782347, -5.77, 0.0000090922)
    update = hyperliquid.parse_ledger("0xa", {"time": 1765994269696, "hash": "0x70df", "delta": {"usdc": "49.8", "type": "deposit"}})
    assert (update.kind, update.usdc, update.delta) == ("deposit", 49.8, '{"type": "deposit", "usdc": "49.8"}')    # Keys sorted.


def test_a_positions_answer_gives_the_account_and_each_position_at_its_time():
    account, positions = hyperliquid.parse_account("0xa", STATE)
    assert (account.time, account.account_value, account.total_notional, account.margin_used, account.withdrawable) == \
        (1791826690000, 1723575.7, 957575.8, 319191.9, 1404383.7)
    btc, eth = positions
    assert (btc.coin, btc.size, btc.leverage, btc.leverage_type, btc.liquidation_price) == ("BTC", 6.08739, 3.0, "cross", None)
    assert (eth.size, eth.leverage_type, eth.liquidation_price, eth.time) == (-120.5, "isolated", 2711.4, 1791826690000)
