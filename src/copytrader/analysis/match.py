"""
Match polymarket.com's markets with Polymarket US's, so a trade on one can be copied on the other.

Only games match so far, by the game id both venues' events share, see api/polymarket_us.py. Within a game, the two
venues write the event's slug alike or not, 'atp-sinner-djokovi-2026-07-08' and 'atp-jansin-novdjo-2026-07-08', but
what follows it in a market's slug alike: '-total-1pt5' and '-1pt5' for a total of 1.5, nothing for the moneyline,
'-com' for one team's win in soccer. polymarket.com puts some of a game's markets in an event of its own, with
'-more-markets' after the game's slug, and Polymarket US starts each market's slug with a prefix, 'aec-' or 'tsc-'. So a
market matches the market of the same game whose slug ends the same way, see suffix(), or, failing that, a moneyline
between two sides matches the moneyline with the same two outcomes, and a soccer team's win, or a draw, with Yes and No
for outcomes, matches Polymarket US's market for that team, or the tie. Either way the outcomes have to be the same two
names, and a polymarket.com outcome is then copied by buying the side of Polymarket US's market that holds it, see
sides().

Spreads do not match, as Polymarket US names their sides by their lines, '+33.50', rather than by team, and neither do
futures and politics, whose events are not games.
"""

import json
from collections import defaultdict
from copytrader.db import database
from dataclasses import dataclass

YES_NO = {"yes", "no"}


@dataclass
class Match:
    """
    The Polymarket US market a polymarket.com market matches, and the side of it that holds each of its outcomes.
    """
    condition_id: str
    slug: str                   # Polymarket US's market.
    sides: tuple                # 'long' or 'short' for each polymarket.com outcome, in its order.
    how: str                    # 'slug', 'outcomes', or 'team', see the top of this file.


def names(outcomes):
    """
    A market's outcomes, from their JSON list, lower cased and stripped, so the two venues' spellings compare.
    """
    return [str(o).strip().lower() for o in json.loads(outcomes or "[]")]


def suffix(slug, event_slug):
    """
    What follows the event's slug in a market's slug, with no 'total-', after Polymarket US's prefix and before
    polymarket.com's '-more-markets', or None when the slug does not hold the event's.
    """
    event_slug = event_slug.removesuffix("-more-markets")
    at = slug.find(event_slug)
    return slug[at + len(event_slug):].replace("-total-", "-") if at >= 0 else None


def sides(market, us):
    """
    The side of Polymarket US's market us that holds each outcome of polymarket.com's market, or None when their
    outcomes are not the same two. The long side holds the outcome its side names, which is not always the first listed,
    as a soccer team's market lists '["No","Yes"]' with its long side holding Yes, or else the first.
    """
    ours, theirs = names(market.outcomes), names(us.outcomes)
    if len(ours) != 2 or sorted(ours) != sorted(theirs):
        return None
    long = us.long_outcome.strip().lower() if us.long_outcome and us.long_outcome.strip().lower() in theirs else theirs[0]
    return tuple("long" if o == long else "short" for o in ours)


def soccer_side(market, us):
    """
    Whether a soccer market of polymarket.com's, 'Will Como 1907 win on 2026-04-17?' or '... end in a draw?', is on the
    same result as Polymarket US's market us, titled for the team, 'Como 1907', or 'Tie' for the draw.
    """
    if us.market_type != "drawable_outcome":
        return False
    if market.question.endswith("end in a draw?"):
        return us.slug.endswith("-draw")
    return bool(us.title) and market.question.startswith(f"Will {us.title} win")


def match(market, event, by_game):
    """
    The Match of a polymarket.com Market of the Event event, among Polymarket US's markets by_game, {game_id: [UsMarket]},
    or None.
    """
    if event is None or event.game_id is None:
        return None
    candidates = by_game.get(event.game_id, [])
    ours = suffix(market.slug or "", event.event_slug)
    for us in candidates:
        if ours is not None and suffix(us.slug, us.event_slug) == ours and (found := sides(market, us)):
            return Match(market.condition_id, us.slug, found, "slug")
    if market.market_type == "moneyline":
        yes_no = set(names(market.outcomes)) == YES_NO
        for us in candidates:
            fits = soccer_side(market, us) if yes_no else us.market_type == "moneyline"
            if fits and (found := sides(market, us)):
                return Match(market.condition_id, us.slug, found, "team" if yes_no else "outcomes")
    return None


def matches(conn):
    """
    The Match of each stored polymarket.com market that has one, as {condition_id: Match}.
    """
    events = database.events(conn)
    by_game = defaultdict(list)
    for us in database.us_markets(conn):
        if us.game_id is not None:
            by_game[us.game_id].append(us)
    found = (match(m, events.get(m.event_slug), by_game) for m in database.markets(conn).values())
    return {f.condition_id: f for f in found if f}


def wanted_prices(conn, found, addresses):
    """
    The times of the trades of the traders of addresses in each market matched in found, {condition_id: Match}, as
    {slug: [times]}, for history/polymarket_us.py to read Polymarket US's prices around.
    """
    wanted = defaultdict(list)
    for condition_id, timestamp in database.trade_times(conn, list(found), addresses):
        wanted[found[condition_id].slug].append(timestamp)
    return dict(wanted)
