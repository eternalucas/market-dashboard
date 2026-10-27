"""Month calendar of macro events for the Macro events tab (HTML grid)."""
from __future__ import annotations

import calendar
import html

import pandas as pd

from . import charts

EVENT_COLORS = {"CPI": "#e34948", "PPI": "#eb6834", "Payrolls (NFP)": "#2a78d6", "Jobless claims": "#8b8b8b",
                "FOMC decision": "#eda100", "BoE decision": "#1baf7a", "BoJ decision": "#e87ba4",
                "PBoC LPR": "#6250d6"}

CSS = f"""
<style>
.mcal {{ display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; font-family: {charts.FONT};
    margin-bottom: 32px; }}
.mcal .h {{ color: {charts.AXIS}; font-size: 12px; font-weight: 600; padding: 0 8px; }}
.mcal .c {{ background: {charts.BG}; border: 1px solid {charts.BORDER}; border-radius: 6px; min-height: 92px;
    padding: 6px 8px; min-width: 0; }}
.mcal .c.out {{ background: transparent; border-color: transparent; }}
.mcal .c.past {{ opacity: 0.45; }}
.mcal .c.today {{ border-color: #f7931a; }}
.mcal .d {{ color: {charts.TEXT}; font-size: 12px; font-weight: 600; margin-bottom: 4px; }}
.mcal .c.today .d {{ color: #f7931a; }}
.mcal .ev {{ display: block; font-size: 11.5px; line-height: 16px; color: {charts.TEXT}; border-radius: 4px;
    padding: 1px 6px; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    border-left: 3px solid var(--c); background: rgba(255,255,255,0.05); }}
@media (max-width: 640px) {{ .mcal .ev {{ font-size: 10px; padding: 1px 3px; }} .mcal .c {{ min-height: 64px; padding: 4px; }} }}
</style>
"""


def month_html(events: pd.DataFrame, year: int, month: int, today: pd.Timestamp) -> str:
    """events: columns date (Timestamp) and event. Weeks start on Sunday."""
    by_day: dict[int, list[str]] = {}
    m = events[(events["date"].dt.year == year) & (events["date"].dt.month == month)]
    for d, e in zip(m["date"], m["event"]):
        by_day.setdefault(d.day, []).append(e)
    cells = [f'<div class="h">{d}</div>' for d in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")]
    for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, month):
        for day in week:
            if day == 0:
                cells.append('<div class="c out"></div>')
                continue
            date = pd.Timestamp(year=year, month=month, day=day)
            cls = "today" if date == today else ("past" if date < today else "")
            evs = "".join(f'<span class="ev" style="--c:{EVENT_COLORS.get(e, "#8b8b8b")}" title="{html.escape(e)}">'
                          f'{html.escape(e)}</span>' for e in sorted(set(by_day.get(day, []))))
            cells.append(f'<div class="c {cls}"><div class="d">{day}</div>{evs}</div>')
    return f'{CSS}<div class="mcal">{"".join(cells)}</div>'
