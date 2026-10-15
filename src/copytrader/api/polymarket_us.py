"""
Polymarket US API client, read only, for pricing copies of polymarket.com's traders where a copy would trade.

Polymarket US is Polymarket's exchange regulated by the CFTC, for traders in the US, with order books of its own,
apart from polymarket.com's. Its public gateway lists the events and markets, the order book of each market, and the
history of each market's best prices. The gateway's books come through Cloudflare's cache, so each may be up to 30
seconds old, as found on 2026-10-13. Live books come from the market feed, a websocket signed with an account's key,
see book_stream(), which sends a market's whole book each time it changes. This is the only file that knows Polymarket
US's field names: the reads return what it sends, and the parse functions at the end turn that into the models the
database stores.

A market is one instrument with two sides. Its long side holds the outcome its side names, usually the first of its
outcomes but not always, and its short side the other, so buying the short side is buying the other outcome. Its price
history gives, about once a minute, the long side's display price, normally the best ask, and the short side's, normally
one less the best bid, so what buying either side cost. Each point is the book at the end of a minute, stamped with the
minute's start, PRICE_OFFSET before, as found on 2026-10-15 against the books of the market feed: in play, 95% of points
matched the book a minute after their stamp, 33% the book at it. A sports event that is a game carries the same
gameId as polymarket.com's event for that game, or, in the smaller leagues, only Sportradar's id, 'sr:sport_event:73835474',
whose number polymarket.com uses as its gameId then, see game_id().

The events listing pages by offset and filters by the events' start times, so a long stretch is read a day at a time.
Price history over a stretch of time, given by its start and end, is meant for stretches of up to PRICES_SPAN, as found
on 2026-10-13.
"""

import base64
import functools
import itertools
import json
import re
import time
from copytrader.api.http import RateLimiter, get_json
from copytrader.common.paths import DATA_DIR
from copytrader.common.timeutil import DAY, epoch
from copytrader.db.models import LagBook, UsBookLevel, UsMarket, UsPrice
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from datetime import UTC, datetime
from websockets.sync.client import connect

GATEWAY = "https://gateway.polymarket.us/v1"
LIMITER = RateLimiter(10)       # Requests a second, half the 20 a second an IP documented for price history.
EVENTS_PAGE = 20                # Events per call. Each comes with its markets, hundreds for a game, 15 MB a page at most.
PRICES_SPAN = DAY               # The longest stretch of price history one call is meant for.
PRICE_OFFSET = 60               # Seconds after a price history point's stamp that its prices were the book's, the end of its minute.
TAKER_FEE = 0.0695              # The taker fee's coefficient where a market gives none: fee = coefficient * contracts * p * (1 - p).
FEED = "wss://api.polymarket.us/v1/ws/markets"
FEED_PATH = "/v1/ws/markets"    # The part of FEED a signature covers.
FEED_CHUNK = 100                # Markets a subscription request, the most the feed takes. It takes 10 requests a connection.
KEY_ID_FILE = DATA_DIR / "polymarket_us_key_id.txt"            # The account's key, the same as PredictionArbitrageTrader's.
SECRET_KEY_FILE = DATA_DIR / "polymarket_us_secret_key.txt"


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
    A market's order book as Cloudflare's cache last took it, up to 30 seconds ago: bids and offers, each a list of
    {"px": {"value": ...}, "qty": ...}, best first, its state, its stats, and transactTime, when it last changed then.
    """
    return get_json(f"{GATEWAY}/markets/{slug}/book", limiter=LIMITER)["marketData"]


@functools.cache
def credentials():
    """
    The account's key id and Ed25519 private key, from KEY_ID_FILE and SECRET_KEY_FILE, read once.
    """
    secret = base64.b64decode(SECRET_KEY_FILE.read_text().strip())
    return KEY_ID_FILE.read_text().strip(), Ed25519PrivateKey.from_private_bytes(secret[:32])


def signed_headers(method, path):
    """
    The headers that sign a request to Polymarket US's API: the account's key over the time in milliseconds, the method
    and the path.
    """
    key_id, key = credentials()
    stamp = str(int(time.time() * 1000))
    signature = base64.b64encode(key.sign(f"{stamp}{method}{path}".encode())).decode()
    return {"X-PM-Access-Key": key_id, "X-PM-Timestamp": stamp, "X-PM-Signature": signature}


def book_stream(slugs):
    """
    A connection to the market feed, subscribed to the markets' books, FEED_CHUNK a request. For each market it sends
    the whole book at once, then again each time it changes, every change, see streamed_book(). Its recv(timeout) waits
    for the next message, raising TimeoutError when none comes in time. No limit on the messages waiting, so a slow
    reader delays them rather than stalling the feed.
    """
    ws = connect(FEED, additional_headers=signed_headers("GET", FEED_PATH), open_timeout=10, max_size=None, max_queue=None)
    for i, chunk in enumerate(itertools.batched(sorted(slugs), FEED_CHUNK)):
        ws.send(json.dumps({"subscribe": {"requestId": f"books-{i}", "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA",
                                          "marketSlugs": list(chunk), "responsesDebounced": False}}))
    return ws


def streamed_book(message):
    """
    The book a message of book_stream() carries, as book() gives it, with its marketSlug, or None for another message.
    An error the feed sent, such as a refused subscription, raises RuntimeError.
    """
    m = json.loads(message)
    if m.get("error"):
        raise RuntimeError(f"Polymarket US's feed sent an error for {m.get('requestId')}: {m['error']}")
    return m.get("marketData")


def price_history(slug, start, end):
    """
    A market's display prices from start to end, in seconds since 1970, at most PRICES_SPAN apart, oldest first: a list of
    {"timestamp": ..., "longPrice": ..., "shortPrice": ...} about once a minute, each the prices PRICE_OFFSET after its
    timestamp, empty for a market with none stored.
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
    A LagBook of the best levels of a market's book(), or streamed_book(), its long side's, read at time, in seconds
    since 1970, best first. The venue's time is when it last changed the book, given to the nanosecond, cut to the
    microsecond.
    """
    def best(side):
        found = [[float(entry["px"]["value"]), float(entry["qty"])] for entry in book.get(side) or []]
        return sorted((level for level in found if level[1] > 0), key=lambda level: level[0], reverse=side == "bids")[:levels]

    head, _, fraction = (book.get("transactTime") or "").rstrip("Z").partition(".")
    return LagBook("polymarket_us", slug, time, epoch(f"{head}.{fraction[:6] or 0}Z") if head else None,
                   json.dumps(best("bids"), separators=(",", ":")), json.dumps(best("offers"), separators=(",", ":")))
