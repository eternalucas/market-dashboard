"""Hyperliquid adapter (read-only). Limits from the official docs, checked
26 Oct 2026 (docs/venues.md):
- 1,200 request weight per minute per IP. Most info requests cost 20;
  clearinghouseState costs 2. Fills, funding and ledger add 1 per 20 items
  returned, and candles add 1 per 60.
- userFillsByTime: at most 2,000 fills per response. The docs also say only
  the 10,000 most recent fills are available, but a time-windowed query
  served 35,491 fills of one wallet for 1-27 Oct 2026 (paged by time), so
  the limit applies per query, not per wallet. fills() therefore walks back
  from now in time windows until it reaches the start; a wallet is flagged
  "history truncated" only when its fills cover < 98% of the venue's
  all-time volume for it.
- Time-range responses (funding, ledger) return at most 500 items.
- candleSnapshot: only the most recent 5,000 candles per interval.
- The leaderboard is the web app's unofficial stats file. It can change
  without notice, so it is only used to choose whom to look at, never as a
  number we show.
"""
from __future__ import annotations

import math
import time

import pandas as pd

from .base import Capabilities, RateLimiter, RetryableError, VenueAdapter, with_backoff

INFO = "https://api.hyperliquid.xyz/info"
STATS_LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
# 1,200 is the venue's limit; the rest of the dashboard also calls Hyperliquid,
# so the review keeps a third in reserve.
LIMITER = RateLimiter(per_minute=800)
FILLS_CAP = None          # no per-wallet cap once queries are windowed (see above); coverage decides truncation
GENESIS_MS = 1_672_531_200_000   # 1 Jan 2023: before Hyperliquid's first perp fills
FILL_WINDOW_MS = 30 * 86_400_000
PER_ITEM = {"userFillsByTime": 20, "userFills": 20, "userFunding": 20, "userNonFundingLedgerUpdates": 20,
            "fundingHistory": 20, "candleSnapshot": 60}
LIGHT = {"clearinghouseState", "l2Book", "allMids", "spotClearinghouseState"}
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def is_perp(coin: str) -> bool:
    """Perp markets are plain names (BTC) or builder-deployed 'dex:NAME'
    (xyz:SP500). Spot pairs are '@123' or 'PURR/USDC' and are left out."""
    return not (coin.startswith("@") or "/" in coin)


