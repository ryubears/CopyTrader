"""
Measure how Polymarket US's prices trail polymarket.com's, from both venues' books recorded side by side, see
record/lag.py, and whether the cheap prices that trail could be bought and would have paid.

For each pair, the moments either venue's book was read are lined up, each with the latest book of the other, unless it
is older than MAX_AGE, a little more than the recorder's heartbeat, so a stretch the recorder missed counts as unknown.
A pair's outcome is the one Polymarket US's long side holds, so its polymarket.com book and Polymarket US's are of the
same outcome, and the other side's book is the same book turned over: its ask one less the outcome's bid.

The report gives, by market type, or by league and market type with --by-league, and by whether the game was under way:

    lag        how long after polymarket.com's mid moves JUMP or more within JUMP_SECONDS Polymarket US's mid has moved
               half as far the same way, at most FOLLOW_SECONDS, and the same the other way round, to see which leads.
               A mid counts only while its book's spread is MAX_SPREAD or less, as a wider one moves when a quote is
               pulled for a moment, without the price moving.
    cheap      the stretches when buying a side on Polymarket US cost less, its taker fee included, than polymarket.com's
               best bid for it: as if Polymarket US's ask had not caught up with polymarket.com. For each, how long it
               lasted, how many cents it was cheaper by at most, how many dollars Polymarket US's asks held at prices that
               cheap, of its best levels recorded, and, once the market resolved, what a contract bought at its start made.
    history    how often Polymarket US's minute price history, which analysis/copy.py priced copies by, agreed within
               AGREE with the best ask and bid recorded at the same moment, and how often it showed a price more than
               CHEAPER below the recorded ask, a fill a copy could not have had.

Books recorded before Polymarket US's came from its market feed were up to 30 seconds old, see record/lag.py, so
--since leaves them out.

Run from src/ with:
    python3 -m copytrader.analysis.lag
    python3 -m copytrader.analysis.lag --since 2026-10-13T17:00 --by-league
"""

import argparse
import itertools
import json
import numpy as np
import statistics
from collections import defaultdict
from copytrader.analysis.copy import league, payout
from copytrader.api import polymarket_us
from copytrader.common.timeutil import epoch
from copytrader.db import database
from dataclasses import dataclass, field
from datetime import UTC, datetime

MAX_AGE = 75                    # Seconds a book read stays the venue's book, a little more than the recorder's heartbeat.
JUMP = 0.03                     # A move of polymarket.com's mid, or Polymarket US's, worth following.
MAX_SPREAD = 0.05               # The widest spread a book's mid counts at.
JUMP_SECONDS = 10               # Within how long it has to move that far.
FOLLOW_SECONDS = 600            # How long the other venue is watched to follow.
AGREE = 0.005                   # How near the price history and the books must be to agree.
CHEAPER = 0.02                  # How far below the recorded ask the price history must be to show a fill there was not.


def levels(row):
    """
    A LagBook's bids and asks, each a list of (price, contracts), best first.
    """
    return [tuple(level) for level in json.loads(row.bids)], [tuple(level) for level in json.loads(row.asks)]


def timeline(com_rows, us_rows, max_age=MAX_AGE):
    """
    Each moment either venue's book was read, oldest first, as (time, polymarket.com's levels(), Polymarket US's
    levels()), each the latest read at or before it, None when older than max_age.
    """
    found, com, us = [], None, None
    for t, venue, row in sorted([(r.time, 0, r) for r in com_rows] + [(r.time, 1, r) for r in us_rows], key=lambda e: e[:2]):
        if venue == 0:
            com = (row.time, levels(row))
        else:
            us = (row.time, levels(row))
        found.append((t, com[1] if com and t - com[0] <= max_age else None, us[1] if us and t - us[0] <= max_age else None))
    return found


def mid(book):
    """
    The midpoint of a book's best bid and ask, or None without both, or when they are more than MAX_SPREAD apart.
    """
    if not book or not book[0] or not book[1] or book[1][0][0] - book[0][0][0] > MAX_SPREAD + 1e-9:
        return None
    return (book[0][0][0] + book[1][0][0]) / 2


def side_of(book, side):
    """
    What buying a side costs and what selling it gets in a book of the long side, as (asks, bids), each a list of (price,
    contracts), best first: the long side's own, or for the short side, the long side's turned over.
    """
    bids, asks = book
    if side == "long":
        return asks, bids
    return [(1 - price, contracts) for price, contracts in bids], [(1 - price, contracts) for price, contracts in asks]


