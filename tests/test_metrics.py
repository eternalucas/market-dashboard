"""Formula tests: each metric on synthetic inputs whose correct answer is
known in closed form."""
import json
import math

import numpy as np
import pandas as pd
import pytest

from src import storage
from src.metrics import Ctx
from src.metrics import futures as fm
from src.metrics import onchain as oc
from src.metrics import options as om
from src.metrics import spot as sm


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    con = storage.connect(tmp_path / "m.db")
    return Ctx(con)


def days(n, start="2020-01-01"):
    return pd.date_range(start, periods=n, freq="D", tz="UTC")


def put(ctx, name, s):
    storage.save_series(ctx.con, name, s)
    ctx._cache.pop(name, None)


# ---------------------------------------------------------------- spot
def test_mayer_and_pi(ctx):
    p = pd.Series(np.arange(1, 801, dtype=float), index=days(800))
    put(ctx, "cm:PriceUSD", p)
    mm = sm.mayer_multiple(ctx)
    assert math.isnan(mm.iloc[198]) and mm.iloc[199] == pytest.approx(200 / np.mean(np.arange(1, 201)))
    pi = sm.pi_cycle(ctx)
    assert pi["sma350x2"].iloc[-1] == pytest.approx(2 * np.mean(np.arange(451, 801)))


def test_realized_vol_constant_log_returns(ctx):
    lr = np.tile([0.01, -0.01], 200)
    p = pd.Series(100 * np.exp(np.cumsum(lr)), index=days(400))
    put(ctx, "cm:PriceUSD", p)
    rv = sm.realized_volatility(ctx)
    expected = np.std([0.01, -0.01] * 15, ddof=1) * np.sqrt(365)
    assert rv["1m"].iloc[-1] == pytest.approx(expected, rel=0.05)


def test_drawdown_and_cycle_aths(ctx):
    v = [1, 10, 2, 2.4, 20, 20, 4, 30, 25]   # 10 -> 2 is -80% (cycle ATH); 20 -> 4 is -80%; 30 is the latest ATH
    put(ctx, "cm:PriceUSD", pd.Series(v, index=days(len(v)), dtype=float))
    assert sm.drawdown_from_ath(ctx).iloc[2] == pytest.approx(-0.8)
    aths = sm.cycle_aths(ctx)
    assert [a.day for a in aths] == [2, 5, 8]


def test_regional_price_split(ctx):
    idx = pd.date_range("2026-01-05 00:00", periods=49, freq="h", tz="UTC")   # winter: no DST
    put(ctx, "cb1h:BTC-USD:close", pd.Series(np.arange(49, dtype=float), index=idx))   # +1 USD every hour
    rp = sm.regional_price(ctx)
    # every hour belongs to at least one region except 20:00-24:00 Berlin... total change is preserved where covered
    covered = rp.iloc[-1].sum()
    assert covered <= 48 and covered > 30
    # Asia works 00:00-12:00 UTC: 12 hours a day; on day 1 it gets every Asia-only hour fully
    assert rp["Asia"].iloc[-1] > 0 and rp["US"].iloc[-1] > 0


def test_power_law_matches_published_formula(ctx):
    put(ctx, "cm:PriceUSD", pd.Series([1.0, 1.0], index=days(2, "2024-03-21")))
    pl = sm.power_law(ctx)
    d = (pd.Timestamp("2024-03-21", tz="UTC") - pd.Timestamp("2009-01-03", tz="UTC")).days
    assert pl.iloc[0] == pytest.approx(math.exp(5.71 * math.log(d) - 38.16))


# ---------------------------------------------------------------- futures
def test_funding_oi_weighted_with_hourly_scaling(ctx, monkeypatch):
    meta = [{"symbol": "A", "asset": "BTC", "kind": "perp", "exchange": "Binance", "margined": "STABLE"},
            {"symbol": "B", "asset": "BTC", "kind": "perp", "exchange": "Hyperliquid", "margined": "STABLE"}]
    monkeypatch.setattr(fm, "_perps", lambda asset="BTC": meta)
    i = days(3)
    put(ctx, "czd:A:fr", pd.Series(0.01, index=i))
    put(ctx, "czd:B:fr", pd.Series(0.001, index=i))       # hourly -> 0.008 per 8h
    put(ctx, "czd:A:oi_usd", pd.Series(3e9, index=i))
    put(ctx, "czd:B:oi_usd", pd.Series(1e9, index=i))
    assert fm.funding_rate(ctx).iloc[-1] == pytest.approx((0.01 * 3 + 0.008 * 1) / 4)


