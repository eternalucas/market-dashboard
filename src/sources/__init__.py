"""Free data sources for the Glassnode-style metrics.

One module per source. Each exposes `update(con) -> int` (rows written):
fetch once, store raw values in the observations table under the source's
prefix, and never serve charts live from the API. Derived metrics are
computed separately (src/metrics.py) from these stored raw series.

Series prefixes:
  cm:      Coin Metrics community API      (daily)
  bg:      BGeometrics / bitcoin-data.com   (daily, 7-day delayed on the free tier)
  bc:      blockchain.com charts            (daily)
  mp:      mempool.space history            (daily)
  dl:      DefiLlama                        (daily)
  fng:     alternative.me Fear & Greed      (daily)
  cftc:    CFTC Traders in Financial Futures (weekly)
  yf:      Yahoo Finance                    (daily)
  fred:    FRED                             (source frequency)
  dvol:    Deribit DVOL                     (daily)
  cg:      CoinGecko global snapshot        (daily, recorded from start)
  farside: Farside ETF flows                (daily, if reachable)
"""
