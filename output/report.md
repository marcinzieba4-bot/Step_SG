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
* Held to expiry, cash-settled at the close. Each (day, expiry) tranche sells notional = NAV x 1.0 x size multiplier / 9,
  so at full size about 100% of NAV is outstanding on average.
* **Low-VXN size cut:** the size multiplier is 0.25 when VXN(prev close) <= 15, 1.0 when VXN >= 20, linear in between.
  At low VXN the strikes are close to spot and the premium barely covers costs, so exposure is cut there.
* **Cash:** margin is modelled with the CBOE short index put rule (option value + max(15% x S - OTM amount, 10% x K)).
  Capital tied up as margin earns 0% of the T-bill rate. **Unused capital earns the money-market rate** (13w T-bill - 10 bp fee).

## Headline risk statistics

| Metric | Strategy (total return) | Option overlay (excess) | NDX (price) |
|---|---|---|---|
| Start | 2005-01-04 | 2005-01-04 | 2005-01-04 |
| End | 2026-09-22 | 2026-09-22 | 2026-09-22 |
| CAGR | 2.36% | 0.78% | 14.59% |
| Ann. volatility | 1.29% | 1.28% | 22.04% |
| Sharpe (vs T-bill) | 0.45 | 0.61 | 0.65 |
| Sortino | 0.53 | 0.70 | 0.92 |
| Max drawdown | -3.23% | -3.25% | -53.71% |
| Max DD peak | 2025-04-02 | 2025-04-02 | 2007-10-31 |
| Max DD trough | 2025-04-04 | 2025-04-04 | 2008-11-20 |
| Max DD recovered | 2025-08-20 | 2026-07-30 | 2011-01-03 |
| Longest underwater (days) | 577 | 1031 | 1157 |
| Calmar | 0.73 | 0.24 | 0.27 |
| Daily skew | -15.57 | -15.78 | -0.06 |
| Daily excess kurtosis | 532.68 | 543.98 | 8.04 |
| Daily VaR 95% | -0.01% | -0.01% | -2.20% |
| Daily CVaR 95% | -0.11% | -0.11% | -3.29% |
| Daily VaR 99% | -0.08% | -0.09% | -3.99% |
| Daily CVaR 99% | -0.43% | -0.44% | -5.19% |
| Worst day | -3.05% | -3.06% | -12.19% |
| Worst day date | 2025-04-04 | 2025-04-04 | 2020-03-16 |
| Worst week | -3.02% | -3.09% | -13.67% |
| Worst month | -1.68% | -1.70% | -16.30% |
| Best month | 1.14% | 1.12% | 15.64% |
| % positive days | 86.36% | 77.61% | 55.35% |
| % positive months | 96.55% | 94.64% | 61.30% |

![Equity curve](equity_curve.png)
![Drawdown](drawdown.png)

## Low-VXN size cut and money-market cash

| Return source / capital usage | Value |
|---|---|
| Money-market interest on unused capital (ann.) | 1.56% |
| Interest on margin collateral (ann.) | 0.00% |
| Option overlay P&L (ann., arith.) | 0.79% |
| Avg margin used (% NAV) | 7.1% |
| Peak margin used (% NAV) | 15.3% |
| Avg unused capital in money market (% NAV) | 92.9% |
| Avg size multiplier | 0.77 |
| % of days with a size cut | 50.2% |

| VXN regime | % of days | Avg size multiplier | Margin used (% NAV) | Unused in money market (% NAV) | MM interest (ann.) | Total return (ann., arith.) |
|---|---|---|---|---|---|---|
| Low (<18) | 35.49% | 0.41 | 4.0% | 96.0% | 1.55% | 1.64% |
| Normal (18-25) | 36.81% | 0.94 | 8.6% | 91.4% | 1.90% | 2.18% |
| Elevated (25-35) | 20.87% | 1.00 | 9.0% | 91.0% | 1.33% | 1.91% |
| Crisis (>35) | 6.81% | 1.00 | 8.5% | 91.5% | 0.47% | 8.28% |

![Size cut and cash](size_cut_cash.png)

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
| Correlation to NDX | 0.38 |
| Down-market beta | 0.04 |
| Up-market beta | 0.02 |
| Avg return on NDX worst-2% days | -0.20% |
| Avg NDX on those days | -4.34% |

