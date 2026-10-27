"""Billions as "B", never Plotly's SI "G".

Plotly formats numbers with d3-format, whose SI suffix ('s') writes 10^9 as
"G" (giga) and has no option to change it. fix_si(fig) rewrites every
SI-formatted value in a figure before it is drawn:

- hover templates: each %{y:$,.4s}-style field (x, y, z or customdata) is
  replaced by a string formatted here with T / B / M / K, carried in the
  trace's customdata;
- axis and colour-bar tick labels with an SI tickformat switch to Plotly's
  own exponent labels with exponentformat "B" (1.2B, 300M, 20k).

app.py wraps st.plotly_chart with it, so every chart in the dashboard is
covered, including ones added later.
"""
from __future__ import annotations

import re

import numpy as np

FIELD = re.compile(r"%\{(x|y|z|customdata)(\[\d+\])?(?::([^}]*))?\}")
SUFFIXES = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K"))


def human(v, fmt: str = "") -> str:
    """Format like d3's '~s' but with B for billions: '$,.4s' -> $1.234B."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(v):
        return ""
    m = re.search(r"\.(\d+)", fmt)
    sig = int(m.group(1)) if m else 3
    dollar = "$" in fmt
    a = abs(v)
    div, suf = next(((d, s) for d, s in SUFFIXES if a >= d), (1, ""))
    x = a / div
    digits = max(sig - (len(str(int(x))) if x >= 1 else 1), 0)
    txt = f"{x:,.{digits}f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return ("-" if v < 0 else "") + ("$" if dollar else "") + txt + suf


def _is_si(fmt: str | None) -> bool:
    return bool(fmt) and fmt.rstrip().endswith("s") and not fmt.startswith("|")   # "|..." is a date format


def _values(trace, field: str, idx: str | None):
    v = getattr(trace, field, None)
    if v is None:
        return None
    arr = np.asarray(v, dtype=object)
    if idx is not None:
        i = int(idx.strip("[]"))
        arr = np.vectorize(lambda c: c[i] if isinstance(c, (list, tuple, np.ndarray)) else c, otypes=[object])(arr)
    return arr


def _fix_trace(tr) -> None:
    ht = getattr(tr, "hovertemplate", None)
    if not isinstance(ht, str):
        return
    matches = [m for m in FIELD.finditer(ht) if _is_si(m.group(3))]
    if not matches:
        return
    shape_src = None
    for f in ("z", "y", "x"):
        if getattr(tr, f, None) is not None:
            shape_src = np.asarray(getattr(tr, f), dtype=object)
            if f == "z" or shape_src.ndim == 1:
                break
    if shape_src is None:
        return
    shape = shape_src.shape
    # existing customdata, as a list per point
    cd = getattr(tr, "customdata", None)
    base = None
    if cd is not None:
        base = np.asarray(cd, dtype=object)
        if base.shape == shape:                      # one value per point
            base = base[..., None]
    n_base = 0 if base is None else base.shape[-1]
    extra = []
    for m in matches:
        vals = _values(tr, m.group(1), m.group(2))
        if vals is None:
            continue
        vals = np.broadcast_to(vals, shape) if vals.shape != shape else vals
        extra.append(np.vectorize(lambda v, f=m.group(3): human(v, f), otypes=[object])(vals))
    if not extra:
        return
    stacked = np.stack(extra, axis=-1)
    new_cd = stacked if base is None else np.concatenate([base.astype(object), stacked], axis=-1)

    k = [n_base]

    def repl(m):
        if _is_si(m.group(3)):
            j = k[0]
            k[0] += 1
            return f"%{{customdata[{j}]}}"
        if m.group(1) == "customdata" and m.group(2) is None and cd is not None and n_base == 1:
            return "%{customdata[0]" + (f":{m.group(3)}" if m.group(3) else "") + "}"
        return m.group(0)
    tr.hovertemplate = FIELD.sub(repl, ht)
    tr.customdata = new_cd.tolist()


def _fix_axis(ax) -> None:
    tf = getattr(ax, "tickformat", None)
    if isinstance(tf, str) and _is_si(tf):
        ax.tickformat = ""
        ax.exponentformat = "B"
        if "$" in tf and not ax.tickprefix:
            ax.tickprefix = "$"


def fix_si(fig):
    """Rewrite SI ('G') number formats in a Plotly figure in place; returns it."""
    try:
        for tr in fig.data:
            _fix_trace(tr)
            cb = getattr(tr, "colorbar", None)
            if cb is not None:
                _fix_axis(cb)
        lay = fig.layout
        for name in list(lay.to_plotly_json()):
            if name.startswith(("xaxis", "yaxis")):
                _fix_axis(lay[name])
    except Exception:  # noqa: BLE001 - formatting must never break a chart
        pass
    return fig
