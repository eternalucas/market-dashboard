"""In-depth explanation for every Studio chart, shown under the chart when
the reader opens "About this chart". Each entry is (how it's calculated,
how to read it). The panel adds the data source, history and any
limitation automatically from the chart's spec, so they aren't repeated here.

Written for this dashboard; formulas match the docstrings in src/metrics.
"""
from __future__ import annotations

E: dict[str, tuple[str, str]] = {}


def add(ids: str, calc: str, read: str) -> None:
    for i in ids.split():
        E[i] = (calc, read)


# =========================================================================== Spot: price
add("price",
    "The daily close of BTC in US dollars, taken from Coin Metrics' reference rate: a volume-weighted price built from many exchanges, with outliers removed, stamped at 00:00 UTC of the day it closes.",
    "The base series every other chart is compared against. Checked against Glassnode's own close at five dates since 2021: the largest gap was 0.12%.")
add("ohlc",
    "Daily open, high, low and close candles built from Coinbase's hourly BTC-USD candles: open = first hour's open, high / low = the day's extremes, close = last hour's close.",
    "Green candles closed above their open, red below. Long wicks show intraday rejection. Coinbase's single-venue prices sit within about 0.1% of composite prices, so levels read the same as on Glassnode.")
add("drawdown",
    "Daily close divided by the all-time high reached so far, minus 1. The all-time high includes intraday highs (Coinbase hourly highs), as Glassnode's does, not only daily closes.",
    "0% means price is at a record. Past bear markets bottomed around -75% to -85%; mid-cycle corrections in bull markets have typically stopped at -20% to -35%. Deep drawdowns that stop shrinking often mark capitulation.")
add("regional_mom",
    "Every hourly price change is credited to the region whose working hours (08:00-20:00 local time in Shanghai, Berlin and New York) it fell in; hours shared by two regions are split between them. Each region's changes are summed into a running 'regional price', and the chart shows its 30-day change in USD.",
    "It shows which time zone has been doing the buying or selling over the past month. Persistent US-hours strength alongside Asia-hours weakness, for example, has historically coincided with ETF-led and Coinbase-led demand.")
add("perf_cycle_ath",
    "For each market cycle, price divided by that cycle's all-time high, plotted against days since the high. A cycle high is an all-time high that was followed by a drop of at least 75% before the next one, plus the most recent all-time high.",
    "Lines start at 1 and show how far and how fast each bear market fell and recovered. Comparing the current cycle's line to earlier ones shows whether this drawdown is deeper, shallower, faster or slower than history.")
add("perf_cycle_low",
    "Price divided by each cycle's lowest close (the lowest point between consecutive cycle highs), plotted against days since that low.",
    "Shows how strong each recovery has been from its bottom. Each cycle's peak multiple has been smaller than the one before (diminishing returns), which this chart makes easy to see.")
add("perf_halving",
    "Price divided by the price on each halving day (Nov 2012, Jul 2016, May 2020, Apr 2024), plotted against days since the halving.",
    "Lets you line the current cycle up with earlier post-halving paths. Past cycle tops arrived roughly 12-18 months after a halving; the current line shows where this cycle sits on that template.")
add("rolling_perf",
    "Price today divided by price 7, 30, 90, 180 and 365 days earlier, minus 1.",
    "Short windows show momentum, long ones the trend. When the short-window returns turn up while the long ones are still negative, a trend change is often under way.")
add("period_returns",
    "Calendar-month returns: last close of each month divided by the previous month's last close, minus 1. Green bars are up months, red bars down months.",
    "A quick read of seasonality and streaks. Runs of several red months have marked bear phases; a string of green months has marked accumulation and markup phases.")
add("cagr_4y",
    "Compound annual growth rate over the trailing four years (1,461 days): (price today / price four years ago)^(1/4) - 1.",
    "Four years spans a full halving cycle, so this strips out where in the cycle you start. It has fallen cycle after cycle as bitcoin matures; readings near zero have historically lined up with cycle lows.")
add("power_law",
    "A model price of exp(5.71 x ln(days since 3 Jan 2009) - 38.16): a straight line on a log-log chart, using the coefficients Glassnode publishes (fitted to March 2024). Shown against price and projected two years ahead.",
    "Price has oscillated around this line for over a decade: well above it near cycle tops, below it near cycle lows. The gap between price and the line is the useful part, not the line itself.")
add("mayer",
    "Price divided by its 200-day simple moving average.",
    "Above 2.4 has historically marked overheated tops; below 0.8 has marked deep value. Around 1 means price is at its long-term trend.")
add("pi_cycle",
    "Two lines: the 111-day moving average of price, and twice the 350-day moving average.",
    "When the faster 111-day line crosses above the doubled 350-day line, price has been extremely stretched above trend. That cross fell within days of the 2013, 2017 and 2021 cycle tops. The distance between the lines shows how far from that condition the market is.")
add("investor_tool",
    "The 2-year (730-day) moving average of price, and 5 times that average.",
    "Price below the 2-year average has historically been a strong long-term buy zone; price above 5x the average has marked cycle tops.")
add("ma_200w",
    "The 200-week (1,400-day) moving average of price.",
    "Bitcoin has rarely closed for long below its 200-week average, which made it the classic 'bear market floor'. Price touching it has coincided with major cycle lows.")
add("price_temperature",
    "How many standard deviations price is above its 4-year moving average: (price - 4-year average) / 4-year standard deviation.",
    "Readings above about 6-8 have coincided with cycle tops; near or below zero with cycle lows. It normalises the distance from trend so different cycles can be compared.")
add("realized_vol",
    "The standard deviation of daily log returns over 1 week, 2 weeks, 1, 3, 6 and 12 months, multiplied by the square root of 365 to annualise.",
    "Short windows react to shocks; long windows show the volatility regime. Compressed volatility across all windows has often preceded large moves. Compare with implied volatility (Options dashboards) to see whether options are cheap or rich.")
add("realized_vol_1m",
    "The standard deviation of the last 30 daily log returns, multiplied by the square root of 365.",
    "Bitcoin's 1-month realized volatility has mostly ranged 30-80% a year. Very low readings tend not to last; a break out of a quiet period usually comes with a jump here.")

# =========================================================================== Spot: volume and technicals
add("spot_volume",
    "Daily traded value of BTC against the US dollar and USD stablecoins, summed over every spot market Coinalyze tracks with buy/sell data, in USD.",
    "Rising price on rising spot volume suggests genuine demand; rising price on falling volume suggests a weaker move. Spikes usually come with capitulation or breakouts.")
add("spot_volume_stacked",
    "The same daily spot volume split by exchange, stacked. The six largest exchanges are shown individually and the rest grouped as 'Other'.",
    "Shows where spot activity is concentrated and how that shifts, for example between offshore venues and US venues such as Coinbase.")
add("spot_vd",
    "Volume delta: taker (market-order) buy volume minus taker sell volume each day, in USD.",
    "Green means aggressive buyers dominated, red aggressive sellers. Divergences, such as price rising while delta is negative, often mean the move is driven by derivatives rather than spot.")
add("spot_cvd",
    "Cumulative volume delta: the running total of daily taker buy minus taker sell volume, in USD.",
    "The slope matters more than the level. A rising CVD means persistent net spot buying; price rising while CVD falls is a warning that spot isn't supporting the move.")
add("spot_buy_sell",
    "Taker buy volume and taker sell volume, each in USD per day, over the covered spot markets.",
    "The two lines move together most of the time; watch for stretches where one side clearly dominates.")
add("spot_vol_mcap",
    "Daily spot volume divided by bitcoin's market cap.",
    "A turnover measure: how much of the network's value trades on spot each day. Low readings mean a quiet, illiquid market; high readings mean heavy repositioning.")
add("spot_rel_vol",
    "Today's spot volume divided by the average of the last 30 days.",
    "Above 1 means busier than usual. Readings of 2-3 or more flag events worth looking at, such as liquidations, news or breakouts.")
add("spot_vol_trend",
    "The 7-day average of spot volume divided by the 7-day average from a week earlier, minus 1.",
    "Positive means activity is picking up week over week, negative that it's fading. It smooths out single-day spikes.")
add("spot_vwap",
    "The 30-day volume-weighted average price: the sum of typical price (high + low + close) / 3 times volume, divided by total volume, over the last 30 days.",
    "Roughly the average entry price of the past month's spot volume. Price above VWAP means recent buyers are in profit (supportive); below means they're underwater (overhead supply).")
add("spot_obv",
    "On-balance volume: a running total that adds the day's volume (in BTC) on up days and subtracts it on down days.",
    "A rising OBV with flat price suggests accumulation; falling OBV with flat or rising price suggests distribution.")
add("spot_vpt",
    "Volume price trend: a running total of daily volume multiplied by the day's percentage price change.",
    "Like OBV, but big moves count more than small ones. Divergence between VPT and price hints at weakening trends.")
add("spot_adl",
    "Accumulation/distribution line: each day's volume is weighted by where the close sits in the day's range, from -1 (closed at the low) to +1 (closed at the high), and the results are summed over time.",
    "Rising means closes keep landing near the day's highs (buying pressure); falling means closes near the lows (selling pressure).")
add("spot_mfi",
    "Money Flow Index over 14 days: typical price times volume, split into days when typical price rose and days it fell; MFI = 100 - 100 / (1 + rising flow / falling flow).",
    "A volume-weighted RSI. Above 80 is overbought, below 20 oversold. Divergences against price are often more useful than the levels.")

# =========================================================================== Spot: market cap
add("mcap",
    "Circulating supply times price, in USD (Coin Metrics).",
    "Bitcoin's total network value. Matches Glassnode within 0.13% at every test date.")
