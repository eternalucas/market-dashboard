"""Cohorts and flows from watched wallets and Hyperliquid accounts."""
from __future__ import annotations

import numpy as np
import pandas as pd

SIZE_BUCKETS = [0, 100_000, 1_000_000, 10_000_000, np.inf]
SIZE_LABELS = ["< $100k", "$100k-1M", "$1M-10M (whale)", "> $10M (mega)"]

HL_BUCKETS = [0, 10_000, 100_000, 1_000_000, 10_000_000, np.inf]
HL_LABELS = ["Shrimp (<$10k)", "Fish ($10k-100k)", "Dolphin ($100k-1M)", "Whale ($1M-10M)", "Leviathan (>$10M)"]


def size_cohort(usd: pd.Series) -> pd.Series:
    return pd.cut(usd, SIZE_BUCKETS, labels=SIZE_LABELS, right=False)


def entity_flows(transfers: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
    """USD moved between entities. Addresses not on the watchlist show as
    'Other'; transfers where neither side is watched are dropped."""
    t = transfers.copy()
    t["from_entity"] = t["from"].str.lower().map(labels).fillna("Other")
    t["to_entity"] = t["to"].str.lower().map(labels).fillna("Other")
    t = t[(t["from_entity"] != "Other") | (t["to_entity"] != "Other")]
    t = t[t["from_entity"] != t["to_entity"]]
    return (t.groupby(["from_entity", "to_entity"])["usd"].agg(["sum", "count"])
             .rename(columns={"sum": "usd", "count": "transfers"}).sort_values("usd", ascending=False).reset_index())


def wallet_activity(last_tx: pd.Series, now: pd.Timestamp) -> pd.Series:
    """Frequency cohort from the most recent transaction time."""
    days = (now - last_tx).dt.days
    return pd.cut(days, [-1, 7, 30, 365, np.inf],
                  labels=["Active (7d)", "Recent (30d)", "Quiet (1y)", "Dormant (>1y)"])


def hl_size_cohort(account_value: pd.Series) -> pd.Series:
    return pd.cut(account_value, HL_BUCKETS, labels=HL_LABELS, right=False)


def hl_turnover_cohort(month_vlm: pd.Series, account_value: pd.Series) -> pd.Series:
    """How actively an account trades: monthly volume / account size."""
    turnover = month_vlm / account_value.replace(0, np.nan)
    return pd.cut(turnover, [-np.inf, 2, 20, np.inf], labels=["Low (<2x/mo)", "Medium (2-20x)", "High (>20x)"])


def cohort_positioning(positions: pd.DataFrame) -> pd.DataFrame:
    """positions: cohort, coin, size (signed), value_usd.
    Per cohort and coin: long and short notional, net, and long share."""
    p = positions.copy()
    p["long_usd"] = np.where(p["size"] > 0, p["value_usd"], 0.0)
    p["short_usd"] = np.where(p["size"] < 0, p["value_usd"], 0.0)
    g = p.groupby(["cohort", "coin"], observed=True)[["long_usd", "short_usd"]].sum()
    g["net_usd"] = g["long_usd"] - g["short_usd"]
    g["long_share"] = g["long_usd"] / (g["long_usd"] + g["short_usd"]).replace(0, np.nan)
    g["accounts"] = p.groupby(["cohort", "coin"], observed=True)["address"].nunique()
    return g.reset_index()
