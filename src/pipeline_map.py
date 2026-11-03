"""Where every data source comes from and where it goes, for the Data health diagram.

Each fetch-log source (see views.source_health) is mapped, by prefix, to:
  providers  who serves the data (an exchange, a data vendor, a public API),
  job        what fetches it and how often (src/recorder.py jobs),
  db         where it is stored,
  tabs       the dashboard tabs that read it.
The diagram is built from these rows and the live status of each source, so it
follows the code: a new or retired source changes the picture by itself.
"""
from __future__ import annotations

import pandas as pd

S, P, F, O, M, T = "Studio", "Positioning", "Flows", "On-chain", "Macro events", "Trade review"
TABS = [S, P, F, O, M, T]

MARKET, TRADES, HLPOS = "market.db", "trades.db", "hl_positions.db"
DBS = {MARKET: "prices, derivatives, on-chain, macro, social",
       TRADES: "Hyperliquid trades and backtests",
       HLPOS: "Hyperliquid liquidation levels"}

# job key -> (name, schedule); schedules from src/recorder.py
JOBS = {
    "books": ("Order books", "every 1 min"),
    "options_rec": ("Deribit options", "every 5 min to 1 h"),
    "curve": ("Futures curve", "every 15 min"),
    "positioning": ("Live positioning", "hourly"),
    "liq": ("Liquidations", "live feeds + 15 min scan"),
    "flows": ("Coinalyze flows", "hourly"),
    "pipeline": ("Update-all steps", "hourly, when due"),
    "sources": ("Daily sources", "hourly check, when due"),
    "mempool": ("Mempool", "every 10 min"),
    "onchain": ("Watched wallets", "hourly"),
    "ibit": ("IBIT option chain", "after each US close"),
    "leaderboard": ("Top 100 sync", "hourly"),
}

# (category, providers in display order)
CATEGORIES = [
    ("Exchanges", ["Binance", "Bybit", "OKX", "Hyperliquid", "Coinbase", "Deribit"]),
    ("Market data", ["Yahoo Finance", "Coinalyze", "Coin Metrics", "CoinGecko", "DefiLlama", "Farside", "CFTC",
                     "Alternative.me"]),
    ("On-chain", ["BGeometrics", "Blockchain.com", "Bitcoin explorers", "Blockscout (Ethereum)"]),
    ("Macro", ["FRED", "Central bank calendars"]),
    ("Social", ["Wikipedia", "YouTube", "Google Trends", "Apple App Store", "Hacker News"]),
    ("Internal", ["Computed internally"]),
]
PROVIDER_NOTES = {
    "Yahoo Finance": "daily prices fall back to Kraken when Yahoo has no data",
    "Bitcoin explorers": "mempool.space, mempool.emzy.de and blockstream.info, rotated with fallback",
    "Central bank calendars": "decision dates built into the code (Fed, BoE, BoJ, PBoC)",
    "Computed internally": "derived from other data: Social Risk score, liquidation model calibration",
}
VENUES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "hyperliquid": "Hyperliquid",
          "coinbase": "Coinbase", "deribit": "Deribit"}
SRC = {  # src:<name> from the source registry (src/sources/registry.py)
    "coinmetrics": ("Coin Metrics", [S, O]), "blockchain_com": ("Blockchain.com", [S]),
    "mempool_hist": ("Bitcoin explorers", [S, O]), "defillama_stablecoins": ("DefiLlama", [S]),
    "defillama_cex": ("DefiLlama", [S]), "fear_greed": ("Alternative.me", [S]),
    "coingecko_global": ("CoinGecko", [S]), "cftc": ("CFTC", [S]), "dvol": ("Deribit", [S]),
    "yahoo": ("Yahoo Finance", [S, O]), "farside": ("Farside", [S]), "binance_quarterly": ("Binance", [S]),
    "coinbase_hourly": ("Coinbase", [S]), "coinalyze_daily": ("Coinalyze", [S]),
    "social": ("Computed internally", [S]), "fred_macro": ("FRED", [S]),
}


def _venues(text: str) -> list[str]:
    return [v for k, v in VENUES.items() if k in text.lower()]


