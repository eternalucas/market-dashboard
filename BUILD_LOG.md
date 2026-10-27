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

- 26 Oct (later): only 4 traders were eligible from the top 150 per window, because the biggest Hyperliquid earners are mostly position traders with few completed trades. The pool is now the top 1,000 per window, with accounts that have no trading volume dropped and the most active accounts synced first. The recorder budget is now 55 min per hourly run.

## 2026-10-26: On-chain tab review and rebuild
- Walkthrough findings:
  - The Bitcoin transaction feed kept only each wallet's newest ~25 transactions, captured on manual updates days apart. For 23 of 92 wallets, the transactions didn't add up to the balance change; for example, a Binance cold wallet gained 7,640 BTC with no transactions recorded.
  - Hyperliquid cohorts:
    - 2,786 emptied accounts (under $100 now) were counted as "Shrimp".
    - The positioning sample took the most active accounts, which were mostly market makers and HFT (median sample turnover 200x to 26,000,000x a month).
    - The table showed summed PnL, which a few outliers dominate.
- Fixes:
  - Hourly incremental sync of every watched Bitcoin wallet in the recorder: pages through every new transaction since the last one stored, stores hourly balances, and tracks `complete_since` per wallet.
  - New flows section with a window selector (24 h, 7 d, 30 d, 90 d), category and entity filters. It shows net, inflow, outflow and transactions, a cumulative flow chart and a per-entity table with balances and history coverage.
  - Moves between two wallets of the same entity count as internal and are left out of flows.
  - USD uses each day's BTC close, not today's price.
  - The section refreshes every 2 minutes.
  - "Every wallet" now sits in an expander.
  - Cohorts:
    - Emptied accounts are excluded and counted separately.
    - The table shows median PnL, % profitable and bot share.
    - The positioning sample is the largest non-bot accounts (turnover ≤ 100x a month).
- Tests: 123 pass (new: flow grouping, internal moves, windows and coverage, sync paging and hole detection).

## 2026-10-26: Alignment pass across the dashboard
- Studio: panels are laid out row by row (pairs start at the same height). Titles are one line (full title on hover) and descriptions are clamped to 3 lines (full text on hover and in About this chart). Charts use fixed margins with no auto-margins, so paired plot areas match exactly; single-series charts drop their redundant legend entry; the x-axis label of overlay charts sits inside the plot. Measured: every pair of real charts in the dashboards checked has identical panel and plot-area positions.
- Other tabs: the same fixed-margin layout. Control rows are bottom-aligned (Positioning Refresh live, Flows Fetch latest, Studio controls, Trade review). The options expiry picker moved above its table and chart pair. Paired tables have equal heights (Trade review breakdowns, Ethereum flows, order-book depth).

- 26 Oct: search box at the right end of the main tab bar. It autocompletes over all 37 dashboards and 266 charts; picking one opens Studio on that dashboard or chart from any tab (tabs are keyed so search can switch to Studio).

