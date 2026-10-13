"""
Tests for the Polymarket client: paging by offset and by keyset cursor, reading a trader's activity in windows of time,
and turning its rows into models, with no network.
"""

import httpx
import json
import pytest
from copytrader.api import polymarket

# Rows as Polymarket sends them, cut down from real answers.
MARKET = {"id": "5524031", "conditionId": "0x3e6f", "slug": "wta-iatcenk-tan-2026-10-12-completed-match", "negRisk": False,
          "question": "Rovereto: Completed Match: Polina Iatcenko vs Harmony Tan", "closed": True, "closedTime": "2026-10-12 17:35:00.151622+00",
          "startDate": "2026-10-11T15:02:11.432Z", "endDate": "2026-10-19T08:00:00Z", "gameStartTime": "2026-10-12 15:00:00+00",
          "sportsMarketType": "moneyline", "volumeNum": 18250.5,
          "outcomes": '["Yes", "No"]', "outcomePrices": '["1", "0"]', "clobTokenIds": '["1341", "7722"]', "umaResolutionStatus": "resolved",
          "events": [{"id": "1167237", "slug": "wta-iatcenk-tan-2026-10-12"}],
          "tags": [{"slug": "tennis"}, {"slug": "sports"}, {"slug": "games"}]}
TRADE = {"proxyWallet": "0x51698A47F840A242ABC2CA0351371C7FFAC41842", "timestamp": 1789485901, "conditionId": "0x8b01", "type": "TRADE",
         "size": 43.233332, "usdcSize": 25.94, "transactionHash": "0xd762", "price": 0.6, "asset": "3407", "side": "BUY", "outcomeIndex": 0,
         "outcome": "Yes", "slug": "lal-ala-val-2026-09-15-ala", "eventSlug": "lal-ala-val-2026-09-15", "name": "00gringo00"}
REBATE = {**TRADE, "type": "MAKER_REBATE", "conditionId": "", "asset": "", "side": "", "outcomeIndex": 999, "outcome": "", "slug": "",
          "eventSlug": "", "price": 0, "size": 439.648, "usdcSize": 439.648, "transactionHash": "0xe1"}


def activity_server(rows):
    """
    A handler answering /activity as the data host does, oldest first: the rows from start to end, both included, a page
    of them from the offset, and an error past ACTIVITY_MAX_OFFSET.
    """
    def answer(request):
        params = request.url.params
        offset, limit = int(params["offset"]), int(params["limit"])
        if offset > polymarket.ACTIVITY_MAX_OFFSET:
            return httpx.Response(400, json={"error": f"max historical activity offset of {polymarket.ACTIVITY_MAX_OFFSET} exceeded"})
        start, end = int(params.get("start", 0)), int(params.get("end", 10**12))
        found = sorted((row for row in rows if start <= row["timestamp"] <= end), key=lambda row: row["timestamp"])
        return httpx.Response(200, json=found[offset:offset + limit])
    return answer


@pytest.fixture
def small_windows(monkeypatch):
    """
    Two activity rows a call and offsets up to 2, so a window of time reads at most 4 rows.
    """
    monkeypatch.setattr(polymarket, "ACTIVITY_PAGE", 2)
    monkeypatch.setattr(polymarket, "ACTIVITY_MAX_OFFSET", 2)


def test_activity_reads_past_the_offset_limit_in_windows_without_missing_or_repeating_a_row(serve, small_windows):
    rows = [{"id": i, "timestamp": ts} for i, ts in enumerate([1, 2, 3, 3, 3, 4, 5, 6, 7])]
    sent = serve(activity_server(rows))
    assert [row["id"] for row in polymarket.activity("0xabc")] == list(range(9))
    assert sorted({request.url.params["start"] for request in sent if "start" in request.url.params}) == ["3", "4", "7"]
    assert all(request.url.params["sortDirection"] == "ASC" for request in sent)


def test_activity_passes_its_filters(serve, small_windows):
    sent = serve(activity_server([]))
    list(polymarket.activity("0xabc", types=["TRADE", "REDEEM"], start=100, end=200))
    params = sent[0].url.params
    assert (params["user"], params["type"], params["start"], params["end"]) == ("0xabc", "TRADE,REDEEM", "100", "200")


def test_activity_raises_when_one_second_holds_more_than_a_window(serve, small_windows):
    serve(activity_server([{"id": i, "timestamp": 5} for i in range(5)]))
    with pytest.raises(ValueError, match="at 5"):
        list(polymarket.activity("0xabc"))


def test_trades_include_the_makers_and_join_a_list_of_markets(serve):
    sent = serve(lambda request: httpx.Response(200, json=[]))
    list(polymarket.trades(market=["0x1", "0x2"]))
    assert dict(sent[0].url.params) == {"market": "0x1,0x2", "takerOnly": "false", "limit": "500", "offset": "0"}


def test_trades_raise_rather_than_stop_short_at_the_offset_limit(serve, monkeypatch):
    monkeypatch.setattr(polymarket, "TRADES_PAGE", 2)
    monkeypatch.setattr(polymarket, "TRADES_MAX_OFFSET", 4)
    sent = serve(lambda request: httpx.Response(200, json=[{"n": 1}, {"n": 2}]))
    with pytest.raises(ValueError, match="more rows than offset 4"):
        list(polymarket.trades(user="0xabc"))
    assert [request.url.params["offset"] for request in sent] == ["0", "2", "4"]