class Hyperliquid(VenueAdapter):
    name, label = "hyperliquid", "HL"
    caps = Capabilities(
        leaderboard=True, fills_history=True, funding_history=True, liquidations=True, account_state=True,
        candles=True, orderbook_history=False,
        notes={"leaderboard": "Unofficial stats file used by the web app; candidates only, every number is recomputed.",
               "fills_history": "Paged back in time windows; a wallet whose fills cover < 98% of its all-time "
                                "volume is flagged 'history truncated'.",
               "candles": "Only the most recent 5,000 candles per interval: 1m covers ~3.5 days, 1h ~208 days.",
               "orderbook_history": "No historical order book; slippage vs mid only for fills after books are recorded."})

    def info(self, payload: dict):
        kind = payload["type"]
        LIMITER.spend(2 if kind in LIGHT else 20)

        def call():
            r = self.session.post(INFO, json=payload, timeout=30)
            if r.status_code == 429:
                raise RetryableError("rate limited (429)")
            r.raise_for_status()
            return r.json()
        data = with_backoff(call)
        if kind in PER_ITEM and isinstance(data, list):
            LIMITER.spend(math.ceil(len(data) / PER_ITEM[kind]))
        return data

    # ------------------------------------------------------------ leaderboard
    def leaderboard(self, con=None) -> pd.DataFrame:
        def call():
            r = self.session.get(STATS_LEADERBOARD, timeout=60)
            r.raise_for_status()
            return r.json()
        rows = []
        for r in with_backoff(call)["leaderboardRows"]:
            row = {"account_id": r["ethAddress"].lower(), "account_value": float(r["accountValue"]),
                   "name": r.get("displayName")}
            for window, perf in r["windowPerformances"]:
                for k in ("pnl", "roi", "vlm"):
                    row[f"{window}_{k}"] = float(perf[k])
            rows.append(row)
        return pd.DataFrame(rows)

    # ------------------------------------------------------------ fills
    def fills(self, account_id: str, start_ms: int = 0, end_ms: int | None = None,
              max_pages: int | None = None) -> pd.DataFrame:
        """Every fill from start_ms to end_ms (default now). Walks back from the
        end in time windows (the venue limits what one query can reach, not
        what a wallet has); inside a window pages forward by time, 2,000 per
        page, until a page brings no new trade id. A window that needed one
        page doubles the next one, so quiet or empty stretches cost little.
        Partial fills of one crossing order are combined (aggregateByTime), as
        the web app shows them; that can return fewer than 2,000 per page while
        more remain, so a short page is not taken as the end."""
        lo_bound = max(int(start_ms), GENESIS_MS)
        hi = int(end_ms) if end_ms else int(time.time() * 1000)
        seen: dict = {}
        pages, span = 0, FILL_WINDOW_MS
        while hi >= lo_bound and not (max_pages and pages >= max_pages):
            lo = max(lo_bound, hi - span)
            cursor, window_pages, oldest = lo, 0, None
            while not (max_pages and pages >= max_pages):
                batch = self.info({"type": "userFillsByTime", "user": account_id, "startTime": int(cursor),
                                   "endTime": int(hi), "aggregateByTime": True}) or []
                pages += 1
                fresh = [f for f in batch if f["tid"] not in seen]
                if not fresh:
                    break
                window_pages += 1
                seen.update((f["tid"], f) for f in fresh)
                oldest = min([f["time"] for f in fresh] + ([oldest] if oldest is not None else []))
                top = max(f["time"] for f in batch)
                cursor = top if top > cursor else cursor + 1      # same-ms fills across pages: deduped by tid
            span = span * 2 if window_pages <= 1 else span
            # if the window's fills start after its beginning, the query may not have reached further
            # back: the next window ends at the oldest fill found (inclusive, deduplicated by tid)
            hi = oldest if oldest is not None and oldest > lo else lo - 1
        return normalize_fills(list(seen.values()), account_id)

    def recent_fill_rate(self, account_id: str, hours: int = 24) -> int:
        """Fills in the last `hours` (one call; capped at 2,000). Used to spot
        market makers before paging through a whole history."""
        start = int((time.time() - hours * 3600) * 1000)
        return len(self.info({"type": "userFillsByTime", "user": account_id, "startTime": start,
                              "aggregateByTime": True}) or [])

    # ------------------------------------------------------------ funding, ledger
    def _paged(self, kind: str, account_id: str, start_ms: int) -> list[dict]:
        out, cursor = [], int(start_ms)
        while True:
            batch = self.info({"type": kind, "user": account_id, "startTime": cursor})
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 500:
                break
            cursor = max(x["time"] for x in batch) + 1
        return out

    def funding(self, account_id: str, start_ms: int = 0) -> pd.DataFrame:
        rows = [{"venue": self.name, "account_id": account_id, "ts": pd.to_datetime(x["time"], unit="ms", utc=True),
                 "coin": x["delta"]["coin"], "usdc": float(x["delta"]["usdc"]),
                 "position_size": float(x["delta"]["szi"]), "rate": float(x["delta"]["fundingRate"])}
                for x in self._paged("userFunding", account_id, start_ms) if x.get("delta", {}).get("type") == "funding"]
        return pd.DataFrame(rows, columns=["venue", "account_id", "ts", "coin", "usdc", "position_size", "rate"])

    def ledger(self, account_id: str, start_ms: int = 0) -> pd.DataFrame:
        """Deposits, withdrawals and transfers (userNonFundingLedgerUpdates),
        paged by time. usdc is signed from this account's side: + into it."""
        return normalize_ledger(self._paged("userNonFundingLedgerUpdates", account_id, start_ms), account_id, self.name)

    # ------------------------------------------------------------ account
    def account_state(self, account_id: str) -> dict:
        s = self.info({"type": "clearinghouseState", "user": account_id})
        return {"account_value": float(s["marginSummary"]["accountValue"]),
                "positions": [p["position"] for p in s.get("assetPositions", [])]}

    def portfolio(self, account_id: str) -> dict:
        """{window: {'av': Series, 'pnl': Series, 'vlm': float}} for day/week/month/allTime (+ perp*)."""
        out = {}
        for window, d in self.info({"type": "portfolio", "user": account_id}):
            av = pd.Series({pd.to_datetime(t, unit="ms", utc=True): float(v) for t, v in d.get("accountValueHistory", [])},
                           dtype=float).sort_index()
            pnl = pd.Series({pd.to_datetime(t, unit="ms", utc=True): float(v) for t, v in d.get("pnlHistory", [])},
                            dtype=float).sort_index()
            out[window] = {"av": av, "pnl": pnl, "vlm": float(d.get("vlm") or 0)}
        return out

    def is_vault(self, account_id: str) -> bool:
        try:
            d = self.info({"type": "vaultDetails", "vaultAddress": account_id})
        except Exception:  # noqa: BLE001 - a non-vault can error instead of returning null
            return False
        return bool(d) and isinstance(d, dict) and bool(d.get("vaultAddress") or d.get("name"))

    # ------------------------------------------------------------ market data
    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        data = self.info({"type": "candleSnapshot",
                          "req": {"coin": coin, "interval": interval, "startTime": int(start_ms), "endTime": int(end_ms)}})
        rows = [{"venue": self.name, "coin": coin, "interval": interval,
                 "ts": pd.to_datetime(c["t"], unit="ms", utc=True), "o": float(c["o"]), "h": float(c["h"]),
                 "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])} for c in data or []]
        return pd.DataFrame(rows, columns=["venue", "coin", "interval", "ts", "o", "h", "l", "c", "v"])

    def market_funding(self, coin: str, start_ms: int) -> pd.DataFrame:
        out, cursor = [], int(start_ms)
        while True:
            batch = self.info({"type": "fundingHistory", "coin": coin, "startTime": cursor})
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 500:
                break
            cursor = max(x["time"] for x in batch) + 1
        rows = [{"venue": self.name, "coin": coin, "ts": pd.to_datetime(x["time"], unit="ms", utc=True),
                 "rate": float(x["fundingRate"])} for x in out]
        return pd.DataFrame(rows, columns=["venue", "coin", "ts", "rate"])


def normalize_fills(raw: list[dict], account_id: str, venue: str = "hyperliquid") -> pd.DataFrame:
    cols = ["venue", "account_id", "fill_id", "ts", "coin", "side", "px", "sz", "start_position", "closed_pnl",
            "fee", "fee_token", "builder_fee", "is_taker", "order_id", "liq_role", "liq_mark_px"]
    if not raw:
        return pd.DataFrame(columns=cols)
    rows = []
    acct = account_id.lower()
    for f in raw:
        liq = f.get("liquidation") or None
        role = None
        if liq:
            role = "liquidated" if str(liq.get("liquidatedUser", "")).lower() == acct else "counterparty"
        rows.append({"venue": venue, "account_id": acct, "fill_id": str(f["tid"]),
                     "ts": pd.to_datetime(f["time"], unit="ms", utc=True), "coin": f["coin"], "side": f["side"],
                     "px": float(f["px"]), "sz": float(f["sz"]), "start_position": float(f["startPosition"]),
                     "closed_pnl": float(f["closedPnl"]), "fee": float(f["fee"]), "fee_token": f.get("feeToken"),
                     "builder_fee": float(f.get("builderFee") or 0), "is_taker": int(bool(f.get("crossed"))),
                     "order_id": str(f.get("oid", "")), "liq_role": role,
                     "liq_mark_px": float(liq["markPx"]) if liq and liq.get("markPx") else None})
    df = pd.DataFrame(rows, columns=cols)
    df = df.drop_duplicates("fill_id").assign(_o=pd.to_numeric(df["fill_id"], errors="coerce"))
    return df.sort_values(["ts", "_o"], kind="stable").drop(columns="_o").reset_index(drop=True)


# Ledger delta types (userNonFundingLedgerUpdates) and what each does to the
# account value used for returns (Hyperliquid's portfolio account value,
# perp + spot). EXTERNAL_FLOWS move money into or out of that account value;
# everything else is stored for the record but is not a flow:
#   accountClassTransfer  spot <-> perp inside the same account
#   send to oneself       between the account's own dexes
#   liquidation           a trading loss, already in P&L
EXTERNAL_FLOWS = frozenset({"deposit", "withdraw", "internalTransfer", "subAccountTransfer", "spotTransfer", "send",
                            "vaultCreate", "vaultDeposit", "vaultWithdraw", "vaultDistribution", "vaultLeaderCommission",
                            "rewardsClaim"})
_DIRECTED = {"internalTransfer", "subAccountTransfer", "spotTransfer", "send"}


def _ledger_value(d: dict) -> float | None:
    """USD value of one ledger delta (unsigned where the type carries the direction)."""
    kind = d.get("type", "")
    if kind == "vaultWithdraw":
        v = d.get("netWithdrawnUsd", d.get("requestedUsd"))
    elif kind in ("spotTransfer", "send"):
        v = d.get("usdcValue", d.get("amount") if d.get("token") in (None, "USDC") else None)
    else:
        v = d.get("usdc", d.get("amount") if d.get("token") in (None, "USDC") else None)
    return None if v is None else float(v)


def normalize_ledger(raw: list[dict], account_id: str, venue: str = "hyperliquid") -> pd.DataFrame:
    """One row per ledger update: kind, usdc signed + into this account. Fees
    on withdrawals and sends (about $1) are not counted."""
    cols = ["venue", "account_id", "ts", "ref", "kind", "usdc"]
    acct = account_id.lower()
    rows = []
    for x in raw or []:
        d = x.get("delta") or {}
        kind = d.get("type", "")
        if kind == "liquidation":
            usdc = 0.0
        else:
            usdc = _ledger_value(d)
            if usdc is None:
                continue
        if kind in ("withdraw", "vaultCreate", "vaultDeposit"):
            usdc = -abs(usdc)
        elif kind in ("deposit", "vaultWithdraw", "vaultDistribution", "vaultLeaderCommission", "rewardsClaim"):
            usdc = abs(usdc)
        elif kind == "accountClassTransfer":                       # + into perp, - into spot (internal)
            usdc = abs(usdc) if d.get("toPerp", True) else -abs(usdc)
        elif kind in _DIRECTED:
            sender, dest = str(d.get("user", "")).lower(), str(d.get("destination", "")).lower()
            if sender == acct and dest == acct:                    # between its own dexes
                usdc = 0.0
            elif sender == acct:
                usdc = -abs(usdc)
            elif dest == acct:
                usdc = abs(usdc)
            else:                                                  # neither side is this account: not ours
                continue
        rows.append({"venue": venue, "account_id": acct, "ts": pd.to_datetime(x["time"], unit="ms", utc=True),
                     "ref": f"{x.get('hash', '')}:{kind}:{usdc:.6f}", "kind": kind, "usdc": usdc})
    return pd.DataFrame(rows, columns=cols)
