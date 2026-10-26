"""Full order books from the venues Glassnode tracks, as (bids, asks) DataFrames
with `price` and `size` (base coin) columns, best price first.

Spot: Coinbase (publishes its full book) and Binance (deepest public snapshot,
5,000 levels). Perpetuals: Binance, Bybit, OKX and Hyperliquid.

Depth caveat (the same one Glassnode documents): Binance, Bybit and OKX only
publish the levels nearest the mid, so depth far from the mid is a minimum,
not a total. Hyperliquid's book is fetched at 5 significant figures so it
reaches further from the mid (each level then aggregates a price range).
"""
from __future__ import annotations

import pandas as pd

from .http import get_json, post_json


def _book(levels, price_key=0, size_key=1) -> pd.DataFrame:
    return pd.DataFrame([(float(l[price_key]), float(l[size_key])) for l in levels], columns=["price", "size"])


# --------------------------------------------------------------------------- #
# Spot
# --------------------------------------------------------------------------- #

def coinbase_spot(asset: str = "BTC"):
    d = get_json(f"https://api.exchange.coinbase.com/products/{asset}-USD/book", {"level": 2})
    return _book(d["bids"]), _book(d["asks"])


def binance_spot(asset: str = "BTC"):
    d = get_json("https://api.binance.com/api/v3/depth", {"symbol": f"{asset}USDT", "limit": 5000})
    return _book(d["bids"]), _book(d["asks"])


# --------------------------------------------------------------------------- #
# Perpetuals (USDT-margined, the main contract on each venue)
# --------------------------------------------------------------------------- #

def binance_perp(asset: str = "BTC"):
    d = get_json("https://fapi.binance.com/fapi/v1/depth", {"symbol": f"{asset}USDT", "limit": 1000})
    return _book(d["bids"]), _book(d["asks"])


def bybit_perp(asset: str = "BTC"):
    d = get_json("https://api.bybit.com/v5/market/orderbook",
                 {"category": "linear", "symbol": f"{asset}USDT", "limit": 500})["result"]
    return _book(d["b"]), _book(d["a"])


OKX_CT_VAL = {"BTC": 0.01, "ETH": 0.1}  # coins per contract on the USDT swap


def okx_perp(asset: str = "BTC"):
    d = get_json("https://www.okx.com/api/v5/market/books", {"instId": f"{asset}-USDT-SWAP", "sz": 400})["data"][0]
    ct = OKX_CT_VAL.get(asset, 1.0)
    bids, asks = _book(d["bids"]), _book(d["asks"])
    bids["size"] *= ct   # OKX quotes size in contracts
    asks["size"] *= ct
    return bids, asks


def hyperliquid_perp(asset: str = "BTC"):
    d = post_json("https://api.hyperliquid.xyz/info", {"type": "l2Book", "coin": asset, "nSigFigs": 5})
    bids = pd.DataFrame([(float(x["px"]), float(x["sz"])) for x in d["levels"][0]], columns=["price", "size"])
    asks = pd.DataFrame([(float(x["px"]), float(x["sz"])) for x in d["levels"][1]], columns=["price", "size"])
    return bids, asks


SPOT = {"coinbase": coinbase_spot, "binance": binance_spot}
PERP = {"binance": binance_perp, "bybit": bybit_perp, "okx": okx_perp, "hyperliquid": hyperliquid_perp}
