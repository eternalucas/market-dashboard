"""On-chain data for watched wallets, from free public explorers.

Bitcoin   mempool.space API (blockstream.info as a fallback; same format)
Ethereum  Blockscout's public API for eth.blockscout.com (no key needed;
          it's rate-limited, so requests are paced)

Labels come from watchlist.yaml, never from these APIs: the explorers only
supply balances and transactions.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import yaml

from ..addresses import valid
from .http import get_json

ROOT = Path(__file__).resolve().parents[2]
BTC_APIS = ["https://mempool.space/api", "https://blockstream.info/api"]
BLOCKSCOUT = "https://eth.blockscout.com/api/v2"
PAUSE = 0.25  # seconds between calls: polite pacing for free public APIs


def load_watchlist(path: Path = ROOT / "watchlist.yaml") -> pd.DataFrame:
    """Watchlist rows, with a 'valid' column from the address checksum check."""
    rows = yaml.safe_load(path.read_text())["wallets"]
    df = pd.DataFrame(rows)
    df["valid"] = [valid(c, a) for c, a in zip(df["chain"], df["address"])]
    return df


# --------------------------------------------------------------------------- #
# Bitcoin
# --------------------------------------------------------------------------- #

def _btc_get(path: str):
    last = None
    for base in BTC_APIS:
        try:
            return get_json(f"{base}{path}")
        except Exception as e:  # noqa: BLE001 - try the fallback explorer
            last = e
    raise last


def btc_balance(address: str) -> float:
    d = _btc_get(f"/address/{address}")
    c, m = d["chain_stats"], d["mempool_stats"]
    sats = c["funded_txo_sum"] - c["spent_txo_sum"] + m["funded_txo_sum"] - m["spent_txo_sum"]
    time.sleep(PAUSE)
    return sats / 1e8


def btc_transfers(address: str) -> pd.DataFrame:
    """Recent confirmed transactions (newest ~25) with this address's net BTC
    change and the counterparty addresses on the other side."""
    txs = _btc_get(f"/address/{address}/txs")
    rows = []
    for tx in txs:
        if not tx.get("status", {}).get("confirmed"):
            continue
        ins = [v.get("prevout") or {} for v in tx["vin"]]
        sent = sum(p.get("value", 0) for p in ins if p.get("scriptpubkey_address") == address)
        recv = sum(o.get("value", 0) for o in tx["vout"] if o.get("scriptpubkey_address") == address)
        senders = {p.get("scriptpubkey_address") for p in ins} - {address, None}
        receivers = {o.get("scriptpubkey_address") for o in tx["vout"]} - {address, None}
        rows.append({"txid": tx["txid"], "time": pd.to_datetime(tx["status"]["block_time"], unit="s", utc=True),
                     "net_btc": (recv - sent) / 1e8, "counterparties": sorted(senders if recv > sent else receivers)})
    time.sleep(PAUSE)
    return pd.DataFrame(rows)


def _parse_btc_tx(tx: dict, address: str) -> dict:
    ins = [v.get("prevout") or {} for v in tx["vin"]]
    sent = sum(p.get("value", 0) for p in ins if p.get("scriptpubkey_address") == address)
    recv = sum(o.get("value", 0) for o in tx["vout"] if o.get("scriptpubkey_address") == address)
    senders = {p.get("scriptpubkey_address") for p in ins} - {address, None}
    receivers = {o.get("scriptpubkey_address") for o in tx["vout"]} - {address, None}
    return {"txid": tx["txid"], "time": pd.to_datetime(tx["status"]["block_time"], unit="s", utc=True),
            "net_btc": (recv - sent) / 1e8, "counterparties": sorted(senders if recv > sent else receivers)}


def btc_sync(address: str, known: set[str], max_pages: int = 8) -> tuple[pd.DataFrame, bool]:
    """Confirmed transactions newer than the ones already stored, newest
    first, 25 per page (mempool.space's page size), until a known txid or
    the start of the address's history is reached.

    Returns (rows, complete): complete=False means the wallet made more
    transactions since the last sync than max_pages covers, so there is a
    hole between the oldest row returned and the newest stored one."""
    rows, last, complete = [], None, False
    for _ in range(max_pages):
        page = _btc_get(f"/address/{address}/txs/chain" + (f"/{last}" if last else ""))
        time.sleep(PAUSE)
        if not page:
            complete = True                    # reached the address's first transaction
            break
        hit = False
        for tx in page:
            if tx["txid"] in known:
                hit = True
                break
            if tx.get("status", {}).get("confirmed"):
                rows.append(_parse_btc_tx(tx, address))
        if hit or len(page) < 25:
            complete = True
            break
        last = page[-1]["txid"]
    return pd.DataFrame(rows, columns=["txid", "time", "net_btc", "counterparties"]), complete


# --------------------------------------------------------------------------- #
# Ethereum
# --------------------------------------------------------------------------- #

def eth_balance(address: str) -> float:
    d = get_json(f"{BLOCKSCOUT}/addresses/{address}")
    time.sleep(PAUSE)
    return int(d.get("coin_balance") or 0) / 1e18


def eth_token_transfers(address: str, pages: int = 2) -> pd.DataFrame:
    """Recent ERC-20 transfers in or out of this address, with USD value where
    Blockscout knows the token's price."""
    rows, params = [], {"type": "ERC-20"}
    for _ in range(pages):
        d = get_json(f"{BLOCKSCOUT}/addresses/{address}/token-transfers", params)
        for it in d.get("items", []):
            tok, tot = it.get("token") or {}, it.get("total") or {}
            dec = int(tot.get("decimals") or tok.get("decimals") or 18)
            amount = int(tot.get("value") or 0) / 10 ** dec
            rate = tok.get("exchange_rate")
            rows.append({"time": pd.to_datetime(it["timestamp"], utc=True), "tx": it.get("transaction_hash"),
                         "from": (it.get("from") or {}).get("hash", ""), "to": (it.get("to") or {}).get("hash", ""),
                         "token": tok.get("symbol"), "amount": amount,
                         "usd": amount * float(rate) if rate else float("nan")})
        time.sleep(PAUSE)
        nxt = d.get("next_page_params")
        if not nxt:
            break
        params = {"type": "ERC-20", **nxt}
    return pd.DataFrame(rows)
