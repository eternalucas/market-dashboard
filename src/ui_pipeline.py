"""Data health: the pipeline diagram.

Four columns, left to right: where data comes from (providers), what fetches it
and how often (recorder jobs), where it is stored, and the tabs that show it.
Boxes and lines are coloured by the worst status of the feeds behind them, so a
failing provider shows the path to every tab it affects. Built from
src/pipeline_map.py and the live health table.

Drawn as an SVG component (not Plotly) so that hovering a box lights up the
lines its feeds travel, clicking opens its details and feeds, and the view
can be zoomed (50-200%) and dragged.
"""
from __future__ import annotations

import pandas as pd
import json

import streamlit.components.v1 as components
import streamlit as st

from . import pipeline_map as pm

COLOR = {"OK": "#2fbf71", "Stale": "#f2a33a", "Failing": "#e5484d"}
EDGE_OK = "rgba(139,139,139,0.30)"
BOX, TEXT, MUTED = "#1c1c1c", "#f7f8fa", "#8b8b8b"
X = {"provider": 0.0, "job": 1.0, "db": 2.0, "tab": 3.0}
W = {"provider": 0.62, "job": 0.62, "db": 0.62, "tab": 0.5}
HEADERS = {"provider": "1) Where it comes from", "job": "2) What fetches it, how often",
           "db": "3) Where it is stored", "tab": "4) Where you see it"}
ACCENT = "#f7931a"                       # the header logo's orange
UNIT = 27                                # px per layout unit (one provider row)
PX_X = {"provider": 16, "job": 356, "db": 696, "tab": 1036}   # left edge of each column, px
PX_W = {"provider": 240, "job": 240, "db": 240, "tab": 200}
WIDTH = 1252
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



def model(h: pd.DataFrame) -> dict:
    """Everything the component draws, in pixels."""
    m = pm.build(h)
    L = _layout(m)
    top = L["ylim"][1]
    rows = m["rows"]

    def py(y):                           # 34 px strip on top for the zoom control
        return (top - y) * UNIT + 34

    nodes, nid = [], {}
    for col in ("provider", "job", "db", "tab"):
        for r in m["nodes"][col].itertuples():
            y, hgt = L["pos"][(col, r.id)]
            title, sub = (pm.JOBS[r.id] if col == "job" else (r.id, pm.DBS.get(r.id) if col == "db" else None))
            key = f"{col}:{r.id}"
            nid[(col, r.id)] = key
            feeds = h[h["Source"].isin(set(rows.loc[rows[col] == r.id, "source"]))]
            feeds = feeds.assign(_r=feeds["Status"].map(pm.RANK)).sort_values(["_r", "Source"], ascending=[False, True])
            nodes.append({
                "id": key, "col": col, "title": title, "sub": sub, "count": _count(r), "status": r.status,
                "x": PX_X[col], "w": PX_W[col], "y": py(y) - hgt * UNIT / 2, "h": hgt * UNIT,
                "detail": _detail(m, col, r.id),
                "feeds": [list(v) for v in zip(feeds["Status"], feeds["Source"],
                                               feeds.get("Updated", pd.Series("", index=feeds.index)).fillna(""),
                                               feeds.get("Last error", pd.Series("", index=feeds.index)).fillna(""))],
            })
    pairs = (("provider", "job"), ("job", "db"), ("db", "tab"))
    edges = [{"id": f"{nid[(r.src_col, r.src)]}|{nid[(r.dst_col, r.dst)]}", "a": nid[(r.src_col, r.src)],
              "b": nid[(r.dst_col, r.dst)], "status": r.status} for r in m["edges"].itertuples()]
    # the lines each box's feeds travel, with the worst status of those feeds on each line
    trace = {}
    for col in ("provider", "job", "db", "tab"):
        for i, g in rows.groupby(col):
            seg = {}
            for a, b in pairs:
                for (va, vb), gg in g.drop_duplicates(["source", a, b]).groupby([a, b]):
                    seg[f"{a}:{va}|{b}:{vb}"] = pm.worst(gg["status"])
            trace[f"{col}:{i}"] = seg
    return {"nodes": nodes, "edges": edges, "trace": trace, "width": WIDTH, "height": py(L["ylim"][0]),
            "headers": [{"x": PX_X[c], "y": py(L["ylim"][1] - 0.5), "text": t} for c, t in HEADERS.items()],
            "cats": [{"x": PX_X["provider"], "y": py(y), "text": c.upper()} for c, y in L["cats"]],
            "colors": COLOR, "accent": ACCENT}


