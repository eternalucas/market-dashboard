"""On-chain tab, laid out like the other tabs: bordered cards with a chart
each, detail tables folded away underneath.

1. Watched wallets: holdings by cohort, BTC held by category over time.
2. Bitcoin flows of the watched wallets over a chosen window (re-read every
   two minutes; the recorder syncs new transactions hourly, see
   pipeline.sync_btc_wallets): cumulative net flow, net flow by entity, every
   transaction on a timeline.
3. Ethereum: flows between entities (ERC-20), market-maker flows, $1M+ transfers.
4. Hyperliquid trader cohorts: size, profitability, positioning.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import storage, views
from .indicators import onchain as oc

WINDOWS = {"24 hours": 1, "7 days": 7, "30 days": 30, "90 days": 90}
ALL = "All watched wallets"


CHART_CFG = {"displayModeBar": False}
GAP = '<div style="height: 28px"></div>'


def _layout(fig, height):
    from .charts import card_layout
    return card_layout(fig, height)


def _gap() -> None:
    st.markdown(GAP, unsafe_allow_html=True)


def _usd_short(x) -> str:
    """$1.2B / $340M / $12K without a sign."""
    if x is None or x != x:
        return "n/a"
    a = abs(x)
    body = (f"{a / 1e9:,.2f}B" if a >= 1e9 else f"{a / 1e6:,.1f}M" if a >= 1e6 else f"{a / 1e3:,.0f}K"
            if a >= 1e3 else f"{a:,.0f}")
    return ("-" if x < 0 else "") + "$" + body


def _coverage(wl: pd.DataFrame) -> tuple[dict, dict]:
    """(wallet -> complete_since, group -> latest complete_since of its wallets)."""
    from .pipeline import BTC_SYNC_STATE, ONCHAIN_DIR
    p = ONCHAIN_DIR / BTC_SYNC_STATE
    state = json.loads(p.read_text()) if p.exists() else {}
    per_wallet = {a: s.get("complete_since") for a, s in state.items() if s.get("complete_since")}
    groups: dict[str, str] = {}
    for w in wl.itertuples():
        g = oc.flow_group(w.entity, w.label)
        cs = per_wallet.get(w.address, pd.Timestamp.now(tz="UTC").isoformat())   # never synced: no history
        if g not in groups or pd.Timestamp(cs) > pd.Timestamp(groups[g]):
            groups[g] = cs
    last = max((s.get("last_sync", "") for s in state.values()), default="")
    return groups, {"last_sync": last}


# --------------------------------------------------------------------------- #
# Whole tab
# --------------------------------------------------------------------------- #

def render(con, cfg: dict, category_names: dict, colors: dict, long_short=("#2a78d6", "#eb6834")) -> None:
    from .collectors import onchain as onchain_api
    wl = onchain_api.load_watchlist()
    watched_wallets(con, wl, category_names, colors)
    _gap()
    btc_flows(con, onchain_api.load_watchlist(), category_names, colors)
    _gap()
    eth_flows(wl)
    _gap()
    hl_cohorts(cfg, long_short)


# --------------------------------------------------------------------------- #
# 1. Watched wallets
# --------------------------------------------------------------------------- #

def _balances(con, wl: pd.DataFrame, category_names: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(one row per wallet with balance, USD and changes; BTC held per category over time)."""
    px_now = {}
    for ch in ("btc", "eth"):
        s = storage.load_series(con, f"price:{ch.upper()}")
        px_now[ch] = s.iloc[-1] if not s.empty else np.nan
    rows, hist = [], []
    for w in wl.itertuples():
        bal = storage.load_series(con, f"onchain_bal:{w.chain}:{w.address}")
        latest = bal.iloc[-1] if not bal.empty else np.nan

        def chg(days, bal=bal, latest=latest):
            past = bal[bal.index <= bal.index[-1] - pd.Timedelta(days=days)] if not bal.empty else bal
            return latest - past.iloc[-1] if len(past) else np.nan
        cat = category_names.get(w.category, w.category)
        rows.append({"Label": w.label, "Entity": w.entity, "Category": cat, "Chain": w.chain.upper(),
                     "Balance": latest, "USD": latest * px_now.get(w.chain, np.nan),
                     "Since last update": latest - bal.iloc[-2] if len(bal) >= 2 else np.nan,
                     "Change 7d": chg(7), "Change 30d": chg(30), "Address": w.address})
        if w.chain == "btc" and len(bal):
            hist.append(bal.rename(cat))
    bals = pd.DataFrame(rows)
    byc = pd.concat(hist, axis=1).T.groupby(level=0).sum().T if hist else pd.DataFrame()
    return bals, byc


