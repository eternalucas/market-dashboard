# Build log: Glassnode-style metrics

Scope: the "Glassnode Build Checklist" doc. Out-of-scope items are not built.

## 2026-10-21: Phase 0 (audit) and Phase 1 (recorders)

**Audit findings**
- Stack: Python + Streamlit + Plotly + SQLite (`data/market.db`), started by `Start Dashboard.command` on the Mac.
- No shared chart component yet; each tab builds its own Plotly figures.
- BGeometrics free tier has 85 endpoints (more than the checklist assumed) but data is 7 days delayed and capped at 15 calls/day, 8/hour.
- Coin Metrics community: realized cap = CapMrktCurUSD / CapMVRVCur, giving realized price, MVRV, MVRV-Z and NUPL with full history.
- Farside and Yahoo block browser (CORS) requests; to be tested from Python.

**Recorders built** (`src/recorder.py`, run by the dashboard or `Start Recorder.command`)
| Job | Cadence | Storage |
| --- | --- | --- |
| Spot books Coinbase, Binance; perp books Binance, Bybit, OKX, Hyperliquid | 1 min | `book_metrics` (spread, depth at 25/100/500/1000/2000 bps, price impact $100k/$1M/$10M) |
| Heatmap, fixed $50 grid, ±5% | 10 min | `heatmap` |
| Dated futures Deribit / Binance / OKX | 15 min | `futures_curve` |
| Deribit option trades (7-day backfill on first run) | 5 min | `option_trades` |
| Deribit option chain | 1 h | `option_snap` (venue deribit) |
| Mempool state + fee bands | 10 min | `observations` mempool:* |
| IBIT option chain | daily after 20:30 UTC | `option_snap` (venue ibit) |
| Lightning averages / fee medians | daily | `observations` ln:* |

Definitions for order-book metrics follow Glassnode's own descriptions (read from their metric catalogue 2026-10-21); time-weighted bucket values are approximated by the mean of 1-minute samples.

Tests: `tests/test_recorder.py` (11 tests, fixtures are real API responses captured 2026-10-21).

**Open issues**
- Mempool "total amount of coins" isn't exposed by mempool.space's API: that chart is dropped.
- Mempool "count by fee band" isn't available (the histogram gives vsize only): bands are by vsize and fees.

## 2026-10-21: Phase 2 (data layer)

One adapter per source in `src/sources/`, scheduled by `src/sources/registry.py` (run hourly by the recorder and by "Update all data"). Every adapter stores raw values under its own prefix; charts read from storage only.

| Source | Adapter | Freshness | Notes |
| --- | --- | --- | --- |
| Coin Metrics community | coinmetrics.py | 6 h | 20 BTC metrics, full history; last 7 days re-fetched (flash values) |
| BGeometrics | bgeometrics.py | queue | 60 endpoints, ≤14 calls/24 h and ≤7/h counted from fetch_log; never-fetched first, then stalest > 6 days |
| blockchain.com | blockchain_com.py | 12 h | yearly unsampled chunks reduced to UTC days |
| mempool.space history | mempool_hist.py | 12 h | hash rate, difficulty, Lightning totals |
| DefiLlama | defillama.py | 12 h / 20 h | stablecoins total, top 8 coins, 8 chains; BTC in top 12 CEX wallets |
| alternative.me | alternative_me.py | 12 h | Fear & Greed since 2018 |
| CoinGecko | coingecko.py | 12 h | global snapshot, recorded from start |
| CFTC | cftc.py | 24 h | TFF report, CME Bitcoin (133741), weekly |
| Deribit | deribit_hist.py | 12 h | DVOL daily since 2021 |
| Yahoo | yahoo.py | 12 h | 12 US ETFs, Purpose ETF, 12 treasury cos, 4 MSTR preferreds, 12 macro, 50 alts |
| Farside | farside.py | 12 h | stdlib HTML parser; if refused (Cloudflare) logs it and ETF per-fund flows stay partial |
| FRED | fred_macro.py | 20 h | 22 series; Germany 2Y, EZ debt/spending not on FRED (partial) |

Tests: `tests/test_sources.py` (12 tests; fixtures are real responses). The Farside test caught a header-row parsing bug, now fixed.

**Open issues**
- The FRED connector on the Mac returned HTML errors today; series IDs were checked on fred.stlouisfed.org instead. MYAGM2EZM196N (EZ M2) may be discontinued: Data health will show its last date.
- My test calls today (2 BGeometrics calls) count against today's 15; the queue only counts its own logged calls, so it could overshoot by 2 today only.

