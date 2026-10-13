"""
Tests for matching polymarket.com's markets with Polymarket US's, on made up markets and in a database.
"""

from copytrader.analysis import match
from copytrader.db import database
from copytrader.db.models import Activity, Event, Market, UsMarket

GAME = Event("nfl-car-gb-2025-11-02", "65487", 19072, "Panthers vs. Packers", "2025-11-02T18:00:00Z", "nfl-2025", 0.0)


def market(slug, outcomes='["Panthers", "Packers"]', market_type="moneyline", event_slug=GAME.event_slug, question="?", condition_id="0xm"):
    """
    A polymarket.com Market.
    """
    return Market(condition_id, "1", slug, question, event_slug, "sports", None, market_type, False, None, None, None, True, None,
                  outcomes, '["1", "2"]', '["1", "0"]', "resolved", None, 0.0)


def us(slug, outcomes='["Panthers","Packers"]', market_type="moneyline", event_slug=GAME.event_slug, long_outcome=None, title=None,
       game_id=GAME.game_id):
    """
    A Polymarket US UsMarket of a game.
    """
    return UsMarket(slug, "1", event_slug, game_id, "sports", market_type, None, "?", title, outcomes, long_outcome, None, True, None, None,
                    None, 0.0695, 0.0)


def test_a_market_matches_its_own_slug_behind_a_prefix_and_buys_the_side_holding_each_outcome():
    by_game = {GAME.game_id: [us("tsc-nfl-car-gb-2025-11-02-total-44pt5", '["Over","Under"]', "totals"),
                              us("aec-nfl-car-gb-2025-11-02", '["Packers","Panthers"]')]}
    found = match.match(market("nfl-car-gb-2025-11-02"), GAME, by_game)
    assert (found.slug, found.sides, found.how) == ("aec-nfl-car-gb-2025-11-02", ("short", "long"), "slug")
    total = match.match(market("nfl-car-gb-2025-11-02-total-44pt5", '["Over", "Under"]', "totals"), GAME, by_game)
    assert (total.slug, total.sides) == ("tsc-nfl-car-gb-2025-11-02-total-44pt5", ("long", "short"))


def test_a_market_matches_what_follows_its_events_slug_when_the_venues_write_the_event_differently():
    tennis = Event("atp-sinner-djokovi-2026-07-08", "1", 7, "Sinner vs. Djokovic", None, "atp", 0.0)
    theirs = "atp-jansin-novdjo-2026-07-08"
    by_game = {7: [us(f"aec-{theirs}", '["Sinner","Djokovic"]', event_slug=theirs, game_id=7),
                   us(f"tsc-{theirs}-22pt5", '["Under","Over"]', "totals", theirs, "Over", game_id=7)]}
    moneyline = market(tennis.event_slug, '["Sinner", "Djokovic"]', event_slug=tennis.event_slug)
    assert match.match(moneyline, tennis, by_game).how == "slug"
    more = f"{tennis.event_slug}-more-markets"          # polymarket.com's event for the game's other markets.
    found = match.match(market(f"{tennis.event_slug}-total-22pt5", '["Over", "Under"]', "totals", more), tennis, by_game)
    assert (found.slug, found.sides) == (f"tsc-{theirs}-22pt5", ("long", "short"))
    assert match.match(market(f"{tennis.event_slug}-total-23pt5", '["Over", "Under"]', "totals", more), tennis, by_game) is None


def test_a_moneyline_matches_by_its_two_outcomes_when_its_slug_does_not_but_nothing_else_does():
    by_game = {GAME.game_id: [us("aec-nfl-car-gnb-2025-11-02", event_slug="nfl-car-gnb-2025-11-03"),
                              us("tsc-nfl-car-gnb-2025-11-02-total-44pt5", '["Over","Under"]', "totals", "nfl-car-gnb-2025-11-03")]}
    assert match.match(market("nfl-car-gb-2025-11-02"), GAME, by_game).how == "outcomes"
    assert match.match(market("nfl-car-gb-2025-11-02-total-44pt5", '["Over", "Under"]', "totals"), GAME, by_game) is None


def test_a_soccer_teams_win_or_the_draw_matches_by_the_team_and_the_long_side_holds_yes_even_when_listed_second():
    game = Event("sea-sas-com-2026-04-17", "1", 9, "Sassuolo vs. Como", None, "sea", 0.0)
    theirs = "sea-sassuolo-como-2026-04-17"
    by_game = {9: [us(f"atc-{theirs}-{team}", '["No","Yes"]', "drawable_outcome", theirs, "Yes", title, 9)
                   for team, title in (("sassuolo", "US Sassuolo Calcio"), ("draw", "Tie"), ("como", "Como 1907"))]}
    como = market(f"{game.event_slug}-com", '["Yes", "No"]', event_slug=game.event_slug, question="Will Como 1907 win on 2026-04-17?")
    found = match.match(como, game, by_game)
    assert (found.slug, found.sides, found.how) == (f"atc-{theirs}-como", ("long", "short"), "team")
    draw = market(f"{game.event_slug}-draw", '["Yes", "No"]', event_slug=game.event_slug,
                  question="Will US Sassuolo Calcio vs. Como 1907 end in a draw?")
    assert match.match(draw, game, by_game).slug == f"atc-{theirs}-draw"
    other = market("will-carolina-win", '["Yes", "No"]', question="Will Carolina win the Super Bowl?")
    assert match.match(other, GAME, {GAME.game_id: [us("atc-x", '["Yes","No"]', "drawable_outcome", title="Carolina Panthers")]}) is None


def test_no_game_no_match():
    assert match.match(market("nfl-car-gb-2025-11-02"), None, {}) is None
    assert match.match(market("nfl-car-gb-2025-11-02"), Event("fed", "1", None, "Fed?", None, "fomc", 0.0), {}) is None
    assert match.match(market("nfl-car-gb-2025-11-02"), GAME, {19073: [us("aec-nfl-car-gb-2025-11-02", game_id=19073)]}) is None


def test_the_prices_wanted_are_the_minutes_the_traders_traded_in_matched_markets(tmp_path):
    conn = database.connect(tmp_path / "copytrader.sqlite")
    database.store(conn, [market("nfl-car-gb-2025-11-02"), market("other", event_slug="other", condition_id="0xo")])
    database.store(conn, [GAME])
    database.store(conn, [us("aec-nfl-car-gb-2025-11-02")])

    def trade(address, timestamp, condition_id="0xm"):
        return Activity(address, timestamp, "TRADE", condition_id, "1", "Panthers", 0, "BUY", 10.0, 5.0, 0.5, f"0x{timestamp}", None, None, 0)

    database.store(conn, [trade("0xa", 125), trade("0xa", 170), trade("0xa", 300, "0xo"), trade("0xb", 600)])
    assert match.wanted_prices(conn, match.matches(conn), ["0xa"]) == {"aec-nfl-car-gb-2025-11-02": [120]}
