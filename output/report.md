# S&P 500 short-put ladder: systematic 1-5 day put selling

Backtest 2005-01-04 to 2026-09-22 (5,463 trading days, 12,820 puts sold per route).
**Routes: IBKR private - XSP options (private) and GS institutional - SPY options (institutional).**
**Returns are option P&L only: no money-market or collateral interest is included.** Add your cash yield on top if the account holds T-bills.
Data: VolVue API (SPX implied vols), CBOE VIX, Yahoo (SPX OHLC), Cboe delayed option chains (spread and skew calibration).

## Rules

* Every trading day, sell SPX puts on the **three nearest Monday / Wednesday / Friday expiries** (each 1-5 business days out).
* **Adjusted VIX** = VIX(prev close) x sqrt(bdays / 252): VIX rescaled from 30 days to the option's own tenor.
* **Adj. vol %** = 2.5 x Adjusted VIX; **target moneyness** = 100% - Adj. vol %. The strike is rounded down to the instrument's grid.
  When VIX rises, strikes automatically move further out-of-the-money (the volatility-regime adjustment).
* Executed at the **full-day TWAP**, approximated as Black-Scholes at spot (O+H+L+C)/4 and the average of the previous and current day's
  VolVue 10-day ATM put IV, with a put skew of IV x (1 + 0.15 x SDs OTM), cap 2.0x, calibrated on the SPX option chains.
  Costs per option: share of the quoted half-spread paid + per-contract fees (venue table below).
* Held to expiry, cash-settled at the close. Each (day, expiry) tranche sells notional = NAV x 1.0 x size multiplier / 9,
  so at full size about 100% of NAV is outstanding on average.
* **Low-VIX size cut:** the size multiplier is 0.25 when VIX(prev close) <= 13, 1.0 when VIX >= 17, linear in between.
* **Term-structure filter:** no new sales on a day when VIX/VIX3M at the previous close is above 0.95 (the curve is flat or
  inverted, the usual shape before and during sell-offs).
* **Stop-loss:** at each close, buy back any short put worth 3x or more its premium, at model value plus 2x the normal execution cost.
  (A stop multiple of 0 means off.) The study in [hedges.md](hedges.md) compares these rules with put-spread wings, tail puts, trend and shock filters.

## Headline risk statistics

Strategy Sharpe ratios are computed on option P&L, which is already an excess return. The index Sharpe is measured against T-bills.

| Metric | IBKR XSP | GS SPY | S&P 500 (price) |
|---|---|---|---|
| Start | 2005-01-04 | 2005-01-04 | 2005-01-04 |
| End | 2026-09-22 | 2026-09-22 | 2026-09-22 |
| CAGR | 0.44% | 0.50% | 8.99% |
| Ann. volatility | 0.27% | 0.26% | 18.98% |
| Sharpe | 1.66 | 1.89 | 0.46 |
| Sortino | 1.75 | 1.99 | 0.64 |
| Max drawdown | -0.91% | -0.84% | -56.78% |
| Max DD peak | 2018-02-01 | 2018-02-01 | 2007-10-09 |
| Max DD trough | 2020-06-11 | 2020-06-11 | 2009-03-09 |
| Max DD recovered | 2021-03-05 | 2020-12-31 | 2013-03-28 |
| Longest underwater (days) | 1127 | 1063 | 1996 |
| Calmar | 0.49 | 0.60 | 0.16 |
| Daily skew | -21.98 | -21.82 | -0.21 |
| Daily excess kurtosis | 628.29 | 618.68 | 13.10 |
| Daily VaR 95% | -0.00% | -0.00% | -1.76% |
| Daily CVaR 95% | -0.02% | -0.02% | -2.93% |
| Daily VaR 99% | -0.02% | -0.02% | -3.45% |
| Daily CVaR 99% | -0.09% | -0.09% | -5.10% |
| Worst day | -0.61% | -0.61% | -11.98% |
| Worst day date | 2018-02-05 | 2018-02-05 | 2020-03-16 |
| Worst week | -0.61% | -0.61% | -18.20% |
| Worst month | -0.62% | -0.62% | -16.94% |
| Best month | 0.24% | 0.24% | 12.68% |
| % positive days | 76.57% | 78.13% | 54.55% |
| % positive months | 89.27% | 91.19% | 64.37% |