LEGEND = ("<span style='color:#2fbf71'>■</span> OK &nbsp; <span style='color:#f2a33a'>■</span> Stale &nbsp; "
          "<span style='color:#e5484d'>■</span> Failing &nbsp;·&nbsp; a box or line takes the colour of the worst "
          "feed passing through it, so a problem shows the whole path to the tabs it affects.")

PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { --text:#f7f8fa; --muted:#8b8b8b; --accent:__ACCENT__; }
html,body { margin:0; background:transparent; color:var(--text);
  font-family:"Source Sans Pro","Source Sans 3",Inter,system-ui,-apple-system,sans-serif; }
#wrap { position:relative; height:__VIEWH__px; overflow:auto; cursor:grab; border-radius:10px;
  scrollbar-width:thin; scrollbar-color:#3a3a3a transparent; }
#wrap.drag { cursor:grabbing; user-select:none; }
#zoom { position:absolute; top:6px; right:14px; z-index:5; display:flex; gap:4px; align-items:center;
  background:rgba(28,28,28,.92); border:1px solid #2f2f2f; border-radius:9px; padding:3px; }
#zoom button, #zoom select { background:#262626; color:var(--text); border:1px solid #363636; border-radius:7px;
  height:26px; font:inherit; font-size:13px; cursor:pointer; }
