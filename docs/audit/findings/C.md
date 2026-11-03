# Subagent C: test-suite audit

Work copy: `/home/claude/audit/work/C/repo`, re-copied after the coordinator said the repo had changed (186 tests, `tests/test_ui_fixes.py` added). Original repo was not touched. Mutant diffs are in `/home/claude/audit/work/C/mutants/`. Harness scripts are in `/home/claude/audit/work/C/scripts/mutate.py` and `/home/claude/audit/work/C/plugins/{nonet,fswatch,reverse}.py`. Environment: Python 3.13.16, pandas 3.0.5, pytest 9.1.1, streamlit 1.66.0.

## 1. Baseline
| Run | Result |
|---|---|
| `pytest -q -p no:randomly` | **186 passed in 9.04s** (the first copy had 194 passed in 8.38s, before the repo changed) |
| `--cov=src --cov=app` | 186 passed, **TOTAL 50%** (9840 stmts, 4956 missed) |
| `-W error::DeprecationWarning -W error::FutureWarning -W error::PendingDeprecationWarning` | 194 passed on the first copy, so no deprecations on 3.13 / pandas 3.0.5 |
| Sockets and DNS blocked (`-p nonet`) | all passed, no NETHIT. **Tests make no network calls.** |
| 8 pytest-randomly seeds plus reversed order | all 194 passed (first copy). No order dependence among tests (see C-01 for why). |
| Warnings | 27–31 `ResourceWarning: unclosed database` (sqlite connections the tests never close) |

The plan says "194 tests, UI 0%, pipeline 0%". **Correction:** there are now 186 tests. `pipeline.py` is at **11%**, not 0% (it was 0% on the first copy). The UI modules are mostly 0%: `ui_backtest`, `ui_batch`, `ui_calendar`, `ui_diagnosis`, `ui_flows`, `ui_header`, `ui_health` and `ui_onchain` are 0%, but `ui_review` is 13% and `ui_studio` is 22%. **`app.py` is never imported**, so it is missing from the coverage table entirely, which is effectively 0%.

### Coverage table (key modules, from the re-copied repo)
| Module | Cover | | Module | Cover |
|---|---|---|---|---|
| pipeline.py | 11% | | review/reconstruct.py | 95% |
| recorder.py | 27% | | review/replay.py | 86% |
| views.py | 60% | | indicators/positioning.py | 85% |
| collectors/derivatives.py | 34% | | collectors/coinalyze.py | 42% |
| ui_* (8 modules) | 0% | | ui_review / ui_studio | 13% / 22% |
| app.py | not imported | | TOTAL | 50% |

These rows came from the first copy and were not re-checked after the re-copy:
- 0%: `sources/coinalyze_daily`, `coinbase_hourly`, `coingecko`, `registry`, `fred_macro`, `mempool_hist`, `deribit_hist`, `flows.py`, `config.py`, `collectors/prices`, `collectors/fred`
- 19–25%: `metrics/books`, `metrics/extra`, `metrics/markets`, `metrics/social`, `review/jobs`
- Full table for that copy: `/home/claude/audit/work/C/coverage.txt`

## 2. Meaningfulness review
I read these files line by line: test_catalog_layout, test_collectors, test_flow, test_funding_intervals, test_numfmt, test_recorder, and the source_health, youtube and bgeometrics tests. **The other files I judged only from grep and mutation results**, so that classification is partly UNVERIFIED. Overall, most tests are class (a): they check hand-worked numbers. For example, `test_recorder.py:31-65` checks book maths and `test_flow.py:17-89` checks CVD, OI and funding values.

Weak tests, with file:line:
- **(b) smoke/shape:**
  - `test_catalog_layout.py:5-16` checks only structure (even panel count, IDs exist).
  - `test_recorder.py:122-124` checks the heatmap with only `not h.empty`, `% 50 == 0` and `usd.max() > 0`. The comment says each bucket should be "2 venues × notional", but that is never asserted.
  - `test_deribit_trades` at `test_recorder.py:81` checks only length and one IV.
  - `test_heatmap_fixed_grid` at `test_recorder.py:57` uses `<=` set membership, so an empty grid would pass.
  - grep finds 12 more `not empty` / `> 0`-only asserts across `test_liquidations`, `test_metrics`, `test_sources`, `test_review_backtest` and `test_review_diagnosis`.
