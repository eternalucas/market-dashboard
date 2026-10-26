"""Phase 5 numeric check: our metrics vs Glassnode's free-tier values at a
cycle top (2021-11-08), a cycle bottom (2022-11-21), two later highs and the
latest common day. Reference values: tests/reference/glassnode_free_*.json
(pulled from studio.glassnode.com's own free data, 2026-10-21).

Run:  MARKET_DB=... python -m scripts.verify   -> prints a markdown table.
Tolerance: 1% for price-derived and futures metrics, 3% for on-chain / options.
"""
from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

from src import storage
from src.metrics import Ctx
from src.metrics import onchain as oc
from src.metrics import markets as mk
from src.metrics import spot as sp

REF = json.loads(open(sorted(glob.glob("tests/reference/glassnode_free_*.json"))[-1]).read())


def ours(c: Ctx) -> dict:
    """Glassnode path -> (our Series, tolerance, note)."""
    p = c.price()
    etf_btc = mk.etf_netflows_btc(c)
    return {
        "market/price_usd_close": (p, 0.01, "Coin Metrics reference rate"),
        "market/marketcap_usd": (c.mcap(), 0.01, ""),
        "market/price_drawdown_relative": (sp.drawdown_from_ath(c), 0.01, "compared as difference in pp"),
        "indicators/power_law": (sp.power_law(c), 0.01, ""),
        "indicators/pi_cycle_top:ma111": (sp.pi_cycle(c)["sma111"], 0.01, ""),
        "indicators/pi_cycle_top:ma350x2": (sp.pi_cycle(c)["sma350x2"], 0.01, ""),
        "supply/current": (c.supply(), 0.03, ""),
        "addresses/active_count": (c.s("cm:AdrActCnt"), 0.03, ""),
        "transactions/count": (c.s("cm:TxCnt"), 0.03, ""),
        "transactions/transfers_count": (c.s("cm:TxTfrCnt"), 0.03, ""),
        "fees/volume_sum": (c.s("cm:FeeTotNtv"), 0.03, ""),
        "fees/volume_mean": (c.s("cm:FeeTotNtv") / c.s("cm:TxCnt"), 0.03, ""),
        "mining/hash_rate_mean": (oc.hashrate(c), 0.03, ""),
        "mining/difficulty_latest": (oc.difficulty(c).get("difficulty", pd.Series(dtype=float)) * 2 ** 32, 0.03,
                                     "ours is relative difficulty; x 2^32 = hashes"),
        "blockchain/block_count": (c.s("cm:BlkCnt"), 0.03, ""),
        "blockchain/block_interval_mean": (oc.blocks(c)["interval_min"] * 60, 0.03, "seconds"),
        "blockchain/block_size_mean": (c.s("bc:avg-block-size") * 1e6, 0.03, "bytes"),
        "blockchain/utxo_count": (oc.utxos(c), 0.03, ""),
        "transactions/transfers_volume_sum": (c.s("bc:output-volume"), 0.03, "BTC, all outputs"),
        "indicators/sopr": (oc.holders(c)["sopr"], 0.03, "BGeometrics"),
        "supply/active_more_1y_percent": (oc.hodl_waves(c).filter(regex="1y_2y|2y_3y|3y_4y|4y_5y|5y_7y|7y_10y|age_10y").sum(axis=1) / 100
                                          if not oc.hodl_waves(c).empty else pd.Series(dtype=float), 0.03, "sum of HODL bands >= 1y"),
        "institutions/us_spot_etf_flows_net": (etf_btc, 0.03, "BTC holdings change (BGeometrics), else Farside USD / close"),
        "institutions/us_spot_etf_balances_all": (mk.etf_balances(c).get("balance_btc", pd.Series(dtype=float)), 0.03, "BGeometrics"),
    }


def main() -> None:
    c = Ctx(storage.connect())
    rows = []
    for path, (s, tol, note) in ours(c).items():
        ref = REF.get(path, {})
        s = s.dropna() if isinstance(s, pd.Series) else pd.Series(dtype=float)
        if not s.empty:
            s.index = pd.to_datetime(s.index, utc=True).normalize()
        for d, g in ref.items():
            if isinstance(g, dict):
                continue
            t = pd.Timestamp(d, tz="UTC")
            o = float(s.get(t, np.nan)) if not s.empty else np.nan
            if path.endswith("drawdown_relative"):
                diff = abs(o - g)          # absolute difference for a percentage
            else:
                diff = abs(o / g - 1) if g else np.nan
            status = "n/a" if np.isnan(o) else ("OK" if diff <= tol else "OUT")
            rows.append({"metric": path, "date": d, "glassnode": g, "ours": o, "diff": diff, "tol": tol, "status": status, "note": note})
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(df.to_markdown(index=False, floatfmt=".4g"))
    print("\nOK:", (df["status"] == "OK").sum(), " OUT:", (df["status"] == "OUT").sum(), " n/a:", (df["status"] == "n/a").sum())


if __name__ == "__main__":
    main()
