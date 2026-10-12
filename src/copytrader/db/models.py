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
class Sync:
    """
    When a followed account was last read for one kind of read.
    """
    venue: str
    address: str
    kind: str                   # 'fills', 'positions', 'funding', or 'ledger'.
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
    game_start: str | None      # UTC, as Gamma writes it.
    end_date: str | None
    closed: bool
    closed_time: str | None
    outcomes: str               # JSON list.
    token_ids: str              # JSON list, in the outcomes' order.
    outcome_prices: str | None  # JSON list, in the outcomes' order.
    resolution: str | None      # UMA's status, 'resolved' once final.
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
