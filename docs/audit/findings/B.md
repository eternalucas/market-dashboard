# B — Data pipeline & recorder audit (read-only)

Scope: src/recorder.py, src/pipeline.py, src/sources/*, src/storage.py, src/collectors/*, src/venues/base.py + hyperliquid.py, src/review/jobs.py + batch.py, src/ui_batch.py, src/views.py source_health, app.py.
Evidence: snapshot DBs (read-only URIs), scripts in /home/claude/audit/work/B/ (`q.py` SQL runner, `exp.py` offline experiments on a copy of the code at work/B/code with stubbed HTTP and throwaway SQLite, `health_sim.py`, `bgeo_budget.py`, `dupes.py`, `schema_keys.py`, `fetchlog_summary.py`).
File:line numbers come from the tree as re-checked after the coordinator's mid-task changes (lighter_sample/tokenized_stocks removed, new source_health). Where my evidence used the old code, I say so.

## Findings

| ID | Sev | Area | file:line | What's wrong | Evidence | Repro | Root cause | Proposed fix | Conf. |
|---|---|---|---|---|---|---|---|---|---|
| B-01 | High | Scheduling / coverage | pipeline.py run_all steps (update_prices, update_positioning_history, update_events, update_hl_cohorts, update_onchain ETH, update_flows); recorder.py JOBS L346-351 | Several series families have no scheduled writer. The recorder's JOBS list never calls them; they are written only by "Update all data" (`pipeline.run_all`), or for the viewed asset only by the Positioning tab (`app.py live_positioning` → `positioning_snapshot`). | Latest ts per family (`work/B/obs_families.txt`): price 2026-10-26, evpx 10-26, funding_hist_8h 10-26 16:00, oi_coin_hist 10-26, hl_cohort_net 10-26 18:00, hl_account_value 10-26. funding_8h/oi_usd/basis: oldest_last 10-26 18:00, newest 11-02 17:00 (BTC only). fetch_log: perp:*:BTC last 11-02 17:42; perp:*:SOL/HYPE/… last 10-26 18:15; coinalyze:1hour:* last 10-26. | `python3 work/B/q.py snapshot_market.db "WITH s AS (SELECT series,MAX(ts) mx FROM observations GROUP BY series) SELECT … GROUP BY fam"` | These parts of the pipeline existed before the recorder and were never moved into JOBS. | Add recorder jobs: prices daily, positioning_history hourly, events daily, hl_cohorts hourly, a positioning snapshot for every asset hourly, flows 1h hourly. Or call run_all's steps from job_sources. | High |
| B-02 | High | Health UI says "working" for stale data | views.py:195-228 (new); app.py:128 | (a) The new source_health marks a source Stale only when it has had no *attempt* for 48 h. A source that returns ok with 0 rows, or one whose own freshness window keeps it from running, still shows OK. (b) The global "data is old" banner (app.py:128) keys only on src:social and src:coinmetrics, which the recorder refreshes every hour. So the banner never fires while price:* is 7 days old. | Old source_health on the snapshot (`health_sim.py`): "Sources working 377 of 424"; 259 of those OK had a last success more than 4 days before the snapshot, e.g. price:BTC OK with last success 2026-10-26 17:57. The new 48 h rule would mark these Stale (last attempt 10-26); I did not run the new function. Banner inputs: src:coinmetrics ok at 2026-11-02 17:42 → no warning. | `python3 work/B/health_sim.py` | Health is defined on fetch attempts, not on data age. | Use per-family data age (MAX(ts) in observations vs the expected cadence) for Status. Build the banner from the oldest critical series (price, funding). | High (a: from code; behaviour of the new function not executed) |
| B-03 | High | Error handling: total failure logged as success | registry.py:57-58; fred_macro.update; blockchain_com.update; social.update | `rows = fn() or 0; log_fetch(ok=True)`. Sources that catch their own per-item errors return 0 rows, and the registry then logs `src:X ok=1`. That success resets the freshness window, so the source is not retried for 20 h. | Snapshot: `2026-10-21T20:38:54.641568 fred_macro:failed ok=0 "WALCL: JSONDecodeError; …"`, then `20:38:54.641861 src:fred_macro ok=1 rows=0`. Reproduced offline: `python3 work/B/exp.py registry_zero_rows` → `('src:fred_macro', 1, 0, '')`, `_fresh = True`. hacker_news partial: 2026-10-26T20:39:19.494 hacker_news ok=0 "stopped after 84 rows", then src:social ok=1 at .497. | as above | Item-level errors are swallowed inside the adapter. Zero rows is treated as success. | Adapters return (rows, failures) or raise when failures and rows==0. The registry logs ok=0 or "partial" and does not refresh freshness on 0 rows. | High |
| B-04 | — | Premise check: "onchain eth Binance logged ok with TypeError" | pipeline.py _fetch_wallet / update_onchain | **REFUTED on the snapshot.** The TypeError row was logged ok=0. No ok=1 row contains an error string except rec:liq_feeds (see B-08). | `SELECT ok,COUNT(*) FROM fetch_log WHERE message LIKE '%TypeError%'` → `0 | 5`. Row: `2026-10-16T22:23:54 onchain:eth:Binance 28 ok=0 "TypeError: sequence item 3: expected str instance, float found"`. The current eth path has no str.join, so the code that raised it is probably gone (UNVERIFIED which version). | q.py | — | — | High |
| B-05 | High | 'database is locked' (src:social) | storage.py:117 (timeout=30, WAL, no retry); social.py:67-74 | `_log` catches the fetch error and then calls `log_fetch`, which also waits 30 s and raises. That exception escapes `_log` and aborts the rest of `social.update` (app store, YouTube, GDELT, HN, Trends skipped). Writers on the same DB at the time: recorder fast and slow threads, plus sources, watched_wallets, hl_liq_scan, liq writer, liq_calibrate and trade_leaderboard threads, each with its own connection, plus Streamlit (app.py:57 opens a connection per rerun) and run_all with its flows thread. | Snapshot: last wiki ok 2026-10-27T19:27:43.588, then `19:28:46.089 src:social ok=0 OperationalError: database is locked`. The gap of about 62 s matches two 30 s timeouts. Offline: `exp.py locked` (holder keeps a write lock 70 s) → `escaped _log after 60.1s: OperationalError: database is locked`. Which connection held the lock on 10-27 is UNVERIFIED: nothing in fetch_log identifies it. | `timeout 120 python3 work/B/exp.py locked` | No single writer. Busy timeout with no retry. The logging path can itself fail. | Use one writer queue/thread per DB, or retry with backoff on SQLITE_BUSY. Make `_log`'s failure logging best-effort (try/except, like Recorder.fail). Set PRAGMA busy_timeout and keep transactions short. | Medium-High |
| B-06 | High | Rate limits: no 429 handling in the shared HTTP helper | collectors/http.py:17-25; social.py update_wikipedia | `get_json` retries only on Timeout. A 429 raises at once and Retry-After is ignored. The Wikipedia loop has no sleep, so the remaining items all 429 in a burst. Failed items have no cooldown and retry every hour. GDELT went 9 of 10 runs as 429. | Snapshot 2026-10-28 22:39:37–40: 38 consecutive wikipedia/wikitotal 429s about 70 ms apart. Shiba_Inu and wikitotal:en hit 429 on every run (25 and 13 failures). Offline `exp.py wiki_429` → `requests: 48 in 0.24s; Retry-After honoured: False`. The 17:53Z storm is after the snapshot; I could not see it. | `python3 work/B/exp.py wiki_429` | Generic helper with no backoff and no pacing. | Route get_json through `venues.base.with_backoff` (honours Retry-After). Pace Wikimedia at ≤ 5 req/s. Stop the loop after the first 429. Add contact info to the User-Agent (Wikimedia policy; whether that reduces 429s is UNVERIFIED). | High |
| B-07 | Medium | Rate limits: Hyperliquid budget not shared | venues/hyperliquid.py:27 (LIMITER 800); liquidation_feeds.py:53,67,89; collectors/books.py:65, derivatives.py:71,137 (unmetered) | The review limiter (800/min, per process) and the scan limiter (300/min, new instance per scan) are independent. books l2Book every minute and the UI's metaAndAssetCtxs/fundingHistory calls go through post_json with no metering. With "Start Recorder" plus the dashboard running, each process has its own 800. The scan's 429 handler sleeps 10 s and skips the account without informing the limiter. | `exp.py hl_limiters` → `review limiter 800 + scan limiter 298 = 1098` weight admitted in about 0.01 s, before any unmetered calls. The venue limit of 1200/min comes from the code comment; I did not check HL docs (no network). The 429s observed today are not in the snapshot. | `python3 work/B/exp.py hl_limiters` | One budget split into per-module limiters. | Use one module-level HL limiter used by every HL call (books, derivatives, scan, review). Use a file/DB-backed budget across processes, or run a single process. | Medium |
| B-08 | Medium | Partial or false success: liquidation feeds | recorder.py job_liq_feeds; liquidation_feeds.py:196, 271 | `counts` is incremented in `_put` before INSERT OR IGNORE dedupe, so re-polled OKX rows are counted again. OKX errors are swallowed with `except: pass`. The Binance websocket stored 0 rows ever, yet rec:liq_feeds is logged ok=1 every hour. | Log `rec:liq_feeds ok=1 rows=19660 "bybit 4, binance 0, okx 19656"`. liquidations table: okx 2738 rows, bybit 476, binance none. `exp.py liq_counts` → `counts okx 3 | stored rows 1`. Why Binance receives nothing is UNVERIFIED (no network). | `python3 work/B/exp.py liq_counts` | Counters measure messages, not stored rows. No per-venue health check. | Count `cursor.rowcount` after insert. Log per-venue sources (`rec:liq_feeds:binance`) and mark ok=0 when a venue has 0 events in 24 h. Record OKX exceptions in `self.errors`. | High |
| B-09 | Medium | Rate limits: Coinalyze pacer per Client | flows.py:64 client_for | Each call builds a new `Client` with its own 38/min deque. The recorder's coinalyze_daily, run_all's flows thread and the UI's ui_flows update_flow can run at the same time, each allowed 38/min against a 40/min key limit. 429s are then absorbed by sleeping. | `exp.py coinalyze_pacers` → `76 Coinalyze calls in 0.00s from two clients`. No Coinalyze 429 row in the snapshot, so it has not been observed. | as above | Pacer state is per instance. | Use a module-level shared pacer (thread lock), keyed by API key. | High (code), Low (observed impact) |
| B-10 | Medium | BGeometrics budget: race and dead endpoint | bgeometrics.py:62-106; registry.py:63 | The budget is counted from fetch_log, so it survives restarts (good). But it is read before any call: two concurrent `update()` calls (recorder sources thread plus "Update all data") both see 7 left and spend 14 in one hour (the free tier allows 8/h). A permanently failing endpoint (delta-cap, HTTP 500) stays at the head of the "never fetched" queue and burns 1 call every run. | `bgeo_budget.py`: history max is 14 per rolling 24 h and 7 per hour (never exceeded so far). delta-cap 500 failed on 10-26, 10-27 (×2) and 10-28. `exp.py bgeo_race` → `HTTP calls made: 14 (HOURLY_BUDGET=7)`. `exp.py bgeo_failing_first` → delta-cap is first on every run. | as above | Check-then-act without a lock. Failures are not tracked in plan(). | Reserve calls by inserting the fetch_log row before the call, inside a BEGIN IMMEDIATE transaction. Quarantine an endpoint for N days after K failures. | High |
| B-11 | High | Background jobs: stuck 'running' disables the UI | jobs.py:58-76; batch.py:35-80; ui_batch.py:35-38,53 | Status lives only in the DB. After a restart the thread is gone (daemon) but the row stays 'running'. There is no heartbeat or stale check. `_latest()` picks the newest batch row, and the "Run on the Top 100" button is `disabled=running`, so the user can never start a new batch. The progress bar stays frozen. | snapshot_trades: `batch-2026-10-28-7077a05bce | running | 0.116 | Backtesting trader 13 of 100 | started 2026-10-28T00:33:31 | finished NULL`. Code: ui_batch.py:53 `disabled=running` with `running = run["status"]=="running"`, and no liveness check. | `python3 work/B/q.py snapshot_trades.db "SELECT … FROM tr_backtest_runs WHERE status<>'done'"` | No heartbeat or ownership of a run. | Add `heartbeat_ts` and `pid` columns updated by progress(). Treat running with heartbeat older than 2 min (or a different pid) as 'interrupted'. Mark such rows at startup. | High |
| B-12 | Medium | Daemon threads killed on exit: non-atomic files | pipeline.py _append_csv (to_csv in place), sync_btc_wallets `state_path.write_text`; liquidation_feeds SCAN_STATE; coinalyze CACHE/META | SQLite writes are transactional, but CSV and JSON state files are rewritten in place by daemon threads that die at process exit. A truncated btc_sync.json makes `json.loads` raise on every later run. | Code read only. I did not observe a truncated file (UNVERIFIED in practice). | — | In-place writes. | Write to a temp file, then `os.replace`. | Medium |
| B-13 | Medium | Timezone: IBIT "after close" wrong in winter | recorder.py:166 | The gate is a fixed 20:30 UTC. After US DST ended on 2026-11-01 that is 15:30 ET, 30 min before the close. The snapshot is stamped as the day's close (`now.normalize()`). There is no US holiday calendar. | `pd.Timestamp('2026-11-02 20:30',tz='UTC').tz_convert('America/New_York')` → `15:30-05:00` (10-30 → 16:30-04:00). | python one-liner in transcript | Hard-coded UTC time for an exchange-local event. | Gate on `America/New_York` 16:15 local plus an exchange calendar. | High |
| B-14 | Low | Timezone: local dates | attention.py:34, social.py:109 (`date.today()`); app.py:440,445 (`pd.Timestamp.now()` naive) | The Wikimedia end date uses the machine's local date (Montréal, UTC-5): one day short in the evening. The events tab compares naive local "now" with stored dates. now_hour(), last_success and the registry are UTC-aware (OK). | grep output in transcript | — | Mixed naive/aware. | Use `datetime.now(timezone.utc).date()` and `pd.Timestamp.now(tz="UTC")`. | High (code), Low impact |
| B-15 | Medium | Silent degradation on upstream changes | coinmetrics.py:60 (`except: pass`); cftc.py:28 (`pass`); yahoo.py download (KeyError skip, yfinance swallows); liquidation_feeds.py:271 | Optional parts fail with no log row. Loud cases: FRED HTML→JSONDecodeError logged ok=0 (10-21), pytrends `method_whitelist` logged ok=0 (10-16; the current code has a retries=0 workaround, attention.py:57-59), blockchain.com fees-usd-per-transaction 404 logged ok=0 (10-21; that chart is no longer in CHARTS), Coin Metrics 400 logged (10-21), bgeo delta-cap 500 logged. The FRED docstring says Data health flags stale FRED series, but no code does this (grep finds none). fred:MYAGM2EZM196N (used as ez_m2) last observation is 2017-03-01; CSCICP03EZM665S and USALOLITONOSTSAM end 2024-01. | fetch_log rows quoted in the summary below. `SELECT series,MAX(ts) FROM observations WHERE series>='fred:' … HAVING MAX(ts)<'2026-07-01'`. | q.py | Bare excepts. Promised feature missing. | Log a `…:partial` ok=0 row for every swallowed part. Add a FRED cadence check (age > 2× frequency → Stale). Replace discontinued IDs. | High |
| B-16 | Low | Idempotency | storage.py SCHEMA, save_series/save_frame (INSERT OR REPLACE); replace_event_type L208-212 | Every market table has a PK in the code SCHEMA and every write is an upsert. No duplicates exist in the data. Re-fetch overlaps: coinbase_hourly 2 h (re-reads the in-progress bar), coinalyze_daily 3 d, blockchain_com 3 d, binance_quarterly 3 d, Coin Metrics 7 d, Yahoo 7 d, Wikimedia 10 d, bgeo 14 d, HN last 2 weeks, GDELT 12 w excluding today, CFTC full history. Weak points: (1) `replace_event_type` commits the DELETE before the INSERT, and an empty fetch deletes all dates of that event type. (2) coinalyze_daily derives the start for all symbols from `perps[0]`, so a newly listed symbol gets only 3 days of history (not observed). (3) The snapshot DDL itself has no PKs (pandas-made copy), so constraints could only be checked in code. | `dupes.py` → 0 duplicate keys in all 10 tables and in fetch_log (ts,source). `schema_keys.py` shows the snapshot DDL has no PKs (artefact). | `python3 work/B/dupes.py` | — | Do the event replace in one transaction and skip it when the new set is empty. Compute a per-symbol start in coinalyze_daily. | High |
| B-17 | Low | Freshness windows vs cadence | registry.py SOURCES; recorder `sources` hourly; Recorder.ok() hourly log | Windows (coinmetrics 6 h, most 12 h, cftc 24 h, fred_macro 20 h, coinbase_hourly/social 1 h) are checked hourly by job_sources, so a 1 h source can drift to about 2 h. Recorder successes are logged at most hourly, so "last ok" lags by up to 1 h (main session confirmed). cadence of books is fine (main session: 34 samples in 33 min). | Code. fetch_log src:* timestamps (above). | — | Hour-slot alignment. | Run due checks every 10 min, or set window = 55 min for hourly sources. | Medium |

## fetch_log summary (snapshot, 4,476 rows, 2026-10-16 22:16 → 2026-11-02 17:48 UTC)
Families group per-item sources (e.g. wikipedia:*). rec:* and src:* are shown per source. Script: `python3 work/B/fetchlog_summary.py`.

| source (family) | attempts | fails | fail % | last ok (UTC) | last fail (UTC) |
|---|---|---|---|---|---|
| app_store | 42 | 1 | 2.4 | 2026-10-28T22:39 | 2026-10-23T17:13 |
| bgeo | 56 | 4 | 7.1 | 2026-10-28T22:40 | 2026-10-28T22:40 |
| book | 202 | 0 | 0.0 | 2026-11-02T17:42 |  |
| central_bank_calendar | 7 | 0 | 0.0 | 2026-10-26T17:57 |  |
| coinalyze | 191 | 0 | 0.0 | 2026-11-02T17:42 |  |
| event_price | 56 | 0 | 0.0 | 2026-10-26T17:57 |  |
| fred | 35 | 10 | 28.6 | 2026-10-26T17:57 | 2026-10-21T16:54 |
| fred_macro | 1 | 1 | 100.0 |  | 2026-10-21T20:38 |
| fred_release | 29 | 9 | 31.0 | 2026-10-26T17:57 | 2026-10-21T16:54 |
| funding_hist | 224 | 0 | 0.0 | 2026-10-26T17:57 |  |
| gdelt | 10 | 9 | 90.0 | 2026-10-26T23:06 | 2026-10-28T22:39 |
| google_trends | 42 | 12 | 28.6 | 2026-10-27T23:13 | 2026-10-21T23:05 |
| hacker_news | 14 | 1 | 7.1 | 2026-10-28T22:40 | 2026-10-26T20:39 |
| hyperliquid_cohorts | 3 | 0 | 0.0 | 2026-10-26T18:02 |  |
| oi_hist | 112 | 0 | 0.0 | 2026-10-26T17:57 |  |
| onchain | 1186 | 8 | 0.7 | 2026-11-02T17:48 | 2026-10-28T23:15 |
| options | 181 | 1 | 0.6 | 2026-11-02T17:42 | 2026-10-21T20:54 |
| perp | 808 | 0 | 0.0 | 2026-11-02T17:42 |  |
| price | 56 | 0 | 0.0 | 2026-10-26T17:57 |  |
| rec:book:perp:binance | 43 | 3 | 7.0 | 2026-11-02T17:42 | 2026-10-28T23:15 |
| rec:book:perp:bybit | 43 | 4 | 9.3 | 2026-11-02T17:42 | 2026-10-28T23:15 |
| rec:book:perp:hyperliquid | 43 | 4 | 9.3 | 2026-11-02T17:42 | 2026-10-28T23:15 |
| rec:book:perp:okx | 45 | 6 | 13.3 | 2026-11-02T17:42 | 2026-10-28T23:15 |
| rec:book:spot:binance | 43 | 3 | 7.0 | 2026-11-02T17:42 | 2026-10-28T23:15 |
| rec:book:spot:coinbase | 43 | 3 | 7.0 | 2026-11-02T17:42 | 2026-10-28T23:15 |
| rec:futures_curve:binance | 40 | 0 | 0.0 | 2026-11-02T17:42 |  |
| rec:futures_curve:deribit | 44 | 5 | 11.4 | 2026-11-02T17:42 | 2026-10-27T23:12 |
| rec:futures_curve:okx | 40 | 0 | 0.0 | 2026-11-02T17:42 |  |
| rec:hl_liq_scan | 13 | 0 | 0.0 | 2026-10-28T23:15 |  |
| rec:lighter_sample | 6 | 0 | 0.0 | 2026-11-02T17:45 |  |
| rec:lightning | 27 | 8 | 29.6 | 2026-11-02T17:43 | 2026-10-28T00:16 |
| rec:liq_feeds | 4 | 0 | 0.0 | 2026-10-28T23:00 |  |
| rec:mempool | 70 | 46 | 65.7 | 2026-11-02T17:43 | 2026-10-28T00:48 |
| rec:option_chain:deribit | 37 | 0 | 0.0 | 2026-11-02T17:43 |  |
| rec:option_chain:ibit | 4 | 0 | 0.0 | 2026-10-28T22:29 |  |
| rec:option_trades:deribit | 43 | 3 | 7.0 | 2026-11-02T17:42 | 2026-10-28T23:08 |
| rec:option_trades_backfill:deribit | 36 | 0 | 0.0 | 2026-11-02T17:43 |  |
| rec:trade_leaderboard | 6 | 0 | 0.0 | 2026-10-28T00:12 |  |
| rec:trade_leaderboard:gmx | 1 | 1 | 100.0 |  | 2026-11-02T17:42 |
| rec:trade_leaderboard:lighter | 1 | 1 | 100.0 |  | 2026-10-28T22:29 |
| src:binance_quarterly | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:blockchain_com | 8 | 1 | 12.5 | 2026-11-02T17:43 | 2026-10-21T20:26 |
| src:cftc | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:coinalyze_daily | 6 | 0 | 0.0 | 2026-10-28T22:39 |  |
| src:coinbase_hourly | 20 | 1 | 5.0 | 2026-11-02T17:43 | 2026-10-26T20:17 |
| src:coingecko_global | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:coinmetrics | 10 | 1 | 10.0 | 2026-11-02T17:42 | 2026-10-21T20:24 |
| src:defillama_cex | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:defillama_stablecoins | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:dvol | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:farside | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:fear_greed | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:fred_macro | 6 | 0 | 0.0 | 2026-10-28T22:40 |  |
| src:mempool_hist | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| src:social | 15 | 1 | 6.7 | 2026-10-28T22:40 | 2026-10-27T19:28 |
| src:tokenized_stocks | 6 | 6 | 100.0 |  | 2026-10-27T22:55 |
| src:yahoo | 7 | 0 | 0.0 | 2026-11-02T17:43 |  |
| trade_review | 8 | 0 | 0.0 | 2026-10-26T17:57 |  |
| wikipedia | 244 | 120 | 49.2 | 2026-10-28T22:39 | 2026-10-28T22:39 |
| wikitotal | 106 | 80 | 75.5 | 2026-10-27T21:43 | 2026-10-28T22:39 |
| youtube | 90 | 0 | 0.0 | 2026-10-28T22:39 |  |

| family | n | distinct error (normalised, first 110 chars) |
|---|---|---|
| app_store | 1 | HTTPError: 502 Server Error: Bad Gateway for url: https://rss.marketingtools.apple.com/api/v2/us/apps/top-free |
| bgeo | 4 | HTTPError: 500 Server Error:  for url: https://bitcoin-data.com/v1/delta-cap |
| fred | 10 | JSONDecodeError: Expecting value: line 1 column 1 (char 0) |
| fred_macro | 1 | WALCL: JSONDecodeError; ECBASSETSW: JSONDecodeError; DFEDTARU: JSONDecodeError; ECBDFR: JSONDecodeError; M2SL: |
| fred_release | 8 | JSONDecodeError: Expecting value: line 1 column 1 (char 0) |
| fred_release | 1 | ReadTimeout: HTTPSConnectionPool(host='api.stlouisfed.org', port=443): Read timed out. (read timeout=15) |
| gdelt | 9 | HTTPError: 429 Client Error: Too Many Requests for url: https://api.gdeltproject.org/api/v2/doc/doc?query=%28b |
| google_trends | 8 | TooManyRequestsError: The request failed: Google returned a response with code 429 |
| google_trends | 4 | TypeError: Retry.__init__() got an unexpected keyword argument 'method_whitelist' |
| hacker_news | 1 | RuntimeError: stopped after 84 rows: ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connec |
| onchain | 6 | ReadTimeout: HTTPSConnectionPool(host='eth.blockscout.com', port=443): Read timed out. (read timeout=15) |
| onchain | 1 | TypeError: sequence item 3: expected str instance, float found |
| onchain | 1 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| options | 1 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:book:perp:binance | 3 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:book:perp:bybit | 4 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:book:perp:hyperliquid | 4 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:book:perp:okx | 5 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:book:perp:okx | 1 | ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response')) |
| rec:book:spot:binance | 3 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:book:spot:coinbase | 3 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:futures_curve:deribit | 5 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:lightning | 8 | ConnectTimeout: HTTPSConnectionPool(host='mempool.space', port=443): Max retries exceeded with url: /api/v1/li |
| rec:mempool | 46 | ConnectTimeout: HTTPSConnectionPool(host='mempool.space', port=443): Max retries exceeded with url: /api/mempo |
| rec:option_trades:deribit | 3 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| rec:trade_leaderboard:gmx | 1 | ChunkedEncodingError: ("Connection broken: ConnectionResetError(54, 'Connection reset by peer')", ConnectionRe |
| rec:trade_leaderboard:lighter | 1 | KeyError: 'account_value' |
| src:blockchain_com | 1 | HTTPError: 404 Client Error: Not Found for url: https://api.blockchain.info/charts/fees-usd-per-transaction?ti |
| src:coinbase_hourly | 1 | ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer')) |
| src:coinmetrics | 1 | HTTPError: 400 Client Error: Bad Request for url: https://community-api.coinmetrics.io/v4/timeseries/asset-met |
| src:social | 1 | OperationalError: database is locked |
| src:tokenized_stocks | 5 | HTTPError: 400 Client Error: Bad Request for url: https://api.geckoterminal.com/api/v2/networks?page=4 |
| src:tokenized_stocks | 1 | ReadTimeout: HTTPSConnectionPool(host='api.geckoterminal.com', port=443): Read timed out. (read timeout=30) |
| wikipedia | 120 | HTTPError: 429 Client Error: Too Many Requests for url: https://wikimedia.org/api/rest_v1/metrics/pageviews/pe |
| wikitotal | 80 | HTTPError: 429 Client Error: Too Many Requests for url: https://wikimedia.org/api/rest_v1/metrics/pageviews/ag |

## Coverage note: not verified, and why
- Behaviour after 17:51Z (the 17:53Z Wikimedia/GDELT 429 storm, today's HL 429s): not in the snapshot. Analogous bursts on 10-28 are verified instead.
- Which connection held the write lock on 10-27 19:28: no trace in fetch_log. Only the mechanism is reproduced.
- Why Binance forceOrder stored 0 rows, Coinbase 300-candle edge, HL weight table, Wikimedia UA policy effect: need network or venue docs (none available).
- The new views.source_health / ui_health.py: reviewed from code only. The "Stale after 48 h" behaviour was not executed against the snapshot.
- Whether the snapshot DBs are row-complete copies: their DDL has no PKs (pandas copy), so PK enforcement was checked in the code SCHEMA only.
- Out of scope / skipped: Lighter/GMX venues and tokenized_stocks (removed mid-task); per-source parsers' numeric correctness (covered by the main session's reference checks).
