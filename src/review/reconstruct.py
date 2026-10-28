"""Fills -> round trips.

A round trip opens when a position leaves flat and closes when it returns
to flat. A flip (long straight to short in one fill) closes one trip and
opens the next on the same fill; that fill's fee is split between the two
by quantity.

Every dollar is traced:
  realized_pnl  = sum of the venue's closedPnl on the trip's reducing fills
  fees          = sum of fees on the trip's fills (builder fee included)
  funding       = sum of funding payments on that coin while the trip was open
  net           = realized_pnl - fees + funding
Anything that can't be attributed (fills of a position already open when the
history starts, funding outside any trip, a position that changed without a
fill in the history) is counted in the report, not dropped silently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..venues.hyperliquid import is_perp

TRIP_COLS = ["trip_id", "coin", "side", "open_ts", "close_ts", "entry_qty", "entry_notional", "exit_qty",
             "exit_notional", "max_qty", "realized_pnl", "fees", "funding", "fills", "taker_fills", "adds",
             "added_while_losing", "liquidated", "is_open", "exit_unobserved", "first_px"]


def _tol(x: float) -> float:
    return 1e-9 * max(1.0, abs(x))


def _flat(x: float, ref: float) -> bool:
    return abs(x) <= _tol(ref)


def _leg(row, qty: float, fee: float) -> tuple:
    return (row.ts, qty, row.px, fee, int(row.is_taker))


def _new_trip(coin: str, row, qty: float, fee: float, ts) -> dict:
    return {"_legs": [_leg(row, qty if row.end_pos > 0 else -qty, fee)],
            "trip_id": f"{coin}:{row.fill_id}", "coin": coin, "side": "Long" if row.end_pos > 0 else "Short",
            "open_ts": ts, "close_ts": pd.NaT, "entry_qty": qty, "entry_notional": qty * row.px,
            "exit_qty": 0.0, "exit_notional": 0.0, "max_qty": qty, "realized_pnl": 0.0, "fees": fee,
            "funding": 0.0, "fills": 1, "taker_fills": int(row.is_taker), "adds": 0,
            "added_while_losing": False, "liquidated": False, "is_open": True, "exit_unobserved": False,
            "first_px": row.px}


def reconstruct(fills: pd.DataFrame, funding: pd.DataFrame | None = None, legs: bool = False
                ) -> tuple[pd.DataFrame, dict]:
    """fills: coin, ts, side (B/A), px, sz, start_position, closed_pnl, fee, is_taker, fill_id, [liq_role].
    funding: coin, ts, usdc, [position_size]. Returns (trips, report).
    legs=True adds a "legs" column: each trip's own part of every fill as
    (ts, signed qty, px, fee, is_taker), a flip's fill split between the two
    trips by quantity (used by the replay engine)."""
    report = {"spot_fills_skipped": 0, "excluded_fills": 0, "excluded_realized_pnl": 0.0, "excluded_fees": 0.0,
              "unattributed_funding_usd": 0.0, "unattributed_funding_n": 0, "liquidations": 0,
              "first_fill": None, "last_fill": None}
    if fills is None or fills.empty:
        return pd.DataFrame(columns=TRIP_COLS), report
    f = fills.copy()
    if "liq_role" not in f:
        f["liq_role"] = None
    perp = f["coin"].map(is_perp)
    report["spot_fills_skipped"] = int((~perp).sum())
    f = f[perp].assign(_order=pd.to_numeric(f["fill_id"], errors="coerce"))
    f = f.sort_values(["ts", "_order"], kind="stable").drop(columns="_order")
    if f.empty:
        return pd.DataFrame(columns=TRIP_COLS), report
    report["first_fill"], report["last_fill"] = f["ts"].min(), f["ts"].max()
    f["signed_sz"] = np.where(f["side"] == "B", f["sz"], -f["sz"])
    f["end_pos"] = f["start_position"] + f["signed_sz"]

    trips: list[dict] = []
    report["position_gaps"] = []
    for coin, g in f.groupby("coin", sort=False):
        cur, expected = None, None
        for row in g.itertuples(index=False):
            start, end, sz = row.start_position, row.end_pos, row.sz
            liq = row.liq_role == "liquidated"
            report["liquidations"] += int(liq)
            # The position before this fill should equal the position after the previous one.
            # If it doesn't, the position changed without a fill in this history (e.g. a
            # liquidation or delisting settlement not reported as the wallet's fill). The open
            # trip is closed as "exit not observed": its exit price and P&L are unknown.
            if expected is not None and abs(start - expected) > _tol(max(abs(start), abs(expected), sz)):
                report["position_gaps"].append({"coin": coin, "before": row.ts, "expected": expected, "found": start})
                if cur is not None:
                    cur["close_ts"], cur["is_open"], cur["exit_unobserved"] = row.ts, False, True
                    trips.append(cur)
                    cur = None
            expected = end
            if _flat(start, sz):                                           # opens from flat
                if not _flat(end, sz):
                    cur = _new_trip(coin, row, abs(end), row.fee, row.ts)
                    cur["realized_pnl"] += row.closed_pnl
                else:
                    report["excluded_fills"] += 1
                    report["excluded_realized_pnl"] += row.closed_pnl
                    report["excluded_fees"] += row.fee
                continue
            if cur is None:                                                # position predates the history
                closing = _flat(end, start) or np.sign(end) != np.sign(start)
                if closing:
                    share = abs(start) / sz if sz else 1.0
                    report["excluded_fills"] += 1
                    report["excluded_realized_pnl"] += row.closed_pnl
                    report["excluded_fees"] += row.fee * share
                    if not _flat(end, sz):                                 # flip: the new side is fully observed
                        cur = _new_trip(coin, row, abs(end), row.fee * (1 - share), row.ts)
                else:
                    report["excluded_fills"] += 1
                    report["excluded_realized_pnl"] += row.closed_pnl
                    report["excluded_fees"] += row.fee
                continue
            cur["fills"] += 1
            cur["taker_fills"] += int(row.is_taker)
            if _flat(end, start) or np.sign(end) != np.sign(start):       # closes, possibly flips
                close_qty = abs(start)
                share = close_qty / sz if sz else 1.0
                cur["exit_qty"] += close_qty
                cur["exit_notional"] += close_qty * row.px
                cur["realized_pnl"] += row.closed_pnl
                cur["fees"] += row.fee * share
                cur["_legs"].append(_leg(row, -start, row.fee * share))
                cur["liquidated"] |= liq
                cur["close_ts"], cur["is_open"] = row.ts, False
                trips.append(cur)
                cur = _new_trip(coin, row, abs(end), row.fee * (1 - share), row.ts) if not _flat(end, sz) else None
            elif abs(end) > abs(start):                                    # adds
                avg = cur["entry_notional"] / cur["entry_qty"]
                cur["adds"] += 1
                cur["added_while_losing"] |= bool(row.px < avg if start > 0 else row.px > avg)
                cur["entry_qty"] += sz
                cur["entry_notional"] += sz * row.px
                cur["max_qty"] = max(cur["max_qty"], abs(end))
                cur["fees"] += row.fee
                cur["_legs"].append(_leg(row, end - start, row.fee))
                cur["realized_pnl"] += row.closed_pnl          # 0 on venues' adding fills; kept so every dollar is traced
            else:                                                          # partial close
                cur["exit_qty"] += sz
                cur["exit_notional"] += sz * row.px
                cur["realized_pnl"] += row.closed_pnl
                cur["fees"] += row.fee
                cur["_legs"].append(_leg(row, end - start, row.fee))
                cur["liquidated"] |= liq
        if cur is not None:
            trips.append(cur)

    df = pd.DataFrame(trips, columns=TRIP_COLS + (["_legs"] if legs else []))
    if legs:
        df = df.rename(columns={"_legs": "legs"})
    if df.empty:
        return df, report
    for c in ("open_ts", "close_ts"):          # an all-open column of NaT would otherwise lose its time zone
        df[c] = pd.to_datetime(df[c], utc=True)
    _attribute_funding(df, funding, report)
    df["avg_entry"] = df["entry_notional"] / df["entry_qty"]
    df["avg_exit"] = df["exit_notional"] / df["exit_qty"].replace(0, np.nan)
    df["max_notional"] = df["max_qty"] * df["avg_entry"]
    df["net"] = df["realized_pnl"] - df["fees"] + df["funding"]
    df["return_on_notional"] = df["net"] / df["max_notional"]
    df["hold_hours"] = (df["close_ts"] - df["open_ts"]).dt.total_seconds() / 3600
    df["taker_share"] = df["taker_fills"] / df["fills"]
    for c in ("added_while_losing", "liquidated", "is_open", "exit_unobserved"):
        df[c] = df[c].astype(bool)
    return df.sort_values("open_ts").reset_index(drop=True), report


def _attribute_funding(trips: pd.DataFrame, funding: pd.DataFrame | None, report: dict) -> None:
    """Each funding payment goes to the trip on that coin whose open period
    contains it (open < ts <= close, or still open), if the position's side
    matches. Anything else is counted as unattributed."""
    if funding is None or funding.empty:
        return
    now = pd.Timestamp.now(tz="UTC")
    for coin, fg in funding.groupby("coin"):
        idx = trips.index[trips["coin"] == coin]
        if len(idx) == 0:
            report["unattributed_funding_usd"] += float(fg["usdc"].sum())
            report["unattributed_funding_n"] += len(fg)
            continue
        t = trips.loc[idx].sort_values("open_ts")
        ns = lambda s: pd.to_datetime(s, utc=True).astype("int64").to_numpy()   # noqa: E731
        opens = ns(t["open_ts"])
        closes = ns(t["close_ts"].fillna(now))
        sides = np.where(t["side"] == "Long", 1, -1)
        fts = ns(fg["ts"])
        pos = np.searchsorted(opens, fts, side="left") - 1
        sizes = fg["position_size"].to_numpy() if "position_size" in fg else np.full(len(fg), np.nan)
        for ts, usdc, size, k in zip(fts, fg["usdc"].to_numpy(), sizes, pos):
            ok = k >= 0 and ts <= closes[k]
            if ok and size == size and size != 0:
                ok = np.sign(size) == sides[k]
            if ok:
                trips.loc[t.index[k], "funding"] += usdc
            else:
                report["unattributed_funding_usd"] += float(usdc)
                report["unattributed_funding_n"] += 1


def to_legacy(trips: pd.DataFrame) -> pd.DataFrame:
    """Column names the existing review UI and rules use."""
    out = trips.rename(columns={"open_ts": "open_time", "close_ts": "close_time", "realized_pnl": "pnl",
                                "is_open": "open"})
    return out


def from_legacy_fills(fills: pd.DataFrame) -> pd.DataFrame:
    """The CSV format the original review stored (Hyperliquid's raw field names)."""
    f = pd.DataFrame({
        "coin": fills["coin"], "ts": pd.to_datetime(fills["time"], utc=True), "side": fills["side"],
        "px": fills["px"].astype(float), "sz": fills["sz"].astype(float),
        "start_position": fills["startPosition"].astype(float), "closed_pnl": fills["closedPnl"].astype(float),
        "fee": fills["fee"].astype(float),
        "is_taker": fills["crossed"].astype(str).str.lower().eq("true").astype(int) if fills["crossed"].dtype == object
        else fills["crossed"].astype(bool).astype(int),
        "fill_id": fills["tid"].astype(str) if "tid" in fills else pd.Series(range(len(fills)), index=fills.index).astype(str).str.zfill(9),
    })
    f["liq_role"] = None
    return f
