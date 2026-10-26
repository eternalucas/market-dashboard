# Venue capabilities (Step 0)

Checked 26 Oct 2026 against each venue's official docs. Wherever I could, I also made a live read-only call. Findings marked **verified live** came from a real response. **Docs** means the documentation says so but I haven't confirmed it. **Unknown** means neither source settles it.

**Decision (26 Oct 2026):** Nado and Variational are **excluded** from the build, because neither offers per-trader data as readily as Hyperliquid. Their findings are kept below for reference. Other DEXes will be checked before Phase 4.

Summary:

| Capability | Hyperliquid | Nado | Variational Omni |
|---|---|---|---|
| leaderboard | Unofficial stats file (fragile) | No general leaderboard; build our own by scanning subaccounts | None |
| fills_history | Yes, but only the last 10,000 fills per wallet | Yes, per subaccount (`matches`) | **No public per-trader data** |
| funding_history | Yes, per user and per coin | Yes, per subaccount | No (own CSV export only) |
| liquidations | Yes (`liquidation` field on fills) | Yes (`matches_and_liquidations`) | No |
| account_state | Yes | Yes (gateway Subaccount Info, archive Portfolio) | No |
| candles | Yes, but only the most recent 5,000 per interval | Yes, from 1-minute candles up | Market stats only |
| orderbook_history | No (current 20 levels only) | No (current only) | No (RFQ, no book) |

---

## Hyperliquid

Base: `POST https://api.hyperliquid.xyz/info`, no key needed.

| Request | What it gives | Limits | Status |
|---|---|---|---|
| `userFillsByTime` | Fills in a time range | "at most 2000 fills per response and only the 10000 most recent fills are available" | Docs + verified live |
| `userFunding` | Funding paid or received per position per hour (`usdc`, `szi`, `fundingRate`, `coin`) | Time-range responses return at most 500 elements, so paginate | Docs + verified live |
| `userNonFundingLedgerUpdates` | Deposits, withdrawals, transfers (types seen live: `send`, `withdraw`, `spotTransfer`, `cStakingTransfer`) | 500 per response | Docs + verified live |
| `clearinghouseState` | Positions, margin, leverage now | Weight 2 | Docs (already used in this repo) |
| `portfolio` | Account value and PnL history for `day`, `week`, `month`, `allTime` and the `perp*` versions | `allTime` is sparse: 36 points across 4 months for one wallet, unevenly spaced | Verified live |
| `candleSnapshot` | OHLCV, 1m to 1M | "Only the most recent 5000 candles are available": 1m covers about 3.5 days and 1h about 208 days | Docs + verified live |
| `fundingHistory` | Hourly funding rate per coin | 500 per call, history back to May 2023 for BTC | Docs + verified live |
| `l2Book` | Order book now | At most 20 levels per side; no history | Docs |
| `userFees` | The user's fee tier | n/a | Docs (exists; fields to check in Phase 1) |

**Fill fields (verified live):** `coin, px, sz, side, time, startPosition, dir, closedPnl, hash, oid, crossed, fee, builderFee, tid, feeToken, twapId, cloid`.
- The docs say `fee` includes `builderFee`.
- A `liquidation` object (`liquidatedUser`, `markPx`, `method`) appears on fills that took part in a liquidation. It isn't in the docs, but I saw it live. The wallet was the one liquidated only when `liquidatedUser` equals that wallet; otherwise it was on the other side of the liquidation.

**Rate limits:**
- 1,200 weight per minute per IP.
- `clearinghouseState` and `l2Book` cost 2. Most other requests cost 20.
- Fills, funding and ledger requests add weight for every 20 items returned. `candleSnapshot` adds weight for every 60 items returned.
- A full 10,000-fill history costs roughly 600 weight, so about two wallets a minute at worst.

