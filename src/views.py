"""Turns stored data into the tables the dashboard shows.

Nothing here touches the network: it only reads the database and calls the
indicator functions. That separation makes it testable with a sample database.
"""
from __future__ import annotations

import pandas as pd

from . import storage
from .indicators import social_risk as sr

COMPONENT_LABELS = {k: v[0] for k, v in sr.COMPONENTS.items()}
# How many readings each component needs before it scores (for the status table).
NEEDED = {"wikipedia_en": 180, "wikipedia_intl": 180, "google_trends": 12, "app_store": 1, "youtube": 97,
          "hacker_news": 52, "news": 60, "x_impressions": 8, "fear_greed": 1, "funding": 186, "altseason": 1,
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
    return {
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
        "x_impressions": _strip(storage.load_prefix(con, "x_impr:"), "x_impr:"),
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
        "news": sr.news_component(raw["gdelt_count"], raw["gdelt_total"], win),
        "x_impressions": sr.x_component(raw["x_impressions"], win),
        "fear_greed": sr.fixed_scale(raw["fng"]),
        "funding": sr.funding_component(raw["funding"], win, min_obs) if not raw["funding"].empty else pd.Series(dtype=float),
        "altseason": sr.fixed_scale(raw["altseason"]),
        "memecoins": sr.memecoin_component(raw["alt_volumes"], win, min_obs),
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
        "news": {"gdelt": raw["gdelt_count"]} if not raw["gdelt_count"].empty else {},
        "x_impressions": raw["x_impressions"], "fear_greed": {"fng": raw["fng"]} if not raw["fng"].empty else {},
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


def source_health(con) -> pd.DataFrame:
    """Latest status of each data source: when it last worked, last error."""
    log = storage.fetch_log(con, limit=5000)
    if log.empty:
        return log
    log["ts"] = pd.to_datetime(log["ts"], utc=True, format="ISO8601")
    rows = []
    for source, g in log.groupby("source"):
        g = g.sort_values("ts")
        last = g.iloc[-1]
        ok = g[g["ok"] == 1]
        rows.append({
            "Source": source,
            "Status": "OK" if last["ok"] else "Failing",
            "Last attempt": last["ts"].strftime("%Y-%m-%d %H:%M UTC"),
            "Last success": ok["ts"].iloc[-1].strftime("%Y-%m-%d %H:%M UTC") if not ok.empty else "never",
            "Rows (last)": int(last["rows"] or 0),
            "Last error": "" if last["ok"] else last["message"],
        })
    out = pd.DataFrame(rows)
    return out.sort_values(["Status", "Source"], ascending=[True, True]).reset_index(drop=True)


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
