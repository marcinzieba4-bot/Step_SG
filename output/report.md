# NDX short-put ladder: systematic 1-5 day put selling

Backtest 2005-01-04 to 2026-09-22 (5,463 trading days, 15,818 puts sold).
Data: VolVue API (NDX implied vols), CBOE VXN, Yahoo (NDX OHLC, 13w T-bill).

## Rules

* Every trading day, sell NDX puts on the **three nearest Monday / Wednesday / Friday expiries** (each 1-5 business days out).
* **Adjusted VXN** = VXN(prev close) x sqrt(bdays / 252): VXN rescaled from 30-day to the option's own tenor.
* **Adj. vol %** = 2.5 x Adjusted VXN; **target moneyness** = 100% - Adj. vol %; strike rounded down to a 10-pt grid.
  When VXN rises, strikes automatically move further out-of-the-money (the volatility-regime adjustment).
* Executed at the **full-day TWAP**, approximated as Black-Scholes at spot (O+H+L+C)/4 and the average of the previous and current day's
  VolVue 10-day ATM put IV, with a parametric put skew (IV x (1 + 0.12 x SDs OTM), cap 2.0x). Cost: 5% of premium, min 0.1 pts.
* Held to expiry, cash-settled at the close. Each (day, expiry) tranche sells notional = NAV x 1.0 / 9,
  so about 100% of NAV is outstanding on average; collateral earns the T-bill rate.

## Headline risk statistics

| Metric | Strategy (total return) | Option overlay (excess) | NDX (price) |
|---|---|---|---|
| Start | 2005-01-04 | 2005-01-04 | 2005-01-04 |
| End | 2026-09-22 | 2026-09-22 | 2026-09-22 |
| CAGR | 2.58% | 0.79% | 14.59% |
| Ann. volatility | 1.52% | 1.51% | 22.04% |
| Sharpe (vs T-bill) | 0.53 | 0.53 | 0.65 |
| Sortino | 0.60 | 0.59 | 0.92 |
| Max drawdown | -5.51% | -5.51% | -53.71% |
| Max DD peak | 2015-08-18 | 2015-08-17 | 2007-10-31 |
| Max DD trough | 2015-08-24 | 2015-08-24 | 2008-11-20 |
| Max DD recovered | 2018-05-01 | 2019-05-14 | 2011-01-03 |
| Longest underwater (days) | 986 | 1365 | 1157 |
| Calmar | 0.47 | 0.14 | 0.27 |
| Daily skew | -16.44 | -16.61 | -0.06 |
| Daily excess kurtosis | 499.72 | 507.44 | 8.04 |
| Daily VaR 95% | -0.01% | -0.02% | -2.20% |
| Daily CVaR 95% | -0.13% | -0.13% | -3.29% |
| Daily VaR 99% | -0.10% | -0.10% | -3.99% |
| Daily CVaR 99% | -0.51% | -0.51% | -5.19% |
| Worst day | -3.05% | -3.06% | -12.19% |
| Worst day date | 2025-04-04 | 2025-04-04 | 2020-03-16 |
| Worst week | -3.01% | -3.09% | -13.67% |
| Worst month | -3.86% | -3.87% | -16.30% |
| Best month | 1.14% | 1.12% | 15.64% |
| % positive days | 86.84% | 77.48% | 55.35% |
| % positive months | 96.17% | 94.64% | 61.30% |

![Equity curve](equity_curve.png)
![Drawdown](drawdown.png)

## Trade statistics

| Trade statistic | Value |
|---|---|
| Puts sold | 15818 |
| Puts settled | 15808 |
| Avg bdays to expiry | 2.97 |
| Avg target moneyness | 94.11% |
| Min target moneyness | 70.34% |
| Avg Adj. vol % | 5.89% |
| Avg premium (bps of spot) | 1.86 |
| Median premium (index pts) | 0.55 |
| % expiring ITM (breached) | 0.66% |
| % losing trades (payoff > premium) | 0.65% |
| Avg loss / avg win (premium units) | -62.80 |
| Worst trade (x premium) | -805.95 |
| Worst breach (spot vs strike) | -29.67% |