## 2026-10-21: Phase 2b (more sources) and Phase 3 (metrics)

**Sources added:** Coinalyze daily (25 BTC + ETH perps on ~20 exchanges, both margin types; BTC spot markets with buy/sell data), Coinbase hourly candles since 2015 (regional price metrics), Binance quarterly continuous futures + index since 2020 (3M rolling basis with full history), CFTC combined report (CME options OI), Coin Metrics ETH (BTC/ETH dominance), blockchain.com block size and mean fee. Deribit option trades older than ~24 h come from history.deribit.com: an hourly job backfills 2 days per run up to 1 year.

**Metrics package** (`src/metrics/`): core, spot, futures, options, books, onchain, markets. Every function's docstring states formula, inputs, window, units and the definition followed; deviations are marked `APPROX:`. Glassnode definitions were read from their metric catalogue (832 descriptions) on 2026-10-21.

Notable definitions:
- Realized cap = Coin Metrics market cap / MVRV (exact inversion) → realized price, MVRV, MVRV-Z (cumulative std, as Glassnode), NUPL with full history.
- Power law: exp(5.71 ln(days) − 38.16), Glassnode's published fit.
- Regional price: hourly changes assigned to 08:00–20:00 local working hours (Shanghai / Berlin / New York); overlaps split equally (APPROX: Glassnode's overlap rule unpublished).
- Cycle ATH: ATH followed by ≥75% drawdown, plus the latest ATH (Glassnode).
- Funding: OI-weighted across perps, hourly venues ×8.
- 3M rolling basis: Binance current/next quarter interpolated linearly in days to 90.
- Options: Black-76 on each expiry's forward; ATM IV by linear interpolation in log-moneyness; constant maturity by linear interpolation in total variance; IV at delta per side; 25Δ normalized skew = (put25 − call25)/ATM (Glassnode sign); GEX = Σ sign·OI·Γ·F²·0.01, dealers short calls / long puts; trade-tagged GEX variant (Glassnode's method) available as history accrues; max pain over listed strikes; skew index up/down vol by variance-swap integration (APPROX).
- Per-snapshot option metrics are computed at record time and stored as `optm:<venue>:*`; `options.rebuild()` recomputes from the raw chains.

**Checks on real data (Deribit snapshot 2026-10-21 20:00 UTC):** 1m ATM IV 35.4%, term structure 30.4% (1w) → 37.9% (6m), 25Δ normalized skew +8.6% (puts richer), OI 389,610 BTC ($33.2B), P/C OI 0.60, 108 surface values in 0.7 s.

**Bugs caught by tests:** skew-index downside integral missed the segment next to the forward (fixed with an at-forward grid point); Bull Bear Index groupby crashed on empty maturity buckets (rewritten).

Tests: `tests/test_metrics.py` (17 closed-form tests). Total suite: 86 passing.

**Open issues / approximations**
- Perp books: Binance/Bybit/OKX publish only ~0.1% around mid, so perp depth beyond ~10 bps is a minimum and the perp heatmap isn't meaningful (same caveat Glassnode states).
- NVT uses blockchain.com's change-adjusted volume: levels differ from Glassnode's all-transfer NVT.
- CVDD not built: needs full-history CDD (BGeometrics free tier has 4 years).
- Options premiums are Deribit only (Glassnode adds OKX, Binance).

## 2026-10-21: Phase 4 (charts, dashboards) and Phase 5 (verification), first pass

**Charts:** `src/charts.py` is the single chart component (Plotly). Design taken from Glassnode Studio screenshots (chart page, dashboard page): background #1a1a1a, panels #242424 with #333 borders, axis text #8b8b8b, Inter / system sans, metric in BTC orange #f7931a on the left axis, BTC price as a thin light line on a log right axis, dot legend top-left, unified crosshair tooltip with unit-aware formatting (B/M/K never SI "G"), threshold bands, positive/negative bars and areas, stacked areas, diverging heatmaps centred on zero. Glassnode's logo, name and watermark are not used. Control bar: range 1M/3M/1Y/2Y/All, scale default/lin/log, MA none/7D/30D, resolution 24h/1h, price overlay toggle. App-wide dark theme in `.streamlit/config.toml`.

**Catalog / dashboards:** `src/catalog.py`: 240 chart specs, 35 dashboards (the 29 in the checklist; several large Glassnode dashboards split into focused pages). Studio tab = Dashboards (grid) or Charts (one large chart).

