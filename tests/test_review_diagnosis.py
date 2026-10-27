"""Trader diagnosis (Trade Review Phase 2): excursions, entry context without
look-ahead, fingerprint, execution, risk, sizing, edge and strengths."""
import numpy as np
import pandas as pd
import pytest

from src.review import diagnosis as dg
from src.review import market as mk
from src.review import store

NOW = pd.Timestamp("2026-03-01 00:00", tz="UTC")


def bars(start, n, freq, closes, spread=0.0):
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    c = np.asarray(closes, float) if np.ndim(closes) else np.full(n, float(closes))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"o": o, "h": np.maximum(o, c) * (1 + spread), "l": np.minimum(o, c) * (1 - spread),
                         "c": c, "v": 1.0}, index=idx)


def trip(coin="BTC", side="Long", open_ts="2026-02-27 10:00", close_ts="2026-02-27 12:00", entry=100.0,
         exit_=105.0, qty=1.0, net=None, adds=0, fills=2, liquidated=False, fees=0.0):
    o, c = pd.Timestamp(open_ts, tz="UTC"), pd.Timestamp(close_ts, tz="UTC") if close_ts else pd.NaT
    s = 1 if side == "Long" else -1
    pnl = (exit_ - entry) * qty * s
    return {"trip_id": f"{coin}:{open_ts}", "coin": coin, "side": side, "open_ts": o, "close_ts": c,
            "entry_qty": qty, "avg_entry": entry, "avg_exit": exit_, "max_qty": qty, "max_notional": qty * entry,
            "realized_pnl": pnl, "fees": fees, "funding": 0.0, "net": pnl - fees if net is None else net,
            "fills": fills, "taker_fills": fills, "adds": adds, "added_while_losing": False,
            "liquidated": liquidated, "is_open": close_ts is None, "exit_unobserved": False,
            "hold_hours": (c - o).total_seconds() / 3600 if close_ts else np.nan,
            "return_on_notional": (pnl - fees if net is None else net) / (qty * entry), "taker_share": 1.0}


def T(rows):
    df = pd.DataFrame(rows)
    df["close_ts"] = pd.to_datetime(df["close_ts"], utc=True)
    return df


def test_excursions_use_the_finest_interval_and_are_signed_by_side():
    p = mk.Prices()
    one_m = bars("2026-02-27 09:00", 240, "1min", 100.0)
    one_m.loc["2026-02-27 10:30", ["h", "l"]] = [110.0, 95.0]     # inside the trip
    one_m.loc["2026-02-27 09:30", ["h", "l"]] = [150.0, 50.0]     # before the trip: must be ignored
    p.bars[("BTC", "1m")] = one_m
    p.bars[("BTC", "1h")] = bars("2026-02-20", 24 * 9, "1h", 100.0, spread=0.3)   # coarser, wider
    t = dg.enrich(T([trip(side="Long"), trip(side="Short", entry=100, exit_=97)]), p, now=NOW)
    long_, short = t.iloc[0], t.iloc[1]
    assert long_["excursion_interval"] == "1m"
    assert long_["mfe_pct"] == pytest.approx(0.10) and long_["mae_pct"] == pytest.approx(-0.05)
    assert short["mfe_pct"] == pytest.approx(0.05) and short["mae_pct"] == pytest.approx(-0.10)


def test_entry_context_ignores_the_bar_the_entry_is_in():
    """A huge move in the hour that contains the entry must not change the
    'prior 24h' context: only bars that closed before the entry count."""
    closes = np.linspace(100, 110, 24 * 40)                       # steady uptrend for 40 days
    b = bars("2026-01-20", len(closes), "1h", closes)
    p = mk.Prices()
    p.bars[("BTC", "1h")] = b
    entry = b.index[-50] + pd.Timedelta(minutes=30)
    t1 = dg.enrich(T([trip(open_ts=str(entry.tz_convert(None)), close_ts=str((entry + pd.Timedelta(hours=1)).tz_convert(None)))]), p, now=NOW)
    b2 = b.copy()
    b2.loc[b.index[-50]:, "c"] *= 0.5                              # crash from the entry's own bar onwards
    p.bars[("BTC", "1h")] = b2
    t2 = dg.enrich(T([trip(open_ts=str(entry.tz_convert(None)), close_ts=str((entry + pd.Timedelta(hours=1)).tz_convert(None)))]), p, now=NOW)
    assert t1["prior_ret"].iloc[0] == pytest.approx(t2["prior_ret"].iloc[0])
    assert t1["prior_ret"].iloc[0] > 0
    assert t1["context"].iloc[0] == "With the move"


def test_funding_at_entry_is_the_last_settled_rate():
    p = mk.Prices()
    p.funding["BTC"] = pd.Series([0.0001, 0.0005], index=pd.to_datetime(["2026-02-27 09:00", "2026-02-27 11:00"], utc=True))
    assert p.funding_at("BTC", pd.Timestamp("2026-02-27 10:59", tz="UTC")) == 0.0001
    assert p.funding_at("BTC", pd.Timestamp("2026-02-27 11:00", tz="UTC")) == 0.0005
    assert np.isnan(p.funding_at("BTC", pd.Timestamp("2026-02-27 08:00", tz="UTC")))
    t = dg.enrich(T([trip(side="Short")]), p, now=NOW)
    assert t["funding_cost_at_entry"].iloc[0] == pytest.approx(-0.0001 * 24 * 365)   # shorts receive