def route(source: str) -> dict:
    """providers / job / db / tabs for one fetch-log source."""
    def r(providers, job, tabs, db=MARKET):
        return {"providers": list(providers), "job": job, "db": db, "tabs": list(tabs)}
    parts = source.split(":")
    head, second = parts[0], (parts[1] if len(parts) > 1 else "")
    if head == "rec":
        if second == "book":
            return r(_venues(source) or ["Coinbase"], "books", [S, P, F])
        if second == "futures_curve":
            return r(_venues(source) or ["Deribit"], "curve", [S])
        if second == "option_chain" and source.startswith("rec:option_chain:ibit"):
            return r(["Yahoo Finance"], "ibit", [S])
        if second.startswith("option_"):
            return r(["Deribit"], "options_rec", [S, P])
        if second == "mempool":
            return r(["Bitcoin explorers"], "mempool", [S, O])
        if second == "hl_liq_scan":
            return r(["Hyperliquid"], "liq", [F], HLPOS)
        if second == "liq_feeds":
            return r(["Binance", "Bybit", "OKX"], "liq", [F])
        if second == "liq_calibrate":
            return r(["Computed internally"], "liq", [F])
        if second == "trade_leaderboard":
            return r(["Hyperliquid"], "leaderboard", [T], TRADES)
    if head in ("perp", "book", "options"):
        return r(_venues(second) or ["Binance"], "positioning", [P])
    if head in ("funding_hist", "oi_hist"):
        return r(_venues(second) or ["Binance"], "pipeline", [S, P, F])
    if head == "coinalyze":
        return r(["Coinalyze"], "flows", [F])
    if head == "price":
        return r(["Yahoo Finance"], "pipeline", [S])
    if head == "event_price":
        return r(["Yahoo Finance"], "pipeline", [M])
    if head in ("fred", "fred_release"):
        return r(["FRED"], "pipeline", [M])
    if head == "central_bank_calendar":
        return r(["Central bank calendars"], "pipeline", [M])
    if head == "hyperliquid_cohorts":
        return r(["Hyperliquid"], "pipeline", [O])
    if head == "trade_review":
        return r(["Hyperliquid"], "pipeline", [T], TRADES)
    if head == "onchain":
        return r(["Blockscout (Ethereum)"] if second == "eth" else ["Bitcoin explorers"], "onchain", [O])
    if head == "fred_macro":                                  # fred_macro:failed note
        return r(["FRED"], "sources", [S])
    if head == "bgeo":
        return r(["BGeometrics"], "sources", [S])
    if head in ("wikipedia", "wikitotal"):
        return r(["Wikipedia"], "sources", [S])
    if head == "youtube":
        return r(["YouTube"], "sources", [S])
    if head == "app_store":
        return r(["Apple App Store"], "sources", [S])
    if head == "google_trends":
        return r(["Google Trends"], "sources", [S])
    if head == "hacker_news":
        return r(["Hacker News"], "sources", [S])
    if head == "src" and second in SRC:
        prov, tabs = SRC[second]
        return r([prov], "sources", tabs)
    return r(["Other"], "sources", [S])


RANK = {"OK": 0, "Stale": 1, "Failing": 2}


def worst(statuses) -> str:
    s = list(statuses)
    return max(s, key=RANK.get) if s else "OK"


def build(h: pd.DataFrame) -> dict:
    """Nodes and edges of the diagram from a views.source_health() table.

    Returns {"rows": one row per (source, provider), "nodes": {column: DataFrame},
    "edges": DataFrame(src_col, src, dst_col, dst, status, n)}."""
    rows = []
    for src, status in zip(h["Source"], h["Status"]):
        r = route(src)
        for p in r["providers"]:
            for t in r["tabs"]:
                rows.append({"source": src, "status": status, "provider": p, "job": r["job"], "db": r["db"],
                             "tab": t})
    df = pd.DataFrame(rows, columns=["source", "status", "provider", "job", "db", "tab"])

    def nodes(col):
        g = df.drop_duplicates(["source", col]).groupby(col)
        out = pd.DataFrame({"n": g.size(), "ok": g["status"].apply(lambda s: int((s == "OK").sum())),
                            "stale": g["status"].apply(lambda s: int((s == "Stale").sum())),
                            "failing": g["status"].apply(lambda s: int((s == "Failing").sum())),
                            "status": g["status"].apply(worst)})
        return out.reset_index().rename(columns={col: "id"})

    def edges(a, b):
        g = df.drop_duplicates(["source", a, b]).groupby([a, b])
        e = pd.DataFrame({"n": g.size(), "status": g["status"].apply(worst)}).reset_index()
        return e.rename(columns={a: "src", b: "dst"}).assign(src_col=a, dst_col=b)

    return {"rows": df, "nodes": {c: nodes(c) for c in ("provider", "job", "db", "tab")},
            "edges": pd.concat([edges("provider", "job"), edges("job", "db"), edges("db", "tab")],
                               ignore_index=True)}