def follows(line, leader, size=JUMP, within=JUMP_SECONDS, follow=FOLLOW_SECONDS):
    """
    For each time the leader's mid, 1 for polymarket.com and 2 for Polymarket US in the timeline's entries, had moved size
    or more since within seconds before, how many seconds later the other's mid had moved half as far the same way from
    where it was then, or None if not within follow, as a list of (time, seconds). A book holds until the next read, so
    where each mid was within seconds before is the latest read at or before then.
    """
    points = [(entry[0], mid(entry[leader]), mid(entry[3 - leader])) for entry in line]
    times = np.array([point[0] for point in points])
    found, quiet_until = [], -np.inf
    for i, (t, lead, _) in enumerate(points):
        if lead is None or t < quiet_until:
            continue
        since = max(0, int(np.searchsorted(times, t - within, side="right")) - 1)
        was = next((point[1] for point in points[since:i] if point[1] is not None), None)
        start = next((point[2] for point in points[since:i + 1] if point[2] is not None), None)
        if was is None or start is None or abs(lead - was) < size:
            continue
        direction, quiet_until = np.sign(lead - was), t + within
        caught = next((p_t - t for p_t, _, p_other in points[i:] if p_t - t <= follow and p_other is not None
                       and direction * (p_other - start) >= abs(lead - was) / 2), None)
        found.append((t, caught))
    return found


def cheap_stretches(line, coefficient):
    """
    The stretches of the timeline when buying a side on Polymarket US cost less, fee included, than polymarket.com's best
    bid for it, as dicts of their side, start, end, the most cents it was cheaper by, the US ask at the start, and the
    dollars Polymarket US's asks held at prices that cheap at the start.
    """
    rate = coefficient or polymarket_us.TAKER_FEE
    found, open_ = [], {}
    for t, com, us in line:
        for side in ("long", "short"):
            cheaper = None
            if com and us:
                asks, _ = side_of(us, side)
                _, their_bids = side_of(com, side)
                limit = their_bids[0][0] if their_bids else 0.0
                cheap = [(price, contracts) for price, contracts in asks if price + rate * price * (1 - price) < limit]
                if cheap:
                    ask = cheap[0][0]
                    cheaper = ((limit - ask - rate * ask * (1 - ask)) * 100, ask, sum(price * contracts for price, contracts in cheap))
            if cheaper and side not in open_:
                open_[side] = {"side": side, "start": t, "end": t, "cents": cheaper[0], "ask": cheaper[1], "dollars": cheaper[2]}
            elif cheaper:
                open_[side]["end"], open_[side]["cents"] = t, max(open_[side]["cents"], cheaper[0])
            elif side in open_:
                stretch = open_.pop(side)
                stretch["end"] = t
                found.append(stretch)
    return found + list(open_.values())


def history_check(prices, us_rows, max_age=MAX_AGE):
    """
    How Polymarket US's minute price history compares with the books recorded: of the points with a recorded book at the
    time, how many agreed within AGREE on both sides, and how many showed the long or short price more than CHEAPER below
    the recorded ask, as (points, agreed, cheaper).
    """
    times = np.array([r.time for r in us_rows])
    points = agreed = cheaper = 0
    for p in prices:
        i = int(np.searchsorted(times, p.time, side="right")) - 1
        if i < 0 or p.time - times[i] > max_age or p.long_price is None or p.short_price is None:
            continue
        bids, asks = levels(us_rows[i])
        if not bids or not asks:
            continue
        points += 1
        agreed += abs(p.long_price - asks[0][0]) <= AGREE and abs(p.short_price - (1 - bids[0][0])) <= AGREE
        cheaper += p.long_price < asks[0][0] - CHEAPER or p.short_price < (1 - bids[0][0]) - CHEAPER
    return points, agreed, cheaper


def paid(stretch, pair, market):
    """
    What a contract bought on Polymarket US at the start of a cheap stretch made by resolution, after the fee, or None if
    the market has not resolved.
    """
    if market is None:
        return None
    won = payout(market, json.loads(market.token_ids).index(pair.token_id)) if pair.token_id in json.loads(market.token_ids) else None
    if won is None:
        return None
    ask = stretch["ask"]
    return (won if stretch["side"] == "long" else 1 - won) - ask - (pair.fee_coefficient or polymarket_us.TAKER_FEE) * ask * (1 - ask)


def percentiles(values, at=(25, 50, 75), digits=0):
    """
    The percentiles of values, as text, to digits after the point, or '-' when there are none.
    """
    return "/".join(f"{np.percentile(values, q):.{digits}f}" for q in at) if values else "-"


@dataclass
class Findings:
    """
    What the pairs of one market type, before or after their games started, showed, see study().
    """
    pairs: int = 0
    seconds: float = 0.0                                    # With both books known.
    com_lags: list = field(default_factory=list)            # follows() of polymarket.com's moves, the seconds alone.
    us_lags: list = field(default_factory=list)             # Of Polymarket US's.
    cheap: list = field(default_factory=list)               # cheap_stretches().
    made: list = field(default_factory=list)                # paid() of each, in the same order.
    history: tuple = (0, 0, 0)                              # history_check(), summed.


