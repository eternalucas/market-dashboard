"""Macro event study: how markets behaved in the days and weeks after each
scheduled release, to read the likely tone of the coming period.

Calendar sources
  US data releases (CPI, PPI, payrolls, jobless claims): FRED release dates,
      including scheduled future dates.
  FOMC: decision dates from federalreserve.gov (FRED's FOMC release updates
      daily, so it can't be used as a meeting calendar).
  Bank of England: MPC announcement dates published by the BoE (checked
      against bankofengland.co.uk; Sep 2022 moved from the 15th to the 22nd
      after the Queen's death).
  Bank of Japan: final day of each Monetary Policy Meeting (boj.or.jp).
  PBoC: the Loan Prime Rate fixing on the 20th of each month, or the next
      weekday if the 20th falls on a weekend. Chinese public holidays aren't
      modelled, so a few dates may be a day early.

Method
  Day 0 is the event date. The base price is the last close BEFORE day 0, so
  the reaction includes the release itself (US data at 8:30 ET and FOMC at
  14:00 ET land before that day's close; BoJ/BoE/PBoC decisions land before
  the US session). Return over h days = close on day h-1 / base - 1.
  Each event's return is compared with the asset's average return over any
  h-day window ("typical"), so a market that rises most of the time doesn't
  make every event look bullish.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BOE = [
    "2022-02-03", "2022-03-17", "2022-05-05", "2022-06-16", "2022-08-04", "2022-09-22", "2022-11-03", "2022-12-15",
    "2023-02-02", "2023-03-23", "2023-05-11", "2023-06-22", "2023-08-03", "2023-09-21", "2023-11-02", "2023-12-14",
    "2024-02-01", "2024-03-21", "2024-05-09", "2024-06-20", "2024-08-01", "2024-09-19", "2024-11-07", "2024-12-19",
    "2025-02-06", "2025-03-20", "2025-05-08", "2025-06-19", "2025-08-07", "2025-09-18", "2025-11-06", "2025-12-18",
    "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-18", "2026-07-30", "2026-09-17", "2026-11-05", "2026-12-17",
    "2027-02-04", "2027-03-18", "2027-04-29", "2027-06-17", "2027-07-29", "2027-09-16", "2027-11-04", "2027-12-16",
]
# FOMC rate decisions (statement day), from federalreserve.gov. 2020 includes the
# two emergency cuts (3 and 15 March); notation votes and non-rate statements are left out.
FOMC = [
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
]
BOJ = [
    "2022-01-18", "2022-03-18", "2022-04-28", "2022-06-17", "2022-07-21", "2022-09-22", "2022-10-28", "2022-12-20",
    "2023-01-18", "2023-03-10", "2023-04-28", "2023-06-16", "2023-07-28", "2023-09-22", "2023-10-31", "2023-12-19",
    "2024-01-23", "2024-03-19", "2024-04-26", "2024-06-14", "2024-07-31", "2024-09-20", "2024-10-31", "2024-12-19",
    "2025-01-24", "2025-03-19", "2025-05-01", "2025-06-17", "2025-07-31", "2025-09-19", "2025-10-30", "2025-12-19",
    "2026-01-23", "2026-03-19", "2026-04-28", "2026-06-16", "2026-07-31", "2026-09-18", "2026-10-30", "2026-12-18",
    "2027-01-22", "2027-03-18", "2027-04-28", "2027-06-11", "2027-07-22", "2027-09-22", "2027-10-29", "2027-12-17",
]


def pboc_lpr_dates(start: str = "2020-01-01", end: str = "2027-12-31") -> pd.DatetimeIndex:
    out = []
    for month in pd.date_range(start, end, freq="MS"):
        d = month + pd.Timedelta(days=19)
        while d.weekday() >= 5:
            d += pd.Timedelta(days=1)
        out.append(d)
    return pd.DatetimeIndex(out)


def central_bank_calendar() -> pd.DataFrame:
    rows = [("BoE decision", pd.Timestamp(d)) for d in BOE] + [("BoJ decision", pd.Timestamp(d)) for d in BOJ]
    rows += [("FOMC decision", pd.Timestamp(d)) for d in FOMC]
    rows += [("PBoC LPR", d) for d in pboc_lpr_dates()]
    return pd.DataFrame(rows, columns=["event", "date"])


# --------------------------------------------------------------------------- #
# What did the release say? (vs the previous reading; consensus isn't free)
# --------------------------------------------------------------------------- #

def _month_before(d: pd.Timestamp) -> pd.Timestamp:
    return (d - pd.DateOffset(months=1)).to_period("M").to_timestamp()


def classify(events: pd.DataFrame, data: dict[str, pd.Series]) -> pd.Series:
    """Label each event by what the data did, using FRED's current (revised)
    values, so labels can differ slightly from what printed on the day.

    CPI / PPI      'Hotter' if YoY inflation rose vs the prior month, else 'Cooler'
    Payrolls       'Strong' if the month's job gain beat the prior 3-month average
    Claims         'Rising' if the week's claims were above their 4-week average
    FOMC           'Hike' / 'Cut' / 'Hold' from the change in the target rate
    """
    labels = []
    for ev, d in zip(events["event"], events["date"]):
        label = "n/a"
        try:
            if ev in ("CPI", "PPI"):
                s = data["cpi_yoy" if ev == "CPI" else "ppi_yoy"]
                m = _month_before(d)
                cur, prev = s.get(m), s.get(m - pd.DateOffset(months=1))
                if pd.notna(cur) and pd.notna(prev):
                    label = "Hotter" if cur > prev else "Cooler"
            elif ev == "Payrolls (NFP)":
                s = data["payrolls_chg"]
                m = _month_before(d)
                if m in s.index:
                    i = s.index.get_loc(m)
                    if i >= 3:
                        label = "Strong" if s.iloc[i] > s.iloc[i - 3:i].mean() else "Weak"
            elif ev == "Jobless claims":
                s = data["claims"]
                past = s[s.index <= d - pd.Timedelta(days=5)]
                if len(past) >= 5:
                    label = "Rising" if past.iloc[-1] > past.iloc[-5:-1].mean() else "Falling"
            elif ev == "FOMC decision":
                s = data["fed_upper"]
                before = s[s.index < d]
                after = s[s.index <= d + pd.Timedelta(days=1)]
                if len(before) and len(after):
                    ch = after.iloc[-1] - before.iloc[-1]
                    label = "Hike" if ch > 0.001 else "Cut" if ch < -0.001 else "Hold"
        except (KeyError, IndexError):
            pass
        labels.append(label)
    return pd.Series(labels, index=events.index)


# --------------------------------------------------------------------------- #
# Reactions
# --------------------------------------------------------------------------- #

def prep(price: pd.Series) -> pd.Series:
    """Daily closes on a plain (timezone-free) date index, sorted, one per day."""
    p = price.dropna().sort_index()
    idx = p.index.tz_localize(None) if p.index.tz is not None else p.index
    p = pd.Series(p.values, index=idx.normalize())
    return p[~p.index.duplicated(keep="last")]


def reaction(p: pd.Series, date: pd.Timestamp, h: int, in_bp: bool = False) -> float:
    """Move from the last close before `date` to the last close on or before
    day h-1 (p must come from prep()). NaN if the window isn't complete yet."""
    end_day = date + pd.Timedelta(days=h - 1)
    if p.empty or p.index[-1] < end_day:
        return np.nan
    i_base = p.index.searchsorted(date) - 1             # last close strictly before day 0
    i_end = p.index.searchsorted(end_day, side="right") - 1
    if i_base < 0 or i_end <= i_base:
        return np.nan
    base, end = p.iloc[i_base], p.iloc[i_end]
    return (end - base) * 100 if in_bp else end / base - 1


