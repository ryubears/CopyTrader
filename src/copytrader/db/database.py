"""
SQLite storage for the traders followed and what they did.

One database file holds every table:

    traders       the traders followed, and Hyperliquid's kind of account, written by record/standings.py and record/accounts.py
    standings     each day's leaderboards, by record/standings.py
    pnl_history   each candidate trader's PnL over time, as the venues chart it, by history/pnl.py
    syncs         when each followed account was last read, for each kind of read, by record/accounts.py
    hl_fills      a followed Hyperliquid account's fills, by record/accounts.py
    hl_funding    its funding payments
    hl_ledger     its deposits, withdrawals, transfers, and other changes of its cash
    hl_accounts   its value and margin at each reading of its positions
    hl_positions  its open positions at each reading
    pm_markets    the Polymarket markets the activity names, from Gamma, by history/polymarket.py
    pm_activity   each followed Polymarket trader's trades and other activity, by history/polymarket.py
    pm_events     the polymarket.com events of the stored markets, for the games they are, by history/polymarket_us.py
    us_markets    the Polymarket US markets those might match, by history/polymarket_us.py
    us_prices     what buying either side of those cost, around the times a copy would have traded, by history/polymarket_us.py
    us_price_reads  the stretches of price history read
    us_books      snapshots of Polymarket US's order books, by history/polymarket_us.py
    lag_pairs     the Polymarket US markets recorded live beside polymarket.com's, by record/lag.py
    lag_books     both venues' best levels for those, as they change, by record/lag.py

The tables themselves are in schema.sql, and the model of each one's rows in models.py. This file holds the reads and
writes.
"""

import sqlite3
from copytrader.common.paths import DATA_DIR
from copytrader.common.timeutil import epoch
from copytrader.db import models, schema
from dataclasses import asdict, fields
from pathlib import Path

DB_PATH = DATA_DIR / "copytrader.sqlite"
# Each model's table, and what a row already stored meets: kept, for a record that never changes, or replaced, for one
# read again as it changes. A trader is kept, so the board and time that first put them there stay.
TABLES = {
    models.Trader: ("traders", "INSERT OR IGNORE"),
    models.Standing: ("standings", "INSERT OR REPLACE"),
    models.PnlPoint: ("pnl_history", "INSERT OR REPLACE"),
    models.Sync: ("syncs", "INSERT OR REPLACE"),
    models.Fill: ("hl_fills", "INSERT OR IGNORE"),
    models.Funding: ("hl_funding", "INSERT OR IGNORE"),
    models.LedgerUpdate: ("hl_ledger", "INSERT OR IGNORE"),
    models.AccountState: ("hl_accounts", "INSERT OR IGNORE"),
    models.Position: ("hl_positions", "INSERT OR IGNORE"),
    models.Market: ("pm_markets", "INSERT OR REPLACE"),
    models.Activity: ("pm_activity", "INSERT OR IGNORE"),
    models.Event: ("pm_events", "INSERT OR REPLACE"),
    models.UsMarket: ("us_markets", "INSERT OR REPLACE"),
    models.UsPrice: ("us_prices", "INSERT OR IGNORE"),
    models.UsPriceRead: ("us_price_reads", "INSERT OR REPLACE"),
    models.UsBookLevel: ("us_books", "INSERT OR IGNORE"),
    models.LagPair: ("lag_pairs", "INSERT OR IGNORE"),
    models.LagBook: ("lag_books", "INSERT OR IGNORE"),
}


# CONNECTION