def test_annualized_basis():
    assert fm.annualized_basis(101.0, 100.0, 36.5) == pytest.approx(0.10)


def test_position_openings_classification(ctx, monkeypatch):
    i = days(40)
    put(ctx, "cm:PriceUSD", pd.Series(np.linspace(100, 140, 40), index=i))      # price up
    monkeypatch.setattr(fm, "open_interest", lambda c, asset="BTC": pd.Series(np.linspace(10, 5, 40), index=i))  # OI down
    po = fm.position_openings_closures(ctx)
    last = po.iloc[-1]
    assert last["short_closure"] > 0 and last["long_opening"] == 0 and last["long_closure"] == 0


def test_rolling_basis_interpolates_to_90_days(ctx, monkeypatch):
    t = pd.Timestamp("2026-01-15", tz="UTC")
    put(ctx, "bq:BTC:index", pd.Series([100.0], index=[t]))
    # Mar 27 2026 (last Fri of Mar) and Jun 26 2026 expiries
    put(ctx, "bq:BTC:current_quarter", pd.Series([101.0], index=[t]))
    put(ctx, "bq:BTC:next_quarter", pd.Series([103.0], index=[t]))
    b = fm.rolling_basis_3m(ctx).iloc[0]
    d1 = (pd.Timestamp("2026-03-27 08:00", tz="UTC") - (t + pd.Timedelta(days=1))).total_seconds() / 86400
    d2 = (pd.Timestamp("2026-06-26 08:00", tz="UTC") - (t + pd.Timedelta(days=1))).total_seconds() / 86400
    b1, b2 = 0.01 * 365 / d1, 0.03 * 365 / d2
    assert b == pytest.approx(b1 + (90 - d1) / (d2 - d1) * (b2 - b1))


# ---------------------------------------------------------------- options
def _chain(F=100.0, iv=0.5, days_=30, ts="2026-10-21T00:00:00+00:00"):
    exp = (pd.Timestamp(ts) + pd.Timedelta(days=days_)).isoformat()
    rows = []
    for k in np.arange(50, 151, 5.0):
        for typ in "CP":
            rows.append({"venue": "deribit", "ts": ts, "expiry": exp, "strike": k, "type": typ, "iv": iv,
                         "oi": 10.0, "volume": 1.0, "mark_price": 0.0, "underlying": F, "bid": None, "ask": None,
                         "bid_iv": None, "ask_iv": None})
    return pd.DataFrame(rows)


def test_flat_smile_surface():
    m = om.snapshot_metrics(pd.concat([_chain(days_=30), _chain(days_=90)]))
    assert m["atm_iv:1m"] == pytest.approx(0.5, abs=1e-3)
    assert m["call_iv_d25:1m"] == pytest.approx(0.5, abs=1e-3)
    assert abs(m["skew25_norm:1m"]) < 1e-6
    assert m["upvol:1m"] == pytest.approx(m["downvol:1m"], rel=0.1)   # symmetric on a flat smile
    assert m["max_pain:all"] == 100.0                                # symmetric OI -> pain minimised at the forward


def test_constant_maturity_total_variance():
    per = pd.DataFrame({"t": [30 / 365, 90 / 365], "atm": [0.4, 0.6]})
    w = np.interp(60 / 365, per["t"], per["atm"] ** 2 * per["t"])
    assert om.constant_maturity(per, "atm", 60) == pytest.approx(math.sqrt(w / (60 / 365)))


def test_gex_sign_convention():
    df = om.prepare(_chain(), "deribit")
    g = om.gex_by_strike(df, "deribit")["gex_usd"]
    assert g.sum() == pytest.approx(0, abs=1e-6)          # equal call/put OI, same gamma -> nets to zero
    calls_only = df[df["type"] == "C"]
    assert om.gex_by_strike(calls_only, "deribit")["gex_usd"].sum() < 0   # dealers short calls -> negative


