"""Post-trade review: turn raw fills into round-trip trades, measure them,
and flag recurring mistakes with a rule that would fix each one.

A round trip starts when a position opens from flat and ends when it returns
to flat. A flip (long straight to short) closes one trip and opens the next
on the same fill.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-9


def duration(hours: float) -> str:
    """2.5 -> '2.5h', 0.25 -> '15 min', 0.004 -> '14 s'."""
    if hours != hours:
        return "n/a"
    if hours >= 1:
        return f"{hours:.1f}h"
    if hours * 60 >= 1:
        return f"{hours * 60:.0f} min"
    return f"{hours * 3600:.0f} s"


def _usd(x: float) -> str:
    """-$12 rather than $-12, with cents for small amounts."""
    dp = 2 if abs(x) < 100 else 0
    return f"{'-' if x < 0 else ''}${abs(x):,.{dp}f}"


def round_trips(fills: pd.DataFrame) -> pd.DataFrame:
    """fills needs: coin, time, px, sz, side ('B' buy / 'A' sell),
    startPosition (signed size before the fill), closedPnl, fee, crossed.

    Since Trade Review v2 this runs the shared engine in
    src/review/reconstruct.py (flip fees split by quantity, liquidations
    flagged, positions predating the history excluded and counted)."""
    from ..review.reconstruct import from_legacy_fills, reconstruct, to_legacy
    trips, _ = reconstruct(from_legacy_fills(fills))
    return to_legacy(trips) if not trips.empty else pd.DataFrame()


# --------------------------------------------------------------------------- #
# Is this a person or a bot?
# --------------------------------------------------------------------------- #

def trading_style(closed: pd.DataFrame) -> dict:
    """Median holding time and trades per active day. Market-making and
    arbitrage bots hold for seconds and trade hundreds of times a day; a
    discretionary trader holds for minutes to days."""
    if closed.empty:
        return {"trades": 0, "median_hold_min": 0.0, "trades_per_day": 0.0}
    days = closed["open_time"].dt.date.nunique()
    return {"trades": len(closed),
            "median_hold_min": float(closed["hold_hours"].median() * 60),
            "trades_per_day": len(closed) / max(days, 1)}


def looks_human(style: dict, min_trades: int = 30, min_hold_min: float = 15, max_per_day: float = 25) -> bool:
    return (style["trades"] >= min_trades and style["median_hold_min"] >= min_hold_min
            and style["trades_per_day"] <= max_per_day)


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #

def stats(closed: pd.DataFrame) -> dict:
    if closed.empty:
        return {}
    wins, losses = closed[closed["net"] > 0], closed[closed["net"] <= 0]
    equity = closed.sort_values("close_time")["net"].cumsum()
    dd = equity - equity.cummax()
    gross_profit = closed.loc[closed["pnl"] > 0, "pnl"].sum()
    streak = longest_losing_streak(closed.sort_values("close_time")["net"])
    return {
        "trades": len(closed),
        "win_rate": len(wins) / len(closed),
        "avg_win": wins["net"].mean() if len(wins) else 0.0,
        "avg_loss": losses["net"].mean() if len(losses) else 0.0,
        "payoff_ratio": (wins["net"].mean() / abs(losses["net"].mean())) if len(wins) and len(losses) and losses["net"].mean() else np.nan,
        "profit_factor": wins["net"].sum() / abs(losses["net"].sum()) if losses["net"].sum() else np.inf,
        "expectancy": closed["net"].mean(),
        "net_pnl": closed["net"].sum(),
        "fees": closed["fees"].sum(),
        "fee_share_of_gross_profit": closed["fees"].sum() / gross_profit if gross_profit > 0 else np.nan,
        "max_drawdown": dd.min(),
        "longest_losing_streak": streak,
        "median_hold_win_h": wins["hold_hours"].median() if len(wins) else np.nan,
        "median_hold_loss_h": losses["hold_hours"].median() if len(losses) else np.nan,
    }


def longest_losing_streak(nets: pd.Series) -> int:
    best = run = 0
    for v in nets:
        run = run + 1 if v <= 0 else 0
        best = max(best, run)
    return best


def breakdown(closed: pd.DataFrame, by: str) -> pd.DataFrame:
    g = closed.groupby(by, observed=True)["net"]
    out = pd.DataFrame({"trades": g.size(), "win_rate": g.apply(lambda x: (x > 0).mean()),
                        "expectancy": g.mean(), "net": g.sum()})
    return out.sort_values("net")


def add_buckets(closed: pd.DataFrame) -> pd.DataFrame:
    df = closed.copy()
    df["entry_hour_utc"] = pd.cut(df["open_time"].dt.hour, [0, 4, 8, 12, 16, 20, 24], right=False,
                                  labels=["00-04", "04-08", "08-12", "12-16", "16-20", "20-24"])
    df["weekday"] = df["open_time"].dt.day_name()
    df["hold"] = pd.cut(df["hold_hours"], [-0.01, 1, 4, 24, 72, np.inf],
                        labels=["<1h", "1-4h", "4-24h", "1-3d", ">3d"])
    df["size_quintile"] = pd.qcut(df["max_notional"].rank(method="first"), 5,
                                  labels=["Smallest", "Q2", "Q3", "Q4", "Largest"])
    return df


# --------------------------------------------------------------------------- #
# Recurring errors -> rules
# --------------------------------------------------------------------------- #

def recurring_errors(closed: pd.DataFrame, min_trades: int = 5) -> pd.DataFrame:
    """Each row: a pattern found in the trades, the evidence, a rule that
    would address it, and what the flagged trades made or lost in total.
    'Impact' = P&L of the flagged trades; a negative number is what the
    rule would have saved."""
    if len(closed) < min_trades:
        return pd.DataFrame()

    def worse(flagged: pd.Series, rest: pd.Series) -> bool:
        """Flagged trades lose money on average, or make clearly less than the
        rest (by more than a quarter of the rest's average)."""
        f, r = flagged.mean(), rest.mean()
        return f < 0 or f < r - 0.25 * abs(r)
    df = add_buckets(closed).sort_values("open_time").reset_index(drop=True)
    flags = []

    def flag(name, finding, rule, mask):
        flags.append({"pattern": name, "finding": finding, "rule": rule,
                      "trades_flagged": int(mask.sum()), "impact_usd": float(df.loc[mask, "net"].sum())})

    # 1. Holding losers longer than winners (disposition effect)
    w, l_ = df[df["net"] > 0], df[df["net"] <= 0]
    lm, wm = l_["hold_hours"].median() if len(l_) else 0, w["hold_hours"].median() if len(w) else 0
    if len(w) >= 3 and len(l_) >= 3 and lm > 1.5 * wm and lm - wm >= 10 / 60:   # at least 10 min apart
        cap = float(w["hold_hours"].quantile(0.75))
        mask = (df["net"] <= 0) & (df["hold_hours"] > cap)
        flag("Losers held too long",
             f"Median hold: losers {duration(lm)} vs winners {duration(wm)}",
             f"Time stop: exit any trade still red after {duration(cap)} (75% of winners were done by then)", mask)

    # 2. Revenge sizing: big trade opened soon after a loss
    med = df["max_notional"].median()
    closes = df.dropna(subset=["close_time"]).sort_values("close_time")
    loss_times = closes.loc[closes["net"] < 0, "close_time"].values
    after_loss = np.array([((loss_times <= t) & (loss_times > t - np.timedelta64(60, "m"))).any()
                           for t in df["open_time"].values]) if len(loss_times) else np.zeros(len(df), bool)
    mask = pd.Series(after_loss, index=df.index) & (df["max_notional"] > 1.5 * med)
    if mask.sum() >= 3 and worse(df.loc[mask, "net"], df.loc[~mask, "net"]):
        flag("Sizing up after a loss",
             f"{int(mask.sum())} trades opened within 1h of a loss at >1.5x median size; expectancy "
             f"{_usd(df.loc[mask, 'net'].mean())} vs {_usd(df.loc[~mask, 'net'].mean())} for the rest "
             f"(win rate {df.loc[mask, 'net'].gt(0).mean():.0%} vs {df.loc[~mask, 'net'].gt(0).mean():.0%})",
             "After a losing trade, cap the next hour's position size at the median", mask)

    # 3. Averaging down
    mask = df["added_while_losing"]
    if mask.sum() >= 3 and worse(df.loc[mask, "net"], df.loc[~mask, "net"]):
        flag("Averaging down",
             f"{int(mask.sum())} trades added to a losing position; expectancy "
             f"{_usd(df.loc[mask, 'net'].mean())} vs {_usd(df.loc[~mask, 'net'].mean())} for the rest",
             "Only add to positions that are in profit", mask)

    # 4 & 5. Coins and sessions with negative expectancy
    for col, what, label in (("coin", "coin", "Losing market"), ("entry_hour_utc", "entry window (UTC)", "Losing time of day")):
        b = breakdown(df, col)
        for key, r in b[(b["trades"] >= max(min_trades, 10)) & (b["expectancy"] < 0)].iterrows():
            flag(f"{label}: {key}",
                 f"{int(r['trades'])} trades in {what} {key}: win rate {r['win_rate']:.0%}, "
                 f"expectancy {_usd(r['expectancy'])}",
                 f"Stop trading {key}" if col == "coin" else f"No new entries in {key} UTC", df[col] == key)

    # 6. Fees eating the edge
    gross = df.loc[df["pnl"] > 0, "pnl"].sum()
    if gross > 0 and df["fees"].sum() / gross > 0.25:
        flag("Fees eating the edge",
             f"Fees are {df['fees'].sum() / gross:.0%} of gross profit; {df['taker_share'].mean():.0%} of fills are taker",
             "Enter with limit (maker) orders where possible", df["taker_share"] > 0.5)

    # 7. Overtrading days
    per_day = df.groupby(df["open_time"].dt.date).size()
    busy = per_day[per_day > 2 * per_day.median()].index
    mask = df["open_time"].dt.date.isin(busy)
    if len(busy) >= 2 and df.loc[mask, "net"].mean() < df.loc[~mask, "net"].mean():
        flag("Overtrading days",
             f"{len(busy)} days with more than {2 * per_day.median():.0f} trades; expectancy "
             f"{_usd(df.loc[mask, 'net'].mean())} on those days vs {_usd(df.loc[~mask, 'net'].mean())}",
             f"Daily cap of {int(max(2 * per_day.median(), 1))} new trades", mask)

    out = pd.DataFrame(flags)
    return out.sort_values("impact_usd").reset_index(drop=True) if not out.empty else out
