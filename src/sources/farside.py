"""Farside Investors: daily US spot Bitcoin ETF flows per fund (US$ m).
https://farside.co.uk/bitcoin-etf-flow-all-data/

Farside's site sits behind Cloudflare and may refuse scripted requests. If it
does, this adapter logs the refusal and the ETF charts fall back to
BGeometrics' total-flow series (per-fund flows then stay partial). No attempt
is made to get around a block.

Parsed with the standard library's HTML parser (no lxml dependency).
Raw series: farside:<TICKER> (US$ m per day), farside:Total.
"""
from __future__ import annotations

from html.parser import HTMLParser

import pandas as pd
import requests

from .. import storage
from ..collectors.http import USER_AGENT

URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self._row, self._cell, self._in = [], None, None, False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._in = [], True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._in:
            self._row.append("".join(self._cell).strip())
            self._in = False
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in:
            self._cell.append(data)


def _num(x: str) -> float:
    x = x.replace(",", "").strip()
    if x in ("", "-"):
        return 0.0
    neg = x.startswith("(") and x.endswith(")")
    try:
        v = float(x.strip("()"))
    except ValueError:
        return float("nan")
    return -v if neg else v


def parse(html: str) -> pd.DataFrame:
    p = _Tables()
    p.feed(html)
    table = max(p.tables, key=len) if p.tables else []
    header = next((r for r in table if r and r[0] == "" or (r and "IBIT" in r)), None)
    if not header:
        return pd.DataFrame()
    cols = [c or "Date" for c in header]
    recs = []
    for r in table:
        try:
            d = pd.to_datetime(r[0], format="%d %b %Y", utc=True)
        except (ValueError, IndexError):
            continue
        if pd.isna(d):
            continue
        recs.append({"date": d, **{c: _num(v) for c, v in zip(cols[1:], r[1:])}})
    return pd.DataFrame(recs).set_index("date").sort_index() if recs else pd.DataFrame()


def update(con) -> int:
    r = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
    if r.status_code in (403, 429, 503) or "cf-challenge" in r.text or "Just a moment" in r.text:
        raise PermissionError(f"Farside refused the request (HTTP {r.status_code}); using BGeometrics ETF flows instead")
    r.raise_for_status()
    df = parse(r.text)
    if df.empty:
        raise ValueError("no flow table found on the page")
    return sum(storage.save_series(con, f"farside:{c}", df[c]) for c in df.columns)
