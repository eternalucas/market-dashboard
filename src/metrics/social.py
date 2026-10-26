"""Social Risk for the Studio charts. The score itself is built in
src/indicators/social_risk.py (method and weights documented there); these
functions slice it into chartable Series/DataFrames.

The frame is cached in views.social_risk_frame until new data is fetched, so
the dozen charts on the Social Risk dashboard share one computation.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from ..indicators import social_risk as sr
from .core import Ctx


@lru_cache(maxsize=1)
def _cfg() -> dict:
    from ..config import load_config
    return load_config()


def frame(c: Ctx) -> tuple[pd.DataFrame, dict]:
    from .. import views
    return views.social_risk_frame(c.con, _cfg())


def bands() -> dict:
    return (_cfg().get("social_risk") or {}).get("bands") or {"low": 0.30, "elevated": 0.70, "extreme": 0.85}


def _col(c: Ctx, name: str) -> pd.Series:
    df, _ = frame(c)
    return df[name].dropna() if name in df else pd.Series(dtype=float)


def score(c: Ctx) -> pd.Series:
    """Social Risk [0-1] = 65% Attention + 35% Speculation."""
    return _col(c, "score").rename("Social Risk")


def groups(c: Ctx) -> pd.DataFrame:
    df, _ = frame(c)
    out = df.reindex(columns=["score", "attention", "speculation"]).rename(
        columns={"score": "Social Risk", "attention": "Attention", "speculation": "Speculation"})
    return out.dropna(how="all")


def contributions(c: Ctx) -> pd.DataFrame:
    """Each component's share of the score: value x its weight within its
    group / that day's group weight total x the group's weight / that day's
    total group weight. The stacked areas add up to the score."""
    df, _ = frame(c)
    if df.empty or "score" not in df:
        return pd.DataFrame()
    df = df[df["score"].notna()]
    comps = [k for k in sr.COMPONENTS if k in df]
    w = {k: sr.COMPONENTS[k][2] for k in comps} | ((_cfg().get("social_risk") or {}).get("component_weights") or {})
    out = {}
    group_total = pd.Series(0.0, index=df.index)
    gw = sr.GROUP_WEIGHTS
    per_group = {}
    for g in gw:
        cols = [k for k in comps if sr.COMPONENTS[k][1] == g]
        if not cols:
            continue
        present = df[cols].notna()
        wsum = (present * pd.Series({k: w[k] for k in cols})).sum(axis=1)
        per_group[g] = (cols, wsum)
        group_total = group_total + (wsum > 0) * gw[g]
    for g, (cols, wsum) in per_group.items():
        for k in cols:
            out[sr.COMPONENTS[k][0]] = df[k] * w[k] / wsum.replace(0, np.nan) * gw[g] / group_total.replace(0, np.nan)
    return pd.DataFrame(out)


def component(key: str):
    def f(c: Ctx) -> pd.Series:
        return _col(c, key).rename(sr.COMPONENTS[key][0])
    f.__name__ = f"component_{key}"
    return f


def coverage(c: Ctx) -> pd.Series:
    """How many of the 12 components fed each day's score."""
    return _col(c, "n_components").rename("Components")


# Raw inputs -------------------------------------------------------------------

def wiki_views(c: Ctx, top: int = 6) -> pd.DataFrame:
    """English Wikipedia human pageviews, 7-day average, top articles."""
    _, raw = frame(c)
    df = pd.DataFrame({k.replace("_", " "): v for k, v in raw["wiki_en"].items()}).sort_index()
    if df.empty:
        return df
    df = df.rolling(7, min_periods=5).mean()
    order = df.iloc[-90:].mean().sort_values(ascending=False).index[:top]
    return df[order]


def wiki_share(c: Ctx) -> pd.DataFrame:
    """Share of all human Wikipedia views going to the Bitcoin article, English
    vs all other tracked languages (7-day average)."""
    _, raw = frame(c)
    out = {}
    for label, views, totals in (("English", raw["wiki_en"], raw["wiki_totals_en"]),
                                 ("Other languages", raw["wiki_intl"], raw["wiki_totals_intl"])):
        keys = [k for k in views if k.split(":")[-1] in ("Bitcoin", "ビットコイン", "비트코인", "比特币", "Биткоин")]
        if not keys or not totals:
            continue
        v = pd.concat([views[k] for k in keys], axis=1, sort=True).sum(axis=1, min_count=1)
        t = pd.concat([totals[k] for k in keys if k in totals], axis=1, sort=True).sum(axis=1, min_count=1)
        out[label] = (v / t).rolling(7, min_periods=5).mean() * 100
    return pd.DataFrame(out).dropna(how="all")