add("btc_dominance",
    "Bitcoin's market cap as a percentage of the total crypto market cap. History comes from BGeometrics; recent days come from the CoinGecko global snapshot recorded daily.",
    "Rising dominance means capital is concentrating in BTC (typical of bear markets and early bull markets); falling dominance means money is rotating into altcoins.")
add("total_mcap",
    "Total crypto market cap, derived as bitcoin's market cap divided by its dominance share.",
    "The size of the whole crypto market. Compare its trend with BTC's own market cap to see whether altcoins are adding to or lagging the move.")
add("altcoin_mcap",
    "Total crypto market cap minus bitcoin's market cap (stablecoins included).",
    "Altcoins tend to outperform late in bull markets. A rising altcoin cap while BTC dominance falls is the classic rotation.")
add("stablecoin_mcap",
    "The total circulating supply of USD-pegged stablecoins (DefiLlama), in USD.",
    "Stablecoins are crypto's cash on the sidelines. Growing supply means new dollars entering the ecosystem, which has historically supported prices.")
add("btc_eth_dominance",
    "Bitcoin's market cap divided by the combined market cap of bitcoin and ether, in %.",
    "A cleaner BTC-versus-ETH rotation gauge than overall dominance, since it isn't affected by stablecoin growth.")

# =========================================================================== Order books
add("ob_spread_cb",
    "Recorded every minute: (best ask - best bid) / mid-price, in basis points (1 bp = 0.01%). Each hour shows the average, the narrowest and the widest spread seen.",
    "BTC spreads on Coinbase are normally a fraction of a basis point. Widening spreads mean market makers are pulling back, which usually happens before or during volatile moves.")
add("ob_depth_cb ob_depth_bn",
    "Recorded every minute: the USD value of all resting bids within 1% (100 bp) below the mid-price, and of all asks within 1% above it, averaged per hour.",
    "Depth is how much can trade before price moves 1%. Thin depth means moves can be sharp; a sudden drop in bid depth means buyers are stepping away. Comparing bid and ask depth shows which side is better supported.")
add("ob_imbalance",
    "(bid depth - ask depth) / (bid depth + ask depth) within 1% of mid, per exchange, from -1 (only asks) to +1 (only bids).",
    "Positive means more resting buy interest than sell interest near the price. Imbalance shifts often lead short-term moves, but large orders can be placed and pulled quickly, so it works best alongside other signals.")
add("ob_delta",
    "Bid depth minus ask depth within 1% of mid on Coinbase, in USD.",
    "The same information as imbalance, but in dollars, so you can see the size of the gap as well as its direction.")
add("ob_slope",
    "Depth within 500 bp divided by depth within 25 bp, for each side of the Coinbase book.",
    "A high slope means little liquidity right at the price and much more further out: small trades are cheap, but large ones quickly move price. Rising slopes signal a fragile top of book.")
add("ob_impact",
    "The cost, in basis points, of a $1 million market order that walks the book from the best price until it is filled, measured against the mid-price. Calculated every minute for buys and sells.",
    "The most direct measure of liquidity: how much it costs to trade size right now. Spikes mean liquidity has thinned out.")
add("ob_heatmap",
    "Every 10 minutes, the USD value resting at each $50 price level (bids and asks combined) on Coinbase and Binance, on a fixed price grid. Brighter = more liquidity.",
    "Bright horizontal bands are large resting orders (walls) that price often reacts to. Walls that vanish as price approaches were likely spoofed or pulled. Because the grid is fixed, a wall that stays put reads as a straight line.")
add("perp_ob_depth",
    "Recorded every minute: USD resting within 1% of mid on the Binance, Bybit, OKX and Hyperliquid BTC perpetual books (bids + asks), averaged per hour.",
    "Perpetual liquidity is where most leveraged trading happens. Compare across venues to see where size can actually be traded.")
add("perp_ob_imbalance",
    "(bid - ask) / (bid + ask) depth within 25 bp of mid on each perpetual book.",
    "Very short-range positioning pressure. It flips often; it's most useful when every venue leans the same way.")
add("perp_ob_spread",
    "Average bid/ask spread per hour on each perpetual book, in basis points.",
    "Spreads on the main perp venues are normally a fraction of a basis point; widening across venues signals stress.")
add("perp_ob_depth_oi",
    "Bids plus asks within 1% of mid on a venue's perpetual book, divided by that venue's open interest.",
    "How much of the outstanding positioning the book could absorb before price moves 1%. Low readings mean a liquidation cascade could move price a long way.")

# =========================================================================== Futures
add("fut_oi",
    "The USD value of all open BTC perpetual-futures positions at each day's close, summed over the 25 BTC perpetual contracts Coinalyze covers (stablecoin- and coin-margined).",
    "Rising open interest means new leverage entering. Rising OI with rising price is leveraged longing; with falling price, shorting. Sharp OI drops come with liquidations or deleveraging, which often clears the way for a bottom.")
add("fut_oi_stacked",
    "The same open interest split by exchange, stacked (six largest shown, the rest grouped).",
    "Shows market share and where leverage is building. Binance and Bybit usually dominate; a sudden jump on one venue can point to a single large player.")
add("fut_oi_cash fut_oi_crypto",
    "Open interest split by margin type: cash-margined contracts are collateralised in USD or stablecoins; crypto-margined ones are collateralised in BTC itself.",
    "Crypto-margined positions are doubly exposed: when price falls, both the position and its collateral lose value, which makes cascades worse. A high crypto-margined share therefore means a more fragile market.")
add("fut_oi_crypto_pct",
    "Crypto-margined open interest as a percentage of all perpetual open interest.",
    "This share has fallen over the years as stablecoin margining took over. Higher readings mean more reflexive, liquidation-prone positioning.")
add("fut_leverage",
    "Perpetual open interest in BTC divided by the BTC held on exchanges (Coin Metrics).",
    "A rough measure of how much leverage sits on top of exchange reserves. A rising ratio means leverage is building faster than collateral, which raises the risk of a liquidation-driven move.")
add("fut_volume",
    "Daily traded value of BTC perpetual futures over the covered exchanges, in USD.",
    "Futures volume is usually 5-10x spot volume. Spikes coincide with liquidation cascades and big breakouts.")
add("fut_volume_stacked",
    "Daily perpetual volume split by exchange, stacked (six largest shown, the rest grouped).",
    "Shows which venues drive derivatives activity and how that shifts over time.")
add("fut_buy_sell",
    "Taker (market-order) buy and sell volume on BTC perpetuals, in USD per day.",
    "Shows which side was aggressive. Both rising together is churn; one side clearly dominating is directional pressure.")
add("fut_vd",
    "Taker buy minus taker sell volume on perpetuals each day, in USD.",
    "Persistent negative delta while price holds up often means spot buyers are absorbing derivatives selling, and the reverse.")
add("fut_funding",
    "The funding rate of each perpetual contract, weighted by its open interest and averaged, in % per 8 hours. Venues that pay funding hourly are scaled x8 first.",
    "Funding is what longs pay shorts (when positive) to keep the perp near spot. 0.01% per 8 h is the neutral default. High positive funding means crowded longs; negative funding means crowded shorts and often comes near local bottoms.")
add("fut_funding_all",
    "Funding on each of the 8 largest exchanges by open interest (last 30 days), each weighted by that exchange's own contracts' open interest, in % per 8 hours.",
    "Look for venues that disagree with the rest. One venue far above the others usually points to a crowded local market or a large single trader.")
add("fut_funding_vs_basis",
    "Two annualised carry rates side by side: funding (% per 8 h x 3 x 365) and the 3-month quarterly-futures basis.",
    "Both measure the premium leveraged longs pay. When funding runs well above the basis, perpetual traders are more aggressive than the dated-futures market, which often happens near short-term tops.")
add("fut_btc_eth_funding",
    "BTC open-interest-weighted funding minus ETH funding, in percentage points per 8 hours.",
    "Positive means leverage is more eager on BTC than on ETH, negative the reverse. It's a positioning view of the BTC/ETH rotation.")
add("fut_liq",
    "Daily USD value of long positions (drawn downwards, red) and short positions (drawn upwards) forcibly closed on the covered exchanges.",
    "Big long-liquidation days usually come at the end of sell-offs, when over-leveraged buyers are flushed out; big short liquidations come in short squeezes.")
add("fut_liq_mean",
    "Long and short liquidations averaged per exchange (total divided by the number of exchanges reporting that day).",
    "Removes the effect of how many venues report, so periods with different coverage can be compared.")
add("fut_liq_dom",
    "Long liquidations as a percentage of all liquidations each day.",
    "Above 50% means longs were hit harder. Extreme readings near 90%+ mark one-sided flushes.")
add("fut_oi_mcap",
    "Perpetual open interest divided by bitcoin's market cap.",
    "Leverage relative to the size of the asset. High readings have preceded violent deleveraging.")
add("fut_oi_volume",
    "Open interest divided by daily futures volume.",
    "Roughly how many days of trading it would take to turn over all open positions. High readings mean positions are being held rather than traded.")
add("fut_vol_mcap",
    "Daily futures volume divided by bitcoin's market cap.",
    "Derivatives activity relative to the asset's size; spikes mark speculative frenzies and liquidation events.")
add("spot_futures_vol",
    "Spot volume divided by perpetual futures volume.",
    "Higher means spot is leading, which is generally a healthier, demand-driven market. Lower means derivatives dominate and moves can be more reflexive.")
add("fut_liq_mcap",
    "Daily liquidations divided by bitcoin's market cap.",
    "Puts liquidation size in context: the same dollar liquidation means less in a bigger market.")
add("fut_liq_oi",
    "Daily liquidations divided by open interest.",
    "The share of outstanding positions wiped out that day. Spikes mark capitulation and squeeze events.")
