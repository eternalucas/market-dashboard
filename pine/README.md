# TradingView indicators (Pine Script v6)

| File | What it shows | Alerts |
|---|---|---|
| `coinbase_premium.pine` | Coinbase BTC-USD vs Binance BTC-USDT, in %, with its average and a z-score read-out | Premium turns positive / negative; premium unusually high / low (z-score beyond a threshold) |
| `rsi_macd_divergence.pine` | Regular bullish and bearish divergences between price and RSI, optionally confirmed by the MACD histogram, plus a higher-timeframe RSI for context | Bullish divergence confirmed; bearish divergence confirmed |

## Add one to a chart

1. In TradingView, open the **Pine Editor** tab at the bottom of a chart.
2. Delete what's there, paste the whole file, click **Save**, then **Add to chart**.
3. To get alerts: click **Alert** (clock icon), set **Condition** to the indicator's
   name, pick the alert (e.g. "Premium turned negative"), and create it.

## Notes

- Divergences are confirmed only after the swing completes (`Pivot right` bars
  later), so a signal never disappears after it prints. The trade-off is a few
  bars of delay.
- The premium uses USDT on Binance, so a USDT de-peg would show up as premium.
- For ETH, change the two symbols to `COINBASE:ETHUSD` and `BINANCE:ETHUSDT`.
