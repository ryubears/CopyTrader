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
is the venue clients in `api/`, which read the traders, their trades,
and the markets. They are thin: requests, paging, rate limits, and
retries, and no database code. They return what the APIs send.

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
PnL, ROI, and volume in one 40 MB answer. Only an account's 10,000 most
recent fills, and about the 5,000 most recent candles of an interval,
can be read, so older history will have to be recorded as it happens.
Some very active accounts' fills lag behind by minutes or months, while
their positions are live. A fill names its coin, and `is_crypto_perp()`
tells the crypto perps from the stock perps and spot pairs an account
may trade too.

`api/http.py` is what they share: one connection pool, a rate limiter
per host, weighted for Hyperliquid, and retries of network errors, rate
limit answers, and server errors, waiting as long as the server asks.

Next come the SQLite tables the histories go into, in `db/`, and the
backfill that fills them. The `data/` folder will hold the databases,
and is not committed.

## Running it

`pip3 install --break-system-packages -r requirements.txt` installs what
it needs, `python3 -m pytest` runs the tests, `python3 -m pytest -m
network` runs the smoke tests against the real APIs, and `ruff check`
lints and checks the imports' order. The code is formatted by hand, so
`ruff format` is not used.