def reaction_table(events: pd.DataFrame, prices: dict[str, pd.Series], horizons: list[int],
                   bp_assets: set[str]) -> pd.DataFrame:
    """One row per (event, asset, horizon) with the move. prices come from prep()."""
    rows = []
    for _, ev in events.iterrows():
        for asset, px in prices.items():
            for h in horizons:
                r = reaction(px, ev["date"], h, in_bp=asset in bp_assets)
                if pd.notna(r):
                    rows.append({"event": ev["event"], "date": ev["date"], "label": ev.get("label", "n/a"),
                                 "asset": asset, "h": h, "move": r})
    return pd.DataFrame(rows)


def typical_move(p: pd.Series, h: int, in_bp: bool = False, since: str = "2018-01-01") -> float:
    """Average h-day move over all days (p from prep()): the baseline each
    event's move is judged against."""
    d = p[p.index >= pd.Timestamp(since)].asfreq("D").ffill()
    move = (d.shift(-(h - 1)) - d.shift(1)) * 100 if in_bp else d.shift(-(h - 1)) / d.shift(1) - 1
    return float(move.mean())


def summarize(rt: pd.DataFrame, typical: dict[tuple[str, int], float], by_label: bool) -> pd.DataFrame:
    """Average move, hit rate and count per event (and label), with the
    excess over the asset's typical move for the same horizon."""
    keys = ["event", "label", "asset", "h"] if by_label else ["event", "asset", "h"]
    g = rt.groupby(keys)["move"]
    out = pd.DataFrame({"n": g.size(), "avg": g.mean(), "median": g.median(),
                        "pct_up": g.apply(lambda x: (x > 0).mean())}).reset_index()
    out["typical"] = [typical.get((a, h), np.nan) for a, h in zip(out["asset"], out["h"])]
    out["excess"] = out["avg"] - out["typical"]
    return out


def average_path(events: pd.DataFrame, price: pd.Series, days: int = 28, in_bp: bool = False) -> pd.DataFrame:
    """Average cumulative move on each day after the event (day 1..days):
    one line for all releases, plus one per label when releases are labelled.
    price comes from prep()."""
    def mean_path(dates) -> tuple[np.ndarray, int]:
        arr = np.array([[reaction(price, d, h, in_bp) for h in range(1, days + 1)] for d in dates], dtype=float)
        if not len(arr):
            return np.full(days, np.nan), 0
        return np.nanmean(arr, axis=0), int((~np.isnan(arr[:, 0])).sum())

    out = {}
    path, n = mean_path(events["date"])
    out[f"All releases (n={n})"] = path
    for label, grp in events[events["label"] != "n/a"].groupby("label"):
        path, n = mean_path(grp["date"])
        out[f"{label} (n={n})"] = path
    return pd.DataFrame(out, index=range(1, days + 1))
