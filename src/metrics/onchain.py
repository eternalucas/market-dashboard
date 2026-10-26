"""On-chain metrics without a node: network activity, fees, supply, miners,
Lightning, mempool, exchange flows, valuation (Coin Metrics + BGeometrics),
indicators and stablecoins."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .core import GENESIS, Ctx, sma

# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #


def addresses(c: Ctx) -> pd.DataFrame:
    """Active Addresses [count/day] (unique addresses active as sender or
    receiver; Coin Metrics AdrActCnt) and Addresses with Non-Zero Balance
    (AdrBalCnt). Momentum = 30-day SMA / 365-day SMA of active addresses
    (APPROX: Glassnode's 'Active Address Momentum' is a workbench chart whose
    exact windows aren't published; 30/365 is the conventional pairing)."""
    a = c.s("cm:AdrActCnt")
    return pd.DataFrame({"active": a, "non_zero": c.s("cm:AdrBalCnt"), "momentum": sma(a, 30) / sma(a, 365)})


def transactions(c: Ctx) -> pd.DataFrame:
    """Transaction Count [tx/day] (cm:TxCnt), Transaction Rate [tx/s] =
    count / 86,400, Transfer Count [transfers/day] (cm:TxTfrCnt), Transfer Rate
    [/s], and momentum = 30-day / 365-day SMA of the transaction count."""
    n, tf = c.s("cm:TxCnt"), c.s("cm:TxTfrCnt")
    return pd.DataFrame({"count": n, "rate": n / 86400, "transfers": tf, "transfer_rate": tf / 86400,
                         "momentum": sma(n, 30) / sma(n, 365)})


def transfer_volume(c: Ctx) -> pd.DataFrame:
    """Transfer Volume [USD/day]: blockchain.com 'estimated transaction volume'
    (change outputs heuristically removed) and total output volume [BTC/day]
    x price. Mean per transaction = USD volume / tx count.
    APPROX: Glassnode's 'Transfer Volume (Total)' counts all transfers;
    blockchain.com's estimate is change-adjusted, so it sits closer to
    Glassnode's 'Change-Adjusted Volume'. Medians aren't available."""
    est = c.s("bc:estimated-transaction-volume-usd")
    out_btc = c.s("bc:output-volume")
    return pd.DataFrame({"change_adjusted_usd": est, "outputs_usd": out_btc * c.price(),
                         "mean_usd": est / c.s("cm:TxCnt")})


def utxos(c: Ctx) -> pd.Series:
    """UTXOs (Total) [count]: unspent transaction outputs (BGeometrics
    utxo-count, 4 years). blockchain.com's series was found stale (flat since
    2024) and ~40% below Glassnode, so it isn't used."""
    return _bg(c, "utxo-count")


def blocks(c: Ctx) -> pd.DataFrame:
    """Blocks Mined [blocks/day] (cm:BlkCnt), Block Height (cumulative),
    Block Interval (Mean) [min] = 1,440 / blocks per day, Block Size (Mean)
    [MB] (blockchain.com)."""
    b = c.s("cm:BlkCnt")
    return pd.DataFrame({"mined": b, "height": b.cumsum() - 1, "interval_min": 1440 / b.replace(0, np.nan),
                         "size_mean_mb": c.s("bc:avg-block-size")})


# --------------------------------------------------------------------------- #
# Supply and fees
# --------------------------------------------------------------------------- #


def supply(c: Ctx) -> pd.DataFrame:
    """Circulating Supply [BTC], Issuance [BTC/day], Inflation Rate
    [%/year] = issuance x 365 / supply x 100, Percent of 21M mined [%]."""
    s, i = c.supply(), c.s("cm:IssTotNtv")
    return pd.DataFrame({"supply": s, "issuance": i, "inflation_pct": i * 365 / s * 100, "pct_21m": s / 21e6 * 100})


def fees(c: Ctx) -> pd.DataFrame:
    """Fees (Total) [BTC/day and USD/day] (cm:FeeTotNtv), Fees (Mean) [USD/tx]
    = total fees / transaction count, Fee Ratio Multiple = (issuance + fees) / fees,
    Thermocap [USD] = cumulative miner revenue (block rewards + fees in USD at
    the time) since genesis, Market Cap / Thermocap [ratio]."""
    f, p = c.s("cm:FeeTotNtv"), c.price()
    rev_usd = c.s("cm:IssTotUSD").add(f * p, fill_value=0)
    thermo = rev_usd.cumsum()
    return pd.DataFrame({"total_btc": f, "total_usd": f * p, "mean_usd": f * p / c.s("cm:TxCnt").replace(0, np.nan),
                         "frm": (c.s("cm:IssTotNtv") + f) / f.replace(0, np.nan),
                         "thermocap": thermo, "mcap_thermocap": c.mcap() / thermo})


# --------------------------------------------------------------------------- #
# Miners
# --------------------------------------------------------------------------- #


def miner_revenue(c: Ctx) -> pd.DataFrame:
    """Miner Revenue [USD/day]: block rewards (cm:IssTotUSD), fees (fees x
    price), total; Hash Price [USD per EH/s per day] = total / (hash rate in
    EH/s); Revenue vs Yearly Average = total / 365-day SMA; fee revenue
    z-scores over 2 and 4 years (730 / 1,460 days)."""
    p = c.price()
    rewards, fee = c.s("cm:IssTotUSD"), c.s("cm:FeeTotNtv") * p
    total = rewards.add(fee, fill_value=0)
    hr = hashrate(c) / 1e18
    z = lambda s, n: (s - s.rolling(n, min_periods=n // 2).mean()) / s.rolling(n, min_periods=n // 2).std()  # noqa: E731
    return pd.DataFrame({"rewards": rewards, "fees": fee, "total": total, "hashprice": total / hr,
                         "vs_yearly_avg": total / sma(total, 365), "fee_z_2y": z(fee, 730), "fee_z_4y": z(fee, 1460)})


def puell_multiple(c: Ctx) -> pd.Series:
    """Puell Multiple [ratio] = daily issuance value (USD) / its 365-day SMA."""
    i = c.s("cm:IssTotUSD")
    return i / sma(i, 365)


def hashrate(c: Ctx) -> pd.Series:
    """Hash Rate (Mean) [H/s] = difficulty x 2^32 x blocks mined that day / 86,400.
    This is the standard estimator and reproduces Glassnode's mining.HashRateMean
    exactly (checked 2026-10-21: 2021-11-08 = 9.3026e22 x 135 / 86,400 = 1.4536e20
    vs Glassnode 1.4562e20). Inputs: difficulty at each adjustment (mempool.space),
    forward-filled; blocks per day (Coin Metrics). mempool.space's own smoothed
    estimate differs by 7-21%, so it isn't used."""
    d = difficulty(c)
    if d.empty:
        return c.s("cm:HashRate") * 1e12
    blocks = c.s("cm:BlkCnt")
    return (d["difficulty"].reindex(blocks.index, method="ffill") * 2 ** 32 * blocks / 86400).dropna()


def difficulty(c: Ctx) -> pd.DataFrame:
    """Difficulty [relative to genesis] at each adjustment, forward-filled
    daily; adjustment % change at each retarget."""
    d = c.s("mp:difficulty")
    if d.empty:
        return pd.DataFrame()
    end = max(d.index[-1], c.price().index[-1] if not c.price().empty else d.index[-1])
    daily = d.resample("1D").last().reindex(pd.date_range(d.index[0].normalize(), end, freq="D", tz="UTC")).ffill()
    return pd.DataFrame({"difficulty": daily, "adjustment_pct": (d.pct_change() * 100).resample("1D").last()})


def hash_ribbon(c: Ctx) -> pd.DataFrame:
    """Hash Ribbon: 30- and 60-day SMAs of hash rate. 'capitulation' = 30DMA <
    60DMA; 'recovery' = the day the 30DMA crosses back above the 60DMA."""
    h = hashrate(c)
    a, b = sma(h, 30), sma(h, 60)
    return pd.DataFrame({"sma30": a, "sma60": b, "capitulation": (a < b).astype(float),
                         "recovery": ((a > b) & (a.shift(1) <= b.shift(1))).astype(float)})


def difficulty_ribbon(c: Ctx) -> pd.DataFrame:
    """Difficulty Ribbon: SMAs (14, 25, 40, 60, 90, 128, 200 days) of
    difficulty; Compression = std of the 7 SMAs / their mean (normalised
    standard deviation; Glassnode's compression threshold is 0.05)."""
    d = difficulty(c)
    if d.empty:
        return d
    s = d["difficulty"]
    rib = pd.DataFrame({f"sma{n}": sma(s, n) for n in (14, 25, 40, 60, 90, 128, 200)})
    rib["compression"] = rib.std(axis=1) / rib.mean(axis=1)
    return rib


def difficulty_regression(c: Ctx) -> pd.DataFrame:
    """Difficulty Regression Model [USD]: least-squares fit of log10(market cap)
    on log10(difficulty) over the full history, divided by supply to give a
    model price. APPROX: Glassnode's coefficients aren't published; this
    refits the standard (Capriole) specification on our data."""
    d = difficulty(c)
    if d.empty:
        return d
    df = pd.concat({"mc": c.mcap(), "d": d["difficulty"], "s": c.supply()}, axis=1).dropna()
    df = df[(df["mc"] > 0) & (df["d"] > 0)]
    b, a = np.polyfit(np.log10(df["d"]), np.log10(df["mc"]), 1)
    model = 10 ** (a + b * np.log10(df["d"])) / df["s"]
    return pd.DataFrame({"model_price": model, "price": c.price()})


# --------------------------------------------------------------------------- #
# Lightning and mempool
# --------------------------------------------------------------------------- #


def lightning(c: Ctx) -> pd.DataFrame:
    """Lightning Network: capacity [BTC], nodes, channels, mean channel size
    [BTC] = capacity / channels, mean node capacity [BTC] = capacity / nodes,
    channel opens - closes (day-over-day change in channel count), node count
    change; fee medians from the daily recording (from start)."""
    cap = c.s("mp:ln:total_capacity") / 1e8
    ch, nodes = c.s("mp:ln:channel_count"), c.s("mp:ln:node_count")
    out = pd.DataFrame({"capacity_btc": cap, "channels": ch, "nodes": nodes, "channel_size_mean": cap / ch,
                        "capacity_per_node": cap / nodes, "channels_change": ch.diff(), "nodes_change": nodes.diff()})
    for k in ("med_fee_rate", "med_base_fee_mtokens", "med_capacity", "avg_capacity"):
        s = c.s(f"ln:{k}")
        if not s.empty:
            out[k] = s
    return out


def mempool(c: Ctx) -> pd.DataFrame:
    """Mempool (recorded every 10 min from start): transaction count, size
    [vMB], total fees [BTC], average and median fee rate [sat/vB], recommended
    fees, and size / fees per fee-rate band."""
    d = c.prefix("mempool:")
    df = pd.DataFrame({k.split(":", 1)[1]: v for k, v in d.items()})
    if "vsize" in df:
        df["vsize_mb"] = df["vsize"] / 1e6
    if "total_fee" in df:
        df["total_fee_btc"] = df["total_fee"] / 1e8
    return df


# --------------------------------------------------------------------------- #
# Exchanges
# --------------------------------------------------------------------------- #


def exchange_flows(c: Ctx) -> pd.DataFrame:
    """Exchange balance [BTC] (cm:SplyExNtv), balance % of supply, inflow /
    outflow / netflow [BTC/day] (cm:FlowInExNtv, FlowOutExNtv), net position
    change [BTC] = 30-day change in balance (Glassnode's definition).
    Coverage: Coin Metrics' set of exchange-attributed addresses."""
    bal = c.s("cm:SplyExNtv")
    i, o = c.s("cm:FlowInExNtv"), c.s("cm:FlowOutExNtv")
    return pd.DataFrame({"balance": bal, "balance_pct": bal / c.supply() * 100, "inflow": i, "outflow": o,
                         "netflow": i - o, "net_position_change": bal - bal.shift(30)})


def proof_of_reserves(c: Ctx) -> pd.DataFrame:
    """Proof of Reserves [BTC] per exchange: BTC in the wallets each exchange
    publishes (DefiLlama CEX transparency), plus the total."""
    d = c.prefix("dl:cex:")
    df = pd.DataFrame({k.split(":")[2]: v for k, v in d.items()}).sort_index().ffill(limit=3)
    if not df.empty:
        df["total"] = df.sum(axis=1)
    return df


# --------------------------------------------------------------------------- #
# Valuation (Coin Metrics: full history; BGeometrics: 4 years, 7-day delayed)
# --------------------------------------------------------------------------- #


def realized(c: Ctx) -> pd.DataFrame:
    """Realized Cap [USD] = market cap / MVRV; Realized Price [USD] = realized
    cap / supply; MVRV [ratio]; NUPL [ratio] = 1 - 1 / MVRV
    (= (market cap - realized cap) / market cap)."""
    mv = c.s("cm:CapMVRVCur")
    rc = c.realized_cap()
    return pd.DataFrame({"realized_cap": rc, "realized_price": rc / c.supply(), "mvrv": mv, "nupl": 1 - 1 / mv})


def mvrv_zscore(c: Ctx) -> pd.Series:
    """MVRV Z-Score = (market cap - realized cap) / std(market cap), the std
    computed cumulatively over all history up to each day (Glassnode: 'The
    standard deviation is computed cumulatively')."""
    mc, rc = c.mcap(), c.realized_cap()
    mc, rc = mc.align(rc, join="inner")
    return (mc - rc) / mc.expanding(min_periods=30).std()


def mvrv_pricing_bands(c: Ctx, levels=(0.8, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)) -> pd.DataFrame:
    """MVRV Pricing Bands [USD] = realized price x MVRV level."""
    rp = realized(c)["realized_price"]
    return pd.DataFrame({f"x{lvl:g}": rp * lvl for lvl in levels})


def mvrv_deviation_bands(c: Ctx, sigmas=(-1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5)) -> pd.DataFrame:
    """MVRV Deviation Pricing Bands [USD]: realized price x (mean MVRV + k x
    std MVRV), mean and std computed cumulatively over all history."""
    r = realized(c)
    m, s = r["mvrv"].expanding(365).mean(), r["mvrv"].expanding(365).std()
    return pd.DataFrame({f"{k:+g}sd": r["realized_price"] * (m + k * s) for k in sigmas})


def _bg(c: Ctx, endpoint: str, field: str | None = None) -> pd.Series:
    """One BGeometrics field (or the endpoint's only / first field)."""
    if field:
        return c.s(f"bg:{endpoint}:{field}")
    d = c.prefix(f"bg:{endpoint}:")
    return next(iter(d.values()), pd.Series(dtype=float))


def bg_metric(c: Ctx, endpoint: str) -> pd.DataFrame:
    """All fields of one BGeometrics endpoint (7-day delayed, last ~4 years)."""
    d = c.prefix(f"bg:{endpoint}:")
    return pd.DataFrame({k.split(":", 2)[2]: v for k, v in d.items()})


def holders(c: Ctx) -> pd.DataFrame:
    """Short- and long-term holder cost basis and profitability (BGeometrics):
    STH/LTH realized price [USD], STH/LTH MVRV, SOPR / aSOPR / STH-SOPR /
    LTH-SOPR, STH/LTH NUPL. Glassnode's STH = coins younger than 155 days."""
    pick = {"sth_realized_price": "sth-realized-price", "lth_realized_price": "lth-realized-price",
            "sth_mvrv": "sth-mvrv", "lth_mvrv": "lth-mvrv", "sopr": "sopr", "asopr": "asopr",
            "sth_sopr": "sth-sopr", "lth_sopr": "lth-sopr", "sth_nupl": "nupl-sth", "lth_nupl": "nupl-lth"}
    return pd.DataFrame({k: _bg(c, ep) for k, ep in pick.items()})


def hodl_waves(c: Ctx, realized_cap: bool = False) -> pd.DataFrame:
    """HODL Waves [% of supply] (or Realized Cap HODL Waves [% of realized
    cap]) by age band, from BGeometrics' per-band totals, normalised to 100%."""
    df = bg_metric(c, "realized-cap-hodl-waves" if realized_cap else "hodl-waves-supply")
    return df.div(df.sum(axis=1), axis=0) * 100 if not df.empty else df


def rhodl_ratio(c: Ctx) -> pd.Series:
    """RHODL Ratio = (1-week RCap HODL band / 1-2 year band) x market age in
    days (Glassnode: 'ratio between the 1-week and the 1-2 years RCap HODL
    bands, additionally weighted by total market age').
    1-week band = 0d-1d + 1d-1w. APPROX: 4-year history (BGeometrics)."""
    w = hodl_waves(c, realized_cap=True)
    if w.empty:
        return pd.Series(dtype=float)
    week = w.filter(regex="age_0d_1d|age_1d_1w").sum(axis=1)
    yr = w.filter(regex="age_1y_2y").sum(axis=1)
    age = pd.Series((w.index - GENESIS).days, index=w.index, dtype=float)
    return (week / yr.replace(0, np.nan)) * age


def cdd(c: Ctx) -> pd.DataFrame:
    """Coin Days Destroyed [coin-days/day], supply-adjusted CDD, 90-day CDD,
    Value Days Destroyed multiple, ASOL [days] (BGeometrics)."""
    return pd.DataFrame({"cdd": _bg(c, "cdd"), "cdd_supply_adj": _bg(c, "supply-adjusted-cdd"),
                         "cdd_90dma": _bg(c, "cdd-90dma"), "vdd_multiple": _bg(c, "vdd-multiple"),
                         "asol": _bg(c, "asol")})


def cost_basis_models(c: Ctx) -> pd.DataFrame:
    """Cost-basis price models [USD] (BGeometrics): True Market Mean, Investor
    Price, Delta Cap / supply (delta price), Thermo price, Terminal price, and
    AVIV ratio."""
    out = {"true_market_mean": _bg(c, "true-market-mean"), "investor_price": _bg(c, "investor-price"),
           "thermo_price": _bg(c, "thermo-price"), "terminal_price": _bg(c, "terminal-price"), "aviv": _bg(c, "aviv")}
    dc = _bg(c, "delta-cap")
    if not dc.empty:
        out["delta_price"] = dc / c.supply()
    return pd.DataFrame(out)


def profit_loss(c: Ctx) -> pd.DataFrame:
    """Supply in profit / loss [BTC], percent supply in profit [%], realized
    profit / loss and net realized P/L [USD/day], UTXOs in profit [%]
    (BGeometrics)."""
    sp, sl = _bg(c, "supply-profit"), _bg(c, "supply-loss")
    rp, rl = _bg(c, "realized-profit"), _bg(c, "realized-loss")
    return pd.DataFrame({"supply_profit": sp, "supply_loss": sl, "pct_supply_profit": sp / (sp + sl) * 100,
                         "realized_profit": rp, "realized_loss": rl, "net_realized_pl": _bg(c, "nrpl-usd"),
                         "realized_pl_ratio": rp / rl.replace(0, np.nan), "utxos_in_profit_pct": _bg(c, "utxos-in-profit-pct")})


ADDRESS_BANDS = ["1-BTC", "10-1-BTC", "100-10-BTC", "1K-100-BTC", "10K-1K-BTC", "10K-BTC"]
BAND_NAMES = {"1-BTC": "< 1 BTC", "10-1-BTC": "1-10", "100-10-BTC": "10-100", "1K-100-BTC": "100-1k",
              "10K-1K-BTC": "1k-10k", "10K-BTC": "> 10k"}


def address_bands(c: Ctx) -> pd.DataFrame:
    """Address count and supply held per balance band (< 1, 1-10, 10-100,
    100-1k, 1k-10k, > 10k BTC), BGeometrics. Columns: count:<band>, supply:<band>."""
    out = {}
    for b in ADDRESS_BANDS:
        out[f"count:{BAND_NAMES[b]}"] = _bg(c, f"balance-addr-{b}")
        out[f"supply:{BAND_NAMES[b]}"] = _bg(c, f"coins-addr-{b}")
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #


def nvt(c: Ctx) -> pd.DataFrame:
    """NVT Ratio = market cap / daily transfer volume [USD]; NVT Signal =
    market cap / 90-day SMA of transfer volume; Velocity = transfer volume /
    market cap. Transfer volume: blockchain.com estimated (change-adjusted) USD
    volume - APPROX vs Glassnode's all-transfers volume, so levels differ;
    shape and turning points match better than levels."""
    v, mc = c.s("bc:estimated-transaction-volume-usd"), c.mcap()
    return pd.DataFrame({"nvt": mc / v, "nvt_signal": mc / sma(v, 90), "velocity": v / mc})


def stock_to_flow(c: Ctx) -> pd.DataFrame:
    """Stock-to-Flow Ratio = supply / issuance over the last 365 days; S2F
    model price [USD] = exp(-1.84) x S2F^3.36 (PlanB, 2019); Deflection =
    price / model price."""
    s = c.supply()
    flow = c.s("cm:IssTotNtv").rolling(365, min_periods=365).sum()
    sf = s / flow
    model = np.exp(-1.84) * sf ** 3.36
    return pd.DataFrame({"s2f": sf, "model_price": model, "deflection": c.price() / model})


def ssr(c: Ctx) -> pd.DataFrame:
    """Stablecoin Supply Ratio = BTC market cap / stablecoin market cap;
    Oscillator = (SSR - 200-day SMA) / (2 x 200-day std), i.e. where SSR sits
    in its BB(200, 2) band (-1 lower band, +1 upper band).
    APPROX: stablecoin aggregate is DefiLlama's (Glassnode lists ~15 coins);
    oscillator form inferred from Glassnode's one-line description."""
    s = (c.mcap() / c.s("dl:stable:total")).dropna()
    m, sd = s.rolling(200, min_periods=200).mean(), s.rolling(200, min_periods=200).std()
    return pd.DataFrame({"ssr": s, "oscillator": (s - m) / (2 * sd)})


def fear_greed(c: Ctx) -> pd.Series:
    """Fear & Greed Index [0-100], alternative.me (APPROX: Glassnode publishes
    its own index; alternative.me's is the most widely used public one)."""
    return c.s("fng")


def altcoin_season_index(c: Ctx, window: int = 90) -> pd.Series:
    """Altcoin Season Index [0-100] = share of the tracked top altcoins (Yahoo,
    ~50 non-stablecoins) whose 90-day return beats BTC's, x 100. >= 75 =
    altcoin season, <= 25 = bitcoin season (blockchaincenter.net convention).
    APPROX: Glassnode's Altcoin Cycle Signal uses the top 250 and its own model."""
    from ..sources.yahoo import GROUPS
    btc = c.price()
    rb = btc / btc.shift(window) - 1
    beats = []
    for t in GROUPS["alt"]:
        p = c.s(f"yf:{t}:close")
        if p.empty:
            continue
        p.index = p.index.normalize()
        ra = p / p.shift(window) - 1
        rbb = rb.reindex(ra.index)
        beats.append((ra > rbb).astype(float).where(ra.notna() & rbb.notna()))
    if not beats:
        return pd.Series(dtype=float)
    df = pd.concat(beats, axis=1)
    return (df.mean(axis=1) * 100)[df.notna().sum(axis=1) >= 20].astype(float)


# --------------------------------------------------------------------------- #
# Stablecoins
# --------------------------------------------------------------------------- #


def stablecoins(c: Ctx) -> pd.DataFrame:
    """Stablecoin supply [USD] (DefiLlama total), Net Position Change [USD] =
    30-day change, 30-day % change, and supply by coin."""
    t = c.s("dl:stable:total")
    out = pd.DataFrame({"total": t, "net_position_change": t - t.shift(30), "change_30d_pct": (t / t.shift(30) - 1) * 100})
    for k, v in c.prefix("dl:stable:coin:").items():
        out[k.split(":")[-1]] = v
    return out


def stablecoin_dominance(c: Ctx) -> pd.DataFrame:
    """Supply Dominance [%] of each tracked stablecoin = coin supply / total x 100."""
    t = c.s("dl:stable:total")
    return pd.DataFrame({k.split(":")[-1]: v / t * 100 for k, v in c.prefix("dl:stable:coin:").items()})


def stablecoins_by_chain(c: Ctx) -> pd.DataFrame:
    return pd.DataFrame({k.split(":")[-1]: v for k, v in c.prefix("dl:stable:chain:").items()})
