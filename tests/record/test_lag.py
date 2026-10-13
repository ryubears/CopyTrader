"""
Tests for recording both venues' books side by side, with stand ins for polymarket.com and Polymarket US.
"""

import json
from copytrader.api import polymarket, polymarket_us
from copytrader.db import database
from copytrader.db.models import LagBook, LagPair
from copytrader.record import lag

NOW = 1791892800.0      # 2026-10-13 12:00 UTC.


def us_event(slug, game_id, start, *markets):
    """
    A Polymarket US event as its gateway sends it, with markets of (suffix, type, outcomes, long outcome).
    """
    return {"slug": slug, "gameId": game_id, "category": "sports", "startTime": start,
            "markets": [{"id": i, "slug": f"aec-{slug}{suffix}", "marketType": kind, "outcomes": outcomes, "closed": False,
                         "marketSides": [{"description": long, "long": True}]} for i, (suffix, kind, outcomes, long) in enumerate(markets)]}


def com_event(slug, game_id, *markets):
    """
    A polymarket.com event as Gamma sends it, with markets of (slug, type, outcomes, token ids).
    """
    return {"id": 1, "slug": slug, "gameId": game_id, "title": slug,
            "markets": [{"id": i, "conditionId": f"0x{i}", "slug": m_slug, "question": "?", "sportsMarketType": kind, "outcomes": outcomes,
                         "clobTokenIds": tokens, "closed": False} for i, (m_slug, kind, outcomes, tokens) in enumerate(markets)]}


def test_pairs_take_the_polymarket_com_outcome_each_long_side_holds_the_games_under_way_first(monkeypatch):
    nhl = us_event("nhl-nj-det-2026-10-13", 7, "2026-10-13T22:00:00Z", ("", "moneyline", '["Red Wings","Devils"]', "Red Wings"),
                   ("-total-5pt5", "totals", '["Over","Under"]', "Over"), ("-pos-1pt5", "spreads", '["+1.50","-1.50"]', "+1.50"))
    soccer = us_event("acle-kyo-rat-2026-10-13", 9, "2026-10-13T10:00:00Z", ("-kyo", "drawable_outcome", '["No","Yes"]', "Yes"))
    monkeypatch.setattr(polymarket_us, "events", lambda start, end: [nhl, soccer])
    asked = []

    def events(game_id, closed):
        asked.append(game_id)
        return [com_event("nhl-nj-det-2026-10-13", 7, ("nhl-nj-det-2026-10-13", "moneyline", '["Devils", "Red Wings"]', '["d", "r"]'),
                          ("nhl-nj-det-2026-10-13-total-5pt5", "totals", '["Over", "Under"]', '["o", "u"]')),
                com_event("acle-kyo-rat-2026-10-13", 9, ("acle-kyo-rat-2026-10-13-kyo", "moneyline", '["Yes", "No"]', '["y", "n"]'))]

    monkeypatch.setattr(polymarket, "events", events)
    found = lag.find_pairs(NOW)
    assert asked == [[7, 9]]
    assert [(p.slug, p.token_id, p.outcome) for p in found] == [("aec-acle-kyo-rat-2026-10-13-kyo", "y", "Yes"),
                                                                 ("aec-nhl-nj-det-2026-10-13", "r", "Red Wings"),
                                                                 ("aec-nhl-nj-det-2026-10-13-total-5pt5", "o", "Over")]


def book(venue, key, time, bid=0.4, ask=0.5):
    """
    A LagBook with one level a side.
    """
    return LagBook(venue, key, time, None, json.dumps([[bid, 10]]), json.dumps([[ask, 10]]))


