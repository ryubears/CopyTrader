"""
Record the venues' leaderboards once a UTC day, and follow the traders at the top of them.

A leaderboard only says who leads now, so a ranking at some past time has to come from a record kept as it happened. On
Polymarket the record keeps the top PM_DEPTH traders of each sports and politics board, by PnL and by volume, over a day,
a week, a month, and all time. On Hyperliquid, whose leaderboard is one file of every account, it keeps every account
but vaults that traded in the month before, about 18,800 on 2026-10-08, a row for each of its four windows.

The top FOLLOW_TOP traders of each PnL board over a week, a month, and all time are followed, see traders in
schema.sql. Following only ever adds, so a trader who falls from the top is still followed, and the record shows what
came after, not only the winners. A followed Hyperliquid account's fills, positions, and funding are then read on a
schedule, see record/accounts.py, since its fills are only kept for so long. A Polymarket trader's whole history can be
read at any time, so on Polymarket following only lists them for the backfill.
"""

from copytrader.api import hyperliquid, polymarket
from copytrader.db import database
from copytrader.db.models import Trader
from datetime import UTC, datetime

PM_CATEGORIES = ("SPORTS", "POLITICS")
PM_PERIODS = ("DAY", "WEEK", "MONTH", "ALL")
PM_ORDERS = ("PNL", "VOL")
PM_DEPTH = 1000             # Traders recorded from the top of each Polymarket board, 20 calls a board, 320 for all 16.
# The PnL boards whose top traders are followed. A day's top is mostly luck, and changes too much to follow.
FOLLOW_BOARDS = {"polymarket": [f"{c}/{p}/PNL" for c in PM_CATEGORIES for p in ("WEEK", "MONTH", "ALL")],
                 "hyperliquid": ["week", "month", "allTime"]}
FOLLOW_TOP = 200            # Traders followed from the top of each of those boards.


def today(now):
    """
    The UTC day of a time in seconds since 1970, as YYYY-MM-DD.
    """
    return datetime.fromtimestamp(now, UTC).date().isoformat()


def top(standings, board, n):
    """
    The n Standings of a board with the most PnL, most first.
    """
    return sorted((s for s in standings if s.board == board), key=lambda s: -s.pnl)[:n]


def follow(conn, venue, standings, names, now):
    """
    Follow the top FOLLOW_TOP traders of each of the venue's FOLLOW_BOARDS, each with the board and rank that first put
    them there, and their name from names where the venue has one. Returns how many were not followed before.
    """
    traders = [Trader(venue, s.address, names.get(s.address), f"{board} #{s.rank or rank} on {s.day}", now, None)
               for board in FOLLOW_BOARDS[venue] for rank, s in enumerate(top(standings, board, FOLLOW_TOP), 1)]
    return database.store(conn, traders)


def record_polymarket(conn, now):
    """
    Store the day's standings on each of Polymarket's sports and politics boards, and follow the top of the PnL ones.
    Returns how many standings were stored and how many traders were newly followed.
    """
    day = today(now)
    standings, names = [], {}
    for category in PM_CATEGORIES:
        for period in PM_PERIODS:
            for order in PM_ORDERS:
                board = f"{category}/{period}/{order}"
                for row in polymarket.leaderboard(category, period, order, PM_DEPTH):
                    standings.append(polymarket.parse_standing(row, board, day, now))
                    names[row["proxyWallet"].lower()] = row.get("userName") or None
    return database.store(conn, standings), follow(conn, "polymarket", standings, names, now)


def record_hyperliquid(conn, now):
    """
    Store the day's standings of every account on Hyperliquid's leaderboard that traded in the month before, but
    vaults, and follow the top of each window's PnL. Returns how many standings were stored and how many traders were
    newly followed.
    """
    day = today(now)
    vaults = {v["summary"]["vaultAddress"].lower() for v in hyperliquid.vaults()}
    standings = [s for row in hyperliquid.leaderboard() if row["ethAddress"].lower() not in vaults
                 for s in hyperliquid.parse_standings(row, day, now)]
    active = {s.address for s in standings if s.board == "month" and s.volume > 0}
    standings = [s for s in standings if s.address in active]
    return database.store(conn, standings), follow(conn, "hyperliquid", standings, {}, now)
