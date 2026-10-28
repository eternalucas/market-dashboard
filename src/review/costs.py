"""Costs for simulated fills: fees, slippage, funding and a liquidation check.

- Fees: the trader's own rates, measured from their fills (median fee /
  notional on taker and on maker fills), so their volume tier and any
  builder fee are included. Fallback: Hyperliquid's base tier, 4.5 bps
  taker and 1.5 bps maker.
- Slippage on simulated market orders (stops, time exits): half the spread
  plus size impact, impact_bps = k x sqrt(order notional / average 1-minute
  traded notional before the order). k is editable (default 10 bps); there
  is no free order-book history to calibrate it.
- Funding: the account's own hourly payments, scaled by the simulated
  position over the actual one at each payment (exact for an unchanged
  trip; the venue's rate is the same for every account).
- Liquidation (only checked when a variant makes a position larger than
  the trader's): isolated-equivalent, leverage = position / account value
  at entry, maintenance margin = half the initial margin at the coin's
  maximum leverage. An approximation for cross-margin accounts, and
  labelled as such.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

BASE_TAKER_BPS, BASE_MAKER_BPS = 4.5, 1.5
MAX_LEVERAGE = {"BTC": 40, "ETH": 25, "SOL": 20, "XRP": 20, "HYPE": 10}
DEFAULT_MAX_LEVERAGE = 10


@dataclass
class Assumptions:
    taker_bps: float | None = None       # None = the trader's own rate
    maker_bps: float | None = None
    half_spread_bps: float = 1.0
    impact_k_bps: float = 10.0
    families: tuple = ("exits", "sizing", "execution", "selection")
    max_leverage_cap: float = 5.0        # sizing variants never go above this (x account value)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["families"] = list(self.families)
        return d


def own_fee_rates(fills: pd.DataFrame) -> dict:
    """{"taker_bps", "maker_bps", "source"} from the trader's fills."""
    out = {"taker_bps": BASE_TAKER_BPS, "maker_bps": BASE_MAKER_BPS, "source": "Hyperliquid base tier"}
    if fills is None or fills.empty:
        return out
    n = (fills["px"] * fills["sz"]).abs()
    bps = (fills["fee"] / n.where(n > 0) * 1e4).replace([np.inf, -np.inf], np.nan)
    t = bps[fills["is_taker"].astype(bool)].dropna()
    m = bps[~fills["is_taker"].astype(bool)].dropna()
    if len(t) >= 10:
        out["taker_bps"] = float(t.median())
    if len(m) >= 10:
        out["maker_bps"] = float(m.median())
    if len(t) >= 10 or len(m) >= 10:
        out["source"] = "the trader's own fills (median fee / notional)"
    return out


def resolve(a: Assumptions, fills: pd.DataFrame) -> dict:
    own = own_fee_rates(fills)
    return {"taker_bps": own["taker_bps"] if a.taker_bps is None else float(a.taker_bps),
            "maker_bps": own["maker_bps"] if a.maker_bps is None else float(a.maker_bps),
            "fee_source": own["source"] if a.taker_bps is None else "assumptions panel",
            "half_spread_bps": float(a.half_spread_bps), "impact_k_bps": float(a.impact_k_bps)}


def slippage_bps(notional: float, minute_notional: float, rates: dict) -> float:
    """Half spread + k x sqrt(order / average 1-minute traded notional)."""
    impact = rates["impact_k_bps"] * np.sqrt(notional / minute_notional) if minute_notional and minute_notional > 0 else 0.0
    return rates["half_spread_bps"] + float(min(impact, 500.0))


def mmr(coin: str) -> float:
    return 0.5 / MAX_LEVERAGE.get(coin, DEFAULT_MAX_LEVERAGE)


def liquidation_price(entry: float, side: int, leverage: float, coin: str) -> float:
    """Isolated-equivalent: the move that wipes the margin down to maintenance."""
    if not leverage or leverage <= 0:
        return np.nan
    move = 1.0 / leverage - mmr(coin)
    if move <= 0:
        return entry
    return entry * (1 - move) if side > 0 else entry * (1 + move)