def test_a_book_is_stored_when_it_changes_or_a_heartbeat_after_the_last(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    last = {}
    rows = [book("polymarket", "t", 0), book("polymarket", "t", 2), book("polymarket", "t", 4, bid=0.41),
            book("polymarket", "t", 4 + lag.HEARTBEAT_SECONDS), book("polymarket_us", "s", 1)]
    assert lag.store_changed(conn, rows, last) == {"polymarket books": 3, "polymarket_us books": 1}
    assert [r.time for r in database.lag_books(conn, "polymarket", "t")] == [0, 4, 4 + lag.HEARTBEAT_SECONDS]


def test_a_short_run_reads_both_venues_and_stores_their_books(tmp_path, monkeypatch):
    pair = LagPair("aec-x", "tok", "0x1", "A", 7, "2020-01-01T00:00:00Z", "moneyline", 0.0695, NOW)
    monkeypatch.setattr(lag, "find_pairs", lambda now: [pair])
    monkeypatch.setattr(lag, "finish", lambda conn, now, log: 0)
    monkeypatch.setattr(lag, "COM_SECONDS", 0.05)
    monkeypatch.setattr(lag, "FLUSH_SECONDS", 0.05)
    reads = []

    def com_books(tokens):
        reads.append(tokens)
        return [{"asset_id": "tok", "bids": [{"price": "0.4", "size": "10"}], "asks": [{"price": str(0.5 + len(reads) / 1000), "size": "10"}]}]

    monkeypatch.setattr(polymarket, "books", com_books)
    us_book = {"bids": [{"px": {"value": "0.41"}, "qty": "5"}], "offers": [], "state": "MARKET_STATE_OPEN"}
    monkeypatch.setattr(polymarket_us, "book", lambda slug: us_book)
    conn = database.connect(tmp_path / "copytrader.sqlite")
    logs = []
    lag.record(conn, seconds=0.3, log=logs.append)
    assert len(database.lag_books(conn, "polymarket", "tok")) == len(reads) >= 2      # Its ask moved each read.
    assert len(database.lag_books(conn, "polymarket_us", "aec-x")) == 1                # It never did.
    assert "1 markets of 1 games to record, 1 of them under way" in logs[1] and logs[-1].startswith("since the start: 1 pairs")


def test_a_past_game_gets_its_price_history_once_and_is_finished_once_resolved(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    past = NOW - lag.AFTER_START - 3600
    database.store(conn, [LagPair("aec-x", "tok", "0x1", "A", 7, polymarket_us.iso(past), "moneyline", None, past - 3600)])
    asked = []
    monkeypatch.setattr(polymarket_us, "price_history",
                        lambda slug, start, end: asked.append((slug, start, end)) or [{"timestamp": start + 60, "longPrice": 0.4,
                                                                                       "shortPrice": 0.61}])
    status = {"resolution": "proposed"}

    def markets(condition_ids, closed, include_tag):
        return [{"id": 1, "conditionId": "0x1", "question": "?", "closed": True, "umaResolutionStatus": status["resolution"],
                 "outcomes": '["A", "B"]', "clobTokenIds": '["tok", "other"]', "outcomePrices": '["1", "0"]'}]

    monkeypatch.setattr(polymarket, "markets", markets)
    assert lag.finish(conn, NOW, lambda line: None) == 0
    status["resolution"] = "resolved"
    assert lag.finish(conn, NOW, lambda line: None) == 1
    assert lag.finish(conn, NOW, lambda line: None) == 0
    assert len(asked) == 1 and asked[0][1] == int(past - 3600) - 300 and [p.long_price for p in database.us_prices(conn, "aec-x")] == [0.4]
    assert database.markets(conn)["0x1"].resolution == "resolved" and asked[0][2] == int(NOW)      # Read up to now, less than a day.


def test_the_books_of_games_under_way_are_read_twice_a_round():
    pairs = [LagPair(slug, "t", "0x", "A", 7, start, "moneyline", None, NOW) for slug, start in (("later", "2026-10-13T20:00:00Z"),
                                                                                                 ("now", "2026-10-13T11:00:00Z"))]
    assert lag.reading_order(pairs, NOW) == ["now", "later", "now"]