| Exposure as of 2026-09-22 | Value |
|---|---|
| Open put tranches | 10 |
| Gross put notional / NAV | 100.9% |
| Net delta (equity-equivalent % NAV) | 0.32% |
| Gamma (Δdelta per 1% NDX, % NAV) | -0.39% |
| Vega (NAV per +1 vol pt) | -0.001% |
| Avg gross notional over history | 68.2% |
| Max net delta over history | 45.1% |

![Greeks](greeks.png)

## Risk by VXN regime (regime at trade time)

| Regime | % of days | Ann. return (arith.) | Ann. vol | Sharpe (raw) | Worst day | Avg moneyness | Avg premium (bps) | % ITM |
|---|---|---|---|---|---|---|---|---|
| Low (<18) | 35.49% | 1.64% | 0.19% | 8.74 | -0.18% | 95.88% | 0.91 | 0.69% |
| Normal (18-25) | 36.81% | 2.18% | 0.79% | 2.78 | -1.18% | 94.51% | 1.67 | 0.82% |
| Elevated (25-35) | 20.87% | 1.91% | 2.15% | 0.89 | -3.05% | 92.42% | 2.88 | 0.48% |
| Crisis (>35) | 6.81% | 8.28% | 2.57% | 3.22 | -1.16% | 88.35% | 4.56 | 0.27% |

![VXN and moneyness](vxn_moneyness.png)

## Stress episodes

| Episode | Strategy | Strategy max DD | Worst day | NDX |
|---|---|---|---|---|
| GFC (Sep-Nov 2008) | 0.13% | -1.16% | -1.16% | -38.08% |
| Flash crash (May 2010) | -0.25% | -0.70% | -0.47% | -9.33% |
| US downgrade (Aug 2011) | -1.01% | -2.19% | -2.12% | -7.76% |
| China deval. (Aug 2015) | -1.70% | -2.83% | -1.58% | -5.65% |
| Volmageddon (Feb 2018) | -0.31% | -1.04% | -1.03% | -8.69% |
| Q4 2018 selloff | 0.96% | -0.51% | -0.51% | -22.66% |
| COVID crash (Feb-Mar 2020) | -0.49% | -1.98% | -0.97% | -27.24% |
| 2022 bear market | 3.09% | -0.86% | -0.81% | -34.49% |
| Yen carry unwind (Aug 2024) | 0.27% | -0.45% | -0.37% | -12.64% |
| DeepSeek (Jan 2025) | 0.12% | -0.04% | -0.04% | -1.93% |
| Tariff shock (Apr 2025) | -2.68% | -3.23% | -3.05% | -15.31% |

## Calendar-year returns

| Year | Strategy | Overlay (excess) | NDX |
|---|---|---|---|
| 2005 | 3.18% | 0.25% | 2.60% |
| 2006 | 4.88% | 0.46% | 6.79% |
| 2007 | 3.92% | -0.13% | 18.67% |
| 2008 | 2.22% | 1.05% | -41.89% |
| 2009 | 1.43% | 1.38% | 53.54% |
| 2010 | 0.50% | 0.47% | 19.22% |
| 2011 | 0.00% | -0.00% | 2.70% |
| 2012 | 0.83% | 0.83% | 16.82% |
| 2013 | 0.30% | 0.30% | 34.99% |
| 2014 | 0.49% | 0.49% | 17.94% |
| 2015 | -0.87% | -0.88% | 8.43% |
| 2016 | 1.14% | 0.95% | 5.89% |
| 2017 | 0.96% | 0.17% | 31.52% |
| 2018 | 2.82% | 1.10% | -1.04% |
| 2019 | 2.87% | 1.03% | 37.96% |
| 2020 | 1.90% | 1.66% | 47.58% |
| 2021 | 1.20% | 1.20% | 26.63% |
| 2022 | 4.74% | 2.99% | -32.97% |
| 2023 | 6.41% | 1.73% | 53.81% |
| 2024 | 5.73% | 1.04% | 24.88% |
| 2025 | 3.36% | -0.30% | 20.17% |
| 2026 | 3.62% | 1.23% | 21.68% |

![Rolling](rolling.png)
![Distribution](return_distribution.png)

## Sensitivity to design choices and model assumptions