- **(c) tautological:**
  - `test_flow.py:77-78`: expected net delta is built from `d` taken out of the function's own output (`20*d-10`). A wrong delta formula would still pass. Only the put/call identity is tested.
- **(d) time/env dependent:**
  - `test_sources.py:89` uses `pd.Timestamp.now()` (a rolling 24h window, so low risk).
  - `test_recorder.py:127` depends on a real lock file (C-02).
  - `test_recorder.py:110` depends on the real DB (C-01).
  - `test_ui_fixes.py:129` reads the real config.yaml (C-03).
- **Seeding:** every RNG use is seeded (`default_rng(seed)` at `test_liquidations.py:42`, `test_review_backtest.py:54,164,234,246,276`, `test_review_reconstruct.py:107`).

## 3. Mutation check (24 mutants, full suite with `-x`, source restored after each; `diff -rq src` against the original is empty)
| Function | Mutant (diff in mutants/) | Result | Killing test |
|---|---|---|---|
| to_8h (derivatives.py:100) | to8h_1: `8/hours` → `8/(hours+1)` | KILLED | test_funding_intervals::test_history_interval_from_spacing |
| | to8h_2: return raw `s` | KILLED | same |
| | to8h_3: rolling median → mean | KILLED | ::test_history_interval_change_and_missing_payment |
| | to8h_4: clip upper 8 → 24 | **SURVIVED** | – |
| | to8h_5: `.bfill()` → `.fillna(8)` (first row gap) | **SURVIVED** | – |
| reconstruct | rec_1: B/A sign swap | KILLED | test_phase2::test_round_trips_open_add_close_and_flip |
| | rec_2: adds `>` → `>=` | **SURVIVED** | – |
| | rec_3: close share = 1.0 | KILLED | same |
| | rec_4: entry_notional drops ×px | KILLED | same |
| | rec_5: funding window `ts<=close` → `<` | **SURVIVED** | – |
| replay.simulate | sim_1: limit offset sign | KILLED | test_review_backtest::test_limit_entry_fills_only_if_price_trades_through |
| | sim_2: tp priority beats stop | KILLED | ::test_stop_fills_before_target_in_the_same_bar |
| | sim_3: stop gap fill ignores open | KILLED | ::test_a_gap_through_the_stop_fills_at_the_open |
| | sim_4: drop funding from net | KILLED | ::test_time_stop_only_closes_losing_trades_and_scales_funding |
| | sim_5: time stop `<0` → `<=0` (breakeven) | **SURVIVED** | – |
| summarize_perps | sum_1: basis inverted | KILLED | test_indicators::test_summarize_perps_weights_by_oi |
| | sum_2: equal weights | KILLED | same |
| | sum_3: spread = max only | **SURVIVED** | – |
| | sum_4: annual = f8×365 (should be ×1095) | **SURVIVED** | – |
| views.source_health | sh_1: failing `!=1` → `==0` (NULL ok) | **SURVIVED** | – |
| | sh_2: stale keyed on last_success | **SURVIVED** | – |
| | sh_3: stale `>` → `<` | KILLED | test_ui_fixes::test_source_health_statuses |
| | sh_4: Last error shown when OK | **SURVIVED** | – |
| | sh_5: status order reversed | KILLED | same |

**Score: 14 of 24 killed (58%).** The weakest are source_health (2 of 5) and summarize_perps (2 of 4). In summarize_perps, the funding annualisation is completely unguarded: a 3× error survives.

