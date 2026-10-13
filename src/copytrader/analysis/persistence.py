"""
Test whether the traders at the top stay there: rank the candidate traders by their PnL over one stretch of time, and see
how they did over the next.

The PnL comes from pnl_history, see history/pnl.py: each trader's PnL since they began, as their venue charts it. Time is
cut into PERIOD_DAYS periods back from the latest point stored, at most PERIODS of them, and a trader's PnL over a period
is the change in their PnL across it, read off their chart by straight lines between its points, and flat after its last.
A trader is ranked in a period only if their chart had begun by its start and their PnL moved in it, so one who had
stopped is not ranked, but one who stops after being ranked counts, at nothing, in the next.

For each period and the one after, the report gives how many traders were ranked, the rank correlation of their PnL in
the two, and, for the top TOP_SHARE by the first period's PnL, the top TOP_N, everyone ranked, and the bottom TOP_SHARE:
the share that made money in the next period and their median PnL in it. If the top's next period is no better than
everyone's, being at the top says nothing about what comes next, and copying the top would be copying luck.

A chart's straight line between two points can cross the cut between two periods, and then it puts some of the same
PnL in both, which makes them look alike. Polymarket's points are a day apart, too close to matter, but Hyperliquid's are
about two weeks apart, so there the period ranked should be compared with one GAP periods later, with --gap 1, so no
line reaches from one to the other.

On Hyperliquid, whose chart gives the account's value, the traders are ranked a second time by return, their PnL over
their account's value at the period's start, counting only accounts worth MIN_VALUE then, so a big account's dollars do
not crowd out a small account's skill. The next period's return is over the same starting value.

The candidates come from the leaderboards of the day they were read, so a trader who had stopped by then, and fell off
every board, is missing. On Hyperliquid, whose leaderboard keeps every account with enough volume, active or not, that
is few. On Polymarket, whose boards only rank the top, it is more, and a trader on a board of all time PnL is there for
doing well over the very periods compared, which makes winners look like they kept winning. --volume-boards ranks only
the traders on a board by volume, chosen for how much they trade rather than how they did.

Run from src/ with:
    python3 -m copytrader.analysis.persistence --venue polymarket
    python3 -m copytrader.analysis.persistence --venue polymarket --volume-boards
    python3 -m copytrader.analysis.persistence --venue hyperliquid --gap 1
"""

import argparse
import numpy as np
from copytrader.db import database
from datetime import UTC, datetime
from scipy import stats

PERIOD_DAYS = 28
PERIODS = 13                # At most, so a year of pairs.
TOP_SHARE = 0.1
TOP_N = 50
MIN_VALUE = 10_000          # Dollars an account must be worth at a period's start to be ranked by return.
DAY = 86400


