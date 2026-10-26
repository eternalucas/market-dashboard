"""Daily mark-to-market P&L, equity and the leaderboard metrics.

Daily P&L per coin (standard mark-to-market):
    pnl_d = pos_{d-1} x (close_d - close_{d-1})        position carried into the day
          + sum over the day's fills of signed_size x (close_d - fill_px)
          - fees_d + funding_d
Closes are the venue's daily candle closes (00:00 UTC boundaries). Summed
over a period this equals realized P&L - fees + funding + the change in
unrealized P&L, without needing entry prices, so it also works when the
history starts mid-position.

Returns are time-weighted: r_d = pnl_d / equity at the end of the previous
day, where equity is the venue's own account-value history. Deposits and
withdrawals change equity but are not returns.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "all": None}


def daily_pnl(fills: pd.DataFrame, funding: pd.DataFrame | None, closes: dict[str, pd.Series],
              end: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per UTC day from the first fill to `end`: mtm, realized,
    fees, funding, fills, missing_price (coins without a close that day)."""
    if fills is None or fills.empty:
        return pd.DataFrame(columns=["mtm", "realized", "fees", "funding", "fills", "missing_price"])
    end = (end or pd.Timestamp.now(tz="UTC")).normalize()
    f = fills.sort_values("ts").copy()
    f["day"] = f["ts"].dt.floor("D")
    f["signed"] = np.where(f["side"] == "B", f["sz"], -f["sz"])
    days = pd.date_range(f["day"].min(), end, freq="D", tz="UTC")
    out = pd.DataFrame(0.0, index=days, columns=["mtm", "realized", "fees", "funding", "fills", "missing_price"])
    for coin, g in f.groupby("coin"):
        c = closes.get(coin)
        if c is None or c.dropna().empty:
            out.loc[out.index >= g["day"].min(), "missing_price"] += 1
            # without prices only realized P&L can be booked for this coin
            d = g.groupby("day").agg(realized=("closed_pnl", "sum"), fees=("fee", "sum"), n=("fee", "size"))
            out.loc[d.index, "mtm"] += d["realized"] - d["fees"]
            out.loc[d.index, "realized"] += d["realized"]
            out.loc[d.index, "fees"] += d["fees"]
            out.loc[d.index, "fills"] += d["n"]
            continue
        c = c.copy()
        c.index = c.index.floor("D")
        c = c[~c.index.duplicated(keep="last")].reindex(days).ffill()
        first = g.iloc[0]
        pos0 = first["start_position"]                      # position held before the first known fill
        d = g.groupby("day").agg(signed=("signed", "sum"), realized=("closed_pnl", "sum"), fees=("fee", "sum"),
                                 n=("fee", "size"))
        flow = g.assign(v=g["signed"] * g["px"]).groupby("day")["v"].sum()
        signed = d["signed"].reindex(days, fill_value=0.0)
        # Positions come from the venue's own start_position on each fill, not a running sum,
        # so a position change that happened without a fill (see reconstruct's position_gaps)
        # can't leave a phantom position being marked for months.
        last_end = (g["start_position"] + g["signed"]).groupby(g["day"]).last()
        first_start = g.groupby("day")["start_position"].first()
        pos_end = last_end.reindex(days).ffill().fillna(pos0)
        pos_start = pos_end.shift(1, fill_value=pos0)
        # where the day's first fill starts from a different position, the venue's figure wins
        pos_start = first_start.reindex(days).combine_first(pos_start)
        prev_close = c.shift(1)
        carry = (pos_start * (c - prev_close)).fillna(0.0)
        # Nothing is known before this coin's first fill. On that day the
        # position held beforehand (pos0) is marked from the first fill's price.
        d0 = first["day"]
        carry[carry.index < d0] = 0.0
        carry[d0] = pos0 * (c[d0] - first["px"]) if pos0 != 0 and pd.notna(c[d0]) else 0.0
        trade = (signed * c - flow.reindex(days, fill_value=0.0)).fillna(0.0)
        out["mtm"] += carry + trade - d["fees"].reindex(days, fill_value=0.0)
        out["realized"] += d["realized"].reindex(days, fill_value=0.0)
        out["fees"] += d["fees"].reindex(days, fill_value=0.0)
        out["fills"] += d["n"].reindex(days, fill_value=0.0)
        out["missing_price"] += c.isna().astype(float)
    if funding is not None and not funding.empty:
        fd = funding.assign(day=funding["ts"].dt.floor("D")).groupby("day")["usdc"].sum()
        fd = fd[fd.index.isin(days)]
        out.loc[fd.index, "funding"] += fd
        out.loc[fd.index, "mtm"] += fd
    return out


