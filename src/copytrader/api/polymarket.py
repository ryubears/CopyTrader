"""
Polymarket API client, read only, for finding the traders worth copying and reading what they trade.

Polymarket spreads its data over three hosts. Gamma lists the events and markets, with their tags and, once they
resolve, their outcomes. The order book host keeps each outcome's book and price history. The data host keeps each
trader's trades, activity, and positions, and the leaderboard. This is the only file that knows Polymarket's field
names: the reads return what Polymarket sends, and the parse functions at the end turn its rows into the models the
database stores. The data host's times are in seconds since 1970.

A trader is their proxy wallet's address, not that of the key that signs their orders. A market, its conditionId, has
one token per outcome, and the order book host knows an outcome by its token id, which Gamma gives in clobTokenIds.
Gamma sends outcomes, outcomePrices, and clobTokenIds as JSON written into strings. Categories are tags, sports
SPORTS_TAG and politics POLITICS_TAG. Markets filter by tag_id and quietly ignore tag_slug; events take either.

Paging has limits that shape the calls, as found on 2026-10-08. The data host's /trades leaves out the trades a trader
made as maker unless asked with takerOnly=false, which trades() always does. It refuses offsets past
TRADES_MAX_OFFSET for trades and ACTIVITY_MAX_OFFSET for activity, so activity() reads a trader's whole history in
windows of time instead. Gamma's offsets stop at a few thousand markets, so markets() and events() follow its keyset
cursor.
"""

import itertools
import json
from collections import Counter
from copytrader.api.http import RateLimiter, get_json
from copytrader.db.models import Activity, Market, Standing

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
GAMMA_LIMITER = RateLimiter(25)     # Requests a second. Polymarket documents 300 per 10 seconds for Gamma's /markets, its lowest.
CLOB_LIMITER = RateLimiter(50)      # Half the 1000 per 10 seconds documented for price history, the book host's lowest that is used.
DATA_LIMITER = RateLimiter(10)      # Two thirds of the 150 per 10 seconds documented for /positions, the data host's lowest.
SPORTS_TAG = 1
POLITICS_TAG = 2
CATEGORIES = ("sports", "politics")   # The tags' slugs a market's category comes from, see parse_market().
KEYSET_PAGE = 100               # The most markets or events one keyset call answers with.
TRADES_PAGE = 500               # Trades per /trades call.
TRADES_MAX_OFFSET = 10_000      # The furthest offset /trades takes.
ACTIVITY_PAGE = 500             # The most rows one /activity call answers with.
ACTIVITY_MAX_OFFSET = 5_000     # The furthest offset /activity takes.
POSITIONS_PAGE = 500            # The most positions one /positions call answers with.
CLOSED_POSITIONS_PAGE = 50      # The most closed positions one /closed-positions call answers with.
LEADERBOARD_PAGE = 50           # The most traders one leaderboard call answers with.


# REQUESTS

def joined(values):
    """
    A filter of one value or a list of them, as the data host takes a list: comma separated.
    """
    return ",".join(values) if isinstance(values, list) else values


def keyset_paged(path, key, filters):
    """
    Every market or event, as key names them, of a Gamma keyset call matching the filters, following its cursor.
    """
    params = {"limit": KEYSET_PAGE, **filters}
    while True:
        data = get_json(f"{GAMMA}{path}", params, GAMMA_LIMITER)
        yield from data[key]
        if not data.get("next_cursor") or not data[key]:
            return
        params = {**params, "after_cursor": data["next_cursor"]}


def offset_paged(path, params, page, max_offset=None):
    """
    Every row of a data host call that pages by offset, page rows a call. Raises ValueError when there are more rows
    than max_offset reaches, rather than miss them.
    """
    offset = 0
    while True:
        rows = get_json(f"{DATA}{path}", {**params, "limit": page, "offset": offset}, DATA_LIMITER)
        yield from rows
        if len(rows) < page:
            return
        offset += page
        if max_offset is not None and offset > max_offset:
            raise ValueError(f"{path} has more rows than offset {max_offset} reaches")


# MARKETS

