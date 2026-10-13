"""
Record what the research needs kept as it happens: the venues' leaderboards once a UTC day, and the followed Hyperliquid
accounts' fills, positions, funding, and ledger, each on its own schedule.

Each pass records the standings of any venue not recorded yet today, see record/standings.py, asks the kind of account
of followed Hyperliquid accounts not asked yet, and reads the accounts due, a batch of each kind of read, see
record/accounts.py, so no kind waits long on the others. Hyperliquid's rate limiter sets the pace, and with nothing due
the process sleeps IDLE_SECONDS. A venue whose standings fail is tried again after RETRY_SECONDS. A status line comes
every STATUS_SECONDS.

What has been read is in the database, so stopping, or this machine sleeping, loses nothing but time, as long as it is
back before the fills Hyperliquid keeps of a followed account have moved past the last one stored: on starting again it
reads whatever came due meanwhile. A day whose standings were missed is lost. It only reads, nothing trades. Ctrl-C or
kill stops it, and each write is a transaction of its own, so nothing is left half written. The first line it logs says
the commit it runs.

Run from src/ with:
    python3 -m copytrader.record.run
    python3 -m copytrader.record.run --seconds 600 --db /tmp/check.sqlite

--seconds is checked between passes, so the last pass may run past it, by minutes while the first reads of newly followed
accounts' whole histories are under way.
"""

import argparse
import signal
import subprocess
import time
from collections import Counter
from copytrader.common.log import log, with_traceback
from copytrader.common.paths import ROOT
from copytrader.db import database
from copytrader.record import accounts, standings

IDLE_SECONDS = 30
RETRY_SECONDS = 600
STATUS_SECONDS = 600
RECORDERS = {"polymarket": standings.record_polymarket, "hyperliquid": standings.record_hyperliquid}


def record_standings(conn, now, tried, log):
    """
    Record the standings of each venue not recorded yet today, unless it was tried within RETRY_SECONDS, which tried
    holds by venue. Returns whether any was tried.
    """
    busy = False
    for venue, record_venue in RECORDERS.items():
        if database.last_day(conn, venue) == standings.today(now) or now - tried.get(venue, 0) < RETRY_SECONDS:
            continue
        tried[venue] = now
        busy = True
        try:
            stored, followed = record_venue(conn, now)
            log(f"{venue}: {stored:,} standings recorded for {standings.today(now)}, {followed:,} traders newly followed")
        except Exception as error:
            log(with_traceback(f"recording {venue}'s standings failed, trying again in {RETRY_SECONDS}s", error))
    return busy


def status(conn, now, totals):
    """
    A line saying what has been stored since the start, and how many followed accounts are due for each kind of read.
    """
    stored = ", ".join(f"{n:,} {what}" for what, n in totals.items()) or "nothing"
    waiting = ", ".join(f"{len(database.due(conn, 'hyperliquid', kind, now - every)):,} for {kind}" for kind, every in accounts.EVERY.items())
    return f"since the start: {stored}. accounts due: {waiting}"


def code_version():
    """
    The commit the process runs, with '-dirty' when files differ from it, or 'unknown' outside a git checkout, so the log
    says what code recorded what follows it.
    """
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def record(conn, seconds=None, log=log):
    """
    Record until seconds have passed, or for good without.
    """
    started = last_status = time.time()
    tried, totals = {}, Counter()
    log(f"recording, {len(database.traders(conn, 'hyperliquid')):,} Hyperliquid and {len(database.traders(conn, 'polymarket')):,} "
        f"Polymarket traders followed so far, with code {code_version()}")
    while seconds is None or time.time() - started < seconds:
        now = time.time()
        busy = record_standings(conn, now, tried, log)
        roles = accounts.check_roles(conn, log)
        totals["roles stored"] += roles
        busy = busy or roles > 0
        for kind, (read, stored) in accounts.read_due(conn, now, log).items():
            totals[f"{kind} rows"] += stored
            busy = busy or read > 0
        if now - last_status >= STATUS_SECONDS:
            log(status(conn, now, totals))
            last_status = now
        if not busy:
            left = None if seconds is None else seconds - (time.time() - started)
            time.sleep(IDLE_SECONDS if left is None else max(0, min(IDLE_SECONDS, left)))
    log(status(conn, time.time(), totals))


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Record the venues' leaderboards and the followed Hyperliquid accounts into SQLite.")
    ap.add_argument("--seconds", type=float, help="stop after this long, by default run until stopped")
    ap.add_argument("--db", help="the database to record into, by default data/copytrader.sqlite, another for a trial run")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, signal.default_int_handler)      # A kill, as from systemd, stops it as Ctrl-C does.
    try:
        record(database.connect(args.db), args.seconds)
    except KeyboardInterrupt:
        log("stopped")
