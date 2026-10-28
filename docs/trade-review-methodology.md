# Trade review: methodology

Every formula and assumption behind the Trade review tab. Actual results
(Review, Diagnosis) are computed from the venue's raw data; simulated
results (Backtest & Improve) are labelled "Simulated" everywhere and never
overwrite actual data.

## 1. Data

| What | Source | Limit |
|---|---|---|
| Fills, funding payments, ledger, account value history | Hyperliquid public info API | 10,000 most recent fills per wallet; wallets that hit it are flagged "history truncated" |
| Candles | Hyperliquid `candleSnapshot` | Only the most recent 5,000 per interval: 1m ≈ 3.5 days, 15m ≈ 52 days, 1h ≈ 208 days, 4h ≈ 833 days, 1d all |
| Market funding | Hyperliquid `fundingHistory` | Loaded for the last 60 days; older entries use the rate on the account's own payments |
| Order book history | none free | Slippage is a proxy (Diagnosis) or a model (Backtest) |

Candles and funding are loaded for the most traded markets (98% of trips,
at most 24 markets) to stay inside the venue's rate limit, in a background
thread, and stored in `data/trades.db`. Each range is fetched once; a range
the venue doesn't have is remembered and not requested again.

## 2. Round trips (`src/review/reconstruct.py`)

A round trip opens when a position leaves flat and closes when it returns to
flat; a flip closes one trip and opens the next on the same fill, its fee
split by quantity. Net = venue's closed P&L − fees (builder fee included) +
funding paid or received while the trip was open (matched by market, time
and side). Fills of a position opened before the stored history, funding
outside any trip, and positions that changed without a fill are counted in
Data quality, not dropped silently. Each trip keeps its own part of every
fill ("legs") for the replay engine.

## 3. Diagnosis (`src/review/diagnosis.py`)

Actual trades only. Trips whose exit wasn't observed are left out.

- **Excursions (MAE/MFE):** the furthest price went against / in favour of
  the average entry during the trip, on the finest candles covering it.
  Intervals coarser than 15 minutes are used only when the trip spans at
  least 3 of their candles. "Deep losers" are measured from the first entry
  (so averaging down can't hide them): losers that went further against
  their first entry than 90% of winners ever did.
- **Entry context:** the return over the 24 hours before the entry, in
  units of that day's volatility (bars that closed before the entry only).
  Above +0.5 = with the move, below −0.5 = against, otherwise flat.
  **Volatility regime:** 24h volatility / its 30-day median (< 0.75 low,
  > 1.33 high). **Chasing:** the 4 hours before entry moved more than 2σ in
  the trade's direction.
- **Leverage:** largest position size / account value at the start of the day.
- **Slippage proxy:** taker fill price vs the previous 1-minute close, in the
  trade's direction, where 1-minute candles exist.
- **Concurrency:** each trip at its largest size for its whole life.
- **Account drawdown:** daily mark-to-market P&L of the whole account
  (positions opened before the history included).
- **Sizing:** size after a loss vs after a win, each relative to the median
  of the previous 20 trips; rank correlations of size with return and with
  volatility.
- **What they do right:** only claims backed by enough trips (usually 20+),
  and never one that a flagged mistake contradicts.

## 4. Backtest & Improve

### Replay (`src/review/replay.py`)

The trader's real entries (time, market, side) are kept and one thing is
changed. Each trip is replayed from its legs: cash flow − fees + funding.

- **Golden check:** an unchanged replay must equal the actual net P&L
  within 1%, or the page says the simulation isn't reliable for that trader.
  A second check replaces the final exit price with the close of the candle
  holding it and reports that error separately.
- **Exits** are laid over the trader's own exits (the earlier one wins):
  stop at a % or a multiple of the 14-bar ATR from the first entry, trailing
  stop (best price up to the previous candle − k × ATR), take profit at a
  percentile of the training trips' maximum favourable move, time stop
  (exit a trade still losing after the median / 75th / 90th percentile of
  the training winners' holding times).
- **Intrabar order:** a time exit happens at the candle's open; then the stop
  fills before the target; a stop gapped through fills at the open. Only
  candles that start after the entry are used.
- **Sizing:** volatility targeting (size ∝ 1 / daily volatility at entry);
  fixed-fractional risk with a k × ATR stop; capped Kelly (mean / variance of
  the previous 30 closed trips' returns on size, 0.25× to 2× the usual
  size); no adding to losing positions (later exits shrink in proportion);
  half size after N losses in a row. Every sizing rule is scaled so its
  median size on the training window equals the trader's, and capped at 5×
  the account (editable) or the trader's own size if larger.
- **Execution:** limit entry at an offset from the actual first entry price,
  filled only if a later candle traded through it within 30 minutes (maker
  fee), otherwise the trip is skipped; skip trips whose side paid more than
  20 / 50 / 100% a year in funding at entry.
- **Selection:** drop markets (5+ trips), 4-hour entry windows (10+ trips)
  or regimes (10+ trips) with negative expectancy on the training window.
  A selection or funding rule that would drop more than 80% of the training
  trips isn't tried (that's "stop trading", not a rule); a rule that skips
  more than half of the test trips is flagged on the page.
- **Adopted rules:** the summary and the plan only list rules that improved
  the training window; a rule that only looks good on the test window would
  be picked with hindsight.
- **Best combination:** the best rule of each category (exits, sizing,
  execution, selection) that improved the training window, together.

### Costs (`src/review/costs.py`)

- Fees: the trader's own median fee rate on taker and on maker fills
  (fallback: Hyperliquid base tier, 4.5 / 1.5 bps). Editable.
- Slippage on simulated market orders: half spread (1 bp) + k × √(order
  notional / average traded notional per minute over the 20 candles before
  the entry), k = 10 bps by default. No free order-book history exists to
  calibrate k.
- Funding: the account's actual hourly payments × simulated position /
  actual position at each payment.
- Liquidation: checked only when a variant makes a position bigger than the
  trader's. Isolated-equivalent: leverage = position / account value at
  entry, maintenance margin = half the initial margin at the market's
  maximum leverage. An approximation for cross-margin accounts.

### Validation (`src/review/validation.py`)

- Trips sorted by entry time: first 70% train, last 30% test. Every
  parameter and threshold is chosen on train; results on test are shown first.
- 90% intervals for the change in test-window P&L and Sharpe: paired
  stationary block bootstrap of daily P&L (2,000 resamples, mean block 5 days).
- Deflated Sharpe ratio with N = every variant tried (shown on the page).
- Confidence: High = the P&L interval excludes zero, deflated Sharpe ≥ 0.95
  and 50+ test trips; Medium = the interval excludes zero; Low otherwise.
- Fewer than 30 closed round trips: no improvement is claimed.
- Look-ahead: tests change every price after a time t and check that every
  result and threshold before t is unchanged.
- Persistence (whether the previous window's top traders stayed on top) is
  measured across the Top 100 in the batch run (Phase 5).

### Rule extraction (`src/review/rules.py`, experimental)

Features at the close of each hourly candle: returns over 5, 15, 60 and 240
candles, ATR percentile over 240 candles, last settled funding rate, hour.
Rules of one or two thresholds (training deciles) are scored by F1 against
the trader's entries on the training window, longs and shorts separately.
The best rule is traded alone on the test window (next candle's open in,
held for the trader's median holding time, taker fee + half spread each
way). "Doesn't reproduce their edge" is a valid result.

### Jobs and cache (`src/review/jobs.py`)

A run is identified by (trader, venue, window, assumptions hash, data
version) and stored in `tr_backtest_runs`; the same inputs reuse the stored
result. New fills or a changed assumption make a new run.