## 2026-10-27: Tokenized equities
- New Studio dashboard "Tokenized Equities": daily on-chain spot DEX volume by token issuer (100% stacked share, like Blockworks' chart), the same in USD, share by chain, and total volume.
- Sources (free, no key): CoinGecko categories and coin ids give each tokenized stock's issuer and contract addresses; GeckoTerminal gives each token's DEX pools and their daily USD volume (~6 months of history). Recorder job `tokenized_stocks` runs daily in its own thread; the first run takes about an hour.
- Coverage: DEX volume only (no centralised venues or issuer apps); tokens with under $1k daily volume and pools beyond each token's 3 busiest are left out. Share charts show no headline number; stacked USD bars show the day's total.

## 2026-10-27: Positioning clean-up and data check
- Every block on the Positioning tab is now a bordered card like the Studio charts: title on top, description and source underneath (perpetuals table, funding history, open interest history, order book, options skew, options open interest). Tables are sized to their rows (no empty rows); the order book shows 6 distance bands (0.1% to 5%). ATM IV, 25d call IV and 25d put IV have hover explanations.
- Funding fix: exchanges quote funding per payment, and some contracts pay every 4h (checked live: Binance HYPE and ENA, Bybit ENA; Hyperliquid pays hourly). These 4h rates were treated as 8h rates, so they showed half their real value. Live snapshots now read each contract's interval (Bybit fundingIntervalHour, Binance fundingInfo, OKX next minus current funding time) and history reads it from the payment spacing. A one-off repair rescaled the stored history and recomputed the OI-weighted average for HYPE and ENA. The table now shows the interval, the raw per-payment rate, the 8h equivalent and the annualised rate.
- Prices in the table keep enough decimals for small coins (DOGE 0.078010, not 0.08).
- Checked live on 27 Oct against the exchanges for all 8 assets: BTC funding really is 0.01% per 8h on all four exchanges (the standard rate when the perp trades near spot); mark, index, OI and basis match the exchange APIs; Coinbase books are complete to well beyond 5%; Deribit 25d IVs match Deribit's own deltas (BTC 30d: ATM 35.7%, 25d call 34.9%, 25d put 38.2%).

## 2026-10-27: Macro events, new charts
- The release summary table and the "Most recent releases" table are gone. In their place, four bordered cards: the average path (kept, now coloured by label: hotter / strong / hike warm, cooler / weak / cut cool), "How often <asset> was higher" (share of releases up at each horizon, next to how often the asset is up over the same number of days starting on any day), "Range of moves" (box and dots of every past release at the chosen horizon), and "Every release" (one dot per release over time, with the average of the last 12 releases).
- "Excess vs typical" is renamed "vs a normal period", with a hover explanation. The horizon selector now drives the calendar and the two horizon charts.

- 27 Oct: chart legends are 12.5px everywhere (between the 11.5px descriptions and the 14px titles).
- 27 Oct: the running indicator beside Deploy cycles through a bar chart, a money bag and the Bitcoin logo (same size and grey) instead of Streamlit's swimmer / plane / runner icons (src/running_icons.py).

## 2026-10-27: Liquidation heatmap replaces the order-book heatmap
- Flows > "Liquidation heatmap (estimated)", in the style of Coinglass: estimated liquidation levels for the Binance USDT perpetual, coloured purple -> teal -> green -> yellow, with candles on top, a liquidity-threshold slider and a "Liquidation map" side panel (USD at each price right now: longs below in red, shorts above in green). Windows 12h to 30 days.
- Model (src/indicators/liquidations.py): when open interest rises, the new positions open at the bar's typical price, split long/short by the bar's taker buy/sell volume, and spread over 10x/25x/50x/100x leverage (30/30/25/15%); liquidation price = p(1 - 1/L + 0.4%) for longs, p(1 + 1/L - 0.4%) for shorts. Falling OI shrinks every level by the same share; a level is removed once price trades through it. Twice the window is fetched so levels opened before the window are already on the chart at its left edge. Data: Binance klines, openInterestHist and takerlongshortRatio (public, no key; OI history is limited to 30 days).
- The old order-book heatmap and its once-a-minute recording on the Flows tab are removed (the Studio order-book charts keep recording through the background recorder). The liquidation heatmap works without a Coinalyze key.

## 2026-10-27: Liquidation heatmap across Binance, Bybit, OKX and Hyperliquid
- One chart, four venues. Binance, Bybit and OKX are modelled from each venue's own open interest (5-minute to 1-hour bars; taker buy/sell split from Binance and OKX, Binance's used for Bybit). Hyperliquid levels are exact: a recorder job reads Hyperliquid accounts' positions every 15 minutes (largest accounts every scan, the rest in rotation, own 300-weight/min budget) and stores each scan's liquidation levels; the caption shows the share of Hyperliquid open interest covered.
- Actual liquidations are recorded live (Bybit allLiquidation websocket, Binance !forceOrder websocket, OKX liquidation-orders every minute), drawn as dots on the chart, and used daily to fit the model's leverage mix (5x/10x/25x/50x/100x) to where liquidations really happened (needs 3+ days and 300+ events; until then the default mix). New dependency: websocket-client (small, pure Python), installed by the launcher.
- Fix: Bybit's openInterest counts both sides (Bybit docs: "the sum of both sides"); every other venue reports one side. Bybit OI now uses singleOpenInterest(Value), and a one-off repair halves the stored Bybit OI and recomputes the cross-exchange totals and OI-weighted averages.
- 27 Oct: Macro events, "Calendar view" toggle: the scheduled events on a month calendar (this month and the next two), coloured by event type; today outlined, past days dimmed (src/ui_calendar.py).
- 27 Oct: space under the macro calendar; full-screen button (top right of the card) on the Macro events charts and the Positioning funding, open-interest and skew charts.
- 27 Oct (fix): the recorder loop stopped at 15:28 after a "database is locked" error while logging a failure. The loop and its failure logging now survive any error; Hyperliquid positions are kept in their own database (data/hl_positions.db) and written one account at a time; the liquidation writer retries when the database is busy; the tokenized-stocks network list stops at GeckoTerminal's last page instead of failing.
- 27 Oct: the recorder runs as two loops: one samples the order books every minute, the other runs every slower job, so a slow job (timeouts, long backfills) never delays the minute samples (after a restart the first pass used to hold the books up for 10+ minutes).
- 27 Oct: jobs that only start their own thread (liquidation feeds, Hyperliquid scan, Top 100, wallets, tokenized stocks, sources, calibration) run first in the slow loop, so after a restart they start at once.
- 27 Oct (fix): the On-chain Bitcoin flows section (which refreshes every 2 minutes) used the page's database connection from another thread and errored; it now opens its own.