## Market sensitivity and current exposure

| Market sensitivity | Value |
|---|---|
| Beta to NDX | 0.02 |
| Correlation to NDX | 0.36 |
| Down-market beta | 0.05 |
| Up-market beta | 0.02 |
| Avg return on NDX worst-2% days | -0.23% |
| Avg NDX on those days | -4.34% |

| Exposure as of 2026-09-22 | Value |
|---|---|
| Open put tranches | 10 |
| Gross put notional / NAV | 104.4% |
| Net delta (equity-equivalent % NAV) | 0.33% |
| Gamma (Δdelta per 1% NDX, % NAV) | -0.39% |
| Vega (NAV per +1 vol pt) | -0.001% |
| Avg gross notional over history | 89.1% |
| Max net delta over history | 52.4% |

![Greeks](greeks.png)

## Risk by VXN regime (regime at trade time)

| Regime | % of days | Ann. return (arith.) | Ann. vol | Sharpe (raw) | Worst day | Avg moneyness | Avg premium (bps) | % ITM |
|---|---|---|---|---|---|---|---|---|
| Low (<18) | 35.49% | 1.93% | 0.32% | 6.03 | -0.51% | 95.88% | 0.91 | 0.69% |
| Normal (18-25) | 36.81% | 2.41% | 1.21% | 1.99 | -2.59% | 94.51% | 1.67 | 0.82% |
| Elevated (25-35) | 20.87% | 1.90% | 2.42% | 0.78 | -3.05% | 92.42% | 2.88 | 0.48% |
| Crisis (>35) | 6.81% | 8.76% | 2.71% | 3.23 | -1.16% | 88.35% | 4.56 | 0.27% |

![VXN and moneyness](vxn_moneyness.png)

## Stress episodes

| Episode | Strategy | Strategy max DD | Worst day | NDX |
|---|---|---|---|---|
| GFC (Sep-Nov 2008) | 0.17% | -1.16% | -1.16% | -38.08% |
| Flash crash (May 2010) | -0.26% | -0.72% | -0.48% | -9.33% |
| US downgrade (Aug 2011) | -1.01% | -2.19% | -2.12% | -7.76% |
| China deval. (Aug 2015) | -3.90% | -5.51% | -2.79% | -5.65% |
| Volmageddon (Feb 2018) | -0.37% | -1.14% | -1.13% | -8.69% |
| Q4 2018 selloff | 0.93% | -0.61% | -0.61% | -22.66% |
| COVID crash (Feb-Mar 2020) | -0.61% | -2.09% | -0.97% | -27.24% |
| 2022 bear market | 3.27% | -0.86% | -0.80% | -34.49% |
| Yen carry unwind (Aug 2024) | 0.32% | -0.44% | -0.36% | -12.64% |
| DeepSeek (Jan 2025) | 0.12% | -0.06% | -0.06% | -1.93% |
| Tariff shock (Apr 2025) | -2.65% | -3.23% | -3.05% | -15.31% |

## Calendar-year returns

| Year | Strategy | Overlay (excess) | NDX |
|---|---|---|---|
| 2005 | 3.56% | 0.40% | 2.60% |
| 2006 | 5.43% | 0.59% | 6.79% |
| 2007 | 4.25% | -0.20% | 18.67% |
| 2008 | 2.43% | 1.05% | -41.89% |
| 2009 | 1.52% | 1.38% | 53.54% |
| 2010 | 0.61% | 0.48% | 19.22% |
| 2011 | 0.09% | 0.04% | 2.70% |
| 2012 | 1.04% | 0.96% | 16.82% |
| 2013 | 0.81% | 0.76% | 34.99% |
| 2014 | 0.80% | 0.78% | 17.94% |
| 2015 | -2.75% | -2.79% | 8.43% |
| 2016 | 1.51% | 1.21% | 5.89% |
| 2017 | 1.42% | 0.50% | 31.52% |
| 2018 | 3.09% | 1.13% | -1.04% |
| 2019 | 3.12% | 1.04% | 37.96% |
| 2020 | 1.92% | 1.57% | 47.58% |
| 2021 | 1.24% | 1.21% | 26.63% |
| 2022 | 5.02% | 2.99% | -32.97% |
| 2023 | 7.07% | 1.83% | 53.81% |
| 2024 | 6.53% | 1.33% | 24.88% |
| 2025 | 3.89% | -0.24% | 20.17% |
| 2026 | 3.94% | 1.23% | 21.68% |

