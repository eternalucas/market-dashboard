"""Social Risk: a 0-1 score of how crowded and euphoric retail crypto
attention is, built from twelve inputs in two groups.

Attention (is the public paying attention?)          weight
  Wikipedia, English (11 crypto articles)              1.00
  Wikipedia, 12 other languages                        0.75
  Google Trends (7 search terms)                       1.00
  App Store ranks of crypto apps (US, Canada, UK)      1.00
  YouTube crypto channels: new views per day           0.75
  Hacker News mentions (weekly count)                  0.50
  News coverage, GDELT (share of all articles)         0.50
  X impressions (hand-logged)                          0.50

Speculation (is that attention turning into risk-taking?)
  Fear & Greed Index                                   0.50
  Perpetual funding rate                               1.00
  Altcoin Season Index                                 0.75
  Memecoin share of altcoin volume                     0.75

Social Risk = 65% Attention + 35% Speculation (whichever group has data
carries the score alone). Attention leads because that is what the score is
for; the speculation group is there because attention without risk-taking
(a crash makes headlines too) isn't froth. This mirrors how published
sentiment indices mix both kinds of input (alternative.me: volatility,
momentum, social media, dominance, search; LunarCrush: mentions,
engagement, social dominance).

Method:
1. Each raw series is turned into a SHARE where a denominator exists (an
   article's views / all views of that Wikipedia; crypto articles / all
   news articles; memecoin volume / altcoin volume), which removes platform
   growth or decline from the signal.
2. Each series is put on a 0-1 scale by a rolling two-year percentile:
   0.9 = higher than 90% of the past two years. Inputs that already have a
   fixed scale (Fear & Greed 0-100, Altcoin Season 0-100, app ranks) are
   rescaled directly instead.
3. Series of the same kind are averaged into a component; components are
   weighted into the two groups; groups into the score. The score needs at
   least three components on a day.

Weekly and monthly inputs are stamped at the END of their period, so a
backtest never uses a week's number before the week is over.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #

def rolling_percentile(s: pd.Series, window_days: int = 730, min_obs: int = 180) -> pd.Series:
    """Share of observations in the trailing window that are <= the latest one.

    Uses a time-based window, so it works for daily, weekly or irregular
    (hand-entered) data alike. Returns NaN until min_obs observations exist.
    """
    s = s.dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    if s.empty:
        return s

    # rank(method="max", pct=True) = share of the window <= the latest value
    return s.rolling(f"{window_days}D", min_periods=min_obs).rank(method="max", pct=True)


def to_daily(s: pd.Series, ffill_days: int) -> pd.Series:
    """Daily calendar index, carrying the last value forward up to ffill_days.

    Carrying values forward lets weekly and monthly data line up with daily
    data, but the limit stops a dead feed from looking like a live reading.
    """
    if s.dropna().empty:
        return pd.Series(dtype=float)
    s = s.dropna().sort_index()
    s.index = s.index.normalize()
    daily = s.groupby(level=0).last().asfreq("D")
    return daily.ffill(limit=ffill_days) if ffill_days > 0 else daily


def mean_of(series: list[pd.Series]) -> pd.Series:
    series = [x for x in series if not x.dropna().empty]
    if not series:
        return pd.Series(dtype=float)
    return pd.concat(series, axis=1, sort=True).mean(axis=1, skipna=True)


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #

def wikipedia_component(views: dict[str, pd.Series], window_days: int, min_obs: int) -> pd.Series:
    """Each article's 7-day average views, scored against its own history."""
    scored = []
    for s in views.values():
        smooth = s.sort_index().rolling("7D", min_periods=5).mean()  # removes weekday swings
        scored.append(to_daily(rolling_percentile(smooth, window_days, min_obs), ffill_days=3))
    return mean_of(scored)


def trends_component(weekly: dict[str, pd.Series], monthly: dict[str, pd.Series], window_days: int) -> pd.Series:
    """Weekly Google Trends where available (last 5 years), monthly before that.

    Each is scored against its own history first, because Google rescales
    every download so its own peak is 100; raw values from the two can't be
    mixed, but their percentile scores can.
    """
    scored = []
    for term in set(weekly) | set(monthly):
        w = weekly.get(term, pd.Series(dtype=float))
        m = monthly.get(term, pd.Series(dtype=float))
        w_score = to_daily(rolling_percentile(w, window_days, min_obs=52), ffill_days=10)
        m_score = to_daily(rolling_percentile(m, window_days, min_obs=12), ffill_days=35)
        scored.append(w_score.combine_first(m_score))
    return mean_of(scored)