add("fut_openings",
    "Takes the 7-day average of daily % changes in price and in open interest. Rising price with rising OI counts as long openings; falling price with rising OI as short openings. The size is the product of the two trends, and each series is shown as a 90-day z-score so longs and shorts can be compared.",
    "Peaks show when traders were adding leverage most aggressively in one direction. Late-stage long openings near highs have often preceded corrections.")
add("fut_closures",
    "As for openings, but with open interest falling: falling price with falling OI counts as long closures; rising price with falling OI as short closures (short covering). Shown as 90-day z-scores.",
    "Long-closure spikes mark deleveraging sell-offs; short-closure spikes mark squeezes. Both often mark local turning points.")
add("fut_basis_3m",
    "The annualised premium of 3-month futures over spot. For each day, Binance's current- and next-quarter contracts give (futures / index - 1) x 365 / days to expiry, interpolated to exactly 90 days.",
    "The yield of a cash-and-carry trade. 5-15% a year is typical in healthy bull markets; 20%+ signals froth; near zero or negative signals fear.")
add("fut_term_structure",
    "The annualised basis of every listed dated future (Deribit, Binance, OKX) at the latest 15-minute snapshot, plotted against days to expiry.",
    "An upward-sloping curve (contango) is normal. A flat or inverted curve shows stress or heavy hedging demand.")
add("fut_btc_eth_oi_dom",
    "BTC perpetual open interest as a percentage of BTC + ETH open interest.",
    "Shows where speculative leverage is concentrated. Falling readings mean traders are rotating leverage into ETH.")
add("fut_btc_eth_vol_dom",
    "BTC perpetual volume as a percentage of BTC + ETH perpetual volume, smoothed with a 7-day exponential average.",
    "The trading-activity counterpart to OI dominance. ETH taking share has historically come with broader altcoin speculation.")
add("cme_oi",
    "Open interest in CME Bitcoin futures from the CFTC's weekly report: contracts x 5 BTC x price on the report date.",
    "CME is where US institutions trade futures. Growth here reflects institutional demand and basis trading (long ETF, short CME).")
add("cme_volume",
    "Daily volume of the front-month CME bitcoin future (Yahoo BTC=F): contracts x 5 BTC x price.",
    "Institutional trading activity. Spikes around expiries and big macro days are normal.")
add("cme_positioning",
    "Net positions (long minus short contracts) of asset managers, leveraged funds and dealers, from the CFTC Traders in Financial Futures report, weekly.",
    "Leveraged funds are usually net short because they hedge long-spot/ETF basis trades, so their short isn't purely bearish. Asset managers are usually net long. Watch for changes in the trend rather than the level.")
add("cme_options_oi",
    "CME bitcoin options open interest, estimated as the CFTC's combined futures-and-options report minus the futures-only report (in delta-adjusted contracts), weekly.",
    "Shows institutional options activity next to Deribit's mostly offshore market.")
add("total_oi",
    "BTC plus ETH perpetual open interest, in USD.",
    "A broad gauge of crypto leverage. BTC and ETH make up the large majority of all crypto open interest.")

# =========================================================================== Options
add("opt_oi",
    "The USD value of all open Deribit BTC options: contracts (1 contract = 1 BTC) x each expiry's forward price, from the hourly chain snapshot, daily.",
    "Growing options open interest means more hedging and more structured positioning. Large open interest around an expiry can pin price near popular strikes.")
add("opt_oi_expiry",
    "Open call and put contracts per expiry date, from the latest hourly snapshot.",
    "Shows which expiries carry the most positioning. Big quarterly expiries (last Friday of Mar/Jun/Sep/Dec) can move price as positions roll or settle.")
add("opt_oi_strike",
    "Open call and put contracts at each strike price across all expiries, latest snapshot.",
    "Large call open interest above the price and put open interest below act like magnets and walls. Clusters show where traders have placed their bets.")
add("opt_pc_oi",
    "Open put contracts divided by open call contracts, daily.",
    "Above 1 = more puts than calls. Rising put/call ratios mean more hedging or bearish bets. Deribit has traditionally been call-heavy, so readings around 0.5-0.7 are normal.")
add("opt_volume",
    "Daily traded options notional in USD (contracts x index price), summed from every Deribit trade.",
    "Activity spikes come with volatility events and large expiries.")
add("opt_buy_sell",
    "Contracts bought and sold by takers (the side that crossed the spread) each day.",
    "Taker buying means demand for options (paying up for protection or upside); taker selling means traders supplying volatility. Persistent taker selling tends to compress implied volatility.")
add("opt_call_put_vol",
    "Contracts traded in calls and in puts each day.",
    "Call-heavy days come with upside chasing; put-heavy days with hedging.")
add("opt_pc_volume",
    "Daily put volume divided by call volume.",
    "Captures today's flow rather than accumulated positions. Spikes in put volume often come during sell-offs as traders hedge.")
add("opt_atm_iv",
    "For each listed expiry, implied volatility exactly at the money (strike = forward), interpolated across the out-of-the-money smile. These are then interpolated to fixed 1-week, 1-, 3- and 6-month maturities using total variance (IV^2 x time), so the tenors stay constant as expiries roll.",
    "The market's expected annualised volatility at each horizon. Normally the curve slopes upward. When 1-week IV jumps above 3- and 6-month IV, the market is pricing near-term stress.")
add("opt_atm_iv_1m",
    "Constant-maturity 30-day at-the-money implied volatility (see ATM IV (All) for the method).",
    "The headline 'how much movement is expected' number. Compare with 1-month realized volatility: IV well above RV means options are expensive, below means cheap.")
add("dvol",
    "Deribit's DVOL index: 30-day implied volatility computed VIX-style across the whole strike range, daily close, since 2021.",
    "Bitcoin's 'VIX'. Close to 1-month ATM IV but includes the wings, so it rises more when tail protection is bid.")
add("iv_rank",
    "Where today's 1-month ATM IV sits between its lowest and highest values over the last 30, 100 and 365 days: (IV - low) / (high - low).",
    "Near 0% = volatility is at the bottom of its range (options cheap); near 100% = at the top (options expensive). Useful for deciding whether to buy or sell options.")
add("iv_percentile",
    "The share of days in the last 30, 100 and 365 days on which 1-month ATM IV was lower than today.",
    "Similar to IV rank, but not distorted by a single extreme day in the window.")
add("opt_term_structure",
    "At-the-money implied volatility of every listed expiry from the latest snapshot, against days to expiry.",
    "Upward slope (contango) is calm; a downward slope (backwardation) means near-term fear. Bumps around specific dates show events being priced.")
add("opt_smile",
    "Mark implied volatility at every strike of the expiry closest to 30 days (puts below the forward, calls above), latest snapshot.",
    "The shape shows where demand is. A left side higher than the right means puts are richer: downside protection is in demand. A flatter or right-tilted smile means call demand.")
add("opt_skew_norm",
    "(25-delta put IV - 25-delta call IV) / ATM IV, at constant 1-week, 1-, 3- and 6-month maturities. Positive means puts are richer than equally out-of-the-money calls.",
    "The standard sentiment gauge from options. Strongly positive = hedging and fear; negative = traders paying up for upside. Dividing by ATM IV makes high- and low-volatility periods comparable.")
add("opt_cp_skew",
    "Call IV minus put IV at 5, 10, 15, 20 and 25 delta, 1-month constant maturity.",
    "Negative values mean puts are richer at that distance from the money. The far wings (5-10 delta) show tail fear or tail greed more sharply than 25 delta.")
add("opt_delta_surface",
    "Implied volatility of 1-month calls and puts at 5, 10, 15, 20, 25 and 50 delta (50 delta = at the money).",
    "This is the whole smile tracked over time. A widening gap between low-delta puts and ATM means tail hedging is getting expensive.")
add("opt_skew_index",
    "Upside implied volatility minus downside implied volatility at 1-week, 1-, 3- and 6-month constant maturities. Each side comes from integrating out-of-the-money option prices across strikes (calls for upside, puts for downside), variance-swap style.",
    "Unlike 25-delta skew, it uses the whole curve. Negative = the market pays more for downside; positive = upside demand dominates.")
add("opt_up_down_iv",
    "The two components of the skew index at the 1-month tenor: upside IV (from out-of-the-money calls) and downside IV (from out-of-the-money puts), scaled so both equal ATM IV on a flat smile.",
    "Shows which side is moving. A rising downside IV with flat upside IV is fear; a rising upside IV is call chasing.")
add("opt_gex_total",
    "Dealer gamma exposure in USD per 1% move, summed over all contracts: open interest x Black-76 gamma x forward^2 x 0.01, assuming dealers are short calls (negative) and long puts (positive).",
    "Negative GEX means dealers must buy into rallies and sell into dips to stay hedged, which amplifies moves. Positive GEX means they lean against moves, which dampens volatility. The sign assumption matters; see the trade-tagged variant as trade history builds.")
add("opt_gex_strike",
    "The same dealer gamma, shown per strike for the latest snapshot.",
    "Large negative bars are levels where dealer hedging can accelerate a move through the strike. Large positive bars act as pinning levels.")
add("opt_gex_heatmap",
    "Daily dealer gamma per strike over the last 30 days, with strikes grouped into 100 log-spaced bins. Green = positive, red = negative.",
    "Shows how the gamma map migrates with price and expiries. Persistent bands mark levels that keep attracting hedging flow.")
add("opt_max_pain",
    "For each expiry, the settlement price at which total option payouts (calls: open interest x max(S - K, 0); puts: open interest x max(K - S, 0)) would be smallest. Tracked for the expiries nearest 1 week, 1, 3 and 6 months, and over all open interest.",
    "Price is sometimes pulled towards max pain into expiry, especially for large monthly and quarterly expiries. It's a positioning reference, not a forecast.")