## 4. Missing tests (highest value first)
1. **Funding annualisation.** `annualize_funding(0.0001) == 0.1095`. `summarize_perps` with two venues must return the exact `oi_weighted_funding_annual`, `funding_spread_8h` (max − min, with one negative venue) and `n_exchanges`. This kills sum_3 and sum_4.
2. **Coinalyze BASE vs QUOTE units.** Feed one OI or volume payload once with the market flagged BASE (coin-denominated) and once QUOTE (USD-denominated), with a known price. Assert both convert to the same USD value. The 0–42% coverage on the coinalyze modules means no such test exists today.
3. **to_8h edge cases.** (a) 24h-interval or daily-sampled data: decide whether the clip at 8 is intended and assert it, which kills to8h_4. (b) First-row behaviour after a gap: the first output must use the next spacing, not a default, which kills to8h_5. (c) A single-point series is returned unchanged. (d) An unsorted index is sorted.
4. **source_health staleness and failure flags.**
   - A source whose last attempt is recent and succeeded but whose last_success is old must still be OK (kills sh_2).
   - `ok` = NULL must count as Failing (kills sh_1).
   - An OK row must have an empty "Last error" even when the stored message is non-empty (kills sh_4).
   - A source that never succeeded must show "never".
   - The STALE_AFTER boundary must be tested exactly.
5. **Recorder scheduling.** With `_now` frozen, check which jobs are due at given minutes (books every minute, others on their own cadence). Check that a job that raises is logged in fetch_log with ok=0 and does not stop the other jobs. Recorder coverage is 27%.
6. **Stuck "running" jobs** (`review/jobs.py`, 22%). A job row left in `running` with a heartbeat older than the timeout must be marked failed or re-queued on startup. A fresh `running` job must not be stolen.
7. **Rate-limit backoff.** Mock `get_json` to raise a 429 twice, then succeed. Assert the retry count and sleep schedule with `time.sleep` monkeypatched. Assert it gives up after N attempts, and that fetch_log records the 429 message (`collectors/http.py` is at 42%).
8. **Partial-bar handling.** When the latest candle is still forming at `now`, the aggregators and CVD should drop it or flag it. Assert the last bar is excluded when its close time is after `now`.
9. **reconstruct boundaries.**
   - A fill exactly at close_ts must attribute funding to the trip (kills rec_5).
   - A zero-size or exact-reduce fill must not count as an add (kills rec_2).
10. **simulate breakeven time stop.** A trade exactly at its average price at the time-stop bar must not close (kills sim_5).
11. **Pipeline smoke with fakes.** With every collector monkeypatched to return a fixture, `pipeline.update_all` (or the main entry point) should write the expected series names into a tmp DB. This lifts pipeline coverage above 11%.
12. **UI import smoke.** Import `app.py` and every `ui_*` module under `streamlit.testing.v1.AppTest` with a fixture DB. Assert there is no exception and no `st.error` output.

## 5. Hidden coupling and version notes
- **Real DB, lock file and config are touched.** Verified with an open/sqlite trace (`-p fswatch`): `test_job_books_idempotent` → `<repo>/data/market.db`; `test_lock_is_exclusive` → `<repo>/data/recorder.lock`; `test_youtube_views…` → `<repo>/config.yaml`.
- **Order dependence between tests: none observed.** Collection always imports `src.storage` before `test_recorder`, so the full suite always hits the real DB. Running `tests/test_recorder.py` alone uses a tmp DB, with no trace hits (shown on the first copy).
- **Python 3.15: UNVERIFIED**, because no 3.15 interpreter is available here. On 3.13, deprecations-as-errors are clean. grep found no `utcnow`, `utcfromtimestamp`, `fillna(method=)` or `applymap` in src or tests. The stale `tests/__pycache__` contains cpython-313 `.pyc` files, which suggests the owner has also run 3.13. The suite leaves sqlite connections unclosed, and 3.13+ emits ResourceWarning for them; if a future pytest or Python turns unraisable warnings into errors, these would fail.

