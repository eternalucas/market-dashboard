"""Social Risk inputs: retail attention from the public web.

Every input here is free and needs no account (YouTube needs a free API key).
The recorder runs these on its hourly loop; each one only does work when its
data is due, so most runs return straight away.

Raw series stored (all daily UTC unless noted):
  wiki:<Article>               English Wikipedia human pageviews (since Jul 2015)
  wiki:<lang>:<Article>        Same, other-language Wikipedias
  wikitotal:<project>          All human pageviews of that Wikipedia (the denominator)
  gtrends_weekly:<term>        Google Trends, weekly, last 5 years (0-100 per download)
  gtrends_monthly:<term>       Google Trends, monthly, since 2004
  appstore_overall:<cc>:<app>  Apple top-free overall rank, country cc (top 100)
  appstore_finance:<cc>:<app>  Apple top-free Finance rank, country cc (top 200)
  yt_subs:<handle>             YouTube subscriber count (rounded by YouTube)
  yt_views:<handle>            YouTube channel lifetime views (exact; daily diff = new views)
  hn:<kind>:<term>             Hacker News items mentioning term, per week (kind: story|comment).
                               No denominator: Algolia's count for an empty query is an
                               estimate (and spam floods it since 2025), so it can't be used.
  gdelt:count / gdelt:total    News articles mentioning crypto / all articles GDELT monitored that day

Weekly Hacker News buckets are stamped at the END of the week (the Monday
after), so a backtest never sees a week's count before the week is over.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .. import storage
from ..collectors import attention
from ..collectors.http import get_json

# --------------------------------------------------------------------------- #
# What is tracked. config.yaml lists are added on top of these.
# --------------------------------------------------------------------------- #
WIKI_EN = ["Bitcoin", "Cryptocurrency", "Ethereum", "Dogecoin", "Blockchain", "Coinbase", "Binance",
           "Solana_(blockchain_platform)", "Meme_coin", "Non-fungible_token", "Shiba_Inu_(cryptocurrency)"]
# Bitcoin and Cryptocurrency articles in the next-largest Wikipedias (titles
# checked against Wikipedia's language links, 2026-10-21).
WIKI_INTL = {
    "de": ["Bitcoin", "Kryptowährung"], "es": ["Bitcoin", "Criptomoneda"], "fr": ["Bitcoin", "Cryptomonnaie"],
    "it": ["Bitcoin", "Criptovaluta"], "pt": ["Bitcoin", "Criptomoeda"], "ja": ["ビットコイン", "暗号通貨"],
    "ko": ["비트코인", "암호화폐"], "zh": ["比特币", "加密貨幣"], "ru": ["Биткоин", "Криптовалюта"],
    "tr": ["Bitcoin", "Kripto_para"], "nl": ["Bitcoin", "Cryptogeld"], "pl": ["Bitcoin", "Kryptowaluta"],
}
TRENDS_TERMS = ["bitcoin", "buy crypto", "crypto", "buy bitcoin", "coinbase", "bitcoin price", "memecoin"]
APP_COUNTRIES = ["us", "ca", "gb"]
# Crypto-native apps only (prefix match, case-insensitive). Brokers and banks
# that also sell crypto (Robinhood, Wealthsimple, SoFi, IBKR) are recorded if
# listed in config.yaml but not scored: their ranks mostly track stock
# trading and banking, and Wealthsimple sits near #1 in Canada's Finance
# chart in any market.
CRYPTO_APPS = ["Coinbase", "Crypto.com", "Binance", "Kraken", "Phantom", "MetaMask", "Trust:", "Exodus", "Gemini:",
               "OKX", "Bybit", "Bitget", "Uphold", "KuCoin", "Shakepay", "Newton", "Bitbuy", "Ledger"]
YT_EXTRA = ["@AltcoinDaily", "@LarkDavis", "@CryptosRUs", "@Bankless"]
HN_TERMS = ["bitcoin", "ethereum", "cryptocurrency"]
HN_START = date(2011, 1, 3)          # a Monday
HN_CALLS_PER_RUN = 450               # Algolia allows 10,000 an hour; backfill spreads over a few runs
GDELT_QUERY = "(bitcoin OR cryptocurrency OR crypto)"

DAY = pd.Timedelta(days=1)


def _log(con, source: str, fn) -> int:
    try:
        rows = fn() or 0
        storage.log_fetch(con, source, True, rows)
        return rows
    except Exception as e:  # noqa: BLE001 - one input failing never stops the rest
        storage.log_fetch(con, source, False, 0, f"{type(e).__name__}: {e}")
        return 0


def _due(con, source: str, every: pd.Timedelta) -> bool:
    last = storage.last_success(con, source)
    if last is None:
        return True
    last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
    return pd.Timestamp.now(tz="UTC") - last >= every


def _cfg(cfg: dict) -> dict:
    return (cfg or {}).get("social_risk") or {}


def _union(base: list, extra) -> list:
    return list(dict.fromkeys([*base, *(extra or [])]))


# --------------------------------------------------------------------------- #
# Wikipedia
# --------------------------------------------------------------------------- #
WIKI_AGG = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate/"
            "{project}/all-access/user/daily/{start}/{end}")


def wiki_articles(cfg: dict) -> list[tuple[str, str, str]]:
    """(project, article, series name) for every tracked article."""
    en = _union(WIKI_EN, (_cfg(cfg).get("wikipedia") or {}).get("articles"))
    out = [("en.wikipedia", a, f"wiki:{a}") for a in en]
    out += [(f"{lang}.wikipedia", a, f"wiki:{lang}:{a}") for lang, arts in WIKI_INTL.items() for a in arts]
    return out


def project_total(project: str, start: date, end: date | None = None) -> pd.Series:
    end = end or date.today()
    url = WIKI_AGG.format(project=project, start=start.strftime("%Y%m%d00"), end=end.strftime("%Y%m%d00"))
    items = get_json(url)["items"]
    return pd.Series({pd.to_datetime(i["timestamp"][:8], format="%Y%m%d", utc=True): i["views"] for i in items},
                     dtype=float).sort_index()


def _start_for(con, name: str) -> date:
    s = storage.load_series(con, name)
    # Wikimedia sometimes revises the latest days, so re-read the last 10.
    return date(2015, 7, 1) if s.empty else (s.index[-1] - 10 * DAY).date()


def update_wikipedia(con, cfg) -> int:
    rows = 0
    for project, article, name in wiki_articles(cfg):
        if not _due(con, f"wikipedia:{project}:{article}", 20 * pd.Timedelta(hours=1)):
            continue
        rows += _log(con, f"wikipedia:{project}:{article}", lambda project=project, article=article, name=name:
                     storage.save_series(con, name, attention.wikipedia_pageviews(
                         article, start=_start_for(con, name), project=project)))
    for project in dict.fromkeys(p for p, _, _ in wiki_articles(cfg)):
        name = f"wikitotal:{project}"
        if not _due(con, f"wikitotal:{project}", 20 * pd.Timedelta(hours=1)):
            continue
        rows += _log(con, f"wikitotal:{project}", lambda project=project, name=name:
                     storage.save_series(con, name, project_total(project, _start_for(con, name))))
    return rows


# --------------------------------------------------------------------------- #
# Google Trends: weekly data only changes once a week, so each term is
# re-downloaded every 6 days (monthly every 25), which keeps well under
# Google's rate limit even with seven terms.
# --------------------------------------------------------------------------- #
def update_google_trends(con, cfg) -> int:
    gt = _cfg(cfg).get("google_trends") or {}
    rows = 0
    for term in _union(TRENDS_TERMS, gt.get("terms")):
        for freq, timeframe, every in (("weekly", "today 5-y", 6), ("monthly", "all", 25)):
            source = f"google_trends:{freq}:{term}"
            if not _due(con, source, every * DAY):
                continue
            rows += _log(con, source, lambda term=term, freq=freq, timeframe=timeframe: storage.save_series(
                con, f"gtrends_{freq}:{term}", attention.google_trends(term, timeframe, gt.get("geo", ""))))
            time.sleep(4)   # Google blocks rapid repeat requests
    return rows


# --------------------------------------------------------------------------- #
# App Store
# --------------------------------------------------------------------------- #
def update_app_store(con, cfg) -> int:
    ac = _cfg(cfg).get("app_store") or {}
    countries = _union(APP_COUNTRIES, [ac["country"]] if ac.get("country") else [])
    apps = _union(CRYPTO_APPS, ac.get("apps"))
    today = attention.today_utc()
    rows = 0
    for cc in countries:
        for chart in ("overall", "finance"):
            source = f"app_store:{cc}:{chart}"
            if not _due(con, source, 20 * pd.Timedelta(hours=1)):
                continue

            def step(cc=cc, chart=chart):
                names = attention.app_store_chart(cc, chart)
                if not names:
                    raise ValueError("chart came back empty")
                n = 0
                for app, rank in attention.find_ranks(names, apps).items():
                    # Not in the chart is stored as chart length + 1, so "absent" is a reading, not a gap.
                    value = len(names) + 1 if pd.isna(rank) else rank
                    n += storage.save_point(con, f"appstore_{chart}:{cc}:{app.rstrip(':')}", value, ts=today)
                return n
            rows += _log(con, source, step)
    return rows


# --------------------------------------------------------------------------- #
# YouTube: subscribers and lifetime views from one call per channel
# --------------------------------------------------------------------------- #
def update_youtube(con, cfg) -> int:
    key = ((cfg or {}).get("api_keys") or {}).get("youtube")
    if not key:
        storage.log_fetch(con, "youtube", False, 0, "skipped: no API key in config.yaml")
        return 0
    handles = _union((_cfg(cfg).get("youtube") or {}).get("handles") or [], YT_EXTRA)
    today = attention.today_utc()
    rows = 0
    for handle in handles:
        source = f"youtube:{handle}"
        if not _due(con, source, 20 * pd.Timedelta(hours=1)):
            continue

        def step(handle=handle):
            st = attention.youtube_stats(handle, key)
            n = storage.save_point(con, f"yt_views:{handle}", st["views"], ts=today)
            if st.get("subs") is not None:
                n += storage.save_point(con, f"yt_subs:{handle}", st["subs"], ts=today)
            return n
        rows += _log(con, source, step)
    return rows


# --------------------------------------------------------------------------- #
# Hacker News (Algolia search API, history back to 2007)
# --------------------------------------------------------------------------- #
HN_URL = "https://hn.algolia.com/api/v1/search_by_date"


def hn_count(term: str, kind: str, start: datetime, end: datetime) -> int:
    params = {"query": term, "tags": kind, "hitsPerPage": 0, "typoTolerance": "false",
              "numericFilters": f"created_at_i>={int(start.timestamp())},created_at_i<{int(end.timestamp())}"}
    return int(get_json(HN_URL, params)["nbHits"])


def hn_weeks(today: date | None = None) -> list[date]:
    """Week start dates (Mondays) of every complete week since HN_START."""
    today = today or datetime.now(timezone.utc).date()
    last_monday = today - timedelta(days=today.weekday())
    n = (last_monday - HN_START).days // 7
    return [HN_START + timedelta(weeks=i) for i in range(n)]


def update_hacker_news(con, cfg) -> int:
    """Weekly counts for every (kind, term) pair plus the all-items totals.
    Works newest week first and fetches all eight counts for a week together,
    so every filled week is complete. The first backfill (about 820 weeks x 6
    counts) spreads over ~11 hourly runs."""
    series = [(kind, term) for kind in ("story", "comment") for term in HN_TERMS]
    names = [f"hn:{kind}:{term}" for kind, term in series]
    weeks = hn_weeks()
    if not weeks:
        return 0
    have = [set(storage.load_series(con, n).index.date) for n in names]
    recent = set(weeks[-2:])        # late comments: always refresh the last two weeks
    todo = [w for w in reversed(weeks)
            if w in recent or any((w + timedelta(days=7)) not in h for h in have)]
    got: dict[str, dict] = {n: {} for n in names}
    budget, error = HN_CALLS_PER_RUN, None
    try:
        for w in todo:
            if budget < len(series):
                break
            start = datetime(w.year, w.month, w.day, tzinfo=timezone.utc)
            stamp = pd.Timestamp(start + timedelta(days=7))
            week = {}
            for (kind, term), n in zip(series, names):
                week[n] = hn_count(term, kind, start, start + timedelta(days=7))
                budget -= 1
                time.sleep(0.12)
            for n, v in week.items():       # only whole weeks are kept
                got[n][stamp] = v
    except Exception as e:  # noqa: BLE001 - keep the weeks already fetched, then report
        error = e
    rows = sum(storage.save_series(con, n, pd.Series(v, dtype=float)) for n, v in got.items() if v)
    if error is not None:
        raise RuntimeError(f"stopped after {rows} rows: {type(error).__name__}: {error}")
    return rows


# --------------------------------------------------------------------------- #
# GDELT: share of the world's online news mentioning crypto. The DOC API only
# searches the last ~3 months, so history builds up from the first run.
# --------------------------------------------------------------------------- #
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def gdelt_volume(query: str = GDELT_QUERY, timespan: str = "12w") -> pd.DataFrame:
    d = get_json(GDELT_URL, {"query": query, "mode": "timelinevolraw", "format": "json", "timespan": timespan})
    data = (d.get("timeline") or [{}])[0].get("data") or []
    if not data:
        raise ValueError("GDELT returned no timeline")
    df = pd.DataFrame(data)
    df.index = pd.to_datetime(df["date"], format="%Y%m%dT%H%M%SZ", utc=True)
    out = df[["value", "norm"]].astype(float).resample("1D").sum()
    today = pd.Timestamp.now(tz="UTC").normalize()
    return out[out.index < today]       # today's bucket is still filling


def update_gdelt(con, cfg) -> int:
    df = gdelt_volume()
    return storage.save_series(con, "gdelt:count", df["value"]) + storage.save_series(con, "gdelt:total", df["norm"])


# --------------------------------------------------------------------------- #
# Entry point used by the source registry
# --------------------------------------------------------------------------- #
def update(con, cfg) -> int:
    rows = 0
    rows += update_wikipedia(con, cfg)
    rows += update_app_store(con, cfg)
    rows += update_youtube(con, cfg)
    if _due(con, "gdelt", 20 * pd.Timedelta(hours=1)):
        rows += _log(con, "gdelt", lambda: update_gdelt(con, cfg))
    rows += _log(con, "hacker_news", lambda: update_hacker_news(con, cfg))
    if (cfg or {}).get("_include_trends", True):
        rows += update_google_trends(con, cfg)
    return rows