![Equity curve](equity_curve.png)
![Drawdown](drawdown.png)

## Risk management: term-structure filter and stop-loss

| IBKR XSP | Managed (default) | Unmanaged | Managed, since 2022 | Unmanaged, since 2022 |
|---|---|---|---|---|
| CAGR | 0.44% | 0.66% | 0.48% | 0.29% |
| Ann. volatility | 0.27% | 1.40% | 0.32% | 1.86% |
| Sharpe | 1.66 | 0.48 | 1.50 | 0.16 |
| Max drawdown | -0.91% | -3.74% | -0.59% | -3.74% |
| Worst day | -0.61% | -3.51% | -0.47% | -3.51% |
| Worst month | -0.62% | -2.74% | -0.49% | -2.74% |

April 2025 tariff shock (25 Mar - 8 Apr 2025): managed -0.33%, unmanaged -3.69%.
Stops were triggered 16 times a year on average, and new sales were paused on 21% of days.

## Low-VIX size cut

| IBKR XSP | With cut (13-17) | No cut | With cut, since 2022 | No cut, since 2022 |
|---|---|---|---|---|
| CAGR | 0.44% | 0.50% | 0.48% | 0.58% |
| Ann. volatility | 0.27% | 0.45% | 0.32% | 0.36% |
| Sharpe | 1.66 | 1.11 | 1.50 | 1.61 |
| Max drawdown | -0.91% | -1.94% | -0.59% | -0.59% |
| Worst month | -0.62% | -1.46% | -0.49% | -0.49% |

| VIX regime | % of days | Avg size multiplier | Margin used (% NAV) | Return (ann., arith.) |
|---|---|---|---|---|
| Low (<14) | 28.96% | 0.28 | 3.1% | 0.12% |
| Normal (14-20) | 38.61% | 0.71 | 6.8% | 0.37% |
| Elevated (20-28) | 22.04% | 0.65 | 6.0% | 0.83% |
| Crisis (>28) | 10.38% | 0.25 | 2.3% | 0.77% |

![Size cut and margin](size_cut_margin.png)

## Trade statistics

| Trade statistic | IBKR XSP | GS SPY |
|---|---|---|
| Puts sold | 12820 | 12820 |
| Puts settled | 12810 | 12810 |
| Avg bdays to expiry | 2.97 | 2.97 |
| Avg target moneyness | 95.61% | 95.61% |
| Min target moneyness | 82.04% | 82.04% |
| Avg Adj. vol % | 4.39% | 4.39% |
| Avg premium (bps of spot) | 1.39 | 1.51 |
| Median premium (index pts) | 0.26 | 0.28 |
| % expiring ITM or stopped | 3.00% | 2.59% |
| % stopped out (bought back) | 2.76% | 2.36% |
| % losing trades (payoff > premium) | 3.00% | 2.59% |
| Avg loss / avg win (premium units) | -15.64 | -16.24 |
| Worst trade (x premium) | -177.55 | -160.07 |
| Worst breach (spot vs strike) | -18.42% | -18.42% |

## Market sensitivity and current exposure

| Market sensitivity | IBKR XSP | GS SPY |
|---|---|---|
| Beta to index | 0.00 | 0.00 |
| Correlation to index | 0.27 | 0.27 |
| Down-market beta | 0.01 | 0.01 |
| Up-market beta | 0.00 | 0.00 |
| Avg return on the index's worst-2% days | -0.04% | -0.04% |
| Avg index return on those days | -4.08% | -4.08% |

