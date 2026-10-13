"""
Record polymarket.com's and Polymarket US's books side by side, live, to measure how far and how long Polymarket US's
prices trail polymarket.com's during games, see analysis/lag.py.

Every REFRESH_SECONDS it reads Polymarket US's events starting from AFTER_START ago to BEFORE_START ahead, and
polymarket.com's events of the same games, by their game ids, and pairs each open Polymarket US market of the TYPES a
copy trades with the polymarket.com outcome its long side holds, see analysis/match.py, the games under way first, up
to MAX_PAIRS. Between refreshes it reads every pair's polymarket.com book, all in one call, every COM_SECONDS, and takes
each change of Polymarket US's books from its market feed as it comes, see api/polymarket_us.py, since the gateway's
books are up to 30 seconds old. The feed needs the account's key in the data folder. A book's best LEVELS levels a
side are stored when they differ from the last stored, or HEARTBEAT_SECONDS after it, a Polymarket US book that has not
changed as well, so a stretch without rows is one the recorder did not see. A Polymarket US market that has ended is
dropped. When the pairs change, the feed is connected again for the new ones, as it takes no unsubscribing.

Once a pair's game started more than AFTER_START ago, Polymarket US's minute price history of the market over its
recording is read, to set beside the books, and its polymarket.com market is read again for how it resolved, until it
has, or FINISH_DAYS have passed.

It only reads. Ctrl-C or a kill stops it, and the books read but not stored yet are stored first.

Run from src/ with:
    python3 -m copytrader.record.lag
    python3 -m copytrader.record.lag --seconds 300 --db /tmp/lag.sqlite
"""

import argparse
import dataclasses
import itertools
import json
import signal
import time
from collections import Counter, defaultdict
from copytrader.analysis import match
from copytrader.api import polymarket, polymarket_us
from copytrader.common.log import log, with_traceback
from copytrader.common.timeutil import DAY, epoch
from copytrader.db import database
from copytrader.db.models import LagPair, UsPriceRead

REFRESH_SECONDS = 600
BEFORE_START = 3600             # How long before a game starts its markets are recorded.
AFTER_START = 4 * 3600          # How long after.
TYPES = ("moneyline", "totals", "drawable_outcome")
MAX_PAIRS = 150                 # The most markets recorded at once, so polymarket.com's books of all of them take two calls a read.
COM_SECONDS = 1                 # Seconds between reads of polymarket.com's books.
COM_BATCH = 100                 # polymarket.com books a call.
GAMES_BATCH = 50                # Games whose polymarket.com events are asked for a call.
HEARTBEAT_SECONDS = 60
LEVELS = 5
FLUSH_SECONDS = 5               # Seconds between writes of the books read.
FINISH_DAYS = 2
STATUS_SECONDS = 600
ENDED = ("MARKET_STATE_EXPIRED", "MARKET_STATE_TERMINATED")     # Polymarket US's states of a market that will not trade again.


def find_pairs(now):
    """
    The LagPair of every open Polymarket US market of TYPES of a game starting from AFTER_START before now to
    BEFORE_START after, matched with polymarket.com's, the games under way first, then the soonest, at most MAX_PAIRS.
    """
    by_game = defaultdict(list)
    for e in polymarket_us.events(now - AFTER_START, now + BEFORE_START):
        for m in e.get("markets") or []:
            us = polymarket_us.parse_market(e, m, now)
            if us.market_type in TYPES and not us.closed and us.game_id is not None:
                by_game[us.game_id].append(us)
    us_markets = {us.slug: us for markets in by_game.values() for us in markets}
    found = {}
    games = (e for chunk in itertools.batched(sorted(by_game), GAMES_BATCH) for e in polymarket.events(game_id=list(chunk), closed=False))
    for e in games:
        event = polymarket.parse_event(e, now)
        for m in e.get("markets") or []:
            market = polymarket.parse_market(m, now)
            pair = match.match(market, event, by_game) if not market.closed else None
            if pair is None or pair.slug in found or "long" not in pair.sides:
                continue
            i, us = pair.sides.index("long"), us_markets[pair.slug]
            found[pair.slug] = LagPair(us.slug, json.loads(market.token_ids)[i], market.condition_id, json.loads(market.outcomes)[i],
                                       event.game_id, us.game_start, us.market_type, us.fee_coefficient, now)
    started = {slug: epoch(p.game_start) or now for slug, p in found.items()}
    return sorted(found.values(), key=lambda p: (started[p.slug] > now, abs(started[p.slug] - now)))[:MAX_PAIRS]


def changed(last, row):
    """
    Whether a LagBook is worth storing after the last stored of its outcome on its venue: its levels differ, or
    HEARTBEAT_SECONDS have passed.
    """
    return last is None or (row.bids, row.asks) != (last.bids, last.asks) or row.time - last.time >= HEARTBEAT_SECONDS


