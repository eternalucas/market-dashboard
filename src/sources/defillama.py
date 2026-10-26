"""DefiLlama (free, no key): stablecoin supply (total, by coin, by chain) and
centralised-exchange BTC reserves from exchanges' published wallets.

Raw series:
  dl:stable:total                 total stablecoin circulating, USD
  dl:stable:coin:<SYMBOL>         one coin's circulating supply, USD
  dl:stable:chain:<Chain>         stablecoins on one chain, USD
  dl:cex:<slug>:BTC               BTC held in a CEX's published wallets (coins)
"""
from __future__ import annotations

import pandas as pd

from .. import storage
from ..collectors.http import get_json

STABLE = "https://stablecoins.llama.fi"
TOP_COINS = 8
CHAINS = ["Ethereum", "Tron", "Solana", "BSC", "Arbitrum", "Base", "Polygon", "Avalanche"]
TOP_CEX = 12


def _chart(rows: list[dict], key: str = "totalCirculatingUSD") -> pd.Series:
    return pd.Series({pd.to_datetime(int(r["date"]), unit="s", utc=True): float((r.get(key) or {}).get("peggedUSD", 0))
                      for r in rows}).sort_index()


def update_stablecoins(con) -> int:
    rows = storage.save_series(con, "dl:stable:total", _chart(get_json(f"{STABLE}/stablecoincharts/all")))
    assets = get_json(f"{STABLE}/stablecoins", {"includePrices": "true"})["peggedAssets"]
    usd = [a for a in assets if a.get("pegType") == "peggedUSD"]
    usd.sort(key=lambda a: (a.get("circulating") or {}).get("peggedUSD", 0), reverse=True)
    for a in usd[:TOP_COINS]:
        s = _chart(get_json(f"{STABLE}/stablecoincharts/all", {"stablecoin": a["id"]}))
        rows += storage.save_series(con, f"dl:stable:coin:{a['symbol']}", s)
    for ch in CHAINS:
        rows += storage.save_series(con, f"dl:stable:chain:{ch}", _chart(get_json(f"{STABLE}/stablecoincharts/{ch}")))
    return rows


def update_cex_btc(con) -> int:
    protos = get_json("https://api.llama.fi/protocols")
    cex = sorted((p for p in protos if p.get("category") == "CEX"), key=lambda p: p.get("tvl") or 0, reverse=True)
    rows = 0
    for p in cex[:TOP_CEX]:
        d = get_json(f"https://api.llama.fi/protocol/{p['slug']}")
        pts = {pd.to_datetime(int(t["date"]), unit="s", utc=True).normalize(): float(t["tokens"].get("BTC", 0) or 0)
               for t in d.get("tokens") or [] if "BTC" in (t.get("tokens") or {})}
        if pts:
            rows += storage.save_series(con, f"dl:cex:{p['slug']}:BTC", pd.Series(pts).sort_index())
    return rows