**Numeric check vs Glassnode** (`scripts/verify.py`; reference: Glassnode's free-tier values for 23 metrics, captured 2026-10-21, at 2021-11-08 top, 2022-11-21 bottom, 2024-03-13, 2025-10-06 and the latest day). First run: 78 OK / 19 out of tolerance. Fixes:
| Metric | Was | Cause | Fix | Now |
| --- | --- | --- | --- | --- |
| Hash rate | 7-21% off | used mempool.space's smoothed estimate | difficulty × 2^32 × blocks / 86,400 (Glassnode's estimator) | ≤0.3% at all dates |
| Drawdown from ATH | 0% vs -1.2% on 2025-10-06 | Glassnode's ATH is intraday | ATH includes Coinbase hourly highs | within tolerance |
| UTXO count | 25-50% off, flat since 2024 | blockchain.com series stale | BGeometrics utxo-count | pending queue |
| ETF net flows (BTC) | 5x off on 2024-03-13 | Glassnode uses holdings changes, not USD/close | Δ BGeometrics ETF holdings; Farside kept for USD per fund | pending queue |
| Altcoin Season latest day | 0 | missing BTC close counted as "alt didn't beat BTC" | skip days without a BTC return | fixed (test added) |
Second run: 85 OK / 9 out / 16 n/a (BGeometrics endpoints still in the queue).

Exact matches: price (≤0.12%), market cap, power law (0.0%), Pi Cycle MAs (≤0.35%), supply, active addresses (≤2.6%), tx count (≤2.9%), difficulty (0.0%), block count (exact), block interval, block size (exact), transfer volume BTC (≤0.06%), SOPR (≤0.14%).

Remaining out of tolerance (source definitions, not bugs):
- Transfer count (Coin Metrics TxTfrCnt): within 0.3% in 2022-2024, +23% (2025) and +95% (2026). Coin Metrics' definition diverged; labelled partial.
- Fees total / mean: 4-6% above Glassnode on 2 of 5 dates (2021-11-08, 2025-10-06), within 1.6% on the others; block counts match exactly, so it's Coin Metrics' fee accounting; labelled.

**Data checks:** Farside's latest row holds "-" for funds that haven't reported (stored as 0): trailing all-zero days are now dropped instead of shown as zero flow.

**Visual review** (35 dashboards rendered against a snapshot of the real data, 2026-10-21 20:47 UTC; no panel errors). Fixes from the review: dark app theme (light-theme controls were unreadable), SI "G" → "B" axis labels and 3-significant-figure price ticks, legend names for single series, markers for sparse series and date-padded axes for <2 days of data, diverging zero-centred heatmaps, candlestick OHLC, stacked charts limited to the largest 6 series + Other with the total as the header value, log axes over >2.5 decades labelled at powers of ten, "No data yet" when a metric is empty even if price exists.

**Coverage note:** Futures Open Interest Perpetual = $28.1B (25 perps via Coinalyze) vs Glassnode's all-futures OI $47.8B (adds CME ~$8.6B, dated futures and venues Coinalyze doesn't carry). Charts are labelled "Perpetual".

## Session report (2026-10-21)
- Phases 0-4 done; Phase 5 first pass done (numeric: 85 OK / 9 out with causes / 16 pending data; visual: all 35 dashboards).
- Not built yet: liquidation heatmap, ETF on-chain custody balances, DAT holdings/flows/mNAV, government & Mt. Gox labelled balances, SegWit/Taproot adoption, inscriptions & runes, ASIC fleet, mining pools chart, own risk signal, stablecoins on exchanges, Ethereum stablecoin activity.
- Waiting on data: BGeometrics queue (~5 days for 60 endpoints): HODL waves, address bands, CDD/VDD/ASOL, UTXO count, ETF holdings, dominance history, STH/LTH NUPL, aSOPR, cost-basis models. IBIT options from tonight's US close. Option-trade backfill (~8 days to a year).
- Next session: re-run scripts/verify.py once BGeometrics has filled; build the unbuilt items above; per-section visual comparison against Glassnode's free charts.

## 2026-10-21 (later): UI fixes requested
- **Description alignment:** Streamlit's markdown blocks carry a -16px bottom margin, so each chart's description spilled ~5px past its panel border (measured on all panels). Reset inside panels; re-measured: 34/34 panels have description and toggle inside the border, including with explanations open.
- **"About this chart" toggle** on every Studio chart (dashboards and single-chart view): how it's calculated, how to read it, data source and any limitation. `src/explanations.py` covers all 241 charts; a test fails if one is missing.
- Percent axes now use automatic precision (funding showed "0.0%, 0.0%"); icon font no longer overridden (toggle showed "arrow_down"); "Funding Rate (All)" shows the 8 largest exchanges by OI; charts with many series skip the header value.

