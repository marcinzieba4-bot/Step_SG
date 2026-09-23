# Step_SG: systematic short-put ladder (S&P 500 / Nasdaq-100)

A Python backtest and daily signal for systematically selling short-dated S&P 500 index puts (XSP at IBKR, SPY at Goldman Sachs). A Nasdaq-100 version is also available.
Strikes are set from VIX (or VXN), and implied vols come from the [VolVue](https://volvue.com) API.

## Strategy

* **Underlying:** S&P 500 (SPX), with **VIX** as the strike driver. The Nasdaq-100 with VXN is still available via `--underlying NDX`.
* Every trading day, sell puts on the **three nearest Monday / Wednesday / Friday expiries**. Each expiry is 1-5 business days away.
* `Adjusted VIX = VIX(prev close) * sqrt(bdays / 252)`: VIX rescaled to the option's own tenor.
* `Adj. vol % = 2.5 * Adjusted VIX` and `Target moneyness = 100% - Adj. vol %`. Strikes widen automatically when VIX is high.
* Trades are executed at an approximate full-day TWAP: Black-Scholes at `(O+H+L+C)/4` with the day's interpolated VolVue ATM IV plus a put skew calibrated on the option chains, less venue costs.
* Positions are held to expiry and cash-settled at the close. At full size, the ladder keeps about 1x NAV of put notional outstanding.
* **Low-VIX size cut:** size is scaled by a multiplier that is 0.25 at VIX <= 13, 1.0 at VIX >= 17, and linear in between.
* **Term-structure filter:** no new sales while VIX/VIX3M (previous close) is above 0.95. It was 0.98-1.01 from 28 Mar 2025, ahead of the April crash.
* **Stop-loss:** buy back any short put worth 3x its premium at the close.
* **No interest:** returns are option P&L only. Money-market and collateral interest is off (`Params.include_cash=False`).

## Run

```bash
pip install -r requirements.txt
export VOLVUE_API_KEY=...            # VolVue Premium API key
python -m short_put.run              # S&P 500: XSP at IBKR + SPY at GS -> output/report.md
python -m short_put.run --nav 2000000 --nav-inst 250000000   # size the next-day orders
python -m short_put.run --underlying NDX --venue ibkr_qqq --venue-inst gs_ndxp   # Nasdaq-100 version
python -m short_put.liquidity SPX    # pre-trade check: Cboe quotes at tomorrow's strikes + spread fit
python -m short_put.hedges           # hedge / regime study -> output/hedges.md
```

## Execution venues

`short_put/costs.py` holds per-venue cost models. Spreads are fitted on Cboe option chains; fees come from the IBKR, Cboe and Nasdaq PHLX schedules.

| Key | Route | Notes |
|---|---|---|
| `ibkr_xsp` (default, private) | IBKR, XSP (Mini-SPX) | Cash-settled, European. About $78k per contract. Quotes are wider than SPY (half-spread about 9-18% of premium at our strikes) |
| `gs_spy` (default, institutional) | Goldman Sachs, SPY | Penny-wide, deepest market. American-style, settles in shares |
| `ibkr_spy`, `gs_spxw` | Comparison routes | SPY at IBKR; SPXW at GS (about $0.78M per contract, the tightest in % of premium) |
| `ibkr_qqq`, `ibkr_ndxp`, `gs_ndxp`, `gs_qqq` | Nasdaq-100 routes | Used with `--underlying NDX` |

Use it programmatically:

```python
from short_put.data import load_all
from short_put.costs import params_for
from short_put.strategy import run_backtest, next_day_signal

df = load_all("SPX")
p = params_for("ibkr_xsp")          # SPX defaults + XSP-at-IBKR cost model
res = run_backtest(df, p, start="2005-01-03")
print(next_day_signal(df, p, nav=1_000_000))   # tomorrow's three XSP strikes and contracts
```

## Files

| File | Purpose |
|---|---|
| `short_put/data.py` | VolVue API client (index IVs), CBOE VIX/VXN, Yahoo index OHLC and T-bill, cleaning and alignment |
| `short_put/strategy.py` | Expiry calendar, strike rule, TWAP pricing, daily mark-to-model backtest, next-day signal |
| `short_put/risk.py` | Performance and risk statistics, VIX/VXN-regime breakdown, stress episodes, index beta |
| `short_put/costs.py` | Venue cost models (spread fit x share paid + per-contract fees) |
| `short_put/liquidity.py` | Cboe chain loader, quotes at tomorrow's strikes, spread calibration |
| `short_put/hedges.py` | Study of hedges (wings, stop-losses, tail puts) and regime filters (VIX term structure, trend, shocks) |
| `short_put/run.py` | Runs everything and writes `output/report.md`, charts and CSVs |

See `output/report.md` for the latest results and the modelling caveats.
