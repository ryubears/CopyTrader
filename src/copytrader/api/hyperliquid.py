"""
Hyperliquid API client, read only, for finding the accounts worth copying and reading what they trade.

Two hosts. The info API answers every read as a POST of {"type": ...} to INFO: market data, and an account's positions,
fills, funding payments, and deposits and withdrawals, with times in milliseconds since 1970. The stats host serves the
files the app's pages load: the leaderboard, every account with its PnL, ROI, and volume over a day, a week, a month,
and all time, and the list of vaults. This is the only file that knows Hyperliquid's request types and field names: the
reads return what Hyperliquid sends, numbers as strings, and the parse functions at the end turn its rows into the
models the database stores.

The info API allows 1200 weight a minute per IP. Most requests weigh WEIGHT, a few cheap ones LIGHT_WEIGHT, and a call
that answers with rows one more for every ROWS_PER_WEIGHT of them, charged once the answer is in. A call over time
answers oldest first with at most a page of rows, see FILLS_PAGE, LEDGER_PAGE, and FUNDING_PAGE, and paged() walks the
pages. Hyperliquid documents that only an account's 10,000 most recent fills can be read, though on 2026-10-12 one
account gave 13,317 back to 2023, and only about the 5,000 most recent candles of each interval are kept, so older
history has to be recorded as it happens. Some very active accounts' fills lag far behind: on
2026-10-08 one account trading that minute had no fill newer than 80 minutes, and another none newer than 6 months.
Their positions, from positions(), are live.

The leaderboard lists vaults too, HLP among them, about 700 of the 9,500 there were on 2026-10-08, and vaults() lists
them all in one call. A sub-account can only be told apart one account at a time, by role(). Builder deployed perp
dexes, HIP-3, such as "xyz" for stocks and commodities, prefix their coins, "xyz:AAPL", and an account's fills include
them and its spot trades too, see is_crypto_perp().
"""

import json
from copytrader.api.http import RateLimiter, get_json, post_json
from copytrader.db.models import AccountState, Fill, Funding, LedgerUpdate, PnlPoint, Position, Standing

INFO = "https://api.hyperliquid.xyz/info"
STATS = "https://stats-data.hyperliquid.xyz/Mainnet"    # The files behind the app's leaderboard and vaults pages.
LIMITER = RateLimiter(10, burst=600)    # Weight a second. Hyperliquid allows 1200 a minute per IP, and this keeps to half, so the recorder
                                        # and a backfill can run at once.
WEIGHT = 20                 # What most info requests weigh.
LIGHT_WEIGHT = 2            # What allMids, l2Book, and clearinghouseState weigh.
ROLE_WEIGHT = 60            # What userRole weighs.
ROWS_PER_WEIGHT = 20        # Rows of fills, funding, or ledger updates that weigh one more.
CANDLES_PER_WEIGHT = 60     # Candles that weigh one more.
FILLS_PAGE = 2000           # The most fills one call answers with.
LEDGER_PAGE = 2000          # The most deposits, withdrawals, and transfers one call answers with.
FUNDING_PAGE = 500          # The most funding payments, or funding rates, one call answers with.


# REQUESTS

