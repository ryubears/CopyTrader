"""
Data classes shared across the project, one for each table, its fields in the order of the table's columns. The tables'
comments in schema.sql say more of each field.
"""

from dataclasses import dataclass


@dataclass
class Trader:
    """
    A trader followed on either venue.
    """
    venue: str                  # 'polymarket' or 'hyperliquid'.
    address: str                # Polymarket's proxy wallet, or Hyperliquid's account, lowercase.
    name: str | None            # Polymarket's userName, where one is set.
    reason: str                 # The board and rank that first put them here.
    first_seen: float           # Seconds since 1970.
    role: str | None            # Hyperliquid's kind of account. Null until asked, and on Polymarket.


@dataclass
class Standing:
    """
    A trader's place on one of a venue's leaderboards on one day.
    """
    venue: str
    day: str                    # The UTC day it was recorded, YYYY-MM-DD.
    board: str                  # 'SPORTS/MONTH/PNL' on Polymarket, 'month' on Hyperliquid.
    address: str
    rank: int | None            # Polymarket's rank.
    pnl: float
    roi: float | None           # Hyperliquid's.
    volume: float
    account_value: float | None # Hyperliquid's.
    recorded_at: float          # Seconds since 1970.


@dataclass
class PnlPoint:
    """
    A trader's PnL since they began, at one time, as the venue charts it.
    """
    venue: str
    address: str
    time: int                   # Seconds since 1970.
    pnl: float
    account_value: float | None # Hyperliquid's.


@dataclass
class Sync:
    """
    When a followed account was last read for one kind of read.
    """
    venue: str
    address: str
    kind: str                   # 'fills', 'positions', 'funding', or 'ledger', or a backfill's, 'pnl' or 'activity'.
    synced_at: float            # Seconds since 1970.


@dataclass
class Fill:
    """
    One fill of a Hyperliquid account.
    """
    address: str
    tid: int
    order_id: int
    time: int                   # Milliseconds since 1970.
    coin: str
    side: str                   # 'B' for a buy, 'A' for a sale.
    price: float
    size: float
    direction: str | None       # 'Open Long', 'Close Short', and the like.
    start_position: float | None
    closed_pnl: float | None
    fee: float | None
    fee_token: str | None
    crossed: bool | None        # Whether it took liquidity.
    hash: str | None
    twap_id: int | None
    liquidation: str | None     # JSON, when part of a liquidation.


@dataclass
class Funding:
    """
    One hourly funding payment on a Hyperliquid account's position.
    """
    address: str
    time: int                   # Milliseconds since 1970.
    coin: str
    usdc: float                 # Received, negative when paid.
    size: float                 # The position, negative for a short.
    rate: float


@dataclass
class LedgerUpdate:
    """
    One change of a Hyperliquid account's cash that is not a trade or funding: a deposit, a withdrawal, a transfer.
    """
    address: str
    time: int                   # Milliseconds since 1970.
    hash: str
    kind: str                   # 'deposit', 'withdraw', and the like.
    usdc: float | None
    delta: str                  # The whole change as JSON.


@dataclass
class AccountState:
    """
    A Hyperliquid account's value and margin on its own dex at one reading.
    """
    address: str
    time: int                   # Milliseconds since 1970.
    account_value: float
    total_notional: float
    margin_used: float
    withdrawable: float


@dataclass
class Position:
    """
    One open perp position of a Hyperliquid account at one reading.
    """
    address: str
    time: int                   # Milliseconds since 1970, as its AccountState's.
    coin: str
    size: float                 # Negative for a short.
    entry_price: float | None
    position_value: float | None
    unrealized_pnl: float | None
    leverage: float | None
    leverage_type: str | None   # 'cross' or 'isolated'.
    liquidation_price: float | None
    margin_used: float | None


@dataclass
class Market:
    """
    One Polymarket market, as Gamma describes it.
    """
    condition_id: str
    market_id: str
    slug: str | None
    question: str
    event_slug: str | None
    category: str | None        # 'sports' or 'politics', from its tags.
    tags: str | None            # JSON list of tag slugs.
    market_type: str | None     # 'moneyline', 'spreads', and the like.
    neg_risk: bool | None
    start_date: str | None      # ISO 8601 UTC.
    game_start: str | None      # UTC, as Gamma writes it.
    end_date: str | None
    closed: bool
    closed_time: str | None
    outcomes: str               # JSON list.
    token_ids: str              # JSON list, in the outcomes' order.
    outcome_prices: str | None  # JSON list, in the outcomes' order.
    resolution: str | None      # UMA's status, 'resolved' once final.
    volume: float | None        # Dollars traded in it, all told.
    updated_at: float           # Seconds since 1970.