| Variant | CAGR | Overlay CAGR | Vol | Sharpe | Max DD | Worst day | Avg moneyness | % ITM |
|---|---|---|---|---|---|---|---|---|
| Base: 2.5x, skew 0.12, 1x, cut 15-20, MM | 2.36% | 0.78% | 1.29% | 0.45 | -3.23% | -3.05% | 94.1% | 0.66% |
| No low-VXN size cut | 2.34% | 0.79% | 1.52% | 0.37 | -5.51% | -3.05% | 94.1% | 0.66% |
| Size cut ramp 14-18 | 2.35% | 0.78% | 1.38% | 0.41 | -3.99% | -3.05% | 94.1% | 0.66% |
| Size cut floor 0 (stop below VXN 15) | 2.37% | 0.78% | 1.24% | 0.48 | -3.23% | -3.05% | 94.1% | 0.66% |
| Collateral also earns T-bill | 2.50% | 0.78% | 1.29% | 0.55 | -3.23% | -3.05% | 94.1% | 0.66% |
| No interest at all (pure overlay) | 0.78% | 0.78% | 1.28% | -0.76 | -3.25% | -3.06% | 94.1% | 0.66% |
| Multiplier 2.0x | 3.36% | 1.77% | 2.14% | 0.73 | -4.63% | -4.26% | 95.3% | 1.56% |
| Multiplier 3.0x | 1.91% | 0.31% | 0.78% | 0.17 | -2.18% | -1.95% | 92.7% | 0.34% |
| No skew (flat ATM vol) | 1.35% | -0.27% | 1.13% | -0.36 | -3.80% | -3.11% | 93.7% | 1.10% |
| Steeper skew 0.20 | 4.47% | 2.85% | 1.36% | 1.93 | -3.19% | -2.99% | 94.1% | 0.65% |
| Double t-costs | 2.18% | 0.59% | 1.29% | 0.32 | -3.24% | -3.06% | 94.0% | 0.72% |
| Leverage 2x | 3.03% | 1.56% | 2.58% | 0.49 | -6.48% | -6.13% | 94.1% | 0.66% |
| Leverage 3x | 3.69% | 2.34% | 3.89% | 0.50 | -9.72% | -9.21% | 94.1% | 0.66% |

## Next trading day: trades to place

Based on the 2026-09-22 close (NDX 30,723.71, VXN 20.18, NDX 10d ATM put IV 17.11).
The strike is computed off the last close; recompute against the live TWAP level when executing.

| expiry | expiry_day | bdays | adj_vxn_pct | adj_vol_pct | target_moneyness_pct | strike | model_iv_pct | est_premium_pts | est_premium_bps | delta | size_mult | notional_pct_nav |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | Friday | 2 | 1.798 | 4.494 | 95.51 | 29340.0 | 22.66 | 4.95 | 1.61 | -0.0194 | 1.0 | 11.11 |
| 2026-09-28 | Monday | 3 | 2.202 | 5.505 | 94.5 | 29030.0 | 22.88 | 4.94 | 1.61 | -0.0165 | 1.0 | 11.11 |
| 2026-09-30 | Wednesday | 5 | 2.843 | 7.106 | 92.89 | 28540.0 | 23.1 | 5.21 | 1.7 | -0.014 | 1.0 | 11.11 |

## Caveats

* **Option prices are modelled, not traded quotes.** VolVue provides constant-maturity ATM IV but no strike-level quotes, so OTM premia rely on the
  parametric skew. The sensitivity table shows how results move with the skew assumption, so treat absolute premia as approximate.
* NDX Monday/Wednesday expiries were only listed from the late 2010s (daily NDXP from 2022). Earlier years assume those expiries existed.
* VXN before the CBOE CSV history (and on 1182 days in this sample) uses VolVue NDX 30d mean IV x 1.138.
  79 bad VolVue ATM IV prints were replaced by VXN x rolling median ratio.
* TWAP uses (O+H+L+C)/4 and interpolated IV. Intraday path, early-close days and settlement-price nuances are ignored.
* Margin uses the CBOE minimum for short broad-index puts. A broker may charge more (house margin), which leaves less capital in the money market.
  The money-market rate is proxied by the 13w T-bill (^IRX) minus a fee.
* The size-cut thresholds (15/20) were chosen after looking at P&L by VXN bucket, so they are in-sample. The sensitivity rows show that nearby choices behave similarly.
