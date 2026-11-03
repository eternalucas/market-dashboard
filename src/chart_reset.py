"""A small "Reset view" button under every chart.

Plotly keeps a chart's zoom and pan across reruns as long as the figure's
layout.uirevision stays the same. The button bumps that chart's revision,
so the next draw discards the zoom and shows the chart's default view.
Charts are numbered in the order they're drawn on each run (app.py resets
the counter at the top of the script), which gives each a stable key.
"""
from __future__ import annotations

import streamlit as st

STATE = "_chart_rev"
CSS = """
<style>
/* Zero-height holder under each chart: the icon floats at the right end of the
   chart's source line, which follows it. */
div[class*="st-key-chart-reset-"] { height: 0; min-height: 0; margin: 0; padding: 0; gap: 0; position: relative; z-index: 5;
    margin-bottom: -1rem; }
div[class*="st-key-chart-reset-"] { display: flex !important; flex-direction: row !important; justify-content: flex-end !important;
    overflow: visible; width: 100% !important; }
div[class*="st-key-chart-reset-"] > div { width: auto !important; margin-top: -0.2rem; }
div[class*="st-key-chart-reset-"] button { min-height: 0; height: 1.2rem; padding: 0 0.15rem; line-height: 1;
    font-size: 0.95rem; color: #5f5f5f; background: transparent; border: 0; }
div[class*="st-key-chart-reset-"] button:hover { color: #f7931a; }
.gn-meta, [data-testid="stCaptionContainer"] { padding-right: 1.4rem; }
</style>
"""
_counter = {"n": 0}


def start_run() -> None:
    _counter["n"] = 0
    st.markdown(CSS, unsafe_allow_html=True)


def _bump(key: str) -> None:
    revs = st.session_state.setdefault(STATE, {})
    revs[key] = revs.get(key, 0) + 1


def wrap(plotly_chart):
    def plotly_chart_with_reset(fig, *a, **k):
        _counter["n"] += 1
        key = k.get("key") or f"pc-{_counter['n']}"
        rev = st.session_state.get(STATE, {}).get(key, 0)
        try:
            fig.update_layout(uirevision=f"{key}-{rev}")
        except AttributeError:      # not a Plotly figure (e.g. a dict spec): draw it as is
            pass
        out = plotly_chart(fig, *a, **k)
        st.button("↺", key=f"chart-reset-{key}", type="tertiary", on_click=_bump, args=(key,),
                  help="Reset view: back to the chart's default zoom")
        return out
    return plotly_chart_with_reset