## Findings
| ID | Sev | Area | file:line | What's wrong | Evidence | Repro | Root cause | Proposed fix | Conf |
|---|---|---|---|---|---|---|---|---|---|
| C-01 | **High** | Test isolation | tests/test_recorder.py:13,110-120; src/storage.py:20 | In the full suite, `test_job_books_idempotent` writes synthetic books (mid 80000, ts 2026-10-21 12:10) into the **real `data/market.db`**. Its `COUNT(*)==3` assert then fails whenever the real DB already holds book data. | fswatch trace: `('sqlite','<repo>/data/market.db')`. The original repo's DB already contains exactly these rows: `book_metrics 3`, `heatmap 320`, all ts `2026-10-21T12:10:00+00:00` (read-only query). Adding 1 row to the copy's DB → `FAILED test_job_books_idempotent - assert 4 == 3`. Run alone, the test uses a tmp DB. | Insert any book_metrics row into data/market.db, then run `pytest -q` | `os.environ.setdefault("MARKET_DB", …)` runs at module import, after earlier test modules have already imported `src.storage` and frozen `DB_PATH`. The same pattern is in `test_sources.py:13` and `test_social_sources.py:12`. | Add a `tests/conftest.py` that sets `MARKET_DB` and `MARKET_CONFIG` before any `src` import (top of conftest, or `pytest_configure`). Use the `tmp_path` connection in this test. Delete the polluted rows from the owner's DB. | High |
| C-02 | Med | Test isolation | tests/test_recorder.py:127-131; src/recorder.py:44 | `test_lock_is_exclusive` uses the real `data/recorder.lock`. It **fails whenever the recorder is running** on the owner's Mac, and while it holds the lock it can block the recorder from starting. | Holding a flock on the copy's lock → `FAILED test_lock_is_exclusive - assert (None is not N…` | Start the recorder (or flock data/recorder.lock), then run `pytest` | `LOCK_PATH` is derived from the module-level `DB_PATH` | Monkeypatch `recorder.LOCK_PATH` to `tmp_path` | High |
| C-03 | Med | Test isolation | tests/test_ui_fixes.py:129-141; src/config.py:9 | The test loads the owner's real `config.yaml`, which holds API keys, through `Ctx`. The result can depend on personal config. | fswatch: `('open','<repo>/config.yaml')` | `pytest tests/test_ui_fixes.py -s -p fswatch` | `MARKET_CONFIG` is not set for tests | Set `MARKET_CONFIG` to `config.example.yaml` in conftest | High |
| C-04 | Med | Coverage gap | src/indicators/positioning.py:18-20,46 | Funding annualisation and funding spread are untested: ×365 instead of ×1095, and max-only spread, both survive. | Mutants sum_3 and sum_4 survived | `python3 scripts/mutate.py` | No test asserts these fields | Missing test #1 | High |
| C-05 | Med | Coverage gap | src/views.py:214-224 | source_health's failure semantics (NULL ok), staleness basis, and Last-error masking are unguarded. | Mutants sh_1, sh_2 and sh_4 survived | same | Only one happy-path fixture test (`test_ui_fixes.py:87`) | Missing test #4 | High |
| C-06 | Med | Coverage gap | src/collectors/derivatives.py:107-109 | to_8h's interval clip and first-row gap fill are unguarded. Daily-paying contracts would be silently capped at 8h. | to8h_4 and to8h_5 survived | same | Tests use only 1h/4h/8h spacing | Missing test #3 | High |
| C-07 | Low | Coverage gap | src/review/reconstruct.py:132,194; src/review/replay.py:296 | Boundary conditions (`>` vs `>=` for adds, the funding window end, the breakeven time stop) are unguarded. | rec_2, rec_5 and sim_5 survived | same | No exact-boundary fixtures | Missing tests #9 and #10 | High |
| C-08 | Med | Coverage gap | app.py, src/ui_*.py, src/pipeline.py | app.py is never imported. 8 UI modules are at 0%, pipeline at 11%, recorder at 27%. Coinalyze unit conversion, backoff and jobs are untested. | Coverage table above | `pytest --cov=src --cov=app` | No UI or pipeline harness | Missing tests #2, #5–#8, #11, #12 | High |
| C-09 | Low | Weak test | tests/test_flow.py:77-78 | The expected net delta is computed from the function's own output (tautological). | Code reading | – | – | Assert against a Black-Scholes delta computed by hand (≈0.529 for ATM, 30d, IV 0.5, r=0) | Med |
| C-10 | Low | Hygiene | tests (several) | 27–31 unclosed sqlite connections (ResourceWarning) | `pytest -W default` output | – | Connections are not closed and there are no fixtures to close them | Use yield fixtures that call `con.close()` | High |
| C-11 | Info | Plan accuracy | – | The plan's "194 tests / pipeline 0%" is out of date: now 186 tests, pipeline 11%, ui_review 13%, ui_studio 22%. | Baseline above | – | – | Update the plan | High |