def rank_to_score(rank: pd.Series, chart_size: int = 100) -> pd.Series:
    """App Store rank -> 0-1. Missing rank (not in the chart) -> 0."""
    score = 1 - (rank - 1) / chart_size
    return score.fillna(0.0).clip(0, 1)


def app_store_component(ranks: dict[str, pd.Series], chart_size: int = 100) -> pd.Series:
    return mean_of([to_daily(rank_to_score(r, chart_size), ffill_days=3) for r in ranks.values()])


def youtube_component(subs: dict[str, pd.Series], window_days: int, min_obs: int) -> pd.Series:
    """30-day subscriber growth per channel, scored against its own history.

    Subscriber counts only exist from the day the dashboard first ran, so
    this component stays empty until roughly 30 + min_obs days of history.
    """
    scored = []
    for s in subs.values():
        daily = to_daily(s, ffill_days=3)
        growth = daily.pct_change(30, fill_method=None)
        scored.append(to_daily(rolling_percentile(growth, window_days, min_obs), ffill_days=3))
    return mean_of(scored)


def x_component(impressions: dict[str, pd.Series], window_days: int) -> pd.Series:
    """Hand-logged X impressions. Entries are sparse, so 8 entries is enough
    to start scoring, and a reading stays valid for 14 days."""
    scored = [to_daily(rolling_percentile(s, window_days, min_obs=8), ffill_days=14) for s in impressions.values()]
    return mean_of(scored)


def wikipedia_share_component(views: dict[str, pd.Series], totals: dict[str, pd.Series],
                              window_days: int, min_obs: int) -> pd.Series:
    """Each article's share of all human views of its Wikipedia (7-day
    average), scored against its own history. Without a total for that
    project the raw views are used."""
    scored = []
    for key, s in views.items():
        total = totals.get(key)
        s = s.sort_index()
        if total is not None and not total.dropna().empty:
            t = total.sort_index()
            s = (s / t.reindex(s.index)).dropna()
        smooth = s.rolling("7D", min_periods=5).mean()
        scored.append(to_daily(rolling_percentile(smooth, window_days, min_obs), ffill_days=3))
    return mean_of(scored)


def end_of_period(s: pd.Series, freq: str) -> pd.Series:
    """Restamp weekly/monthly buckets at the end of their period (no lookahead)."""
    s = s.dropna().sort_index()
    if s.empty:
        return s
    if freq == "weekly":
        s.index = s.index + pd.Timedelta(days=7)
    else:
        s.index = s.index + pd.offsets.MonthBegin(1)
    return s


def trends_component_v2(weekly: dict[str, pd.Series], monthly: dict[str, pd.Series], window_days: int) -> pd.Series:
    """Google Trends per term, weekly where available and monthly before,
    each restamped at the end of its period and scored against itself."""
    scored = []
    for term in set(weekly) | set(monthly):
        w = end_of_period(weekly.get(term, pd.Series(dtype=float)), "weekly")
        m = end_of_period(monthly.get(term, pd.Series(dtype=float)), "monthly")
        w_score = to_daily(rolling_percentile(w, window_days, min_obs=52), ffill_days=10)
        m_score = to_daily(rolling_percentile(m, window_days, min_obs=12), ffill_days=35)
        scored.append(w_score.combine_first(m_score))
    return mean_of(scored)


def app_ladder(overall: pd.Series | None, finance: pd.Series | None) -> pd.Series:
    """One 0-1 ladder from two charts: Finance #200 -> ~0, Finance #1 -> 0.5,
    Overall #100 -> 0.5, Overall #1 -> 1.0. Not in either chart -> 0.

    A crypto app in the overall top 100 is a mania-level reading (Coinbase
    hit #1 overall at the 2017 and 2021 tops), so it sits above any
    Finance-chart rank."""
    ins = [x.dropna() for x in (overall, finance) if x is not None and not x.dropna().empty]
    if not ins:
        return pd.Series(dtype=float)
    idx = ins[0].index
    for x in ins[1:]:
        idx = idx.union(x.index)
    cand = []
    if finance is not None:
        f = finance.reindex(idx)
        cand.append(0.5 * (1 - (f.where(f <= 200) - 1) / 200))
    if overall is not None:
        o = overall.reindex(idx)
        cand.append(0.5 + 0.5 * (1 - (o.where(o <= 100) - 1) / 100))
    # every day in idx was observed in at least one chart, so "in neither" = 0
    return pd.concat(cand, axis=1, sort=True).max(axis=1).fillna(0.0)