| Exposure as of 2026-09-22 (IBKR XSP) | Value |
|---|---|
| Open put tranches | 10 |
| Gross put notional / NAV | 73.9% |
| Margin used / NAV (CBOE index rule) | 7.9% |
| Net delta (equity-equivalent % NAV) | 0.47% |
| Gamma (Δdelta per 1% move, % NAV) | -0.70% |
| Vega (NAV per +1 vol pt) | -0.001% |
| Avg gross notional over history | 47.3% |
| Max net delta over history | 7.1% |

![Greeks](greeks.png)

## Risk by VIX regime (IBKR XSP, regime at trade time)

| VIX regime | % of days | Ann. return (arith.) | Ann. vol | Sharpe (raw) | Worst day | Avg moneyness | Avg premium (bps) | % ITM |
|---|---|---|---|---|---|---|---|---|
| Low (<14) | 28.96% | 0.12% | 0.08% | 1.48 | -0.18% | 96.77% | 0.90 | 3.42% |
| Normal (14-20) | 38.61% | 0.37% | 0.28% | 1.32 | -0.61% | 95.66% | 1.37 | 3.07% |
| Elevated (20-28) | 22.04% | 0.83% | 0.37% | 2.25 | -0.47% | 93.94% | 2.14 | 2.39% |
| Crisis (>28) | 10.38% | 0.77% | 0.27% | 2.88 | -0.35% | 91.17% | 3.09 | 0.71% |

![VIX and moneyness](vol_moneyness.png)

## Stress episodes

| Episode | IBKR XSP | GS SPY | S&P 500 | Worst day (IBKR XSP) |
|---|---|---|---|---|
| GFC (Sep-Nov 2008) | 0.05% | 0.06% | -31.09% | -0.05% |
| Flash crash (May 2010) | -0.24% | -0.23% | -9.73% | -0.27% |
| US downgrade (Aug 2011) | -0.02% | -0.02% | -9.38% | -0.02% |
| China deval. (Aug 2015) | -0.30% | -0.30% | -5.71% | -0.25% |
| Volmageddon (Feb 2018) | -0.62% | -0.61% | -8.82% | -0.61% |
| Q4 2018 selloff | -0.12% | -0.11% | -19.32% | -0.07% |
| COVID crash (Feb-Mar 2020) | -0.27% | -0.31% | -33.61% | -0.27% |
| 2022 bear market | 0.06% | 0.13% | -24.82% | -0.47% |
| Yen carry unwind (Aug 2024) | 0.00% | 0.00% | -6.77% | -0.01% |
| DeepSeek (Jan 2025) | 0.02% | 0.02% | -1.28% | -0.00% |
| Tariff shock (Apr 2025) | -0.33% | -0.32% | -13.61% | -0.35% |

## Calendar-year returns

| Year | IBKR XSP | GS SPY | S&P 500 |
|---|---|---|---|
| 2005 | 0.25% | 0.29% | 3.84% |
| 2006 | 0.18% | 0.22% | 13.62% |
| 2007 | 0.05% | 0.10% | 3.53% |
| 2008 | 0.90% | 0.96% | -38.49% |
| 2009 | 1.32% | 1.39% | 23.45% |
| 2010 | 0.72% | 0.82% | 12.78% |
| 2011 | 0.78% | 0.85% | -0.00% |
| 2012 | 1.18% | 1.26% | 13.41% |
| 2013 | 0.30% | 0.34% | 29.60% |
| 2014 | 0.23% | 0.27% | 11.39% |
| 2015 | 0.32% | 0.37% | -0.73% |
| 2016 | 0.39% | 0.45% | 9.54% |
| 2017 | 0.10% | 0.13% | 19.42% |
| 2018 | -0.61% | -0.57% | -6.24% |
| 2019 | 0.22% | 0.26% | 28.88% |
| 2020 | 0.27% | 0.33% | 16.26% |
| 2021 | 0.70% | 0.80% | 26.89% |
| 2022 | 0.48% | 0.56% | -19.44% |
| 2023 | 0.88% | 0.94% | 24.23% |
| 2024 | 0.13% | 0.18% | 23.31% |
| 2025 | 0.24% | 0.31% | 16.39% |
| 2026 | 0.54% | 0.60% | 13.40% |