def watched_wallets(con, wl: pd.DataFrame, category_names: dict, colors: dict) -> None:
    from .ui_studio import card
    st.subheader("Watched wallets")
    st.caption("Exchanges, market makers and every Bitcoin wallet holding 10,000+ BTC, from public labels "
               "(Etherscan name tags, the bitinfocharts rich list). Balances and transfers come from public "
               "explorers (mempool.space, Blockscout). Edit watchlist.yaml to add wallets, e.g. ones you see on Arkham.")
    bals, byc = _balances(con, wl, category_names)
    have = bals.dropna(subset=["Balance"])
    k = st.columns(4)
    k[0].metric("Wallets watched", f"{len(wl)}", help="Bitcoin and Ethereum")
    k[1].metric("Entities", f"{wl['entity'].nunique()}")
    k[2].metric("BTC held", f"{have.loc[have['Chain'] == 'BTC', 'Balance'].sum():,.0f}",
                help="Sum of the watched Bitcoin wallets' latest balances.")
    k[3].metric("Value held", _usd_short(have["USD"].sum()), help="Bitcoin and Ethereum wallets at today's prices.")
    if have.empty:
        st.info("No balances yet. Run **Update all data**.")
        return
    left, right = st.columns(2)
    with left:
        cat = have.groupby(["Category", "Chain"]).agg(wallets=("Label", "size"), coins=("Balance", "sum"),
                                                      usd=("USD", "sum")).reset_index()
        order = cat.groupby("Category")["usd"].sum().sort_values().index
        with card("oc-holdings", "Holdings by cohort", paired=True, expandable=True,
                  desc="Value held by each cohort of watched wallets at today's prices, Bitcoin and Ethereum wallets "
                       "stacked. Hover for the number of wallets and coins.",
                  source="mempool.space, Blockscout") as cc:
            f = go.Figure()
            for chain, color in (("BTC", "#f7931a"), ("ETH", "#8c8cff")):
                d = cat[cat["Chain"] == chain].set_index("Category").reindex(order)
                f.add_trace(go.Bar(y=d.index, x=d["usd"], orientation="h", name=chain, marker_color=color,
                                   customdata=np.c_[d["wallets"].fillna(0), d["coins"].fillna(0)],
                                   hovertemplate="%{y}: %{x:$,.3s}<br>%{customdata[0]:.0f} wallets, %{customdata[1]:,.0f} "
                                                 + chain + "<extra>" + chain + "</extra>"))
            f.update_layout(barmode="stack", bargap=0.35)
            f.update_xaxes(tickprefix="$", title_text="Value held")
            cc.chart(_layout(f, 320).update_layout(margin=dict(l=125)), width="stretch", config=CHART_CFG)
    with right:
        with card("oc-btc-held", "BTC held by category over time", paired=True, expandable=True,
                  desc="Sum of the watched Bitcoin wallets' balances per category, one reading per daily update.",
                  source="mempool.space") as cc:
            if len(byc) > 1:
                f = go.Figure()
                for c in byc.columns:
                    f.add_trace(go.Scatter(x=byc.index, y=byc[c], name=c, line=dict(color=colors.get(c), width=2)))
                f.update_yaxes(title_text="BTC held")
                cc.chart(_layout(f, 320), width="stretch", config=CHART_CFG)
            else:
                st.caption("The history builds from one reading per daily update.")
    with st.expander(f"Every wallet ({len(bals)})"):
        st.dataframe(bals.sort_values("USD", ascending=False), hide_index=True, width="stretch", column_config={
            "Balance": st.column_config.NumberColumn(format="%,.2f"),
            "USD": st.column_config.NumberColumn(format="$%,.0f"),
            "Since last update": st.column_config.NumberColumn(format="%+,.2f"),
            "Change 7d": st.column_config.NumberColumn(format="%+,.2f"),
            "Change 30d": st.column_config.NumberColumn(format="%+,.2f")})
        st.caption("Blank 7-day and 30-day changes fill in once there are readings that far back.")


# --------------------------------------------------------------------------- #
# 2. Bitcoin flows
# --------------------------------------------------------------------------- #

@st.fragment(run_every="120s")
def btc_flows(_con, wl: pd.DataFrame, category_names: dict, colors: dict) -> None:
    # A fragment that refreshes on a timer runs in another thread, where the page's
    # SQLite connection can't be used: open one for this run.
    con = storage.connect()
    try:
        _btc_flows(con, wl, category_names, colors)
    finally:
        con.close()


