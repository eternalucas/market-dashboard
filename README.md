# Market Dashboard

A Python and Streamlit dashboard for the attention, positioning and on-chain
data I used to check by hand across a dozen websites before making
discretionary trades. It pulls free public data from about 28 providers,
cleans it, stores it in a local SQLite database, and shows it in seven views.

It's a data tool, not a trading bot: it informs decisions, it doesn't make them.

**At a glance**

- 287 charts across 38 dashboards, each with an "About this chart" note on how the metric is built
- 121 labelled wallets tracked (exchanges, market makers, Bitcoin wallets holding 10,000+ BTC)
- 150+ tests, run without the network against real captured API responses
- Outputs checked against Glassnode's free reference values where available; every discrepancy and fix is logged in [`BUILD_LOG.md`](BUILD_LOG.md)

## Views

- **Studio**: 38 dashboards in the style of Glassnode Studio (spot, futures,
  options, ETFs, macro, on-chain, stablecoins, tokenized equities and
  indicators), built only from free sources and excluding anything that needs
  a Bitcoin node. A search box finds any chart or dashboard by name.
- **Positioning**: funding, open interest, basis, order-book depth and
  imbalance, options skew and options open interest across exchanges.
- **Flows**: cross-exchange order flow: liquidations, aggregated OI, spot vs
  perp CVD, funding, spot CVD per exchange, Coinbase premium, and a
  liquidation heatmap across Binance, Bybit, OKX and Hyperliquid.
- **On-chain**: balances and transfers of labelled wallets, flows between
  entities, size and activity cohorts, and how Hyperliquid traders of each
  account size are positioned.
- **Macro events**: how BTC, ETH, SOL, the S&P 500, Nasdaq, the dollar, gold
  and the 10-year yield moved 1 to 28 days after CPI, PPI, payrolls, jobless
  claims, FOMC, BoE, BoJ and PBoC decisions, plus the calendar ahead (as a
  list or a month calendar).
- **Trade review**: a Top 100 of on-chain perp traders (Hyperliquid), ranked
  from each trader's own fills, funding and account history rather than the
  venue's leaderboard, with market makers and vaults excluded by behaviour;
  each trader opens into a post-trade review that rebuilds round trips from
  raw fills and flags recurring mistakes.
- **Data health**: which feeds succeeded or failed on the last run, and why.

Also in this repo: two TradingView Pine Script indicators in [`pine/`](pine/)
(Coinbase premium, RSI and MACD divergence).

## Run it

