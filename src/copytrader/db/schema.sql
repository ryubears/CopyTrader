-- The tables. database.connect() runs this on every connection, so a new table or index appears on its own.
-- Times are in seconds since 1970 where Polymarket and the recorder give them, and in milliseconds where Hyperliquid does.

-- The traders followed on either venue: those whose standings put them at the top of a board, see record/standings.py.
-- Once followed, a trader stays followed, so their record goes on after they fall from the top.
CREATE TABLE IF NOT EXISTS traders (
    venue       TEXT NOT NULL,      -- 'polymarket' or 'hyperliquid'.
    address     TEXT NOT NULL,      -- Polymarket's proxy wallet, or Hyperliquid's account, lowercase.
    name        TEXT,               -- Polymarket's userName, where one is set.
    reason      TEXT NOT NULL,      -- The board and rank that first put them here, 'SPORTS/MONTH/PNL #12 on 2026-10-13'.
    first_seen  REAL NOT NULL,      -- When they were first followed, in seconds since 1970.
    role        TEXT,               -- Hyperliquid's: 'user', 'vault', 'subAccount', 'agent', or 'missing'. Only a 'user' is read.
                                    -- Null until asked, and on Polymarket.
    PRIMARY KEY (venue, address)
);

-- Each day's standings on the venues' leaderboards, as recorded: Polymarket's sports and politics boards, and every account
-- on Hyperliquid's that traded in the month before, but vaults.
CREATE TABLE IF NOT EXISTS standings (
    venue          TEXT NOT NULL,
    day            TEXT NOT NULL,       -- The UTC day it was recorded, YYYY-MM-DD.
    board          TEXT NOT NULL,       -- Polymarket's category, period, and order, 'SPORTS/MONTH/PNL', or Hyperliquid's window, 'month'.
    address        TEXT NOT NULL,
    rank           INTEGER,             -- Polymarket's rank. Hyperliquid's file ranks no one.
    pnl            REAL NOT NULL,       -- Dollars won or lost over the board's period.
    roi            REAL,                -- Hyperliquid's PnL over the account's value. Polymarket gives none.
    volume         REAL NOT NULL,       -- Dollars traded over the board's period.
    account_value  REAL,                -- Hyperliquid's account value when recorded.
    recorded_at    REAL NOT NULL,
    PRIMARY KEY (venue, day, board, address)
) WITHOUT ROWID;

-- Each candidate trader's PnL over time, as the venues chart it on their own account pages, for the persistence test,
-- see history/pnl.py: Polymarket's daily across every category, and Hyperliquid's perps alone, a point about every two
-- weeks over the account's life.
CREATE TABLE IF NOT EXISTS pnl_history (
    venue          TEXT NOT NULL,
    address        TEXT NOT NULL,
    time           INTEGER NOT NULL,    -- Seconds since 1970.
    pnl            REAL NOT NULL,       -- Dollars won or lost since the account began, by then.
    account_value  REAL,                -- Hyperliquid's account value then. Polymarket's chart gives none.
    PRIMARY KEY (venue, address, time)
) WITHOUT ROWID;

-- When each followed account was last read, for each kind of read, so each comes due on its own schedule.
CREATE TABLE IF NOT EXISTS syncs (
    venue      TEXT NOT NULL,
    address    TEXT NOT NULL,
    kind       TEXT NOT NULL,       -- 'fills', 'positions', 'funding', or 'ledger', or a backfill's, 'pnl' or 'activity'.
    synced_at  REAL NOT NULL,       -- When the last read, or try, finished.
    PRIMARY KEY (venue, address, kind)
) WITHOUT ROWID;

