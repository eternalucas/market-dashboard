# Trade Review v2: plan (approved 26 Oct 2026)

Scope: the attached brief. Venue findings are in [`docs/venues.md`](venues.md). Nothing below is built yet.

## 1. What exists today (read 26 Oct 2026)

| Piece | Where | Notes |
|---|---|---|
| Trader pick | `pipeline.select_trader` | Seeded random draw from the Hyperliquid leaderboard: accounts between $10k and $100k, profitable all-time and this month, active this month. Bots are skipped by holding time and trades per day. |
| Fills | `collectors/hyperliquid.fills` → `data/hyperliquid/<addr>_fills.csv` | Incremental. Capped at 10,000 fills. |
| Round trips | `indicators/trades.round_trips` | Uses `startPosition`. Handles opens from flat, adds, partial closes and flips. Skips positions already open when history starts. |
| Stats, buckets, mistakes → rules | `indicators/trades.py` | 7 rules: losers held too long, sizing up after a loss, averaging down, losing market, losing time of day, fees eating the edge, overtrading days. |
| UI | `app.py` → `tab_review` | Metrics, equity and drawdown, rules table, breakdowns, hold-vs-result scatter, recent trips. |
| Tests | `tests/test_phase2.py` | Open, add, close and flip; losing-coin rule. |

**Problems in the current engine, fixed in Phase 1 and covered by tests:**
1. On a flip, the whole fee goes to the closing trip. It should be split between the closing and opening parts by quantity.
2. Funding is ignored, so "net" excludes funding paid or received.
3. A wallet's liquidations aren't detected. Hyperliquid fills carry an undocumented `liquidation` object, which I confirmed on live data.
4. Positions that were already open when history starts are silently dropped. They should be counted and shown as excluded.

The current single-trader review stays as the trader detail page. The seeded random draw stays as a "Random draw" option on the leaderboard.

## 2. Files

New files:

```
src/venues/__init__.py      registry: VENUES = {"hyperliquid": ..., "nado": ..., "variational": ...}
src/venues/base.py          Adapter base class, Capabilities flags, rate limiter with exponential backoff + jitter,
                            env-var secrets (none needed today; hook for later keys)
src/venues/hyperliquid.py   info endpoint + unofficial stats leaderboard (marked fragile)
src/venues/nado.py          archive + gateway; bytes32 subaccounts                      (Phase 4)
src/venues/variational.py   market stats + Omni CSV import                              (Phase 4)
src/venues/later.py         Lighter, Paradex, Aster, edgeX: capability stubs, all flags False until checked
src/review/store.py         schema below + upserts + sync cursors
src/review/reconstruct.py   fills -> round trips (fee split, funding, liquidation, truncation)
src/review/equity.py        daily mark-to-market equity, ROE, Sharpe/Sortino, drawdown, weekly hit rate
src/review/leaderboard.py   candidate pool -> eligibility -> market-maker detection -> flags -> Top 100
src/review/diagnosis.py     fingerprint, execution, risk (MAE/MFE), sizing, edge decomposition, "what they do right"
src/review/costs.py         per-venue fee tiers, hourly funding, spread + size impact, margin/liquidation rules
src/review/replay.py        counterfactual engine (entries fixed, one variable changed)
src/review/validation.py    70/30 split, block bootstrap, deflated Sharpe, confidence labels, persistence test
src/review/rules.py         rule extraction (experimental; small threshold search, no new library)
src/review/jobs.py          background backtest jobs with progress; cache by (trader, venue, window, config hash, data version)
src/ui_review.py            Leaderboard page, trader page (Review | Backtest & Improve | Data quality), batch insights
docs/trade-review-methodology.md   every formula and assumption, written as each part is built
tests/test_review_*.py      see section 6
```

Files changed:
- `app.py`: the Trade review tab calls `ui_review.render(con, cfg)`.
- `src/recorder.py`: new jobs for the daily leaderboard snapshot, account refresh and recording 1m candles for traded coins.
- `src/pipeline.py`: `update_trade_review` keeps working, but writes to the new tables as well as the CSV.
- `config.example.yaml`: new `trade_review` keys for eligibility thresholds, default fees and slippage, bootstrap settings.
- `src/indicators/trades.py`: kept as is. Its public functions call the new engine, so existing callers and tests keep passing.