## 2026-10-27: Billions as B everywhere
- Plotly writes billions as "G" (giga) when a value uses its SI number format, which the Studio hovers and the liquidation heatmap did. src/numfmt.py rewrites every SI-formatted hover value (T/B/M/K) and switches SI tick formats to Plotly's "B" exponent labels; app.py applies it to every chart drawn with st.plotly_chart, so new charts are covered too.

## 2026-10-27: Even dashboards
- 17 Studio dashboards had an odd number of charts (an empty slot at the bottom right). Each got one new related chart (src/metrics/extra.py, with explanations): Days Since ATH (Spot Market), RSI 14D (Technical Analysis), 30-Day Rolling Liquidations (Liquidations), Dated Futures OI (Term Structure), ETH/BTC (BTC vs ETH), Implied vs Realized Vol (Implied Volatility), 25 Delta Skew Term Structure (Skew & Gamma), IBIT Options Volume (IBIT), Cumulative ETF Net Flows (US Spot ETFs), US Real Policy Rate (Macro), BTC Correlations (TradFi), Exchange Netflow USD (Exchange Flows), Proof of Reserves 30-Day Change (Proof of Reserves), Supply Held by Whales (Supply & Holders), Net Unrealized P/L USD (Profit & Loss), Overheating Signals (Bull Market Risk), Altcoin/BTC Market Cap Ratio (Altcoin Season). A test keeps every dashboard even.
- 27 Oct (fix): charts whose data frame ended up with a plain (non-date) index, e.g. Supply Held by Address Balance when one balance band is missing, ignored the range buttons and the price overlay; dates are restored before drawing. Count values under 1,000 are shown without decimals.

## 2026-10-27: On-chain tab redesign
- Same data, laid out like the other tabs: bordered cards with one chart each, space between sections, detail tables folded into expanders. Watched wallets: KPI row, holdings by cohort (bars, BTC and ETH stacked), BTC held by category over time. Bitcoin flows: KPIs, cumulative net flow, net flow by entity (diverging bars; transaction sizes when one entity is picked), every transaction on a timeline (up/down triangles, log scale). Ethereum: KPIs, flows by entity (received vs sent), market-maker flows by counterparty, $1M+ transfers on a timeline. Hyperliquid cohorts: accounts and capital by cohort, this month's results (share in profit, median PnL), positioning by cohort with the net marked.
- 27 Oct (fix): the Bybit and Binance liquidation websockets connected without a CA bundle; with Python from python.org on a Mac that fails certificate checks, so only the OKX poll was recording. They now use certifi (as every other source does through requests), and the hourly rec:liq_feeds log includes each feed's last error.

## 2026-10-27: Trade Review Phase 2, trader diagnosis
- The trader page has three views: Review (as before), Diagnosis (new) and Data quality (now a full page). Nothing on Diagnosis is simulated.
- src/review/diagnosis.py measures, from the trader's actual round trips: strategy fingerprint (market concentration, long/short bias by size, holding-time and entry-hour/weekday distributions, leverage = size / account value at the start of the day, scaling in/out, entry context = prior 24h move in units of that day's volatility, volatility regime, funding at entry); execution (taker share by fills and volume, fees in bps by maker/taker, funding paid/received by market, slippage proxy = taker fill vs the previous 1-minute close, chasing = entries after a >2 sigma 4h move in the trade's direction); risk (MAE/MFE per trip on the finest candles still available, losers that went deeper than 90% of winners ever did, how much of the best move winners keep, liquidations, positions open at once and gross leverage, daily drawdown depth/length/recovery); sizing (results by size fifth, size after a loss vs after a win relative to the last 20 trips, size vs volatility); edge decomposition (by market, side, holding time, entry context, volatility, funding; best 5 trips' share of P&L); and "what they do right" (only claims with enough trips behind them), next to the existing mistakes -> rules.
- No look-ahead: entry context and funding at entry use only bars and rates settled before the entry (tests check that a move inside the entry's own hour changes nothing).
- src/review/market.py loads Hyperliquid candles (1m covers ~3.5 days, 15m ~52, 1h ~208, 4h ~833; each trip uses the finest interval covering it, reported in Data quality) and 60 days of market funding, for the most traded markets (95% of trips, at most 12) to stay inside the rate limit. It runs in a background thread the first time a trader is opened, stores everything in data/trades.db, fetches only missing ranges afterwards, and remembers ranges the venue doesn't have.
- Data quality adds: trips with excursions (by candle interval), entry context, funding at entry and leverage; markets without price data; price-data errors; and what isn't available (historical order book, historical margin/liquidation distance).
