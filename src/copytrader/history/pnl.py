"""
Read each candidate trader's PnL over time, as the venues chart it on their own account pages, for the persistence test,
see analysis/persistence.py.

The candidates are drawn wide, so the test is not of the winners alone. On Polymarket they are every trader on any board
of the latest standings recorded, see record/standings.py, about 8,000: the top of each sports and politics board by PnL
and by volume, over a day, a week, a month, and all time. Their PnL is their profile page's chart, a point a day, across
every category, one call a trader. On Hyperliquid they are every account on the leaderboard but vaults that traded
HL_MIN_VOLUME over its life, about 7,000 on 2026-10-08, more than half no longer trading that month, so those who stopped
are in too. Their PnL is portfolio()'s, perps alone, a point about every two weeks, one call an account, 30 a minute on
half of Hyperliquid's limit, so about 4 hours for all. The followed traders are candidates on both.

A trader read within REREAD_DAYS is skipped, so a rerun picks up where the last stopped, and a later one brings them up to
date. A failed read is logged and left for the next run.

Run from src/ with:
    python3 -m copytrader.history.pnl
    python3 -m copytrader.history.pnl --venue hyperliquid
"""

import argparse
import time
from copytrader.api import hyperliquid, polymarket
from copytrader.common.log import log, with_traceback
from copytrader.db import database

HL_MIN_VOLUME = 100_000_000     # Dollars an account must have traded over its life to be a candidate on Hyperliquid.
REREAD_DAYS = 7
LOG_EVERY = 250                 # Traders between progress lines.


def polymarket_candidates(conn):
    """
    Every trader on any board of Polymarket's latest standings recorded, and every one followed.
    """
    found = set(database.standing_addresses(conn, "polymarket", database.last_day(conn, "polymarket")))
    return sorted(found | {t.address for t in database.traders(conn, "polymarket")})


def hyperliquid_candidates(conn):
    """
    Every account on Hyperliquid's leaderboard but vaults that traded HL_MIN_VOLUME over its life, trading now or not,
    and every one followed but a vault or a sub-account.
    """
    vaults = {v["summary"]["vaultAddress"].lower() for v in hyperliquid.vaults()}
    found = {row["ethAddress"].lower() for row in hyperliquid.leaderboard()
             if row["ethAddress"].lower() not in vaults and float(dict(row["windowPerformances"])["allTime"]["vlm"]) >= HL_MIN_VOLUME}
    return sorted(found | {t.address for t in database.traders(conn, "hyperliquid") if t.role in (None, "user")})


CANDIDATES = {"polymarket": polymarket_candidates, "hyperliquid": hyperliquid_candidates}
READERS = {"polymarket": lambda address: polymarket.parse_pnl(address, polymarket.pnl_history(address)),
           "hyperliquid": lambda address: hyperliquid.parse_portfolio(address, hyperliquid.portfolio(address))}


def backfill(conn, venue, addresses, now, log=print):
    """
    Store the PnL history of each of the addresses not read within REREAD_DAYS of now, in seconds since 1970, and note
    each read in syncs as 'pnl'. Returns how many were read.
    """
    done = database.synced_since(conn, venue, "pnl", now - REREAD_DAYS * 86400)
    todo = [a for a in addresses if a not in done]
    log(f"{venue}: {len(addresses):,} candidates, {len(todo):,} to read")
    read = stored = 0
    t0 = time.time()
    for i, address in enumerate(todo, 1):
        try:
            stored += database.store(conn, READERS[venue](address))
            database.synced(conn, venue, "pnl", address, time.time())
            read += 1
        except Exception as error:
            log(with_traceback(f"reading the PnL of {address} failed", error))
        if i % LOG_EVERY == 0 or i == len(todo):
            log(f"{venue}: {i:,} of {len(todo):,} tried, {stored:,} points stored, in {time.time() - t0:.0f}s")
    return read


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read the candidate traders' PnL histories into SQLite.")
    ap.add_argument("--venue", choices=sorted(CANDIDATES), help="only this venue, by default both, one after the other")
    args = ap.parse_args()
    conn = database.connect()
    for venue in [args.venue] if args.venue else list(CANDIDATES):
        backfill(conn, venue, CANDIDATES[venue](conn), time.time(), log)