def _btc_flows(con, wl: pd.DataFrame, category_names: dict, colors: dict) -> None:
    from .ui_studio import card
    st.subheader("Bitcoin: watched-wallet flows")
    tx = views.onchain_file("btc_txs.csv")
    if tx.empty:
        st.caption("No Bitcoin transactions recorded yet.")
        return
    wl = wl[wl["chain"] == "btc"].copy()
    wl["category"] = wl["category"].map(category_names).fillna(wl["category"])
    px = storage.load_series(con, "price:BTC")
    moves = oc.btc_moves(tx, wl, px)
    groups_cov, meta = _coverage(wl)

    c1, c2, c3 = st.columns([2.2, 2, 2.4], vertical_alignment="bottom")
    win_label = c1.segmented_control("Window", list(WINDOWS), default="7 days", key="btc_win") or "7 days"
    cats = c2.multiselect("Category", sorted(wl["category"].unique()), key="btc_cats", placeholder="All categories")
    pool = moves if not cats else moves[moves["category"].isin(cats)]
    named = sorted(pool["group"].unique(), key=lambda g: (g.startswith("Whale"), g))
    group = c3.selectbox("Entity", [ALL] + named, key="btc_group")
    start = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=WINDOWS[win_label])
    sel = pool if group == ALL else pool[pool["group"] == group]
    win = sel[(sel["time"] >= start)]
    flows = win[~win["internal"]]

    m = st.columns(5)
    m[0].metric("Net flow (BTC)", f"{flows['net_btc'].sum():+,.1f}",
                help="Into watched wallets (+) or out of them (-). Moves between two wallets of the same entity "
                     "are internal and left out.")
    m[1].metric("Net flow (USD)", _usd(flows["usd"].sum()), help="Each transaction valued at that day's BTC close.")
    m[2].metric("Inflows (BTC)", f"{flows.loc[flows['net_btc'] > 0, 'net_btc'].sum():,.1f}")
    m[3].metric("Outflows (BTC)", f"{flows.loc[flows['net_btc'] < 0, 'net_btc'].sum():,.1f}")
    m[4].metric("Transactions", f"{flows['txid'].nunique():,}", help=f"{int(win['internal'].sum())} internal moves not counted")

    sel_groups = [group] if group != ALL else sorted(set(pool["group"]))
    partial = {g: groups_cov.get(g) for g in sel_groups if groups_cov.get(g) and pd.Timestamp(groups_cov[g]) > start}
    if partial:
        names = ", ".join(f"{g} (from {pd.Timestamp(t):%d %b %H:%M} UTC)" for g, t in list(partial.items())[:6])
        st.caption(f"⚠️ History isn't complete over the whole window for {len(partial)} of {len(sel_groups)} "
                   f"selected entities, so their flows only count from then: {names}"
                   f"{'…' if len(partial) > 6 else ''}. Coverage fills in as the hourly sync runs.")

    tbl = oc.window_flows(sel, start, groups_cov) if group == ALL else pd.DataFrame()
    left, right = st.columns(2)
    with left:
        with card("oc-btc-cum", f"Cumulative net flow, last {win_label}", paired=True, expandable=True,
                  desc="Running total of BTC moving into (+) or out of (-) the selection, "
                       + ("per category, with the total dotted." if group == ALL else f"for {group}."),
                  source="mempool.space") as cc:
            if flows.empty:
                st.caption("No flows in this window.")
            else:
                freq = "1h" if WINDOWS[win_label] <= 1 else "1D"
                key = "category" if group == ALL else "group"
                series = (flows.set_index("time").groupby(key)["net_btc"].resample(freq).sum().unstack(0).fillna(0)
                          .sort_index().cumsum())
                f = go.Figure()
                for col in series.columns:
                    f.add_trace(go.Scatter(x=series.index, y=series[col], name=str(col), mode="lines",
                                           line=dict(width=2, color=colors.get(col))))
                if series.shape[1] > 1:
                    tot = series.sum(axis=1)
                    f.add_trace(go.Scatter(x=tot.index, y=tot, name="Total", mode="lines",
                                           line=dict(width=2, dash="dot", color="#999")))
                f.add_hline(y=0, line_color="#555", line_width=1)
                f.update_yaxes(title_text="Net BTC in (+) / out (-)")
                cc.chart(_layout(f, 340), width="stretch", config=CHART_CFG)
    with right:
        if group == ALL:
            with card("oc-btc-entities", f"Net flow by entity, last {win_label}", paired=True, expandable=True,
                      desc="The 15 entities with the largest net flow in the window: green = BTC flowing in, "
                           "red = flowing out. Full table below.", source="mempool.space") as cc:
                if tbl.empty:
                    st.caption("No flows in this window.")
                else:
                    t = tbl.reindex(tbl["net_btc"].abs().sort_values(ascending=False).index).head(15).iloc[::-1]
                    f = go.Figure(go.Bar(
                        y=t.index, x=t["net_btc"], orientation="h",
                        marker_color=np.where(t["net_btc"] >= 0, "#2fbf71", "#e5484d"),
                        customdata=np.c_[t["net_usd"], t["txs"]],
                        hovertemplate="%{y}: %{x:+,.1f} BTC (%{customdata[0]:$,.3s})<br>%{customdata[1]} transactions"
                                      "<extra></extra>"))
                    f.add_vline(x=0, line_color="#555", line_width=1)
                    f.update_xaxes(title_text="Net BTC")
                    f.update_layout(margin=dict(l=150))
                    cc.chart(_layout(f, 340).update_layout(margin=dict(l=150)), width="stretch", config=CHART_CFG)
        else:
            with card("oc-btc-sizes", f"{group}: transaction sizes, last {win_label}", paired=True, expandable=True,
                      desc="Inflows and outflows by transaction size.", source="mempool.space") as cc:
                if flows.empty:
                    st.caption("No flows in this window.")
                else:
                    sz = flows.assign(size=oc.size_cohort(flows["usd"].abs()),
                                      side=np.where(flows["net_btc"] > 0, "In", "Out"))
                    g = sz.groupby(["size", "side"], observed=False)["net_btc"].sum().unstack().reindex(oc.SIZE_LABELS)
                    f = go.Figure()
                    for side, color in (("In", "#2fbf71"), ("Out", "#e5484d")):
                        if side in g:
                            f.add_trace(go.Bar(x=g.index, y=g[side], name=side, marker_color=color))
                    f.update_layout(barmode="relative")
                    f.update_yaxes(title_text="BTC")
                    cc.chart(_layout(f, 340), width="stretch", config=CHART_CFG)

    _gap()
    labels = {a.lower(): e for a, e in zip(wl["address"], wl["entity"])}
    show = win.copy()
    show["Size"] = oc.size_cohort(show["usd"].abs())
    show["Counterparty"] = show["counterparties"].fillna("").map(
        lambda c: ", ".join(sorted({labels.get(a.lower(), "") for a in str(c).split()} - {""})) or "unlabelled")
    show["Type"] = np.where(show["internal"], "internal", np.where(show["net_btc"] > 0, "in", "out"))
    with card("oc-btc-tx", f"Every transaction, last {win_label} ({len(show):,})", expandable=True,
              desc="One marker per transaction: ▲ = BTC into the watched wallet, ▼ = out, height = BTC moved (log "
                   "scale), colour = category; grey dots = internal moves between two wallets of the same entity. "
                   "Hover for the entity, wallet and counterparty; the full list with mempool.space links is below.",
              source="mempool.space") as cc:
        if show.empty:
            st.caption("No transactions in this window.")
        else:
            f = go.Figure()
            d0 = show.assign(abs_btc=show["net_btc"].abs()).query("abs_btc > 0")
            for catg, d in d0.groupby(np.where(d0["internal"], "Internal moves", d0["category"])):
                sym = np.where(d["internal"], "circle", np.where(d["net_btc"] > 0, "triangle-up", "triangle-down"))
                f.add_trace(go.Scatter(
                    x=d["time"], y=d["abs_btc"], mode="markers", name=str(catg),
                    marker=dict(size=np.clip(np.log10(d["usd"].abs().fillna(0) + 1) * 2.6 - 6, 5, 20), symbol=sym,
                                color="#666" if catg == "Internal moves" else colors.get(catg), opacity=0.85,
                                line=dict(width=0)),
                    customdata=np.c_[d["group"], d["label"], d["Counterparty"], d["usd"].abs(),
                                     np.where(d["internal"], "internal move", np.where(d["net_btc"] > 0, "in", "out"))],
                    hovertemplate="%{x|%d %b %H:%M}<br>%{customdata[0]} (%{customdata[1]})<br>%{customdata[4]}: "
                                  "%{y:,.2f} BTC (%{customdata[3]:$,.3s})<br>counterparty: %{customdata[2]}"
                                  "<extra></extra>"))
            f.update_layout(hovermode="closest", legend=dict(itemsizing="constant"))
            f.update_yaxes(title_text="BTC (log scale)", type="log", dtick=1)
            cc.chart(_layout(f, 380), width="stretch", config=CHART_CFG)
    with st.expander("Tables: flows by entity and every transaction"):
        if not tbl.empty:
            bal = {}
            for w in wl.itertuples():
                g = oc.flow_group(w.entity, w.label)
                b = storage.load_series(con, f"onchain_bal:btc:{w.address}")
                if not b.empty:
                    bal[g] = bal.get(g, 0) + b.iloc[-1]
            tbl["balance"] = tbl.index.map(bal)
            st.markdown("**Flows by entity**")
            st.dataframe(tbl.reset_index().rename(columns={"group": "Entity"}), hide_index=True, width="stretch",
                         height=min(36 * (len(tbl) + 1) + 3, 420), column_config={
                             "category": "Category", "txs": "Transactions",
                             "inflow_btc": st.column_config.NumberColumn("Inflow (BTC)", format="%,.2f"),
                             "outflow_btc": st.column_config.NumberColumn("Outflow (BTC)", format="%,.2f"),
                             "net_btc": st.column_config.NumberColumn("Net (BTC)", format="%+,.2f"),
                             "net_usd": st.column_config.NumberColumn("Net (USD)", format="dollar"),
                             "balance": st.column_config.NumberColumn("Balance now (BTC)", format="%,.0f"),
                             "last": st.column_config.DatetimeColumn("Last move (UTC)", format="YYYY-MM-DD HH:mm"),
                             "coverage": "History"})
        st.markdown(f"**Transactions ({len(show):,})**")
        show["link"] = "https://mempool.space/tx/" + show["txid"]
        st.dataframe(show[["time", "group", "label", "category", "Type", "net_btc", "usd", "Size", "Counterparty", "link"]],
                     hide_index=True, width="stretch", height=380, column_config={
                         "time": st.column_config.DatetimeColumn("Time (UTC)", format="YYYY-MM-DD HH:mm"),
                         "group": "Entity", "label": "Wallet(s)", "category": "Category",
                         "net_btc": st.column_config.NumberColumn("Net BTC", format="%+,.4f"),
                         "usd": st.column_config.NumberColumn("USD (day's close)", format="dollar"),
                         "link": st.column_config.LinkColumn("Transaction", display_text=r"tx/(.{10})",
                                                             help="Opens on mempool.space")})
    st.caption(f"Synced hourly by the recorder (every new transaction since the last sync); last sync "
               f"{pd.Timestamp(meta['last_sync']):%d %b %H:%M} UTC." if meta["last_sync"] else
               "The hourly sync hasn't run yet; it starts with the recorder.")