def test_fingerprint_shares_and_scaling():
    rows = [trip(coin="BTC", side="Long", entry=100, qty=3, adds=1, fills=4) for _ in range(3)] + \
           [trip(coin="ETH", side="Short", entry=100, qty=1)]
    fp = dg.fingerprint(dg.enrich(T(rows), mk.Prices(), now=NOW))
    assert fp["top_asset"] == "BTC" and fp["top_asset_share"] == pytest.approx(0.9)
    assert fp["long_share_trips"] == 0.75 and fp["long_share_notional"] == pytest.approx(0.9)
    assert fp["scale_in_share"] == 0.75
    assert fp["scale_out_share"] == 0.75          # 4 fills = open, add, partial exit, final exit
    assert "mostly long" in dg.describe(fp)


def test_tilt_after_losses_is_measured():
    rows, t0 = [], pd.Timestamp("2026-02-01 00:00")
    for i in range(30):
        loss_before = i % 2 == 1                  # odd trips follow a loss
        o = t0 + pd.Timedelta(hours=3 * i)
        win = i % 2 == 1                          # trips alternate loss, win, loss...
        rows.append(trip(open_ts=str(o), close_ts=str(o + pd.Timedelta(hours=1)), entry=100,
                         exit_=110 if win else 95, qty=4 if loss_before else 1))
    sz = dg.sizing(dg.enrich(T(rows), mk.Prices(), now=NOW))
    assert sz["tilt_ratio"] > 2
    assert sz["after"].loc["After a loss", "median_size"] == 400


def test_losers_held_past_the_winners_cut_and_concurrency():
    p = mk.Prices()
    b = bars("2026-02-26", 3 * 24 * 60, "1min", 100.0)
    p.bars[("BTC", "1m")] = b
    rows = []
    for i in range(10):                            # winners dip at most 1%
        o = pd.Timestamp("2026-02-26 01:00") + pd.Timedelta(hours=i)
        b.loc[pd.Timestamp(o, tz="UTC") + pd.Timedelta(minutes=10), "l"] = 99.0
        rows.append(trip(open_ts=str(o), close_ts=str(o + pd.Timedelta(minutes=30)), exit_=101))
    for i in range(4):                             # losers ride 6% against
        o = pd.Timestamp("2026-02-27 01:00") + pd.Timedelta(hours=i)
        b.loc[pd.Timestamp(o, tz="UTC") + pd.Timedelta(minutes=10), "l"] = 94.0
        rows.append(trip(open_ts=str(o), close_ts=str(o + pd.Timedelta(hours=2)), exit_=95))
    rk = dg.risk(dg.enrich(T(rows), p, now=NOW), now=NOW)
    assert rk["winner_mae_p10"] == pytest.approx(-0.01)
    assert rk["losers_past_winner_cut"] == 4 and rk["losers_past_winner_cut_pnl"] == pytest.approx(-20)
    assert rk["max_concurrent"] == 2               # the 2h losers overlap
    assert rk["liquidations"] == 0


def test_edge_tables_and_top5_share():
    rows = [trip(exit_=100 + k, entry=100) for k in range(1, 11)] + [trip(exit_=99, entry=100) for _ in range(5)]
    ed = dg.edge(dg.enrich(T(rows), mk.Prices(), now=NOW))
    assert ed["overall"]["trades"] == 15
    assert ed["top5_share"] == pytest.approx((10 + 9 + 8 + 7 + 6) / (55 - 5))
    assert ed["tables"]["Side"].loc["Long", "share_of_pnl"] == pytest.approx(1.0)


def test_strengths_only_claim_what_the_data_shows():
    good = [trip(exit_=106, entry=100) for _ in range(30)] + [trip(exit_=98, entry=100) for _ in range(20)]
    res = dg.run(T(good), pd.DataFrame(), pd.DataFrame(), mk.Prices(), now=NOW)
    titles = [s["title"] for s in res["strengths"]]
    assert "Positive edge overall" in titles and "Never liquidated" in titles
    bad = [trip(exit_=99, entry=100) for _ in range(30)] + [trip(exit_=101, entry=100) for _ in range(5)]
    titles = [s["title"] for s in dg.run(T(bad), pd.DataFrame(), pd.DataFrame(), mk.Prices(), now=NOW)["strengths"]]
    assert "Positive edge overall" not in titles


