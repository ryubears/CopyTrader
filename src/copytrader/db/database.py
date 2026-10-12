"""
SQLite storage for the traders followed and what they did.

One database file holds every table:

    traders       the traders followed, and Hyperliquid's kind of account, written by record/standings.py and record/accounts.py
    standings     each day's leaderboards, by record/standings.py
    syncs         when each followed account was last read, for each kind of read, by record/accounts.py
    hl_fills      a followed Hyperliquid account's fills, by record/accounts.py
    hl_funding    its funding payments
    hl_ledger     its deposits, withdrawals, transfers, and other changes of its cash
    hl_accounts   its value and margin at each reading of its positions
    hl_positions  its open positions at each reading
    pm_markets    Polymarket's markets, from Gamma, for the backfill
    pm_activity   each Polymarket trader's trades and other activity, for the backfill

The tables themselves are in schema.sql, and the model of each one's rows in models.py. This file holds the reads and
writes.
"""

import sqlite3
from copytrader.common.paths import DATA_DIR
from copytrader.db import schema
from copytrader.db.models import AccountState, Activity, Fill, Funding, LedgerUpdate, Market, Position, Standing, Sync, Trader
from dataclasses import asdict, fields
from pathlib import Path

DB_PATH = DATA_DIR / "copytrader.sqlite"
# Each model's table, and what a row already stored meets: kept, for a record that never changes, or replaced, for one
# read again as it changes. A trader is kept, so the board and time that first put them there stay.
TABLES = {
    Trader: ("traders", "INSERT OR IGNORE"),
    Standing: ("standings", "INSERT OR REPLACE"),
    Sync: ("syncs", "INSERT OR REPLACE"),
    Fill: ("hl_fills", "INSERT OR IGNORE"),
    Funding: ("hl_funding", "INSERT OR IGNORE"),
    LedgerUpdate: ("hl_ledger", "INSERT OR IGNORE"),
    AccountState: ("hl_accounts", "INSERT OR IGNORE"),
    Position: ("hl_positions", "INSERT OR IGNORE"),
    Market: ("pm_markets", "INSERT OR REPLACE"),
    Activity: ("pm_activity", "INSERT OR IGNORE"),
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


# TRADERS

def traders(conn, venue):
    """
    A venue's followed traders, first followed first.
    """
    return [Trader(*row) for row in conn.execute(f"SELECT {columns(Trader)} FROM traders WHERE venue = ? ORDER BY first_seen, address", (venue,))]


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
    store(conn, [Sync(venue, address, kind, at)])


def last_time(conn, table, address, column="time"):
    """
    The latest time of an account's rows in a table, or None when it has none, for the next read to start from.
    """
    return conn.execute(f"SELECT MAX({column}) FROM {table} WHERE address = ?", (address,)).fetchone()[0]
