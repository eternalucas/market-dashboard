"""Data health tab: what is working, what is stale, what is failing.

Reads the whole fetch log (src/views.py source_health). Layout: four
headline numbers, then only what needs attention, then one line per source
group, the recorder's jobs, and every source in an expander.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .localtime import fmt_col
from . import ui_pipeline, views

BADGE = {"OK": "🟢 OK", "Stale": "🟠 Stale", "Failing": "🔴 Failing"}

# Recorder job behind each rec:* source, and how often it runs.
REC_CADENCE = [("rec:book:", "Order books", "every minute"), ("rec:option_trades_backfill", "Option trades backfill", "hourly"),
               ("rec:option_trades", "Option trades", "every 5 min"), ("rec:option_chain:ibit", "IBIT option chain", "after each US close"),
               ("rec:option_chain", "Option chain", "hourly"), ("rec:futures_curve", "Futures curve", "every 15 min"),
               ("rec:mempool", "Mempool", "every 10 min"), ("rec:lightning", "Lightning", "daily"),
               ("rec:hl_liq_scan", "Hyperliquid liquidation scan", "every 15 min"),
               ("rec:liq_feeds", "Liquidation feeds", "hourly"), ("rec:liq_calibrate", "Liquidation model calibration", "daily"),
               ("rec:trade_leaderboard", "Trade review: Top 100 sync", "hourly"),
               ("rec:pipeline", "Update-all steps (prices, history, events, cohorts)", "hourly"),
               ("rec:flows", "Coinalyze 1h flows", "hourly"), ("rec:positioning", "Live positioning, every asset", "hourly")]

CSS = """
<style>
.dh-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 6px 0 18px 0; }
.dh-card { background: #1c1c1c; border: 1px solid #2a2a2a; border-radius: 10px; padding: 14px 16px; }
.dh-card .k { font-size: 0.72rem; letter-spacing: 0.12em; text-transform: uppercase; color: #8b8b8b; }
.dh-card .v { font-size: 1.9rem; font-weight: 650; color: #f7f8fa; line-height: 1.2; margin-top: 4px; }
.dh-card .s { font-size: 0.78rem; color: #8b8b8b; margin-top: 2px; }
.dh-card.ok .v { color: #2fbf71; } .dh-card.stale .v { color: #f2a33a; } .dh-card.fail .v { color: #e5484d; }
.dh-bar { height: 6px; border-radius: 3px; background: #2a2a2a; overflow: hidden; display: flex; margin-top: 10px; }
.dh-bar i { display: block; height: 100%; }
@media (max-width: 760px) { .dh-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
"""


def _cards(h: pd.DataFrame, recorder_state: str) -> None:
    n = len(h)
    ok, stale, fail = (int((h["Status"] == s).sum()) for s in ("OK", "Stale", "Failing"))
    rec_on = recorder_state in ("started", "already running", "running elsewhere")
    rec_txt = "Recording" if rec_on else "Off"
    rec_sub = ("in this dashboard" if recorder_state in ("started", "already running")
               else "in Start Recorder" if recorder_state == "running elsewhere" else "start the dashboard to record")
    bar = (f'<div class="dh-bar"><i style="width:{ok / n:.1%};background:#2fbf71"></i>'
           f'<i style="width:{stale / n:.1%};background:#f2a33a"></i>'
           f'<i style="width:{fail / n:.1%};background:#e5484d"></i></div>') if n else ""
    st.markdown(
        CSS + '<div class="dh-cards">'
        f'<div class="dh-card ok"><div class="k">Healthy</div><div class="v">{ok}</div>'
        f'<div class="s">of {n} sources</div>{bar}</div>'
        f'<div class="dh-card stale"><div class="k">Stale</div><div class="v">{stale}</div>'
        f'<div class="s">older than expected</div></div>'
        f'<div class="dh-card fail"><div class="k">Failing</div><div class="v">{fail}</div>'
        f'<div class="s">last attempt failed</div></div>'
        f'<div class="dh-card {"ok" if rec_on else "fail"}"><div class="k">Recorder</div>'
        f'<div class="v" style="font-size:1.35rem;padding-top:6px">{rec_txt}</div><div class="s">{rec_sub}</div></div>'
        '</div>', unsafe_allow_html=True)


def _table(df: pd.DataFrame, cols: list[str], height: int | None = None) -> None:
    show = df[cols].copy()
    if "Status" in show:
        show["Status"] = show["Status"].map(BADGE)
    for c in ("Last success", "Last attempt"):
        if c in show:
            show[c] = fmt_col(show[c]).fillna("never")
    st.dataframe(show, hide_index=True, width="stretch", height=height or min(35 * len(show) + 38, 520),
                 column_config={"Last error": st.column_config.TextColumn(width="large"),
                                "Source": st.column_config.TextColumn(width="medium")})


def render(con, recorder_state: str) -> None:
    st.subheader("Data health")
    h = views.source_health(con)
    if h.empty:
        st.caption("Nothing fetched yet.")
        return
    _cards(h, recorder_state)
    ui_pipeline.render(h, lambda df: _table(df, ["Status", "Group", "Source", "Expected", "Updated", "Last success",
                                                 "Last error"]))

    bad = h[h["Status"] != "OK"]
    st.markdown("##### Needs attention")
    if bad.empty:
        st.success("Every source is current for its schedule and its last attempt worked.")
    else:
        st.caption("Failing: the last attempt returned an error. Stale: the last successful fetch is older than "
                   "the source's schedule allows (two missed runs, see 'Expected'), or its last "
                   f"{views.ZERO_ROWS_RUNS} successful runs stored no rows. A source can be Stale while its "
                   "fetches 'succeed'.")
        _table(bad, ["Status", "Group", "Source", "Expected", "Updated", "Last success", "Last error"])

    st.markdown("##### By group")
    g = h.groupby("Group").agg(Sources=("Source", "size"), Healthy=("Status", lambda s: int((s == "OK").sum())),
                               Stale=("Status", lambda s: int((s == "Stale").sum())),
                               Failing=("Status", lambda s: int((s == "Failing").sum())),
                               newest=("Last success", "max")).reset_index()
    g["Health"] = g["Healthy"] / g["Sources"]
    g["Newest data"] = (pd.Timestamp.now(tz="UTC") - g["newest"]).map(views.ago)
    g = g.sort_values(["Health", "Group"]).drop(columns="newest")
    st.dataframe(g[["Group", "Health", "Sources", "Healthy", "Stale", "Failing", "Newest data"]], hide_index=True,
                 width="stretch", height=35 * len(g) + 38,
                 column_config={"Health": st.column_config.ProgressColumn("Healthy share", format="percent",
                                                                          min_value=0.0, max_value=1.0)})

    st.markdown("##### Recorder jobs")
    rec = h[h["Source"].str.startswith("rec:")].copy()
    if rec.empty:
        st.caption("The recorder hasn't logged anything yet.")
    else:
        def job(src):
            return next(((n, c) for p, n, c in REC_CADENCE if src.startswith(p)), (src.removeprefix("rec:"), ""))
        rec["Job"] = rec["Source"].map(lambda s: job(s)[0])
        rec["Runs"] = rec["Source"].map(lambda s: job(s)[1])
        rec["Detail"] = rec["Source"].str.split(":").str[2:].str.join(" ")
        _table(rec.sort_values(["Job", "Detail"]), ["Status", "Job", "Detail", "Runs", "Updated", "Last error"])
        st.caption("Successful runs are logged at most once an hour per job, so 'Updated' can lag a job that runs "
                   "every minute by up to an hour; a failure is logged at once.")

    with st.expander(f"Every source ({len(h)})"):
        a, b = st.columns([2, 1])
        groups = a.multiselect("Group", sorted(h["Group"].unique()), placeholder="All groups", key="dh-groups")
        status = b.multiselect("Status", ["OK", "Stale", "Failing"], placeholder="Any status", key="dh-status")
        keep = pd.Series(True, index=h.index)
        if groups:
            keep &= h["Group"].isin(groups)
        if status:
            keep &= h["Status"].isin(status)
        f = h[keep]
        _table(f, ["Status", "Group", "Source", "Expected", "Updated", "Last success", "Last attempt", "Rows (last)",
                   "Last error"],
               height=520)