def study(pair, com_rows, us_rows, prices, market, found, group=None):
    """
    Add what one pair showed, from its books on either venue, its minute price history, and its polymarket.com market, to
    found, {(group, under way): Findings}, each finding by whether it came after its game's start. The group is the
    pair's market type unless given.
    """
    group = group or pair.market_type
    start = epoch(pair.game_start) or 0
    line = timeline(com_rows, us_rows)
    playing = {False: [entry for entry in line if entry[0] < start], True: [entry for entry in line if entry[0] >= start]}
    for part in (False, True):
        if len(playing[part]) >= 2:
            found[(group, part)].pairs += 1
    for a, b in itertools.pairwise(line):
        if a[1] and a[2]:
            found[(group, a[0] >= start)].seconds += b[0] - a[0]
    for leader in (1, 2):
        for t, seconds in follows(line, leader):
            f = found[(group, t >= start)]
            (f.com_lags if leader == 1 else f.us_lags).append(seconds)
    for stretch in cheap_stretches(line, pair.fee_coefficient):
        f = found[(group, stretch["start"] >= start)]
        f.cheap.append(stretch)
        f.made.append(paid(stretch, pair, market))
    for part in (False, True):
        f = found[(group, part)]
        checked = history_check([p for p in prices if (p.time >= start) == part], us_rows)
        f.history = tuple(a + b for a, b in zip(f.history, checked, strict=True))


def report(found):
    """
    Lines of the report from the Findings, {(group, under way): Findings}.
    """
    lines = []
    for (group, playing), f in sorted(found.items()):
        if not f.pairs:
            continue
        name = f"{group}, {'under way' if playing else 'before the start'}"
        hours = f.seconds / 3600
        lines.append(f"{name}: {f.pairs} markets, {hours:,.1f} hours of both books")
        for leader, label in (("com", "polymarket.com moved first"), ("us", "Polymarket US moved first")):
            lags = f.com_lags if leader == "com" else f.us_lags
            caught = [s for s in lags if s is not None]
            other = "Polymarket US" if leader == "com" else "polymarket.com"
            lines.append(f"    {label}: {len(lags):,} moves of {JUMP * 100:.0f} cents or more, {other} followed half way in "
                         f"{percentiles(caught)} seconds (quartiles), {len(lags) - len(caught):,} not within {FOLLOW_SECONDS // 60} minutes")
        stretches = f.cheap
        if stretches:
            made = [m for m in f.made if m is not None]
            lines.append(f"    cheaper on Polymarket US than polymarket.com's bid, fee included: {len(stretches):,} stretches, "
                         f"{len(stretches) / hours:.1f} an hour, lasting {percentiles([s['end'] - s['start'] for s in stretches])} seconds, "
                         f"by up to {percentiles([s['cents'] for s in stretches], digits=1)} cents, "
                         f"with ${percentiles([s['dollars'] for s in stretches])} to buy that cheap")
            lines.append(f"        a contract bought at each start: {len(made):,} resolved, "
                         + (f"made {statistics.mean(made) * 100:+.1f} cents on average, {sum(m > 0 for m in made) / len(made):.0%} of them won"
                            if made else "none yet"))
        else:
            lines.append("    never cheaper on Polymarket US than polymarket.com's bid, fee included")
        points, agreed, cheaper = f.history
        if points:
            lines.append(f"    minute price history: {points:,} points beside a recorded book, {agreed / points:.0%} agreed within "
                         f"{AGREE * 100:.1f} cents, {cheaper / points:.1%} showed a price {CHEAPER * 100:.0f} cents or more below the ask")
    return lines


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Measure how Polymarket US's prices trail polymarket.com's, from both venues' books recorded.")
    ap.add_argument("--since", help="study only what was recorded from this UTC time on, such as 2026-10-13T17:00")
    ap.add_argument("--by-league", action="store_true", help="report each league's markets of each type apart")
    args = ap.parse_args()
    since = epoch(f"{args.since}Z") if args.since else 0.0
    if since is None:
        ap.error(f"--since {args.since} is not a time like 2026-10-13T17:00")
    conn = database.connect()
    pairs = database.lag_pairs(conn)
    markets = database.markets(conn)
    found, studied = defaultdict(Findings), 0
    for pair in pairs.values():
        com_rows, us_rows = ([r for r in database.lag_books(conn, venue, key) if r.time >= since]
                             for venue, key in (("polymarket", pair.token_id), ("polymarket_us", pair.slug)))
        if com_rows and us_rows:
            prices = [p for p in database.us_prices(conn, pair.slug) if p.time >= since]
            group = f"{league(pair.slug)} {pair.market_type}" if args.by_league else None
            study(pair, com_rows, us_rows, prices, markets.get(pair.condition_id), found, group)
            studied += 1
    first = since or min((p.first_seen for p in pairs.values()), default=0)
    print(f"{studied:,} markets recorded since {datetime.fromtimestamp(first, UTC):%Y-%m-%d %H:%M} UTC")
    print("\n".join(report(found)))