def test_closed_positions_page_by_offset_until_a_short_page(serve):
    counts = {0: 50, 50: 50, 100: 10}
    sent = serve(lambda request: httpx.Response(200, json=[{}] * counts[int(request.url.params["offset"])]))
    assert len(list(polymarket.closed_positions("0xabc"))) == 110
    assert len(sent) == 3


def test_the_leaderboard_stops_at_its_limit(serve):
    sent = serve(lambda request: httpx.Response(200, json=[{"rank": str(int(request.url.params["offset"]) + i + 1)} for i in range(50)]))
    rows = list(polymarket.leaderboard("SPORTS", "MONTH", limit=70))
    assert len(rows) == 70 and rows[-1]["rank"] == "70"
    assert [request.url.params["offset"] for request in sent] == ["0", "50"]
    params = sent[0].url.params
    assert (params["category"], params["timePeriod"], params["orderBy"]) == ("SPORTS", "MONTH", "PNL")


def test_markets_follow_the_keyset_cursor(serve):
    pages = {None: {"markets": [{"id": "1"}, {"id": "2"}], "next_cursor": "c1"}, "c1": {"markets": [{"id": "3"}], "next_cursor": None}}
    sent = serve(lambda request: httpx.Response(200, json=pages[request.url.params.get("after_cursor")]))
    assert [m["id"] for m in polymarket.markets(tag_id=polymarket.SPORTS_TAG, closed=True)] == ["1", "2", "3"]
    assert all(request.url.path == "/markets/keyset" for request in sent)
    assert all((r.url.params["tag_id"], r.url.params["closed"], r.url.params["limit"]) == ("1", "true", "100") for r in sent)


def test_the_order_book_host_gives_midpoints_and_price_history(serve):
    answers = {"/midpoint": {"mid": "0.42"}, "/prices-history": {"history": [{"t": 1, "p": 0.5}]}}
    sent = serve(lambda request: httpx.Response(200, json=answers[request.url.path]))
    assert polymarket.midpoint("123") == 0.42
    assert polymarket.price_history("123", interval="max") == [{"t": 1, "p": 0.5}]
    assert dict(sent[1].url.params) == {"market": "123", "interval": "max"}


def test_value_is_zero_for_a_trader_without_positions(serve):
    answers = iter([[{"user": "0xabc", "value": 12.5}], []])
    serve(lambda request: httpx.Response(200, json=next(answers)))
    assert polymarket.value("0xabc") == 12.5
    assert polymarket.value("0xabc") == 0.0


def test_a_leaderboard_row_gives_its_standing():
    row = {"rank": "3", "proxyWallet": "0x4F1D", "userName": "e46m3", "vol": 2473568.9, "pnl": 1959318.6}
    found = polymarket.parse_standing(row, "SPORTS/MONTH/PNL", "2026-10-13", 1791849600.0)
    assert (found.venue, found.board, found.address, found.rank, found.pnl, found.volume) == \
        ("polymarket", "SPORTS/MONTH/PNL", "0x4f1d", 3, 1959318.6, 2473568.9)


def test_a_market_takes_its_category_from_its_tags():
    found = polymarket.parse_market(MARKET, 1791849600.0)
    assert (found.condition_id, found.market_id, found.event_slug, found.category, found.market_type) == \
        ("0x3e6f", "5524031", "wta-iatcenk-tan-2026-10-12", "sports", "moneyline")
    assert (found.closed, found.outcome_prices, found.token_ids, found.resolution) == (True, '["1", "0"]', '["1341", "7722"]', "resolved")
    assert (found.start_date, found.volume) == ("2026-10-11T15:02:11.432Z", 18250.5)
    assert json.loads(found.tags) == ["tennis", "sports", "games"]
    assert polymarket.parse_market({**MARKET, "tags": [{"slug": "crypto"}]}, 0).category is None
    assert polymarket.parse_market({k: v for k, v in MARKET.items() if k != "tags"}, 0).tags is None    # Read without include_tag.


def test_activity_rows_alike_in_one_transaction_are_numbered_apart():
    other_second = {**TRADE, "timestamp": TRADE["timestamp"] + 1, "transactionHash": "0xf0"}
    found = list(polymarket.parse_activity([TRADE, REBATE, TRADE, other_second, other_second]))
    assert [a.seq for a in found] == [0, 0, 1, 0, 1]
    trade, rebate = found[0], found[1]
    assert (trade.address, trade.side, trade.size, trade.usdc_size, trade.outcome_index) == \
        ("0x51698a47f840a242abc2ca0351371c7ffac41842", "BUY", 43.233332, 25.94, 0)
    assert (rebate.condition_id, rebate.asset, rebate.side, rebate.outcome, rebate.slug) == ("", "", "", None, None)


def test_a_pnl_chart_gives_a_point_a_day():
    found = polymarket.parse_pnl("0xa", [{"t": 1788912000, "p": 683444}, {"t": 1788998400, "p": 996377.3}])
    assert [(p.venue, p.address, p.time, p.pnl, p.account_value) for p in found] == [("polymarket", "0xa", 1788912000, 683444.0, None),
                                                                                    ("polymarket", "0xa", 1788998400, 996377.3, None)]