def markets(**filters):
    """
    Every market matching the filters, Gamma's query parameters, such as tag_id=SPORTS_TAG, closed=True, or
    condition_ids=[...]. Gamma leaves out closed markets unless closed=True, and gives each market's tags with
    include_tag=True.
    """
    return keyset_paged("/markets/keyset", "markets", filters)


def events(**filters):
    """
    Every event matching the filters, each with its markets and tags, such as tag_slug="politics" and closed=True.
    """
    return keyset_paged("/events/keyset", "events", filters)


def tag(slug):
    """
    The tag with a slug, such as "sports", whose id is "1".
    """
    return get_json(f"{GAMMA}/tags/slug/{slug}", limiter=GAMMA_LIMITER)


def sports():
    """
    Every sports league Polymarket lists, each with its tag ids, comma separated, in tags.
    """
    return get_json(f"{GAMMA}/sports", limiter=GAMMA_LIMITER)


# PRICES

def book(token_id):
    """
    An outcome's order book now. Both sides come worst first, so the best bid and ask are each side's last level.
    """
    return get_json(f"{CLOB}/book", {"token_id": token_id}, CLOB_LIMITER)


def midpoint(token_id):
    """
    The midpoint of an outcome's best bid and ask now.
    """
    return float(get_json(f"{CLOB}/midpoint", {"token_id": token_id}, CLOB_LIMITER)["mid"])


def price_history(token_id, start=None, end=None, interval=None, fidelity=None):
    """
    An outcome's price over time as [{"t": seconds since 1970, "p": price}], for an interval back from now, "1m", "1h",
    "6h", "1d", "1w", or "max", or from start to end, a point every fidelity minutes. A closed market's points come far
    apart, so its trades say better what a copy would have paid.
    """
    params = {"market": token_id, "startTs": start, "endTs": end, "interval": interval, "fidelity": fidelity}
    return get_json(f"{CLOB}/prices-history", params, CLOB_LIMITER)["history"]


# ACCOUNTS

def leaderboard(category="OVERALL", period="ALL", order="PNL", limit=None):
    """
    The traders ranked best first by "PNL" or "VOL" over a period, "DAY", "WEEK", "MONTH", or "ALL", in one category,
    "OVERALL", "SPORTS", "POLITICS", and the like, the first limit of them, or all. Each has its proxyWallet, userName,
    pnl, vol, and rank. These are standings now, so picking past traders by them would pick by what came after.
    """
    rows = offset_paged("/v1/leaderboard", {"category": category, "timePeriod": period, "orderBy": order}, LEADERBOARD_PAGE)
    return itertools.islice(rows, limit)


def trades(user=None, market=None, taker_only=False):
    """
    The trades of a trader, of a market's conditionId or a list of them, or both, newest first, those made as maker too
    unless taker_only. The call takes no times and no offset past TRADES_MAX_OFFSET, so it raises ValueError there
    rather than miss trades. activity() reads a trader's whole history.
    """
    params = {"user": user, "market": joined(market), "takerOnly": taker_only}
    return offset_paged("/trades", params, TRADES_PAGE, TRADES_MAX_OFFSET)


def activity(user, types=None, market=None, start=None, end=None):
    """
    A trader's activity from start to end, in seconds since 1970 and both included, oldest first: trades, splits,
    merges, redeems, rewards, yield, conversions, and rebates, or those of types, such as "TRADE" or ["TRADE", "REDEEM"]. The
    call stops at ACTIVITY_MAX_OFFSET, so each time a window of time gets there the next starts at the last time seen,
    and the rows at that time are held back for it to read again, so none is missed or read twice. Raises ValueError
    when one second holds more rows than a window reaches.
    """
    params = {"user": user, "type": joined(types), "market": joined(market), "end": end, "sortBy": "TIMESTAMP", "sortDirection": "ASC"}
    while True:
        rows, offset = [], 0
        while True:
            page = get_json(f"{DATA}/activity", {**params, "start": start, "limit": ACTIVITY_PAGE, "offset": offset}, DATA_LIMITER)
            rows.extend(page)
            offset += ACTIVITY_PAGE
            if len(page) < ACTIVITY_PAGE or offset > ACTIVITY_MAX_OFFSET:
                break
        if len(page) < ACTIVITY_PAGE:
            yield from rows
            return
        last = rows[-1]["timestamp"]
        if last == start:
            raise ValueError(f"over {len(rows)} activity rows of {user} at {start}, more than a window reaches")
        yield from (row for row in rows if row["timestamp"] < last)
        start = last