## 2026-10-21 (later): Social Risk rebuilt and moved into Studio
- **Where:** Studio → Indicators → *Social Risk* (score, attention vs speculation, what's driving it, input coverage, one chart per input, then today's reading, an input status table, the "Does it work?" tables and the X impressions log) and *Social Risk: Raw Inputs*. The Overview dashboard also shows the score. The standalone tab is gone.
- **Research** (what comparable tools use): alternative.me Fear & Greed (volatility, momentum/volume, social media, dominance, Google Trends); Santiment (social volume, social dominance vs its own average); LunarCrush (mentions, engagements, creators, social dominance); the Coinbase App Store rank as the retail tell; YouTube view velocity over subscriber counts; Google Trends and Wikipedia attention in the academic literature. Reddit is not used: the site is blocked for this project's fetch tools.
- **Inputs, 3 → 12** (`src/indicators/social_risk.py`): Attention (65%): Wikipedia English (11 articles, as a share of all Wikipedia views), Wikipedia in 12 other languages, Google Trends (7 terms), App Store ranks of crypto-native apps (US/CA/UK, Finance + overall charts on one ladder), YouTube views per day (11 channels), Hacker News weekly mentions (since 2011), GDELT news share, X impressions. Speculation (35%): Fear & Greed (half weight), perp funding, Altcoin Season, memecoin share of altcoin volume. Weekly/monthly buckets now stamped at period end (no lookahead).
- **Check against the old score** (same period, 2018-02 to now, local snapshot): after an *Extreme* reading BTC's next 90 days averaged **-6.4%** (new) vs **+23.7%** (old), against +14.0% on all days; next 180 days +5.6% vs +28.5% (all days +32.7%). In-sample and only ~3 cycles, so a sanity check rather than proof.
- **Collection:** `src/sources/social.py` in the source registry, so the recorder refreshes it hourly; each input keeps its own schedule (Trends weekly per term, Hacker News backfills ~56 weeks per run).
- **Speed:** prefix queries on the observations table used `LIKE` (a full scan of ~3M rows per call); now a range on the primary key. The Social Risk frame dropped from ~48 s to ~2 s locally, and every Studio chart that reads a group of series benefits.
- Tests: 100 pass (new: app ladder, period stamping, grouped composite, Hacker News, Wikipedia share, mocked adapters).

## 2026-10-26: Trade Review v2, Phase 1 (Hyperliquid adapter, tables, round-trip fixes, Top 100)
- Plan and venue findings: `docs/trade-review-plan.md` and `docs/venues.md`. Nado and Variational were checked and excluded (no open per-trader history; Variational settles batched USDC on-chain with no trade prices or sizes).
- `src/venues/`: an adapter interface with capability flags, a shared rate limiter (800 of Hyperliquid's 1,200 weight a minute) and backoff on 429, 5xx and timeouts. The Hyperliquid adapter covers the leaderboard (unofficial stats file, used for candidates only), fills, funding, ledger, portfolio, vaults, candles and market funding.
- `data/trades.db` (separate from the market database): new `tr_*` tables, additive only.
- Round-trip engine (`src/review/reconstruct.py`), now also behind the existing review:
  - Flip fees are split by quantity.
  - Funding is attributed to the trip on that coin and side.
  - Liquidations are tagged as the liquidated account vs the counterparty.
  - Positions that predate the history are excluded and counted.
  - Positions that change without a fill are detected and their trips closed as "exit not observed".
- Real-wallet check (anonymized fixture, 756 fills):
  - Realized P&L, fees and funding reconcile to the cent.
  - 30-day mark-to-market P&L is within 1% of Hyperliquid's own portfolio figure.
  - One real position gap was found and surfaced.
- Top 100 (`src/review/leaderboard.py`):
  - Candidates are the top 150 per venue window.
  - Market makers are detected on sync (≥ 2,000 fills in 24 h) and by behaviour (fills per day, maker share with hold time, two-sided fills within 1 s); vaults via `vaultDetails`.
  - Metrics come from daily mark-to-market P&L and time-weighted return on equity.
  - Eligibility is re-applied when reading, so the UI filters need no refetch.
  - The board is snapshotted daily for the persistence test.
- The recorder runs `trade_leaderboard` hourly, with a 40-minute budget per run.
- UI: Trade review → Top 100 | Trader review (leaderboard trader with a Data quality panel, or the original random draw).
- Tests: 118 pass (new: reconstruction, attribution, mark-to-market identity, real-wallet reconciliation, leaderboard pipeline on a fake venue).
