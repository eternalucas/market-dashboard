# Market Dashboard — Audit Report

> **Status 3 Nov 2026 (Phase 3/4):** all 22 approved IDs fixed (17 High + A1-02, A1-03, A3-03, M-03, E-04), plus A2-02 (real rate) and per-group Social Risk coverage at the owner's request. Each fix has a regression test that fails on the pre-fix code (tests/test_audit_fixes_F1..F5.py); full suite 236 passed; Mac files hash-identical to the tested tree. The 323 fake test rows were deleted from data/market.db (backup in .backup-2026-11-03-audit/). Live re-verification pending a dashboard restart.

Prepared 2 Nov 2026. **Read-only audit: nothing in this report has been fixed yet.** Fix only the IDs you approve (Phase 3).

Separate from this report, you asked for 12 UI changes on 2 Nov (header, Data health, chart labels, Macro lines, Lighter/GMX/tokenized removal, Social Risk inputs, reset icon). Those are already live on your Mac and are not part of the findings below.

## 1. Summary

- **90 findings** from 7 review threads, plus 4 from the main session (M-01…M-04). 1 claim was refuted (B-04).
- **Counts by severity:** 17 High, 34 Medium, 29 Low, 8 Info.
- **What's solid (verified against primary sources):**
  - **Coinalyze daily data:** close, funding, liquidations and OI match exactly for 24 Oct–1 Nov.
  - **Funding normalisation to 8h:** exact on 255 venue payments (1h, 4h and 8h contracts).
  - **Other external sources:** Coin Metrics, CFTC (both reports), DVOL, Fear & Greed, blockchain.com, Wikimedia and DefiLlama are exact at the sampled dates.
  - **FRED:** all levels match exactly. cpi_yoy and payrolls_chg also match exactly, and so do 104 of 150 A2 specs and 77 A1 specs.
  - **Positioning tab live (18:00–18:13Z):** OI is within 0.04% per venue, the total of $15.96bn sums correctly, and OI-weighted 8h funding recomputes exactly.
  - **Hyperliquid fills:** per-day closedPnl, fee and notional match fresh Hyperliquid pulls exactly on every fully covered day for 4 traders.
  - **BTC wallets:** 89 of 92 balances match to the satoshi.
- **Highest-priority problems (fixed first):**
  1. **Stale numbers presented as current.** Prices, event prices, funding/OI history and Hyperliquid cohorts only update on the manual button (B-01). On-chain "Value held at today's prices" uses 26 Oct prices (A3-04) and includes about $460m of phantom ETH from wallets that stopped updating (A3-05). The sidebar text claims the recorder refreshes everything (M-01).
  2. **Wrong formulas:**
     - Core CPI YoY counts back 12 rows instead of 12 months, overstating it by up to 0.44pp since Nov 2025 (A2-01).
     - The Flows tab double-counts Bybit OI (A3-01) and shows funding 100× too high when rates are low (A3-02).
     - The order-book delta chart sums hourly values instead of averaging them (A1-01).
     - Today's partial day is shown as a full day (A1-02).
  3. **Trade review:**
     - Same-millisecond fill ordering creates false position gaps and misallocates P&L (A4-01).
     - ROE ignores deposits and withdrawals (A4-02).
     - Truncated histories are ranked as if complete (A4-03).
  4. **Pipeline honesty:**
     - Failures are logged as success (B-03, B-08).
     - There is no backoff on HTTP 429 (B-06).
     - The 28 Oct batch job is stuck "running" and permanently disables its button (B-11).
     - The test suite writes fake rows into your real database: there are 323 such rows in it now (C-01).

## 2. High-severity findings and reproduction status

Full detail for every finding (Evidence / Repro / Root cause / Proposed fix / Confidence) is in `docs/audit/findings/<agent>.md`. Rows below are one-line digests.