def trends(c: Ctx) -> pd.DataFrame:
    """Google Trends weekly interest per term (0-100, each term scaled to its own 5-year peak)."""
    _, raw = frame(c)
    return pd.DataFrame(raw["trends_weekly"]).sort_index()


def app_ranks(c: Ctx) -> pd.DataFrame:
    """Best overall-chart rank and best Finance-chart rank of any tracked
    crypto app, per country (rank 1 = top; absent = chart length + 1)."""
    _, raw = frame(c)
    out = {}
    for chart, d in (("Overall", raw["app_overall"]), ("Finance", raw["app_finance"])):
        by_cc: dict[str, list] = {}
        for k, s in d.items():
            by_cc.setdefault(k.split(":")[0].upper(), []).append(s)
        for cc, lst in by_cc.items():
            out[f"{cc} {chart}"] = pd.concat(lst, axis=1, sort=True).min(axis=1)
    return pd.DataFrame(out).sort_index()


def coinbase_rank(c: Ctx) -> pd.DataFrame:
    """Coinbase's rank in each country's Finance and overall charts."""
    _, raw = frame(c)
    out = {}
    for chart, d in (("Finance", raw["app_finance"]), ("Overall", raw["app_overall"])):
        for k, s in d.items():
            cc, app = k.split(":", 1)
            if app.lower() == "coinbase":
                out[f"{cc.upper()} {chart}"] = s
    return pd.DataFrame(out).sort_index()


def youtube_views(c: Ctx) -> pd.Series:
    """New views per day across the tracked crypto YouTube channels (7-day average)."""
    _, raw = frame(c)
    per = []
    for v in raw["yt_views"].values():
        d = sr.to_daily(v, 0).interpolate(limit_area="inside").diff()
        per.append(d.where(d >= 0))
    if not per:
        return pd.Series(dtype=float)
    return pd.concat(per, axis=1, sort=True).sum(axis=1, min_count=1).rolling(7, min_periods=5).mean().dropna()


def hacker_news(c: Ctx) -> pd.DataFrame:
    """Hacker News stories and comments per week mentioning bitcoin, ethereum
    or cryptocurrency."""
    _, raw = frame(c)
    out = {}
    for kind, label in (("story", "Stories"), ("comment", "Comments")):
        terms = [s for k, s in raw["hn"].items() if k.startswith(f"{kind}:") and not k.endswith(":all")]
        if terms:
            out[label] = pd.concat(terms, axis=1, sort=True).sum(axis=1, min_count=1)
    return pd.DataFrame(out).sort_index()


def news_share(c: Ctx) -> pd.Series:
    """Share of all online news articles GDELT monitored that mention crypto [%], 7-day."""
    _, raw = frame(c)
    cnt, tot = raw["gdelt_count"], raw["gdelt_total"]
    if cnt.empty or tot.empty:
        return pd.Series(dtype=float)
    cnt, tot = sr.to_daily(cnt, 0).rolling(7, min_periods=5).sum(), sr.to_daily(tot, 0).rolling(7, min_periods=5).sum()
    return (cnt / tot.replace(0, np.nan) * 100).dropna()


def memecoin_share(c: Ctx) -> pd.Series:
    """DOGE + SHIB + PEPE share of the top-50 altcoin trading volume [%], 7-day."""
    _, raw = frame(c)
    vols = raw["alt_volumes"]
    if not vols:
        return pd.Series(dtype=float)
    df = pd.concat({k: sr.to_daily(v, 0) for k, v in vols.items()}, axis=1, sort=True).rolling(7, min_periods=5).sum()
    memes = df[[k for k in df.columns if k in sr.MEMECOINS]]
    enough = df.notna().sum(axis=1) >= 20
    return (memes.sum(axis=1, min_count=1) / df.sum(axis=1, min_count=1) * 100).where(enough).dropna()
