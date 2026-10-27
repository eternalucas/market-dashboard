"""Cohorts and flows from watched wallets and Hyperliquid accounts."""
from __future__ import annotations

import numpy as np
import pandas as pd

SIZE_BUCKETS = [0, 100_000, 1_000_000, 10_000_000, np.inf]
SIZE_LABELS = ["< $100k", "$100k-1M", "$1M-10M (whale)", "> $10M (mega)"]

HL_BUCKETS = [0, 10_000, 100_000, 1_000_000, 10_000_000, np.inf]
HL_EMPTIED_BELOW = 100        # $: account emptied/withdrawn; current size says nothing
HL_BOT_TURNOVER = 100         # monthly volume / account above this = market-maker / HFT-like
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


# --------------------------------------------------------------------------- #
# Bitcoin watched-wallet flows over a chosen window
# --------------------------------------------------------------------------- #

def flow_group(entity: str, label: str) -> str:
    """Wallets are grouped by entity (all Binance wallets = 'Binance'), except
    unlabelled rich-list whales, which are separate owners: kept by label."""
    return label if entity in ("Unlabelled whale", "", None) else entity


def btc_moves(tx: pd.DataFrame, wl: pd.DataFrame, daily_px: pd.Series) -> pd.DataFrame:
    """One row per (transaction, entity). A transaction between two wallets
    of the same entity nets to ~0 for that entity and is marked internal, so
    it never counts as a flow. USD uses that day's BTC close."""
    if tx.empty:
        return pd.DataFrame()
    meta = wl.set_index("address")
    t = tx.copy()
    t["time"] = pd.to_datetime(t["time"], utc=True, format="ISO8601")
    t["label"] = t["address"].map(meta["label"])
    t["category"] = t["address"].map(meta["category"])
    t["group"] = [flow_group(e, lab) for e, lab in zip(t["address"].map(meta["entity"]), t["label"])]
    g = t.groupby(["txid", "group"], as_index=False).agg(
        time=("time", "first"), net_btc=("net_btc", "sum"), gross_btc=("net_btc", lambda x: x.abs().sum()),
        wallets=("address", "nunique"), category=("category", "first"),
        label=("label", lambda x: ", ".join(sorted(set(x)))), counterparties=("counterparties", "first"))
    g["internal"] = (g["wallets"] >= 2) & (g["net_btc"].abs() < 0.01 * g["gross_btc"])
    px = daily_px.copy()
    px.index = pd.to_datetime(px.index, utc=True).normalize()
    px = px[~px.index.duplicated(keep="last")].sort_index()
    day = g["time"].dt.normalize()
    g["usd"] = g["net_btc"] * px.reindex(day.unique()).ffill().reindex(day).to_numpy()
    return g.sort_values("time", ascending=False).reset_index(drop=True)


def window_flows(moves: pd.DataFrame, start: pd.Timestamp, coverage: dict[str, str] | None = None,
                 groups_meta: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per entity over [start, now]: inflow, outflow, net (BTC and USD at each
    day's close), transactions. 'Coverage' says whether every wallet's
    history is complete over the whole window."""
    m = moves[(moves["time"] >= start) & ~moves["internal"]]
    if m.empty:
        return pd.DataFrame()
    out = m.groupby("group").agg(
        category=("category", "first"),
        inflow_btc=("net_btc", lambda x: x[x > 0].sum()), outflow_btc=("net_btc", lambda x: x[x < 0].sum()),
        net_btc=("net_btc", "sum"), net_usd=("usd", "sum"), txs=("txid", "nunique"), last=("time", "max"))
    if coverage is not None:
        out["coverage"] = [("complete" if pd.Timestamp(coverage.get(gr, "1970-01-01T00:00:00+00:00")) <= start
                            else f"complete from {pd.Timestamp(coverage[gr]):%d %b %H:%M}") for gr in out.index]
    return out.sort_values("net_btc", key=abs, ascending=False)