def connect(db_path=None):
    """
    Open the database, creating the file and tables if needed. Uses DB_PATH unless a path is given.
    """
    db_path = Path(db_path or DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    # Write ahead logging lets an analysis read while the recorder writes.
    conn.execute("PRAGMA journal_mode=WAL")
    schema.create(conn)
    return conn


# WRITES

def columns(model):
    """
    A model's columns, its fields in order, comma separated.
    """
    return ", ".join(f.name for f in fields(model))


def insert_sql(model):
    """
    An insert of every field of a model into its table, with a named parameter per column, meeting a row already stored
    as TABLES says.
    """
    table, verb = TABLES[model]
    return f"{verb} INTO {table} ({columns(model)}) VALUES ({', '.join(':' + f.name for f in fields(model))})"


def store(conn, rows):
    """
    Store rows, models all of one kind, in one transaction. Returns how many were new, or replaced.
    """
    if not rows:
        return 0
    before = conn.total_changes
    with conn:
        conn.executemany(insert_sql(type(rows[0])), [asdict(row) for row in rows])
    return conn.total_changes - before


def read(conn, model, rest="", params=()):
    """
    The rows of a model's table, as models, that the rest of the query, such as its WHERE and ORDER BY, picks.
    """
    return [model(*row) for row in conn.execute(f"SELECT {columns(model)} FROM {TABLES[model][0]} {rest}", params)]


# TRADERS

def traders(conn, venue):
    """
    A venue's followed traders, first followed first.
    """
    return read(conn, models.Trader, "WHERE venue = ? ORDER BY first_seen, address", (venue,))


def unchecked(conn, venue, limit):
    """
    Up to limit of a venue's followed traders whose kind of account is not known yet, first followed first.
    """
    return [row[0] for row in conn.execute("SELECT address FROM traders WHERE venue = ? AND role IS NULL ORDER BY first_seen, address LIMIT ?",
                                           (venue, limit))]


def set_role(conn, venue, address, role):
    """
    Store what kind of account a followed trader's is.
    """
    with conn:
        conn.execute("UPDATE traders SET role = ? WHERE venue = ? AND address = ?", (role, venue, address))


# STANDINGS

def last_day(conn, venue):
    """
    The last UTC day a venue's standings were recorded, as YYYY-MM-DD, or None before the first.
    """
    return conn.execute("SELECT MAX(day) FROM standings WHERE venue = ?", (venue,)).fetchone()[0]


def standing_addresses(conn, venue, day, boards="%"):
    """
    Every trader on any of a venue's boards on a day, as YYYY-MM-DD, in order, or only on boards LIKE boards, as '%/VOL'.
    """
    return [row[0] for row in conn.execute("SELECT DISTINCT address FROM standings WHERE venue = ? AND day = ? AND board LIKE ? ORDER BY address",
                                           (venue, day, boards))]


# READS OF ACCOUNTS

def due(conn, venue, kind, before, limit=-1):
    """
    The followed accounts of a venue whose last read of a kind finished before before, in seconds since 1970: those never
    read for it first, then those read longest ago, up to limit, or all for -1. On Hyperliquid an account is read only
    once its role says it is a user's own.
    """
    return [row[0] for row in conn.execute("""
        SELECT t.address FROM traders t
        LEFT JOIN syncs s ON s.venue = t.venue AND s.address = t.address AND s.kind = ?
        WHERE t.venue = ? AND (t.venue != 'hyperliquid' OR t.role = 'user') AND (s.synced_at IS NULL OR s.synced_at < ?)
        ORDER BY s.synced_at IS NOT NULL, s.synced_at, t.first_seen, t.address
        LIMIT ?""", (kind, venue, before, limit))]


def synced(conn, venue, kind, address, at):
    """
    Note that an account's read of a kind finished at a time, in seconds since 1970.
    """
    store(conn, [models.Sync(venue, address, kind, at)])


def pnl_histories(conn, venue):
    """
    Each trader's PnL history on a venue, as {address: [(time, pnl, account_value)]}, oldest first.
    """
    found = {}
    for address, time, pnl, value in conn.execute("SELECT address, time, pnl, account_value FROM pnl_history WHERE venue = ? ORDER BY address, time",
                                                  (venue,)):
        found.setdefault(address, []).append((time, pnl, value))
    return found


def synced_since(conn, venue, kind, since):
    """
    The accounts of a venue whose last read of a kind finished at or after since, in seconds since 1970.
    """
    return {row[0] for row in conn.execute("SELECT address FROM syncs WHERE venue = ? AND kind = ? AND synced_at >= ?", (venue, kind, since))}


def synced_at(conn, venue, kind, address):
    """
    When an account's last read of a kind finished, in seconds since 1970, or None if never.
    """
    row = conn.execute("SELECT synced_at FROM syncs WHERE venue = ? AND kind = ? AND address = ?", (venue, kind, address)).fetchone()
    return row[0] if row else None


def activity_of(conn, address):
    """
    A Polymarket trader's stored activity, oldest first.
    """
    return read(conn, models.Activity, "WHERE address = ? ORDER BY timestamp, seq", (address,))


def markets(conn):
    """
    Every stored Polymarket market, as {condition_id: Market}.
    """
    return {m.condition_id: m for m in read(conn, models.Market)}


def fills_of(conn, address):
    """
    A Hyperliquid account's stored fills, oldest first.
    """
    return read(conn, models.Fill, "WHERE address = ? ORDER BY time, tid", (address,))


def markets_wanted(conn):
    """
    The condition ids of the markets the stored Polymarket activity names that are not stored, or were not resolved when
    last read, in order.
    """
    return [row[0] for row in conn.execute("""
        SELECT DISTINCT a.condition_id FROM pm_activity a LEFT JOIN pm_markets m ON m.condition_id = a.condition_id
        WHERE a.condition_id != '' AND (m.condition_id IS NULL OR m.resolution IS NOT 'resolved')
        ORDER BY a.condition_id""")]


def last_time(conn, table, address, column="time"):
    """
    The latest time of an account's rows in a table, or None when it has none, for the next read to start from.
    """
    return conn.execute(f"SELECT MAX({column}) FROM {table} WHERE address = ?", (address,)).fetchone()[0]


# POLYMARKET US

def events_wanted(conn):
    """
    The slugs of the polymarket.com events of the stored sports and politics markets that are not stored, in order.
    """
    return [row[0] for row in conn.execute("""
        SELECT DISTINCT m.event_slug FROM pm_markets m LEFT JOIN pm_events e ON e.event_slug = m.event_slug
        WHERE m.category IN ('sports', 'politics') AND m.event_slug IS NOT NULL AND e.event_slug IS NULL
        ORDER BY m.event_slug""")]


def events(conn):
    """
    Every stored polymarket.com event, as {event_slug: Event}.
    """
    return {e.event_slug: e for e in read(conn, models.Event)}


def game_ids(conn):
    """
    The ids of the games among the stored polymarket.com events.
    """
    return {row[0] for row in conn.execute("SELECT game_id FROM pm_events WHERE game_id IS NOT NULL")}


def us_markets(conn):
    """
    Every stored Polymarket US market.
    """
    return read(conn, models.UsMarket)


def price_reads(conn):
    """
    The stretches of Polymarket US's price history read, as a set of (slug, start).
    """
    return set(conn.execute("SELECT slug, start FROM us_price_reads"))


def us_prices(conn, slug):
    """
    A Polymarket US market's stored prices, oldest first.
    """
    return read(conn, models.UsPrice, "WHERE slug = ? ORDER BY time", (slug,))


def trade_times(conn, condition_ids, addresses):
    """
    The minutes in which the traders of addresses traded in each market of condition_ids, as (condition_id, seconds
    since 1970) pairs, each minute once.
    """
    found, wanted = set(), set(condition_ids)
    for address in addresses:
        rows = conn.execute("SELECT condition_id, timestamp FROM pm_activity WHERE address = ? AND type = 'TRADE'", (address,))
        found.update((c, t // 60 * 60) for c, t in rows if c in wanted)
    return sorted(found)


def us_books(conn):
    """
    Every stored snapshot of a Polymarket US order book, as {(slug, time): {"bid": [(price, contracts)], "offer": [...]}},
    each side best first.
    """
    found = {}
    rows = conn.execute("SELECT slug, time, side, price, contracts FROM us_books ORDER BY slug, time, side, level")
    for slug, time, side, price, contracts in rows:
        found.setdefault((slug, time), {"bid": [], "offer": []})[side].append((price, contracts))
    return found


# THE LAG

def lag_pairs(conn):
    """
    Every pair recorded by record/lag.py, as {slug: LagPair}.
    """
    return {p.slug: p for p in read(conn, models.LagPair)}


def lag_unfinished(conn, before):
    """
    The recorded pairs whose games started before before, in seconds since 1970, and are not noted finished, see
    record/lag.py, oldest first.
    """
    done = synced_since(conn, "lag", "finished", 0)
    return [p for p in read(conn, models.LagPair, "ORDER BY game_start") if p.slug not in done and (epoch(p.game_start) or 0) < before]


def lag_books(conn, venue, key):
    """
    One venue's recorded books of one outcome, oldest first, as LagBook.
    """
    return read(conn, models.LagBook, "WHERE venue = ? AND key = ? ORDER BY time", (venue, key))