![Rolling](rolling.png)
![Distribution](return_distribution.png)

## Execution venues

**Market check at tomorrow's strikes** (Cboe end-of-day quotes, 2026-09-22 16:14:59; mids converted to SPX points):

| Expiry | Instrument | Strike | Bid / ask | Mid (index pts) | Model premium (index pts) | Half-spread % mid | Open interest | Volume |
|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | SPXW | 7515 | 0.85 / 0.95 | 0.90 | 0.81 | 6% | 638 | 24 |
| 2026-09-25 | SPY | 748 | 0.08 / 0.09 | 0.85 | 0.81 | 6% | 2047 | 1337 |
| 2026-09-25 | XSP | 751 | 0.07 / 0.10 | 0.85 | 0.81 | 18% | 263 | 15 |
| 2026-09-28 | SPXW | 7460 | 1.15 / 1.30 | 1.23 | 0.88 | 6% | 134 | 152 |
| 2026-09-28 | SPY | 743 | 0.12 / 0.13 | 1.25 | 0.88 | 4% | 839 | 87 |
| 2026-09-28 | XSP | 746 | 0.11 / 0.14 | 1.25 | 0.88 | 12% | 60 | 3 |
| 2026-09-30 | SPXW | 7375 | 1.60 / 1.80 | 1.70 | 1.05 | 6% | 460 | 100 |
| 2026-09-30 | SPY | 734 | 0.17 / 0.18 | 1.76 | 1.05 | 3% | 1812 | 128 |
| 2026-09-30 | XSP | 737 | 0.16 / 0.19 | 1.75 | 1.05 | 9% | 36 | 0 |

End-of-day quotes are wider than during the session, so these spreads are an upper bound for a TWAP. The quotes also have one more session to run
than a trade placed tomorrow midday, so market mids for the longer expiries tend to exceed the model premium.

