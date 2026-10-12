"""
Tests for the Hyperliquid client: the info requests, paging over time, the weight they spend, and the stats files, with
no network.
"""

import httpx
import json
import pytest
from copytrader.api import hyperliquid


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
