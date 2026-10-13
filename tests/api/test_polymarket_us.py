"""
Tests for the Polymarket US client: paging events over a window of start times, asking for a stretch of price history,
and turning markets and prices into models, with no network.
"""

import httpx
from copytrader.api import polymarket_us

# A game's event and its moneyline as Polymarket US sends them, cut down from real answers.
MONEYLINE = {"id": "2", "slug": "aec-nfl-car-gb-2025-11-02", "question": "Carolina vs. Green Bay", "title": "CAR Panthers vs GB Packers",
             "category": "sports", "marketType": "moneyline", "sportsMarketType": "moneyline", "closed": True,
             "status": "MARKET_STATUS_RESOLVED", "gameStartTime": "2025-11-02T18:00:00Z", "endDate": "2025-11-03T18:00:00Z",
             "outcomes": '["Panthers","Packers"]', "outcomePrices": '["1","0"]', "feeCoefficient": 0.0695,
             "marketSides": [{"description": "Packers", "long": False}, {"description": "Panthers", "long": True}]}
EVENT = {"id": "1", "slug": "aec-nfl-car-gb-2025-11-02", "gameId": 19072, "category": "sports", "startTime": "2025-11-02T18:00:00Z",
         "markets": [MONEYLINE]}


def test_events_page_by_offset_over_a_window_of_start_times(serve, monkeypatch):
    monkeypatch.setattr(polymarket_us, "EVENTS_PAGE", 2)
    events = [{"slug": f"e{i}"} for i in range(5)]
    sent = serve(lambda request: httpx.Response(200, json={"events": events[int(request.url.params["offset"]):][:2]}))
    assert [e["slug"] for e in polymarket_us.events(1762041600, 1762128000, closed=True)] == ["e0", "e1", "e2", "e3", "e4"]
    assert [request.url.params["offset"] for request in sent] == ["0", "2", "4"]
    params = sent[0].url.params
    assert (params["start_date_min"], params["start_date_max"], params["closed"]) == ("2025-11-02T00:00:00Z", "2025-11-03T00:00:00Z", "true")


def test_price_history_asks_for_a_stretch_a_minute_apart_and_reads_nothing_as_empty(serve):
    sent = serve(lambda request: httpx.Response(200, json={"history": [{"timestamp": 1762101000, "longPrice": 0.178, "shortPrice": 0.923}]}))
    assert polymarket_us.price_history("aec-nfl-car-gb-2025-11-02", 1762101000, 1762104600.5)[0]["longPrice"] == 0.178
    params = sent[0].url.params
    assert (params["symbol"], params["timestamp.startTimestamp"], params["timestamp.endTimestamp"], params["fidelity"]) == \
        ("aec-nfl-car-gb-2025-11-02", "1762101000", "1762104600", "1")
    serve(lambda request: httpx.Response(200, json={}))
    assert polymarket_us.price_history("unknown", 1, 2) == []


def test_a_market_takes_its_events_game_and_names_the_outcome_its_long_side_holds():
    m = polymarket_us.parse_market(EVENT, MONEYLINE, 99.0)
    assert (m.slug, m.event_slug, m.game_id, m.market_type, m.long_outcome, m.closed, m.fee_coefficient) == \
        ("aec-nfl-car-gb-2025-11-02", "aec-nfl-car-gb-2025-11-02", 19072, "moneyline", "Panthers", True, 0.0695)
    futures = polymarket_us.parse_market({"slug": "mlb-nlchamp", "category": "sports", "startTime": "2026-09-07T00:00:00Z"},
                                         {"id": 7, "slug": "tec-mlb-nlchamp-mil", "outcomes": '["Yes","No"]'}, 99.0)
    assert (futures.game_id, futures.long_outcome, futures.game_start, futures.category) == (None, None, "2026-09-07T00:00:00Z", "sports")


def test_prices_become_one_row_a_point():
    rows = polymarket_us.parse_prices("s", [{"timestamp": 60, "longPrice": 0.4, "shortPrice": 0.65}, {"timestamp": 120}])
    assert [(r.time, r.long_price, r.short_price) for r in rows] == [(60, 0.4, 0.65), (120, None, None)]