def _usd(x):
    if x != x:
        return "n/a"
    a = abs(x)
    body = f"{a / 1e9:,.2f}B" if a >= 1e9 else f"{a / 1e6:,.1f}M" if a >= 1e6 else f"{a:,.0f}"
    return f"{'-' if x < 0 else '+'}${body}"


# --------------------------------------------------------------------------- #
# 3. Ethereum flows
# --------------------------------------------------------------------------- #

MARKET_MAKERS = ["Wintermute", "Jump Trading"]


def eth_flows(wl: pd.DataFrame) -> None:
    from .ui_studio import card
    eth_tr = views.onchain_file("eth_transfers.csv")
    if eth_tr.empty:
        return
    st.subheader("Ethereum: flows between entities (ERC-20 tokens)")
    labels = {a.lower(): e for a, e in zip(wl["address"], wl["entity"])}
    eth_tr["time"] = pd.to_datetime(eth_tr["time"], utc=True)
    c1, _ = st.columns([2.2, 4.4])
    win_label = c1.segmented_control("Window", ["24 hours", "7 days", "30 days", "90 days"], default="7 days",
                                     key="eth_win") or "7 days"
    days = WINDOWS[win_label]
    win = eth_tr[eth_tr["time"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)].copy()
    # Moves between two wallets of the same firm are internal shuffles, not flows
    same = win["from"].str.lower().map(labels).fillna("a") == win["to"].str.lower().map(labels).fillna("b")
    win = win[~same]
    flows_tbl = oc.entity_flows(win, labels)
    m = st.columns(4)
    m[0].metric("Value moved", _usd_short(flows_tbl["usd"].sum()), help="Transfers touching a watched entity.")
    m[1].metric("Transfers", f"{int(flows_tbl['transfers'].sum()):,}")
    m[2].metric("Entity pairs", f"{len(flows_tbl):,}")
    m[3].metric("Internal transfers left out", f"{int(same.sum()):,}",
                help="Between two wallets of the same firm.")

    left, right = st.columns(2)
    with left:
        with card("oc-eth-entities", f"Flows by entity, last {win_label}", paired=True, expandable=True,
                  desc="USD each watched entity received (green) and sent (red) in the window, the 12 most active. "
                       "Transfers with addresses outside the watchlist count too.", source="Blockscout") as cc:
            sent = flows_tbl.groupby("from_entity")["usd"].sum()
            recv = flows_tbl.groupby("to_entity")["usd"].sum()
            ent = pd.DataFrame({"received": recv, "sent": sent}).fillna(0).drop(index="Other", errors="ignore")
            if ent.empty:
                st.caption("No transfers in this window.")
            else:
                ent = ent.reindex((ent["received"] + ent["sent"]).sort_values().index).tail(12)
                f = go.Figure()
                f.add_trace(go.Bar(y=ent.index, x=ent["received"], orientation="h", name="Received",
                                   marker_color="#2fbf71", hovertemplate="%{y}: received %{x:$,.3s}<extra></extra>"))
                f.add_trace(go.Bar(y=ent.index, x=-ent["sent"], orientation="h", name="Sent", marker_color="#e5484d",
                                   customdata=ent["sent"], hovertemplate="%{y}: sent %{customdata:$,.3s}<extra></extra>"))
                f.update_layout(barmode="relative")
                f.add_vline(x=0, line_color="#555", line_width=1)
                f.update_xaxes(title_text="USD: sent (left) / received (right)")
                cc.chart(_layout(f, 360).update_layout(margin=dict(l=110)), width="stretch", config=CHART_CFG)
    with right:
        with card("oc-eth-mm", f"Market-maker flows, last {win_label}", paired=True, expandable=True,
                  desc="What Wintermute and Jump Trading sent to (red) and received from (green) each counterparty.",
                  source="Blockscout") as cc:
            rows = []
            for mm in MARKET_MAKERS:
                out_ = flows_tbl[flows_tbl["from_entity"] == mm].groupby("to_entity")["usd"].sum()
                in_ = flows_tbl[flows_tbl["to_entity"] == mm].groupby("from_entity")["usd"].sum()
                for cp, v in out_.items():
                    rows.append({"mm": mm, "cp": cp, "usd": -v})
                for cp, v in in_.items():
                    rows.append({"mm": mm, "cp": cp, "usd": v})
            mmf = pd.DataFrame(rows)
            if mmf.empty:
                st.caption("No market-maker transfers in this window.")
            else:
                mmf["pair"] = mmf["mm"] + " ↔ " + mmf["cp"]
                g = mmf.groupby("pair")["usd"].agg(["sum"]).rename(columns={"sum": "net"})
                g["in"] = mmf[mmf["usd"] > 0].groupby("pair")["usd"].sum()
                g["out"] = mmf[mmf["usd"] < 0].groupby("pair")["usd"].sum()
                g = g.fillna(0).reindex((g["in"] - g["out"]).sort_values().index).tail(12)
                f = go.Figure()
                f.add_trace(go.Bar(y=g.index, x=g["in"], orientation="h", name="Received", marker_color="#2fbf71",
                                   hovertemplate="%{y}: received %{x:$,.3s}<extra></extra>"))
                f.add_trace(go.Bar(y=g.index, x=g["out"], orientation="h", name="Sent", marker_color="#e5484d",
                                   customdata=-g["out"], hovertemplate="%{y}: sent %{customdata:$,.3s}<extra></extra>"))
                f.update_layout(barmode="relative")
                f.add_vline(x=0, line_color="#555", line_width=1)
                f.update_xaxes(title_text="USD: sent (left) / received (right)")
                cc.chart(_layout(f, 360).update_layout(margin=dict(l=190)), width="stretch", config=CHART_CFG)

    _gap()
    big = win[win["usd"] >= 1_000_000].assign(
        From=lambda d: d["from"].str.lower().map(labels).fillna(d["from"].str[:10] + "..."),
        To=lambda d: d["to"].str.lower().map(labels).fillna(d["to"].str[:10] + "..."))
    with card("oc-eth-big", f"Transfers of $1M or more, last {win_label} ({len(big)})", expandable=True,
              desc="One dot per transfer: height and size = USD value (log scale), colour = token. Hover for who sent it to whom.",
              source="Blockscout") as cc:
        if big.empty:
            st.caption("No $1M+ transfers in this window.")
        else:
            f = go.Figure()
            tok_colors = {"USDT": "#26a17b", "USDC": "#2775ca", "WETH": "#8c8cff", "WBTC": "#f7931a"}
            for tok, d in big.groupby("token"):
                f.add_trace(go.Scatter(
                    x=d["time"], y=d["usd"], mode="markers", name=str(tok),
                    marker=dict(size=np.clip(np.sqrt(d["usd"]) / 400, 5, 24), color=tok_colors.get(tok), opacity=0.8),
                    customdata=np.c_[d["From"], d["To"], d["amount"]],
                    hovertemplate="%{x|%d %b %H:%M}<br>%{customdata[0]} → %{customdata[1]}<br>%{customdata[2]:,.0f} "
                                  + str(tok) + " (%{y:$,.3s})<extra></extra>"))
            f.update_layout(hovermode="closest", legend=dict(itemsizing="constant"))
            f.update_yaxes(type="log", tickprefix="$", dtick=1, title_text="USD (log scale)")
            cc.chart(_layout(f, 340), width="stretch", config=CHART_CFG)
    with st.expander("Tables: entity flows, market-maker flows and $1M+ transfers"):
        t = flows_tbl.rename(columns={"from_entity": "From", "to_entity": "To", "transfers": "Transfers"})
        st.markdown("**All entity flows**")
        st.dataframe(t, hide_index=True, width="stretch", height=320,
                     column_config={"usd": st.column_config.NumberColumn("USD", format="$%,.0f")})
        st.markdown("**Market-maker flows**")
        st.dataframe(t[t["From"].isin(MARKET_MAKERS) | t["To"].isin(MARKET_MAKERS)], hide_index=True, width="stretch",
                     column_config={"usd": st.column_config.NumberColumn("USD", format="$%,.0f")})
        st.markdown("**$1M+ transfers**")
        st.dataframe(big.sort_values("time", ascending=False)[["time", "From", "To", "token", "amount", "usd"]].head(200),
                     hide_index=True, width="stretch",
                     column_config={"time": st.column_config.DatetimeColumn("Time (UTC)", format="YYYY-MM-DD HH:mm"),
                                    "token": "Token",
                                    "usd": st.column_config.NumberColumn("USD", format="$%,.0f"),
                                    "amount": st.column_config.NumberColumn("Amount", format="%,.2f")})