Requires Python 3.10+.

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # add free API keys here (optional)
streamlit run app.py
```

On a Mac, double-click **Start Dashboard.command** instead. Then click
**Update all data** in the sidebar. The first backfill takes several
minutes; some sources (BGeometrics, App Store ranks, YouTube) build history
over days because of free-tier limits or because history only exists from
the first recording.

Three free keys unlock more data: Coinalyze (Flows tab), FRED (US macro
events) and YouTube (optional). Everything else needs no key.

Run the tests with `python -m pytest`.

## How it's built

```
src/collectors/   fetch live data from exchange and public APIs  (network only, no maths)
src/sources/      one adapter per historical source, scheduled by registry.py
src/metrics/      chart formulas for the Studio dashboards       (maths only, no network)
src/indicators/   scores, flows, trades, cohorts                  (maths only, no network)
src/review/       trade reconstruction and leaderboard sampling for Trade review
src/catalog.py    every Studio chart: its name, description, source and formula
src/pipeline.py   fetch -> clean -> store, logging every success and failure
src/recorder.py   background jobs: order books, heatmap, futures curve, options
src/storage.py    SQLite: one long table of (series, timestamp, value) + a fetch log
app.py            Streamlit dashboard
watchlist.yaml    labelled wallets and where each label comes from
tests/            formulas checked against hand-worked numbers; parsers against real responses
```

Keeping fetching and maths apart means every formula is tested without the
network, and a broken source never breaks the rest of the dashboard.

**Data providers**: Coin Metrics, BGeometrics, blockchain.com, mempool.space,
Blockstream, Blockscout, DefiLlama, CoinGecko, alternative.me, Coinalyze,
Coinbase, Binance, Bybit, OKX, Kraken, Hyperliquid, Deribit, CFTC, FRED,
Yahoo Finance, Farside, Wikimedia, Google Trends, YouTube, the App Store,
Hacker News and GDELT.

## Social Risk method

A 0 to 1 score of how crowded and euphoric retail crypto attention is,
built from 12 inputs in two groups.

| Group | Input | Weight |
|---|---|---|
| Attention | Wikipedia pageviews, English (11 crypto articles) | 1.00 |
| Attention | Wikipedia pageviews, 12 other languages | 0.75 |
| Attention | Google Trends (7 search terms) | 1.00 |
| Attention | App Store ranks of crypto apps (US, Canada, UK) | 1.00 |
| Attention | YouTube crypto channels: new views per day | 0.75 |
| Attention | Hacker News mentions (weekly) | 0.50 |
| Attention | News coverage, GDELT (share of all articles) | 0.50 |
| Attention | X impressions (logged by hand; X's API is paid) | 0.50 |
| Speculation | Fear & Greed Index | 0.50 |
| Speculation | Perpetual funding rate | 1.00 |
| Speculation | Altcoin Season Index | 0.75 |
| Speculation | Memecoin share of altcoin volume | 0.75 |

Social Risk = 65% Attention + 35% Speculation.

1. Each raw series is turned into a share where a denominator exists (an
   article's views over all views of that Wikipedia, crypto articles over
   all news), which removes platform growth or decline from the signal.
2. Each series is scored by its rolling two-year percentile: 0.9 means
   higher than 90% of the past two years. Inputs with a fixed scale
   (Fear & Greed, Altcoin Season, app ranks) are rescaled directly.
3. Series of the same kind are averaged into a component, components are
   weighted into the two groups, and the groups into the score. A day needs
   at least three components to get a score.

## Positioning

- **Funding** from Bybit, OKX, Binance and Hyperliquid, normalised to an
  8-hour rate using each contract's own payment interval (Hyperliquid pays
  hourly, some contracts every 4 hours) and averaged by open interest.
- **Open interest** is one-sided on every venue (Bybit reports both sides,
  so its figure is halved to its one-sided value).
- **Basis**: perp mark over spot index.
- **Open interest** in coins, so price moves don't look like new positions.
- **Order book**: USD resting within 0.1% to 5% of mid, bid/ask imbalance,
  and a flag when the snapshot doesn't reach the full band.
- **25-delta risk reversal** from Deribit options: delta computed with
  Black-76 on each expiry's forward, IV interpolated to exactly 25 delta.

## Flows method

Source: Coinalyze's free API, aggregated over Coinbase, Binance, Bybit and
OKX spot and Binance, Bybit, OKX and Hyperliquid stablecoin-margined perps.

- **CVD** per bar = taker buys - taker sells, summed across exchanges bar by
  bar, then accumulated over the window.
- **OI in coins** = each exchange's USD OI / its own price, summed.
- **Funding** = OI-weighted average, % per 8h.
- **Coinbase premium** = Coinbase BTC-USD / Binance BTC-USDT - 1.
- **Liquidation heatmap**: Binance, Bybit and OKX levels are modelled from
  each venue's open interest (new positions placed at the bar's price, split
  long/short by taker flow, spread over a leverage mix; a level disappears
  once price trades through it). Hyperliquid levels are exact, read from its
  accounts' positions. Real liquidations recorded from the exchanges' public
  feeds are drawn on the chart and used to fit the leverage mix.
- Requests are paced to Coinalyze's rate limit, and every bar is stored
  locally because Coinalyze deletes intraday bars after about 1,500.

## Macro event method

US release dates come from FRED (including scheduled future dates); BoE, BoJ
and PBoC dates from the central banks' published calendars. The move is
measured from the close before the event to the close N days later, and
compared with the asset's typical N-day move, so a rising market doesn't make
every event look bullish. Releases are labelled against the previous reading
(CPI hotter or cooler, payrolls strong or weak, FOMC hike, cut or hold)
because consensus forecasts aren't freely available.

## Trade review method

**Top 100.** Candidates come from Hyperliquid's leaderboard file (the top
1,000 per window, active accounts first), but every number shown is
recomputed from the account's own fills, funding and ledger: PnL marked to
market daily, time-weighted return on equity (deposits and withdrawals don't
count as performance), Sharpe, Sortino, drawdown and share of winning weeks.
Market makers, vaults and protocol accounts are excluded by behaviour (fill
rate, maker share with very short holds, two-sided quoting), and every
eligibility filter is adjustable. Details in
[`docs/trade-review-plan.md`](docs/trade-review-plan.md).

**Round trips.** Fills from Hyperliquid's public API are rebuilt into round
trips: open from flat, adds, partial exits, close to flat; a flip closes one
trip and opens the next, with the fee split by quantity. Funding is assigned
to the trip it was paid on, liquidations are detected, and positions already
open when the history starts are counted and excluded rather than dropped. Then: win rate, payoff ratio, profit factor, expectancy, drawdown,
fees, and breakdowns by market, entry time, holding time and size. The
recurring-error check looks for losers held longer than winners, sizing up
after a loss, averaging down, losing markets and time windows, fee drag and
overtrading days, and reports what the flagged trades cost.

A reproducible random draw (seed in config) from leaderboard accounts worth
$10k to $100k is still available as a single example.

## On-chain method

Labels come only from `watchlist.yaml` (Etherscan name tags, Arkham entity
pages, the bitinfocharts rich list), never from guesswork, and every Bitcoin
address is checksum-verified. Balances and transfers come from mempool.space
and Blockscout.

## Known limits

- Exchanges block some regions; the Data health tab shows which feeds fail and why.
- Google Trends rate-limits heavily. Fallback: export a CSV from
  trends.google.com into `data/manual/google_trends_weekly_<term>.csv`.
- App Store, YouTube and X history only exists from when collection started.
- BGeometrics' free tier allows 15 calls a day and delays data by 7 days.
- SOL skew isn't covered (Deribit SOL options are thin).
- Hyperliquid keeps each account's 10,000 most recent fills, so a very
  active trader's review covers a shorter period.
- Macro labels use revised data, so they can differ slightly from the print.

Built with Claude Code as a coding assistant: I set the scope, methodology
and checks; Claude wrote most of the code.