def app_store_component_v2(overall: dict[str, pd.Series], finance: dict[str, pd.Series], top: int = 3) -> pd.Series:
    """Keys are '<country>:<app>'. Per country, the mean ladder score of the
    `top` best-placed tracked apps that day (so dozens of absent apps don't
    dilute the reading); then the mean across countries."""
    by_country: dict[str, list[pd.Series]] = {}
    for key in set(overall) | set(finance):
        cc = key.split(":", 1)[0]
        lad = app_ladder(overall.get(key), finance.get(key))
        if not lad.dropna().empty:
            by_country.setdefault(cc, []).append(to_daily(lad, ffill_days=3))
    per_country = []
    for series in by_country.values():
        df = pd.concat(series, axis=1, sort=True)
        top_n = df.apply(lambda r: r.dropna().nlargest(top).mean() if r.notna().any() else np.nan, axis=1)
        per_country.append(top_n)
    return mean_of(per_country)


def youtube_views_component(views: dict[str, pd.Series], subs: dict[str, pd.Series],
                            window_days: int, min_obs: int = 90) -> pd.Series:
    """New views per day (7-day average) per channel, scored against its own
    history; 30-day subscriber growth counts one third. Lifetime views are
    exact, so day-to-day changes are real (subscriber counts are rounded)."""
    scored = []
    for handle, v in views.items():
        d = to_daily(v, ffill_days=0)
        per_day = d.interpolate(limit_area="inside").diff()
        per_day = per_day.where(per_day >= 0)          # deleted videos can make it dip
        vel = per_day.rolling(7, min_periods=5).mean()
        v_score = to_daily(rolling_percentile(vel, window_days, min_obs), ffill_days=3)
        parts = [v_score, v_score]                     # views count twice
        if handle in subs:
            g = to_daily(subs[handle], ffill_days=3).pct_change(30, fill_method=None)
            parts.append(to_daily(rolling_percentile(g, window_days, min_obs), ffill_days=3))
        scored.append(mean_of(parts))
    return mean_of(scored)


def hacker_news_component(counts: dict[str, pd.Series], window_days: int) -> pd.Series:
    """counts keys: '<kind>:<term>'. Weekly stories (and comments) mentioning
    any tracked term, summed over terms, scored against their own two years;
    stories and comments averaged. Absolute counts: Hacker News publishes no
    reliable total to divide by, and the two-year percentile absorbs slow
    changes in the site's audience."""
    scored = []
    for kind in ("story", "comment"):
        terms = [s for k, s in counts.items() if k.startswith(f"{kind}:") and not k.endswith(":all")]
        if not terms:
            continue
        hits = pd.concat(terms, axis=1, sort=True).sum(axis=1, min_count=1).dropna()
        scored.append(to_daily(rolling_percentile(hits, window_days, min_obs=52), ffill_days=10))
    return mean_of(scored)


def news_component(count: pd.Series, total: pd.Series, window_days: int, min_obs: int = 60) -> pd.Series:
    """Share of all GDELT-monitored news articles that mention crypto (7-day)."""
    if count.dropna().empty or total.dropna().empty:
        return pd.Series(dtype=float)
    c, t = to_daily(count, 0).rolling(7, min_periods=5).sum(), to_daily(total, 0).rolling(7, min_periods=5).sum()
    share = (c / t.replace(0, np.nan)).dropna()
    return to_daily(rolling_percentile(share, window_days, min_obs), ffill_days=3)


def fixed_scale(s: pd.Series, lo: float = 0, hi: float = 100, ffill_days: int = 3) -> pd.Series:
    return to_daily(((s - lo) / (hi - lo)).clip(0, 1), ffill_days=ffill_days)


def funding_component(funding: pd.Series, window_days: int, min_obs: int) -> pd.Series:
    """7-day mean of the OI-weighted perpetual funding rate, scored against
    its own history: crowded longs paying up is the leverage side of froth."""
    smooth = to_daily(funding, 3).rolling(7, min_periods=5).mean()
    return to_daily(rolling_percentile(smooth, window_days, min_obs), ffill_days=3)


MEMECOINS = ("DOGE-USD", "SHIB-USD", "PEPE24478-USD")


