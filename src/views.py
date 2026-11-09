"""Turns stored data into the tables the dashboard shows.

Nothing here touches the network: it only reads the database and calls the
indicator functions. That separation makes it testable with a sample database.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import storage
from .indicators import social_risk as sr

COMPONENT_LABELS = {k: v[0] for k, v in sr.COMPONENTS.items()}
# How many readings each component needs before it scores (for the status table).
NEEDED = {"wikipedia_en": 180, "wikipedia_intl": 180, "google_trends": 12, "app_store": 1, "youtube": 37,
          "hacker_news": 52, "fear_greed": 1, "funding": 186, "altseason": 1, "leverage": 186, "taker_buys": 186,
          "memecoins": 186}
_FRAME_CACHE: dict = {}


def _strip(d: dict[str, pd.Series], prefix: str) -> dict[str, pd.Series]:
    return {k[len(prefix):]: v for k, v in d.items()}


def _is_crypto_app(name: str) -> bool:
    from .sources.social import CRYPTO_APPS
    return any(name.lower().startswith(a.rstrip(":").lower()) for a in CRYPTO_APPS)


def _apps(con, chart: str, default_cc: str = "ca") -> dict[str, pd.Series]:
    """'<cc>:<app>' -> rank. Readings stored before countries were added
    ('appstore_overall:Coinbase') were Canadian and are merged into ca."""
    out: dict[str, pd.Series] = {}
    for key, s in _strip(storage.load_prefix(con, f"appstore_{chart}:"), f"appstore_{chart}:").items():
        if ":" not in key:
            key = f"{default_cc}:{key}"
        if not _is_crypto_app(key.split(":", 1)[1]):
            continue
        out[key] = s.combine_first(out[key]) if key in out else s
    return out


def social_inputs(con) -> dict:
    """Every raw input of the Social Risk score, read from the database."""
    from .metrics import Ctx
    from .metrics import futures as fu
    from .metrics import onchain as oc
    from .sources.social import WIKI_INTL
    from .sources.yahoo import GROUPS

    c = Ctx(con)
    wiki = _strip(storage.load_prefix(con, "wiki:"), "wiki:")
    totals = _strip(storage.load_prefix(con, "wikitotal:"), "wikitotal:")
    en = {k: v for k, v in wiki.items() if k.split(":", 1)[0] not in WIKI_INTL}
    intl = {k: v for k, v in wiki.items() if k.split(":", 1)[0] in WIKI_INTL}
    try:
        funding = fu.funding_rate(c)
    except Exception:  # noqa: BLE001 - Coinalyze not loaded yet
        funding = pd.Series(dtype=float)
    try:
        alt = oc.altcoin_season_index(c)
    except Exception:  # noqa: BLE001
        alt = pd.Series(dtype=float)
    try:
        perp_oi, perp_vol = fu.open_interest(c), fu.volume(c)
    except Exception:  # noqa: BLE001 - Coinalyze not loaded yet
        perp_oi, perp_vol = pd.Series(dtype=float), pd.DataFrame()
    return {
        "perp_oi": perp_oi, "perp_vol": perp_vol, "mcap": c.mcap(),
        "wiki_en": en, "wiki_intl": intl,
        "wiki_totals_en": {k: totals.get("en.wikipedia") for k in en if totals.get("en.wikipedia") is not None},
        "wiki_totals_intl": {k: totals.get(f"{k.split(':', 1)[0]}.wikipedia") for k in intl
                             if totals.get(f"{k.split(':', 1)[0]}.wikipedia") is not None},
        "trends_weekly": _strip(storage.load_prefix(con, "gtrends_weekly:"), "gtrends_weekly:"),
        "trends_monthly": _strip(storage.load_prefix(con, "gtrends_monthly:"), "gtrends_monthly:"),
        "app_overall": _apps(con, "overall"), "app_finance": _apps(con, "finance"),
        "yt_views": _strip(storage.load_prefix(con, "yt_views:"), "yt_views:"),
        "yt_subs": _strip(storage.load_prefix(con, "yt_subs:"), "yt_subs:"),
        "hn": _strip(storage.load_prefix(con, "hn:"), "hn:"),
        "gdelt_count": storage.load_series(con, "gdelt:count"),
        "gdelt_total": storage.load_series(con, "gdelt:total"),
        "fng": storage.load_series(con, "fng"),
        "funding": funding,
        "altseason": alt,
        "alt_volumes": {t: c.s(f"yf:{t}:volume") for t in GROUPS["alt"] if not c.s(f"yf:{t}:volume").empty},
        "price": c.price(),
    }


def social_risk_frame(con, cfg) -> tuple[pd.DataFrame, dict]:
    """Composite score, both groups and every component, plus the raw inputs.
    Cached until new data is fetched (the score takes a few seconds)."""
    row = con.execute("SELECT MAX(ts) FROM fetch_log WHERE ok = 1").fetchone()
    key = ((row[0] or "")[:15] if row else None, str((cfg.get("social_risk") or {}).get("component_weights")))
    if key in _FRAME_CACHE:
        return _FRAME_CACHE[key]
    scfg = cfg.get("social_risk") or {}
    win, min_obs = scfg.get("lookback_days", 730), scfg.get("min_history_days", 180)
    raw = social_inputs(con)
    components = {
        "wikipedia_en": sr.wikipedia_share_component(raw["wiki_en"], raw["wiki_totals_en"], win, min_obs),
        "wikipedia_intl": sr.wikipedia_share_component(raw["wiki_intl"], raw["wiki_totals_intl"], win, min_obs),
        "google_trends": sr.trends_component_v2(raw["trends_weekly"], raw["trends_monthly"], win),
        "app_store": sr.app_store_component_v2(raw["app_overall"], raw["app_finance"]),
        "youtube": sr.youtube_views_component(raw["yt_views"], raw["yt_subs"], win),
        "hacker_news": sr.hacker_news_component(raw["hn"], win),
        "fear_greed": sr.fixed_scale(raw["fng"]),
        "funding": sr.funding_component(raw["funding"], win, min_obs) if not raw["funding"].empty else pd.Series(dtype=float),
        "altseason": sr.fixed_scale(raw["altseason"]),
        "memecoins": sr.memecoin_component(raw["alt_volumes"], win, min_obs),
        "leverage": sr.leverage_component(raw["perp_oi"], raw["mcap"], win, min_obs),
        "taker_buys": (sr.taker_buy_component(raw["perp_vol"]["buy"], raw["perp_vol"]["total"], win, min_obs)
                       if not raw["perp_vol"].empty else pd.Series(dtype=float)),
    }
    df = sr.grouped_composite(components, scfg.get("component_weights"), scfg.get("group_weights"))
    if "score" in df and df["score"].notna().any():
        df = df[df.index >= df["score"].first_valid_index()]
    _FRAME_CACHE.clear()
    _FRAME_CACHE[key] = (df, raw)
    return df, raw


def component_status(df: pd.DataFrame, raw: dict, cfg) -> pd.DataFrame:
    """One row per component: latest score, weight, and if it has no score yet, why."""
    counts = {
        "wikipedia_en": raw["wiki_en"], "wikipedia_intl": raw["wiki_intl"],
        "google_trends": {**raw["trends_weekly"], **raw["trends_monthly"]},
        "app_store": {**raw["app_overall"], **raw["app_finance"]},
        "youtube": raw["yt_views"], "hacker_news": raw["hn"],
        "leverage": {"oi": raw["perp_oi"]} if not raw["perp_oi"].empty else {},
        "taker_buys": {"vol": raw["perp_vol"]["total"]} if not raw["perp_vol"].empty else {},
        "fear_greed": {"fng": raw["fng"]} if not raw["fng"].empty else {},
        "funding": {"funding": raw["funding"]} if not raw["funding"].empty else {},
        "altseason": {"alt": raw["altseason"]} if not raw["altseason"].empty else {},
        "memecoins": raw["alt_volumes"],
    }
    weights = (cfg.get("social_risk") or {}).get("component_weights") or {}
    rows = []
    for key, (label, group, w) in sr.COMPONENTS.items():
        have = max((len(s.dropna()) for s in counts[key].values()), default=0)
        col = df[key].dropna() if key in df else pd.Series(dtype=float)
        if not col.empty and col.index[-1] >= df.index[-1] - pd.Timedelta(days=14):
            status, latest, as_of = "Live", col.iloc[-1], col.index[-1].date()
        elif not col.empty:
            status, latest, as_of = "Stale (no new data for 2+ weeks)", col.iloc[-1], col.index[-1].date()
        elif have == 0:
            status, latest, as_of = "No data yet", None, None
        else:
            status, latest, as_of = f"Collecting history ({have}/{NEEDED[key]} readings)", None, None
        rows.append({"Component": label, "Group": group.title(), "Weight": weights.get(key, w),
                     "Score": latest, "As of": as_of, "Status": status, "Series": len(counts[key])})
    return pd.DataFrame(rows)


# Sources that were removed from the dashboard: their old log rows are not shown.
RETIRED_SOURCES = {"src:tokenized_stocks", "rec:trade_leaderboard:lighter", "rec:trade_leaderboard:gmx",
                   "rec:lighter_sample",
                   # renamed: now wikipedia:<project>:<article> and app_store:<country>:<chart>
                   "wikipedia:Bitcoin", "wikipedia:Cryptocurrency", "wikipedia:Dogecoin", "wikipedia:Ethereum",
                   "app_store:finance", "app_store:overall",
                   "fred_release:FOMC decision",              # FOMC dates come from the Fed's calendar now
                   "bgeo:delta-cap",                          # computed from Coin Metrics instead
                   "gdelt", "social:partial",                 # GDELT stopped publishing (27 Oct 2026)
                   "rec:lightning", "mempool_hist:lightning",  # Lightning retired (3 Nov 2026)
                   "onchain:btc:967"}                          # wallet relabelled "Whale (12ib7d…)"
# Fetched only when a page asks for them (Flows bar sizes other than the hourly
# one the recorder keeps): healthy unless the last fetch failed.
ON_DEMAND = ("coinalyze:1min", "coinalyze:5min", "coinalyze:15min", "coinalyze:30min", "coinalyze:4hour",
             "coinalyze:daily")
STALE_AFTER = pd.Timedelta(hours=48)     # default: no successful fetch for this long = stale

_H, _D = pd.Timedelta(hours=1), pd.Timedelta(days=1)
# How often each source is expected to succeed (prefix, first match wins), from
# the recorder's schedule (src/recorder.py) and each input's own due window.
# A source is Stale when its last success is older than stale_after(cadence).
CADENCE = [
    ("rec:option_chain:ibit", 3 * _D),            # weekdays after the US close: Friday -> Monday
    ("rec:lightning", _D), ("rec:liq_calibrate", _D), ("rec:", _H),
    ("google_trends:weekly", 6 * _D), ("google_trends:monthly", 25 * _D), ("bgeo:", 7 * _D),
    ("hacker_news", _H), ("src:social", _H), ("src:coinbase_hourly", _H), ("src:coinmetrics", 6 * _H),
    ("src:cftc", _D), ("src:", 12 * _H),
    ("perp:", _H), ("book:", _H), ("options:", _H), ("funding_hist:", _H), ("oi_hist:", _H),
    ("hyperliquid_cohorts", _H), ("coinalyze:1hour", _H),
]
# Sources whose successful runs legitimately store nothing (no new
# transactions, no inputs due this hour); all others are Stale after
# ZERO_ROWS_RUNS successes in a row with 0 rows.
ZERO_ROWS_OK = ("onchain", "src:social", "trade_review", "rec:liq_feeds")
ZERO_ROWS_RUNS = 3
MARKER_SUFFIXES = (":partial", ":failed")   # ok=0 notes beside a source that did run (e.g. blockchain_com:partial)


def _span(t: pd.Timedelta) -> str:
    h = t.total_seconds() / 3600
    return f"{h / 24:.0f} d" if h >= 48 else f"{h:.0f} h"


def cadence(source: str) -> pd.Timedelta:
    return next((c for p, c in CADENCE if source.startswith(p)), _D)


def stale_after(source: str) -> pd.Timedelta:
    """Allowed age of the last success: two missed runs, and at least two
    hours of slack for hourly jobs (successes are logged at most hourly)."""
    c = cadence(source)
    return max(2 * c, c + 2 * _H)

SOURCE_GROUPS = [   # (prefix, group shown on Data health), first match wins
    ("rec:", "Recorder (live)"), ("onchain", "Watched wallets"), ("wikipedia:", "Social: Wikipedia"),
    ("wikitotal:", "Social: Wikipedia"), ("youtube:", "Social: YouTube"), ("app_store:", "Social: App Store"),
    ("google_trends", "Social: Google Trends"), ("gdelt", "Social: news and forums"),
    ("hacker_news", "Social: news and forums"), ("src:social", "Social: news and forums"),
    ("bgeo:", "BGeometrics (on-chain)"), ("fred", "FRED (macro)"), ("src:fred", "FRED (macro)"),
    ("central_bank_calendar", "FRED (macro)"), ("coinalyze", "Coinalyze (derivatives)"),
    ("src:coinalyze", "Coinalyze (derivatives)"), ("perp:", "Exchanges: perps snapshot"),
    ("book:", "Exchanges: order books"), ("options:", "Exchanges: options"),
    ("funding_hist:", "Exchanges: funding and OI history"), ("oi_hist:", "Exchanges: funding and OI history"),
    ("price:", "Prices"), ("event_price:", "Prices"),
    ("trade_review", "Trade review"), ("hyperliquid_cohorts", "Hyperliquid cohorts"),
    ("src:", "Market data sources"),
]


def source_group(source: str) -> str:
    return next((g for p, g in SOURCE_GROUPS if source.startswith(p)), source.split(":")[0])


def ago(delta) -> str:
    """'just now', '42 min ago', '5 h ago', '3 d ago' (for a pd.Timedelta)."""
    if delta is None or pd.isna(delta):
        return "never"
    m = delta.total_seconds() / 60
    if m < 2:
        return "just now"
    if m < 90:
        return f"{m:.0f} min ago"
    if m < 48 * 60:
        return f"{m / 60:.0f} h ago"
    return f"{m / 1440:.0f} d ago"


def source_health(con, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per data source over the whole fetch log: status, when it last
    worked and the last error. Status:
      Failing = the last attempt failed;
      Stale   = the last success is older than the source's expected cadence
                (stale_after), or its last ZERO_ROWS_RUNS successes stored 0 rows;
      OK otherwise.
    ':partial' / ':failed' notes are shown only while recent."""
    q = """
        SELECT f.source, f.ts AS last_attempt, f.ok AS last_ok, f.rows AS last_rows, f.message AS last_message,
               s.last_success
        FROM fetch_log f
        JOIN (SELECT source, MAX(ts) AS mts, MAX(CASE WHEN ok = 1 THEN ts END) AS last_success
              FROM fetch_log GROUP BY source) s ON s.source = f.source AND s.mts = f.ts
        GROUP BY f.source"""
    df = pd.read_sql_query(q, con)
    df = df[~df["source"].isin(RETIRED_SOURCES)]
    if df.empty:
        return pd.DataFrame()
    zero = pd.read_sql_query(
        "SELECT source, MAX(rows) AS mx, COUNT(*) AS n FROM (SELECT source, rows, ROW_NUMBER() OVER "
        "(PARTITION BY source ORDER BY ts DESC) AS k FROM fetch_log WHERE ok = 1) WHERE k <= ? GROUP BY source",
        con, params=(ZERO_ROWS_RUNS,))
    empty_runs = set(zero.loc[(zero["n"] >= ZERO_ROWS_RUNS) & (zero["mx"].fillna(0) == 0), "source"])
    now = now or pd.Timestamp.now(tz="UTC")
    for c in ("last_attempt", "last_success"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    limit = df["source"].map(stale_after)
    marker = df["source"].str.endswith(MARKER_SUFFIXES)
    df = df[~marker | ((now - df["last_attempt"]) <= limit)]
    # Spot books are tried venue by venue (Coinbase, then Kraken, then Bybit) and the first that
    # answers wins, so a fallback venue goes quiet once the first one works again: drop it while
    # another venue for the same asset has succeeded since it was last tried.
    book = df["source"].str.match(r"^book:[^:]+:[^:]+$")
    if book.any():
        asset = df.loc[book, "source"].str.split(":").str[2]
        newest = df.loc[book].groupby(asset)["last_success"].transform("max")
        quiet_fallback = df.loc[book, "last_attempt"] < newest
        df = df.drop(quiet_fallback[quiet_fallback].index)
    limit = limit[df.index]
    failing = df["last_ok"] != 1
    too_old = df["last_success"].isna() | ((now - df["last_success"]) > limit)
    no_rows = df["source"].isin(empty_runs) & ~df["source"].str.startswith(ZERO_ROWS_OK)
    stale = (too_old | no_rows) & ~df["source"].str.startswith(ON_DEMAND)
    note = np.where(no_rows, f"last {ZERO_ROWS_RUNS} runs stored 0 rows",
                    "no success for over " + limit.map(_span))
    out = pd.DataFrame({
        "Status": np.where(failing, "Failing", np.where(stale, "Stale", "OK")),
        "Group": df["source"].map(source_group),
        "Source": df["source"],
        "Last success": df["last_success"],
        "Updated": (now - df["last_success"]).map(ago),
        "Last attempt": df["last_attempt"],
        "Rows (last)": df["last_rows"].fillna(0).astype(int),
        "Expected": df["source"].map(lambda x: "every " + _span(cadence(x))),
        "Last error": np.where(failing, df["last_message"].fillna(""), np.where(stale, note, "")),
    })
    order = {"Failing": 0, "Stale": 1, "OK": 2}
    return out.sort_values(["Status", "Group", "Source"], key=lambda c: c.map(order) if c.name == "Status" else c) \
              .reset_index(drop=True)


# Series behind the headline panels: when any is stale the dashboard warns at the top.
KEY_SOURCES = ("price:", "perp:", "src:coinmetrics", "src:yahoo", "funding_hist:")


def staleness_banner(h: pd.DataFrame, assets: list[str]) -> str | None:
    """Warning text for the top of the dashboard, or None when key data is
    current: names the stale/failing key sources for the first asset, and
    the overall share of sources that are not OK."""
    if h is None or h.empty:
        return None
    a0 = assets[0] if assets else "BTC"
    key = h[h["Source"].map(lambda x: x.startswith(KEY_SOURCES) and (":" not in x.removeprefix("src:")
                                                                         or x.endswith(f":{a0}")))]
    bad_key = key[key["Status"] != "OK"]
    bad_key = bad_key.iloc[bad_key["Source"].map(lambda x: next(i for i, p in enumerate(KEY_SOURCES)
                                                                if x.startswith(p))).argsort(kind="stable")]
    share = float((h["Status"] != "OK").mean())
    if bad_key.empty and share < 0.25:
        return None
    parts = [f"{r.Source} (updated {r.Updated})" for r in bad_key.itertuples()][:4]
    return ((f"Some key data is old or failing: {', '.join(parts)}. " if parts else "")
            + f"{share:.0%} of sources are stale or failing (see Data health).")


# --------------------------------------------------------------------------- #
# Macro events
# --------------------------------------------------------------------------- #

def event_inputs(con, cfg):
    """Events (with labels), prepared prices, and which assets move in bp."""
    from . import events as ev

    events = storage.load_events(con)
    if events.empty:
        return events, {}, set()
    data = {k: _naive(storage.load_series(con, f"fred:{k}"))
            for k in ("cpi_yoy", "ppi_yoy", "payrolls_chg", "claims", "fed_upper")}
    events["label"] = ev.classify(events, data)
    prices = {name: ev.prep(storage.load_series(con, f"evpx:{name}")) for name in cfg["events"]["assets"]}
    prices = {k: v for k, v in prices.items() if not v.empty}
    return events, prices, set(cfg["events"]["bp_assets"])


def _naive(s: pd.Series) -> pd.Series:
    if s.empty:
        return s
    s = s.copy()
    s.index = s.index.tz_localize(None).normalize()
    return s


# --------------------------------------------------------------------------- #
# Trade review and on-chain files
# --------------------------------------------------------------------------- #

def trade_review_inputs():
    import json

    from .pipeline import HL_DIR
    sel_path = HL_DIR / "selection.json"
    if not sel_path.exists():
        return None
    sel = json.loads(sel_path.read_text())
    addr = sel["address"]
    fills_path, pos_path = HL_DIR / f"{addr}_fills.csv", HL_DIR / f"{addr}_positions.csv"
    fills = read_fills(fills_path) if fills_path.exists() else pd.DataFrame()
    positions = pd.read_csv(pos_path) if pos_path.exists() and pos_path.stat().st_size > 2 else pd.DataFrame()
    return {"selection": sel, "fills": fills, "positions": positions}


def read_fills(path) -> pd.DataFrame:
    """Saved fills with their timestamps parsed. format='ISO8601' accepts both
    '...:54.933000+00:00' and '...:54+00:00', which appear in the same file."""
    f = pd.read_csv(path)
    f["time"] = pd.to_datetime(f["time"], utc=True, format="ISO8601")
    for c in ("px", "sz", "startPosition", "closedPnl", "fee"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f["crossed"] = f["crossed"].astype(str).str.lower().eq("true")
    return f


def onchain_file(name: str) -> pd.DataFrame:
    from .pipeline import ONCHAIN_DIR
    p = ONCHAIN_DIR / name
    return pd.read_csv(p) if p.exists() and p.stat().st_size > 2 else pd.DataFrame()
