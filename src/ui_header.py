"""Page header ("Market Dashboard") and print styles.

The header uses the app's own design tokens (src/charts.py): near-black
canvas, Inter, bitcoin orange as the only accent.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from . import storage

CSS = """
<style>
.md-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px;
    padding: 2px 0 14px 0; margin: -2.2rem 0 6px 0; border-bottom: 1px solid #2a2a2a; position: relative; }
.md-header::after { content: ""; position: absolute; left: 0; bottom: -1px; width: 132px; height: 2px;
    background: linear-gradient(90deg, #f7931a, rgba(247,147,26,0)); }
.md-brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
.md-mark { width: 34px; height: 34px; border-radius: 8px; flex: 0 0 auto;
    background: linear-gradient(145deg, #f7931a 0%, #c86f0a 100%);
    box-shadow: 0 0 0 1px rgba(255,255,255,0.06) inset, 0 6px 18px rgba(247,147,26,0.18);
    display: flex; align-items: flex-end; justify-content: center; gap: 3px; padding: 0 0 8px 0; box-sizing: border-box; }
.md-mark i { display: block; width: 4px; border-radius: 1px; background: #141414; }
.md-title { font-family: Inter, 'Helvetica Neue', system-ui, -apple-system, sans-serif; font-size: 1.75rem;
    font-weight: 650; letter-spacing: -0.02em; line-height: 1.1; color: #f7f8fa; margin: 0; white-space: nowrap; }
.md-sub { font-family: Inter, 'Helvetica Neue', system-ui, sans-serif; font-size: 0.72rem; font-weight: 500;
    letter-spacing: 0.14em; text-transform: uppercase; color: #8b8b8b; margin-top: 5px; white-space: nowrap; }
.md-status { font-family: Inter, 'Helvetica Neue', system-ui, sans-serif; font-size: 0.78rem; color: #8b8b8b;
    text-align: right; line-height: 1.5; white-space: nowrap; }
.md-status b { color: #f7f8fa; font-weight: 600; }
.md-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px;
    vertical-align: 1px; }
@media (max-width: 760px) { .md-sub, .md-status { display: none; } .md-title { font-size: 1.4rem; } }

/* Print (main menu > Print): only the page content, colours kept as on screen, charts kept whole. */
@media print {
    [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
    [data-testid="stStatusWidget"], .st-key-global-search, [data-testid="stSidebarCollapsedControl"] { display: none !important; }
    [data-testid="stAppViewContainer"], [data-testid="stMain"], .stApp { background: #141414 !important; }
    * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
    [data-testid="stMainBlockContainer"] { padding-top: 0 !important; max-width: 100% !important; }
    [data-testid="stPlotlyChart"], [data-testid="stVerticalBlockBorderWrapper"], [data-testid="stMetric"] {
        break-inside: avoid; page-break-inside: avoid; }
    .md-header { margin-top: 0; }
}
</style>
"""


def render(con, recorder_state: str = "") -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    log = storage.fetch_log(con, limit=1)
    last = pd.to_datetime(log["ts"].iloc[0], utc=True) if not log.empty else None
    recording = recorder_state in ("started", "already running", "running elsewhere")
    dot = "#2fbf71" if recording else "#8b8b8b"
    state = "Recording" if recording else "Recorder off"
    when = f"Last fetch <b>{last:%d %b %H:%M} UTC</b>" if last is not None else "No data yet"
    st.markdown(
        f'<div class="md-header"><div class="md-brand">'
        f'<div class="md-mark"><i style="height:9px"></i><i style="height:15px"></i><i style="height:12px"></i></div>'
        f'<div><div class="md-title">Market Dashboard</div>'
        f'<div class="md-sub">Crypto market structure &middot; Positioning &middot; On-chain &middot; Macro</div></div></div>'
        f'<div class="md-status"><span class="md-dot" style="background:{dot}"></span>{state}<br>{when}</div></div>',
        unsafe_allow_html=True)