add("opt_itm",
    "For each strike of the expiry nearest 30 days: N(d2) for calls and N(-d2) for puts, using that strike's implied volatility and the expiry's forward (Black-76). This is the risk-neutral probability of finishing in the money.",
    "Read it as the options market's implied odds. The strike where the call and put curves cross is about the forward price. These are risk-neutral odds, so they include risk premia and aren't pure forecasts.")
add("opt_premiums",
    "From every Deribit option trade: premium in USD = price (BTC) x contracts x index price, grouped by who initiated the trade. 'Paid' = takers buying calls or puts; 'received' = takers selling them.",
    "Shows where money is going. Heavy put buying is hedging; heavy call buying is upside chasing; heavy selling of either is traders collecting premium, usually when they expect calm.")
add("opt_net_premium_strike",
    "Over the last 24 hours, premium paid minus premium received (taker side) at each strike, for calls and puts.",
    "Shows the strikes where traders spent the most to gain exposure. Large net call premium at a high strike is a bet or hedge on a rally to that level.")
add("opt_premium_heatmap",
    "Daily net premium (paid minus received) in $5,000 strike buckets, over time. Green = net buying, red = net selling.",
    "Tracks how the strikes traders target move with price. Bands of persistent call buying above price suggest sustained upside demand.")
add("opt_pwms",
    "Each day, the strike at which cumulative traded premium (sorted by strike) passes half of the total, for calls and puts, split by moneyness (in the money; at the money within 5% of index; out of the money).",
    "Shows the price levels where options money is concentrated. Out-of-the-money call medians rising over time mean traders keep raising their upside targets.")
add("opt_bbi",
    "(Premium paid by call buyers - premium paid by put buyers) / (their sum), daily, overall and by maturity: short (0-30 days), mid (30-90 days), long (more than 90 days).",
    "+1 = only call buyers, -1 = only put buyers. Short-dated readings show near-term conviction, long-dated ones strategic positioning. Divergence between them is worth noting.")
add("opt_combo",
    "Premium on legs of multi-leg strategy trades (spreads, straddles and other structures executed as combos or blocks on Deribit): paid by buyers, received by sellers, and net.",
    "Structured trades are mostly institutional. Rising net buying of structures means larger players are paying for defined-risk exposure.")
add("ibit_atm_iv",
    "Constant-maturity at-the-money implied volatility of options on BlackRock's IBIT ETF, recorded after each US close, using Yahoo's implied volatilities and a flat 4% rate.",
    "The US-listed, TradFi side of bitcoin volatility. Compare with Deribit ATM IV: gaps show different demand between the two investor bases.")
add("ibit_skew",
    "(25-delta put IV - 25-delta call IV) / ATM IV for IBIT options, at constant maturities.",
    "Sentiment of US ETF option traders. IBIT has often shown more call demand than Deribit, driven by yield-generating and speculative retail flows.")
add("ibit_oi",
    "Total open IBIT option contracts (each covers 100 shares), recorded daily.",
    "Growth here tracks how fast US institutions and retail are using listed options on bitcoin exposure.")
add("ibit_pc",
    "IBIT open put contracts divided by open call contracts.",
    "Higher means more hedging in the ETF wrapper.")
add("ibit_max_pain",
    "Max pain (the settlement price minimising total option payouts) for the IBIT expiries nearest 1 week, 1, 3 and 6 months, in IBIT share price.",
    "A pinning reference for IBIT expiries. Divide BTC's price by IBIT's price to compare levels.")

# =========================================================================== ETFs / DATs
add("etf_netflows",
    "The day-over-day change in BTC held by all US spot bitcoin ETFs combined (BGeometrics holdings). Like Glassnode, flows are counted in BTC from reported holdings; when holdings aren't available yet, Farside's USD flows divided by the daily close stand in.",
    "Green = net creations (the ETFs bought bitcoin), red = redemptions. Sustained inflows have been one of the strongest drivers of price since January 2024.")
add("etf_netflows_usd",
    "Daily net creations and redemptions in US dollars, all US spot bitcoin ETFs, from Farside's table (US$ millions x 1e6). Days not yet reported are left empty rather than shown as zero.",
    "The flow figure most often quoted in the news. Big single-day outflows usually come with risk-off moves in equities.")
add("etf_flows",
    "Daily US$ flows for each ETF (IBIT, FBTC, GBTC, ARKB, BITB, HODL and others), from Farside.",
    "Shows which issuers are winning or losing assets. GBTC's long run of outflows and IBIT's dominant inflows are the clearest examples.")
add("etf_balances",
    "Total BTC held by US spot bitcoin ETFs (BGeometrics).",
    "The ETFs have become one of the largest holders of bitcoin. The trend matters more than daily changes.")
add("etf_cost_basis",
    "Cumulative net USD flows divided by the cumulative BTC bought, where each day's BTC bought = that day's USD flow / that day's close. This gives the average price the ETFs paid.",
    "When price falls below the ETF cost basis, the average ETF holder is underwater, a level that has acted as support and as a stress point.")
add("etf_volume etf_volume_fund",
    "Daily dollar trading volume (shares x close) of each US spot ETF on the stock exchange (Yahoo), in total and by fund.",
    "Exchange trading volume isn't the same as flows. It includes holders trading with each other. Spikes show heavy repositioning, often on large BTC moves.")
add("etf_price",
    "Daily closing share price of each US spot ETF (Yahoo).",
    "Each tracks BTC with a different number of BTC per share, so levels differ; the moves are what to compare.")
add("purpose_etf",
    "The Purpose Bitcoin ETF (Toronto, BTCC-B): daily closing price and dollar volume, in CAD.",
    "The first spot bitcoin ETF in North America (2021), a Canadian demand gauge.")
add("dat_prices",
    "Daily share prices of listed companies holding bitcoin on their balance sheet (Strategy, Metaplanet, MARA and others), in their local currency, log scale.",
    "These stocks trade as leveraged bitcoin proxies. When they underperform BTC, their premium to the bitcoin they hold (mNAV) is shrinking.")
add("dat_volume",
    "Daily dollar trading volume of the treasury companies' shares.",
    "Rising volume in these names shows equity investors chasing or dumping bitcoin exposure through the stock market.")

# =========================================================================== Macro / TradFi
add("macro_cb",
    "Total assets on the Federal Reserve's balance sheet (US$ billions, weekly) and the Eurosystem's (EUR billions, weekly), from FRED.",
    "Expanding central-bank balance sheets add liquidity to markets; shrinking them (quantitative tightening) drains it. Bitcoin has tended to do best when global liquidity is rising.")
add("macro_rates",
    "The Fed funds target rate (upper bound) and the ECB deposit facility rate, from FRED.",
    "Higher policy rates raise the return on cash and tighten financial conditions, usually a headwind for risk assets like bitcoin. Cutting cycles have tended to support them.")
add("macro_m2",
    "US M2 money supply (cash, deposits and money-market funds), US$ billions, monthly, from FRED.",
    "Bitcoin is often framed as a hedge against monetary expansion. M2 growth has loosely led bitcoin's major cycles.")
add("macro_core_infl",
    "Year-over-year change in US core CPI and euro-area core HICP (both exclude food and energy), from FRED.",
    "Core inflation drives central-bank decisions. Falling core inflation opens the door to rate cuts.")
add("macro_infl_exp",
    "University of Michigan 1-year inflation expectations (survey) and the 5-year breakeven inflation rate (from Treasury prices), from FRED.",
    "Rising expectations can push central banks to stay tight. They also feed the 'inflation hedge' narrative for bitcoin.")
add("macro_debt",
    "US federal debt as a percentage of GDP (quarterly) and federal spending as a percentage of GDP (annual), from FRED.",
    "Structural fiscal pressure underpins the long-term 'debasement' argument for scarce assets.")
add("macro_activity",
    "The OECD composite leading indicator for the US, the University of Michigan consumer sentiment index, and euro-area consumer confidence, from FRED.",
    "Leading indicators turning up have tended to coincide with risk-on phases.")
add("tradfi_indices",
    "Daily closes of the S&P 500, Nasdaq 100 and Euro Stoxx 50 (Yahoo), log scale.",
    "Bitcoin's correlation with US tech stocks has been high since 2020, especially in risk-off episodes.")
add("tradfi_fear",
    "The VIX (S&P 500 30-day implied volatility) and VSTOXX (Euro Stoxx 50 equivalent), daily (Yahoo).",
    "VIX spikes mark equity stress, which has usually spilled into crypto. Compare with DVOL.")
add("tradfi_yields",
    "US 2-year and 10-year Treasury yields and the 10Y-2Y spread (daily), and the German 10-year Bund yield (monthly), from FRED.",
    "Rising real yields tighten conditions. A curve moving out of inversion (spread turning positive) has historically come around recessions and easing cycles.")
add("tradfi_commodities",
    "Daily closes of gold, silver and WTI crude futures (Yahoo).",
    "Gold is bitcoin's main 'store of value' comparison; oil drives inflation expectations.")
add("tradfi_fx",
    "The US Dollar Index (DXY) and EUR/USD, daily (Yahoo).",
    "A strong dollar tightens global liquidity and has usually been a headwind for bitcoin; a weakening dollar a tailwind.")

# =========================================================================== On-chain: network
add("active_addresses",
    "The number of unique addresses that sent or received BTC in successful transactions each day (Coin Metrics).",
    "A rough proxy for network usage. Rising activity alongside rising price signals organic demand. One user can control many addresses and one address can serve many users (exchanges), so read it as a trend, not a user count.")