| ID | Sev | Area | file:line | What's wrong | Reproduced by main session |
|---|---|---|---|---|---|
| A1-01 | High | Order book, 24h view | `src/charts.py:198` | At the default 24h resolution, `ob_delta` (bars_pn, usd) sums the hourly mean depth-deltas, so the daily bar is N× the real bid−ask depth. The multi charts (depth, spread, impact, imbalance, slope, perp depth) show the last hour o… | ✅ reproduced (charts.py:198 sums hourly ob_delta for bars/usd) |
| A2-01 | High | US core CPI YoY (`macro_core_infl`), `macro_real_rate` | `src/metrics/markets.py `yoy()` (`s.shift(12)`)` | FRED has no CPILFESL value for Oct 2025. `shift(12)` counts rows, not months, so every month from Nov 2025 to Sep 2026 (11 points) is compared with the month 13 months earlier. The YoY is overstated by +0.07 to +0.44 pp. The real … | ✅ reproduced: dashboard 2.789 vs true 12-month YoY 2.566 (Sep 2026) |
| A2-03 | High | Lightning: `ln_capacity`, `ln_nodes`, `ln_channels`, `ln_channel_size`, `ln_node_capacity`, `ln_*_change` | `src/metrics/onchain.py `lightning`` | The `mp:ln:*` history ends 2026-08-30, although `src:mempool_hist` logs ok=1 every run up to 11-02. That is 64 days stale. These charts have one column, so the new `_ended` legend label (multi-column only, >400 d) does not fire. T… | ◐ mp:ln:* ends 2026-08-30 confirmed; upstream flip not re-checked |
| A2-08 | High | Social Risk headline (`social_risk`, `social_groups`, `social_contrib`) | `src/indicators/social_risk.py `grouped_composite`` | When a whole group drops out, its weight moves to the other group with no flag. All attention inputs end 10-29..10-31 (social source last ran 10-28). On 11-01/02 the score is speculation only: 0.370 (10-29) → 0.441 (10-30, attenti… | ◐ mechanism confirmed in code; the 0.37→0.61 jump is a snapshot artefact (live 2 Nov: attention 0.35, score 0.465) |
| A3-01 | High | Flows OI / funding vs Positioning | `src/flows.py:139-160 (`load_flow` oi), src/indicators/flow.py:36-70` | The Flows tab adds up Coinalyze's raw Bybit OI, which the main session says counts both sides. Positioning halves Bybit (`derivatives.py:35`). The two tabs show different total OI for the same hour, and Flows' OI-weighted funding … | ✅ direction confirmed: live Bybit `openInterest` 67,828.5 = 2.000× `singleOpenInterest` 33,914.3 → Flows double-counts |
| A3-02 | High | Flows funding units | `src/indicators/flow.py:47-53 (`as_percent`)` | The heuristic "if the median of \ | ✅ reproduced in code (flow.py `as_percent` ×100 when median |rate| < 0.001) |
| A3-04 | High | On-chain value held | `src/ui_onchain.py:89-92,128,136` | "Value held … at today's prices" uses `price:BTC`/`price:ETH`, which were last written 2026-10-26 (run_all only). The main session saw "Value held $239.77B" on screen. | ✅ reproduced: $239.77B = balances × price:BTC 80,201.6 / price:ETH 2,323.39 (26 Oct) |
| A3-05 | High | On-chain stale ETH balances | `src/ui_onchain.py:95-96` | The latest balance is shown with no date check. 3 ETH wallets stopped updating on 10-19/10-21 and still appear as current. Binance 14 `0x28C6…`: stored 198,128.97 ETH (10-19, the only point), while Blockscout shows 0.8033 ETH now … | ✅ reproduced: 0x28C6… stored 198,128.97 ETH (19 Oct, only point) vs Blockscout 0.8033 ETH |
| A4-01 | High | Round trips | `src/review/reconstruct.py:68-69` | Fills in the same millisecond are ordered by tid, but Hyperliquid tids aren't sequential. That creates false "position gaps", trips closed as "exit unobserved", and realized P&L wrongly excluded as "opened before history". | ◐ partly: 81 position-chain breaks for 0x58b2 under ts+tid order, 30 directly between same-ms fills; P&L impact not re-run |
| A4-02 | High | ROE | `src/review/equity.py:120-126; hyperliquid.py:129 (ledger never called)` | ROE uses the previous day's end-of-day venue account value as the denominator. Deposits and withdrawals are unknown because the ledger is never fetched (tr_ledger has 0 rows; `grep -rn ledger src` finds no caller). Same-day flows … | ◐ partly: snapshot 0x8738 30d PnL −$2,651 with ROE −90.7% on ~$14–20k equity (inconsistent); A4's +$8.5k figure is from a later recompute |
| B-01 | High | Scheduling / coverage | `pipeline.py run_all steps (update_prices, update_positioning_history, ` | Several series families have no scheduled writer. The recorder's JOBS list never calls them; they are written only by "Update all data" (`pipeline.run_all`), or for the viewed asset only by the Positioning tab (`app.py live_positi… | ✅ reproduced (pipeline.run_all-only writers; price:BTC last 2026-10-26 in live DB at 18:07Z) |
| B-02 | High | Health UI says "working" for stale data | `views.py:195-228 (new); app.py:128` | (a) The new source_health marks a source Stale only when it has had no *attempt* for 48 h. A source that returns ok with 0 rows, or one whose own freshness window keeps it from running, still shows OK. (b) The global "data is old"… | ✅ reproduced (old health view; new view addresses part, see M-03) |
| B-03 | High | Error handling: total failure logged as success | `registry.py:57-58; fred_macro.update; blockchain_com.update; social.up` | `rows = fn() or 0; log_fetch(ok=True)`. Sources that catch their own per-item errors return 0 rows, and the registry then logs `src:X ok=1`. That success resets the freshness window, so the source is not retried for 20 h. | ✅ reproduced from fetch_log rows quoted in B.md |
| B-05 | High | 'database is locked' (src:social) | `storage.py:117 (timeout=30, WAL, no retry); social.py:67-74` | `_log` catches the fetch error and then calls `log_fetch`, which also waits 30 s and raises. That exception escapes `_log` and aborts the rest of `social.update` (app store, YouTube, GDELT, HN, Trends skipped). Writers on the same… | ◐ code path confirmed; lock holder on 27 Oct unknown |
| B-06 | High | Rate limits: no 429 handling in the shared HTTP helper | `collectors/http.py:17-25; social.py update_wikipedia` | `get_json` retries only on Timeout. A 429 raises at once and Retry-After is ignored. The Wikipedia loop has no sleep, so the remaining items all 429 in a burst. Failed items have no cooldown and retry every hour. GDELT went 9 of 1… | ✅ fetch_log shows ~40 Wikimedia 429s within ~1.5 s (17:53Z today) |
| B-11 | High | Background jobs: stuck 'running' disables the UI | `jobs.py:58-76; batch.py:35-80; ui_batch.py:35-38,53` | Status lives only in the DB. After a restart the thread is gone (daemon) but the row stays 'running'. There is no heartbeat or stale check. `_latest()` picks the newest batch row, and the "Run on the Top 100" button is `disabled=r… | ✅ tr_backtest_runs batch-2026-10-28-7077a05bce status running, 13/100 |
| C-01 | High | Test isolation | `tests/test_recorder.py:13,110-120; src/storage.py:20` | In the full suite, `test_job_books_idempotent` writes synthetic books (mid 80000, ts 2026-10-21 12:10) into the real `data/market.db`. Its `COUNT(*)==3` assert then fails whenever the real DB already holds book data. | ✅ reproduced on your live DB: 3 fake book_metrics rows + 320 heatmap rows at 2026-10-21 12:10 (mid exactly 80,000) |

### Main-session findings

| ID | Sev | Area | file:line | What's wrong | Evidence |
|---|---|---|---|---|---|
| M-01 | High | Sidebar honesty | `app.py` sidebar caption | It says "the recorder refreshes every source on its own". False: see B-01. | Live 18:07Z: price:BTC last 2026-10-26 while src:yahoo ran OK at 17:43 |
| M-02 | Low | Data health lag | `recorder.py` `Recorder.ok()` | A successful job is logged at most hourly, so "Updated" can lag by up to 1 h (now explained on the page). | rec:book:* logged once at 17:42 while book_metrics had 34 samples by 18:15 |
| M-03 | Medium | Data health (new) | `views.source_health` | "Stale" is based on the last attempt. A source that runs but returns 0 rows or old data stays "OK" (interacts with B-03). | Code reading. Fix together with B-03 |
| M-04 | Info | HL fill history | `venues/hyperliquid.py` FILLS_CAP | Hyperliquid served 35,491 unaggregated fills for 0x2eaca over 1–27 Oct. The "10,000 most recent" limit needs re-checking (see A4-03). | refs/REFC3.json |

## 3. All findings (index)

| ID | Sev | Area | file:line | What's wrong (digest) |
|---|---|---|---|---|
| A1-01 | High | Order book, 24h view | `src/charts.py:198` | At the default 24h resolution, `ob_delta` (bars_pn, usd) sums the hourly mean depth-deltas, so the daily bar is N× the real bid−ask depth. The multi charts (depth, spread, impact, imbalance, slope, perp depth) show the last hour o… |
| A1-02 | Medium | Partial last bar | `src/metrics/spot.py:30,247; futures.py:82; markets.py:73; charts.lates` | Today's partial UTC day (11-02, data up to ~17:00Z) is drawn and headlined as a complete day. Affects every daily volume/OHLC spec. |
| A1-03 | Medium | Staleness not shown | `src/ui_studio.py:80-90; src/charts.py:241` | The panel headline shows the latest value with no as-of date. The new "last data" marker only fires at >400 days. Stale inputs show no warning: czd BTC-PERP.C/ETH-PERP.C (stopped 2026-10-01, 32 d), XBT_USDT.0 (2026-09-21, 43 d), B… |
| A1-04 | Medium | Funding methodology | `src/sources/coinalyze_daily.py:73; futures.py:112` | `fut_funding` uses Coinalyze's daily close funding (one point sample at day end), not the day's mean. The spec says "funding rate" daily, while Glassnode averages it. |
| A1-05 | Low | Funding interval set | `src/metrics/futures.py:14` | Lead result. No stored perp clusters at the 4h level (0.005). Every non-×8 venue with a non-zero baseline clusters at 0.01, which is 8h, so no 4h understatement is provable. But Aster is in HOURLY_FUNDING while its fr clusters at … |
| A1-06 | Low | Liquidations (Mean) | `src/metrics/futures.py:153` | `n` = exchanges with a row that day. Coinalyze omits zero/missing days (Bitfinex 257, Huobi 187, BTCPERP.6 438 missing days), and Deribit liquidations stop 2023-10-02. So the divisor flips 1↔2↔3 day to day, and "mean per exchange"… |
| A1-07 | Low | Coverage changes / truncation | `futures.py:42` | OI-based specs (fut_oi, ratios, leverage, funding weighting) start 2022-09-08, although fr/vol raw history goes back to 2019-20, because Coinalyze daily OI starts there. Contracts enter and exit the sum: BTC.H on 2024-10-10 (+1.6%… |
| A1-08 | Low | Coinbase hourly gap | `src/sources/coinbase_hourly.py:33` | 2021-03-01..03-24 (24 days, 576 h) are missing. 654 hours missing in total. On a failed or empty response the cursor still advances, so the hole is never back-filled. Affects ohlc, drawdown ATH (the Mar-2021 intraday high is missi… |
| A1-09 | Low | Bad tick | `cb1h store` | 2017-04-15 23:00 bar has open=low=$0.06 (close $1,178.85). The ohlc daily candle and the ADL for that day use it, and it shows as a spike to $0.06 on the log OHLC chart. |
| A1-10 | Low | Binance quarterly gaps | `bq:BTC:next_quarter` | 306 missing days (current_quarter is complete). Rolling basis falls back to the near contract on those days, which shifts the effective tenor away from 90 d. |
| A1-11 | Low | Period returns partial month | `spot.py:145` | The current calendar month (Nov 2026, 1 day of data) is drawn as a full monthly return bar. |
| A1-12 | Info | Correlation horizon mismatch | `markets.py:155` | `tradfi_corr` aligns BTC 1-day returns with Yahoo trading-day returns. A Monday stock return covers Fri→Mon while the BTC return covers Sun→Mon. |
| A1-13 | Info | DAT volume units | `markets.py:104, catalog dat_volume (unit usd)` | 3350.T (Metaplanet) price×volume is in JPY but is stacked with USD on a "usd" axis. |
| A2-01 | High | US core CPI YoY (`macro_core_infl`), `macro_real_rate` | `src/metrics/markets.py `yoy()` (`s.shift(12)`)` | FRED has no CPILFESL value for Oct 2025. `shift(12)` counts rows, not months, so every month from Nov 2025 to Sep 2026 (11 points) is compared with the month 13 months earlier. The YoY is overstated by +0.07 to +0.44 pp. The real … |
| A2-02 | Med | `macro_real_rate` | `src/metrics/extra.py `real_policy_rate` `.ffill(limit=2)`` | The Oct 2026 row repeats Sep CPI (1.2108, same as Sep), so it looks like a fresh reading. The monthly CPI values sit at the reference month (Sep 1), about 6 weeks before the release date, which is look-ahead when read against BTC … |
| A2-03 | High | Lightning: `ln_capacity`, `ln_nodes`, `ln_channels`, `ln_channel_size`, `ln_node_capacity`, `ln_*_change` | `src/metrics/onchain.py `lightning`` | The `mp:ln:*` history ends 2026-08-30, although `src:mempool_hist` logs ok=1 every run up to 11-02. That is 64 days stale. These charts have one column, so the new `_ended` legend label (multi-column only, >400 d) does not fire. T… |
| A2-04 | Med | `ln_fees` | `onchain.py `lightning` (`out[k] = s`)` | The chart is empty. `ln:med_fee_rate` and `ln:med_base_fee_mtokens` (6 points, 10-21..11-02) are assigned into a frame indexed by `mp:ln` dates, which end 08-30, so every point is dropped. |
| A2-05 | Med | `total_mcap`, `altcoin_mcap`, `alt_btc_ratio` | `src/metrics/spot.py `btc_dominance`; src/sources/bgeometrics.py `parse` | (a) 120 BGeometrics dominance points (2022-10-27..2023-06-22) are stamped 01:00/02:00 UTC (CET/CEST-shaped). They never match Coin Metrics' 00:00 index, so these charts start 2023-06-23 and lose 8 months. (b) The CoinGecko extensi… |
| A2-06 | Med | `cost_basis_models` | `onchain.py `cost_basis_models`; bgeometrics.py PRIORITY` | Only 2 of the 4 lines in the title render. `delta-cap` has returned HTTP 500 on all 4 attempts, so `delta_price` is absent. `thermo-price` has never been fetched (still queued), so `thermo_price` is all-NaN. `cols()` drops them si… |
| A2-07 | Med | All BG specs labelled "7-day delayed" | `catalog.py `BG` label; bgeometrics.py queue` | The real lag at the snapshot is 8 to 19 days: sth/lth MVRV, SOPR and NUPL end 10-14; nrpl-usd ends 10-16. The first batch (fetched 10-21) has not been refreshed because never-fetched endpoints take queue priority. |
| A2-08 | High | Social Risk headline (`social_risk`, `social_groups`, `social_contrib`) | `src/indicators/social_risk.py `grouped_composite`` | When a whole group drops out, its weight moves to the other group with no flag. All attention inputs end 10-29..10-31 (social source last ran 10-28). On 11-01/02 the score is speculation only: 0.370 (10-29) → 0.441 (10-30, attenti… |
| A2-09 | Low | `taker_buys`, `leverage` (new) | `social_risk.py:287-300; views.py:66` | Both are formula-correct (A2-V1 below). On 11-02 taker_buys uses Coinalyze's partial-day bar (README caveat), and leverage divides the 11-02 OI by the 11-01 mcap carried forward (`to_daily(mcap, 3)`). The two inputs are out of syn… |
| A2-10 | Low | `nvts` | `onchain.py `nvt` (`sma(v, 90)` by rows)` | The repo produces 634 more points than a calendar-day 90-day SMA. Values agree where both exist, but early bc gaps (up to 15 days in 2011) let the row window span more than 90 days. Recent data is unaffected. |
| A2-11 | Low | `macro_activity` | `markets.py `macro`` | `USALOLITONOSTSAM` and `CSCICP03EZM665S` are discontinued on FRED: the last data is 2024-01. The new `_ended` label now covers this (multi-column, >400 d). |
| A2-12 | Info | `realized-cap-hodl-waves` age_10y / age_7y_10y | `raw` | The last 57 and 55 stored values are identical. Possibly BG holding the value; I could not check this without a reference. UNVERIFIED. |
| A3-01 | High | Flows OI / funding vs Positioning | `src/flows.py:139-160 (`load_flow` oi), src/indicators/flow.py:36-70` | The Flows tab adds up Coinalyze's raw Bybit OI, which the main session says counts both sides. Positioning halves Bybit (`derivatives.py:35`). The two tabs show different total OI for the same hour, and Flows' OI-weighted funding … |
| A3-02 | High | Flows funding units | `src/indicators/flow.py:47-53 (`as_percent`)` | The heuristic "if the median of \ |
| A3-03 | Medium | Flows funding interval | `src/indicators/flow.py:13,62-63` | Only Hyperliquid is rescaled (×8). The 4h contracts (Binance HYPE/ENA, Bybit ENA) are shown per 4h but labelled "% per 8h", so they are understated ×2. Positioning handles these contracts correctly. |
| A3-04 | High | On-chain value held | `src/ui_onchain.py:89-92,128,136` | "Value held … at today's prices" uses `price:BTC`/`price:ETH`, which were last written 2026-10-26 (run_all only). The main session saw "Value held $239.77B" on screen. |
| A3-05 | High | On-chain stale ETH balances | `src/ui_onchain.py:95-96` | The latest balance is shown with no date check. 3 ETH wallets stopped updating on 10-19/10-21 and still appear as current. Binance 14 `0x28C6…`: stored 198,128.97 ETH (10-19, the only point), while Blockscout shows 0.8033 ETH now … |
| A3-06 | Medium | Positioning history freshness | `app.py:247-285` | The funding history and OI history cards show data ending 10-26 (run_all only) with no age label. The "last 60 days" window is anchored on `s.index.max()`, not on now. "Latest Binance payment: x SD" refers to the 10-26 payment. |
| A3-07 | Low | Sparse snapshot series | `src/pipeline.py:143-201, app.py:320-346` | `funding_8h/oi_usd/basis/agg_*/skew_rr25/atm_iv/options_net_delta/depth_*` are written only when someone opens the Positioning tab (via `live_positioning`) or by run_all. Nothing records them on a schedule. BTC (the default asset)… |
| A3-08 | Medium | Actual liquidation dots | `src/sources/liquidation_feeds.py:243-250` | 0 Binance rows in `liquidations`, although the UI says the dots come from Bybit, Binance and OKX. Separately, Bybit captured ~0.1–15% of Coinalyze's daily Bybit liquidations on overlap days (the recorder ran only a few hours). |
| A3-09 | Low | HL coverage caption | `src/ui_flows.py:382-387` | The caption shows "covering X% … in the latest scan" from `hl_liq_coverage` with no date. The last scan is 10-28 23:15 (28.4%), and it is shown as current. Also, coverage counts `hl_positions` rows up to 24h old (liquidation_feeds… |
| A3-10 | Low | Event study start point | `src/events.py:172-177, app.py:422` | "Day 1 = event day, measured from the previous close" uses daily closes, so the window includes the hours before the release on the release day. This is not look-ahead, but the move is not "after" the release time. `past = date <=… |
| A3-11 | Info | REFD_CZ README | `refs/README.md` | The README says the range ends 2026-11-01. The last t=1793577600 is 2026-11-02 (10 days starting 10-24). |
| A4-01 | High | Round trips | `src/review/reconstruct.py:68-69` | Fills in the same millisecond are ordered by tid, but Hyperliquid tids aren't sequential. That creates false "position gaps", trips closed as "exit unobserved", and realized P&L wrongly excluded as "opened before history". |
| A4-02 | High | ROE | `src/review/equity.py:120-126; hyperliquid.py:129 (ledger never called)` | ROE uses the previous day's end-of-day venue account value as the denominator. Deposits and withdrawals are unknown because the ledger is never fetched (tr_ledger has 0 rows; `grep -rn ledger src` finds no caller). Same-day flows … |
| A4-03 | Med | Truncation | `leaderboard.py:163-164, 350-366; hyperliquid.py:6-7, 28` | 4 of 5 traders are flagged history_truncated (volume coverage 15–50%) but are still eligible=1 and ranked by PnL, Sharpe and so on as if complete. Only a text flag is shown. The "10,000 most recent fills" premise conflicts with HL… |
| A4-04 | Low | Persistence | `equity.py:23,137-140` | "% profitable weeks" for 7d always spans 2 partial W-SUN weeks, so it is close to meaningless. 0x61ce 7d shows 100% with 1 active day. |
| A4-05 | Low | Storage | `leaderboard.py:412-414` | tr_round_trips has no exit_unobserved column, so stored is_open=0 rows mix real and unobserved exits. |
| A4-06 | Low | Fees | `reconstruct.py:10 vs code (uses `fee` only); equity.py:43,54` | The docstring says builder fee is included, but builder_fee is never added. Impact is $103.10 (0xc30c, 5 fills). Whether HL's `fee` already includes builderFee is UNVERIFIED. |
| A4-07 | Info | Reproducibility | `store.py:133 (INSERT OR REPLACE candles)` | Stored tr_metrics can't be reproduced exactly: daily candles were rewritten on 2 Nov, so the as_of day's then in-progress close is gone. Stored-vs-recomputed PnL offsets are constant per trader (+4,663 / −105,702 / +90,395 / +106,… |
| B-01 | High | Scheduling / coverage | `pipeline.py run_all steps (update_prices, update_positioning_history, ` | Several series families have no scheduled writer. The recorder's JOBS list never calls them; they are written only by "Update all data" (`pipeline.run_all`), or for the viewed asset only by the Positioning tab (`app.py live_positi… |
| B-02 | High | Health UI says "working" for stale data | `views.py:195-228 (new); app.py:128` | (a) The new source_health marks a source Stale only when it has had no *attempt* for 48 h. A source that returns ok with 0 rows, or one whose own freshness window keeps it from running, still shows OK. (b) The global "data is old"… |
| B-03 | High | Error handling: total failure logged as success | `registry.py:57-58; fred_macro.update; blockchain_com.update; social.up` | `rows = fn() or 0; log_fetch(ok=True)`. Sources that catch their own per-item errors return 0 rows, and the registry then logs `src:X ok=1`. That success resets the freshness window, so the source is not retried for 20 h. |
| B-04 | Refuted | Premise check: "onchain eth Binance logged ok with TypeError" | `pipeline.py _fetch_wallet / update_onchain` | REFUTED on the snapshot. The TypeError row was logged ok=0. No ok=1 row contains an error string except rec:liq_feeds (see B-08). |
| B-05 | High | 'database is locked' (src:social) | `storage.py:117 (timeout=30, WAL, no retry); social.py:67-74` | `_log` catches the fetch error and then calls `log_fetch`, which also waits 30 s and raises. That exception escapes `_log` and aborts the rest of `social.update` (app store, YouTube, GDELT, HN, Trends skipped). Writers on the same… |
| B-06 | High | Rate limits: no 429 handling in the shared HTTP helper | `collectors/http.py:17-25; social.py update_wikipedia` | `get_json` retries only on Timeout. A 429 raises at once and Retry-After is ignored. The Wikipedia loop has no sleep, so the remaining items all 429 in a burst. Failed items have no cooldown and retry every hour. GDELT went 9 of 1… |
| B-07 | Medium | Rate limits: Hyperliquid budget not shared | `venues/hyperliquid.py:27 (LIMITER 800); liquidation_feeds.py:53,67,89;` | The review limiter (800/min, per process) and the scan limiter (300/min, new instance per scan) are independent. books l2Book every minute and the UI's metaAndAssetCtxs/fundingHistory calls go through post_json with no metering. W… |
| B-08 | Medium | Partial or false success: liquidation feeds | `recorder.py job_liq_feeds; liquidation_feeds.py:196, 271` | `counts` is incremented in `_put` before INSERT OR IGNORE dedupe, so re-polled OKX rows are counted again. OKX errors are swallowed with `except: pass`. The Binance websocket stored 0 rows ever, yet rec:liq_feeds is logged ok=1 ev… |
| B-09 | Medium | Rate limits: Coinalyze pacer per Client | `flows.py:64 client_for` | Each call builds a new `Client` with its own 38/min deque. The recorder's coinalyze_daily, run_all's flows thread and the UI's ui_flows update_flow can run at the same time, each allowed 38/min against a 40/min key limit. 429s are… |
| B-10 | Medium | BGeometrics budget: race and dead endpoint | `bgeometrics.py:62-106; registry.py:63` | The budget is counted from fetch_log, so it survives restarts (good). But it is read before any call: two concurrent `update()` calls (recorder sources thread plus "Update all data") both see 7 left and spend 14 in one hour (the f… |
| B-11 | High | Background jobs: stuck 'running' disables the UI | `jobs.py:58-76; batch.py:35-80; ui_batch.py:35-38,53` | Status lives only in the DB. After a restart the thread is gone (daemon) but the row stays 'running'. There is no heartbeat or stale check. `_latest()` picks the newest batch row, and the "Run on the Top 100" button is `disabled=r… |
| B-12 | Medium | Daemon threads killed on exit: non-atomic files | `pipeline.py _append_csv (to_csv in place), sync_btc_wallets `state_pat` | SQLite writes are transactional, but CSV and JSON state files are rewritten in place by daemon threads that die at process exit. A truncated btc_sync.json makes `json.loads` raise on every later run. |
| B-13 | Medium | Timezone: IBIT "after close" wrong in winter | `recorder.py:166` | The gate is a fixed 20:30 UTC. After US DST ended on 2026-11-01 that is 15:30 ET, 30 min before the close. The snapshot is stamped as the day's close (`now.normalize()`). There is no US holiday calendar. |
| B-14 | Low | Timezone: local dates | `attention.py:34, social.py:109 (`date.today()`); app.py:440,445 (`pd.T` | The Wikimedia end date uses the machine's local date (Montréal, UTC-5): one day short in the evening. The events tab compares naive local "now" with stored dates. now_hour(), last_success and the registry are UTC-aware (OK). |
| B-15 | Medium | Silent degradation on upstream changes | `coinmetrics.py:60 (`except: pass`); cftc.py:28 (`pass`); yahoo.py down` | Optional parts fail with no log row. Loud cases: FRED HTML→JSONDecodeError logged ok=0 (10-21), pytrends `method_whitelist` logged ok=0 (10-16; the current code has a retries=0 workaround, attention.py:57-59), blockchain.com fees-… |
| B-16 | Low | Idempotency | `storage.py SCHEMA, save_series/save_frame (INSERT OR REPLACE); replace` | Every market table has a PK in the code SCHEMA and every write is an upsert. No duplicates exist in the data. Re-fetch overlaps: coinbase_hourly 2 h (re-reads the in-progress bar), coinalyze_daily 3 d, blockchain_com 3 d, binance_… |
| B-17 | Low | Freshness windows vs cadence | `registry.py SOURCES; recorder `sources` hourly; Recorder.ok() hourly l` | Windows (coinmetrics 6 h, most 12 h, cftc 24 h, fred_macro 20 h, coinbase_hourly/social 1 h) are checked hourly by job_sources, so a 1 h source can drift to about 2 h. Recorder successes are logged at most hourly, so "last ok" lag… |
| C-01 | High | Test isolation | `tests/test_recorder.py:13,110-120; src/storage.py:20` | In the full suite, `test_job_books_idempotent` writes synthetic books (mid 80000, ts 2026-10-21 12:10) into the real `data/market.db`. Its `COUNT(*)==3` assert then fails whenever the real DB already holds book data. |
| C-02 | Med | Test isolation | `tests/test_recorder.py:127-131; src/recorder.py:44` | `test_lock_is_exclusive` uses the real `data/recorder.lock`. It fails whenever the recorder is running on the owner's Mac, and while it holds the lock it can block the recorder from starting. |
| C-03 | Med | Test isolation | `tests/test_ui_fixes.py:129-141; src/config.py:9` | The test loads the owner's real `config.yaml`, which holds API keys, through `Ctx`. The result can depend on personal config. |
| C-04 | Med | Coverage gap | `src/indicators/positioning.py:18-20,46` | Funding annualisation and funding spread are untested: ×365 instead of ×1095, and max-only spread, both survive. |
| C-05 | Med | Coverage gap | `src/views.py:214-224` | source_health's failure semantics (NULL ok), staleness basis, and Last-error masking are unguarded. |
| C-06 | Med | Coverage gap | `src/collectors/derivatives.py:107-109` | to_8h's interval clip and first-row gap fill are unguarded. Daily-paying contracts would be silently capped at 8h. |
| C-07 | Low | Coverage gap | `src/review/reconstruct.py:132,194; src/review/replay.py:296` | Boundary conditions (`>` vs `>=` for adds, the funding window end, the breakeven time stop) are unguarded. |
| C-08 | Med | Coverage gap | `app.py, src/ui_*.py, src/pipeline.py` | app.py is never imported. 8 UI modules are at 0%, pipeline at 11%, recorder at 27%. Coinalyze unit conversion, backoff and jobs are untested. |
| C-09 | Low | Weak test | `tests/test_flow.py:77-78` | The expected net delta is computed from the function's own output (tautological). |
| C-10 | Low | Hygiene | `tests (several)` | 27–31 unclosed sqlite connections (ResourceWarning) |
| C-11 | Info | Plan accuracy | `–` | The plan's "194 tests / pipeline 0%" is out of date: now 186 tests, pipeline 11%, ui_review 13%, ui_studio 22%. |
| E-01 | Medium | Personal data | `gh-remote commits f1d92fd, 1e7d1f1` | The owner's personal Gmail address and full name are in the author metadata of the public-facing remote. Whether the repo is actually public was not checked (UNVERIFIED: that needs network access). |
| E-02 | Medium | Personal data / hygiene | `local repo, `claude-skills/` (8 files, added in 57b1313)` | `claude-skills/` is tracked locally but is not in .gitignore. It holds the owner's personal Claude skill prompts and names him (e.g. crypto-market-structure/SKILL.md:18 and :46, prompt-refiner/SKILL.md:15). The remote does not hav… |
| E-03 | Medium | Hygiene | `local vs remote history` | The local repo (47 commits, `git remote -v` empty) and the remote (6 squashed commits) share no history. A future push from local would fail or need a force-push, which would upload all 47 commits, claude-skills included. |
| E-04 | Low | .gitignore coverage | `.gitignore:1-11` | Covered: `config.yaml`, `data/` (so data/*.db too), `.venv/`, `__pycache__/`, `.pytest_cache/`. NOT covered: `claude-skills/`, `dashboard-update.zip` (or `*.zip`), `md-audit-extracts/`, and `*.db` outside data/. |
| E-05 | Low | Leftover files | `Owner's folder copy `/mnt/user-data/uploads/Documents/market-dashboard` | Four throwaway check DBs (~294 MB) sit beside the real DB: `_events_check.db` 15.8 MB, `_fundfix_test.db` 2.3 MB, `_top100_check.db` 81 MB, `_verify_snapshot.db` 194 MB. They are ignored through `data/` but waste disk. `dashboard-… |
| E-06 | Low / UNVERIFIED | Personal data | `tests/fixtures/hl_real_wallet.json (aecba86)` | The file is a real Hyperliquid wallet's complete public history (756 fills, 301 funding payments, portfolio PnL). The address is anonymised to `0x00…`, but the unique `oid`/`tid` values allow re-identification against Hyperliquid'… |
| E-07 | Info | Wallets | `watchlist.yaml (121 addresses), docs/venues.md:118-120, tests` | Every address is a publicly labelled entity (Wintermute 1-4, exchanges, rich-list ranks), a protocol contract, or a test fixture. No address is marked as the owner's. watchlist.yaml:9 ("Add your own rows") is a template comment. |
| E-08 | Medium | Dependencies | `requirements.txt:1-9; no lockfile, no pyproject` | All 9 requirements use `>=` with no upper bounds, and there is no lock or constraints file, so every install on the Mac resolves fresh. Tests ran on Python 3.13.16 with pandas 3.0.5, numpy 2.5.3, streamlit 1.66.0, plotly 7.1.0, ur… |
| E-09 | Low | Dependencies | `pytrends request.py:123-128; src/collectors/attention.py:57-59` | pytrends 4.9.2 builds `Retry(method_whitelist=…)`, a parameter urllib3 2.x removed, so it crashes whenever `retries>0` or `backoff_factor>0`. The code avoids it by passing `retries=0, backoff_factor=0`. pytrends is unmaintained, a… |
| E-10 | Medium | Docs drift / dead feature | `README.md:217-218 vs src/collectors/attention.py:71, src/config.py:11` | README says a CSV exported to `data/manual/google_trends_weekly_<term>.csv` is the fallback when Trends is blocked. `google_trends_from_csv()` is called only by a test, nothing reads `MANUAL_DIR`, and no code references `google_tr… |
| E-11 | Medium | Unfinished promise | `src/review/batch.py:246-249; src/review/leaderboard.py:479-488` | The persistence-test note shown to users hard-codes "Daily leaderboard snapshots started on 27 Oct 2026 and will replace this once two windows have passed". Snapshots are written to `tr_leaderboard_snapshots`, but no code reads th… |
| E-12 | Medium | Error handling | `78 broad `except Exception` (0 bare) across src/app; 17 of them silent` | Riskiest: (a) `venues/hyperliquid.py:167-170` and `venues/lighter.py:165-169`: `is_vault()` returns False on ANY error (rate limit, network), so a vault can be classified as a trader and enter the Top 100 (caller leaderboard.py:12… |
| E-13 | Medium | Dead code | `various` | 15 functions are never referenced outside their own def (checked with grep after vulture): `attention.youtube_subscribers` :131, `derivatives.okx_oi_history` :167, `indicators/onchain.wallet_activity` :32, `social_risk.wikipedia_c… |
| E-14 | Low | Duplication | `src/pipeline.py:255-272 (FRED_DATA) vs src/sources/fred_macro.py:14-51` | Two FRED registries share the `fred:` namespace and one HTTP collector (`collectors/fred.series`). DFEDTARU is fetched and stored twice, as `fred:fed_upper` (pipeline) and `fred:DFEDTARU` (fred_macro). The namespace mixes friendly… |
| E-15 | Medium | Duplication / inconsistency | `collectors/derivatives.py:62-111; metrics/futures.py:14,104-120; indic` | Three funding-to-8h normalisers disagree. `derivatives` uses the interval from the API or from payment spacing (handles 4h contracts). `metrics/futures` multiplies by 8 for the set `{"Hyperliquid","dYdX","Kraken","Coinbase","Light… |
| E-16 | Low | Hard-coded values | `src/events.py:31-62 (BoE/FOMC/BoJ/PBoC lists end 2027-12; `pboc_lpr_da` | The central-bank calendar silently goes empty after Dec 2027. Funding intervals and feature start dates are literals. |
| E-17 | Low | Size / complexity | `src/ui_onchain.py:186 `_btc_flows` 167 lines; replay.py:174 `simulate`` | 8 of 921 functions are over 100 lines. The largest files are explanations.py (923 lines), app.py (622), ui_onchain.py (598), catalog.py (579) and pipeline.py (578). The total is 18,975 lines. |
| E-18 | Low | Docs drift | `README.md:5, 35, 89-93; docs/trade-review-plan.md:33-34 vs :171` | README says "about 28 providers" and lists 27, but omits Lighter (`elliot.ai`), GMX (`gmxinfra.io`/`gmx.io`) and GeckoTerminal, all of which the code calls (33 distinct domains). The Trade review is described as "(Hyperliquid)" on… |
| E-19 | Info | Hygiene | `both repos` | Clean. Untracked: local `?? REVIEW_PLAN.md` only; remote has nothing untracked. Largest blob ever is 285,654 B (tests/fixtures/hl_real_wallet.json), and local .git is 4.7 MB. All text is LF (`git ls-files --eol`: 156 `i/lf`, 4 `i/… |

## 4. Coverage matrix (summary)

| Scope | Verified ✅ | Defect ❌ | Unverified ⚠️ | Main reasons unverified |
|---|---|---|---|---|
| A1 Studio market/derivatives | 77 | 3 specs (13 findings) | ~50 | Only 6 Deribit chains and 1 IBIT chain in the snapshot; Yahoo charts read but not recomputed; dated futures |
| A2 Studio on-chain/macro/social | 104 | 19 specs (12 findings) | 27 | Social component and raw-input charts run but not independently recomputed |
| A3 Positioning/Flows/On-chain/Macro | POS-1,2,5,6,8,9; FLW-3,4; OC-1 (BTC) | FLW-5, OC-1 (ETH), OC-2 | POS-3,4,7; FLW-1,2; OC-3,4; EV-1..4 | Live order book/heatmap and HL levels need live captures; EV recompute not done |
| A4 Trade review | TR-1 math (≤1.3e-8), TR-5 reconciliation | TR-1 (ROE, truncation), TR-5 (A4-01) | TR-2, TR-6, TR-7, TR-8 | Not reached in time |
| B Pipeline | — | 16 findings | — | Lock holder on 27 Oct unknown |
| C Tests | 186 pass, no network, no order dependence | 11 findings; 10/24 mutants survive | Python 3.15 | No 3.15 interpreter in the container |
| D Live UI | All 7 tabs render with no exceptions; 37 dashboards render offline with no errors | M-01 | Render-time numbers (the browser pane was hidden, so timings are unreliable) | |
| E Hygiene | No secrets in git | 19 findings | Public/private status of the GitHub repo | No network from the container |

The per-spec status lists are in the A1/A2 files and the per-row status (POS/FLW/OC/EV/TR) in the A3/A4 files.

## 5. Test plan for the fixes (one regression test per approved ID)

- **A2-01:** a CPI series with a missing month. The YoY must use a 12-month offset; assert Sep 2026 = 2.566%.
- **A3-01/02/03:** Flows OI equals Positioning OI for the same hour. Funding is never rescaled by magnitude. 4h contracts are scaled ×2.
- **A1-01:** the daily ob_delta equals the mean of the hourly values. A1-02: the latest partial day is excluded or labelled.
- **A3-04/05, B-01:** On-chain value uses the recorder's live price with an "as of" time. A wallet older than one cycle is flagged or excluded. Every UI-visible series has a scheduled writer (table-driven test over the catalog).
- **A4-01:** a fixture with same-ms fills in non-sequential tid order. The position chain must be continuous.
- **A4-02:** ROE with a synthetic deposit. The return must be unchanged by the deposit.
- **B-03/M-03:** an adapter with all items failing must be logged ok=0 and must not reset freshness.
- **B-06:** get_json retries 429s with backoff (mocked clock).
- **B-11:** a stale 'running' job older than its heartbeat window becomes 'interrupted' and the button is enabled again.
- **C-01/02/03:** a conftest.py points MARKET_DB, the config and the lock at tmp paths before any src import. Also: a one-off cleanup of the 323 polluted rows. That is a write to market.db, so it needs your OK.
- **Mutation survivors (C):** add assertions for annualisation ×1095, the to_8h cap, the reconstruct boundary and the time-stop breakeven.

## 6. Decisions needed from you

1. **Which IDs to fix.** My recommendation is every High, plus A1-02, A1-03, A3-03, M-03 and E-04. Fixing Lows and Infos is optional.
2. **Deleting the 323 fake test rows from `data/market.db` (C-01).** This is a write to your real DB.
3. **Privacy:**
   - E-01: your email is in the GitHub commit authors. You may want a noreply address and a history rewrite. That's a force-push, so it needs your OK.
   - E-02/E-03: `claude-skills/` is tracked locally and not ignored.
4. **A4-02 ROE.** Either fetch the Hyperliquid ledger (deposits/withdrawals), or relabel the figure as "return on start-of-day equity, unadjusted for transfers".

## 7. Remaining risks / not covered

- **Options and IBIT history:** few chains were stored, so most options specs stay ⚠️.
- **Upstream data:**
  - GDELT upstream has returned no data since 27 Oct (now out of the score).
  - The Lightning upstream looks inconsistent (A2-03).
  - OECD series were discontinued in Jan 2024 (A2-11).
- **YouTube** joins Social Risk only after 30 days of recordings.
- **API budget:** this audit used about 360 reference calls plus about 200 Hyperliquid info calls (fills paging, spaced ≥10 s from 18:15Z) and 0 BGeometrics calls.

## 8. Remaining risks after the fixes (Phase 4)

- **Not yet live-verified.** The running dashboard predates the fixes; restart it, then re-check Data health, Flows OI vs Positioning, On-chain value held and one trader's transfer-adjusted ROE.
- **Heavier Hyperliquid sync.** Fills now page back to 2023 and the ledger is fetched, so the first full sync of ~1,700 candidates takes longer. It stays within the rate limiter.
- **More writers on market.db.** 3 new recorder threads. Busy-timeout + retry mitigate lock errors; there's still no single writer.
- **ETH wallet balances** update only through the new hourly pipeline job; until it runs, ETH wallets are flagged stale and left out of "Value held".
- **Unfixed lower-severity IDs** remain open (see section 3), and `claude-skills/` is still tracked in git (owner decision).
- **Pending owner decision:** E-01, whether to switch commits to a GitHub noreply address.
