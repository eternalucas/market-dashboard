"""Event study, trade review, on-chain cohorts and address checks."""
import numpy as np
import pandas as pd
import pytest

from src import events as E
from src.addresses import valid_btc, valid_evm
from src.collectors import hyperliquid as hl
from src.indicators import onchain as oc
from src.indicators import trades as tr

# ------------------------------- Addresses -------------------------------- #

def test_btc_checksums_catch_one_wrong_character():
    good = ["34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo",
            "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97",
            "bc1p6mv2d3rpfhatkv77r6huuurgqyyklxpsnw3090k2qjwqtd6cwkcqzsruxt"]
    for a in good:
        assert valid_btc(a)
        assert not valid_btc(a[:-1] + ("q" if a[-1] != "q" else "p"))
    assert valid_evm("0x28C6c06298d514Db089934071355E5743bf21d60")
    assert not valid_evm("0x28C6c06298d514Db0899340")


# ------------------------------- Events ----------------------------------- #

def test_pboc_dates_skip_weekends():
    d = E.pboc_lpr_dates("2026-09-01", "2026-10-01")
    assert list(d.strftime("%Y-%m-%d")) == ["2026-09-21", "2026-10-20"]   # Sep 20 2026 is a Sunday


def test_reaction_uses_previous_close_as_base():
    p = E.prep(pd.Series([100.0, 110.0, 121.0, 133.1],
                         index=pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")))
    assert E.reaction(p, pd.Timestamp("2026-01-02"), 1) == pytest.approx(0.10)
    assert E.reaction(p, pd.Timestamp("2026-01-02"), 3) == pytest.approx(0.331)
    assert np.isnan(E.reaction(p, pd.Timestamp("2026-01-03"), 5))       # window not complete


def test_reaction_on_weekend_event_uses_next_trading_close():
    # Friday and Monday closes only (a stock index): Saturday event -> Monday close vs Friday close
    p = E.prep(pd.Series([100.0, 102.0], index=pd.to_datetime(["2026-01-02", "2026-01-05"])))
    assert E.reaction(p, pd.Timestamp("2026-01-03"), 3) == pytest.approx(0.02)


def test_classify_cpi_and_fomc():
    data = {
        "cpi_yoy": pd.Series([3.0, 3.2], index=pd.to_datetime(["2026-01-01", "2026-02-01"])),
        "ppi_yoy": pd.Series(dtype=float), "payrolls_chg": pd.Series(dtype=float), "claims": pd.Series(dtype=float),
        "fed_upper": pd.Series([4.5, 4.25], index=pd.to_datetime(["2026-03-17", "2026-03-19"])),
    }
    events = pd.DataFrame({"event": ["CPI", "FOMC decision"],
                           "date": pd.to_datetime(["2026-03-11", "2026-03-18"])})
    assert list(E.classify(events, data)) == ["Hotter", "Cut"]


# ------------------------------ Trade review ------------------------------ #

def fill(t, side, px, sz, start, pnl=0.0, fee=0.1, coin="BTC", crossed=True):
    return {"coin": coin, "time": pd.Timestamp(t, tz="UTC"), "side": side, "px": px, "sz": sz,
            "startPosition": start, "closedPnl": pnl, "fee": fee, "crossed": crossed}


def test_round_trips_open_add_close_and_flip():
    f = pd.DataFrame([
        fill("2026-01-01 00:00", "B", 100, 1, 0),                 # open long 1 @100
        fill("2026-01-01 01:00", "B", 90, 1, 1),                  # add while losing -> avg 95
        fill("2026-01-01 02:00", "A", 110, 2, 2, pnl=30),         # close: +30
        fill("2026-01-01 03:00", "A", 110, 1, 0),                 # open short 1
        fill("2026-01-01 04:00", "B", 100, 2, -1, pnl=10),        # flip: close short +10, open long 1 @100
        fill("2026-01-01 05:00", "A", 95, 1, 1, pnl=-5),          # close long -5
    ])
    t = tr.round_trips(f)
    assert len(t) == 3 and not t["open"].any()
    first = t.iloc[0]
    assert first["side"] == "Long" and first["avg_entry"] == pytest.approx(95)
    assert first["added_while_losing"] and first["pnl"] == 30
    assert first["net"] == pytest.approx(30 - 0.3)
    assert list(t["side"]) == ["Long", "Short", "Long"]
    # the flip fill (size 2: 1 closes the short, 1 opens the long) splits its 0.1 fee 50/50
    assert t.iloc[2]["avg_entry"] == 100 and t.iloc[2]["net"] == pytest.approx(-5 - 0.05 - 0.1)
    assert t.iloc[1]["net"] == pytest.approx(10 - 0.1 - 0.05)


def test_stats_and_streak():
    closed = pd.DataFrame({"net": [10.0, -5.0, -5.0, 20.0], "pnl": [10.5, -4.5, -4.5, 20.5],
                           "fees": [0.5] * 4, "hold_hours": [1, 5, 5, 2],
                           "close_time": pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")})
    s = tr.stats(closed)
    assert s["win_rate"] == 0.5 and s["profit_factor"] == pytest.approx(3.0)
    assert s["max_drawdown"] == -10 and s["longest_losing_streak"] == 2


def test_recurring_errors_flags_losing_coin():
    rows = []
    for i in range(24):
        coin, net = ("DOGE", -10.0) if i % 2 else ("BTC", 15.0)
        rows.append({"coin": coin, "net": net, "pnl": net + 0.1, "fees": 0.1, "hold_hours": 2.0,
                     "open_time": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=6 * i),
                     "close_time": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=6 * i + 2),
                     "max_notional": 1000.0, "added_while_losing": False, "taker_share": 0.0})
    errs = tr.recurring_errors(pd.DataFrame(rows))
    row = errs[errs["pattern"] == "Losing market: DOGE"].iloc[0]
    assert row["trades_flagged"] == 12 and row["impact_usd"] == -120


def test_trader_pool_and_reproducible_pick():
    lb = pd.DataFrame({"address": ["0xa", "0xb", "0xc", "0xd"], "account_value": [50e3, 80e3, 500e3, 30e3],
                       "allTime_pnl": [10, 20, 30, -5], "month_pnl": [1, 2, 3, 4], "month_vlm": [1e6] * 4})
    pool = hl.eligible_traders(lb, 10_000, 100_000, 250_000)
    assert sorted(pool["address"]) == ["0xa", "0xb"]          # 0xc too big, 0xd unprofitable
    assert hl.pick_trader(pool, 7)["address"] == hl.pick_trader(pool.iloc[::-1], 7)["address"]


# ------------------------------- On-chain --------------------------------- #

def test_entity_flows_and_cohorts():
    labels = {"0xw": "Wintermute", "0xb": "Binance"}
    t = pd.DataFrame({"from": ["0xW", "0xB", "0xz"], "to": ["0xb", "0xq", "0xy"], "usd": [5e6, 2e5, 1e9]})
    f = oc.entity_flows(t, labels)
    assert f.iloc[0].to_dict() == {"from_entity": "Wintermute", "to_entity": "Binance", "usd": 5e6, "transfers": 1}
    assert len(f) == 2                                         # unwatched-to-unwatched dropped
    assert list(oc.size_cohort(pd.Series([5e4, 5e6]))) == ["< $100k", "$1M-10M (whale)"]
    pos = pd.DataFrame({"cohort": ["Fish ($10k-100k)"] * 2, "address": ["a", "b"], "coin": ["BTC", "BTC"],
                        "size": [1.0, -0.5], "value_usd": [100e3, 50e3]})
    c = oc.cohort_positioning(pos).iloc[0]
    assert c["net_usd"] == 50e3 and c["long_share"] == pytest.approx(2 / 3) and c["accounts"] == 2


def test_leaderboard_parsing(monkeypatch):
    sample = {"leaderboardRows": [{"ethAddress": "0xabc", "accountValue": "50123.4", "displayName": None, "prize": 0,
                                   "windowPerformances": [["day", {"pnl": "1", "roi": "0.01", "vlm": "10"}],
                                                          ["month", {"pnl": "500", "roi": "0.1", "vlm": "900000"}],
                                                          ["allTime", {"pnl": "9000", "roi": "0.5", "vlm": "5e6"}]]}]}
    hl._cache.clear()
    monkeypatch.setattr(hl, "get_json", lambda url, params=None: sample)
    lb = hl.leaderboard()
    r = lb.iloc[0]
    assert r["address"] == "0xabc" and r["account_value"] == pytest.approx(50123.4)
    assert r["month_vlm"] == 900000 and r["allTime_pnl"] == 9000


def test_btc_transfer_parsing(monkeypatch):
    from src.collectors import onchain
    me, other = "1Me", "3Other"
    txs = [{"txid": "a", "status": {"confirmed": True, "block_time": 1_790_000_000},
            "vin": [{"prevout": {"scriptpubkey_address": other, "value": 600_000_000}}],
            "vout": [{"scriptpubkey_address": me, "value": 500_000_000},
                     {"scriptpubkey_address": other, "value": 99_000_000}]},
           {"txid": "b", "status": {"confirmed": False}, "vin": [], "vout": []}]
    monkeypatch.setattr(onchain, "get_json", lambda url, params=None: txs)
    monkeypatch.setattr(onchain, "PAUSE", 0)
    t = onchain.btc_transfers(me)
    assert len(t) == 1 and t.iloc[0]["net_btc"] == 5.0 and t.iloc[0]["counterparties"] == [other]


def test_eth_token_transfer_parsing(monkeypatch):
    from src.collectors import onchain
    page = {"items": [{"timestamp": "2026-10-01T00:00:00Z", "transaction_hash": "0x1",
                       "from": {"hash": "0xA"}, "to": {"hash": "0xB"},
                       "token": {"symbol": "USDT", "decimals": "6", "exchange_rate": "1.0"},
                       "total": {"value": "2500000000", "decimals": "6"}}], "next_page_params": None}
    monkeypatch.setattr(onchain, "get_json", lambda url, params=None: page)
    monkeypatch.setattr(onchain, "PAUSE", 0)
    t = onchain.eth_token_transfers("0xA")
    assert t.iloc[0]["amount"] == 2500 and t.iloc[0]["usd"] == 2500 and t.iloc[0]["token"] == "USDT"


def test_trading_style_flags_bots():
    t0 = pd.Timestamp("2026-01-01", tz="UTC")
    bot = pd.DataFrame({"open_time": [t0 + pd.Timedelta(minutes=i) for i in range(100)],
                        "hold_hours": [5 / 3600] * 100})
    human = pd.DataFrame({"open_time": [t0 + pd.Timedelta(hours=8 * i) for i in range(40)],
                          "hold_hours": [2.0] * 40})
    assert not tr.looks_human(tr.trading_style(bot))
    assert tr.looks_human(tr.trading_style(human))
    assert tr.duration(2.5) == "2.5h" and tr.duration(0.25) == "15 min" and tr.duration(10 / 3600) == "10 s"


def test_fomc_calendar_is_meetings_not_daily():
    cal = E.central_bank_calendar()
    f = cal[cal["event"] == "FOMC decision"]
    assert len(f[f["date"].dt.year == 2025]) == 8
    assert pd.Timestamp("2026-10-28") in set(f["date"])


def test_flags_skip_patterns_that_make_money():
    rows = []
    for i in range(30):
        rows.append({"coin": "BTC", "net": 20.0, "pnl": 20.1, "fees": 0.1, "hold_hours": 2.0,
                     "open_time": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=6 * i),
                     "close_time": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=6 * i + 2),
                     "max_notional": 1000.0, "added_while_losing": i % 3 == 0, "taker_share": 0.0})
    errs = tr.recurring_errors(pd.DataFrame(rows))
    assert errs.empty or "Averaging down" not in set(errs["pattern"])
