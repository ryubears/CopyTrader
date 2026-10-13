"""
Rebuild each followed trader's results from what they did, and tell apart the kinds of trader a copy cannot follow.

On Polymarket a trader's result in a market comes from their activity in it, see history/polymarket.py: the dollars they
paid and got, buying and selling, splitting and merging, and redeeming, plus what the shares they still held paid when
it resolved. Only resolved sports and politics markets count, and only those opened after the trader's first row read,
so none is missing its first trades. Rewards, yield, and rebates are counted apart, as income. On Hyperliquid an
account's result is its realized PnL on crypto perps, see is_crypto_perp(), less fees, from its fills, see
record/accounts.py, so a position still open counts only once it closes.

Each trader is put in the first kind that fits:

    bot          BOT_TRADES_PER_DAY trades a day or more on the days they trade, or a maker share of MAKER_SHARE or more
                 at half that pace: market making or high frequency trading, too fast for a copy to follow. The pace tells
                 even when the few weeks read of such a trader hold few markets that count.
    few          fewer than MIN_MARKETS resolved markets, or MIN_FILLS fills, too few to judge
    one-hit      ONE_HIT_SHARE or more of the money they made came from one market, or on Hyperliquid one coin
    favorites    FAVORITE_SHARE or more of the dollars they bought were at FAVORITE_PRICE or more, near certain outcomes,
                 which win often and lose seldom but big
    directional  the rest, the traders a copy might follow

The thresholds are rules of thumb, to be tuned against what the traders turn out to be.

Run from src/ with:
    python3 -m copytrader.analysis.traders --venue polymarket
    python3 -m copytrader.analysis.traders --venue hyperliquid --top 50
"""

import argparse
import json
import statistics
from collections import Counter, defaultdict
from copytrader.api.hyperliquid import is_crypto_perp
from copytrader.common.timeutil import DAY, epoch
from copytrader.db import database
from dataclasses import dataclass

CATEGORIES = ("sports", "politics")
INCOME = {"REWARD", "YIELD", "MAKER_REBATE", "TAKER_REBATE"}     # Activity that is income rather than a position.
MIN_MARKETS = 10
MIN_FILLS = 100
BOT_TRADES_PER_DAY = 100
MAKER_SHARE = 0.7
ONE_HIT_SHARE = 0.5
FAVORITE_PRICE = 0.9
FAVORITE_SHARE = 0.5
KINDS = ("directional", "favorites", "one-hit", "bot", "few")


@dataclass
class Result:
    """
    What a trader did and how it went, rebuilt as above, and the kind of trader it makes them.
    """
    venue: str
    address: str
    markets: int                    # Resolved markets counted on Polymarket, crypto perps traded on Hyperliquid.
    trades: int                     # Trades on Polymarket, fills on Hyperliquid.
    trades_per_day: float           # On the days they traded.
    pnl: float                      # Dollars the markets, or the fills, made, before income.
    spent: float                    # Dollars bought in the markets counted on Polymarket, traded on Hyperliquid.
    win_rate: float | None          # Share of the markets counted that made money. None on Hyperliquid.
    top_share: float | None         # Share of the money made that came from the best market, or coin. None when none made money.
    favorite_share: float | None    # Share of the dollars bought at FAVORITE_PRICE or more. None on Hyperliquid.
    maker_share: float | None       # Share of fills that added liquidity. None on Polymarket, whose activity does not say.
    income: float                   # Dollars of rewards, yield, and rebates.
    liquidations: int               # Fills of the account's own liquidation.
    kind: str = ""


def market_pnl(rows, market):
    """
    A trader's PnL in one resolved market from their rows of activity in it, and the dollars they bought in it: the
    dollars they paid and got, plus what the shares they still held paid. A split buys a share of every outcome, a merge
    sells one back, and a redeem pays out the trader's shares and ends them.
    """
    payout = [float(p) for p in json.loads(market.outcome_prices)]
    shares, cash, bought = defaultdict(float), 0.0, 0.0
    for r in rows:
        if r.type == "TRADE":
            sign = 1 if r.side == "BUY" else -1
            shares[r.outcome_index] += sign * r.size
            cash -= sign * r.usdc_size
            bought += r.usdc_size if r.side == "BUY" else 0.0
        elif r.type in ("SPLIT", "MERGE"):
            sign = 1 if r.type == "SPLIT" else -1
            for i in range(len(payout)):
                shares[i] += sign * r.size
            cash -= sign * r.usdc_size
        elif r.type == "REDEEM":
            shares.clear()
            cash += r.usdc_size
        elif r.type == "CONVERSION":
            cash += r.usdc_size
    return cash + sum(n * payout[i] for i, n in shares.items() if i is not None and 0 <= i < len(payout)), bought


def counted(market, begun):
    """
    Whether a market counts in a trader's results: a resolved sports or politics market, opened after begun, the trader's
    first row read, in seconds since 1970.
    """
    return (market is not None and market.category in CATEGORIES and market.resolution == "resolved" and bool(market.outcome_prices)
            and (epoch(market.start_date) or 0) >= begun)


def kind(result):
    """
    The kind of trader a Result makes them, see the top of this file.
    """
    if result.trades_per_day >= BOT_TRADES_PER_DAY or ((result.maker_share or 0) >= MAKER_SHARE and result.trades_per_day >= BOT_TRADES_PER_DAY / 2):
        return "bot"
    if (result.markets < MIN_MARKETS) if result.venue == "polymarket" else (result.trades < MIN_FILLS):
        return "few"
    if result.top_share is not None and result.top_share >= ONE_HIT_SHARE:
        return "one-hit"
    if result.favorite_share is not None and result.favorite_share >= FAVORITE_SHARE:
        return "favorites"
    return "directional"