## 3. Schema (new SQLite tables only; nothing existing changes)

Every row has `venue` and `account_id`. All tables are created with `CREATE TABLE IF NOT EXISTS`, so the change is additive and reversible: drop the new tables and everything is back to today.

| Table | Key | Main columns |
|---|---|---|
| `tr_accounts` | venue, account_id | owner_address, subaccount_name, label, first_seen, is_vault_or_protocol |
| `tr_fills` | venue, account_id, fill_id | ts, coin, side, px, sz, start_position, closed_pnl, fee, fee_token, builder_fee, is_taker, order_id, liq_role (liquidated / counterparty), liq_mark_px |
| `tr_funding` | venue, account_id, ts, coin | usdc, position_size, rate |
| `tr_liquidations` | venue, account_id, ts, coin | size, px, mark_px, method, fill_id |
| `tr_ledger` | venue, account_id, ts, ref | kind (deposit/withdraw/transfer), usdc. Needed for return on equity. |
| `tr_account_snapshots` | venue, account_id, ts | account_value, margin_used, positions_json |
| `tr_round_trips` | venue, account_id, trip_id | coin, side, open_ts, close_ts, qty/notional/avg prices, realized_pnl, fees, funding, net, fills, taker_fills, adds, added_while_losing, liquidated, is_open, engine_version |
| `tr_candles` | venue, coin, interval, ts | o, h, l, c, v |
| `tr_market_funding` | venue, coin, ts | rate |
| `tr_sync_state` | venue, account_id, dataset | cursor, last_ok, history_truncated, earliest_ts, notes |
| `tr_leaderboard_snapshots` | venue, window, as_of, account_id | rank, metrics_json. Daily, so the persistence test has a real history. |
| `tr_backtest_runs` | run_id | venue, account_id, window, config_hash, data_version, status, progress, started, finished, result_json |

**Database size:** the database is already ~430 MB.
- Fills for 100 traders add up to about 120 MB.
- 1m candles for ~40 coins add about 150 MB a month. I'll keep 1m candles for 180 days and coarser candles forever.
- Fills from wallets screened out of the Top 100 are dropped after screening.

## 4. Methodology choices to sign off on

**Leaderboard (Hyperliquid first)**
- **Candidate pool:** the top 300 by PnL in each window from the stats file. 90d has no stats window, so its pool is the union of the month and all-time pools. The pool is only used to choose who to fetch; **every number shown is recomputed from our own fills, funding and ledger.**
- **Metrics:**
  - PnL = realized + funding − fees + change in unrealized, marked daily at the close.
  - Return on equity uses time-weighted returns, so deposits and withdrawals don't count as performance.
  - Sharpe and Sortino use daily returns, annualized with √365.
  - Max drawdown is on equity; % of profitable weeks.
  - Plus every metric the current trader view shows.
