"""Every dashboard has an even number of panels and every panel has a spec and an explanation."""
from src import catalog, explanations


def test_dashboards_even_and_known():
    for group, name, ids in catalog.DASHBOARDS:
        assert len(ids) % 2 == 0, f"{group} / {name} has {len(ids)} charts"
        for i in ids:
            assert i in catalog.BY_ID, i


def test_new_charts_explained():
    for i in ("days_since_ath", "rsi_14", "fut_liq_30d", "fut_dated_oi", "eth_btc", "opt_iv_rv", "opt_skew_term",
              "ibit_volume", "etf_cum_flows", "macro_real_rate", "tradfi_corr", "ex_netflow_usd", "por_change",
              "whale_supply", "unrealized_pl", "risk_signals", "alt_btc_ratio"):
        assert i in explanations.E