class FakeAdapter:
    name = "hyperliquid"

    def __init__(self):
        self.calls = []

    def candles(self, coin, interval, s, e):
        self.calls.append((coin, interval, s, e))
        step = mk.INTERVAL_TD[interval]
        idx = pd.date_range(pd.Timestamp(s, unit="ms", tz="UTC").ceil(step), pd.Timestamp(e, unit="ms", tz="UTC"), freq=step)
        return pd.DataFrame({"venue": "hyperliquid", "coin": coin, "interval": interval, "ts": idx,
                             "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0})


def test_plan_picks_interval_by_age_and_fetches_only_gaps(tmp_path):
    rows = [trip(open_ts="2026-02-28 12:00", close_ts="2026-02-28 13:00"),     # 12h old -> 1m
            trip(open_ts="2026-02-10 12:00", close_ts="2026-02-10 13:00"),     # 19 days -> 15m
            trip(open_ts="2025-12-01 12:00", close_ts="2025-12-01 13:00")]     # 3 months -> 1h
    need = mk.plan(T(rows), NOW)
    ivs = {iv for _, iv, _, _ in need}
    assert ivs == {"1m", "15m", "1h"}
    h = [x for x in need if x[1] == "1h"][0]
    assert h[2] <= pd.Timestamp("2025-11-01 12:00", tz="UTC")                   # 30 days of context
    con = store.connect(tmp_path / "t.db")
    ad = FakeAdapter()
    assert len(mk.missing(con, need, now=NOW)) == 3
    mk.ensure_candles(con, ad, need, now=NOW)
    assert mk.missing(con, need, now=NOW) == []
    first = len(ad.calls)
    assert first == 3
    mk.ensure_candles(con, ad, need, now=NOW)
    assert len(ad.calls) == first                                              # all stored: no new calls
    p = mk.Prices.load(con, ["BTC"])
    assert p.finest("BTC", pd.Timestamp("2026-02-28 12:00", tz="UTC"), pd.Timestamp("2026-02-28 13:00", tz="UTC"))[0] == "1m"
    assert p.finest("BTC", pd.Timestamp("2026-02-10 12:00", tz="UTC"), pd.Timestamp("2026-02-10 13:00", tz="UTC"))[0] == "15m"


def test_chosen_coins_cover_most_trips():
    rows = [trip(coin="BTC")] * 90 + [trip(coin="ETH")] * 6 + [trip(coin="DOGE")] * 4
    keep, rest = mk.chosen_coins(T(rows), max_coins=12, cover=0.95)
    assert keep == ["BTC", "ETH"] and rest == ["DOGE"]


def test_a_range_the_venue_does_not_have_is_not_asked_for_again(tmp_path):
    """A coin listed after the requested start: the venue returns bars from
    its listing only; the earlier part must not be re-requested every time."""
    class LateListing(FakeAdapter):
        def candles(self, coin, interval, s, e):
            return super().candles(coin, interval, max(s, int(pd.Timestamp("2026-02-20", tz="UTC").timestamp() * 1000)), e)
    con = store.connect(tmp_path / "t.db")
    ad = LateListing()
    need = [("NEW", "1h", pd.Timestamp("2026-01-20", tz="UTC"), pd.Timestamp("2026-02-28 23:00", tz="UTC"))]
    mk.ensure_candles(con, ad, need, now=NOW)
    assert mk.missing(con, need, now=NOW) == []
    mk.ensure_candles(con, ad, need, now=NOW)
    assert len(ad.calls) == 1


def test_coarse_candles_are_not_used_for_short_trips():
    p = mk.Prices()
    p.bars[("BTC", "1d")] = bars("2026-01-01", 59, "1D", 100.0, spread=0.05)
    two_hours = p.finest("BTC", pd.Timestamp("2026-02-27 10:00", tz="UTC"), pd.Timestamp("2026-02-27 12:00", tz="UTC"))
    four_days = p.finest("BTC", pd.Timestamp("2026-02-20 10:00", tz="UTC"), pd.Timestamp("2026-02-24 12:00", tz="UTC"))
    assert two_hours[0] is None and four_days[0] == "1d"


def test_deep_losers_are_measured_from_the_first_entry():
    """Averaging down moves the average entry; a loser that went 10% against
    its first fill must still count as deep."""
    p = mk.Prices()
    b = bars("2026-02-26", 3 * 24 * 60, "1min", 100.0)
    p.bars[("BTC", "1m")] = b
    b.loc[pd.Timestamp("2026-02-27 01:10", tz="UTC"), "l"] = 90.0
    row = trip(open_ts="2026-02-27 01:00", close_ts="2026-02-27 02:00", entry=95, exit_=93, adds=1, fills=3)
    row["first_px"] = 100.0
    t = dg.enrich(T([row]), p, now=NOW)
    assert t["mae_first_pct"].iloc[0] == pytest.approx(-0.10)
    assert t["mae_pct"].iloc[0] == pytest.approx(90 / 95 - 1)


def test_strengths_never_contradict_the_mistakes():
    sz = {"tilt_ratio": 0.9}
    ed = {"overall": {"trades": 50, "expectancy": -1, "profit_factor": 0.9, "payoff": 1.0, "win_rate": 0.4}}
    errs = pd.DataFrame({"pattern": ["Sizing up after a loss"]})
    assert "No revenge sizing" in [s["title"] for s in dg.strengths({}, {}, {}, sz, ed)]
    assert "No revenge sizing" not in [s["title"] for s in dg.strengths({}, {}, {}, sz, ed, errors=errs)]
