"""Lighter adapter (read-only). Checked live on 27 Oct 2026:

- Public, no login: every account's trades (`trades?account_index=`, 100
  per page, newest first, with the account's position and entry cost
  before each trade and the fee rate), daily account P&L with deposits and
  withdrawals (`pnl`), account state (`account`), markets (`orderBooks`),
  candles (`candles`, 500 per call), hourly funding rates (`fundings`) and
  the latest trades of each market with both accounts (`recentTrades`).
- Not public: an account's own funding payments and liquidations (login
  needed). Funding is therefore **estimated** from the account's position
  at each hour times the market's hourly rate, and labelled as estimated.
- No public P&L leaderboard (Lighter's own ranks points and hides
  addresses). Candidates are found by sampling every market's latest trades
  (recorded over time in tr_seen) and ranking those accounts by their own
  P&L history. That favours accounts that trade often; the bias is stated.
- 60 requests per minute per IP for unauthenticated users (no weights);
  this adapter keeps to 50.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .base import Capabilities, RateLimiter, RetryableError, VenueAdapter, with_backoff

BASE = "https://mainnet.zklighter.elliot.ai/api/v1/"
LIMITER = RateLimiter(per_minute=50)
FILLS_CAP = 10_000
RES = {"1m": "1m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
COLS = ["venue", "account_id", "fill_id", "ts", "coin", "side", "px", "sz", "start_position", "closed_pnl",
        "fee", "fee_token", "builder_fee", "is_taker", "order_id", "liq_role", "liq_mark_px"]


class Lighter(VenueAdapter):
    name, label = "lighter", "LT"
    FILLS_CAP = FILLS_CAP
    funding_estimated = True
    caps = Capabilities(
        leaderboard=False, fills_history=True, funding_history=False, liquidations=False, account_state=True,
        candles=True, orderbook_history=False,
        notes={"leaderboard": "No public P&L leaderboard: candidates come from sampling every market's latest "
                              "trades over time, ranked by each account's own P&L history.",
               "funding_history": "An account's funding payments need its login; funding is estimated from its "
                                  "position each hour x the market's hourly rate.",
               "liquidations": "Liquidation records need the account's login; liquidation trades are flagged "
                               "from the trade type only.",
               "fills_history": f"Paged 100 at a time at 60 requests a minute; the newest {FILLS_CAP:,} are kept."})

    def __init__(self):
        super().__init__()
        self._markets: dict[str, int] | None = None
        self._symbols: dict[int, str] = {}

    def get(self, path: str, params: dict | None = None):
        LIMITER.spend(1)

        def call():
            r = self.session.get(BASE + path, params=params or {}, timeout=30)
            if r.status_code in (429, 405):
                raise RetryableError(f"rate limited ({r.status_code})")
            r.raise_for_status()
            return r.json()
        return with_backoff(call)

    # ------------------------------------------------------------ markets
    def markets(self) -> dict[str, int]:
        if self._markets is None:
            d = self.get("orderBooks")
            self._markets = {m["symbol"]: int(m["market_id"]) for m in d.get("order_books", [])
                             if m.get("market_type", "perp") == "perp"}
            self._symbols = {v: k for k, v in self._markets.items()}
        return self._markets

    def symbol(self, market_id: int) -> str:
        if not self._symbols:
            self.markets()
        return self._symbols.get(int(market_id), f"M{market_id}")

    # ------------------------------------------------------------ discovery
    def sample_accounts(self, top_markets: int = 40) -> pd.DataFrame:
        """Accounts in the latest 100 trades of the most traded markets
        (one call per market): account_id, notional, trades."""
        st = self.get("exchangeStats")
        by_vol = sorted(st.get("order_book_stats", []), key=lambda x: -(x.get("daily_quote_token_volume") or 0))
        mk = self.markets()
        rows = []
        for s in by_vol[:top_markets]:
            mid = mk.get(s["symbol"])
            if mid is None:
                continue
            for t in self.get("recentTrades", {"market_id": mid, "limit": 100}).get("trades", []):
                usd = float(t.get("usd_amount") or 0)
                for a in (t.get("ask_account_id"), t.get("bid_account_id")):
                    if a is not None:
                        rows.append((str(a), usd))
        if not rows:
            return pd.DataFrame(columns=["account_id", "notional", "trades"])
        df = pd.DataFrame(rows, columns=["account_id", "notional"])
        return df.groupby("account_id").agg(notional=("notional", "sum"), trades=("notional", "size")).reset_index()

    def leaderboard(self, con=None, max_accounts: int = 600, budget_s: float = 900) -> pd.DataFrame:
        """Candidates from the sampled accounts (most notional seen first),
        with week / month / all-time P&L and volume from each account's own
        daily P&L history."""
        if con is None:
            return pd.DataFrame()
        seen = pd.read_sql_query("SELECT account_id, notional FROM tr_seen WHERE venue=? ORDER BY notional DESC LIMIT ?",
                                 con, params=(self.name, max_accounts))
        rows, t0 = [], time.time()
        for acct in seen["account_id"]:
            if time.time() - t0 > budget_s:
                break
            try:
                p = self.pnl_history(acct)
            except Exception:  # noqa: BLE001 - one account failing never stops the list
                continue
            if p.empty:
                continue
            row = {"account_id": acct, "account_value": float(p["equity"].iloc[-1])}
            last = p.iloc[-1]
            for w, days in (("week", 7), ("month", 30), ("allTime", None)):
                base = p[p.index <= p.index[-1] - pd.Timedelta(days=days)].iloc[-1] if days and \
                    (p.index <= p.index[-1] - pd.Timedelta(days=days)).any() else p.iloc[0] * 0
                row[f"{w}_pnl"] = float(last["trade_pnl"] - base["trade_pnl"])
                row[f"{w}_vlm"] = float(last["volume"] - base["volume"])
            rows.append(row)
        return pd.DataFrame(rows)

    # ------------------------------------------------------------ account
    def pnl_history(self, account_id: str, resolution: str = "1d") -> pd.DataFrame:
        """Daily cumulative trade P&L, volume, flows and equity
        (= inflow - outflow + every P&L line; checked against the account's
        collateral on a live account)."""
        now = int(time.time())
        d = self.get("pnl", {"by": "index", "value": account_id, "resolution": resolution,
                              "start_timestamp": now - 400 * 86400, "end_timestamp": now, "count_back": 400})
        rows = d.get("pnl") or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        for c in ("trade_pnl", "inflow", "outflow", "volume", "pool_pnl", "staking_pnl", "trade_spot_pnl",
                  "spot_inflow", "spot_outflow"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0) if c in df else 0.0
        df["equity"] = (df["inflow"] - df["outflow"] + df["trade_pnl"] + df["pool_pnl"] + df["staking_pnl"]
                        + df["trade_spot_pnl"] - df["spot_outflow"] + df["spot_inflow"])
        df.index = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        return df.sort_index()

    def portfolio(self, account_id: str) -> dict:
        p = self.pnl_history(account_id)
        if p.empty:
            return {}
        out = {"allTime": {"av": p["equity"], "vlm": float(p["volume"].iloc[-1] - p["volume"].iloc[0])}}
        out["perpAllTime"] = out["allTime"]
        return out

    def account_state(self, account_id: str) -> dict:
        d = self.get("account", {"by": "index", "value": account_id})
        a = (d.get("accounts") or [{}])[0]
        return {"account_value": float(a.get("collateral") or 0), "positions": a.get("positions", []),
                "account_type": a.get("account_type")}

    def is_vault(self, account_id: str) -> bool:
        """Public pools (Lighter's vaults) are accounts of a different type."""
        try:
            return int(self.account_state(account_id).get("account_type") or 0) != 0
        except Exception:  # noqa: BLE001
            return False

    def recent_fill_rate(self, account_id: str, hours: int = 24) -> int:
        cutoff = (time.time() - hours * 3600) * 1000
        n, cursor = 0, None
        for _ in range(20):                                  # at most 2,000
            params = {"account_index": account_id, "sort_by": "timestamp", "sort_dir": "desc", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            d = self.get("trades", params)
            tr = d.get("trades") or []
            n += sum(1 for t in tr if t["timestamp"] >= cutoff)
            if not tr or tr[-1]["timestamp"] < cutoff or not d.get("next_cursor"):
                break
            cursor = d["next_cursor"]
        return n

    def fills(self, account_id: str, start_ms: int = 0, end_ms: int | None = None,
              max_pages: int | None = None) -> pd.DataFrame:
        """Trades newest first, back to start_ms or FILLS_CAP."""
        raw, cursor, pages = [], None, 0
        limit_pages = max_pages or FILLS_CAP // 100
        while pages < limit_pages:
            params = {"account_index": account_id, "sort_by": "timestamp", "sort_dir": "desc", "limit": 100}
            if cursor:
                params["cursor"] = cursor
            d = self.get("trades", params)
            pages += 1
            tr = d.get("trades") or []
            raw.extend(t for t in tr if t["timestamp"] > start_ms and (end_ms is None or t["timestamp"] <= end_ms))
            if not tr or tr[-1]["timestamp"] <= start_ms or not d.get("next_cursor"):
                break
            cursor = d["next_cursor"]
        return normalize_trades(raw, account_id, self.symbol)

    def funding(self, account_id: str, start_ms: int = 0) -> pd.DataFrame:
        """Not public; see estimate_funding."""
        return pd.DataFrame(columns=["venue", "account_id", "ts", "coin", "usdc", "position_size", "rate"])

    # ------------------------------------------------------------ market data
    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        mid = self.markets().get(coin)
        cols = ["venue", "coin", "interval", "ts", "o", "h", "l", "c", "v"]
        if mid is None:
            return pd.DataFrame(columns=cols)
        step = {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[interval] * 1000
        out, end = [], int(end_ms)
        while end > start_ms:
            d = self.get("candles", {"market_id": mid, "resolution": RES[interval], "start_timestamp": int(start_ms // 1000),
                                     "end_timestamp": int(end // 1000), "count_back": 500})
            c = d.get("c") or []
            if not c:
                break
            out.extend(c)
            first = min(x["t"] for x in c)
            if first <= start_ms or len(c) < 500:
                break
            end = first - step
        rows = [{"venue": self.name, "coin": coin, "interval": interval, "ts": pd.to_datetime(x["t"], unit="ms", utc=True),
                 "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]), "c": float(x["c"]), "v": float(x.get("v") or 0)}
                for x in out]
        df = pd.DataFrame(rows, columns=cols)
        return df.drop_duplicates("ts").sort_values("ts") if not df.empty else df

    def market_funding(self, coin: str, start_ms: int) -> pd.DataFrame:
        """Hourly rates as a fraction, positive = longs pay (Lighter gives a
        percentage and the paying side)."""
        mid = self.markets().get(coin)
        cols = ["venue", "coin", "ts", "rate"]
        if mid is None:
            return pd.DataFrame(columns=cols)
        out, start = [], int(start_ms // 1000)
        now = int(time.time())
        while start < now:
            d = self.get("fundings", {"market_id": mid, "resolution": "1h", "start_timestamp": start,
                                      "end_timestamp": now, "count_back": 1000})
            f = d.get("fundings") or []
            if not f:
                break
            out.extend(f)
            last = max(x["timestamp"] for x in f)
            if len(f) < 1000 or last <= start:
                break
            start = last + 1
        rows = [{"venue": self.name, "coin": coin, "ts": pd.to_datetime(x["timestamp"], unit="s", utc=True),
                 "rate": float(x["rate"]) / 100 * (1 if x.get("direction", "long") == "long" else -1)} for x in out]
        df = pd.DataFrame(rows, columns=cols)
        return df.drop_duplicates("ts") if not df.empty else df


def normalize_trades(raw: list[dict], account_id: str, symbol=lambda m: str(m)) -> pd.DataFrame:
    """Lighter account trades -> the normalized fills schema. The account's
    side and role come from the bid/ask account ids and is_maker_ask; its
    position before the trade (signed) and entry cost come from the
    matching maker_/taker_ fields; closed P&L is computed from them:
    closed qty x (price - average entry) on the reducing part."""
    if not raw:
        return pd.DataFrame(columns=COLS)
    acct = str(account_id)
    rows = []
    for t in raw:
        buy = str(t.get("bid_account_id")) == acct
        maker = (t.get("is_maker_ask") and not buy) or (not t.get("is_maker_ask") and buy)
        role = "maker" if maker else "taker"
        size, px = float(t["size"]), float(t["price"])
        pos = float(t.get(f"{role}_position_size_before") or 0)
        entry_quote = float(t.get(f"{role}_entry_quote_before") or 0)
        signed = size if buy else -size
        closed = 0.0
        if pos and np.sign(signed) != np.sign(pos):
            q = min(size, abs(pos))
            avg = entry_quote / abs(pos)
            closed = q * (px - avg) * np.sign(pos)
        fee_rate = t.get(f"{role}_fee")
        fee = size * px * float(fee_rate) / 1e6 if fee_rate else 0.0
        kind = t.get("type", "trade")
        liq = None
        if kind in ("liquidation", "deleverage"):
            liq = "liquidated" if pos and np.sign(signed) != np.sign(pos) and not maker else "counterparty"
        rows.append({"venue": "lighter", "account_id": acct, "fill_id": f"{t['trade_id']}{'b' if buy else 'a'}",
                     "ts": pd.to_datetime(t["timestamp"], unit="ms", utc=True), "coin": symbol(t["market_id"]),
                     "side": "B" if buy else "A", "px": px, "sz": size, "start_position": pos, "closed_pnl": closed,
                     "fee": fee, "fee_token": "USDC", "builder_fee": 0.0, "is_taker": int(not maker),
                     "order_id": str(t.get("bid_id" if buy else "ask_id", "")), "liq_role": liq, "liq_mark_px": None})
    df = pd.DataFrame(rows, columns=COLS).drop_duplicates("fill_id")
    df["_o"] = pd.to_numeric(df["fill_id"].str[:-1], errors="coerce")
    return df.sort_values(["ts", "_o"], kind="stable").drop(columns="_o").reset_index(drop=True)


def estimate_funding(fills: pd.DataFrame, rates: dict[str, pd.Series], prices: dict[str, pd.Series],
                     account_id: str, venue: str = "lighter") -> pd.DataFrame:
    """Hourly funding the account would have paid or received: its position
    at each settlement (from the fills) x the mark (hourly close) x the
    market's rate (positive = longs pay). Labelled as estimated."""
    cols = ["venue", "account_id", "ts", "coin", "usdc", "position_size", "rate"]
    if fills is None or fills.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for coin, g in fills.sort_values("ts").groupby("coin"):
        r = rates.get(coin)
        if r is None or r.empty:
            continue
        end_pos = g["start_position"] + np.where(g["side"] == "B", g["sz"], -g["sz"])
        r = r[r.index > g["ts"].min()]
        if r.empty:
            continue
        k = np.searchsorted(g["ts"].to_numpy(), r.index.to_numpy(), side="right") - 1
        pos = np.where(k >= 0, end_pos.to_numpy()[np.clip(k, 0, None)], 0.0)
        px = prices.get(coin)
        mark = px.reindex(r.index, method="ffill").to_numpy() if px is not None and not px.empty else \
            np.full(len(r), np.nan)
        usdc = -pos * mark * r.to_numpy()
        m = (pos != 0) & np.isfinite(usdc)
        out.append(pd.DataFrame({"venue": venue, "account_id": str(account_id), "ts": r.index[m], "coin": coin,
                                 "usdc": usdc[m], "position_size": pos[m], "rate": r.to_numpy()[m]}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=cols)