**Leaderboard (unofficial):** `GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard` is what the web app uses, and it isn't in the docs.
- Checked live: 47,633 rows. Each row has `accountValue` plus PnL, ROI and volume for `day`, `week`, `month` and `allTime`.
- There is **no 90-day window**, so 90d has to be computed from our own fills.
- The file is about 20 MB and could change without notice. Treat it as a candidate list only, never as the source of any number we show.

**Gaps:**
1. **History truncation:** only the last 10,000 fills per wallet. Active traders lose older history, and those wallets get flagged "history truncated".
   - Paid alternatives: Hyperliquid's node-data S3 bucket (requester pays, so AWS charges apply) and third-party indexers that sell historical fills, such as [Dwellir](https://www.dwellir.com/docs/hyperliquid/hyperliquid-index/fills) and [GoldRush](https://goldrush.dev/docs/goldrush-hyperliquid/info-api/overview). **Not used without your approval.**
2. **1-minute candles:** only the last ~3.5 days are available. Older trades get the finest interval that still reaches back to them: 5m (~17 days), 15m (~52 days), 1h (~208 days), 4h, 1d. From Phase 1 the recorder also stores 1m candles for every coin a tracked trader uses, so 1m coverage builds up over time.
3. **Order book history:** none. Slippage against the mid at fill time can only be measured for fills after we start recording books. Earlier fills get a candle-based estimate, labelled as an estimate.

Sources:
- [Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Info endpoint: perpetuals](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals)
- [Rate limits and user limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits)

---

## Nado

A central limit order book (CLOB) on Ink. Mainnet archive: `POST https://api.prod.nado.xyz/archive/v1` (the legacy host `archive.prod.nado.xyz/v1` also answers). Gateway: `https://api.prod.nado.xyz/gateway/v1`. No key needed for reads.

**Identity:** a subaccount is 32 bytes: the 20-byte wallet address plus a 12-byte name (for example `default`). Verified live: `0xe728…1fb6` + `64656661756c74…` ("default"). One wallet can have several subaccounts, and isolated positions get their own child subaccounts.
- We store `account_id` = the 32-byte subaccount hex, `owner_address` = the wallet, `subaccount_name` = the name.
- A trader's view groups subaccounts by owner only when the owner address is identical.

| Query | What it gives | Limits | Status |
|---|---|---|---|
| `subaccounts` | Lists every subaccount, or the ones belonging to one address | 500 per page; weight 2 | Docs + verified live: about 111,400 subaccounts, the first created 17 Nov 2025 |
| `matches` | Fills: base and quote filled, fee (incl. sequencer and builder), `is_taker`, `realized_pnl`, `closed_amount`, balances before and after | Up to 5 subaccounts and 500 rows per call; newest first, paged by `idx`/`max_time` | Docs |
| `matches_and_liquidations` | The same, plus liquidation events (`liquidate_subaccount` transactions; no explicit price, only `quote_filled / base_filled`) | Weight 2 + limit × subaccounts / 10 | Docs |
| `interest_and_funding` | Funding payments per subaccount and product, with the rate and oracle price | 100 per call, paged by `next_idx`; weight 5 | Docs |
| `portfolio`, `portfolio_history`, `portfolio_calendar` | Account value and PnL per subaccount (daily grid) | Weight 5; cross-margin subaccounts only | Docs |
| `candlesticks` | OHLCV from 60 s to 4 weeks | Weight 2 + limit/100. The docs give two different maximums (500 and 2000) | Docs + verified live: 1m candles available |
| `funding_rate_history`, `product_snapshots`, `market_snapshots` | Market history | n/a | Docs |
| Rewards `leaderboard` | Trading-competition standings only (pnl, roi, volume), per contest | 500 per page | Docs: **not a general leaderboard** |

**Gap: no leaderboard.** Proposed alternative:
1. Page through all ~111k subaccounts once.
2. Call `portfolio` on each one. At weight 5 that's about 240 calls a minute, so the first pass takes **about 8 hours** in the background.
3. Re-check only subaccounts with new matches after that.