**Venue cost models** (spreads fitted on the 2026-09-22 Cboe chains around the strategy's strikes):

| Setup | Instrument | Settlement | Quoted half-spread (bps of spot) | Share of half-spread paid | Fees per contract |
|---|---|---|---|---|---|
| IBKR private - XSP options | XSP | Cash-settled, European, PM | 0.167 + 0.013 x premium | 50% | $0.77 |
| GS institutional - SPY options | SPY | Physical (shares), American | 0.065 + 0.000 x premium | 25% | $0.47 |
| IBKR private - SPY options (comparison) | SPY | Physical (shares), American | 0.065 + 0.000 x premium | 50% | $0.70 |
| GS institutional - SPXW options (comparison) | SPXW | Cash-settled, European, PM | 0.056 + 0.010 x premium | 25% | $0.82 |

| Setup | CAGR | Sharpe | Max DD | Cost % of premium | CAGR since 2022 | Sharpe since 2022 | Max DD since 2022 | Cost % of premium since 2022 |
|---|---|---|---|---|---|---|---|---|
| IBKR private - XSP options | 0.44% | 1.66 | -0.91% | 12.5% | 0.48% | 1.50 | -0.59% | 12.3% |
| GS institutional - SPY options | 0.50% | 1.89 | -0.84% | 5.0% | 0.55% | 1.72 | -0.58% | 4.9% |
| IBKR private - SPY options (comparison) | 0.48% | 1.80 | -0.87% | 8.0% | 0.52% | 1.64 | -0.58% | 7.8% |
| GS institutional - SPXW options (comparison) | 0.52% | 1.97 | -0.82% | 1.9% | 0.58% | 1.81 | -0.58% | 1.8% |

The full-history columns apply today's cost structure to the whole period. The columns from 2022 onward are the better guide to live trading.

**Minimum account size** (tranche = 11.1% of NAV at full size; contracts are whole numbers):

| Instrument | Contract notional | Min NAV, 1 contract per tranche at full size | Min NAV, 1 contract at the 0.25x floor |
|---|---|---|---|
| XSP | $77,629 | $698,663 | $2,794,653 |
| SPY | $77,321 | $695,892 | $2,783,568 |
| SPXW | $776,297 | $6,986,669 | $27,946,676 |

## S&P 500 vs Nasdaq-100 (same rules, realistic venues, no interest)

| Setup | Strike driver | CAGR | Vol | Sharpe | Max DD | Worst day | CAGR since 2022 | Sharpe since 2022 |
|---|---|---|---|---|---|---|---|---|
| S&P 500 via IBKR private - XSP options | VIX | 0.44% | 0.27% | 1.66 | -0.91% | -0.61% | 0.48% | 1.50 |
| S&P 500 via GS institutional - SPY options | VIX | 0.50% | 0.26% | 1.89 | -0.84% | -0.61% | 0.55% | 1.72 |
| Nasdaq-100 via IBKR private - QQQ options | VXN | 0.88% | 1.28% | 0.69 | -3.25% | -3.06% | 1.24% | 0.74 |
| Nasdaq-100 via GS institutional - NDXP options | VXN | 0.83% | 1.28% | 0.65 | -3.25% | -3.06% | 1.18% | 0.70 |

## Sensitivity to design choices and model assumptions (IBKR XSP)

| Variant | CAGR | Vol | Sharpe | Max DD | Worst day | Avg moneyness | % ITM or stopped |
|---|---|---|---|---|---|---|---|
| Base (managed default) | 0.44% | 0.27% | 1.66 | -0.91% | -0.61% | 95.6% | 3.00% |
| Unmanaged (no term-structure filter, no stop-loss) | 0.66% | 1.40% | 0.48 | -3.74% | -3.51% | 95.0% | 0.67% |
| No term-structure filter | 0.54% | 0.76% | 0.71 | -2.84% | -2.28% | 95.0% | 3.32% |
| No stop-loss | 0.46% | 0.51% | 0.92 | -1.95% | -1.14% | 95.6% | 0.49% |
| Stop-loss at 5x premium | 0.45% | 0.32% | 1.38 | -1.00% | -0.88% | 95.6% | 1.67% |
| Term-structure threshold 1.00 | 0.60% | 0.33% | 1.81 | -0.94% | -0.74% | 95.4% | 3.04% |
| No low-vol size cut | 0.50% | 0.45% | 1.11 | -1.94% | -1.43% | 95.6% | 3.00% |
| Size cut ramp 12-16 | 0.46% | 0.31% | 1.47 | -1.22% | -0.88% | 95.6% | 3.00% |
| Size cut ramp 15-20 | 0.38% | 0.21% | 1.75 | -0.63% | -0.47% | 95.6% | 3.00% |
| Size cut floor 0 (stop at low vol) | 0.42% | 0.23% | 1.85 | -0.59% | -0.47% | 95.2% | 2.77% |
| Multiplier 2.0x | 0.49% | 0.42% | 1.16 | -1.49% | -1.01% | 96.5% | 5.00% |
| Multiplier 3.0x | 0.35% | 0.19% | 1.77 | -0.61% | -0.61% | 94.7% | 2.31% |
| No skew (flat ATM vol) | 0.44% | 0.27% | 1.66 | -0.91% | -0.61% | 95.6% | 3.00% |
| Flatter skew 0.10 | 0.44% | 0.27% | 1.66 | -0.91% | -0.61% | 95.6% | 3.00% |
| Steeper skew 0.20 | 0.44% | 0.27% | 1.66 | -0.91% | -0.61% | 95.6% | 3.00% |
| Double t-costs | 0.35% | 0.25% | 1.39 | -0.94% | -0.61% | 95.6% | 4.74% |
| Sell on the bid (pay full half-spread) | 0.40% | 0.25% | 1.58 | -0.92% | -0.61% | 95.6% | 3.55% |
| Leverage 2x | 0.88% | 0.53% | 1.66 | -1.82% | -1.22% | 95.6% | 3.00% |
| Leverage 3x | 1.33% | 0.80% | 1.66 | -2.72% | -1.83% | 95.6% | 3.00% |

## Next trading day: orders to place

Based on the 2026-09-22 close (SPX 7,762.97, VIX 14.21, SPX 10d ATM put IV 11.16).
Strikes are computed off the last close; recompute against the live TWAP level when executing. Premium and cost are per option, in instrument points.

**IBKR private - XSP options, NAV $1,000,000:**

| expiry | expiry_day | bdays | adj_vix_pct | adj_vol_pct | target_moneyness_pct | strike | est_premium_pts | regime_mult | size_mult | notional_pct_nav | instrument | instrument_strike | est_premium_instr | est_cost_instr | contracts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | Friday | 2 | 1.266 | 3.165 | 96.84 | 7515.0 | 0.81 | 1.0 | 0.477 | 5.3 | XSP | 751.0 | 0.081 | 0.015 | 1 |
| 2026-09-28 | Monday | 3 | 1.55 | 3.876 | 96.12 | 7460.0 | 0.88 | 1.0 | 0.477 | 5.3 | XSP | 746.0 | 0.088 | 0.015 | 1 |
| 2026-09-30 | Wednesday | 5 | 2.002 | 5.004 | 95.0 | 7370.0 | 1.05 | 1.0 | 0.477 | 5.3 | XSP | 737.0 | 0.105 | 0.015 | 1 |

**GS institutional - SPY options, NAV $100,000,000:**

| expiry | expiry_day | bdays | adj_vix_pct | adj_vol_pct | target_moneyness_pct | strike | est_premium_pts | regime_mult | size_mult | notional_pct_nav | instrument | instrument_strike | est_premium_instr | est_cost_instr | contracts |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | Friday | 2 | 1.266 | 3.165 | 96.84 | 7515.0 | 0.81 | 1.0 | 0.477 | 5.3 | SPY | 748.0 | 0.08 | 0.006 | 69 |
| 2026-09-28 | Monday | 3 | 1.55 | 3.876 | 96.12 | 7460.0 | 0.88 | 1.0 | 0.477 | 5.3 | SPY | 743.0 | 0.088 | 0.006 | 69 |
| 2026-09-30 | Wednesday | 5 | 2.002 | 5.004 | 95.0 | 7370.0 | 1.05 | 1.0 | 0.477 | 5.3 | SPY | 734.0 | 0.104 | 0.006 | 69 |

## Caveats

* **Option prices are modelled, not traded quotes.** VolVue provides constant-maturity ATM IV but no strike-level quotes, so OTM premia rely on the
  parametric skew, calibrated on one day's chains. The sensitivity table shows how results move with the skew assumption.
* Monday/Wednesday expiries were only listed from the mid-2010s (SPXW) and later for XSP and SPY. Earlier years assume those expiries existed.
* VIX gaps in the CBOE history (0 days in this sample) are filled with VolVue 30d mean IV x 1.184.
  31 bad VolVue ATM IV prints were replaced by VIX x rolling median ratio.
* The model settles every option in cash at the close. SPY options are American-style and physically settled. Assignment delivers shares, which then
  carry overnight gap risk until sold. Close positions that are near the money before expiry-day close.
* TWAP uses (O+H+L+C)/4 and interpolated IV. Intraday path, early-close days and settlement-price nuances are ignored.
* The size-cut, term-structure (0.95) and stop-loss (3x) thresholds were chosen after looking at this history, so they are in-sample.
  hedges.md shows the effect holds in both halves of the sample (2005-15 and 2016-26).
* The put skew is calibrated on one day's SPXW chain (2026-09-22), extended further out so hedge legs are priced realistically. That
  calibration also lowered the modelled premium at the short strikes versus the earlier linear skew.
* Stop-losses fill at the close at model value plus a stressed cost; a real stop in a fast market can fill worse, and overnight gaps are not protected.