def daily_equity(account_values: list[pd.Series], days: pd.DatetimeIndex) -> pd.Series:
    """End-of-day account value from the venue's history (the finest series
    wins where several overlap); carried forward between observations."""
    merged = pd.Series(dtype=float)
    for s in sorted([s for s in account_values if s is not None and not s.empty], key=lambda s: -len(s) / max(
            (s.index[-1] - s.index[0]).total_seconds(), 1)):
        merged = merged.combine_first(s) if not merged.empty else s.copy()
    if merged.empty:
        return pd.Series(np.nan, index=days)
    merged = merged.sort_index()
    eod = merged.resample("1D").last().reindex(days).ffill()
    eod[eod.index < merged.index[0].floor("D")] = np.nan
    return eod


def window_metrics(daily: pd.DataFrame, equity: pd.Series, trips_legacy: pd.DataFrame, days: int | None,
                   now: pd.Timestamp | None = None) -> dict:
    """Leaderboard metrics for the last `days` (None = all history)."""
    from ..indicators import trades as tr
    now = (now or pd.Timestamp.now(tz="UTC")).normalize()
    start = now - pd.Timedelta(days=days - 1) if days else daily.index.min() if not daily.empty else now
    d = daily[(daily.index >= start) & (daily.index <= now)] if not daily.empty else daily
    out: dict = {"window_start": start, "covered_from": daily.index.min() if not daily.empty else None}
    out["window_partial"] = bool(days and not daily.empty and daily.index.min() > start)
    pnl = d["mtm"] if not d.empty else pd.Series(dtype=float)
    out["pnl"] = float(pnl.sum()) if len(pnl) else 0.0
    out["realized"] = float(d["realized"].sum()) if len(d) else 0.0
    out["fees_paid"] = float(d["fees"].sum()) if len(d) else 0.0
    out["funding"] = float(d["funding"].sum()) if len(d) else 0.0
    out["active_days"] = int((d["fills"] > 0).sum()) if len(d) else 0
    out["days"] = len(d)
    out["missing_price_days"] = int((d["missing_price"] > 0).sum()) if len(d) else 0
    eq_prev = equity.shift(1).reindex(d.index) if len(d) else pd.Series(dtype=float)
    if len(d):   # no observation the day before: start-of-day equity = end of day - that day's P&L
        eq_prev = eq_prev.fillna(equity.reindex(d.index) - pnl)
    r = (pnl / eq_prev.where(eq_prev > 0)).replace([np.inf, -np.inf], np.nan)
    r = r[r.abs() < 5]                                  # a day's return beyond +/-500% means equity is unreliable
    out["return_days"] = int(r.notna().sum())
    out["roe"] = float(np.prod(1 + r.dropna()) - 1) if r.notna().any() else np.nan
    if r.notna().sum() >= 2 and r.std() > 0:
        out["sharpe"] = float(r.mean() / r.std() * math.sqrt(365))
        downside = math.sqrt(float((np.minimum(r.dropna(), 0) ** 2).mean()))
        out["sortino"] = float(r.mean() / downside * math.sqrt(365)) if downside > 0 else np.nan
    else:
        out["sharpe"] = out["sortino"] = np.nan
    cum = pnl.cumsum()
    out["max_dd_usd"] = float((cum - cum.cummax().clip(lower=0)).min()) if len(cum) else 0.0
    idx = (1 + r.fillna(0)).cumprod()
    out["max_dd_pct"] = float((idx / idx.cummax() - 1).min()) if len(idx) else np.nan
    weeks = pnl.resample("W-SUN").sum() if len(pnl) else pnl
    active_weeks = d["fills"].resample("W-SUN").sum() if len(d) else pnl
    weeks = weeks[(active_weeks > 0) | (weeks != 0)] if len(weeks) else weeks
    out["pct_weeks_profitable"] = float((weeks > 0).mean()) if len(weeks) else np.nan
    out["weeks"] = len(weeks)
    closed = trips_legacy[(~trips_legacy["open"]) & (trips_legacy["close_time"] >= start)] if not trips_legacy.empty else trips_legacy
    if "exit_unobserved" in closed:                     # exit P&L unknown -> not a trade statistic
        closed = closed[~closed["exit_unobserved"]]
    st = tr.stats(closed) if not closed.empty else {}
    out.update({f"trip_{k}": v for k, v in st.items()})
    out["trips"] = int(st.get("trades", 0))
    out["profit_factor"] = st.get("profit_factor", np.nan)
    best = float(closed["net"].max()) if not closed.empty else np.nan
    out["top_trade_share"] = best / out["pnl"] if out["pnl"] > 0 and best == best else np.nan
    return out