# --------------------------------------------------------------------------- #
# 4. Hyperliquid cohorts
# --------------------------------------------------------------------------- #

def hl_cohorts(cfg, colors_long_short=("#2a78d6", "#eb6834")) -> None:
    from .ui_studio import card
    st.subheader("Hyperliquid trader cohorts")
    st.caption("Accounts on Hyperliquid's leaderboard file that traded this month, grouped by current account size. "
               "Positioning comes from a sample of the largest accounts in each cohort, leaving out market makers "
               "and high-frequency bots (monthly volume above 100x the account).")
    lbc = views.onchain_file("hl_leaderboard.csv")
    hpos = views.onchain_file("hl_cohort_positions.csv")
    if lbc.empty:
        st.caption("No cohort data yet. Run **Update all data**.")
        return
    order = oc.HL_LABELS
    short = {lab: lab.split(" (")[0] for lab in order}
    lbc["turnover_x"] = lbc["month_vlm"] / lbc["account_value"].replace(0, np.nan)
    emptied = lbc[lbc["account_value"] < oc.HL_EMPTIED_BELOW]
    live = lbc[lbc["account_value"] >= oc.HL_EMPTIED_BELOW].copy()
    live["cohort"] = oc.hl_size_cohort(live["account_value"])
    sizes = live.groupby("cohort", observed=True).agg(
        accounts=("address", "size"), total_value=("account_value", "sum"),
        median_pnl=("month_pnl", "median"), profitable=("month_pnl", lambda x: (x > 0).mean()),
        month_pnl=("month_pnl", "sum"),
        bots=("turnover_x", lambda x: (x > oc.HL_BOT_TURNOVER).mean())).reindex(order).rename_axis("Cohort")
    x = [short[c] for c in sizes.index]

    left, right = st.columns(2)
    with left:
        with card("oc-hl-size", "Accounts and capital by cohort", paired=True, expandable=True,
                  desc="Bars: number of accounts in each size cohort. Line: the cohort's combined account value (right "
                       "axis). A few hundred large accounts hold most of the capital.", source="Hyperliquid") as cc:
            f = go.Figure()
            f.add_trace(go.Bar(x=x, y=sizes["accounts"], name="Accounts", marker_color="#3a6fd6",
                               hovertemplate="%{x}: %{y:,} accounts<extra></extra>"))
            f.add_trace(go.Scatter(x=x, y=sizes["total_value"], name="Account value", yaxis="y2", mode="lines+markers",
                                   line=dict(color="#f7931a", width=2),
                                   hovertemplate="%{x}: %{y:$,.3s} held<extra></extra>"))
            f = _layout(f, 320)
            f.update_layout(margin=dict(r=70), yaxis2=dict(overlaying="y", side="right", showgrid=False, rangemode="tozero",
                                                           tickprefix="$", title=dict(text="Account value")))
            f.update_yaxes(title_text="Accounts", selector=dict(overlaying=None))
            cc.chart(f, width="stretch", config=CHART_CFG)
    with right:
        with card("oc-hl-pnl", "This month's results by cohort", paired=True, expandable=True,
                  desc="Bars: share of accounts in profit this month. Line: the median account's PnL (right axis); the "
                       "median is the typical trader, unlike the total, which a few large accounts dominate.",
                  source="Hyperliquid") as cc:
            f = go.Figure()
            f.add_trace(go.Bar(x=x, y=sizes["profitable"] * 100, name="In profit", marker_color="#2fbf71",
                               hovertemplate="%{x}: %{y:.0f}% in profit<extra></extra>"))
            f.add_trace(go.Scatter(x=x, y=sizes["median_pnl"], name="Median PnL", yaxis="y2", mode="lines+markers",
                                   line=dict(color="#f7f8fa", width=2),
                                   hovertemplate="%{x}: median %{y:$,.0f}<extra></extra>"))
            f = _layout(f, 320)
            f.update_layout(margin=dict(r=70), yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                                           title=dict(text="Median PnL (USD)")))
            f.update_yaxes(title_text="% of accounts in profit", ticksuffix="%", range=[0, 100],
                           selector=dict(overlaying=None))
            cc.chart(f, width="stretch", config=CHART_CFG)

    zero = (live["month_pnl"] == 0).mean()
    with st.expander("Cohort table and notes"):
        st.dataframe(sizes, width="stretch", column_config={
            "accounts": st.column_config.NumberColumn("Accounts", format="%,d"),
            "total_value": st.column_config.NumberColumn("Account value", format="$%,.0f"),
            "median_pnl": st.column_config.NumberColumn("Median PnL this month", format="dollar"),
            "profitable": st.column_config.NumberColumn("Profitable this month", format="percent"),
            "month_pnl": st.column_config.NumberColumn("Total PnL this month", format="dollar",
                                                       help="Dominated by a few large accounts; the median is the typical trader."),
            "bots": st.column_config.NumberColumn("Bot-like turnover", format="percent",
                                                  help="Share of accounts trading more than 100x their size a month.")})
        st.caption(f"{len(emptied):,} accounts that traded this month now hold under \\${oc.HL_EMPTIED_BELOW} (emptied or "
                   f"withdrawn; total PnL this month {_usd(emptied['month_pnl'].sum()).replace('$', chr(92) + '$')}). Their current size says nothing "
                   "about how big they traded, so they're left out of the cohorts. The leaderboard file only lists "
                   f"accounts above Hyperliquid's own activity threshold ({len(lbc):,} active this month), not every account. "
                   f"{zero:.0%} of accounts show trading volume but exactly 0 PnL this month (most likely spot-only "
                   "trading, which this PnL figure doesn't count), which is why some medians are 0.")
    if hpos.empty:
        return
    _gap()
    summ = oc.cohort_positioning(hpos)
    coins = [c for c in cfg["onchain"]["cohort_coins"] if c in set(summ["coin"])] or sorted(summ["coin"].unique())
    c1, _ = st.columns([1, 3])
    coin = c1.selectbox("Coin", coins, key="hl_coin")
    one = summ[summ["coin"] == coin].set_index("cohort").reindex(order).dropna(how="all").reset_index()
    sampled = hpos.groupby("cohort")["address"].nunique()
    one["sampled"] = one["cohort"].map(sampled)
    with card("oc-hl-pos", f"{coin} positioning by cohort", expandable=True,
              desc=f"Long positions above zero, shorts below, net as the white marker, for a sample of the "
                   f"{cfg['onchain']['hl_cohort_sample']} largest non-bot accounts per cohort. With so few accounts, one "
                   "large position can swing a cohort: treat it as a lean, not a census.", source="Hyperliquid") as cc:
        xs = [short.get(c, c) for c in one["cohort"]]
        f = go.Figure()
        f.add_trace(go.Bar(x=xs, y=one["long_usd"], name="Long", marker_color=colors_long_short[0],
                           customdata=one["long_share"] * 100,
                           hovertemplate="%{x}: %{y:$,.3s} long (%{customdata:.0f}% of the cohort's book)<extra></extra>"))
        f.add_trace(go.Bar(x=xs, y=-one["short_usd"], name="Short", marker_color=colors_long_short[1],
                           hovertemplate="%{x}: %{y:$,.3s} short<extra></extra>"))
        f.add_trace(go.Scatter(x=xs, y=one["net_usd"], name="Net", mode="markers",
                               marker=dict(symbol="line-ew-open", size=28, color="#f7f8fa", line=dict(width=3, color="#f7f8fa")),
                               hovertemplate="%{x}: net %{y:$,.3s}<extra></extra>"))
        f.update_layout(barmode="relative")
        f.add_hline(y=0, line_color="#555", line_width=1)
        f.update_yaxes(title_text=f"{coin} positions (USD)")
        cc.chart(_layout(f, 340), width="stretch", config=CHART_CFG)
    with st.expander(f"{coin} positioning table"):
        st.dataframe(one, hide_index=True, width="stretch", column_config={
            "cohort": "Cohort", "coin": "Coin", "accounts": "Accounts holding it", "sampled": "Accounts with any position",
            "long_usd": st.column_config.NumberColumn("Long", format="$%,.0f"),
            "short_usd": st.column_config.NumberColumn("Short", format="$%,.0f"),
            "net_usd": st.column_config.NumberColumn("Net", format="dollar"),
            "long_share": st.column_config.NumberColumn("Long share", format="percent")})
