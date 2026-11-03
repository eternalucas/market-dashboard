"""Data health: the pipeline diagram.

Four columns, left to right: where data comes from (providers), what fetches it
and how often (recorder jobs), where it is stored, and the tabs that show it.
Boxes and lines are coloured by the worst status of the feeds behind them, so a
failing provider shows the path to every tab it affects. Built from
src/pipeline_map.py and the live health table; clicking a box lists its feeds.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import pipeline_map as pm

COLOR = {"OK": "#2fbf71", "Stale": "#f2a33a", "Failing": "#e5484d"}
EDGE_OK = "rgba(139,139,139,0.30)"
BOX, TEXT, MUTED = "#1c1c1c", "#f7f8fa", "#8b8b8b"
X = {"provider": 0.0, "job": 1.0, "db": 2.0, "tab": 3.0}
W = {"provider": 0.62, "job": 0.62, "db": 0.62, "tab": 0.5}
HEADERS = {"provider": "1  Where it comes from", "job": "2  What fetches it, how often",
           "db": "3  Where it is stored", "tab": "4  Where you see it"}
ROW = 1.0          # provider row pitch (data units)
GAP = 0.9          # extra space above each provider category


def _count(r) -> str:
    bad = [f"{r.failing} failing"] * bool(r.failing) + [f"{r.stale} stale"] * bool(r.stale)
    return f"{r.ok}/{r.n} OK" + (f" · {', '.join(bad)}" if bad else "")


def _layout(m: dict) -> dict:
    """y position and height of every node: providers on fixed rows by category,
    the other columns spread over the same height, ordered to follow the lines."""
    prov = m["nodes"]["provider"].set_index("id")
    pos, cats, y = {}, [], 0.0
    for cat, names in pm.CATEGORIES + [("Other", ["Other"])]:
        names = [n for n in names if n in prov.index]
        if not names:
            continue
        y -= GAP
        cats.append((cat, y + 0.62))
        for n in names:
            pos[("provider", n)] = (y, 0.74)
            y -= ROW
    top, bottom = -GAP + 0.37, y + ROW - 0.37
    e = m["edges"]
    for col, prev, h in (("job", "provider", 1.7), ("db", "job", 2.2), ("tab", "db", 1.5)):
        ids = list(m["nodes"][col]["id"])
        ee = e[(e.src_col == prev) & (e.dst_col == col)]

        def bary(i):
            ys = [pos[(prev, s)][0] * n for s, n in zip(ee.loc[ee.dst == i, "src"], ee.loc[ee.dst == i, "n"])]
            ws = ee.loc[ee.dst == i, "n"].sum()
            return sum(ys) / ws if ws else 0.0
        if col == "tab":
            order = [t for t in pm.TABS if t in ids]
        else:
            order = sorted(ids, key=bary, reverse=True)
        k = len(order)
        span = top - bottom - h
        for j, i in enumerate(order):
            pos[(col, i)] = (top - h / 2 - (span * j / (k - 1) if k > 1 else span / 2), h)
    return {"pos": pos, "cats": cats, "ylim": (bottom - 0.4, top + 1.6)}


def _detail(m: dict, col: str, i: str) -> str:
    rows = m["rows"][m["rows"][col] == i]

    def names(c, fmt=lambda v: v):
        return ", ".join(fmt(v) for v in dict.fromkeys(rows[c]))
    out = []
    if col != "provider":
        out.append("From: " + names("provider"))
    if col != "job":
        out.append("Fetched by: " + names("job", lambda j: f"{pm.JOBS[j][0]} ({pm.JOBS[j][1]})"))
    if col != "db":
        out.append("Stored in: " + names("db"))
    if col != "tab":
        out.append("Shown in: " + names("tab"))
    if col == "provider" and i in pm.PROVIDER_NOTES:
        out.append(pm.PROVIDER_NOTES[i])
    return "<br>".join(out)


def figure(h: pd.DataFrame) -> tuple[go.Figure, dict]:
    m = pm.build(h)
    L = _layout(m)
    pos = L["pos"]
    shapes, ann = [], []
    # lines first (below the boxes), problems on top of healthy ones
    e = m["edges"].assign(r=lambda d: d["status"].map(pm.RANK)).sort_values("r")
    for r in e.itertuples():
        (y0, _), (y1, _) = pos[(r.src_col, r.src)], pos[(r.dst_col, r.dst)]
        x0, x1 = X[r.src_col] + W[r.src_col] / 2, X[r.dst_col] - W[r.dst_col] / 2
        xm = (x0 + x1) / 2
        ok = r.status == "OK"
        shapes.append(dict(type="path", path=f"M {x0},{y0} C {xm},{y0} {xm},{y1} {x1},{y1}", layer="below",
                           line=dict(color=EDGE_OK if ok else COLOR[r.status], width=1.1 if ok else 2.2)))
    hx, hy, htext, hdata = [], [], [], []
    for col in ("provider", "job", "db", "tab"):
        for r in m["nodes"][col].itertuples():
            y, hgt = pos[(col, r.id)]
            x, w = X[col], W[col]
            c = COLOR[r.status]
            shapes.append(dict(type="rect", x0=x - w / 2, x1=x + w / 2, y0=y - hgt / 2, y1=y + hgt / 2,
                               fillcolor=BOX, line=dict(color=c, width=1.6 if r.status != "OK" else 1.0)))
            shapes.append(dict(type="rect", x0=x - w / 2, x1=x - w / 2 + 0.018, y0=y - hgt / 2, y1=y + hgt / 2,
                               fillcolor=c, line=dict(width=0)))
            if col == "job":
                title, sub = pm.JOBS[r.id]
            elif col == "db":
                title, sub = r.id, pm.DBS[r.id]
            else:
                title, sub = r.id, None
            count = _count(r)
            tone = c if r.status != "OK" else MUTED
            left = x - w / 2 + 0.04
            if col == "provider":            # one line: name left, count right
                ann.append(dict(x=left, y=y, text=f"<b>{title}</b>", xanchor="left", showarrow=False,
                                font=dict(color=TEXT, size=12)))
                ann.append(dict(x=x + w / 2 - 0.03, y=y, text=count, xanchor="right", showarrow=False,
                                font=dict(color=tone, size=11)))
            else:                            # name, then schedule / contents, then count
                lines = [f"<b>{title}</b>"] + ([f"<span style='color:{MUTED};font-size:10.5px'>{sub}</span>"]
                                               if sub else []) + [f"<span style='color:{tone};font-size:10.5px'>{count}</span>"]
                ann.append(dict(x=left, y=y, text="<br>".join(lines), xanchor="left", yanchor="middle",
                                showarrow=False, align="left", font=dict(color=TEXT, size=12)))
            hx.append(x)
            hy.append(y)
            hdata.append([col, r.id])
            htext.append(f"<b>{title}</b><br>{r.n} feeds: {count}<br>{_detail(m, col, r.id)}"
                         "<br><i>Click to list these feeds below</i>")
    for col, t in HEADERS.items():
        ann.append(dict(x=X[col] - W[col] / 2, y=L["ylim"][1] - 0.5, text=f"<b>{t}</b>", xanchor="left",
                        showarrow=False, font=dict(color=TEXT, size=13)))
    for cat, y in L["cats"]:
        ann.append(dict(x=X["provider"] - W["provider"] / 2, y=y, text=cat.upper(), xanchor="left", showarrow=False,
                        font=dict(color=MUTED, size=10)))
    fig = go.Figure(go.Scatter(
        x=hx, y=hy, mode="markers", customdata=hdata, hovertext=htext, hoverinfo="text",
        marker=dict(symbol="square", size=34, color="rgba(0,0,0,0)"),
        selected=dict(marker=dict(color="rgba(0,0,0,0)")), unselected=dict(marker=dict(opacity=1))))
    n_rows = L["ylim"][1] - L["ylim"][0]
    fig.update_layout(
        shapes=shapes, annotations=ann, height=int(26 * n_rows) + 40, showlegend=False, dragmode=False,
        margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor="#262626", bordercolor="#3a3a3a", font=dict(color=TEXT, size=12), align="left"),
        xaxis=dict(visible=False, range=[-0.36, 3.3], fixedrange=True),
        yaxis=dict(visible=False, range=list(L["ylim"]), fixedrange=True))
    return fig, m


LEGEND = ("<span style='color:#2fbf71'>■</span> OK &nbsp; <span style='color:#f2a33a'>■</span> Stale &nbsp; "
          "<span style='color:#e5484d'>■</span> Failing &nbsp;·&nbsp; a box or line takes the colour of the worst "
          "feed passing through it, so a problem shows the whole path to the tabs it affects.")


def render(h: pd.DataFrame, table) -> None:
    """Draw the diagram; `table(df)` shows the feeds of the box that was clicked."""
    st.markdown("##### How the data flows")
    st.caption("Every source the dashboard uses, left to right: who serves it, which recorder job fetches it and "
               "how often, where it is stored, and which tabs read it. Hover a box for details; click one to list "
               "its feeds below (click empty space to clear).")
    st.markdown(f"<div style='font-size:0.8rem;color:{MUTED};margin:-4px 0 4px 0'>{LEGEND}</div>",
                unsafe_allow_html=True)
    fig, m = figure(h)
    ev = st.plotly_chart(fig, width="stretch", key="dh-pipeline", on_select="rerun", selection_mode="points",
                         config={"displayModeBar": False})
    pts = (ev or {}).get("selection", {}).get("points", []) if isinstance(ev, dict) or hasattr(ev, "get") else []
    if pts and pts[0].get("customdata"):
        col, i = pts[0]["customdata"][:2]
        name = pm.JOBS[i][0] if col == "job" else i
        srcs = list(dict.fromkeys(m["rows"].loc[m["rows"][col] == i, "source"]))
        st.markdown(f"**Feeds behind {name}** ({len(srcs)})")
        table(h[h["Source"].isin(srcs)])