def test_itm_prob_sums_to_one():
    pc = om.itm_prob(100, 110, 0.5, 0.1, "C") + om.itm_prob(100, 110, 0.5, 0.1, "P")
    assert float(pc) == pytest.approx(1.0)


def test_bull_bear_index(ctx):
    rows = [("1", "C", "buy", 3.0), ("2", "P", "buy", 1.0), ("3", "C", "sell", 9.0)]
    df = pd.DataFrame([{"trade_id": i, "ts": "2026-10-21T01:00:00+00:00", "instrument": "x",
                        "expiry": "2026-11-27T08:00:00+00:00", "strike": 80000.0, "type": t, "direction": d,
                        "amount": a, "price": 0.01, "index_price": 80000.0, "iv": 0.5, "combo": 0} for i, t, d, a in rows])
    storage.save_frame(ctx.con, "option_trades", df)
    bbi = om.bull_bear_index(ctx)
    assert bbi["all"].iloc[-1] == pytest.approx((3 - 1) / (3 + 1))    # sells don't count


# ---------------------------------------------------------------- on-chain
def test_realized_and_mvrv_z(ctx):
    i = days(100)
    mc = pd.Series(np.linspace(100, 200, 100), index=i)
    mv = pd.Series(2.0, index=i)
    put(ctx, "cm:CapMrktCurUSD", mc)
    put(ctx, "cm:CapMVRVCur", mv)
    put(ctx, "cm:SplyCur", pd.Series(10.0, index=i))
    r = oc.realized(ctx)
    assert r["realized_cap"].iloc[-1] == 100 and r["realized_price"].iloc[-1] == 10 and r["nupl"].iloc[-1] == 0.5
    z = oc.mvrv_zscore(ctx)
    assert z.iloc[-1] == pytest.approx((200 - 100) / mc.std())


def test_puell_and_hash_ribbon(ctx):
    i = days(400)
    put(ctx, "cm:IssTotUSD", pd.Series(1.0, index=i))
    assert oc.puell_multiple(ctx).iloc[-1] == pytest.approx(1.0)
    put(ctx, "cm:PriceUSD", pd.Series(1.0, index=i))
    put(ctx, "mp:difficulty", pd.Series([1e12], index=i[:1]))
    put(ctx, "cm:BlkCnt", pd.Series(np.r_[np.full(300, 144.0), np.full(100, 72.0)], index=i))   # hash rate halves
    hrb = oc.hash_ribbon(ctx)
    assert hrb["capitulation"].iloc[-80] == 1.0          # 20 days after the drop: 30DMA below 60DMA
    assert hrb["capitulation"].iloc[-200] == 0.0


def test_ssr(ctx):
    i = days(10)
    put(ctx, "cm:CapMrktCurUSD", pd.Series(1000.0, index=i))
    put(ctx, "dl:stable:total", pd.Series(250.0, index=i))
    assert oc.ssr(ctx)["ssr"].iloc[-1] == 4.0


def test_hashrate_matches_glassnode_formula(ctx):
    # Glassnode 2021-11-08: difficulty 9.30261777e22 hashes, 135 blocks -> 1.4562e20 H/s
    i = days(1, "2021-11-08")
    put(ctx, "cm:PriceUSD", pd.Series(1.0, index=i))
    put(ctx, "mp:difficulty", pd.Series([9.30261777116591e22 / 2 ** 32], index=i))
    put(ctx, "cm:BlkCnt", pd.Series([135.0], index=i))
    assert oc.hashrate(ctx).iloc[-1] == pytest.approx(1.4562e20, rel=0.003)


def test_altseason_skips_days_without_btc_price(ctx):
    from src.sources.yahoo import GROUPS
    i = days(200)
    put(ctx, "cm:PriceUSD", pd.Series(100.0, index=i[:-1]))              # last day missing
    for t in list(GROUPS["alt"])[:25]:
        put(ctx, f"yf:{t}:close", pd.Series(np.linspace(1, 2, 200), index=i))   # every alt rising
    a = oc.altcoin_season_index(ctx)
    assert a.index[-1] == i[-2] and a.iloc[-1] == 100.0


def test_every_chart_has_an_explanation():
    from src import catalog, explanations
    missing = [s.id for s in catalog.SPECS if s.id not in explanations.E or not all(explanations.E[s.id])]
    assert missing == []
