# Lessons (recurring bug patterns found in the Nov 2026 audit)

- **Every number on screen needs a scheduled writer and an age.** Series refreshed only by a manual button went 7 days stale while the page called them "today". Rule: each UI series maps to a recorder job (tested), and every chart/total shows its as-of date when stale.
- **Success must mean data arrived.** Adapters that swallow per-item errors and return 0 rows were logged ok=1 and reset their freshness window. Rule: 0 rows + item errors = failure; freshness counts only successes that stored rows.
- **Never guess units from magnitude.** A "×100 if small" heuristic fired on real percent data when funding was low. Rule: units come from the source's documented contract, once, at load time.
- **One convention per quantity, normalised in one place.** Bybit OI is two-sided; one tab halved it, another didn't. Funding intervals differ by contract (1h/4h/8h). Rule: normalise at the loader, test that two tabs agree.
- **Calendar offsets, not row offsets.** YoY by `shift(12)` silently compared with 13 months ago when a month was missing. Rule: offset by time (`DateOffset(months=12)`), missing base → NaN.
- **Don't aggregate stock quantities with sum.** Daily resample summed hourly order-book depth. Rule: each spec declares its aggregation (mean/last for levels, sum for flows).
- **Partial periods are not periods.** Today's unfinished day was headlined as a full day. Rule: daily views use complete days only.
- **Don't trust identifiers for ordering.** Hyperliquid tids aren't sequential within a millisecond; ordering by tid created fake position gaps. Rule: order by the data's own invariant (position chain).
- **Returns must remove transfers.** ROE without the deposit/withdrawal ledger turned deposits into gains. Rule: r = (E_t − E_{t−1} − flows) / E_{t−1}.
- **Tests must never see real paths.** Module-level env overrides ran after the first import; the suite wrote fake rows into the real DB. Rule: conftest sets all paths before any src import, plus a guard test.
- **A snapshot is a point in time.** Several "defects" were snapshot timing (sources that ran minutes later). Rule: re-check suspected staleness against the live DB before reporting.
- **Background jobs need heartbeats.** A process exit left a job "running" forever and disabled its button.