def memecoin_component(volumes: dict[str, pd.Series], window_days: int, min_obs: int) -> pd.Series:
    """Memecoin (DOGE, SHIB, PEPE) share of the top-50 altcoin trading
    volume, 7-day sums, scored against its own history."""
    if not volumes:
        return pd.Series(dtype=float)
    df = pd.concat({k: to_daily(v, 0) for k, v in volumes.items()}, axis=1, sort=True)
    df = df.rolling(7, min_periods=5).sum()
    memes = df[[c for c in df.columns if c in MEMECOINS]]
    if memes.empty:
        return pd.Series(dtype=float)
    enough = df.notna().sum(axis=1) >= 20
    share = (memes.sum(axis=1, min_count=1) / df.sum(axis=1, min_count=1)).where(enough).dropna()
    return to_daily(rolling_percentile(share, window_days, min_obs), ffill_days=3)


# Components, labels, groups and weights -------------------------------------
COMPONENTS = {   # key: (label, group, weight)
    "wikipedia_en": ("Wikipedia (English)", "attention", 1.00),
    "wikipedia_intl": ("Wikipedia (12 other languages)", "attention", 0.75),
    "google_trends": ("Google Trends", "attention", 1.00),
    "app_store": ("App Store ranks (US, CA, UK)", "attention", 1.00),
    "youtube": ("YouTube views per day", "attention", 0.75),
    "hacker_news": ("Hacker News mentions", "attention", 0.50),
    "news": ("News coverage (GDELT)", "attention", 0.50),
    "x_impressions": ("X impressions (hand-logged)", "attention", 0.50),
    # Half weight: half of Fear & Greed is price momentum and volatility, which
    # trend-follow, and its social part overlaps the attention group.
    "fear_greed": ("Fear & Greed Index", "speculation", 0.50),
    "funding": ("Perp funding rate", "speculation", 1.00),
    "altseason": ("Altcoin Season Index", "speculation", 0.75),
    "memecoins": ("Memecoin volume share", "speculation", 0.75),
}
GROUP_WEIGHTS = {"attention": 0.65, "speculation": 0.35}
MIN_COMPONENTS = 3