def finish(conn, now, log):
    """
    For each recorded pair whose game started more than AFTER_START ago and is not noted finished, read Polymarket US's
    price history of it over its recording, once, and its polymarket.com market, and note it finished once that has
    resolved, or FINISH_DAYS after its game. Returns how many were noted finished.
    """
    waiting = database.lag_unfinished(conn, now - AFTER_START)
    read = database.price_reads(conn)
    for p in waiting:
        start = int(p.first_seen) - 300
        if (p.slug, start) not in read:
            end = int(min(now, start + polymarket_us.PRICES_SPAN))
            history = polymarket_us.price_history(p.slug, start, end)
            database.store(conn, polymarket_us.parse_prices(p.slug, history))
            database.store(conn, [UsPriceRead(p.slug, start, end, len(history), now)])
    resolved = set()
    for chunk in itertools.batched(sorted({p.condition_id for p in waiting}), 50):
        markets = [polymarket.parse_market(m, now) for m in polymarket.markets(condition_ids=list(chunk), closed=True, include_tag=True)]
        database.store(conn, markets)
        resolved.update(m.condition_id for m in markets if m.resolution == "resolved")
    done = [p for p in waiting if p.condition_id in resolved or (epoch(p.game_start) or 0) < now - FINISH_DAYS * DAY]
    for p in done:
        database.synced(conn, "lag", "finished", p.slug, now)
    if waiting:
        log(f"{len(waiting):,} games' markets past, {len(done):,} of them resolved and finished")
    return len(done)


def store_changed(conn, rows, last):
    """
    Store the rows, LagBooks, that changed(), in order, keeping last, {(venue, key): LagBook}, up to date. Returns a
    Counter of the books stored by venue.
    """
    keep = []
    for row in rows:
        if changed(last.get((row.venue, row.key)), row):
            keep.append(row)
            last[(row.venue, row.key)] = row
    database.store(conn, keep)
    return Counter(f"{row.venue} books" for row in keep)


def heartbeats(last, slugs, now):
    """
    For each of the markets, slugs, whose book has come from Polymarket US's feed on its connection, a copy of the last
    book stored, timed now, if that was HEARTBEAT_SECONDS ago or more: the feed sends a book only when it changes, so
    one not sent since is still the book. last is {(venue, key): LagBook}, see store_changed().
    """
    rows = (last.get(("polymarket_us", slug)) for slug in sorted(slugs))
    return [dataclasses.replace(row, time=now) for row in rows if row and now - row.time >= HEARTBEAT_SECONDS]


def record(conn, seconds=None, log=log):
    """
    Record both venues' books, see the top of this file, until seconds have passed, or for good without.
    """
    started = time.time()
    pairs, stream, streamed = {}, None, set()
    last, waiting, counts = {}, [], Counter()
    refreshed = com_read = flushed = status_at = 0.0
    log(f"recording both venues' books of the games from {BEFORE_START // 60} minutes before their start to {AFTER_START // 3600} hours after")
    try:
        while seconds is None or time.time() - started < seconds:
            now = time.time()
            if now - refreshed >= REFRESH_SECONDS:
                refreshed = now
                try:
                    found = find_pairs(now)
                    counts["pairs"] += database.store(conn, found)
                    if stream and {p.slug for p in found} != set(pairs):
                        stream.close()
                        stream = None
                    pairs = {p.slug: p for p in found}
                    under_way = sum((epoch(p.game_start) or now) <= now for p in found)
                    log(f"{len(found):,} markets of {len({p.game_id for p in found}):,} games to record, {under_way:,} of them under way")
                    finish(conn, now, log)
                except Exception as error:
                    log(with_traceback("finding the markets failed, trying again at the next refresh", error))
            if not pairs:
                time.sleep(min(30, max(0.0, REFRESH_SECONDS - (time.time() - refreshed))))
                continue
            try:
                if stream is None:
                    stream, streamed = polymarket_us.book_stream(pairs), set()
                if now - com_read >= COM_SECONDS:
                    com_read = now
                    for chunk in itertools.batched([p.token_id for p in pairs.values()], COM_BATCH):
                        books = polymarket.books(list(chunk))
                        seen = time.time()
                        waiting += [polymarket.parse_book(b, seen, LEVELS) for b in books]
                try:
                    book = polymarket_us.streamed_book(stream.recv(timeout=max(0.0, com_read + COM_SECONDS - time.time())))
                except TimeoutError:
                    book = None
                slug = book.get("marketSlug") if book else None
                if slug in pairs and book.get("state") in ENDED:
                    del pairs[slug]
                elif slug in pairs:
                    waiting.append(polymarket_us.parse_lag_book(slug, book, time.time(), LEVELS))
                    streamed.add(slug)
            except Exception as error:
                log(with_traceback("reading the books failed, connecting again in 5 seconds", error))
                if stream:
                    stream.close()
                stream = None
                time.sleep(5)
            if time.time() - flushed >= FLUSH_SECONDS:
                counts.update(store_changed(conn, waiting, last))
                counts.update(store_changed(conn, heartbeats(last, streamed & set(pairs), time.time()), last))
                waiting, flushed = [], time.time()
            if time.time() - status_at >= STATUS_SECONDS:
                status_at = time.time()
                log(f"recording {len(pairs):,} markets; since the start: " + ", ".join(f"{n:,} {what}" for what, n in sorted(counts.items())))
    finally:
        if stream:
            stream.close()
        counts.update(store_changed(conn, waiting, last))
        log("since the start: " + ", ".join(f"{n:,} {what}" for what, n in sorted(counts.items())))


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Record polymarket.com's and Polymarket US's books side by side, live.")
    ap.add_argument("--seconds", type=float, help="stop after this long, by default run until stopped")
    ap.add_argument("--db", help="the database to record into, by default data/copytrader.sqlite, another for a trial run")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        record(database.connect(args.db), args.seconds)
    except KeyboardInterrupt:
        log("stopped")
