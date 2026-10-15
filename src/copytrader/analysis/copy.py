"""
Simulate copying polymarket.com traders on Polymarket US over the last PERIODS periods, to see whether a copy would
have made money after its wait, Polymarket US's prices, and its fees.

Who is copied. Time is cut into PERIOD_DAYS periods back from the latest PnL point, as in analysis/persistence.py. At
each period's start the traders of the pool are ranked by the Sharpe ratio of their daily PnL over the LOOKBACK_DAYS
before it, see sharpe(), which picked traders who made money in the next period far more often than ranking them by
their PnL did. Of the top CANDIDATES, those too fast to copy, see traders.BOT_TRADES_PER_DAY, and those without a trade in
a market that matches one of Polymarket US's over the lookback are dropped, and the top COPIED of the rest are copied
through the period. The pool is the traders on a volume board on the latest day, chosen for how much they trade rather
than how they did. --pool followed copies the followed traders instead, chosen for doing well over the very periods
simulated, to show how much that flatters.

What a copy does. Each trader copied gets an equal share of the capital, their sleeve, in each period they are copied,
an account of its own: what its buys cost comes out of it and what they pay goes back, so a trader who loses loses their
sleeve and no more, and the others' are untouched. When they buy an outcome of a polymarket.com market that matches a
Polymarket US market, see analysis/match.py, the copy buys the side of that market holding the outcome a wait later, at
Polymarket US's price then, the side's best ask, from its price history, see history/polymarket_us.py, each point of it
taken as the book at the end of its minute, see api/polymarket_us.py, unless that is more than CHASE above what the
trader paid, or, with --floor, more than that below it. Polymarket US's minute prices are often well below
polymarket.com's during a game, too often to trust them as fills, which --floor leaves out. The copy
spends BUY_SHARE of the sleeve, more as the trade is bigger than the trader's median buy, up to SIZE_RANGE times, and
less as it is smaller, but no more than is left in the sleeve. When the trader sells, the copy sells as large a share of
its holding as they sold of theirs, at the side's best bid then, unless that is more than CHASE below what the trader
got, when it holds on, or, with --hold, it sells nothing. What it holds when a market resolves pays a dollar a contract
if its outcome won. Each order pays Polymarket US's taker fee, the market's fee coefficient times the contracts times
p(1 - p), rounded to the cent.

A copy is skipped when Polymarket US has no usable() price within STALE seconds after the wait, or the sleeve is empty.
Polymarket US keeps no history of its books' depth, so it is taken from snapshots of its books, see
history/polymarket_us.py --books, see depth(): an order pays, a contract beyond the best price, the median of what an
order of its size would have paid walking those books, for the market's league and type, or its type where the league's
snapshots are few, and an order bigger than the median book fills is cut to what it fills. Without snapshots, every
order fills at the best price.

Beside each copy runs its ideal: the same dollars at the trader's own prices and times, without fees, the most a copy
could make. What the copy makes less than its ideal is what the wait, Polymarket US's prices, and the fees cost. What
is still held at the end counts at its cost, for both.

Run from src/ with:
    python3 -m copytrader.analysis.copy
    python3 -m copytrader.analysis.copy --floor 0.03
    python3 -m copytrader.analysis.copy --floor 0.03 --hold
    python3 -m copytrader.analysis.copy --pool followed
"""

import argparse
import heapq
import itertools
import json
import numpy as np
import statistics
from collections import Counter, defaultdict
from copytrader.analysis import match, persistence, traders
from copytrader.api import polymarket_us
from copytrader.common.timeutil import DAY, epoch
from copytrader.db import database
from dataclasses import dataclass, field
from datetime import UTC, datetime

PERIOD_DAYS = 28
PERIODS = 6                     # As far back as the followed traders' activity goes, 180 days.
LOOKBACK_DAYS = 84              # The days of PnL a trader is ranked on.
CANDIDATES = 100                # The top traders by Sharpe ratio each period whose activity is read.
COPIED = 25                     # The traders copied each period.
DELAYS = (60, 300, 900, 3600)   # Seconds from a trader's trade to its copy.
CAPITALS = (10_000, 100_000)    # Dollars.
BUY_SHARE = 0.02                # The share of a sleeve a copied buy of the trader's median size spends.
SIZE_RANGE = 2                  # How many times bigger or smaller than that a buy can be, as the trader's buy is.
STALE = 300                     # Seconds after the wait within which Polymarket US's price must have been seen.
MIN_CONTRACTS = 1               # Polymarket US's smallest order.
SIZES = (10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000)     # Dollars an order spends, for the depth model.
MIN_SIDES = 20                  # Sides of book snapshots a league's market type needs to be priced on its own, not by its type's.
CHASE = 0.05                    # The most a copy pays a contract above the trader's price, or sells below it, in dollars.


