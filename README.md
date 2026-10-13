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
files, imported as `from copytrader.api import polymarket`: the venue
clients in `api/`, the database in `db/`, the recorder in `record/`,
which keeps what the venues will not keep for us, the backfills in
`history/`, which read what they still have, and the research in
`analysis/`.

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

`api/polymarket_us.py` reads Polymarket US, Polymarket's exchange for
the US, where a copy would trade: its events and markets, each market's
order book, and each market's best prices about once a minute, back to
its first markets in late 2025. Its markets are not polymarket.com's,
but a game's event carries the same game id on both, which is how the
two are matched.

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
them for the backfill.

`history/pnl.py` reads each candidate trader's PnL over time, as the
venue charts it on their own page: Polymarket's profile chart, a point a
day across every category, and Hyperliquid's portfolio, perps alone, a
point about every two weeks over the account's life. The candidates are
drawn wide, so the record is not of the winners alone: every trader on
any of the latest day's Polymarket boards, about 8,000, and every
Hyperliquid account but vaults that traded $100 million over its life,
about 7,100, more than half of them no longer trading.

`history/polymarket.py` reads the followed Polymarket traders' activity
and the markets it touched, with how each resolved. Most traders make a
few thousand rows a year and are read back 180 days. A few make tens of
thousands a month, too fast for a copy to follow, and only their latest
20,000 rows are read, enough to tell what they are.

`analysis/persistence.py` asks whether the traders at the top stay
there. It cuts time into 28 day periods, ranks the traders by their PnL
in each, and, on Hyperliquid, by their return on the account's value,
and reports how the top tenth, the top 50, everyone, and the bottom
tenth did in the next period: how many made money, and their median.
If the top does no better than everyone, copying the top is copying
luck.

`analysis/traders.py` rebuilds each followed trader's results from what
they did: on Polymarket, their PnL in each resolved sports and politics
market, from their trades, splits, merges, and redeems and the market's
payout, and on Hyperliquid their realized PnL on crypto perps less fees.
It sorts them into kinds: too few markets to judge, bots too fast to
copy, one-hit wonders, buyers of near certain favorites, and the
directional traders a copy might follow.

`history/polymarket_us.py` reads what pricing a copy on Polymarket US
takes: the game of each polymarket.com event the stored markets belong
to, Polymarket US's markets of those games and of its events that are
not games, and Polymarket US's prices around each trade a copy might
make. `analysis/match.py` matches the markets of the two venues: within
a game, by the slug polymarket.com's market and Polymarket US's share
behind a prefix, or for a moneyline, by its two outcomes. Futures and
politics do not match yet.

`analysis/copy.py` simulates copying polymarket.com traders on Polymarket
US. Each 28 day period it ranks the traders on a volume board by the
Sharpe ratio of their daily PnL over the 84 days before, which picked
traders who made money in the next period far more often than their
PnL did, drops the bots and those who trade no matched market, and
copies the top 25, each from a sleeve of the capital of its own: their
buys in matched markets a minute to an hour late at Polymarket US's best
ask, plus what the order's size would have paid walking a snapshot of
Polymarket US's books, and their sales at its best bid, each order
paying Polymarket US's taker fee, with $10,000 or $100,000. It does not
chase a price more than 5 cents above the trader's, `--floor` skips one
more than a few cents below, which Polymarket US's minute prices show
too often to trust, and `--hold` holds to resolution instead of copying
sales. Beside each copy runs its ideal, the same dollars at the traders'
own prices without fees, so what the copy makes less is what copying
costs.

`record/lag.py` records polymarket.com's and Polymarket US's books side
by side, live, for the moneylines, totals, and soccer results of the
games under way or starting within the hour: polymarket.com's every two
seconds, all in one call, and Polymarket US's one market at a time as
fast as its limit lets, those of games under way twice as often. A book
is stored when it changes, and at least once a minute. `analysis/lag.py`
then times how long Polymarket US takes to follow a move of
polymarket.com's, finds the stretches when buying on Polymarket US cost
less, fee included, than polymarket.com's bid, with how long they
lasted and how many dollars were there to buy, and checks the minute
price history the copy simulation priced by against the books.

A Polymarket US game's event carries the game id polymarket.com's does,
or, in the smaller leagues, only Sportradar's id, whose number is
polymarket.com's game id then. Tennis matches of Sportradar's alone do
not match, as polymarket.com has them by other ids and slugs.

## Running it

`pip3 install --break-system-packages -r requirements.txt` installs what
it needs, `python3 -m pytest` runs the tests, `python3 -m pytest -m
network` runs the smoke tests against the real APIs, and `ruff check`
lints and checks the imports' order. The code is formatted by hand, so
`ruff format` is not used.

The recorder runs from `src/` with `python3 -m copytrader.record.run`,
until stopped, and `--seconds 120 --db /tmp/check.sqlite` tries it for
two minutes on a database of its own. What it has read is in the
database, so it can be stopped and started again at any time, and it
picks up what came due meanwhile. Only a day it does not run at all
loses that day's leaderboards, and an account that makes more fills
while it is stopped than Hyperliquid keeps loses the oldest of them.
Its first hours go to reading each newly followed account's last year,
as far as Hyperliquid keeps it.

The backfills run from `src/` the same way, and can run beside the
recorder, each keeping to half of Hyperliquid's limit:
`python3 -m copytrader.history.pnl` for the PnL histories, a few hours,
and `python3 -m copytrader.history.polymarket` for the followed traders'
activity, several. Either can be stopped and run again, and picks up
where it stopped. Then `python3 -m copytrader.analysis.persistence
--venue hyperliquid` and `python3 -m copytrader.analysis.traders --venue
polymarket` print their reports.

The copy simulation needs three more backfills, in order: `python3 -m
copytrader.history.polymarket --copy-candidates` reads the activity of
the traders it might copy, `python3 -m copytrader.history.polymarket_us`
reads the games, Polymarket US's markets, and its prices, and then
`python3 -m copytrader.analysis.copy` prints the simulation.

The lag recorder runs apart from the recorder, `python3 -m
copytrader.record.lag`, until stopped, and `python3 -m
copytrader.analysis.lag` reports on what it has recorded so far.

## Deployment

- **Instance:** an m7a.medium in us-east-1, beside the instances of
  PredictionArbitrageTrader and CryptoUpOrDownTrader, where Polymarket
  US's exchange runs, set up by `deploy/setup.sh` on Ubuntu 24.04 and
  its Python 3.12.
- **Service:** the systemd service `copy-trader`, in
  `deploy/copy-trader.service`, runs the recorder from `~/CopyTrader/src`.
  It starts on boot, restarts on any exit, and logs to
  `data/logs/record.log`, the first line of each run saying its commit.
- **Backfills and analyses:** run by hand on the instance, at the lowest
  priority, the backfills with `nohup` into `data/logs/`, against the live
  database.
- **Deploying:** `git pull` on the instance, then the tests, then a
  service restart only if they pass.
- **Operating:** `commands.txt`, gitignored and kept on the operator's
  machine, holds the commands to check, deploy, and operate the instance.
