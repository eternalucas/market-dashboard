# Reference pulls (Phase 1)

All pulled 2026-11-02 between 17:51Z and 18:15Z from the Mac's browser pane (public APIs) or the FRED tool.
Snapshot DBs (`../snapshot_market.db`, `../snapshot_trades.db`) were taken ~17:51Z, a few minutes after the
recorder restarted at 17:42Z. Sources that ran **after** 17:51Z (coinalyze_daily 17:53, fred_macro 17:54,
wikipedia, bgeo...) are NOT in the snapshot: a "missing latest value" in the snapshot for those is snapshot
timing, not a defect. Main session confirmed this for czd:*:fr / liq_* (present in the live DB at 18:07Z).

| File | Contents | Caveats |
|---|---|---|
| REFA.json | `dl_total` DefiLlama stablecoin total (usd = totalCirculatingUSD, circ = totalCirculating) at 4 dates; `fng` alternative.me; `dvol` Deribit DVOL daily [ms,o,h,l,c]; `cm_btc`/`cm_eth` Coin Metrics community at 4 dates; `cb1h` Coinbase 1h candles [t_s, low, high, open, close, vol]; `cftc_fut`/`cftc_comb` CFTC TFF (Socrata gpe5-46if / yw9f-hn96) last 3 weeks; `bc` blockchain.com charts (fees-usd-per-transaction returned 404) | cb1h 2026-11-02T17:00 bar was in progress when stored (upsert with 2h overlap fixes it next run) |
| REFB.json | `fh_<venue>_<coin>` raw funding history [ms, rate(, realized)] for binance/bybit/okx/hl × BTC/ETH/HYPE/ENA (raw per-payment rate, NOT 8h-normalised); `oi_binance_BTC` [ms, oi_coin, oi_usd] daily; `oi_bybit_BTC` [ms, oi_coin] daily; `wikitotal_en`, `wiki_*` pageviews (user agent) ; `btc_bal` {addr: [sats, tx_count]} from mempool.space-compatible API; `eth_bal` {addr: [wei_str, block]} from Blockscout (null/ERR = request failed, not zero) | eth_bal partial (rate limit). Stored ETH balances date from 2026-10-26, so current vs stored need not match |
| REFC.json | HL per-trader per-day aggregates `days[d] = [n_fills, sum closedPnl, sum fee, sum px*sz]` from userFillsByTime **aggregateByTime=false**, plus portfolio vlm. Only 0x8738 is complete; 0x2eaca entry there is a 429 artefact (n=0) — ignore it | dashboard uses aggregateByTime=true, so fill COUNTS differ; sums (pnl, fee, notional) are comparable |
| REFC3.json (if present) | same aggregates, paged with dedupe by tid, endTime 2026-10-28T00:00Z; fee includes builderFee | see above |
| REFD_CZ.json | Coinalyze daily: `fr` funding OHLC, `ohlcv` (v/bv in BASE units for BTCUSDT_PERP.A, BTCUSDT.6, BTC.H), `liq` (l/s USD), `fr_1h` BTC.H hourly; range t=1792800000..1793577600 (2026-10-24..2026-11-01 UTC day starts) | stored vol_usd = v × that day's close (by design, docstring); 2026-11-02 is a partial day |
| REF_FRED.json | FRED tool pulls: DGS10, T10Y2Y, WALCL, ICSA, PAYEMS, CPILFESL (levels), CPIAUCSL (**pc1**, i.e. YoY %) | FRED revised ICSA 2026-10-17 from 194000 to 195000 |
| _onchain_stored_latest.json | latest stored onchain_bal per address (BTC ts 2026-11-02T17:00, ETH last 2026-10-26) | |

Main-session results already established (reproduced, with evidence in the main transcript):
- Coinalyze close/fr/liq/oi match exactly for 24 Oct–1 Nov; 28 Oct liq differed 0.02–0.03% in the snapshot (partial day stored at 23:15Z, before collection stopped).
- Funding history normalisation to 8h matches the venues exactly for 1h (HL), 4h (Binance/Bybit HYPE/ENA) and 8h contracts (255 points); missing points are after the last stored update (26 Oct).
- Coin Metrics, CFTC (futures-only and combined), DVOL, F&G, blockchain.com charts, Wikimedia, DefiLlama total (USD field): exact at sampled dates.
- FRED levels (live DB, 18:07Z): exact; `cpi_yoy` = FRED CPIAUCSL pc1 exactly; `payrolls_chg` = PAYEMS diff exactly.
- price:BTC, evpx:*, funding_hist_8h:*, oi_coin_hist:*, fred:claims are only written by `pipeline.run_all` (manual refresh), not by the recorder → stale since 26 Oct.