add("non_zero_addresses",
    "The number of addresses holding a non-zero BTC balance (Coin Metrics).",
    "A slow-moving adoption gauge. Dust and address-reuse practices affect it, so use the trend.")
add("address_momentum",
    "The 30-day average of active addresses divided by the 365-day average.",
    "Above 1 = activity is above its yearly norm (expanding usage); below 1 = cooling. Crossings of 1 have often lined up with trend changes.")
add("tx_count",
    "The number of transactions confirmed on-chain each day (Coin Metrics).",
    "Network demand for block space. Note that inscriptions and runes since 2023 have inflated counts without implying more economic use.")
add("tx_rate",
    "Transactions per second: daily transaction count / 86,400.",
    "The same information as transaction count, in throughput terms.")
add("transfer_count",
    "The number of individual transfers (payments to distinct outputs) each day, as Coin Metrics defines them.",
    "A finer measure of activity than transactions, since one transaction can pay many recipients.")
add("tx_momentum",
    "The 30-day average of daily transactions divided by the 365-day average.",
    "Above 1 = block-space demand is running above its yearly norm.")
add("transfer_volume",
    "The estimated USD value transferred on-chain each day, with change outputs removed by blockchain.com's heuristics (change-adjusted).",
    "The economic throughput of the network. Spikes often come with exchange movements and large holder activity.")
add("transfer_volume_mean",
    "Change-adjusted USD transfer volume divided by the number of transactions.",
    "The average payment size. A rising average means larger players dominate on-chain activity.")
add("utxo_count",
    "The number of unspent transaction outputs: the 'coins' in the UTXO set (BGeometrics).",
    "Growth reflects new outputs from activity, including dust from inscriptions. Consolidation phases, when large holders merge outputs, show up as dips.")
add("blocks_mined",
    "The number of blocks found each UTC day (Coin Metrics). The protocol targets 144 (one every 10 minutes).",
    "More than 144 means hash rate grew faster than the last difficulty adjustment expected; fewer means it fell. Matches Glassnode exactly.")
add("block_interval",
    "The mean time between blocks each day, in minutes: 1,440 / blocks mined.",
    "The difficulty adjustment keeps this near 10 minutes over each 2,016-block epoch.")
add("block_size",
    "The mean block size in MB (blockchain.com).",
    "Full blocks (around 1.5-2 MB with SegWit and witness data) mean strong demand for block space and higher fees.")
add("supply",
    "Total BTC issued so far (Coin Metrics).",
    "Follows bitcoin's fixed schedule towards 21 million; about 95% has been issued.")
add("issuance",
    "New BTC created each day through block rewards (Coin Metrics).",
    "Steps down by half at each halving (the latest was April 2024: about 450 BTC a day). Lower issuance means less natural sell pressure from miners.")
add("inflation",
    "Annualised supply growth: daily issuance x 365 / circulating supply, in %.",
    "Now below 1% a year, lower than gold's. It halves again at the next halving.")
add("pct_21m",
    "Circulating supply / 21,000,000, in %.",
    "Shows how little supply remains to be issued.")
add("fees_total",
    "Total transaction fees paid to miners each day, in USD (Coin Metrics fees in BTC x price).",
    "A direct measure of demand for block space. Fee spikes come with congestion: speculative manias, inscriptions, or panic moves to and from exchanges.")
add("fees_mean",
    "Total daily fees / number of transactions, in USD.",
    "The typical cost to transact. High averages price out small payments and push users to Lightning or to batching.")
add("frm",
    "Fee Ratio Multiple: (block rewards + fees) / fees.",
    "How much the network's security depends on the subsidy rather than fees. Lower is better for long-term security; it falls during fee spikes and rises again after.")
add("thermocap",
    "Cumulative miner revenue (block rewards + fees, in USD at the time) since 2009: the total spent securing the network.",
    "A measure of the network's 'security cost basis'. Its long-run growth is steady because it never falls.")
add("mcap_thermocap",
    "Market cap divided by thermocap.",
    "How richly the market values the network relative to the total ever paid to secure it. Cycle tops have come at high multiples, bottoms at low ones; the ratio has trended down over time.")

# =========================================================================== Miners
add("hashrate",
    "The network's hashing power each day, estimated as difficulty x 2^32 x blocks found / 86,400 (the standard estimator, matching Glassnode within 0.3%).",
    "Hash rate measures miners' investment in securing the network. New all-time highs mean miners are expanding despite price; sharp drops mean capitulation or outages. Daily values are noisy because block discovery is random.")
add("difficulty",
    "Mining difficulty after each adjustment (every 2,016 blocks, about two weeks), carried forward daily (mempool.space).",
    "Adjusts so blocks keep arriving every 10 minutes. It's a smoothed version of hash rate.")
add("difficulty_adj",
    "The % change in difficulty at each adjustment.",
    "Big positive adjustments mean hash rate is flooding in; negative ones mean miners are switching off, usually when revenue falls.")
add("miner_revenue",
    "Daily miner income in USD: block rewards (new BTC x price) and transaction fees, stacked.",
    "Total income for the mining industry. Halvings cut the reward part in half overnight; fees occasionally make up a meaningful share.")
add("hashprice",
    "Daily miner revenue (USD) divided by hash rate in EH/s: the USD earned per exahash per day.",
    "Miners' revenue per unit of work. Falling hash price squeezes less efficient miners, and sustained lows have historically pushed them to sell BTC or switch off.")
add("revenue_vs_yearly",
    "Daily miner revenue divided by its 365-day average.",
    "Above 1 = miners are earning more than their yearly norm (bullish periods); deep below 1 = miner stress.")
add("fee_z",
    "Z-score of daily fee revenue against its 2-year and 4-year history: (fees - average) / standard deviation.",
    "Readings above 2 mark unusual fee demand, which has coincided with speculative peaks and congestion events.")
add("puell",
    "The USD value of daily issuance divided by its 365-day average.",
    "Measures miners' income relative to normal. Above 4 has marked cycle tops (miners earn a lot and tend to sell); below 0.5 has marked bottoms (miner capitulation).")
add("hash_ribbon",
    "The 30-day and 60-day moving averages of hash rate.",
    "When the 30-day average falls below the 60-day, miners are capitulating. The moment it crosses back above has historically been a good long-term buy signal.")
add("difficulty_ribbon",
    "Seven moving averages of difficulty (14, 25, 40, 60, 90, 128 and 200 days).",
    "When the ribbon compresses or inverts, mining growth has stalled, typically late in bear markets, which have often been good accumulation zones.")
add("difficulty_compression",
    "The standard deviation of the seven difficulty averages divided by their mean.",
    "Below 0.05 = the ribbon is compressed (mining growth flat). Long compressed periods have preceded major rallies.")
add("difficulty_regression",
    "A fitted model of market cap against difficulty: log10(market cap) = a + b x log10(difficulty), fitted over all history and converted to a model price by dividing by supply.",
    "Difficulty reflects what it costs to mine, so the model price acts as an estimate of mining's 'fair value'. Price below it has marked deep value; far above it, euphoria.")

# =========================================================================== Lightning / mempool
add("ln_capacity",
    "Total BTC locked in public Lightning Network channels (mempool.space).",
    "Lightning is bitcoin's payments layer. Capacity growth shows how much BTC people commit to it.")
add("ln_nodes",
    "The number of public Lightning nodes (Tor, clearnet, both, and unannounced).",
    "The breadth of the network. Many private channels aren't visible, so this understates true usage.")
add("ln_channels",
    "The number of public Lightning payment channels.",
    "Channel counts falling while capacity rises means the network is consolidating around larger, better-connected nodes.")
add("ln_channel_size",
    "Average channel size: capacity / channels, in BTC.",
    "Bigger channels support larger payments and point to professional routing nodes.")
add("ln_node_capacity",
    "Average capacity per node: capacity / nodes, in BTC.",
    "How much liquidity a typical node commits.")
add("ln_channels_change",
    "The day-over-day change in public channel count (opens minus closes, net).",
    "Net closures often come when on-chain fees fall (cheap to close) or when nodes consolidate.")
add("ln_nodes_change",
    "The day-over-day change in public node count.",
    "Day-to-day changes are noisy; watch the trend.")
add("ln_fees",
    "The median channel fee rate (parts per million of the payment) and the median base fee (millisatoshis per payment), recorded daily.",
    "The cost of routing payments over Lightning. It stays tiny compared with on-chain fees.")
add("mempool_count",
    "The number of unconfirmed transactions waiting in the mempool, recorded every 10 minutes (mempool.space).",
    "A growing backlog means demand for block space exceeds supply and fees rise.")
add("mempool_size",
    "The total virtual size (vMB) of unconfirmed transactions. One block clears about 1 vMB.",
    "Divide by 1 to estimate how many blocks the backlog would take to clear at full blocks.")
add("mempool_fees",
    "The total fees (BTC) offered by all unconfirmed transactions.",
    "The money waiting to be claimed by miners. Spikes come with congestion events.")
add("mempool_fee_rates",
    "The average fee rate (total fees / total size) and the size-weighted median fee rate of unconfirmed transactions, in sat/vB.",
    "What it currently costs to get into a block. A median well below the average means a few high-fee transactions are skewing it.")
add("mempool_size_bands mempool_fee_bands",
    "The mempool split by fee-rate band (sat/vB): virtual size and total fees waiting in each band, from mempool.space's fee histogram.",
    "Shows how urgent the queue is. A thick high-fee band means competition for the next blocks; a thick low band is patient, low-priority traffic.")

# =========================================================================== Exchanges
add("ex_balance",
    "The BTC held on addresses Coin Metrics attributes to exchanges.",
    "A long, steady decline since 2020 shows coins moving into self-custody and ETFs. Rising balances can mean holders preparing to sell.")