#zoom button { width:26px; font-size:15px; line-height:1; padding:0; }
#zoom button:hover, #zoom select:hover { border-color:var(--accent); }
#zoom select { padding:0 6px; }
.node { cursor:pointer; transition:opacity .15s; }
.node .t { font-size:12.5px; font-weight:700; fill:var(--text); }
.node .s { font-size:11px; fill:var(--muted); }
.node .c { font-size:11px; }
.edge { fill:none; transition:stroke .12s, opacity .12s; }
svg.hovering .edge { stroke:#4a4a4a !important; opacity:.18; stroke-width:1.1 !important; }
svg.hovering .edge.on { opacity:1; stroke-width:2.4 !important; }
svg.hovering .node.off { opacity:.45; }
.hdr { font-size:15px; font-weight:700; fill:var(--accent); }
.cat { font-size:10.5px; letter-spacing:.08em; fill:var(--muted); }
#panel { position:absolute; top:44px; right:14px; width:420px; max-height:calc(100% - 60px); display:none; z-index:6;
  flex-direction:column; background:linear-gradient(180deg,#262626,#1b1b1b); border:1px solid #333;
  border-radius:12px; box-shadow:0 12px 32px rgba(0,0,0,.5); overflow:hidden; }
#panel.open { display:flex; }
#panel .hd { padding:12px 14px 8px; border-bottom:1px solid #2e2e2e; }
#panel .hd b { font-size:15px; } #panel .x { float:right; cursor:pointer; color:var(--muted); font-size:18px; line-height:1; }
#panel .x:hover { color:var(--text); }
#panel .meta { font-size:12.5px; color:#c9c9c9; line-height:1.55; margin-top:6px; }
#panel .cnt { font-size:12.5px; margin-top:2px; }
#panel .feeds { overflow:auto; font-size:12px; }
#panel table { width:100%; border-collapse:collapse; }
#panel th { position:sticky; top:0; background:#1f1f1f; color:var(--muted); font-weight:600; text-align:left;
  padding:6px 10px; border-bottom:1px solid #2e2e2e; }
#panel td { padding:5px 10px; border-bottom:1px solid #252525; vertical-align:top; word-break:break-word; }
#panel td.e { color:#e9a0a0; }
.dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
</style></head><body>
<div id="wrap"><svg id="svg"></svg></div>
<div id="zoom"><button id="zo" title="Zoom out">−</button>
<select id="zs" title="Zoom">__OPTS__</select><button id="zi" title="Zoom in">+</button></div>
<div id="panel"><div class="hd"><span class="x" id="px">×</span><b id="pt"></b><div class="cnt" id="pc"></div>
<div class="meta" id="pm"></div></div><div class="feeds"><table><thead><tr><th>Status</th><th>Feed</th>
<th>Updated</th><th>Last error</th></tr></thead><tbody id="pf"></tbody></table></div></div>
<script>
const D = __DATA__, NS = "http://www.w3.org/2000/svg", C = D.colors, OKE = "rgba(139,139,139,0.30)";
const svg = document.getElementById("svg"), wrap = document.getElementById("wrap");
const el = (t, a, p) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); (p||svg).appendChild(e); return e; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const defs = el("defs", {});
const g1 = el("linearGradient", {id:"fill", x1:0, y1:0, x2:0, y2:1}, defs);
el("stop", {offset:"0%", "stop-color":"#2b2b2b"}, g1); el("stop", {offset:"100%", "stop-color":"#171717"}, g1);
const g2 = el("linearGradient", {id:"gloss", x1:0, y1:0, x2:0, y2:1}, defs);
el("stop", {offset:"0%", "stop-color":"#ffffff", "stop-opacity":.10}, g2); el("stop", {offset:"100%", "stop-color":"#ffffff", "stop-opacity":0}, g2);
const fl = el("filter", {id:"sh", x:"-10%", y:"-20%", width:"120%", height:"150%"}, defs);
el("feDropShadow", {dx:0, dy:2, stdDeviation:2.5, "flood-color":"#000", "flood-opacity":.45}, fl);
const N = {}; D.nodes.forEach(n => N[n.id] = n);
const gE = el("g", {}), gN = el("g", {});
const E = {};
const base = s => s === "OK" ? OKE : C[s];
const order = {OK:0, Stale:1, Failing:2};
[...D.edges].sort((a,b) => order[a.status]-order[b.status]).forEach(e => {
  const a = N[e.a], b = N[e.b], x0 = a.x + a.w, y0 = a.y + a.h/2, x1 = b.x, y1 = b.y + b.h/2, xm = (x0+x1)/2;
  const ok = e.status === "OK";
  E[e.id] = el("path", {d:`M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1}`, class:"edge",
    stroke: base(e.status), "stroke-width": ok ? 1.1 : 2.2}, gE);
});
D.headers.forEach(h => el("text", {x:h.x, y:h.y, class:"hdr"}).textContent = h.text);
D.cats.forEach(c => el("text", {x:c.x, y:c.y, class:"cat"}).textContent = c.text);
D.nodes.forEach(n => {
  const g = el("g", {class:"node", "data-id":n.id}, gN), c = C[n.status], tone = n.status === "OK" ? "#8b8b8b" : c;
  el("rect", {x:n.x, y:n.y, width:n.w, height:n.h, rx:9, fill:"url(#fill)", filter:"url(#sh)"}, g);
  el("rect", {x:n.x+1, y:n.y+1, width:n.w-2, height:Math.min(n.h/2, 16), rx:8, fill:"url(#gloss)"}, g);
  el("rect", {x:n.x+.75, y:n.y+.75, width:n.w-1.5, height:n.h-1.5, rx:8.5, fill:"none", stroke:c,
              "stroke-width": n.status === "OK" ? 1.2 : 1.8, "stroke-opacity": n.status === "OK" ? .8 : 1}, g);
  const L = n.x + 12;
  if (n.col === "provider") {
    const cy = n.y + n.h/2 + 4;
    el("text", {x:L, y:cy, class:"t"}, g).textContent = n.title;
    el("text", {x:n.x+n.w-10, y:cy, class:"c", fill:tone, "text-anchor":"end"}, g).textContent = n.count;
  } else {
    const lines = [[n.title, "t", null]].concat(n.sub ? [[n.sub, "s", null]] : [], [[n.count, "c", tone]]);
    const lh = 15, y0 = n.y + n.h/2 - (lines.length-1)*lh/2 + 4;
    lines.forEach(([t, k, f], i) => { const a = {x:L, y:y0 + i*lh, class:k}; if (f) a.fill = f; el("text", a, g).textContent = t; });
  }
  g.addEventListener("mouseenter", () => hover(n.id));
  g.addEventListener("mouseleave", () => hover(null));
  g.addEventListener("click", ev => { if (!moved) open(n); });
});
function hover(id) {
  svg.classList.toggle("hovering", !!id);
  Object.values(E).forEach(p => { p.classList.remove("on"); });
  document.querySelectorAll(".node").forEach(g => g.classList.toggle("off", !!id && g.dataset.id !== id));
  if (!id) { Object.entries(E).forEach(([k,p]) => p.style.stroke = ""); return; }
  const on = new Set([id]);
  Object.entries(D.trace[id] || {}).forEach(([k, s]) => {
    const p = E[k]; if (!p) return; p.classList.add("on"); p.style.setProperty("stroke", C[s], "important");
    k.split("|").forEach(x => on.add(x));
  });
  document.querySelectorAll(".node").forEach(g => g.classList.toggle("off", !on.has(g.dataset.id)));
}
// panel
const P = document.getElementById("panel");
function open(n) {
  document.getElementById("pt").textContent = n.title + (n.sub ? " · " + n.sub : "");
  const pc = document.getElementById("pc"); pc.textContent = n.feeds.length + (n.feeds.length === 1 ? " feed: " : " feeds: ") + n.count;
  pc.style.color = n.status === "OK" ? "#8b8b8b" : C[n.status];
  document.getElementById("pm").innerHTML = n.detail;
  document.getElementById("pf").innerHTML = n.feeds.map(([s, src, up, err]) =>
    `<tr><td><span class="dot" style="background:${C[s]}"></span>${s}</td><td>${esc(src)}</td><td>${esc(up)}</td>` +
    `<td class="e">${esc(err)}</td></tr>`).join("");
  P.classList.add("open");
}
document.getElementById("px").onclick = () => P.classList.remove("open");
// zoom and drag
let scale = 1;
const zs = document.getElementById("zs"), steps = [...zs.options].map(o => +o.value);
function zoom(s, cx, cy) {
  const r = wrap.getBoundingClientRect(); cx = cx ?? r.width/2; cy = cy ?? r.height/2;
  const fx = (wrap.scrollLeft + cx) / scale, fy = (wrap.scrollTop + cy) / scale;
  scale = s; zs.value = String(s);
  svg.setAttribute("width", D.width * s); svg.setAttribute("height", D.height * s);
  wrap.scrollLeft = fx * s - cx; wrap.scrollTop = fy * s - cy;
}
svg.setAttribute("viewBox", `0 0 ${D.width} ${D.height}`);
zoom(1);
zs.onchange = () => zoom(+zs.value);
document.getElementById("zi").onclick = () => zoom(steps.find(v => v > scale) ?? scale);
document.getElementById("zo").onclick = () => zoom([...steps].reverse().find(v => v < scale) ?? scale);
let drag = null, moved = false;
wrap.addEventListener("mousedown", e => { drag = {x:e.clientX, y:e.clientY, l:wrap.scrollLeft, t:wrap.scrollTop}; moved = false; });
window.addEventListener("mousemove", e => {
  if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) { moved = true; wrap.classList.add("drag"); }
  wrap.scrollLeft = drag.l - dx; wrap.scrollTop = drag.t - dy;
});
window.addEventListener("mouseup", () => { drag = null; wrap.classList.remove("drag"); setTimeout(() => moved = false, 0); });
</script></body></html>"""


def page(h: pd.DataFrame) -> tuple[str, int]:
    d = model(h)
    view_h = int(d["height"]) + 8
    opts = "".join(f'<option value="{v}"{" selected" if v == 1 else ""}>{int(v * 100)}%</option>'
                   for v in (0.5, 0.75, 1, 1.25, 1.5, 2))
    out = (PAGE.replace("__DATA__", json.dumps(d).replace("</", "<\\/")).replace("__ACCENT__", ACCENT)
               .replace("__VIEWH__", str(view_h)).replace("__OPTS__", opts))
    return out, view_h


def render(h: pd.DataFrame) -> None:
    st.markdown("##### How the data flows")
    st.caption("Every source the dashboard uses, left to right: who serves it, which recorder job fetches it and "
               "how often, where it is stored, and which tabs read it. Hover a box to light up the lines its feeds "
               "travel; click it for details and its feeds. Zoom with the control at the top right and drag to move.")
    st.markdown(f"<div style='font-size:0.8rem;color:{MUTED};margin:-4px 0 4px 0'>{LEGEND}</div>",
                unsafe_allow_html=True)
    doc, view_h = page(h)
    components.html(doc, height=view_h, scrolling=False)