# WHO IS COPIED

@dataclass
class Trader:
    """
    What the copy needs of a trader's activity: how fast they trade, the size of their median buy, and their rows in
    matched markets, oldest first.
    """
    address: str
    pace: float                 # Trades a day on the days they traded.
    median_buy: float           # Dollars.
    rows: list = field(default_factory=list)


def summary(address, rows, matches):
    """
    A Trader from a trader's activity, oldest first, and the matches, {condition_id: Match}.
    """
    trades = [r for r in rows if r.type == "TRADE"]
    days = len({r.timestamp // DAY for r in trades})
    return Trader(
        address=address,
        pace=len(trades) / days if days else 0.0,
        median_buy=statistics.median([r.usdc_size for r in trades if r.side == "BUY"] or [1.0]),
        rows=[r for r in rows if r.condition_id in matches],
    )


def daily_pnl(points, start, stop):
    """
    A trader's PnL on each day from start to stop, in seconds since 1970, read off their chart, or None when their chart
    begins after start.
    """
    times = np.array([p[0] for p in points], dtype=float)
    if times[0] > start:
        return None
    return np.diff(np.interp(np.arange(start, stop + 1, DAY), times, [p[1] for p in points]))


def sharpe(points, start, days=LOOKBACK_DAYS):
    """
    The Sharpe ratio of a trader's daily PnL over the days before start, its mean over its standard deviation, or None
    when their chart begins later or never moves.
    """
    gains = daily_pnl(points, start - days * DAY, start)
    if gains is None or gains.std() == 0:
        return None
    return float(gains.mean() / gains.std())


def ranked(histories, pool, start, top=CANDIDATES):
    """
    The top traders of the pool by sharpe() at start, best first.
    """
    scores = {a: s for a in pool if a in histories and (s := sharpe(histories[a], start)) is not None}
    return sorted(scores, key=lambda a: -scores[a])[:top]


def copyable(trader, start, days=LOOKBACK_DAYS):
    """
    Whether a Trader can be copied from start: slower than traders.BOT_TRADES_PER_DAY, and with a trade in a matched
    market over the days before.
    """
    return (trader is not None and trader.pace < traders.BOT_TRADES_PER_DAY
            and any(r.type == "TRADE" and start - days * DAY <= r.timestamp < start for r in trader.rows))


def plan(histories, pool, cuts, summaries, copied=COPIED):
    """
    Who is copied in each period between consecutive cuts, as a list of (start, stop, [addresses]): the copyable() of
    the ranked() traders, best first, from their summaries, {address: Trader}.
    """
    found = []
    for start, stop in itertools.pairwise(cuts):
        chosen = [a for a in ranked(histories, pool, start) if copyable(summaries.get(a), start)][:copied]
        found.append((float(start), float(stop), chosen))
    return found


# PRICES

def prices_of(points):
    """
    A market's stored UsPrice points as arrays of the times their prices were the book's, PRICE_OFFSET after their
    stamps, see api/polymarket_us.py, their long prices, and their short prices, for price_after().
    """
    times = np.array([p.time + polymarket_us.PRICE_OFFSET for p in points], dtype=float)
    return times, [p.long_price for p in points], [p.short_price for p in points]


def usable(long, short):
    """
    Whether a market's long and short prices are a book's: each between 0 and 1, and not crossed. An empty or settled
    book shows 1 and 0, and now and then a point is crossed, its bid above its ask.
    """
    return long is not None and short is not None and 0 < long < 1 and 0 < short < 1 and long + short >= 1 - 1e-9


def price_after(prices, at, stale=STALE):
    """
    The first usable() long and short prices seen at or after at, in seconds since 1970, within stale seconds, else None.
    """
    times, longs, shorts = prices
    i = int(np.searchsorted(times, at))
    while i < len(times) and times[i] - at <= stale:
        if usable(longs[i], shorts[i]):
            return longs[i], shorts[i]
        i += 1
    return None


def ask(quote, side):
    """
    What a contract of a side costs, from the long and short prices: its own display price.
    """
    return quote[0] if side == "long" else quote[1]


def bid(quote, side):
    """
    What a contract of a side sells for, from the long and short prices: one less the other side's display price.
    """
    return 1 - (quote[1] if side == "long" else quote[0])


def fee(coefficient, contracts, price):
    """
    Polymarket US's taker fee for an order, rounded half to even to the cent.
    """
    return round((coefficient or polymarket_us.TAKER_FEE) * contracts * price * (1 - price), 2)


# DEPTH

def premium(levels, dollars):
    """
    What spending dollars on a side pays a contract beyond its best price, walking its levels, best first, as (price,
    contracts), or None when they run out first.
    """
    spent = bought = 0.0
    for price, contracts in levels:
        take = min(contracts, (dollars - spent) / price)
        spent, bought = spent + take * price, bought + take
        if spent >= dollars * (1 - 1e-9):
            return spent / bought - levels[0][0]
    return None


def league(slug):
    """
    The league of a Polymarket US market, from its slug after its prefix: 'nfl' of 'aec-nfl-car-gb-2025-11-02'.
    """
    parts = slug.split("-")
    return parts[1] if len(parts) > 1 else None


def kind_of(model, us):
    """
    The key of the depth() model a Polymarket US market is priced by: its league and type, if the model has them, else
    its type alone, or None when the market is not known.
    """
    if us is None:
        return None
    return (league(us.slug), us.market_type) if (league(us.slug), us.market_type) in model else (None, us.market_type)


def depth(books, us_markets):
    """
    For each league and market type with MIN_SIDES sides of snapshots or more, and for each market type, what an order
    spending each of SIZES pays a contract beyond the best price: the median over both sides of every snapshot of their
    books, from database.us_books(), as {(league, market_type): {dollars: premium}}, the league None for a type's, the
    premium None where most could not fill it.
    """
    found = defaultdict(lambda: defaultdict(list))
    for (slug, _), book in books.items():
        kind = us_markets[slug].market_type if slug in us_markets else None
        for levels in (book["offer"], [(1 - price, contracts) for price, contracts in book["bid"]]):
            for size in SIZES if levels else ():
                for key in ((league(slug), kind), (None, kind)):
                    found[key][size].append(premium(levels, size))
    model = {}
    for key, by_size in found.items():
        if key[0] is not None and len(by_size[SIZES[0]]) < MIN_SIDES:
            continue
        model[key] = {}
        for size, premiums in by_size.items():
            ordered = sorted(premiums, key=lambda p: np.inf if p is None else p)
            model[key][size] = ordered[len(ordered) // 2]
    return model


def fillable(model, kind):
    """
    The most dollars an order in a market of a kind, see kind_of(), spends by the depth() model: the largest of SIZES the
    median book filled, none if it filled none, or no limit without snapshots of the kind.
    """
    sizes = [size for size, p in (model.get(kind) or {}).items() if p is not None]
    return max(sizes, default=0.0) if model.get(kind) else np.inf


def slippage(model, kind, dollars):
    """
    What an order spending dollars, no more than fillable(), pays a contract beyond the best price in a market of a kind,
    see kind_of(), by the depth() model, between its sizes by straight lines, or nothing without snapshots of the kind.
    """
    by_size = {size: p for size, p in (model.get(kind) or {}).items() if p is not None}
    if not by_size:
        return 0.0
    sizes = sorted(by_size)
    return float(np.interp(dollars, sizes, [by_size[size] for size in sizes]))


# THE COPY

def payout(market, outcome):
    """
    What a contract of an outcome of a resolved market paid, or None when it has not resolved.
    """
    if market is None or market.resolution != "resolved" or not market.outcome_prices or outcome is None:
        return None
    paid = json.loads(market.outcome_prices)
    return float(paid[outcome]) if 0 <= outcome < len(paid) else None


def as_sale(row, outcome):
    """
    A merge's row as the sale of one outcome it is, at the price a contract of the merge got.
    """
    return type(row)(**{**row.__dict__, "type": "TRADE", "side": "SELL", "outcome_index": outcome,
                        "price": row.usdc_size / row.size if row.size else 0.0})


def signals(summaries, chosen, markets):
    """
    The trades of the copied traders a copy acts on, oldest first, as (time, address, Activity, fraction): their buys
    while they are copied, with fraction None, and their sales, with the share of their holding of the outcome each sold,
    for the copy to sell as much of its own. A merge sells a contract of each outcome. Only rows in matched markets are
    in the summaries.
    """
    found = []
    for address, trader in summaries.items():
        held = defaultdict(float)
        for r in trader.rows:
            key = (r.condition_id, r.outcome_index)
            if r.type == "TRADE" and r.side == "BUY":
                held[key] += r.size
                if any(start <= r.timestamp < stop for start, stop in chosen.get(address, [])):
                    found.append((r.timestamp, address, r, None))
            elif r.type == "TRADE" and r.side == "SELL":
                if held[key] > 0:
                    found.append((r.timestamp, address, r, min(1.0, r.size / held[key])))
                held[key] = max(0.0, held[key] - r.size)
            elif r.type == "MERGE" and (m := markets.get(r.condition_id)):
                for outcome in range(len(json.loads(m.outcomes))):
                    before = held[(r.condition_id, outcome)]
                    if before > 0:
                        found.append((r.timestamp, address, as_sale(r, outcome), min(1.0, r.size / before)))
                    held[(r.condition_id, outcome)] = max(0.0, before - r.size)
            elif r.type == "REDEEM":
                for k in [k for k in held if k[0] == r.condition_id]:
                    held[k] = 0.0
    return sorted(found, key=lambda s: (s[0], s[1]))


def sleeves(plan_found, capital):
    """
    Each copied trader's sleeve in each period, as {address: [(start, stop, dollars)]}: the capital shared equally.
    """
    found = defaultdict(list)
    for start, stop, addresses in plan_found:
        for a in addresses:
            found[a].append((start, stop, capital / len(addresses)))
    return found


def period_of(plan_found, at):
    """
    Which period of plan_found a time falls in, counting from 0, or the last for a time after them all.
    """
    return next((i for i, (start, stop, _) in enumerate(plan_found) if start <= at < stop), len(plan_found) - 1)


def simulate(plan_found, summaries, matches, markets, us_markets, prices, capital, delay, model=None, chase=CHASE, hold=False,
             floor=None):
    """
    Copy the traders of plan_found, from their summaries, {address: Trader}, with capital dollars, each trade delay
    seconds late, beside its ideal, see the top of this file. matches is {condition_id: Match}, markets {condition_id:
    Market}, us_markets {slug: UsMarket}, prices {slug: prices_of()}, model the books' depth(), none by default, and chase
    the most a copy pays above the trader's price, or sells below it, without limit when None, and floor the most it buys
    below the trader's price, without limit when None. With hold, the copy holds what it buys to resolution, selling
    nothing. Returns a Counter of what happened, in counts and dollars, with the PnL of what was bought in each period as
    "pnl 0", "pnl 1", and so on.
    """
    model = model or {}
    shares = sleeves(plan_found, capital)
    chosen = {a: [(start, stop) for start, stop, _ in s] for a, s in shares.items()}
    found = [f for f in signals(summaries, chosen, markets) if not hold or f[3] is None]
    queue = [(t + delay, 1, i, address, r, fraction) for i, (t, address, r, fraction) in enumerate(found)]
    closes = {}
    for condition_id in {r.condition_id for _, _, r, _ in found}:
        m = markets.get(condition_id)
        if payout(m, 0) is not None and (at := epoch(m.closed_time) or epoch(m.end_date)):
            closes[condition_id] = at
            queue.append((at, 0, -1, None, condition_id, None))     # A resolution comes before a trade of the same second.
    heapq.heapify(queue)
    out, sizes, above = Counter(), [], []
    cash = {}                                       # What is left in each sleeve, by (address, period).
    held = defaultdict(lambda: [0.0, 0.0])          # The copy's contracts and their cost, by (address, period, condition_id, outcome).
    ideal = defaultdict(lambda: [0.0, 0.0])         # The ideal's.
    while queue:
        at, kind, _, address, item, fraction = heapq.heappop(queue)
        if kind == 0:                               # A market resolved, and what is held of it pays out.
            m = markets[item]
            for book in (held, ideal):
                for key in [k for k in book if k[2] == item]:
                    contracts, cost = book.pop(key)
                    paid = contracts * payout(m, key[3])
                    out["pnl" if book is held else "ideal pnl"] += paid - cost
                    if book is held:
                        cash[key[:2]] += paid
                        out[f"pnl {key[1]}"] += paid - cost
            continue
        r = item
        found_match = matches[r.condition_id]
        side = found_match.sides[r.outcome_index] if r.outcome_index is not None and r.outcome_index < len(found_match.sides) else None
        quote = price_after(prices[found_match.slug], at) if found_match.slug in prices and side else None
        us = us_markets.get(found_match.slug)
        coefficient, kind = (us.fee_coefficient if us else None), kind_of(model, us)
        if fraction is None:                        # A buy.
            out["buys"] += 1
            if at >= closes.get(r.condition_id, float("inf")):
                out["buys after the market closed"] += 1
                continue
            period = period_of(plan_found, r.timestamp)
            sleeve = next(s for start, stop, s in shares[address] if start <= r.timestamp < stop)
            account, key = (address, period), (address, period, r.condition_id, r.outcome_index)
            cash.setdefault(account, sleeve)
            want = min(cash[account], sleeve * BUY_SHARE * min(SIZE_RANGE, max(1 / SIZE_RANGE, r.usdc_size / summaries[address].median_buy)))
            if want > fillable(model, kind):
                want = fillable(model, kind)
                out["buys cut to the book's depth"] += 1
            price = ask(quote, side) + slippage(model, kind, want) if quote else None
            if price is None or not 0 < price < 1:
                out["buys without a price" if quote is None else "buys with nothing to buy"] += 1
                continue
            if chase is not None and price > r.price + chase:
                out["buys priced too far above the trader"] += 1
                continue
            if floor is not None and price < r.price - floor:
                out["buys priced too far below the trader"] += 1
                continue
            contracts = want / (price * (1 + (coefficient or polymarket_us.TAKER_FEE) * (1 - price)))
            if contracts < MIN_CONTRACTS:
                out["buys without the cash" if want == cash[account] else "buys too small"] += 1
                continue
            paid = fee(coefficient, contracts, price)
            cash[account] -= contracts * price + paid
            held[key][0] += contracts
            held[key][1] += contracts * price + paid
            if r.price > 0:
                ideal[key][0] += contracts * price / r.price
                ideal[key][1] += contracts * price
            out["copied buys"] += 1
            out["spent"] += contracts * price
            out["fees"] += paid
            sizes.append(contracts * price)
            above.append(price - r.price)
            continue
        for key in [(address, period, r.condition_id, r.outcome_index) for period in range(len(plan_found))]:
            for book in (held, ideal):              # A sale, of the same share of what each sleeve holds.
                if key not in book:
                    continue
                if book is ideal:
                    price, paid = r.price, 0.0
                elif quote is None or not 0 < bid(quote, side) < 1:
                    out["sales without a price"] += 1
                    continue
                else:
                    price = bid(quote, side)
                    price = max(0.001, price - slippage(model, kind, min(book[key][0] * fraction * price, fillable(model, kind))))
                    if chase is not None and price < r.price - chase:
                        out["sales held, the bid too far below the trader"] += 1
                        continue
                    paid = fee(coefficient, book[key][0] * fraction, price)
                    out["copied sales"] += 1
                    out["fees"] += paid
                contracts, cost = book[key][0] * fraction, book[key][1] * fraction
                out["pnl" if book is held else "ideal pnl"] += contracts * price - paid - cost
                if book is held:
                    cash[key[:2]] += contracts * price - paid
                    out[f"pnl {key[1]}"] += contracts * price - paid - cost
                book[key][0] -= contracts
                book[key][1] -= cost
    out["still held, at cost"] = sum(cost for _, cost in held.values())
    out["median order"] = statistics.median(sizes) if sizes else 0.0
    out["largest order"] = max(sizes, default=0.0)
    out["median price above"] = statistics.median(above) if above else 0.0     # Than the trader paid, a contract.
    out["sleeves used"] = len(cash)
    return out


# REPORT

def pool_of(conn, name):
    """
    The traders a pool holds: "volume", those on a volume board on the latest day, or "followed".
    """
    if name == "followed":
        return {t.address for t in database.traders(conn, "polymarket")}
    return set(database.standing_addresses(conn, "polymarket", database.last_day(conn, "polymarket"), "%/VOL"))


def candidates(conn, name="volume"):
    """
    Every trader of a pool ranked() in any period, whose activity the copy needs, see history/polymarket.py.
    """
    histories = database.pnl_histories(conn, "polymarket")
    pool = pool_of(conn, name)
    cuts = persistence.bounds(histories, PERIOD_DAYS, PERIODS)
    return sorted({a for start in cuts[:-1] for a in ranked(histories, pool, start)})


def planned(conn, name, matches, markets, copied=COPIED):
    """
    The plan() of a pool, the summaries of its candidates whose activity has been read, {address: Trader}, and how many
    candidates it has.
    """
    histories = database.pnl_histories(conn, "polymarket")
    pool = pool_of(conn, name)
    cuts = persistence.bounds(histories, PERIOD_DAYS, PERIODS)
    wanted = {a for start in cuts[:-1] for a in ranked(histories, pool, start)}
    read = database.synced_since(conn, "polymarket", "activity", 0)
    summaries = {a: summary(a, database.activity_of(conn, a), matches) for a in wanted if a in read}
    return plan(histories, pool, cuts, summaries, copied), summaries, len(wanted)


def day(seconds):
    """
    The UTC date of a time in seconds since 1970.
    """
    return f"{datetime.fromtimestamp(seconds, UTC):%Y-%m-%d}"


def report(capital, delay, out, plan_found):
    """
    Lines saying what became of a copy with capital dollars, delay seconds late, of the traders of plan_found.
    """
    skipped = ", ".join(f"{out[k]:,} {k[5:]}" for k in sorted(out) if k.startswith("buys ") and out[k])
    return [f"${capital:,} copied {delay}s late: {out['buys']:,} buys, {out['copied buys']:,} copied"
            + (f", {skipped}" if skipped else "") + f", {out['copied sales']:,} sales copied",
            f"    spent ${out['spent']:,.0f}, median order ${out['median order']:,.2f}, largest ${out['largest order']:,.0f}, "
            f"paying a median {out['median price above'] * 100:+.1f} cents a contract above the trader",
            f"    fees ${out['fees']:,.0f}, pnl ${out['pnl']:+,.0f} ({out['pnl'] / capital:+.1%}) against the ideal's "
            f"${out['ideal pnl']:+,.0f}, ${out['still held, at cost']:,.0f} still held at cost",
            "    pnl of what was bought in each period: " + ", ".join(f"{out[f'pnl {i}']:+,.0f}" for i in range(len(plan_found)))
            + f", from {out['sleeves used']:,} sleeves used of {sum(len(chosen) for _, _, chosen in plan_found):,}"]


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Simulate copying polymarket.com traders on Polymarket US.")
    ap.add_argument("--pool", choices=["volume", "followed"], default="volume", help="the traders ranked, volume by default")
    ap.add_argument("--copied", type=int, default=COPIED, help=f"traders copied each period, {COPIED} by default")
    ap.add_argument("--chase", type=float, default=CHASE, help=f"the most paid above the trader's price, {CHASE} by default, -1 for no limit")
    ap.add_argument("--hold", action="store_true", help="hold what is bought to resolution rather than copy the traders' sales")
    ap.add_argument("--floor", type=float, default=-1, help="the most bought below the trader's price, no limit by default")
    args = ap.parse_args()
    chase = None if args.chase < 0 else args.chase
    conn = database.connect()
    matches = match.matches(conn)
    markets = database.markets(conn)
    found, summaries, wanted = planned(conn, args.pool, matches, markets, args.copied)
    us_markets = {m.slug: m for m in database.us_markets(conn)}
    books = database.us_books(conn)
    model = depth(books, us_markets)
    print(f"{args.pool} pool: {wanted:,} candidates over {len(found)} periods of {PERIOD_DAYS} days, "
          f"{wanted - len(summaries):,} without their activity read")
    for start, stop, chosen in found:
        print(f"    {day(start)} to {day(stop)}: {len(chosen)} copied")
    leagues = sum(key[0] is not None for key in model)
    print(f"depth from {len(books):,} book snapshots, by type here and for {leagues} leagues apart: cents a contract beyond the best price, "
          f"by dollars an order spends, and chasing at most {chase} a contract")
    for (key_league, kind), by_size in sorted(model.items(), key=lambda item: str(item[0][1])):
        if key_league is None:
            cells = (f"{'$' + format(size, ','):>9} {'-' if p is None else format(p * 100, '.1f'):>4}" for size, p in by_size.items())
            print(f"    {kind!s:<18}" + "".join(cells))
    copied = {a for _, _, chosen in found for a in chosen}
    slugs = {matches[r.condition_id].slug for a in copied for r in summaries[a].rows}
    prices = {slug: prices_of(database.us_prices(conn, slug)) for slug in slugs}
    print(f"{len(matches):,} polymarket.com markets matched, {len(slugs):,} of them traded by the copied traders, "
          f"{sum(len(p[0]) for p in prices.values()):,} Polymarket US prices of those")
    for capital in CAPITALS:
        for delay in DELAYS:
            print()
            out = simulate(found, summaries, matches, markets, us_markets, prices, capital, delay, model, chase, args.hold,
                           None if args.floor < 0 else args.floor)
            print("\n".join(report(capital, delay, out, found)))