add("ex_balance_pct",
    "Exchange balance as a percentage of circulating supply.",
    "The share of all bitcoin that's readily available to trade.")
add("ex_netflow",
    "BTC deposited to exchanges minus BTC withdrawn, each day. Green = net inflow, red = net outflow.",
    "Large net inflows often come before selling; sustained outflows suggest accumulation. Single days can be internal reshuffling, so look at runs.")
add("ex_inout",
    "Total BTC deposited to and withdrawn from exchanges each day.",
    "Shows the gross activity behind the netflow.")
add("ex_npc",
    "The 30-day change in exchange balance (green = balances rising, red = falling).",
    "Smooths daily noise into a clear accumulation or distribution signal.")
add("por",
    "BTC held in the wallets each exchange publishes as its reserves (DefiLlama's CEX transparency data), stacked by exchange.",
    "Self-reported reserves. Sudden drops show withdrawals or reshuffles; differences against Coin Metrics' exchange balance come from how wallets are attributed.")

# =========================================================================== Valuation
add("realized_price",
    "Realized cap divided by supply: the average price at which every coin last moved on-chain, i.e. the network's average cost basis. Realized cap comes from Coin Metrics' market cap divided by its MVRV.",
    "Price below realized price means the average holder is at a loss, historically the deepest-value zone. Bear markets have bottomed near or below it.")
add("realized_cap",
    "The sum of every coin valued at the price when it last moved: market cap / MVRV.",
    "It only grows when coins change hands at higher prices, so it tracks real capital inflows better than market cap does.")
add("mvrv",
    "Market cap divided by realized cap.",
    "How far price sits above holders' average cost. Above about 3.7 has marked cycle tops; below 1 (holders at a loss on average) has marked bottoms.")
add("mvrv_z",
    "(market cap - realized cap) divided by the standard deviation of market cap, computed over all history up to each day (as Glassnode does).",
    "Normalises MVRV for volatility. Readings above 7 have marked cycle tops; below 0 has marked bottoms.")
add("nupl",
    "Net unrealized profit/loss: 1 - 1/MVRV, the share of market cap that is unrealized profit.",
    "Zones: below 0 capitulation, 0-0.25 hope/fear, 0.25-0.5 optimism/anxiety, 0.5-0.75 belief/denial, above 0.75 euphoria. Tops have come in euphoria, bottoms in capitulation.")
add("mvrv_bands",
    "Realized price multiplied by fixed MVRV levels (0.8, 1, 1.5, 2, 2.5, 3, 3.5).",
    "Turns MVRV zones into price levels: where price would sit at each historical extreme. Upper bands have capped cycle tops; the 0.8-1 bands have marked bottoms.")
add("mvrv_dev_bands",
    "Realized price multiplied by (average MVRV + k standard deviations), with the average and standard deviation over all history, for k from -1 to +2.5.",
    "A statistical version of the pricing bands. The +2 sigma and higher bands have marked overheated markets.")
add("sth_lth_price",
    "The average cost basis of short-term holders (coins that moved in the last 155 days) and long-term holders (older coins), from BGeometrics.",
    "The short-term holder cost basis is the key bull/bear line: in bull markets price tends to hold above it, and losing it often starts corrections. The long-term holder basis sits far below in bull markets.")
add("sth_lth_mvrv",
    "MVRV for each cohort: price divided by that cohort's cost basis.",
    "Short-term holder MVRV below 1 means recent buyers are underwater, a common capitulation trigger. Long-term holder MVRV shows how much profit the most patient holders sit on.")
add("sopr",
    "Spent Output Profit Ratio: the USD value of coins when spent, divided by their value when acquired, for all coins moved that day.",
    "Above 1 = coins are being sold at a profit on average; below 1 = at a loss. In bull markets, dips to 1 tend to hold (holders refuse to sell at a loss); in bear markets, 1 acts as resistance. Matches Glassnode within 0.14%.")
add("asopr",
    "Adjusted SOPR: SOPR excluding outputs younger than one hour, which removes noise from exchange and internal shuffles.",
    "A cleaner read of the profit or loss taken by holders who actually moved coins.")
add("sth_lth_sopr",
    "SOPR computed separately for short-term holders (coins held under 155 days) and long-term holders.",
    "Short-term holder SOPR below 1 shows new buyers panic-selling at a loss; long-term holder SOPR spiking shows old hands taking big profits, typical near tops.")
add("sth_lth_nupl",
    "NUPL computed separately for each cohort: the share of the cohort's value that is unrealized profit.",
    "Long-term holder NUPL above 0.75 has marked tops; short-term holder NUPL deeply negative has marked panic bottoms.")
add("hodl_waves",
    "The share of supply in each age band, based on when coins last moved (under 1 day, 1 day to 1 week, ... over 10 years), normalised to 100%.",
    "Older bands growing means holders are sitting tight (typical of bear markets and accumulation). Young bands swelling means old coins are being sold to new buyers, which peaks near cycle tops.")
add("rcap_hodl_waves",
    "The same age bands weighted by realized value (each coin at the price it last moved) instead of coin count.",
    "Emphasises where the money is. The young bands swell sharply near tops, when new buyers pay the highest prices.")
add("rhodl",
    "The realized-value share of the 1-week band divided by the 1-2-year band, multiplied by the network's age in days.",
    "Compares hot money with established holders. Very high readings have marked cycle tops, low readings bottoms; the age weighting keeps cycles comparable.")
add("cdd",
    "Coin days destroyed: for every coin spent, its amount times the days since it last moved, summed per day.",
    "Spikes mean old coins are moving: long-term holders selling or reorganising. Low CDD means holders are sitting tight.")
add("cdd_sa",
    "Coin days destroyed divided by circulating supply.",
    "Makes readings comparable across years as supply grows.")
add("cdd_90",
    "The 90-day average of coin days destroyed.",
    "A smoothed view of old-coin spending. Rising trends mean long-term holders are distributing.")
add("vdd_multiple",
    "Value days destroyed (coin days destroyed x price) over the last 30 days relative to its 365-day average.",
    "High readings (above about 2.9) have marked tops, when old coins are spent at high prices; low readings mark quiet accumulation.")
add("asol",
    "Average spent output lifespan: the average age, in days, of the coins spent each day.",
    "Rising ASOL means older coins are being spent: long-term holder activity.")
add("cost_basis_models",
    "Cost-basis price models from BGeometrics: True Market Mean (the cost basis of active investors), Investor Price, Delta Price (realized cap minus average market cap, per coin) and Thermo Price.",
    "Each is a different estimate of 'fair value'. Delta Price has sat near cycle bottoms; True Market Mean splits bull and bear regimes.")
add("aviv",
    "Active value to investor value: the MVRV of the 'active' supply (Cointime economics).",
    "Like MVRV, but ignores dormant coins. High readings mark overheating, low readings mark value.")
add("supply_profit",
    "The share of circulating supply whose price when it last moved is below today's price.",
    "Above 95% has marked euphoric tops; below 50% has marked bear-market bottoms.")
add("supply_pl",
    "The BTC in profit and the BTC at a loss, stacked.",
    "The balance shifts sharply around cycle turning points.")
add("realized_pl",
    "USD profit realized and USD loss realized by coins spent each day (each coin's spend price vs its acquisition price).",
    "Shows who is selling and how. Big realized profits mean holders are cashing out; big realized losses mean capitulation.")
add("nrpl",
    "Net realized profit/loss: realized profit minus realized loss, per day.",
    "Large positive spikes mark profit-taking peaks, large negative spikes mark capitulation lows.")
add("rpl_ratio",
    "Realized profit divided by realized loss.",
    "Above 1 = profit-taking dominates; below 1 = losses dominate. Sustained readings above 5 have marked late bull markets.")
add("utxo_profit",
    "The percentage of unspent outputs whose price when created is below today's price.",
    "A count-based cousin of percent supply in profit, less influenced by a few large outputs.")
add("address_counts",
    "The number of addresses in each balance band (under 1 BTC, 1-10, 10-100, 100-1k, 1k-10k, over 10k), log scale.",
    "Small-holder counts track retail adoption; the 1k+ bands track whales and institutions (including exchanges and ETF custodians).")
add("address_supply",
    "The BTC held by addresses in each balance band, stacked.",
    "Shows how supply is distributed and which cohort is accumulating. Exchange and custodian wallets sit in the largest bands.")

# =========================================================================== Indicators
add("nvt",
    "Network value to transactions: market cap divided by daily on-chain USD transfer volume.",
    "Bitcoin's 'P/E ratio'. High readings mean the valuation is running ahead of on-chain use; low readings mean heavy usage relative to value.")
add("nvts",
    "NVT signal: market cap divided by the 90-day average of daily USD transfer volume.",
    "A smoother NVT that's more useful as a top signal. Spikes have come before major corrections.")
add("velocity",
    "Daily USD transfer volume divided by market cap: the inverse of NVT.",
    "How quickly coins circulate. It has been falling for years as more supply is held long-term.")
add("s2f",
    "The stock-to-flow model: S2F = circulating supply / BTC issued over the last 365 days; model price = exp(-1.84) x S2F^3.36 (PlanB, 2019). Shown against price.",
    "A scarcity model that jumps at each halving. Price tracked it closely until 2021 and has fallen well below it since, so it's best read as a historical reference.")
add("s2f_deflection",
    "Price divided by the stock-to-flow model price.",
    "Above 1 = price above the model, below 1 = below it.")
add("ssr",
    "Stablecoin supply ratio: bitcoin's market cap divided by the total stablecoin market cap.",
    "Low SSR means stablecoins have more buying power relative to BTC (dry powder on the sidelines), which has historically been bullish. High SSR means less fuel.")