@dataclass
class Activity:
    """
    One thing a Polymarket trader did on chain: a trade, split, merge, redeem, reward, yield, conversion, or rebate.
    """
    address: str
    timestamp: int              # Seconds since 1970.
    type: str
    condition_id: str           # '' when of no one market.
    asset: str                  # The outcome's token id, '' where there is none.
    outcome: str | None
    outcome_index: int | None
    side: str                   # 'BUY' or 'SELL' for a trade, '' for the rest.
    size: float
    usdc_size: float
    price: float
    tx_hash: str
    slug: str | None
    event_slug: str | None
    seq: int                    # How many identical rows of the same transaction came before.


@dataclass
class Event:
    """
    One polymarket.com event, as Gamma describes it, kept for the game it is, if any, to match it with Polymarket US's.
    """
    event_slug: str
    event_id: str               # Gamma's id.
    game_id: int | None         # The game's id in Polymarket's sports data, the same on Polymarket US. None if not a game.
    title: str
    start_time: str | None      # When a game starts, ISO 8601 UTC.
    series_slug: str | None     # 'nfl-2025', 'fomc', and the like.
    updated_at: float           # Seconds since 1970.


@dataclass
class UsMarket:
    """
    One Polymarket US market, as its gateway describes it: one instrument, whose long side holds long_outcome.
    """
    slug: str
    market_id: str
    event_slug: str
    game_id: int | None         # Its event's game, as on polymarket.com. None if not a game.
    category: str | None        # 'sports', 'politics', and the like.
    market_type: str | None     # 'moneyline', 'totals', 'spreads', 'futures', 'props', and the like.
    sports_market_type: str | None  # Finer: 'football_team_full_game_winner', and the like.
    question: str
    title: str | None
    outcomes: str               # JSON list.
    long_outcome: str | None    # The outcome the long side holds, as its side names it, not always the first.
    outcome_prices: str | None  # JSON list in the outcomes' order. Once resolved, 1 for the winner and 0 for the other.
    closed: bool
    status: str | None          # 'MARKET_STATUS_RESOLVED', and the like.
    game_start: str | None      # ISO 8601 UTC.
    end_date: str | None
    fee_coefficient: float | None   # The taker fee's coefficient.
    updated_at: float           # Seconds since 1970.


@dataclass
class UsPrice:
    """
    What buying either side of a Polymarket US market cost at the end of a minute.
    """
    slug: str
    time: int                   # Seconds since 1970, as Polymarket US stamps it: the minute's start, see api/polymarket_us.py PRICE_OFFSET.
    long_price: float | None    # The long side's display price, normally its best ask.
    short_price: float | None   # The short side's, normally one less the long side's best bid.


@dataclass
class UsPriceRead:
    """
    A stretch of a Polymarket US market's price history that has been read, so it is not read again.
    """
    slug: str
    start: int                  # Seconds since 1970.
    end: int
    points: int                 # How many prices it held.
    read_at: float


@dataclass
class UsBookLevel:
    """
    One level of a Polymarket US market's order book at a moment: a price of its long side and the contracts there.
    """
    slug: str
    time: int                   # Seconds since 1970.
    side: str                   # 'bid' or 'offer', of the long side.
    level: int                  # 0 for the best.
    price: float
    contracts: float


@dataclass
class LagPair:
    """
    A Polymarket US market and the polymarket.com outcome its long side holds, recorded side by side by record/lag.py.
    """
    slug: str                   # Polymarket US's market.
    token_id: str               # polymarket.com's outcome that Polymarket US's long side holds.
    condition_id: str           # polymarket.com's market.
    outcome: str                # The outcome's name, as polymarket.com writes it.
    game_id: int
    game_start: str | None      # ISO 8601 UTC.
    market_type: str | None     # Polymarket US's: 'moneyline', 'totals', or 'drawable_outcome'.
    fee_coefficient: float | None   # Polymarket US's taker fee's coefficient.
    first_seen: float           # Seconds since 1970.


@dataclass
class LagBook:
    """
    The best levels of one venue's order book for an outcome at a moment, as record/lag.py read it: polymarket.com's for
    the outcome's token, Polymarket US's for the market's long side.
    """
    venue: str                  # 'polymarket' or 'polymarket_us'.
    key: str                    # The token id on polymarket.com, the market's slug on Polymarket US.
    time: float                 # When it was read, seconds since 1970, by this machine's clock.
    venue_time: float | None    # When the venue last changed it, by the venue's clock, if it says.
    bids: str                   # JSON list of [price, contracts], best first.
    asks: str                   # The same, of the asks.
