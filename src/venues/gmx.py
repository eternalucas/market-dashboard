"""GMX v2 adapter, Arbitrum (read-only). Checked live on 27 Oct 2026:

- Public, no login: every account's executed orders with realised P&L,
  price impact, position/borrowing/funding fees and position size after
  each order (GMX's own data service, `tradeActions`), per-account stats for
  any period (`periodAccountStats`: P&L, fees, volume, maximum capital,
  wins/losses; what GMX's own leaderboard uses), the token list and candles
  (arbitrum-api.gmxinfra.io).
- How it differs from an order book: every order fills at the oracle price
  plus a price-impact charge (no spread, no maker/taker; limit orders are
  marked as "maker" here only to keep the field meaningful). Long and short
  positions on the same market are separate, so each is its own "market"
  here ("ETH long", "ETH short").
- No account-value history: the account's maximum capital in the period
  (GMX's own denominator for P&L %) stands in for it, and is labelled.
- Funding: paid funding is in each order's fees (recorded as a funding
  payment at that order); claimable funding received isn't included.
- No market funding-rate history is read for GMX.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .base import Capabilities, RateLimiter, RetryableError, VenueAdapter, with_backoff
from .keccak import checksum

GQL = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"
API = "https://arbitrum-api.gmxinfra.io"
LIMITER = RateLimiter(per_minute=120)
E30 = 10 ** 30
FILLS_CAP = 10_000
ORDER_TYPES = {2: "MarketIncrease", 3: "LimitIncrease", 4: "MarketDecrease", 5: "LimitDecrease",
               6: "StopLossDecrease", 7: "Liquidation", 8: "StopIncrease"}
INCREASE = {2, 3, 8}
COLS = ["venue", "account_id", "fill_id", "ts", "coin", "side", "px", "sz", "start_position", "closed_pnl",
        "fee", "fee_token", "builder_fee", "is_taker", "order_id", "liq_role", "liq_mark_px"]
FIELDS = ("id orderType marketAddress isLong sizeDeltaUsd sizeDeltaInTokens positionSizeInTokens executionPrice "
          "basePnlUsd pnlUsd priceImpactUsd positionFeeAmount borrowingFeeAmount fundingFeeAmount liquidationFeeAmount "
          "collateralTokenPriceMin initialCollateralTokenAddress timestamp")


class GMX(VenueAdapter):
    name, label = "gmx", "GMX"
    FILLS_CAP = FILLS_CAP
    caps = Capabilities(
        leaderboard=True, fills_history=True, funding_history=True, liquidations=True, account_state=False,
        candles=True, orderbook_history=False,
        notes={"leaderboard": "GMX's own per-account period stats (Arbitrum).",
               "funding_history": "Funding paid is taken from each order's fees; claimable funding received is "
                                  "not included.",
               "account_state": "No account-value history: the account's maximum capital stands in for it.",
               "orderbook_history": "GMX has no order book: orders fill at the oracle price plus price impact."})

    def __init__(self):
        super().__init__()
        self._tokens: dict[str, dict] | None = None
        self._markets: dict[str, dict] = {}

    def gql(self, query: str):
        LIMITER.spend(1)

        def call():
            r = self.session.post(GQL, json={"query": query}, timeout=60)
            if r.status_code == 429:
                raise RetryableError("rate limited (429)")
            r.raise_for_status()
            d = r.json()
            if d.get("errors"):
                raise ValueError(str(d["errors"])[:300])
            return d["data"]
        return with_backoff(call)

    def rest(self, path: str, params: dict | None = None):
        LIMITER.spend(1)

        def call():
            r = self.session.get(API + path, params=params or {}, timeout=60)
            if r.status_code == 429:
                raise RetryableError("rate limited (429)")
            r.raise_for_status()
            return r.json()
        return with_backoff(call)

    # ------------------------------------------------------------ reference data
    def tokens(self) -> dict[str, dict]:
        if self._tokens is None:
            self._tokens = {t["address"].lower(): t for t in self.rest("/tokens").get("tokens", [])}
        return self._tokens

    def market(self, address: str) -> dict:
        a = address.lower()
        if a not in self._markets:
            d = self.gql('{ marketInfos(limit: 500) { id indexTokenAddress longTokenAddress shortTokenAddress } }')
            tk = self.tokens()
            for m in d["marketInfos"]:
                idx = tk.get(m["indexTokenAddress"].lower(), {})
                self._markets[m["id"].lower()] = {"symbol": idx.get("symbol", m["indexTokenAddress"][:8]),
                                                  "decimals": int(idx.get("decimals", 18))}
        return self._markets.get(a, {"symbol": address[:8], "decimals": 18})

    # ------------------------------------------------------------ leaderboard
    def period_stats(self, days: int | None) -> pd.DataFrame:
        day = int(time.time() // 86400 * 86400)
        frm = day - days * 86400 if days else 0
        rows, offset = [], 0
        while True:
            d = self.gql("{ periodAccountStats(limit: 2000, offset: %d, where: {from: %d}) { id realizedPnl "
                         "realizedFees realizedPriceImpact startUnrealizedPnl volume maxCapital closedCount wins "
                         "losses } }" % (offset, frm))["periodAccountStats"]
            rows.extend(d)
            if len(d) < 2000 or offset > 40_000:
                break
            offset += 2000
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        for c in ("realizedPnl", "realizedFees", "realizedPriceImpact", "volume", "maxCapital"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0) / E30
        df["pnl"] = df["realizedPnl"] - df["realizedFees"] + df["realizedPriceImpact"]
        return df

    def leaderboard(self, con=None) -> pd.DataFrame:
        out = None
        for w, days in (("week", 7), ("month", 30), ("allTime", None)):
            d = self.period_stats(days)
            if d.empty:
                continue
            d = d.rename(columns={"id": "account_id"})
            d["account_id"] = d["account_id"].str.lower()
            part = d[["account_id", "pnl", "volume", "maxCapital"]].rename(
                columns={"pnl": f"{w}_pnl", "volume": f"{w}_vlm", "maxCapital": f"{w}_cap"})
            out = part if out is None else out.merge(part, on="account_id", how="outer")
        if out is None:
            return pd.DataFrame()
        out["account_value"] = out.get("allTime_cap", out.get("month_cap"))
        return out.fillna({c: 0.0 for c in out.columns if c.endswith(("_pnl", "_vlm"))})

    # ------------------------------------------------------------ account
    def actions(self, account_id: str, start_s: int = 0, limit_total: int = FILLS_CAP) -> list[dict]:
        acct = checksum(account_id)
        out, cursor = [], int(start_s)
        while len(out) < limit_total:
            q = ('{ tradeActions(limit: 1000, orderBy: timestamp_ASC, where: {account_eq: "%s", eventName_eq: '
                 '"OrderExecuted", orderType_in: [2,3,4,5,6,7,8], timestamp_gte: %d}) { %s } }' % (acct, cursor, FIELDS))
            d = self.gql(q)["tradeActions"]
            new = [x for x in d if x["id"] not in {y["id"] for y in out[-1000:]}]
            out.extend(new)
            if len(d) < 1000 or not new:
                break
            cursor = max(x["timestamp"] for x in d)
        return out

    def fills(self, account_id: str, start_ms: int = 0, end_ms: int | None = None,
              max_pages: int | None = None) -> pd.DataFrame:
        raw = self.actions(account_id, int(start_ms // 1000) + (1 if start_ms else 0))
        if end_ms:
            raw = [x for x in raw if x["timestamp"] * 1000 <= end_ms]
        f, _ = normalize_actions(raw, account_id, self.market, self.tokens())
        return f

    def funding(self, account_id: str, start_ms: int = 0) -> pd.DataFrame:
        raw = self.actions(account_id, int(start_ms // 1000) + (1 if start_ms else 0))
        _, fund = normalize_actions(raw, account_id, self.market, self.tokens())
        return fund

    def recent_fill_rate(self, account_id: str, hours: int = 24) -> int:
        d = self.gql('{ tradeActionsConnection(orderBy: id_ASC, where: {account_eq: "%s", eventName_eq: '
                     '"OrderExecuted", timestamp_gte: %d}) { totalCount } }'
                     % (checksum(account_id), int(time.time() - hours * 3600)))
        return int(d["tradeActionsConnection"]["totalCount"])

    def is_vault(self, account_id: str) -> bool:
        return False

    def portfolio(self, account_id: str) -> dict:
        """No account-value history on GMX: the account's maximum capital
        over its whole history, as a flat series, stands in for it."""
        d = self.gql('{ periodAccountStats(limit: 1, where: {from: 0, id_eq: "%s"}) { maxCapital volume } }'
                     % checksum(account_id))["periodAccountStats"]
        if not d:
            return {}
        cap = float(d[0]["maxCapital"]) / E30
        days = pd.date_range(pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=400),
                             pd.Timestamp.now(tz="UTC").normalize(), freq="D")
        av = pd.Series(cap, index=days)
        out = {"allTime": {"av": av, "vlm": float(d[0]["volume"]) / E30}}
        out["perpAllTime"] = out["allTime"]
        return out

    # ------------------------------------------------------------ market data
    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        sym = coin.split(" ")[0]
        cols = ["venue", "coin", "interval", "ts", "o", "h", "l", "c", "v"]
        step = {"1m": 60, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[interval]
        n = int(min(10_000, max(1, (end_ms - start_ms) / 1000 / step + 2)))
        try:
            d = self.rest("/prices/candles", {"tokenSymbol": sym, "period": interval, "limit": n})
        except Exception:  # noqa: BLE001 - e.g. a market without candles
            return pd.DataFrame(columns=cols)
        rows = [{"venue": self.name, "coin": coin, "interval": interval, "ts": pd.to_datetime(c[0], unit="s", utc=True),
                 "o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4]), "v": 0.0}
                for c in d.get("candles", [])]
        df = pd.DataFrame(rows, columns=cols)
        if df.empty:
            return df
        df = df[(df["ts"] >= pd.Timestamp(start_ms, unit="ms", tz="UTC") - pd.Timedelta(seconds=step))]
        return df.sort_values("ts")

    def market_funding(self, coin: str, start_ms: int) -> pd.DataFrame:
        return pd.DataFrame(columns=["venue", "coin", "ts", "rate"])


def coin_name(symbol: str, is_long: bool) -> str:
    return f"{symbol} {'long' if is_long else 'short'}"


def normalize_actions(raw: list[dict], account_id: str, market, tokens: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Executed GMX orders -> (fills, funding payments).

    - Size in index tokens; the position before = position after -/+ the
      order's size, signed by side.
    - Increase price = order size in USD / size in tokens (the price the
      position was opened at); its cost is GMX's own P&L for the order
      (fees after discounts). Decrease: the realised P&L before costs is
      GMX's P&L + fees + funding paid, so closed - fees + funding equals
      GMX's own realised P&L exactly; the fill price is set so that the
      fill's cash flow equals that P&L against the average entry.
    - Fees (position, borrowing, liquidation) and funding paid are in the
      collateral token: amount x the collateral price (both scaled by 1e30).
    """
    acct = account_id.lower()
    fills, fund = [], []
    avg: dict[str, tuple[float, float]] = {}               # coin -> (tokens, cost)
    for x in sorted(raw, key=lambda x: (x["timestamp"], x["id"])):
        ot = int(x["orderType"])
        m = market(x["marketAddress"])
        dec = m["decimals"]
        coin = coin_name(m["symbol"], bool(x["isLong"]))
        sgn = 1.0 if x["isLong"] else -1.0
        size_tok = float(x["sizeDeltaInTokens"] or 0) / 10 ** dec
        size_usd = float(x["sizeDeltaUsd"] or 0) / E30
        after = float(x["positionSizeInTokens"] or 0) / 10 ** dec
        if size_tok <= 0:
            continue
        inc = ot in INCREASE
        before = after - size_tok if inc else after + size_tok
        cpx = float(x.get("collateralTokenPriceMin") or 0) / E30
        fee = sum(float(x.get(k) or 0) for k in ("positionFeeAmount", "borrowingFeeAmount", "liquidationFeeAmount")) * cpx
        paid_funding = float(x.get("fundingFeeAmount") or 0) * cpx
        tok, cost = avg.get(coin, (0.0, 0.0))
        if abs(before) < 1e-12 or tok <= 1e-12:
            tok, cost = abs(before), abs(before) * (size_usd / size_tok)      # unknown history: assume today's price
        pnl = float(x["pnlUsd"]) / E30 if x.get("pnlUsd") is not None else None
        if inc:
            px = size_usd / size_tok
            closed = 0.0
            if pnl is not None:            # GMX books an increase's costs as its (negative) P&L: use its number
                fee = -pnl
            tok, cost = tok + size_tok, cost + size_usd
        else:
            entry = cost / tok if tok else size_usd / size_tok
            # GMX's realised P&L after every cost; the costs are split out again so that
            # closed - fee + funding == GMX's own figure exactly
            closed = (pnl + fee + paid_funding) if pnl is not None else \
                (float(x.get("basePnlUsd") or 0) + float(x.get("priceImpactUsd") or 0)) / E30
            px = entry + closed / size_tok * sgn                              # cash flow == realised P&L
            frac = min(size_tok / tok, 1.0) if tok else 1.0
            tok, cost = max(tok - size_tok, 0.0), cost * (1 - frac)
        avg[coin] = (tok, cost)
        buy = (inc and x["isLong"]) or (not inc and not x["isLong"])
        fills.append({"venue": "gmx", "account_id": acct, "fill_id": x["id"],
                      "ts": pd.to_datetime(int(x["timestamp"]), unit="s", utc=True), "coin": coin,
                      "side": "B" if buy else "A", "px": px, "sz": size_tok, "start_position": sgn * before,
                      "closed_pnl": closed, "fee": fee, "fee_token": "USD", "builder_fee": 0.0,
                      "is_taker": int(ot not in (3, 5)), "order_id": x["id"].split(":")[0],
                      "liq_role": "liquidated" if ot == 7 else None, "liq_mark_px": None})
        if paid_funding:
            fund.append({"venue": "gmx", "account_id": acct, "ts": pd.to_datetime(int(x["timestamp"]), unit="s", utc=True),
                         "coin": coin, "usdc": -paid_funding, "position_size": sgn * before, "rate": np.nan})
    f = pd.DataFrame(fills, columns=COLS)
    u = pd.DataFrame(fund, columns=["venue", "account_id", "ts", "coin", "usdc", "position_size", "rate"])
    if not u.empty:   # several orders in one second on one coin: one row per (ts, coin) key
        u = u.groupby(["venue", "account_id", "ts", "coin"], as_index=False).agg(
            usdc=("usdc", "sum"), position_size=("position_size", "first"), rate=("rate", "first"))
    return f, u