- **Eligibility (all configurable and shown in the UI):**
  - At least 50 round trips and at least 10 active days.
  - Market maker / vault / protocol if any of these hold:
    - More than 2,000 fills a day.
    - Maker share above 80% and median hold under 1 minute.
    - Two-sided quoting: buys and sells of the same coin within 1 s on more than 30% of fills.
    - A known vault or protocol address (Hyperliquid's vault list).
  - **Flagged:** a single trade is more than 50% of PnL; history truncated; window only partly covered.
- **Cross-venue identity:** linked only when the 20-byte owner address is identical and the wallet signed on both venues. Nado subaccounts are linked by their owner address. Nothing is inferred from behaviour.

**Backtest & Improve**
- **Candles:** each trade uses the finest interval available at that point in time: 1m for the last ~3.5 days and anything we've recorded, then 5m, 15m, 1h. The resolution used is shown per trade and in the data-quality panel. Coarser candles make the "stop fills first" rule more pessimistic; that is shown, not hidden.
- **Golden test:** a replay with no changes uses actual fills, fees and funding. It must match actual net PnL within 1%; if it doesn't, nothing else ships. A second check replays exits from candles alone and reports that error separately.
- **Costs:**
  - Hyperliquid: the trader's own tier from `userFees`, falling back to the base schedule.
  - Nado: fees from `matches` plus the sequencer fee.
  - Variational: zero fees, spread taken from market stats.
  - Funding: hourly, from actual history.
  - Slippage: half-spread plus impact = k × √(order notional / average 1-minute volume). k is editable, and a default is calibrated on fills that have both a candle and a recorded book.
- **Liquidation:** isolated-equivalent check using the venue's maintenance margin table and the trader's actual leverage at entry (position notional ÷ account value then). This is an approximation for cross-margin accounts, and it's labelled.
- **Validation:**
  - Round trips are split by entry time, 70/30. All parameter and selection choices are made on the first 70% only.
  - Out-of-sample results are shown first.
  - Every PnL and Sharpe change gets a paired stationary block bootstrap: 2,000 resamples of daily PnL, mean block 5 days, 90% intervals.
  - Deflated Sharpe ratio (Bailey & López de Prado) with N = every variant tried, and the count shown.
  - **Confidence label:**
    - High: the interval excludes 0, deflated Sharpe ≥ 0.95, and ≥ 50 test trips.
    - Medium: the interval excludes 0.
    - Low: anything else.
- **Small samples:** fewer than 30 round trips shows diagnosis only, with improvement claims switched off.
- **Survivorship and persistence:**
  - Daily leaderboard snapshots start now.
  - Until two windows have passed, the test ranks the wider pool on the previous 30 days and measures the next 30 days. The pool's own bias is stated.
- **Rule extraction (experimental):**
  - Features at entry: returns over 5, 15, 60 and 240 bars, ATR percentile, funding, hour, side.
  - Search: rules of up to 2 thresholds, fit on train, tested on test.
  - Uses no new library. "Didn't reproduce their edge" is reported as a valid result.

**UX:**
- "Run Backtest" starts a background job with a progress bar, and results are cached.
- An assumptions panel controls fees, slippage bps, stop types and the impact k.
- Every simulated number carries a "Simulated" badge, and actual results are never overwritten.

## 5. Phases (tests and a short summary after each)

| Phase | Builds | Done when |
|---|---|---|
| 1 | Adapter base, Hyperliquid adapter, schema, reconstruction fixes, equity metrics, Top 100 | 100 eligible Hyperliquid traders load. Reconstruction tests pass. The existing review still works. |
| 2 | Diagnosis: all areas in the brief, including "what they do right", plus the data-quality panel | Diagnosis renders for 3 sample traders |
| 3 | Costs, replay engine, validation, rule extraction, jobs, assumptions panel | Golden test within 1%. Lookahead test passes. Full backtest on 3 traders, with screenshots. |
| 4 | Step 0 for other DEXes with open per-trader data, then their adapters (Nado and Variational excluded) | Their traders appear on the board, or the findings explain why not |
| 5 | Batch backtest across the Top 100 → "most common mistakes" | Batch completes; results are cached |

**First-run time on Hyperliquid:** about 300–600 candidate wallets. At the documented rate limit that's roughly 30–90 minutes in the background on the first run. After that it's incremental, with a daily refresh.

## 6. Tests

- **Reconstruction:**
  - partial closes
  - a long flipping to a short in one fill, with the fee split
  - scale-ins
  - adds while losing
  - history starting mid-position (excluded and counted)
- **Attribution:**
  - fees, including the builder fee
  - funding assigned to the right trip by coin and time
  - liquidation as the liquidated wallet vs the counterparty
- **Golden:** a no-change replay equals actual net PnL within 1%, on synthetic data and on 3 real wallets.
- **Lookahead:** change prices after time t and assert that every signal and decision before t is unchanged.
- **Validation:**
  - the bootstrap covers a known mean in simulation
  - the deflated Sharpe falls as N rises
  - the 70/30 split never leaks
- **Adapters:** recorded fixtures plus pagination and backoff behaviour.
- **End to end:** Top 100 loads; a backtest on 3 traders; screenshots.

## 7. Decisions (26 Oct 2026)

- **Hyperliquid history:** free data only. Wallets with more than 10,000 fills are flagged "history truncated", and their windows are limited to the period actually covered. No paid source.
- **Nado and Variational:** excluded. Phase 4 becomes "other DEXes whose per-trader data is as openly available as Hyperliquid's". Candidates to check (Step 0, before building): dYdX v4 (public indexer), Drift, Lighter and others. I'll report findings and ask before building.
- **Plan approved;** Phase 1 started.
