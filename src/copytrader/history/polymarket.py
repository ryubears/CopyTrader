"""
Read the followed Polymarket traders' activity, and the markets it touched, for the trader analytics, see
analysis/traders.py, or, with --copy-candidates, that of the traders the copy simulation might copy, see
analysis/copy.py.

Each followed trader's activity is read oldest first from where its last read stopped, or, the first time, from as far
back as ROWS_PER_TRADER rows reach at the pace of their latest ACTIVITY_PAGE rows, but no further than HISTORY_DAYS. Most
traders make a few thousand rows a year and are read whole, a few seconds each. A few make tens of thousands a month, as
on 2026-10-12 two of twelve followed traders sampled made over 60,000 rows in under three months, too fast for a copy to
follow, and their latest weeks are plenty to tell them apart. The rows are stored a CHUNK at a time as they come, so a
stop keeps what was read, and a trader read within REREAD_DAYS is skipped, so a rerun picks up where the last stopped.

Then every market the stored activity names that is not stored, or was not resolved when last read, is read from Gamma,
MARKETS_BATCH a call, closed and open ones asked for apart, since Gamma leaves out closed markets unless asked. A market
Gamma does not return is left out, and asked for again on the next run.

Run from src/ with:
    python3 -m copytrader.history.polymarket
    python3 -m copytrader.history.polymarket --days 90
    python3 -m copytrader.history.polymarket --copy-candidates
"""

import argparse
import itertools
import time
from copytrader.analysis import copy
from copytrader.api import polymarket
from copytrader.common.log import log, with_traceback
from copytrader.db import database

HISTORY_DAYS = 180
ROWS_PER_TRADER = 20_000
ACTIVITY_PAGE = 500             # The latest rows asked for to judge a trader's pace, one call.
CHUNK = 2_000                   # Rows stored per transaction.
REREAD_DAYS = 1
MARKETS_BATCH = 50              # Condition ids per Gamma call.
LOG_EVERY = 50                  # Traders, or market calls, between progress lines.
DAY = 86400


def first_start(address, now, days):
    """
    Where a trader's first read starts, in seconds since 1970: as far back as ROWS_PER_TRADER rows reach at the pace of
    their latest ACTIVITY_PAGE rows, but no further than days before now. A trader with fewer rows than that is read back
    the whole days.
    """
    floor = int(now - days * DAY)
    latest = polymarket.latest_activity(address, ACTIVITY_PAGE)
    if len(latest) < ACTIVITY_PAGE:
        return floor
    span = max(1, latest[0]["timestamp"] - latest[-1]["timestamp"])
    return max(floor, int(now - span * ROWS_PER_TRADER / ACTIVITY_PAGE))


def read_trader(conn, address, now, days):
    """
    Store a trader's activity from where its last read stopped, or from first_start(), up to ROWS_PER_TRADER rows, a CHUNK
    at a time. Returns how many rows were new.
    """
    last = database.last_time(conn, "pm_activity", address, "timestamp")
    start = last if last is not None else first_start(address, now, days)
    stored = 0
    for chunk in itertools.batched(itertools.islice(polymarket.parse_activity(polymarket.activity(address, start=start)), ROWS_PER_TRADER), CHUNK):
        stored += database.store(conn, list(chunk))
    return stored


def read_traders(conn, addresses, now, days=HISTORY_DAYS, log=print):
    """
    Store the activity of each trader not read within REREAD_DAYS of now, in seconds since 1970, noting each read in syncs
    as 'activity'. A failed read is logged and left for the next run. Returns how many rows were new.
    """
    done = database.synced_since(conn, "polymarket", "activity", now - REREAD_DAYS * DAY)
    todo = [a for a in addresses if a not in done]
    log(f"activity: {len(addresses):,} traders followed, {len(todo):,} to read")
    stored = 0
    t0 = time.time()
    for i, address in enumerate(todo, 1):
        try:
            stored += read_trader(conn, address, now, days)
            database.synced(conn, "polymarket", "activity", address, time.time())
        except Exception as error:
            log(with_traceback(f"reading the activity of {address} failed", error))
        if i % LOG_EVERY == 0 or i == len(todo):
            log(f"activity: {i:,} of {len(todo):,} traders tried, {stored:,} rows stored, in {time.time() - t0:.0f}s")
    return stored


def read_markets(conn, now, log=print):
    """
    Store every market the stored activity names that is not stored, or was not resolved when last read. Returns how
    many markets were stored.
    """
    wanted = database.markets_wanted(conn)
    calls = [wanted[i:i + MARKETS_BATCH] for i in range(0, len(wanted), MARKETS_BATCH)]
    log(f"markets: {len(wanted):,} to read, in {len(calls):,} batches")
    stored = 0
    for i, batch in enumerate(calls, 1):
        for closed in (True, False):
            found = polymarket.markets(condition_ids=batch, closed=closed, include_tag=True)
            stored += database.store(conn, [polymarket.parse_market(m, now) for m in found])
        if i % LOG_EVERY == 0 or i == len(calls):
            log(f"markets: {i:,} of {len(calls):,} batches read, {stored:,} markets stored")
    return stored


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read the followed Polymarket traders' activity, and its markets, into SQLite.")
    ap.add_argument("--days", type=int, default=HISTORY_DAYS, help=f"how far back a first read may reach, {HISTORY_DAYS} days by default")
    ap.add_argument("--copy-candidates", action="store_true", help="read the traders analysis/copy.py might copy, not the followed")
    args = ap.parse_args()
    conn = database.connect()
    addresses = copy.candidates(conn) if args.copy_candidates else [t.address for t in database.traders(conn, "polymarket")]
    read_traders(conn, addresses, time.time(), args.days, log)
    read_markets(conn, time.time(), log)