def positions(user, market=None):
    """
    A trader's open positions, each with its size, average price, and PnL.
    """
    return list(offset_paged("/positions", {"user": user, "market": joined(market)}, POSITIONS_PAGE))


def closed_positions(user, market=None):
    """
    A trader's closed positions, each with its realized PnL.
    """
    return offset_paged("/closed-positions", {"user": user, "market": joined(market)}, CLOSED_POSITIONS_PAGE)


def holders(market, limit=20):
    """
    The biggest holders of each outcome of a market, its conditionId, as [{"token": token id, "holders": [...]}].
    """
    return get_json(f"{DATA}/holders", {"market": market, "limit": limit}, DATA_LIMITER)


def value(user):
    """
    What a trader's open positions are worth now, in dollars.
    """
    rows = get_json(f"{DATA}/value", {"user": user}, DATA_LIMITER)
    return float(rows[0]["value"]) if rows else 0.0


# ROWS

def parse_standing(row, board, day, recorded_at):
    """
    A Standing from one row of a leaderboard board, named as "SPORTS/MONTH/PNL", recorded on a UTC day at a time in
    seconds since 1970.
    """
    return Standing("polymarket", day, board, row["proxyWallet"].lower(), int(row["rank"]), float(row["pnl"]), None, float(row["vol"]), None,
                    recorded_at)


def parse_market(m, updated_at):
    """
    A Market from one market of markets(), read with include_tag=True so its tags come with it. Its category is the
    first of CATEGORIES among its tags.
    """
    tags = [t["slug"] for t in m.get("tags") or []]
    return Market(
        condition_id=m["conditionId"],
        market_id=str(m["id"]),
        slug=m.get("slug"),
        question=m.get("question") or "",
        event_slug=(m.get("events") or [{}])[0].get("slug"),
        category=next((c for c in CATEGORIES if c in tags), None),
        tags=json.dumps(tags) if "tags" in m else None,
        market_type=m.get("sportsMarketType"),
        neg_risk=m.get("negRisk"),
        game_start=m.get("gameStartTime"),
        end_date=m.get("endDate"),
        closed=bool(m.get("closed")),
        closed_time=m.get("closedTime"),
        outcomes=m.get("outcomes") or "[]",         # Already JSON, as Gamma sends these three.
        token_ids=m.get("clobTokenIds") or "[]",
        outcome_prices=m.get("outcomePrices"),
        resolution=m.get("umaResolutionStatus"),
        updated_at=updated_at,
    )


def parse_activity(rows):
    """
    The Activity of rows from activity(), in its order. Two rows of one transaction can be the same in every field, as
    when one order fills against two makers alike, so each row's seq counts the rows like it in its transaction before
    it. That keeps them apart in the table, and a second read of the same rows stores nothing new. activity() keeps
    the rows of one second together, so the count starts over at each new second.
    """
    seen, second = Counter(), None
    for r in rows:
        if r["timestamp"] != second:
            seen.clear()
            second = r["timestamp"]
        activity = Activity(
            address=r["proxyWallet"].lower(),
            timestamp=r["timestamp"],
            type=r["type"],
            condition_id=r.get("conditionId") or "",
            asset=r.get("asset") or "",
            outcome=r.get("outcome") or None,
            outcome_index=r.get("outcomeIndex"),
            side=r.get("side") or "",
            size=float(r.get("size") or 0),
            usdc_size=float(r.get("usdcSize") or 0),
            price=float(r.get("price") or 0),
            tx_hash=r["transactionHash"],
            slug=r.get("slug") or None,
            event_slug=r.get("eventSlug") or None,
            seq=0,
        )
        key = (activity.tx_hash, activity.type, activity.asset, activity.side, activity.size, activity.usdc_size, activity.price)
        activity.seq = seen[key]
        seen[key] += 1
        yield activity
