"""Tokenized equities: on-chain spot DEX volume by token issuer.

Free sources, no key:
- CoinGecko (api.coingecko.com/api/v3): which coins are tokenized stocks
  (category "tokenized-stock" plus each issuer's own category), the issuer of
  each (category membership first, then the issuer suffix CoinGecko puts in
  the coin id, e.g. "nvidia-ondo-tokenized-stock"), and their contract
  addresses on each chain (/coins/list?include_platform=true).
- GeckoTerminal (api.geckoterminal.com/api/v2, ~30 calls a minute): every DEX
  pool trading each token, and each pool's daily USD volume
  (/pools/{pool}/ohlcv/day). The free API serves about 6 months of history.

A pool's volume is credited to the issuer of the tokenized stock in it. A pool
is counted once even if both its tokens are tokenized stocks.

Raw series: tsv:issuer:<issuer> and tsv:chain:<network> (daily USD DEX volume),
tsv:total. Pool registry: data/tokenized_stocks.json.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

from .. import storage

CG = "https://api.coingecko.com/api/v3"
GT = "https://api.geckoterminal.com/api/v2"
REGISTRY = Path(__file__).resolve().parents[2] / "data" / "tokenized_stocks.json"
UA = {"User-Agent": "market-dashboard/0.2 (research)", "Accept": "application/json"}

# CoinGecko issuer categories (checked 27 Oct 2026)
ISSUER_CATEGORIES = {
    "xstocks-ecosystem": "xStocks", "backpack-securities-ecosystem": "Backpack", "bstocks-ecosystem": "bStocks",
    "dinari-ecosystem": "Dinari", "ondo-tokenized-assets": "Ondo", "prestocks-ecosystem": "PreStocks",
    "robinhood-chain-stocks-ecosystem": "Robinhood", "remora-markets-tokenized-rstocks": "Remora",
    "tessera-tokenized-pre-ipo-assets": "Tessera Labs", "securitize-ecosystem": "Securitize",
    "anchored-ecosystem": "Anchored", "reality-ecosystem": "reality",
    "republic-tokenized-pre-ipo-assets": "Republic",
}
# Issuer from the coin id when no issuer category lists it (order matters: suffix first)
ID_PATTERNS = [
    (r"-robinhood-token(ized-stocks?)?(-\d+)?$", "Robinhood"), (r"-coinbase-tokenized-stocks?(-\d+)?$", "Coinbase"),
    (r"-ondo-tokenized(-stocks?)?(-\d+)?$", "Ondo"), (r"-bstocks?(-tokenized-stocks?)?(-\d+)?$", "bStocks"),
    (r"-xstocks?(-\d+)?$", "xStocks"), (r"-rstock(-\d+)?$", "Remora"), (r"backpack-securities(-\d+)?$", "Backpack"),
    (r"prestocks?(-\d+)?$", "PreStocks"), (r"-dinari-tokenized-stocks?(-\d+)?$", "Dinari"),
    (r"-reality-protocol(-\d+)?$", "reality"), (r"-st0x(-\d+)?$", "st0x"), (r"^backed-", "Backed Finance"),
    (r"securitize", "Securitize"), (r"tessera", "Tessera Labs"), (r"centrifuge", "Centrifuge"),
    (r"aktionariat|^draggable-", "Aktionariat"),
]
MIN_VOLUME_USD = 1_000      # CoinGecko 24h volume (all venues) below this: skip, no DEX volume to speak of
POOLS_PER_TOKEN = 3
MIN_POOL_VOLUME = 100         # USD in the last 24 h, for pools after a token's busiest one
GT_PAUSE = 2.3              # seconds: stays under GeckoTerminal's ~30 calls a minute


def _get(url, params=None, pause=0.0, tries=4):
    for attempt in range(tries):
        r = requests.get(url, params=params, headers=UA, timeout=30)
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        r.raise_for_status()
        if pause:
            time.sleep(pause)
        return r.json()
    raise RuntimeError(f"rate limited: {url}")


def issuer_of(coin_id: str, cat_issuer: dict[str, str]) -> str | None:
    if coin_id in cat_issuer:
        return cat_issuer[coin_id]
    for pat, name in ID_PATTERNS:
        if re.search(pat, coin_id):
            return name
    return None


def build_registry(say=print) -> dict:
    """Tokenized stocks with real volume, their issuer, chains and top pools."""
    cat_issuer: dict[str, str] = {}
    volume: dict[str, float] = {}
    for cat in ["tokenized-stock", *ISSUER_CATEGORIES]:
        for page in range(1, 6):
            rows = _get(f"{CG}/coins/markets", {"vs_currency": "usd", "category": cat, "per_page": 250, "page": page},
                        pause=7)
            for r in rows:
                volume[r["id"]] = max(volume.get(r["id"], 0.0), float(r.get("total_volume") or 0))
                if cat in ISSUER_CATEGORIES:
                    cat_issuer[r["id"]] = ISSUER_CATEGORIES[cat]
            if len(rows) < 250:
                break
    platforms = {c["id"]: c.get("platforms") or {} for c in _get(f"{CG}/coins/list", {"include_platform": "true"}, pause=7)}
    nets = {}
    for page in range(1, 10):
        try:
            d = _get(f"{GT}/networks", {"page": page}, pause=GT_PAUSE)
        except requests.HTTPError:          # past the last page GeckoTerminal answers 400
            break
        if not d.get("data"):
            break
        for n in d["data"]:
            if n["attributes"].get("coingecko_asset_platform_id"):
                nets[n["attributes"]["coingecko_asset_platform_id"]] = n["id"]
    tokens, unknown = [], 0
    for cid, vol in volume.items():
        iss = issuer_of(cid, cat_issuer)
        if iss is None:
            unknown += 1
            continue
        if vol < MIN_VOLUME_USD:
            continue
        for plat, addr in platforms.get(cid, {}).items():
            if addr and plat in nets:
                tokens.append({"coin": cid, "issuer": iss, "network": nets[plat], "address": addr})
    pools: dict[str, dict] = {}
    for i, t in enumerate(tokens):
        try:
            d = _get(f"{GT}/networks/{t['network']}/tokens/{t['address']}/pools", {"page": 1}, pause=GT_PAUSE)
        except requests.HTTPError:
            continue
        ranked = sorted(d.get("data", []), key=lambda p: -float(p["attributes"]["volume_usd"]["h24"] or 0))
        for p in ranked[:POOLS_PER_TOKEN]:
            if float(p["attributes"]["volume_usd"]["h24"] or 0) < MIN_POOL_VOLUME and p is not ranked[0]:
                continue
            pools.setdefault(p["id"], {"network": t["network"], "address": p["attributes"]["address"],
                                       "issuer": t["issuer"], "coin": t["coin"], "name": p["attributes"]["name"]})
        if i % 50 == 0:
            say(f"tokenized stocks: pools for {i}/{len(tokens)} tokens")
    reg = {"as_of": pd.Timestamp.now(tz="UTC").isoformat(), "tokens": len(tokens), "unmatched_coins": unknown,
           "pools": pools}
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=1))
    return reg


def pool_daily_volume(network: str, address: str, days: int) -> pd.Series:
    d = _get(f"{GT}/networks/{network}/pools/{address}/ohlcv/day", {"limit": min(days, 1000)}, pause=GT_PAUSE)
    rows = d["data"]["attributes"]["ohlcv_list"]
    return pd.Series({pd.to_datetime(r[0], unit="s", utc=True): float(r[5]) for r in rows}, dtype=float).sort_index()


def update(con, say=print) -> int:
    """Weekly pool list refresh; daily volumes for every pool (last 4 days,
    or ~6 months the first time a pool is seen), summed per issuer and chain."""
    reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else None
    if reg is None or pd.Timestamp.now(tz="UTC") - pd.Timestamp(reg["as_of"]) > pd.Timedelta(days=7):
        reg = build_registry(say)
    store_path = REGISTRY.with_name("tokenized_stocks_pool_volume.csv")
    old = pd.read_csv(store_path) if store_path.exists() else \
        pd.DataFrame(columns=["pool", "ts", "usd", "issuer", "network"])
    seen = set(old["pool"].unique())
    rebuilt = reg["as_of"] > (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=1)).isoformat()
    recent = old[pd.to_datetime(old["ts"], utc=True, format="ISO8601") >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=14)] \
        if not old.empty else old
    active = set(recent.loc[recent["usd"] > 0, "pool"])
    new = []
    for pid, p in reg["pools"].items():
        if pid in seen and pid not in active and not rebuilt:
            continue                          # no trades for two weeks: re-checked at the weekly pool refresh
        try:
            s = pool_daily_volume(p["network"], p["address"], 4 if pid in seen else 200)
        except Exception:  # noqa: BLE001 - one pool failing never stops the rest
            continue
        new.append(pd.DataFrame({"pool": pid, "ts": s.index.map(lambda t: t.isoformat()), "usd": s.values,
                                 "issuer": p["issuer"], "network": p["network"]}))
    allv = pd.concat([old, *new], ignore_index=True) if new else old
    allv = allv.drop_duplicates(["pool", "ts"], keep="last")
    allv.to_csv(store_path, index=False)
    # issuer and chain are stored with each row, so pools that later drop out of the
    # weekly pool list keep their history
    v = allv.copy()
    v["ts"] = pd.to_datetime(v["ts"], utc=True, format="ISO8601")
    today = pd.Timestamp.now(tz="UTC").normalize()
    v = v[v["ts"] < today]                    # today's bucket is still filling
    rows = 0
    for key, col in (("issuer", "issuer"), ("chain", "network")):
        wide = v.pivot_table(index="ts", columns=col, values="usd", aggfunc="sum").fillna(0.0)
        for name in wide.columns:
            rows += storage.save_series(con, f"tsv:{key}:{name}", wide[name])
    rows += storage.save_series(con, "tsv:total", v.groupby("ts")["usd"].sum())
    return rows