def bounds(histories, days=PERIOD_DAYS, periods=PERIODS):
    """
    The times, in seconds since 1970, that cut the periods: back from the latest point of any history, days apart, as
    many as there are periods with some trader's chart begun by their start, up to periods.
    """
    end = max(points[-1][0] for points in histories.values())
    first = min(points[0][0] for points in histories.values())
    count = min(periods, int((end - first) // (days * DAY)))
    return np.array([end - k * days * DAY for k in range(count, -1, -1)], dtype=float)


def period_pnl(points, cuts):
    """
    A trader's PnL over each period between consecutive cuts, and their account value at each period's start, as two
    lists, None for a period that starts before their chart, or a value their chart does not give.
    """
    times = np.array([p[0] for p in points], dtype=float)
    pnl = np.interp(cuts, times, [p[1] for p in points])
    value = np.interp(cuts, times, [p[2] if p[2] is not None else np.nan for p in points])
    begun = cuts >= times[0]
    gains = [float(pnl[k + 1] - pnl[k]) if begun[k] else None for k in range(len(cuts) - 1)]
    starts = [float(value[k]) if begun[k] and not np.isnan(value[k]) else None for k in range(len(cuts) - 1)]
    return gains, starts


def group(after, members):
    """
    How a group did in the next period, given everyone's PnL in it, after, and the group's places in it: its size, the
    share that made money, and its median and mean PnL.
    """
    picked = after[members]
    return {"n": len(picked), "positive": float(np.mean(picked > 0)), "median": float(np.median(picked)), "mean": float(np.mean(picked))}


def compare(before, after):
    """
    How the traders ranked by before, their PnL or return in one period, did in the next, after, in the same order: their
    number, the rank correlation of the two, and the next period of the top TOP_SHARE, the top TOP_N, all, and the bottom
    TOP_SHARE, see group().
    """
    before, after = np.array(before), np.array(after)
    order = np.argsort(-before, kind="stable")
    share = max(1, int(len(before) * TOP_SHARE))
    return {"n": len(before), "rho": float(stats.spearmanr(before, after).statistic), "top": group(after, order[:share]),
            "top_n": group(after, order[:TOP_N]), "all": group(after, order), "bottom": group(after, order[-share:])}


def pairs(histories, cuts, by="pnl", gap=0):
    """
    For each period with one gap periods after it, the period's start and compare() of the traders ranked in it, by
    "pnl", or by "roi", return on their account's value, and how they did in the period gap after the next.
    """
    periods = {address: period_pnl(points, cuts) for address, points in histories.items()}
    found = []
    for k in range(len(cuts) - 2 - gap):
        later = k + 1 + gap
        before, after = [], []
        for gains, starts in periods.values():
            if gains[k] is None or gains[k] == 0:
                continue
            if by == "pnl":
                before.append(gains[k])
                after.append(gains[later])
            elif starts[k] is not None and starts[k] >= MIN_VALUE:
                before.append(gains[k] / starts[k])
                after.append(gains[later] / starts[k])
        if len(before) >= 2 * TOP_N:
            found.append((cuts[k], compare(before, after)))
    return found


def report(found, by, gap=0):
    """
    The comparisons of pairs() as lines of a table, one a period, and last lines of their averages.
    """
    money = "{:+,.0f}" if by == "pnl" else "{:+.1%}"
    later = "the next period" if not gap else f"the period {gap} after the next"
    lines = [f"ranked by {by}: each period's traders, and how they did {later}: share that made money, and median {by}",
             f"{'period from':<12}{'ranked':>8}{'rho':>7}  {'top 10%':>20}{'top ' + str(TOP_N):>20}{'everyone':>20}{'bottom 10%':>20}"]
    for start, c in found:
        cells = "".join(f"{g['positive']:>8.0%} {money.format(g['median']):>11}" for g in (c["top"], c["top_n"], c["all"], c["bottom"]))
        lines.append(f"{datetime.fromtimestamp(start, UTC):%Y-%m-%d}  {c['n']:>8,}{c['rho']:>7.2f}  {cells}")
    if found:
        mean = {key: np.mean([c[key]["positive"] for _, c in found]) for key in ("top", "top_n", "all", "bottom")}
        rho = np.mean([c["rho"] for _, c in found])
        beat = sum(c["top"]["positive"] > c["all"]["positive"] for _, c in found)
        lines.append(f"{'average':<12}{'':>8}{rho:>7.2f}  " + "".join(f"{mean[key]:>8.0%} {'':>11}" for key in ("top", "top_n", "all", "bottom")))
        lines.append(f"the top 10% made money more often than everyone in {beat} of {len(found)} periods")
    return lines


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Test whether the traders at the top of one period do well in the next.")
    ap.add_argument("--venue", choices=["polymarket", "hyperliquid"], required=True)
    ap.add_argument("--days", type=int, default=PERIOD_DAYS, help=f"days a period, {PERIOD_DAYS} by default")
    ap.add_argument("--gap", type=int, default=0, help="periods skipped between the one ranked and the one compared, 0 by default")
    ap.add_argument("--volume-boards", action="store_true", help="rank only the traders on a board by volume on the latest day")
    args = ap.parse_args()
    conn = database.connect()
    histories = database.pnl_histories(conn, args.venue)
    if args.volume_boards:
        chosen = set(database.standing_addresses(conn, args.venue, database.last_day(conn, args.venue), "%/VOL"))
        histories = {address: points for address, points in histories.items() if address in chosen}
    cuts = bounds(histories, args.days)
    print(f"{args.venue}: {len(histories):,} traders with a PnL history, {len(cuts) - 1} periods of {args.days} days, gap {args.gap}")
    for by in ("pnl", "roi") if args.venue == "hyperliquid" else ("pnl",):
        print()
        print("\n".join(report(pairs(histories, cuts, by, args.gap), by, args.gap)))