add("ssr_osc",
    "Where SSR sits within its 200-day Bollinger band: (SSR - 200-day average) / (2 x 200-day standard deviation). -1 = lower band, +1 = upper band.",
    "Near -1, stablecoin buying power is unusually high compared with the past 200 days, a contrarian bullish signal. Near +1 it's unusually low.")
add("fear_greed",
    "alternative.me's Crypto Fear & Greed Index (0-100), built from volatility, momentum and volume, social media, dominance and search trends.",
    "A contrarian sentiment gauge. Extreme fear (under 25) has often been a buying opportunity; extreme greed (over 75) a time for caution.")
add("altseason",
    "The share of about 50 large altcoins (Yahoo) whose 90-day return beat BTC's, x 100.",
    "75 or higher = altcoin season (most alts outperforming BTC); 25 or lower = bitcoin season. Moves above 75 have been rare and short-lived, usually late in bull markets.")

# =========================================================================== Stablecoins
add("stable_supply",
    "The total circulating supply of USD-pegged stablecoins (DefiLlama).",
    "New stablecoins are minted when dollars come into crypto. Expanding supply has been a reliable sign of capital inflows.")
add("stable_npc",
    "The 30-day change in total stablecoin supply (green = expanding, red = contracting).",
    "Turning points in this series have tended to lead turning points in crypto liquidity.")
add("stable_30d",
    "The 30-day % change in total stablecoin supply.",
    "The same signal as net position change, in growth-rate terms, so different-sized markets can be compared.")
add("stable_dominance",
    "Each major stablecoin's supply as a share of the total.",
    "Tracks competition between issuers (USDT vs USDC and newer entrants), which matters for regulatory and counterparty risk.")
add("stable_chains",
    "Stablecoin supply on each blockchain (Ethereum, Tron, Solana, BSC and others), stacked.",
    "Shows where dollar liquidity sits and where on-chain activity is moving.")

# =========================================================================== Social Risk (own indicator)
add("social_risk",
    "Twelve inputs in two groups. Attention (65%): Wikipedia views in English and 12 other languages, Google searches, the App Store ranks of crypto apps, views on crypto YouTube channels, Hacker News mentions, news coverage (GDELT) and any X impressions you log. Speculation (35%): Fear & Greed, perpetual funding, the Altcoin Season Index and the memecoin share of altcoin volume. Each input is turned into a share where a denominator exists (e.g. crypto articles / all news articles), then scored 0-1 by where today sits in its own last two years (a rolling percentile; inputs with a fixed 0-100 scale are rescaled instead). Inputs are weighted within each group, groups are weighted into the score, and a day needs at least 3 inputs to get a score.",
    "Above 0.85 (red) retail attention and risk-taking are at or near two-year highs together, which is what crowded tops have looked like. Below 0.30 (green) nobody is paying attention, which has been typical of bear-market lows. It is a crowding gauge, not a timing signal: attention can stay high for months in a strong bull market, so read it alongside on-chain valuation (MVRV, NUPL) and leverage. The 'Does it work?' tables under this dashboard test it against what BTC did next.")
add("social_groups",
    "Attention is the weighted mean of the eight attention inputs; Speculation the weighted mean of the four speculation inputs; Social Risk = 0.65 x Attention + 0.35 x Speculation (if one group has no data, the other carries the score).",
    "When both lines are high together, attention is turning into leverage and memecoin trading: the late-cycle pattern. Attention spiking while speculation stays low usually means a crash or a scare is in the news (people are looking, not buying).")
add("social_contrib",
    "Each input's reading x its weight within its group / that day's total weight of the inputs present, x its group's weight / that day's total group weight. The stacked areas add up exactly to the Social Risk score.",
    "Shows which inputs are pushing the score up or down. A score carried by one or two inputs is weaker evidence than one where most inputs agree.")
add("social_coverage",
    "The number of the twelve inputs that had a reading on each day.",
    "Before 2018 the score rests on Wikipedia, Google Trends and Hacker News only; Fear & Greed, funding and the newer inputs join later. The App Store, YouTube and news inputs only have history from when this dashboard first recorded them, so the count rises over the first months.")
add("social_c_wikipedia_en",
    "For each of 11 English articles (Bitcoin, Cryptocurrency, Ethereum, Dogecoin, Blockchain, Coinbase, Binance, Solana, Meme coin, Non-fungible token, Shiba Inu): daily human pageviews / all human views of English Wikipedia, 7-day average, then its percentile within the last two years; the articles are averaged.",
    "Wikipedia is where newcomers look things up, so views of crypto articles rise when people new to crypto arrive. Dividing by total Wikipedia traffic removes the site's own decline in readership (search engines and AI answers now take some of it).")
add("social_c_wikipedia_intl",
    "The same method for the Bitcoin and Cryptocurrency articles in the German, Spanish, French, Italian, Portuguese, Japanese, Korean, Chinese, Russian, Turkish, Dutch and Polish Wikipedias, each divided by its own Wikipedia's total views.",
    "Catches retail waves that start outside the English-speaking world (Korea and Turkey have led several), and makes the score less dependent on one language.")
add("social_c_google_trends",
    "Weekly Google search interest (last 5 years) and monthly (back to 2004) for seven terms: bitcoin, buy crypto, crypto, buy bitcoin, coinbase, bitcoin price and memecoin. Each bucket is stamped at the end of its week or month so the score never uses a number before it existed, each term is scored against its own two years, and the terms are averaged.",
    "'Buy' searches and exchange names track newcomers acting, not just reading. Google rescales every download so its own peak = 100; scoring each term against itself makes that rescaling irrelevant.")
add("social_c_app_store",
    "Daily ranks of crypto-native apps (Coinbase, Crypto.com, Binance, Kraken, Phantom, MetaMask, Trust, Exodus and others; brokers such as Robinhood and Wealthsimple are left out because their ranks mostly track stock trading) in Apple's top-free Finance chart (top 200) and overall chart (top 100), in the US, Canada and the UK. Ranks map onto one ladder: Finance #200 ~ 0, Finance #1 = 0.5, overall #100 = 0.5, overall #1 = 1.0. Per country, the three best-placed apps are averaged; then the countries are averaged.",
    "A crypto app breaking into the overall top 100 is a mania-level reading: Coinbase reached #1 overall near the 2017 and 2021 tops. A reading around 0.3-0.45 is the usual state (a crypto app somewhere in the Finance chart); near 0 means crypto apps have dropped out of the charts. This input works from its first day because ranks have a fixed scale.")
add("social_c_youtube",
    "For each tracked channel, lifetime views are recorded daily; the daily difference is new views per day (exact, unlike subscriber counts, which YouTube rounds). Its 7-day average is scored against the channel's own history (counted twice), together with 30-day subscriber growth (counted once); channels are averaged.",
    "Views are present-tense attention: when price moves, crypto video views jump within days, before subscriber counts catch up. Needs about three months of recordings before it scores.")
add("social_c_hacker_news",
    "Weekly counts of Hacker News stories and comments mentioning bitcoin, ethereum or cryptocurrency (Algolia's HN search, back to 2011), each scored against its own two years and averaged. Weeks are stamped at their end. Counts are absolute: Algolia's total-item count is an estimate (and swollen by spam since 2025), so there is no reliable denominator; the two-year percentile absorbs the site's slow audience shifts.",
    "A tech-savvy but mostly non-crypto audience. When it starts discussing crypto heavily the topic has gone mainstream in tech. Yearly bitcoin mentions peaked in 2013-14, 2017 and 2021 (comments), and have fallen steadily since as the site's attention moved to AI.")
add("social_c_news",
    "GDELT monitors online news worldwide. The daily count of articles mentioning bitcoin, cryptocurrency or crypto, divided by all articles GDELT monitored that day, summed over 7 days and scored against its own history.",
    "Mainstream coverage follows price; extreme coverage share is what pulls in late buyers. Rises in coverage during crashes are why this input is paired with the speculation group.")
add("social_c_x_impressions",
    "Impressions you enter by hand (form below this dashboard), scored against their own history once 8 entries exist; each entry stays valid for 14 days.",
    "Optional. X's API is paid, so this input only exists if you log it.")
add("social_c_fear_greed",
    "alternative.me's Fear & Greed Index / 100. Half weight in its group: half of that index is price momentum and volatility, and its social part overlaps the attention inputs.",
    "Above 0.75 = extreme greed. Extreme greed has often persisted through the strongest parts of bull markets, which is why this index alone is a weak top signal.")
add("social_c_funding",
    "7-day average of the open-interest-weighted perpetual funding rate across ~20 exchanges, scored against its own two years.",
    "High funding = longs are paying a premium to hold leveraged positions. That is retail attention becoming leverage, and it is what liquidation cascades unwind.")
add("social_c_altseason",
    "The Altcoin Season Index / 100: the share of about 50 large altcoins beating BTC over 90 days.",
    "Money rotating down the risk curve into altcoins has been a late-cycle behaviour.")
add("social_c_memecoins",
    "DOGE + SHIB + PEPE trading volume as a share of the top-50 altcoin volume (7-day sums), scored against its own two years.",
    "Memecoin turnover is the purest measure of speculative appetite: it has no fundamentals, so a rising share means traders are chasing lottery tickets.")
add("social_wiki_views",
    "Human pageviews per day (Wikimedia's 'user' agent type, which excludes known bots), 7-day average, for the six tracked English articles with the most views over the last 90 days.",
    "Raw attention in absolute terms. Log scale, because the Bitcoin article gets far more views than the others.")