def info(body, weight=WEIGHT, rows_per_weight=None):
    """
    One info request's answer. Fields set to None are left out. An answer of rows then spends one more weight for every
    rows_per_weight rows, so the next request waits for it.
    """
    answer = post_json(INFO, {k: v for k, v in body.items() if v is not None}, LIMITER, weight)
    if rows_per_weight and isinstance(answer, list) and len(answer) >= rows_per_weight:
        LIMITER.wait(len(answer) // rows_per_weight)
    return answer


def paged(body, start, end, page):
    """
    Every row of an info call over time from start to end, in milliseconds since 1970 and both included, oldest first,
    a page a call. A full page may stop partway through a millisecond, so the rows at its last time are held back and
    asked for again as the start of the next page. Raises ValueError for a full page all at one time, which paging
    cannot get past.
    """
    while True:
        rows = info({**body, "startTime": start, "endTime": end}, rows_per_weight=ROWS_PER_WEIGHT)
        if len(rows) < page:
            yield from rows
            return
        last = rows[-1]["time"]
        if last == start:
            raise ValueError(f"a full page of {body['type']} rows all at {start}, which paging cannot get past")
        yield from (row for row in rows if row["time"] < last)
        start = last


# MARKETS

def is_crypto_perp(coin):
    """
    Whether a coin, as a fill names it, is a perp on Hyperliquid's own dex, which lists only crypto. A HIP-3 dex's perp
    carries the dex's prefix, "xyz:AAPL", and a spot pair is "@" and its index, "@107", or its two tokens, "PURR/USDC".
    """
    return ":" not in coin and "/" not in coin and not coin.startswith("@")


def meta(dex=""):
    """
    A perp dex's coins, each with its size decimals and most leverage. The empty dex is Hyperliquid's own.
    """
    return info({"type": "meta", "dex": dex})


def asset_contexts(dex=""):
    """
    [meta, contexts]: a perp dex's coins and, in the same order, each one's mark price, funding rate, and open interest.
    """
    return info({"type": "metaAndAssetCtxs", "dex": dex})


def perp_dexs():
    """
    Every perp dex, the first None for Hyperliquid's own.
    """
    return info({"type": "perpDexs"})


def mids(dex=""):
    """
    Each coin's mid price now, as {coin: price}.
    """
    return info({"type": "allMids", "dex": dex}, LIGHT_WEIGHT)


def book(coin):
    """
    A coin's order book now: levels holds the bids and the asks, best first, up to 20 levels each.
    """
    return info({"type": "l2Book", "coin": coin}, LIGHT_WEIGHT)


def recent_trades(coin):
    """
    A coin's latest trades, each with users, the buyer's and the seller's addresses.
    """
    return info({"type": "recentTrades", "coin": coin})


def candles(coin, interval, start, end=None):
    """
    A coin's candles of an interval, "1m" to "1M", from start to end, in milliseconds since 1970, oldest first. Only about
    the 5,000 most recent of each interval are kept, three and a half days of 1 minute candles.
    """
    request = {"coin": coin, "interval": interval, "startTime": start, "endTime": end}
    return info({"type": "candleSnapshot", "req": {k: v for k, v in request.items() if v is not None}}, rows_per_weight=CANDLES_PER_WEIGHT)


def funding_rates(coin, start, end=None):
    """
    A coin's hourly funding rates from start to end, in milliseconds since 1970, oldest first.
    """
    return paged({"type": "fundingHistory", "coin": coin}, start, end, FUNDING_PAGE)


# ACCOUNTS

def leaderboard():
    """
    Every account on the app's leaderboard, vaults among them, 47,000 or so in a 40 MB answer, each with its ethAddress,
    accountValue, and windowPerformances: the PnL, ROI, and volume over a day, a week, a month, and all time. These are
    standings now, so picking past traders by them would pick by what came after.
    """
    return get_json(f"{STATS}/leaderboard")["leaderboardRows"]


def vaults():
    """
    Every vault, 9,500 or so in a 15 MB answer, each with its address in summary's vaultAddress.
    """
    return get_json(f"{STATS}/vaults")


def role(user):
    """
    What an account is, as {"role": role}: "user", "agent", "vault", "subAccount", whose data names its master, or
    "missing". It weighs ROLE_WEIGHT, three times most requests.
    """
    return info({"type": "userRole", "user": user}, ROLE_WEIGHT)


def sub_accounts(user):
    """
    A master account's sub-accounts, each with its address in subAccountUser, or None when it has none.
    """
    return info({"type": "subAccounts", "user": user})


def positions(user, dex=""):
    """
    An account's open perp positions on one dex and its margin, from the clearinghouseState call. Live, even when its
    fills lag.
    """
    return info({"type": "clearinghouseState", "user": user, "dex": dex}, LIGHT_WEIGHT)


def portfolio(user):
    """
    An account's value and PnL over time, for each of day, week, month, and allTime, and the same four for perps alone.
    """
    return info({"type": "portfolio", "user": user})


def recent_fills(user):
    """
    An account's latest fills, at most FILLS_PAGE, newest first, on every dex and spot.
    """
    return info({"type": "userFills", "user": user}, rows_per_weight=ROWS_PER_WEIGHT)


def fills(user, start=0, end=None):
    """
    An account's fills from start to end, in milliseconds since 1970, oldest first, on every dex and spot. Hyperliquid
    documents that only its 10,000 most recent can be read, though some accounts give more.
    """
    return paged({"type": "userFillsByTime", "user": user}, start, end, FILLS_PAGE)


def funding_payments(user, start=0, end=None):
    """
    The funding an account paid or was paid on its positions from start to end, in milliseconds since 1970, oldest
    first.
    """
    return paged({"type": "userFunding", "user": user}, start, end, FUNDING_PAGE)


def ledger(user, start=0, end=None):
    """
    An account's deposits, withdrawals, transfers, and liquidations from start to end, in milliseconds since 1970,
    oldest first.
    """
    return paged({"type": "userNonFundingLedgerUpdates", "user": user}, start, end, LEDGER_PAGE)


# ROWS

def number(value):
    """
    A number Hyperliquid sends as a string, as a float, or None for a missing one.
    """
    return float(value) if value not in (None, "") else None


def parse_standings(row, day, recorded_at):
    """
    The Standings of one leaderboard row, one for each of its windows, "day", "week", "month", and "allTime", recorded
    on a UTC day at a time in seconds since 1970.
    """
    address, value = row["ethAddress"].lower(), number(row.get("accountValue"))
    return [Standing("hyperliquid", day, window, address, None, float(p["pnl"]), number(p.get("roi")), float(p["vlm"]), value, recorded_at)
            for window, p in row["windowPerformances"]]


def parse_fill(address, f):
    """
    A Fill from one fill of fills() or recent_fills().
    """
    return Fill(
        address=address,
        tid=f["tid"],
        order_id=f["oid"],
        time=f["time"],
        coin=f["coin"],
        side=f["side"],
        price=float(f["px"]),
        size=float(f["sz"]),
        direction=f.get("dir"),
        start_position=number(f.get("startPosition")),
        closed_pnl=number(f.get("closedPnl")),
        fee=number(f.get("fee")),
        fee_token=f.get("feeToken"),
        crossed=f.get("crossed"),
        hash=f.get("hash"),
        twap_id=f.get("twapId"),
        liquidation=json.dumps(f["liquidation"]) if f.get("liquidation") else None,
    )


def parse_funding(address, row):
    """
    A Funding from one payment of funding_payments().
    """
    delta = row["delta"]
    return Funding(address, row["time"], delta["coin"], float(delta["usdc"]), float(delta["szi"]), float(delta["fundingRate"]))


def parse_ledger(address, row):
    """
    A LedgerUpdate from one row of ledger(). The change is kept whole as JSON, its keys sorted, since its fields differ by
    kind and a read of the same row again must match it.
    """
    delta = row["delta"]
    return LedgerUpdate(address, row["time"], row["hash"], delta["type"], number(delta.get("usdc")), json.dumps(delta, sort_keys=True))


def parse_account(address, state):
    """
    The AccountState and the Positions of one positions() answer, all at Hyperliquid's time for it.
    """
    summary, time = state["marginSummary"], state["time"]
    account = AccountState(address, time, float(summary["accountValue"]), float(summary["totalNtlPos"]), float(summary["totalMarginUsed"]),
                           float(state["withdrawable"]))
    positions = []
    for entry in state["assetPositions"]:
        p = entry["position"]
        leverage = p.get("leverage") or {}
        positions.append(Position(
            address=address,
            time=time,
            coin=p["coin"],
            size=float(p["szi"]),
            entry_price=number(p.get("entryPx")),
            position_value=number(p.get("positionValue")),
            unrealized_pnl=number(p.get("unrealizedPnl")),
            leverage=number(leverage.get("value")),
            leverage_type=leverage.get("type"),
            liquidation_price=number(p.get("liquidationPx")),
            margin_used=number(p.get("marginUsed")),
        ))
    return account, positions


def parse_portfolio(address, answer, window="perpAllTime"):
    """
    The PnlPoints of one portfolio() answer: the account's PnL since it began, and its value, at each point of a window,
    perpAllTime by default, its perps alone over its whole life, a point about every two weeks. Times become seconds since
    1970, as pnl_history keeps them.
    """
    history = dict(answer)[window]
    values = {t: float(v) for t, v in history["accountValueHistory"]}
    return [PnlPoint("hyperliquid", address, t // 1000, float(pnl), values.get(t)) for t, pnl in history["pnlHistory"]]