-- Every fill of a followed Hyperliquid account, on every dex and spot, see api/hyperliquid.py's is_crypto_perp().
CREATE TABLE IF NOT EXISTS hl_fills (
    address         TEXT NOT NULL,
    tid             INTEGER NOT NULL,   -- The trade's id, which both its sides share.
    order_id        INTEGER NOT NULL,
    time            INTEGER NOT NULL,   -- Milliseconds since 1970.
    coin            TEXT NOT NULL,      -- 'BTC', or 'xyz:AAPL' on a HIP-3 dex, or '@107' or 'PURR/USDC' for spot.
    side            TEXT NOT NULL,      -- 'B' for a buy, 'A' for a sale.
    price           REAL NOT NULL,
    size            REAL NOT NULL,      -- In the coin.
    direction       TEXT,               -- 'Open Long', 'Close Short', and the like.
    start_position  REAL,               -- The position in the coin before the fill, negative for a short.
    closed_pnl      REAL,               -- Dollars the fill realized, before its fee.
    fee             REAL,               -- Negative for a maker's rebate.
    fee_token       TEXT,
    crossed         INTEGER,            -- 1 when the fill took liquidity, as a taker.
    hash            TEXT,
    twap_id         INTEGER,            -- The TWAP order it came from, if any.
    liquidation     TEXT,               -- JSON {liquidatedUser, markPx, method} when the fill was part of a liquidation.
    PRIMARY KEY (address, tid, order_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_hl_fills_time ON hl_fills (address, time);

-- Every funding payment of a followed Hyperliquid account, hourly for each open position.
CREATE TABLE IF NOT EXISTS hl_funding (
    address  TEXT NOT NULL,
    time     INTEGER NOT NULL,      -- Milliseconds since 1970.
    coin     TEXT NOT NULL,
    usdc     REAL NOT NULL,         -- Dollars received, negative when paid.
    size     REAL NOT NULL,         -- The position paid on, negative for a short.
    rate     REAL NOT NULL,         -- The hour's funding rate.
    PRIMARY KEY (address, time, coin)
) WITHOUT ROWID;

-- Every deposit, withdrawal, transfer, and other change of a followed Hyperliquid account's cash that is not a trade or funding.
CREATE TABLE IF NOT EXISTS hl_ledger (
    address  TEXT NOT NULL,
    time     INTEGER NOT NULL,      -- Milliseconds since 1970.
    hash     TEXT NOT NULL,
    kind     TEXT NOT NULL,         -- 'deposit', 'withdraw', 'send', 'subAccountTransfer', 'liquidation', and the like.
    usdc     REAL,                  -- Dollars, where the change gives them in one field.
    delta    TEXT NOT NULL,         -- The whole change as JSON, since its fields differ by kind.
    PRIMARY KEY (address, time, hash, delta)
) WITHOUT ROWID;

-- A followed Hyperliquid account's value and margin on its own dex at each reading of its positions.
CREATE TABLE IF NOT EXISTS hl_accounts (
    address         TEXT NOT NULL,
    time            INTEGER NOT NULL,   -- Hyperliquid's time for the reading, milliseconds since 1970.
    account_value   REAL NOT NULL,
    total_notional  REAL NOT NULL,      -- The open positions' value.
    margin_used     REAL NOT NULL,
    withdrawable    REAL NOT NULL,
    PRIMARY KEY (address, time)
) WITHOUT ROWID;

-- Each open perp position of a followed Hyperliquid account on its own dex at each reading, live even when its fills lag.
CREATE TABLE IF NOT EXISTS hl_positions (
    address            TEXT NOT NULL,
    time               INTEGER NOT NULL,    -- As in hl_accounts.
    coin               TEXT NOT NULL,
    size               REAL NOT NULL,       -- Negative for a short.
    entry_price        REAL,
    position_value     REAL,
    unrealized_pnl     REAL,
    leverage           REAL,
    leverage_type      TEXT,                -- 'cross' or 'isolated'.
    liquidation_price  REAL,                -- Null when the account's margin covers any move.
    margin_used        REAL,
    PRIMARY KEY (address, time, coin)
) WITHOUT ROWID;

-- Polymarket's markets, from Gamma, refreshed as they close and resolve.
CREATE TABLE IF NOT EXISTS pm_markets (
    condition_id    TEXT PRIMARY KEY,
    market_id       TEXT NOT NULL,      -- Gamma's id.
    slug            TEXT,
    question        TEXT NOT NULL,
    event_slug      TEXT,               -- The first event it belongs to.
    category        TEXT,               -- 'sports' or 'politics', from its tags, null for any other.
    tags            TEXT,               -- JSON list of its tags' slugs.
    market_type     TEXT,               -- A sports market's kind: 'moneyline', 'spreads', 'totals', and the like.
    neg_risk        INTEGER,            -- 1 when one outcome of an event with several, where a No converts into a Yes on each other.
    start_date      TEXT,               -- When it opened for trading, ISO 8601 UTC.
    game_start      TEXT,               -- When a game starts, in UTC as Gamma writes it, '2026-10-12 15:00:00+00', so trades during it stand out.
    end_date        TEXT,               -- When it is expected to resolve, ISO 8601 UTC.
    closed          INTEGER NOT NULL,   -- 1 once trading has stopped.
    closed_time     TEXT,               -- When trading stopped, in UTC as Gamma writes it.
    outcomes        TEXT NOT NULL,      -- JSON list, '["Yes", "No"]'.
    token_ids       TEXT NOT NULL,      -- JSON list of the outcomes' token ids, in the same order.
    outcome_prices  TEXT,               -- JSON list in the same order. Once resolved, 1 for the winner and 0 for the rest.
    resolution      TEXT,               -- UMA's status, 'resolved' once final.
    volume          REAL,               -- Dollars traded in it, all told.
    updated_at      REAL NOT NULL       -- When it was last read.
);

-- Everything a Polymarket trader did on chain: trades, splits, merges, redeems, rewards, yield, conversions, and rebates.
CREATE TABLE IF NOT EXISTS pm_activity (
    address        TEXT NOT NULL,
    timestamp      INTEGER NOT NULL,    -- Seconds since 1970.
    type           TEXT NOT NULL,       -- 'TRADE', 'REDEEM', and the like.
    condition_id   TEXT NOT NULL,       -- The market, '' for a rebate or reward of no one market.
    asset          TEXT NOT NULL,       -- The outcome's token id, '' where there is none.
    outcome        TEXT,                -- 'Yes', 'No', or a team's name.
    outcome_index  INTEGER,
    side           TEXT NOT NULL,       -- 'BUY' or 'SELL' for a trade, '' for the rest.
    size           REAL NOT NULL,       -- Shares.
    usdc_size      REAL NOT NULL,       -- Dollars.
    price          REAL NOT NULL,
    tx_hash        TEXT NOT NULL,
    slug           TEXT,                -- The market's slug and its event's, so a market not read from Gamma can still be placed.
    event_slug     TEXT,
    seq            INTEGER NOT NULL,    -- 0, or how many identical rows of the same transaction came before, see parse_activity().
    PRIMARY KEY (address, tx_hash, type, asset, side, size, usdc_size, price, seq)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_pm_activity_time ON pm_activity (address, timestamp);

-- The polymarket.com events the stored markets belong to, for the game each is, to match its markets with Polymarket US's.
CREATE TABLE IF NOT EXISTS pm_events (
    event_slug   TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL,     -- Gamma's id.
    game_id      INTEGER,           -- The game's id in Polymarket's sports data, the same on Polymarket US. Null if not a game.
    title        TEXT NOT NULL,
    start_time   TEXT,              -- When a game starts, ISO 8601 UTC.
    series_slug  TEXT,              -- 'nfl-2025', 'fomc', and the like.
    updated_at   REAL NOT NULL      -- When it was last read.
);

-- Polymarket US's markets that a polymarket.com market traded by a followed trader might match: the markets of its games
-- that polymarket.com's events share, by game_id, and of its events that are not games.
CREATE TABLE IF NOT EXISTS us_markets (
    slug                TEXT PRIMARY KEY,
    market_id           TEXT NOT NULL,
    event_slug          TEXT NOT NULL,
    game_id             INTEGER,            -- Its event's game, as on polymarket.com. Null if not a game.
    category            TEXT,               -- 'sports', 'politics', and the like.
    market_type         TEXT,               -- 'moneyline', 'totals', 'spreads', 'futures', 'props', and the like.
    sports_market_type  TEXT,               -- Finer: 'football_team_full_game_winner', and the like.
    question            TEXT NOT NULL,
    title               TEXT,
    outcomes            TEXT NOT NULL,      -- JSON list.
    long_outcome        TEXT,               -- The outcome the long side holds, as its side names it, not always the first.
    outcome_prices      TEXT,               -- JSON list in the outcomes' order. Once resolved, 1 for the winner and 0 for the other.
    closed              INTEGER NOT NULL,
    status              TEXT,               -- 'MARKET_STATUS_RESOLVED', and the like.
    game_start          TEXT,               -- ISO 8601 UTC.
    end_date            TEXT,
    fee_coefficient     REAL,               -- The taker fee's coefficient: fee = coefficient * contracts * p * (1 - p).
    updated_at          REAL NOT NULL       -- When it was last read.
);

CREATE INDEX IF NOT EXISTS idx_us_markets_game ON us_markets (game_id);

-- What buying either side of a Polymarket US market cost, about once a minute, around the times a copy would have traded.
CREATE TABLE IF NOT EXISTS us_prices (
    slug         TEXT NOT NULL,
    time         INTEGER NOT NULL,     -- Seconds since 1970.
    long_price   REAL,                 -- The long side's display price, normally its best ask.
    short_price  REAL,                 -- The short side's, normally one less the long side's best bid.
    PRIMARY KEY (slug, time)
) WITHOUT ROWID;

-- The stretches of each Polymarket US market's price history read, so none is read twice, even one that held nothing.
CREATE TABLE IF NOT EXISTS us_price_reads (
    slug     TEXT NOT NULL,
    start    INTEGER NOT NULL,         -- Seconds since 1970.
    end      INTEGER NOT NULL,
    points   INTEGER NOT NULL,         -- How many prices it held.
    read_at  REAL NOT NULL,
    PRIMARY KEY (slug, start)
) WITHOUT ROWID;

-- Snapshots of Polymarket US's order books, for how much an order of a size pays above the best price.
CREATE TABLE IF NOT EXISTS us_books (
    slug       TEXT NOT NULL,
    time       INTEGER NOT NULL,       -- Seconds since 1970.
    side       TEXT NOT NULL,          -- 'bid' or 'offer', of the long side. The short side's offers are the long side's bids.
    level      INTEGER NOT NULL,       -- 0 for the best.
    price      REAL NOT NULL,
    contracts  REAL NOT NULL,
    PRIMARY KEY (slug, time, side, level)
) WITHOUT ROWID;

-- Polymarket US's markets recorded live beside polymarket.com's, for how far and how long one lags the other, by
-- record/lag.py.
CREATE TABLE IF NOT EXISTS lag_pairs (
    slug             TEXT PRIMARY KEY,      -- Polymarket US's market.
    token_id         TEXT NOT NULL,         -- polymarket.com's outcome that Polymarket US's long side holds.
    condition_id     TEXT NOT NULL,         -- polymarket.com's market.
    outcome          TEXT NOT NULL,
    game_id          INTEGER NOT NULL,
    game_start       TEXT,                  -- ISO 8601 UTC.
    market_type      TEXT,                  -- Polymarket US's: 'moneyline', 'totals', or 'drawable_outcome'.
    fee_coefficient  REAL,                  -- Polymarket US's taker fee's coefficient.
    first_seen       REAL NOT NULL          -- Seconds since 1970.
);

-- The best levels of both venues' books for each pair, stored when they change and at least once a minute.
CREATE TABLE IF NOT EXISTS lag_books (
    venue       TEXT NOT NULL,              -- 'polymarket' or 'polymarket_us'.
    key         TEXT NOT NULL,              -- The token id on polymarket.com, the market's slug on Polymarket US.
    time        REAL NOT NULL,              -- When it was read, seconds since 1970, by this machine's clock.
    venue_time  REAL,                       -- When the venue last changed it, by the venue's clock, if it says.
    bids        TEXT NOT NULL,              -- JSON list of [price, contracts], best first.
    asks        TEXT NOT NULL,
    PRIMARY KEY (venue, key, time)
) WITHOUT ROWID;
