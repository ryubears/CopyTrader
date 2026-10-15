"""
Read the followed Hyperliquid accounts, each kind of read on its own schedule, so their history is kept past what
Hyperliquid keeps.

Before anything else, each followed account's kind is asked, a ROLE_BATCH at a time, since role() weighs three times
most requests, and only a user's own account is read after: a sub-account is left out, as vaults already are, see
record/standings.py. Then each kind of read in READERS comes due EVERY seconds after the account's last one. Fills come
due most often, since Hyperliquid documents that only an account's 10,000 most recent can be read, and an account that
trades more than that between reads loses the rest. In practice it varies: on 2026-10-12 one account gave 13,317 fills
back to 2023. Positions come next, since they are live even when an account's fills lag, and funding and the ledger
once a day.

A read of fills, funding, or the ledger starts from the latest row stored, or FIRST_DAYS back for an account read for
the first time, and stores the rows a CHUNK at a time as they come, so a long history is kept as far as it got. A read
takes at most ROWS_PER_READ rows, so one account's long history does not hold up the rest, and one that stops there is
due again at once, after the accounts never read yet, to go on where it stopped. Rows read again are skipped, so a read
that fails loses nothing: it is logged, and the account waits its turn as if it had been read.
"""

import itertools
import time
from copytrader.api import hyperliquid
from copytrader.common.log import with_traceback
from copytrader.db import database

ROLE_BATCH = 20             # Accounts asked their kind of account per pass, 1200 weight, a minute of Hyperliquid's limit.
BATCH = 5                   # Accounts read per pass for each kind of read.
EVERY = {"fills": 3 * 3600, "positions": 3600, "funding": 24 * 3600, "ledger": 24 * 3600}   # Seconds between an account's reads of each kind.
FIRST_DAYS = 365            # How far back an account's first read of fills, funding, or the ledger reaches.
ROWS_PER_READ = 20_000      # The most rows one read takes: 10 pages of fills, a minute of Hyperliquid's limit.
CHUNK = 2_000               # Rows stored per transaction.


def check_roles(conn, log, limit=ROLE_BATCH):
    """
    Ask what kind of account each of up to limit followed accounts not asked yet is, and store it. A failure is logged
    and the account asked again on a later pass. Returns how many were stored.
    """
    stored = 0
    for address in database.unchecked(conn, "hyperliquid", limit):
        try:
            database.set_role(conn, "hyperliquid", address, hyperliquid.role(address)["role"])
            stored += 1
        except Exception as error:
            log(with_traceback(f"asking the role of {address} failed", error))
    return stored


def start(conn, table, address, now):
    """
    Where an account's next read of a table starts, in milliseconds since 1970: at its latest row stored, or FIRST_DAYS
    before now, in seconds since 1970, when it has none.
    """
    last = database.last_time(conn, table, address)
    return last if last is not None else int((now - FIRST_DAYS * 86400) * 1000)


def store_rows(conn, rows, parse):
    """
    Store up to ROWS_PER_READ rows, read oldest first, each turned into its model by parse, CHUNK at a time as they come,
    and those read before a failure too, so a read that fails partway keeps all it read. Returns how many were new, and
    whether the rows ran out before ROWS_PER_READ.
    """
    stored = taken = 0
    chunk = []
    try:
        for row in itertools.islice(rows, ROWS_PER_READ):
            chunk.append(parse(row))
            taken += 1
            if len(chunk) == CHUNK:
                stored += database.store(conn, chunk)
                chunk = []
    finally:
        stored += database.store(conn, chunk)       # The last chunk, short, or the one a failure cut short.
    return stored, taken < ROWS_PER_READ


def read_fills(conn, address, now):
    """
    Store an account's fills from where its last read stopped, see start(). Returns how many were new, and whether
    the read reached the latest.
    """
    rows = hyperliquid.fills(address, start(conn, "hl_fills", address, now))
    return store_rows(conn, rows, lambda f: hyperliquid.parse_fill(address, f))


def read_positions(conn, address, now):
    """
    Store an account's value and open positions on Hyperliquid's own dex now. Returns how many rows were new, and True,
    since one call reads them all.
    """
    account, positions = hyperliquid.parse_account(address, hyperliquid.positions(address))
    return database.store(conn, [account]) + database.store(conn, positions), True


def read_funding(conn, address, now):
    """
    Store an account's funding payments from where its last read stopped, as read_fills() does.
    """
    rows = hyperliquid.funding_payments(address, start(conn, "hl_funding", address, now))
    return store_rows(conn, rows, lambda row: hyperliquid.parse_funding(address, row))


def read_ledger(conn, address, now):
    """
    Store an account's deposits, withdrawals, and transfers from where its last read stopped, as read_fills() does.
    """
    rows = hyperliquid.ledger(address, start(conn, "hl_ledger", address, now))
    return store_rows(conn, rows, lambda row: hyperliquid.parse_ledger(address, row))


READERS = {"fills": read_fills, "positions": read_positions, "funding": read_funding, "ledger": read_ledger}


def read_due(conn, now, log, limit=BATCH):
    """
    For each kind in READERS, read up to limit followed accounts due for it at now, in seconds since 1970, those never
    read first. Each read that reached the latest, or failed, is noted as done now. One stopped at ROWS_PER_READ is
    noted as done in 1970, so it is due again at once, but after the accounts never read. Returns {kind: (accounts read,
    rows stored)}.
    """
    done = {}
    for kind, read in READERS.items():
        addresses = database.due(conn, "hyperliquid", kind, now - EVERY[kind], limit)
        stored = 0
        for address in addresses:
            try:
                new, finished = read(conn, address, now)
                stored += new
            except Exception as error:
                log(with_traceback(f"reading the {kind} of {address} failed", error))
                finished = True
            database.synced(conn, "hyperliquid", kind, address, time.time() if finished else 0)
        done[kind] = (len(addresses), stored)
    return done
