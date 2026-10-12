# CopyTrader

A bot to copy the trades of the best traders on Polymarket and
Hyperliquid, if copying them turns out to pay. On Polymarket it follows
the sports and politics prediction markets, each a bet on an outcome
that pays a dollar or nothing on a fixed date. On Hyperliquid it follows
the crypto perps, bets on a coin's price with leverage that last as long
as their margin holds, and leaves out vaults and sub-accounts.

The first phase is research, before any trading: collect the traders'
histories, and test on them whether the traders at the top stay there,
and whether copying them, a little late and paying the spread and fees,
would have made money. The ranking at any past time has to come from
what was known then, out of a set of traders far wider than today's top,
since today's leaderboard already knows who did well.

## How it works

Everything lives in `src/copytrader/`, a package without `__init__.py`
files, imported as `from copytrader.api import polymarket`. So far that
is the venue clients in `api/`, the database in `db/`, and the recorder
in `record/`, which keeps what the venues will not keep for us.

The venue clients read the traders, their trades, and the markets. They
are thin: requests, paging, rate limits, and retries, and no database
code. Their reads return what the APIs send, and their parse functions
turn the rows into the models the database stores.

`api/polymarket.py` reads Polymarket's three hosts: Gamma for the events
and markets, filtered to sports or politics by tag, the order book host
for books and price history, and the data host for the leaderboard and
each trader's trades, activity, and positions. The data host stops
paging at 5,000 rows of activity, so a trader's whole history is read in
windows of time, 13,000 rows in about 30 seconds. Its trades call leaves
out the trades a trader made as maker unless asked for them, which the
client always does.

`api/hyperliquid.py` reads Hyperliquid's info API, for market data and
each account's positions, fills, and funding, and the files behind the
app's leaderboard and vaults pages, the leaderboard with every account's
PnL, ROI, and volume in one 40 MB answer. Hyperliquid documents that
only an account's 10,000 most recent fills can be read, though some
accounts give more, and keeps only about the 5,000 most recent candles
of an interval, so older history has to be recorded as it happens.
Some very active accounts' fills lag behind by minutes or months, while
their positions are live. A fill names its coin, and `is_crypto_perp()`
tells the crypto perps from the stock perps and spot pairs an account
may trade too.

`api/http.py` is what they share: one connection pool, a rate limiter
per host, weighted for Hyperliquid, and retries of network errors, rate
limit answers, and server errors, waiting as long as the server asks.

`db/` is one SQLite file, `data/copytrader.sqlite`, which is not
committed. `schema.sql` holds the tables, `models.py` a data class for
each, and `database.py` the reads and writes. Hyperliquid's tables start
`hl_` and Polymarket's `pm_`. `traders` and `standings` hold both.

`record/` keeps what would otherwise be lost. Once a UTC day it records
the leaderboards, since a leaderboard only says who leads now: the top
1,000 of each of Polymarket's 16 sports and politics boards, and every
Hyperliquid account but vaults that traded in the month before, about
18,700, with its PnL, ROI, and volume over a day, a week, a month, and
all time. The top 200 by PnL over a week, a month, and all time are
followed, 931 traders on Polymarket and 440 accounts on Hyperliquid on
the first day, and stay followed when they fall from the top, so the
record is not of the winners alone. Each followed Hyperliquid account is
asked its kind, and a sub-account is left out. A user's own is read on a
schedule: its fills every 3 hours, before they pass out of what
Hyperliquid keeps, its positions and value every hour, and its funding
and deposits and withdrawals once a day. Its first read reaches a year
back, and a read takes at most 20,000 rows, stored as they come, so a
long history is read in pieces, one a pass. A Polymarket trader's whole
history can be read whenever it is wanted, so following them only lists
them for the backfill that comes next.

## Running it

`pip3 install --break-system-packages -r requirements.txt` installs what
it needs, `python3 -m pytest` runs the tests, `python3 -m pytest -m
network` runs the smoke tests against the real APIs, and `ruff check`
lints and checks the imports' order. The code is formatted by hand, so
`ruff format` is not used.

The recorder runs from `src/` with `python3 -m copytrader.record.run`,
until stopped. What it has read is in the database, so it can be stopped
and started again at any time, and it picks up what came due meanwhile.
Only a day it does not run at all loses that day's leaderboards, and an
account that makes more fills while it is stopped than Hyperliquid keeps
loses the oldest of them. Its first hours go to reading each newly
followed account's last year, as far as Hyperliquid keeps it.
