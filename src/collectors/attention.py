"""Non-financial attention data: Wikipedia, Google Trends, App Store, YouTube.

Each function fetches raw data and returns plain pandas objects. Scoring
happens later in indicators/social_risk.py, so collection and analysis stay
separate and each can be tested on its own.

History available from each source:
  Wikipedia pageviews  daily, back to July 2015
  Google Trends        monthly back to 2004, weekly for the last 5 years
  App Store rank       today only; history builds up as the dashboard runs
  YouTube subscribers  today only; history builds up as the dashboard runs
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from .http import get_json

# --------------------------------------------------------------------------- #
# Wikipedia
# --------------------------------------------------------------------------- #
WIKI_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "{project}/all-access/user/{article}/daily/{start}/{end}"
)


def wikipedia_pageviews(article: str, start: date = date(2015, 7, 1), end: date | None = None,
                        project: str = "en.wikipedia") -> pd.Series:
    """Daily human pageviews ('user' agent type excludes known bots)."""
    from urllib.parse import quote
    end = end or date.today()
    url = WIKI_URL.format(project=project, article=quote(article, safe=""),
                          start=start.strftime("%Y%m%d00"), end=end.strftime("%Y%m%d00"))
    items = get_json(url)["items"]
    s = pd.Series(
        {pd.to_datetime(i["timestamp"][:8], format="%Y%m%d", utc=True): i["views"] for i in items},
        dtype=float,
    )
    return s.sort_index()


# --------------------------------------------------------------------------- #
# Google Trends (unofficial API via pytrends; Google rate-limits it heavily)
# --------------------------------------------------------------------------- #
def google_trends(term: str, timeframe: str, geo: str = "") -> pd.Series:
    """Search interest, 0-100. timeframe: 'all' (monthly) or 'today 5-y' (weekly).

    Google scales every request so its own peak = 100, so a monthly and a
    weekly series are not on the same scale. They are stored separately and
    each is scored against its own history.
    """
    from pytrends.request import TrendReq  # imported here: slow, and optional

    # retries=0: pytrends' own retry setup uses an argument urllib3 2.x removed
    # ('method_whitelist'), which crashes. The pipeline pauses between calls instead.
    py = TrendReq(hl="en-US", tz=0, timeout=(10, 25), retries=0, backoff_factor=0)
    py.build_payload([term], timeframe=timeframe, geo=geo)
    df = py.interest_over_time()
    if df.empty:
        return pd.Series(dtype=float)
    if "isPartial" in df:
        df = df[~df["isPartial"].astype(bool)]  # drop the incomplete latest bucket
    s = df[term].astype(float)
    s.index = pd.to_datetime(s.index, utc=True)
    return s


def google_trends_from_csv(path: str) -> pd.Series:
    """Fallback when the API is blocked: a CSV downloaded from trends.google.com.

    Google's export has a couple of header lines, then 'Week,<term>' or
    'Month,<term>'. Values like '<1' are treated as 0.5.
    """
    raw = pd.read_csv(path, skiprows=_header_rows(path))
    raw.columns = ["date", "value"]
    raw["value"] = pd.to_numeric(raw["value"].astype(str).str.replace("<1", "0.5"), errors="coerce")
    s = pd.Series(raw["value"].values, index=pd.to_datetime(raw["date"], utc=True))
    return s.dropna()


def _header_rows(path: str) -> int:
    with open(path, encoding="utf-8-sig") as f:
        for n, line in enumerate(f):
            if line.lower().startswith(("week,", "month,", "day,", "time,")):
                return n
    return 0


# --------------------------------------------------------------------------- #
# Apple App Store rankings
# --------------------------------------------------------------------------- #
FINANCE_GENRE = 6015
FINANCE_URL = "https://itunes.apple.com/{cc}/rss/topfreeapplications/limit=200/genre={genre}/json"
OVERALL_URL = "https://rss.applemarketingtools.com/api/v2/{cc}/apps/top-free/100/apps.json"


def app_store_chart(country: str = "ca", chart: str = "finance") -> list[str]:
    """App names in rank order (index 0 = rank 1)."""
    if chart == "finance":
        data = get_json(FINANCE_URL.format(cc=country, genre=FINANCE_GENRE))
        entries = data.get("feed", {}).get("entry", [])
        return [e["im:name"]["label"] for e in entries]
    data = get_json(OVERALL_URL.format(cc=country))
    return [r["name"] for r in data["feed"]["results"]]


def find_ranks(chart: list[str], apps: list[str]) -> dict[str, float]:
    """Rank of each tracked app; NaN when it's not in the chart.

    Matching is by prefix, ignoring case, because App Store names carry
    taglines: 'Coinbase: Buy Bitcoin & Ether' should match 'Coinbase'.
    """
    lowered = [name.lower() for name in chart]
    out = {}
    for app in apps:
        key = app.lower()
        rank = next((i + 1 for i, name in enumerate(lowered) if name.startswith(key)), None)
        out[app] = float(rank) if rank else float("nan")
    return out


# --------------------------------------------------------------------------- #
# YouTube subscriber counts
# --------------------------------------------------------------------------- #
YOUTUBE_URL = "https://www.googleapis.com/youtube/v3/channels"


def youtube_subscribers(handle: str, api_key: str) -> float:
    """Current subscriber count. YouTube rounds public counts to 3 significant
    figures, so small daily changes don't show; 30-day growth is used instead."""
    # Accepts a handle (@CoinBureau) or a channel ID (UC...), which never changes.
    by = {"id": handle} if handle.startswith("UC") else {"forHandle": handle}
    data = get_json(YOUTUBE_URL, params={"part": "statistics", "key": api_key, **by})
    items = data.get("items") or []
    if not items:
        raise ValueError(f"YouTube handle not found: {handle}")
    stats = items[0]["statistics"]
    if stats.get("hiddenSubscriberCount"):
        raise ValueError(f"{handle} hides its subscriber count")
    return float(stats["subscriberCount"])


def youtube_stats(handle: str, api_key: str) -> dict:
    """Lifetime views (exact) and subscribers (None when hidden) for one channel."""
    by = {"id": handle} if handle.startswith("UC") else {"forHandle": handle}
    data = get_json(YOUTUBE_URL, params={"part": "statistics", "key": api_key, **by})
    items = data.get("items") or []
    if not items:
        raise ValueError(f"YouTube handle not found: {handle}")
    st = items[0]["statistics"]
    subs = None if st.get("hiddenSubscriberCount") else float(st["subscriberCount"])
    return {"views": float(st["viewCount"]), "subs": subs}


def today_utc() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