def grouped_composite(components: dict[str, pd.Series], weights: dict[str, float] | None = None,
                      group_weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Weighted mean within each group, then across groups.

    Returns one column per component, 'attention', 'speculation', 'score'
    and 'n_components'. Days with fewer than MIN_COMPONENTS have no score."""
    weights = {k: v[2] for k, v in COMPONENTS.items()} | (weights or {})
    group_weights = GROUP_WEIGHTS | (group_weights or {})
    available = {k: v for k, v in components.items() if v is not None and not v.dropna().empty}
    if not available:
        return pd.DataFrame(columns=["attention", "speculation", "score", "n_components"])
    df = pd.concat(available, axis=1, sort=True).sort_index()
    df = df.ffill(limit=3)
    df = df[df.index <= pd.Timestamp.now(tz="UTC")]
    groups = {}
    for g in group_weights:
        cols = [c for c in df.columns if COMPONENTS.get(c, ("", "attention"))[1] == g]
        if not cols:
            continue
        w = pd.Series({c: weights.get(c, 1.0) for c in cols})
        present = df[cols].notna()
        groups[g] = (df[cols].fillna(0) * w).sum(axis=1) / (present * w).sum(axis=1).replace(0, np.nan)
    gdf = pd.DataFrame(groups).reindex(columns=list(group_weights))
    gw = pd.Series(group_weights)
    have = gdf.notna()
    score = (gdf.fillna(0) * gw).sum(axis=1) / (have * gw).sum(axis=1).replace(0, np.nan)
    n = df.notna().sum(axis=1)
    out = df.copy()
    out["attention"], out["speculation"] = gdf["attention"], gdf["speculation"]
    out["score"] = score.where(n >= MIN_COMPONENTS)
    out["n_components"] = n
    return out


def component_signal(df: pd.DataFrame, price: pd.Series, horizon: int = 90, hot: float = 0.85) -> pd.DataFrame:
    """For each component and the score: how its reading related to BTC's
    return over the next `horizon` days. Rank correlation < 0 means high
    readings were followed by weaker returns, which is what a risk gauge
    should show."""
    px = to_daily(price, ffill_days=3)
    fwd = (px.shift(-horizon) / px - 1).dropna()
    rows = []
    for c in [*COMPONENTS, "attention", "speculation", "score"]:
        if c not in df:
            continue
        x = df[c].dropna()
        both = pd.concat([x, fwd], axis=1, join="inner").dropna()
        if len(both) < 60:
            continue
        a, b = both.iloc[:, 0], both.iloc[:, 1]
        hot_days = b[a >= hot]
        rows.append({
            "Input": COMPONENTS.get(c, (c.title(),))[0] if c in COMPONENTS else {"score": "Social Risk", "attention": "Attention group", "speculation": "Speculation group"}[c],
            "Since": both.index[0].date(),
            "Days": len(both),
            "Rank corr. with next 90d": a.rank().corr(b.rank()),
            f"Days >= {hot}": len(hot_days),
            f"Next 90d after >= {hot}": hot_days.mean() if len(hot_days) else np.nan,
            "Next 90d, all days": b.mean(),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Composite
# --------------------------------------------------------------------------- #

def composite(components: dict[str, pd.Series], weights: dict[str, float]) -> pd.DataFrame:
    """Weighted mean of available components each day.

    Returns a DataFrame with one column per component, plus 'score' (the
    composite) and 'n_components' (how many fed into each day's score).
    """
    available = {k: v for k, v in components.items() if not v.dropna().empty}
    if not available:
        return pd.DataFrame(columns=["score", "n_components"])
    df = pd.concat(available, axis=1, sort=True)
    # Sources update on different days (Wikipedia lags a day, Trends is
    # weekly), so carry each component forward up to 3 days to line them up,
    # and drop dates in the future (weekly buckets can be labelled ahead).
    df = df.ffill(limit=3)
    df = df[df.index <= pd.Timestamp.now(tz="UTC")]
    w = pd.Series({c: weights.get(c, 1.0) for c in df.columns})
    present = df.notna()
    weighted_sum = (df.fillna(0) * w).sum(axis=1)
    weight_total = (present * w).sum(axis=1)
    df["score"] = weighted_sum / weight_total.replace(0, np.nan)
    df["n_components"] = present.sum(axis=1)
    return df


def band(score: float, bands: dict[str, float]) -> str:
    if score is None or np.isnan(score):
        return "n/a"
    if score >= bands["extreme"]:
        return "Extreme"
    if score >= bands["elevated"]:
        return "Elevated"
    if score <= bands["low"]:
        return "Low"
    return "Neutral"


# --------------------------------------------------------------------------- #
# Does it work? Checking the score against what price did next.
# --------------------------------------------------------------------------- #

def forward_returns_by_band(score: pd.Series, price: pd.Series, bands: dict[str, float],
                            horizons: tuple[int, ...] = (30, 90, 180)) -> pd.DataFrame:
    """Average BTC return over the next N days, grouped by the score band on
    the day the score was read.

    If the indicator does what it claims, returns after 'Extreme' readings
    should be worse than average. This table is the evidence (or not).
    """
    px = to_daily(price, ffill_days=3)
    sc = score.reindex(px.index)
    out = pd.DataFrame({"band": sc.map(lambda v: band(v, bands))})
    for h in horizons:
        out[f"next_{h}d"] = px.shift(-h) / px - 1
    out = out[out["band"] != "n/a"]
    order = ["Low", "Neutral", "Elevated", "Extreme"]
    table = out.groupby("band").agg(
        days=("band", "size"),
        **{f"avg_next_{h}d": (f"next_{h}d", "mean") for h in horizons},
    ).reindex([b for b in order if b in out["band"].unique()])
    all_row = pd.DataFrame(
        {"days": [len(out)], **{f"avg_next_{h}d": [out[f"next_{h}d"].mean()] for h in horizons}},
        index=["All days"],
    )
    return pd.concat([table, all_row])


def peak_lead_times(score: pd.Series, price: pd.Series, half_window_days: int = 180,
                    search_before_days: int = 120, search_after_days: int = 30) -> pd.DataFrame:
    """For each major price top, find when the score peaked nearby.

    A 'major top' is a day whose close is the highest within +/- half_window
    days. For each, the score's highest point in the window from
    search_before days before to search_after days after is found.
    lead_days > 0 means the score peaked before price did.
    """
    px = to_daily(price, ffill_days=3).dropna()
    sc = score.dropna()
    if px.empty or sc.empty:
        return pd.DataFrame()
    win = 2 * half_window_days + 1
    is_top = px == px.rolling(win, center=True, min_periods=half_window_days).max()
    tops = px[is_top]
    rows = []
    for t, p in tops.items():
        nearby = sc[t - pd.Timedelta(days=search_before_days): t + pd.Timedelta(days=search_after_days)]
        if nearby.empty:
            continue
        rows.append({
            "price_top": t.date(),
            "top_price": p,
            "score_peak": nearby.idxmax().date(),
            "score_at_peak": nearby.max(),
            "score_on_top_day": sc.get(t, np.nan),
            "lead_days": (t - nearby.idxmax()).days,
            # A top less than half_window days old could still be exceeded.
            "confirmed": t <= px.index[-1] - pd.Timedelta(days=half_window_days),
        })
    return pd.DataFrame(rows)
