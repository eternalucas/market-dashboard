"""Billions as B, never G."""
import numpy as np
import plotly.graph_objects as go

from src.numfmt import fix_si, human


def test_human():
    assert human(250_312_000_000, "$,.4s") == "$250.3B"
    assert human(1_234_567, ",.3s") == "1.23M"
    assert human(-5_400, "$.2s") == "-$5.4K"
    assert human(2.5e12, "$.3s") == "$2.5T"
    assert human(512, ".3s") == "512"


def test_scatter_hover_and_axis():
    fig = go.Figure(go.Scatter(x=[1, 2], y=[2.5e11, 3e9], hovertemplate="$%{y:,.4s}<extra></extra>"))
    fig.update_yaxes(tickformat="$~s")
    fix_si(fig)
    tr = fig.data[0]
    assert "customdata[0]" in tr.hovertemplate and "s}" not in tr.hovertemplate
    assert [c[0] for c in tr.customdata] == ["250B", "3B"]   # the $ sits outside the field in the template
    assert fig.layout.yaxis.tickformat == "" and fig.layout.yaxis.exponentformat == "B"


def test_existing_customdata_kept():
    fig = go.Figure(go.Scatter(x=[1, 2], y=[1, 2], customdata=[5e9, 7e6],
                               hovertemplate="%{x}: %{customdata:$,.3s} at %{y:.2f}"))
    fix_si(fig)
    tr = fig.data[0]
    assert tr.hovertemplate == "%{x}: %{customdata[1]} at %{y:.2f}"
    assert tr.customdata[0][1] == "$5B" and tr.customdata[1][1] == "$7M"


def test_heatmap_z_and_plain_customdata():
    z = np.array([[1e9, 2e6], [3e3, 0.0]])
    fig = go.Figure(go.Heatmap(z=z, customdata=z / 2, colorbar=dict(tickformat="$.2s"),
                               hovertemplate="%{z:$,.3s} (of which %{customdata:$,.3s})"))
    fix_si(fig)
    tr = fig.data[0]
    assert tr.hovertemplate == "%{customdata[1]} (of which %{customdata[2]})"
    assert tr.customdata[0][0][1] == "$1B" and tr.customdata[0][0][2] == "$500M"
    assert tr.colorbar.exponentformat == "B"


def test_untouched_when_no_si():
    fig = go.Figure(go.Scatter(x=[1], y=[1], hovertemplate="%{y:.2f}%"))
    fix_si(fig)
    assert fig.data[0].hovertemplate == "%{y:.2f}%" and fig.data[0].customdata is None
