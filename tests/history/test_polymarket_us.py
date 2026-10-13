"""
Tests for reading polymarket.com's games, Polymarket US's markets, and its prices around the copied trades, with stand
ins for both venues.
"""

from copytrader.api import polymarket, polymarket_us
from copytrader.db import database
from copytrader.db.models import Market
from copytrader.history import polymarket_us as history

NOW = 1791849600.0      # 2026-10-13 00:00 UTC.
DAY = history.DAY


def us_event(slug, game_id, *types):
    """
    A Polymarket US event as its gateway sends it, with a market of each of types.
    """
    markets = [{"id": i, "slug": f"{slug}-{t}", "marketType": t, "outcomes": '["A","B"]'} for i, t in enumerate(types)]
    return {"slug": slug, "gameId": game_id, "category": "sports", "markets": markets}


def test_every_market_but_props_is_kept():
    assert [m["marketType"] for m in history.kept(us_event("g", 7, "moneyline", "props", "totals"))] == ["moneyline", "totals"]
    assert [m["marketType"] for m in history.kept(us_event("f", None, "futures"))] == ["futures"]


def test_stretches_merge_around_nearby_trades_and_stay_within_a_span():
    before, after = history.BEFORE, history.AFTER
    assert history.stretches([1000, 1000 + after, 50_000]) == [(1000 - before, 1000 + 2 * after), (50_000 - before, 50_000 + after)]
    assert history.stretches([0, 30, 100], span=before + after + 60) == [(-before, after + 30), (100 - before, 100 + after)]


def test_events_are_read_for_the_game_of_each_stored_sports_or_politics_market(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    stored = [Market(c, c, c, "?", event, category, None, None, False, None, None, None, True, None, "[]", "[]", None, None, None, 0.0)
              for c, event, category in (("0x1", "nfl-a-b", "sports"), ("0x2", "nfl-a-b", "sports"), ("0x3", "btc", None))]
    database.store(conn, stored)
    asked = []

    def events(slug):
        asked.append(slug)
        return [{"id": 1, "slug": s, "title": s, "gameId": 7} for s in slug]

    monkeypatch.setattr(polymarket, "events", events)
    assert history.read_events(conn, NOW, log=lambda line: None) == 1
    assert asked == [["nfl-a-b"]] and database.game_ids(conn) == {7}
    assert history.read_events(conn, NOW, log=lambda line: None) == 0 and len(asked) == 1


def test_us_markets_are_read_a_day_at_a_time_noting_how_far_the_read_got(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    windows = []

    def events(start, end):
        windows.append((start, end))
        return [us_event(f"g{len(windows)}", None, "futures")]

    monkeypatch.setattr(polymarket_us, "events", events)
    assert history.read_markets(conn, NOW - 2 * DAY, NOW - DAY / 2, NOW, log=lambda line: None) == 2
    assert windows == [(NOW - 2 * DAY, NOW - DAY), (NOW - DAY, NOW - DAY / 2)]
    assert sorted(m.slug for m in database.us_markets(conn)) == ["g1-futures", "g2-futures"]
    assert database.synced_at(conn, "polymarket_us", "markets", "") == NOW - DAY / 2


def test_prices_are_read_once_a_stretch_even_a_stretch_that_held_none(tmp_path, monkeypatch):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    asked = []

    def price_history(slug, start, end):
        asked.append(slug)
        return [{"timestamp": start + 60, "longPrice": 0.4, "shortPrice": 0.65}] if slug == "a" else []

    monkeypatch.setattr(polymarket_us, "price_history", price_history)
    assert history.read_prices(conn, {"a": [NOW], "b": [NOW]}, NOW, log=lambda line: None) == 1
    assert history.read_prices(conn, {"a": [NOW], "b": [NOW]}, NOW, log=lambda line: None) == 0
    assert asked == ["a", "b"] and [p.long_price for p in database.us_prices(conn, "a")] == [0.4]