def top_share(pnls):
    """
    The share of the money made, the sum of the positive PnLs, that the best one made, or None when none made any.
    """
    gains = sum(p for p in pnls if p > 0)
    return max(pnls) / gains if gains > 0 else None


def polymarket_result(address, rows, markets):
    """
    A Polymarket trader's Result from their activity, oldest first, and the stored markets, as {condition_id: Market}.
    """
    begun = rows[0].timestamp if rows else 0
    by_market, income = defaultdict(list), 0.0
    for r in rows:
        if r.type in INCOME:
            income += r.usdc_size
        elif r.condition_id:
            by_market[r.condition_id].append(r)
    results = [market_pnl(market_rows, markets[c]) for c, market_rows in by_market.items() if counted(markets.get(c), begun)]
    trades = [r for r in rows if r.type == "TRADE"]
    buys = [r for r in trades if r.side == "BUY"]
    bought = sum(r.usdc_size for r in buys)
    pnls = [pnl for pnl, _ in results]
    found = Result(
        venue="polymarket",
        address=address,
        markets=len(results),
        trades=len(trades),
        trades_per_day=len(trades) / max(1, len({r.timestamp // DAY for r in trades})),
        pnl=sum(pnls),
        spent=sum(spent for _, spent in results),
        win_rate=sum(p > 0 for p in pnls) / len(pnls) if pnls else None,
        top_share=top_share(pnls),
        favorite_share=sum(r.usdc_size for r in buys if r.price >= FAVORITE_PRICE) / bought if bought else None,
        maker_share=None,
        income=income,
        liquidations=0,
    )
    found.kind = kind(found)
    return found


def hyperliquid_result(address, fills):
    """
    A Hyperliquid account's Result from its fills, oldest first, crypto perps alone.
    """
    perps = [f for f in fills if is_crypto_perp(f.coin)]
    by_coin = defaultdict(float)
    for f in perps:
        by_coin[f.coin] += (f.closed_pnl or 0.0) - (f.fee or 0.0)
    own = [f for f in perps if f.liquidation and json.loads(f.liquidation).get("liquidatedUser", "").lower() == address]
    found = Result(
        venue="hyperliquid",
        address=address,
        markets=len(by_coin),
        trades=len(perps),
        trades_per_day=len(perps) / max(1, len({f.time // (DAY * 1000) for f in perps})),
        pnl=sum(by_coin.values()),
        spent=sum(f.price * f.size for f in perps),
        win_rate=None,
        top_share=top_share(list(by_coin.values())),
        favorite_share=None,
        maker_share=sum(not f.crossed for f in perps) / len(perps) if perps else None,
        income=0.0,
        liquidations=len(own),
    )
    found.kind = kind(found)
    return found


def results(conn, venue):
    """
    The Result of each followed trader of a venue with anything stored, a Hyperliquid account only if it is a user's own.
    """
    followed = database.traders(conn, venue)
    if venue == "polymarket":
        markets = database.markets(conn)
        return [polymarket_result(t.address, rows, markets) for t in followed if (rows := database.activity_of(conn, t.address))]
    return [hyperliquid_result(t.address, fills) for t in followed if t.role == "user" and (fills := database.fills_of(conn, t.address))]


def report(found, top=25):
    """
    Lines saying how many traders are of each kind and the median of each kind's numbers, and the top directional traders
    by PnL, the ones a copy might follow.
    """
    counts = Counter(r.kind for r in found)
    lines = [f"{len(found):,} traders: " + ", ".join(f"{counts[k]:,} {k}" for k in KINDS),
             "", f"{'kind':<12}{'traders':>8}{'markets':>9}{'trades/day':>11}{'median pnl':>13}{'made money':>11}"]
    for k in KINDS:
        group = [r for r in found if r.kind == k]
        if group:
            markets, pace = statistics.median(r.markets for r in group), statistics.median(r.trades_per_day for r in group)
            lines.append(f"{k:<12}{len(group):>8,}{markets:>9.0f}{pace:>11.1f}{statistics.median(r.pnl for r in group):>+13,.0f}"
                         f"{sum(r.pnl > 0 for r in group) / len(group):>11.0%}")
    best = sorted((r for r in found if r.kind == "directional"), key=lambda r: -r.pnl)[:top]
    lines += ["", f"top {len(best)} directional traders by pnl",
              f"{'address':<44}{'markets':>8}{'trades/day':>11}{'pnl':>13}{'return':>8}{'win rate':>9}"]
    for r in best:
        win = f"{r.win_rate:.0%}" if r.win_rate is not None else "-"
        lines.append(f"{r.address:<44}{r.markets:>8,}{r.trades_per_day:>11.1f}{r.pnl:>+13,.0f}{r.pnl / r.spent if r.spent else 0:>8.1%}{win:>9}")
    return lines


# MAIN

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rebuild the followed traders' results and tell their kinds apart.")
    ap.add_argument("--venue", choices=["polymarket", "hyperliquid"], required=True)
    ap.add_argument("--top", type=int, default=25, help="directional traders listed, 25 by default")
    args = ap.parse_args()
    print("\n".join(report(results(database.connect(), args.venue), args.top)))
