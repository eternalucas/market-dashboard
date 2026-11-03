"""Data health pipeline diagram: every source is routed, and problems colour their whole path."""
from __future__ import annotations

import pandas as pd

from src import pipeline_map as pm, ui_pipeline

# One of each source family the fetch log holds (3 Nov 2026)
SOURCES = ["app_store:us:finance", "bgeo:sopr", "book:coinbase:BTC", "central_bank_calendar", "coinalyze:1hour:BTC",
           "coinalyze:daily:BTC", "event_price:Gold", "fred:CPIAUCSL", "fred_macro:failed", "fred_release:CPI",
           "funding_hist:okx:BTC", "google_trends:weekly:bitcoin", "hacker_news", "hyperliquid_cohorts",
           "oi_hist:bybit:ETH", "onchain", "onchain:btc:Tether", "onchain:btc_sync", "onchain:eth:Wintermute 1",
           "options:deribit:BTC", "perp:hyperliquid:SOL", "price:BTC", "rec:book:perp:okx", "rec:book:spot:coinbase",
           "rec:futures_curve:deribit", "rec:hl_liq_scan", "rec:liq_calibrate:BTC", "rec:liq_feeds", "rec:mempool",
           "rec:option_chain:deribit", "rec:option_chain:ibit", "rec:option_trades:deribit",
           "rec:option_trades_backfill:deribit", "rec:trade_leaderboard", "src:binance_quarterly",
           "src:blockchain_com", "src:cftc", "src:coinalyze_daily", "src:coinbase_hourly", "src:coingecko_global",
           "src:coinmetrics", "src:defillama_cex", "src:defillama_stablecoins", "src:dvol", "src:farside",
           "src:fear_greed", "src:fred_macro", "src:mempool_hist", "src:social", "src:yahoo",
           "trade_review:hyperliquid", "wikipedia:en.wikipedia:Bitcoin", "wikitotal:en.wikipedia",
           "youtube:@Bankless"]


def _h(failing=()):
    return pd.DataFrame({"Source": SOURCES, "Status": ["Failing" if s in failing else "OK" for s in SOURCES]})


def test_every_source_has_a_known_provider_job_store_and_tab():
    known = {p for _, ps in pm.CATEGORIES for p in ps}
    for s in SOURCES:
        r = pm.route(s)
        assert set(r["providers"]) <= known, s
        assert r["job"] in pm.JOBS and r["db"] in pm.DBS and set(r["tabs"]) <= set(pm.TABS), s


def test_routes_follow_the_code():
    assert pm.route("rec:book:perp:okx")["providers"] == ["OKX"]
    assert pm.route("rec:option_chain:ibit") == {"providers": ["Yahoo Finance"], "job": "ibit", "db": pm.MARKET,
                                                 "tabs": [pm.S]}
    assert pm.route("rec:hl_liq_scan")["db"] == pm.HLPOS
    assert pm.route("trade_review:hyperliquid")["db"] == pm.TRADES
    assert pm.route("onchain:eth:Wintermute 1")["providers"] == ["Blockscout (Ethereum)"]
    assert pm.route("rec:liq_feeds")["providers"] == ["Binance", "Bybit", "OKX"]


def test_a_failing_provider_colours_the_path_to_its_tabs_only():
    m = pm.build(_h(failing={"src:cftc"}))
    nodes = {c: d.set_index("id")["status"] for c, d in m["nodes"].items()}
    assert nodes["provider"]["CFTC"] == "Failing" and nodes["provider"]["Binance"] == "OK"
    assert nodes["job"]["sources"] == "Failing" and nodes["db"][pm.MARKET] == "Failing"
    assert nodes["tab"]["Studio"] == "Failing" and nodes["tab"]["Trade review"] == "OK"
    bad = m["edges"][m["edges"]["status"] == "Failing"]
    assert set(map(tuple, bad[["src", "dst"]].values)) == {("CFTC", "sources"), ("sources", pm.MARKET),
                                                          (pm.MARKET, "Studio")}


def test_counts_are_feeds_not_rows():
    m = pm.build(_h())
    prov = m["nodes"]["provider"].set_index("id")
    assert prov.loc["Deribit", "n"] == 6   # options snapshot, futures curve, option chain, trades, backfill, DVOL
    assert m["nodes"]["tab"].set_index("id").loc["Trade review", "n"] == 2


def test_component_has_every_node_and_highlight_paths():
    d = ui_pipeline.model(_h(failing={"src:cftc"}))
    assert len(d["nodes"]) == sum(1 for _ in d["nodes"]) and {n["col"] for n in d["nodes"]} == set(ui_pipeline.X)
    assert [hd["text"][:2] for hd in d["headers"]] == ["1)", "2)", "3)", "4)"]
    cftc = d["trace"]["provider:CFTC"]                 # hovering CFTC lights exactly its path, in its colour
    assert cftc == {"provider:CFTC|job:sources": "Failing", "job:sources|db:market.db": "Failing",
                    "db:market.db|tab:Studio": "Failing"}
    node = next(n for n in d["nodes"] if n["id"] == "provider:CFTC")
    assert node["feeds"][0][:2] == ["Failing", "src:cftc"]          # details only exist in the click panel
    doc, height = ui_pipeline.page(_h())
    assert "__" not in doc.split("<script>")[0] and height > 600