add("social_wiki_share",
    "Views of the Bitcoin article / all human views of that Wikipedia, x 100, 7-day average; English vs the 12 other tracked languages combined.",
    "The share version of attention, comparable across years. The gap between the lines shows whether interest is led by English-speaking or other markets.")
add("social_trends",
    "Google's weekly search interest per term, 0-100, each term scaled to its own peak in the 5-year window (so terms can't be compared with each other by level, only by shape).",
    "Look for the 'buy' terms rising faster than 'bitcoin price': people acting rather than checking.")
add("social_app_ranks",
    "For each country and chart, the best rank held by any tracked crypto app that day. Axis reversed: rank 1 at the top. An app outside the chart is recorded as just below it (101 overall, 201 Finance).",
    "The overall line spends most of the time pinned at 101 (no crypto app in the top 100); when it lifts off, retail has arrived.")
add("social_coinbase_rank",
    "Coinbase's rank in the Finance and overall charts per country. Axis reversed.",
    "The single most-quoted retail signal in crypto (The Block and Cointelegraph report on it at each peak).")
add("social_youtube",
    "Sum across the tracked channels of the daily change in lifetime views, 7-day average.",
    "How many crypto videos are being watched per day. Needs a few weeks of recordings before the shape means anything.")
add("social_hn",
    "Weekly stories and comments mentioning bitcoin, ethereum or cryptocurrency (an item mentioning two terms counts twice). Log scale.",
    "Comments react faster than stories. Peaks line up with the 2013, 2017 and 2021 tops; the long decline since 2022 is the site's audience moving to AI, which is why the input scores each week against the last two years only.")
add("social_news",
    "Daily articles mentioning bitcoin, cryptocurrency or crypto / all articles GDELT monitored, 7-day sums, x 100.",
    "How much of the world's news is about crypto.")
add("social_memes",
    "DOGE + SHIB + PEPE volume / the top-50 altcoin volume on Yahoo Finance, 7-day sums, x 100.",
    "Spikes coincide with memecoin manias (DOGE in early 2021, PEPE in 2023, the 2024 memecoin run).")

# =========================================================================== Tokenized equities (own)
add("ts_issuer_share",
    "Every tokenized stock CoinGecko lists (its 'Tokenized Stock' category and each issuer's own category) is matched to its issuer: the issuer's category first, otherwise the issuer suffix in CoinGecko's coin id (e.g. '...-ondo-tokenized-stock'). For each token, the five busiest DEX pools on each chain come from GeckoTerminal, and each pool's daily USD volume is credited to the token's issuer. Each bar is the issuer's share of that day's total; a pool is counted once even if both its sides are tokenized stocks.",
    "Who is winning on-chain trading of tokenized stocks. Shifts are fast because volume follows listings and incentives: a new chain or a DEX campaign can move an issuer's share by tens of points in weeks. Read it with the USD chart: a rising share in a shrinking market means less than it looks.")
add("ts_issuer_volume",
    "The same pool volumes as the share chart, in USD, stacked by issuer (the six largest over the last 30 days, the rest grouped as Other).",
    "The size of on-chain tokenized-equity trading and who carries it. On-chain DEX volume only: centralized-exchange trading of the same tokens isn't included.")
add("ts_chain_share",
    "The same pool volumes grouped by the blockchain the pool trades on (GeckoTerminal's network), as a share of the day's total.",
    "Where tokenized stocks actually trade: Solana DEXs, Ethereum, BNB Chain, Robinhood Chain and others.")
add("ts_total",
    "Sum of every tracked pool's daily USD volume, all issuers and chains.",
    "Overall on-chain demand for tokenized equities. Worth watching around US market events (earnings, index changes) and on weekends, when the underlying stock markets are closed and on-chain venues still trade.")


# =========================================================================== Added 27 Oct 2026 (one per odd dashboard)
add("days_since_ath",
    "For each day: the number of days since the daily close last reached a new all-time high (0 on a record day).",
    "Counts up through every correction and resets at each new high. Past bear markets took roughly 1,000+ days to reset; "
    "bull-market pauses usually reset within a few months. A long count with price near the old high is a market still digesting supply.")
add("rsi_14",
    "Daily close changes split into gains and losses, each smoothed with Wilder's 14-day average; RSI = 100 - 100 / (1 + average gain / average loss).",
    "Above 70 = overbought (strong recent upside), below 30 = oversold. In strong trends RSI can stay overbought or oversold for weeks, "
    "so the more useful signal is divergence: price making a new high while RSI makes a lower high.")
add("fut_liq_30d",
    "Daily long and short liquidations across the covered exchanges (Coinalyze), each summed over a rolling 30 days.",
    "Shows which side has been forced out over the past month. A spike in long liquidations marks a flush of leveraged buyers, "
    "often near local lows; heavy short liquidations mark squeezes. When both are low, leverage has been building quietly.")
add("fut_dated_oi",
    "Open interest of every dated (expiring) BTC future in the recorded futures curve (Deribit, Binance, OKX), using the last "
    "snapshot of each day, summed per venue.",
    "Dated futures are where basis (cash-and-carry) trades live. Rising dated OI alongside a positive basis means more carry trades: "
    "funds buying spot or ETFs and selling futures, which is market-neutral rather than directional demand.")
add("eth_btc",
    "ETH's daily close divided by BTC's daily close (Coin Metrics reference rates).",
    "Rising = ETH outperforming BTC, typically when risk appetite spreads beyond bitcoin. A falling ratio means capital is concentrating in BTC, "
    "common in early bull markets and in risk-off periods.")
add("opt_iv_rv",
    "Implied: Deribit 1-month constant-maturity ATM IV (DVOL before our own record starts). Realized: standard deviation of daily log returns "
    "over 30 days x sqrt(365). Premium = implied - realized.",
    "Options usually price more volatility than is realized (a positive premium) because sellers want to be paid for risk. A large premium "
    "means options are expensive relative to recent moves; a negative premium means realized moves are outrunning what options priced, "
    "often during sharp breakouts or sell-offs.")
add("opt_skew_term",
    "For every listed Deribit expiry in the latest snapshot: (25-delta put IV - 25-delta call IV) / ATM IV, plotted by days to expiry.",
    "Positive values mean puts are dearer than calls (demand for downside protection). Comparing short and long expiries shows whether "
    "fear is about the next few days (short end high) or the longer run.")
add("ibit_volume",
    "Sum of each IBIT option contract's daily volume from Yahoo's option chain, recorded after each US close.",
    "Shows how actively US investors trade bitcoin exposure through ETF options. Spikes often line up with large BTC moves or ETF flow days.")
add("etf_cum_flows",
    "Running total of the daily net flows of all US spot bitcoin ETFs (Farside's USD table) since launch.",
    "The slope matters more than the level: a steepening line means accelerating inflows; a flattening or falling line means demand from "
    "ETF buyers has stalled or reversed.")
add("macro_real_rate",
    "Fed funds target (upper bound, FRED DFEDTARU) minus US core CPI inflation (YoY of CPILFESL), monthly.",
    "Positive real rates mean money in cash earns more than inflation: tight policy, historically a headwind for risk assets. Falling or "
    "negative real rates loosen financial conditions and have tended to coincide with strong bitcoin periods.")
add("tradfi_corr",
    "90-day rolling correlation between BTC's daily log returns and those of the S&P 500, gold and the US dollar index (Yahoo closes).",
    "+1 = moving together, -1 = moving opposite, 0 = unrelated. High correlation with stocks means BTC is trading as a risk asset; a negative "
    "correlation with the dollar is the usual pattern when dollar strength tightens liquidity.")
add("ex_netflow_usd",
    "Coin Metrics' exchange inflow minus outflow in USD (FlowInExUSD - FlowOutExUSD), averaged over 7 days.",
    "Positive = more value moving onto exchanges (potential selling); negative = coins leaving for custody. The USD view weighs each day by "
    "price, so recent flows count more than the same BTC amount in older, cheaper periods.")
add("por_change",
    "For each exchange that publishes its wallets (DefiLlama CEX transparency): BTC held today minus 30 days earlier. The five largest "
    "exchanges are shown separately, the rest summed.",
    "Shows which exchanges are gaining or losing BTC. Broad, sustained declines usually mean coins moving to self-custody or ETFs; a "
    "single exchange's sharp drop can also be an internal wallet change, so check it against its flows.")
add("whale_supply",
    "BTC held by addresses with 1k-10k BTC and with more than 10k BTC (BGeometrics balance bands), each divided by circulating supply.",
    "Large addresses include exchanges, ETF custodians and long-term whales, so changes reflect custody moves as well as buying. A rising "
    "share during weakness has historically been read as accumulation.")
add("unrealized_pl",
    "Market cap minus realized cap (Coin Metrics): the total paper profit (positive) or loss (negative) of all holders, valued at the price "
    "each coin last moved.",
    "The USD version of NUPL. Very large paper profits mean many holders have a reason to sell; paper losses across the market have marked "
    "bear-market lows.")
add("risk_signals",
    "Each day, count how many of six classic top signals are on: MVRV above 3, NUPL above 0.6, percent of supply in profit above 95%, "
    "Mayer Multiple above 2.4, perpetual funding (7-day average) above 0.03% per 8 hours, Fear & Greed above 80. A signal counts once data exists for it.",
    "0-1 = no sign of overheating. 3 or more at once has historically appeared only near cycle tops or in the most euphoric legs of bull "
    "markets. It is a checklist, not a timing tool: signals can stay on for weeks.")
add("alt_btc_ratio",
    "Altcoin market cap (total crypto market cap minus BTC's) divided by BTC's market cap.",
    "Rising = money rotating into altcoins relative to bitcoin (altcoin season conditions); falling = bitcoin dominance strengthening.")
