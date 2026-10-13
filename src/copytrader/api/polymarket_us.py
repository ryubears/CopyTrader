"""
Polymarket US API client, read only, for pricing copies of polymarket.com's traders where a copy would trade.

Polymarket US is Polymarket's exchange regulated by the CFTC, for traders in the US, with order books of its own,
apart from polymarket.com's. Its public gateway lists the events and markets, the order book of each market, and the
history of each market's best prices. This is the only file that knows its field names: the reads return what the
gateway sends, and the parse functions at the end turn it into the models the database stores.

A market is one instrument with two sides. Its long side holds the outcome its side names, usually the first of its
outcomes but not always, and its short side the other, so buying the short side is buying the other outcome. Its price
history gives, about once a minute, the long side's display price, normally the best ask, and the short side's, normally
one less the best bid, so what buying either side cost at the time. A sports event that is a game carries the same
gameId as polymarket.com's event for that game, or, in the smaller leagues, only Sportradar's id, 'sr:sport_event:73835474',
whose number polymarket.com uses as its gameId then, see game_id().

The events listing pages by offset and filters by the events' start times, so a long stretch is read a day at a time.
Price history over a stretch of time, given by its start and end, is meant for stretches of up to PRICES_SPAN, as found
on 2026-10-13.
"""

import json
import re
from copytrader.api.http import RateLimiter, get_json
from copytrader.common.timeutil import DAY, epoch
from copytrader.db.models import LagBook, UsBookLevel, UsMarket, UsPrice
from datetime import UTC, datetime

GATEWAY = "https://gateway.polymarket.us/v1"
LIMITER = RateLimiter(10)       # Requests a second, half the 20 a second an IP documented for price history.
EVENTS_PAGE = 20                # Events per call. Each comes with its markets, hundreds for a game, 15 MB a page at most.
PRICES_SPAN = DAY               # The longest stretch of price history one call is meant for.
TAKER_FEE = 0.0695              # The taker fee's coefficient where a market gives none: fee = coefficient * contracts * p * (1 - p).


# REQUESTS

def iso(seconds):
    """
    The ISO 8601 UTC time the gateway takes for seconds since 1970.
    """
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def events(start, end, **filters):
    """
    Every event starting at or after start and before end, in seconds since 1970, each with its markets, matching the
    filters, such as closed=True or tag_slug="nfl".
    """
    offset = 0
    while True:
        params = {"start_date_min": iso(start), "start_date_max": iso(end), "limit": EVENTS_PAGE, "offset": offset, **filters}
        page = get_json(f"{GATEWAY}/events", params, LIMITER)["events"]
        yield from page
        if len(page) < EVENTS_PAGE:
            return
        offset += EVENTS_PAGE


def book(slug):
    """
    A market's order book now: bids and offers, each a list of {"px": {"value": ...}, "qty": ...}, best first, its
    state, and its stats.
    """
    return get_json(f"{GATEWAY}/markets/{slug}/book", limiter=LIMITER)["marketData"]


def price_history(slug, start, end):
    """
    A market's display prices from start to end, in seconds since 1970, at most PRICES_SPAN apart, oldest first: a list of
    {"timestamp": ..., "longPrice": ..., "shortPrice": ...} about once a minute, empty for a market with none stored.
    """
    params = {"symbol": slug, "timestamp.startTimestamp": int(start), "timestamp.endTimestamp": int(end), "fidelity": 1}
    return get_json(f"{GATEWAY}/price-history", params, LIMITER).get("history") or []


# ROWS

def long_outcome(m):
    """
    The outcome a market's long side holds, as its side names it, or None when its sides do not say.
    """
    for side in m.get("marketSides") or []:
        if side.get("long"):
            return side.get("description")
    return None


def game_id(event):
    """
    The game an event is, by the id polymarket.com's event for it carries: its gameId, or the number of its Sportradar
    id when that is all it has. None if it is not a game.
    """
    if event.get("gameId"):
        return event["gameId"]
    found = re.fullmatch(r"sr:sport_event:(\d+)", event.get("sportradarGameId") or "")
    return int(found.group(1)) if found else None


def parse_market(event, m, updated_at):
    """
    A UsMarket from one market of an event of events().
    """
    return UsMarket(
        slug=m["slug"],
        market_id=str(m["id"]),
        event_slug=event["slug"],
        game_id=game_id(event),
        category=m.get("category") or event.get("category"),
        market_type=m.get("marketType"),
        sports_market_type=m.get("sportsMarketType"),
        question=m.get("question") or "",
        title=m.get("title"),
        outcomes=m.get("outcomes") or "[]",         # Already JSON, as the gateway sends it.
        long_outcome=long_outcome(m),
        outcome_prices=m.get("outcomePrices"),
        closed=bool(m.get("closed")),
        status=m.get("status"),
        game_start=m.get("gameStartTime") or event.get("startTime"),
        end_date=m.get("endDate"),
        fee_coefficient=m.get("feeCoefficient"),
        updated_at=updated_at,
    )


def parse_prices(slug, history):
    """
    The UsPrice of each point of a market's price_history().
    """
    return [UsPrice(slug, int(p["timestamp"]), p.get("longPrice"), p.get("shortPrice")) for p in history]


def parse_book(slug, book, time, levels):
    """
    The UsBookLevel of each of the best levels of a book() on either side, read at time, in seconds since 1970.
    """
    return [UsBookLevel(slug, int(time), side, i, float(entry["px"]["value"]), float(entry["qty"]))
            for side, key in (("bid", "bids"), ("offer", "offers")) for i, entry in enumerate((book.get(key) or [])[:levels])]


def parse_lag_book(slug, book, time, levels):
    """
    A LagBook of the best levels of a market's book(), its long side's, read at time, in seconds since 1970, best first.
    The venue's time is when it last changed the book, given to the nanosecond, cut to the microsecond.
    """
    def best(side):
        return [[float(entry["px"]["value"]), float(entry["qty"])] for entry in (book.get(side) or [])[:levels]]

    head, _, fraction = (book.get("transactTime") or "").rstrip("Z").partition(".")
    return LagBook("polymarket_us", slug, time, epoch(f"{head}.{fraction[:6] or 0}Z") if head else None,
                   json.dumps(best("bids"), separators=(",", ":")), json.dumps(best("offers"), separators=(",", ":")))
