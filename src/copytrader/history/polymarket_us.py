"""
Read what pricing copies on Polymarket US takes: which game each polymarket.com event the stored markets belong to is,
Polymarket US's markets that those might match, and what Polymarket US's markets cost around the times a copy would have
traded.

First the events of the stored sports and politics markets are read from Gamma, for the game each is, see
api/polymarket.py. Then Polymarket US's events starting over the days the activity covers are read, a day at a time,
and their markets kept but for their props, which no market of polymarket.com's matches. How far that read got is noted,
and a later run reads on from REREAD_DAYS before it, for what was listed or resolved since. Last, for each trade of a
trader analysis/copy.py copies, in a market that matches one of Polymarket US's, see analysis/match.py, its price history
is read from BEFORE before the trade to AFTER after, the stretches of one market merged, so a busy market is read once a
day rather than once a trade. Each stretch read is noted, so running again reads only what is new.

Polymarket US keeps no history of its books' depth, so --books instead snapshots the books of its open markets of
BOOK_TYPES, the kinds a copy trades, whose games start within BOOK_HOURS, BOOK_LEVELS levels a side, for how much an
order of a size pays above the best price, see analysis/copy.py. Run before games at different times, it gathers more.

Run from src/ with:
    python3 -m copytrader.history.polymarket_us
    python3 -m copytrader.history.polymarket_us --days 30
    python3 -m copytrader.history.polymarket_us --books
"""

import argparse
import time
from copytrader.analysis import copy, match
from copytrader.api import polymarket, polymarket_us
from copytrader.common.log import log
from copytrader.common.timeutil import DAY, epoch
from copytrader.db import database
from copytrader.db.models import UsPriceRead

HISTORY_DAYS = 180          # How far back Polymarket US's events are read, as far as the followed traders' activity goes.
REREAD_DAYS = 2             # How far before the last read's end a later read of Polymarket US's events starts.
EVENTS_BATCH = 50           # polymarket.com events a call.
SKIPPED_TYPES = {"props"}   # Polymarket US's market types no market of polymarket.com's matches.
BEFORE = 300                # Seconds of prices read before a trade, so the price a copy meets has one before it.
AFTER = 3600 + 600          # Seconds after: the longest wait a copy is simulated with, and some.
POOLS = ("volume", "followed")     # The pools of analysis/copy.py whose copied traders' trades are priced.
COPIED = 50                 # The traders copied a period whose trades are priced, twice what analysis/copy.py copies, for room.
BOOK_TYPES = ("moneyline", "totals", "drawable_outcome")
BOOK_HOURS = (-3, 48)       # From when to when, in hours from now, the games whose books are snapshot start.
BOOK_LEVELS = 20
LOG_EVERY = 500


def read_events(conn, now, log=print):
    """
    Store the polymarket.com events of the stored sports and politics markets not stored yet. Returns how many.
    """
    wanted = database.events_wanted(conn)
    log(f"polymarket.com events: {len(wanted):,} to read")
    stored = 0
    for i in range(0, len(wanted), EVENTS_BATCH):
        found = polymarket.events(slug=wanted[i:i + EVENTS_BATCH])
        stored += database.store(conn, [polymarket.parse_event(e, now) for e in found])
    log(f"polymarket.com events: {stored:,} stored, {len(database.game_ids(conn)):,} games among all stored")
    return stored


def kept(event):
    """
    The markets of a Polymarket US event that a polymarket.com market might match: all but SKIPPED_TYPES.
    """
    return [m for m in event.get("markets") or [] if m.get("marketType") not in SKIPPED_TYPES]


def read_markets(conn, start, end, now, log=print):
    """
    Store the markets kept, see kept(), of Polymarket US's events starting from start to end, in seconds since 1970, a day
    at a time, noting how far it got. Returns how many.
    """
    stored, day = 0, start
    while day < end:
        markets = [polymarket_us.parse_market(e, m, now) for e in polymarket_us.events(day, min(day + DAY, end)) for m in kept(e)]
        stored += database.store(conn, markets)
        day = min(day + DAY, end)
        database.synced(conn, "polymarket_us", "markets", "", day)
    log(f"Polymarket US markets: {stored:,} stored for events starting {(end - start) / DAY:.0f} days back")
    return stored


def stretches(times, span=polymarket_us.PRICES_SPAN):
    """
    The stretches of price history covering BEFORE before to AFTER after each of times, in seconds since 1970, as (start,
    end) pairs: overlapping ones merged, and none longer than span.
    """
    merged = []
    for t in sorted(times):
        start, end = int(t) - BEFORE, int(t) + AFTER
        if merged and start <= merged[-1][1] and end - merged[-1][0] <= span:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def read_prices(conn, wanted, now, log=print):
    """
    Store the price history of each Polymarket US market of wanted, {slug: [times]}, around those times, see
    stretches(), but for the stretches read before. Returns how many prices were stored.
    """
    done = database.price_reads(conn)
    todo = [(slug, start, end) for slug, times in wanted.items() for start, end in stretches(times) if (slug, start) not in done]
    log(f"Polymarket US prices: {len(todo):,} stretches of {len(wanted):,} markets to read")
    stored = 0
    for i, (slug, start, end) in enumerate(todo, 1):
        history = polymarket_us.price_history(slug, start, end)
        stored += database.store(conn, polymarket_us.parse_prices(slug, history))
        database.store(conn, [UsPriceRead(slug, start, end, len(history), now)])
        if i % LOG_EVERY == 0 or i == len(todo):
            log(f"Polymarket US prices: {i:,} of {len(todo):,} stretches read, {stored:,} prices stored")
    return stored


def read_books(conn, now, log=print):
    """
    Snapshot the books of the open Polymarket US markets of BOOK_TYPES whose games start within BOOK_HOURS of now. Returns
    how many books were stored.
    """
    first, last = (now + hours * 3600 for hours in BOOK_HOURS)
    slugs = [m.slug for m in database.us_markets(conn)
             if not m.closed and m.market_type in BOOK_TYPES and first <= (epoch(m.game_start) or 0) <= last]
    log(f"Polymarket US books: {len(slugs):,} to snapshot")
    stored = 0
    for slug in slugs:
        levels = polymarket_us.parse_book(slug, polymarket_us.book(slug), time.time(), BOOK_LEVELS)
        stored += bool(database.store(conn, levels))
    log(f"Polymarket US books: {stored:,} stored")
    return stored


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read Polymarket US's markets and prices, for pricing copies of polymarket.com's traders.")
    ap.add_argument("--days", type=int, default=HISTORY_DAYS, help=f"how far back Polymarket US's events are read, {HISTORY_DAYS} by default")
    ap.add_argument("--books", action="store_true", help="snapshot the books of the markets a copy trades instead")
    args = ap.parse_args()
    conn = database.connect()
    now = time.time()
    if args.books:
        read_books(conn, now, log)
    else:
        read_events(conn, now, log)
        last = database.synced_at(conn, "polymarket_us", "markets", "")
        read_markets(conn, max(now - args.days * DAY, (last or 0) - REREAD_DAYS * DAY), now + DAY, now, log)
        found, markets = match.matches(conn), database.markets(conn)
        copied = {a for pool in POOLS for _, _, chosen in copy.planned(conn, pool, found, markets, COPIED)[0] for a in chosen}
        log(f"Polymarket US prices: for {len(copied):,} traders the copy simulation copies")
        read_prices(conn, match.wanted_prices(conn, found, sorted(copied)), now, log)