Ranking needs no third party. History retention isn't documented, so I'll check it with the oldest trades in Phase 4.

Sources:
- [Endpoints](https://docs.nado.xyz/developer-resources/api/endpoints)
- [Subaccounts](https://docs.nado.xyz/developer-resources/api/archive-indexer/subaccounts)
- [Matches](https://docs.nado.xyz/developer-resources/api/archive-indexer/matches)
- [Matches & liquidations](https://docs.nado.xyz/developer-resources/api/archive-indexer/matches-and-liquidations)
- [Interest & funding payments](https://docs.nado.xyz/developer-resources/api/archive-indexer/interest-and-funding-payments)
- [Portfolio history](https://docs.nado.xyz/developer-resources/api/archive-indexer/portfolio-history)
- [Candlesticks](https://docs.nado.xyz/developer-resources/api/archive-indexer/candlesticks)
- [Liquidation feed](https://docs.nado.xyz/developer-resources/api/archive-indexer/liquidation-feed)
- [Trading competitions](https://docs.nado.xyz/developer-resources/api/rewards/trading-competitions)
- [Docs index](https://docs.nado.xyz/llms.txt)

---

## Variational Omni

Request-for-quote (RFQ) perps on Arbitrum. Each user has a bilateral settlement pool with the Omni Liquidity Provider (OLP), which is always the counterparty.

**What the API exposes:** only `GET https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/v2/stats`. It gives platform-wide and per-market statistics, with no key needed.
- Rate limit: 10 requests per 10 s per IP and 1,000 per minute globally. Over the limit returns 429 with `retry_after_seconds`.
- The trading API "is still in development, and is not yet available to any users".
- **There is no per-trader endpoint and no leaderboard.**

**Trader's own history:** Omni's portfolio page exports CSVs of trades, funding, realized PnL and transfers. The export covers up to 365 days, with a maximum of 10,000 rows per download.

**On-chain:**
- Contracts on Arbitrum:
  - Settlement Pool Factory: `0x0F820B9afC270d658a9fD7D16B1Bdc45b70f074C`
  - Oracle: `0x84BE56470d45b7f6629A66A219a38681F6BA6172`
  - Core OLP Vault: `0x74bbbb0e7f0bad6938509dd4b556a39a4db1f2cd`
- One settlement transaction from 26 Oct 2026 (`0x8690…662b`, ~700 internal calls) contained only USDC `Transfer` events between pools: batched cash settlement. **No trade prices or sizes appeared on-chain.**
- So on-chain data could give an approximate equity curve per pool. It can't give fills, so it can't support round trips, MAE/MFE or replays.

**Costs:** "There are no trading fees on Omni". Execution cost is the spread against a quoted index/mark. Forced liquidation happens at the quote ± 0.5%. Deposits and withdrawals cost $0.10 each.

**Proposed alternative (instead of building a Variational leaderboard):**
1. Add a Variational adapter whose only capability is market stats, used for the spread model.
2. Add an **"Import Omni CSV"** path so a trader (you, for example) can load their own exported history and get the full per-trader review and backtest.
3. Leave Variational off the Top 100, and say why in the UI.

Indexing settlement pools on-chain would give net USDC flows only. It isn't worth building unless Variational publishes trade-level data.

Sources:
- [API](https://docs.variational.io/technical-documentation/api)
- [Trade and transfer history](https://docs.variational.io/technical-documentation/trade-and-transfer-history)
- [Settlement pools](https://docs.variational.io/variational-protocol/key-concepts/settlement-pools)
- [Mainnet contracts](https://docs.variational.io/technical-documentation/mainnet-contracts)
- [Fees](https://docs.variational.io/omni/trading/fees)
- [API access (help centre)](https://help.variational.io/en/articles/14766804-variational-api-access)

---

## Later venues (interface only, not built)

Lighter, Paradex, Aster and edgeX each get an adapter class that declares its capability flags. They are marked unchecked until the same Step 0 check is done for each.