![Rolling](rolling.png)
![Distribution](return_distribution.png)

## Sensitivity to design choices and model assumptions

| Variant | CAGR | Overlay CAGR | Vol | Sharpe | Max DD | Worst day | Avg moneyness | % ITM |
|---|---|---|---|---|---|---|---|---|
| Base: 2.5x, skew 0.12, 1x | 2.58% | 0.79% | 1.52% | 0.53 | -5.51% | -3.05% | 94.1% | 0.66% |
| Multiplier 2.0x | 3.72% | 1.90% | 2.40% | 0.80 | -6.82% | -4.26% | 95.3% | 1.56% |
| Multiplier 3.0x | 2.06% | 0.27% | 0.99% | 0.29 | -4.30% | -2.45% | 92.7% | 0.34% |
| No skew (flat ATM vol) | 1.43% | -0.34% | 1.25% | -0.26 | -4.94% | -3.11% | 93.7% | 1.10% |
| Steeper skew 0.20 | 5.07% | 3.23% | 1.58% | 2.02 | -5.48% | -2.99% | 94.1% | 0.65% |
| Double t-costs | 2.35% | 0.56% | 1.52% | 0.38 | -5.52% | -3.05% | 94.0% | 0.72% |
| Leverage 2x | 3.38% | 1.57% | 3.06% | 0.53 | -11.02% | -6.12% | 94.1% | 0.66% |
| Leverage 3x | 4.17% | 2.34% | 4.63% | 0.53 | -16.52% | -9.20% | 94.1% | 0.66% |

## Next trading day: trades to place

Based on the 2026-09-22 close (NDX 30,723.71, VXN 20.18, NDX 10d ATM put IV 17.11).
The strike is computed off the last close; recompute against the live TWAP level when executing.

| expiry | expiry_day | bdays | adj_vxn_pct | adj_vol_pct | target_moneyness_pct | strike | model_iv_pct | est_premium_pts | est_premium_bps | delta |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | Friday | 2 | 1.798 | 4.494 | 95.51 | 29340.0 | 22.66 | 4.95 | 1.61 | -0.0194 |
| 2026-09-28 | Monday | 3 | 2.202 | 5.505 | 94.5 | 29030.0 | 22.88 | 4.94 | 1.61 | -0.0165 |
| 2026-09-30 | Wednesday | 5 | 2.843 | 7.106 | 92.89 | 28540.0 | 23.1 | 5.21 | 1.7 | -0.014 |

## Caveats

* **Option prices are modelled, not traded quotes.** VolVue provides constant-maturity ATM IV but no strike-level quotes, so OTM premia rely on the
  parametric skew. The sensitivity table shows how results move with the skew assumption, so treat absolute premia as approximate.
* NDX Monday/Wednesday expiries were only listed from the late 2010s (daily NDXP from 2022). Earlier years assume those expiries existed.
* VXN before the CBOE CSV history (and on 1182 days in this sample) uses VolVue NDX 30d mean IV x 1.138.
  79 bad VolVue ATM IV prints were replaced by VXN x rolling median ratio.
* TWAP uses (O+H+L+C)/4 and interpolated IV. Intraday path, early-close days and settlement-price nuances are ignored.
* No margin model: the 1x version is fully cash-secured on average, but a day with several expiries can briefly exceed 1x notional.
