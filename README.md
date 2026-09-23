# Step_SG: NDX systematic short-put ladder

A Python backtest and daily signal for systematically selling short-dated Nasdaq-100 (NDX) puts.
Strikes are set from VXN, and implied vols come from the [VolVue](https://volvue.com) API.

## Strategy

* Every trading day, sell puts on the **three nearest Monday / Wednesday / Friday expiries**. Each expiry is 1-5 business days away.
* `Adjusted VXN = VXN(prev close) * sqrt(bdays / 252)`: VXN rescaled to the option's own tenor.
* `Adj. vol % = 2.5 * Adjusted VXN` and `Target moneyness = 100% - Adj. vol %`. Strikes widen automatically when VXN is high.
* Trades are executed at an approximate full-day TWAP: Black-Scholes at `(O+H+L+C)/4` with the day's interpolated VolVue ATM IV plus a parametric put skew, less costs.
* Positions are held to expiry and cash-settled at the close. At full size, the ladder keeps about 1x NAV of put notional outstanding.
* **Low-VXN size cut:** size is scaled by a multiplier that is 0.25 at VXN <= 15, 1.0 at VXN >= 20, and linear in between (`Params.size_cut`, `cut_vxn_low`, `cut_vxn_full`, `cut_floor`).
* **Money market:** margin follows the CBOE short index put rule. Unused capital earns the money-market rate (13w T-bill - 10 bp, `mm_fee`), and margin earns `collateral_rate_mult` x T-bill (default 0).

## Run

```bash
pip install -r requirements.txt
export VOLVUE_API_KEY=...            # VolVue Premium API key
python -m short_put.run              # full backtest + report -> output/report.md
python -m short_put.run --refresh    # re-download data
python -m short_put.run --venue gs_ndxp --nav 150000000   # institutional setup
python -m short_put.liquidity        # pre-trade check: Cboe quotes at tomorrow's strikes + spread fit
```

## Execution venues

`short_put/costs.py` holds per-venue cost and cash models. Spreads are fitted on Cboe option chains; fees come from IBKR and Nasdaq PHLX schedules.

| Key | Route | Notes |
|---|---|---|
| `ibkr_qqq` (default) | IBKR private, QQQ options | Penny-wide, liquid. About $75k per contract, so NAV of $0.7M or more is needed for 1 lot per tranche |
| `ibkr_ndxp` | IBKR private, NDXP | About $3M per contract and wide quotes. Not practical below about $28M |
| `gs_ndxp` | Goldman institutional, NDXP | Cash-settled and European, with no assignment risk. Needs NAV of about $110M or more for the size cut to work |
| `gs_qqq` | Goldman institutional, QQQ | Cheapest route per unit of premium. Physical settlement |

Use it programmatically:

```python
from short_put.data import load_all
from short_put.strategy import Params, run_backtest, next_day_signal

df = load_all()
res = run_backtest(df, Params(sigma_mult=2.5, leverage=1.0), start="2005-01-03")
print(next_day_signal(df))          # tomorrow's three strikes
```

## Files

| File | Purpose |
|---|---|
| `short_put/data.py` | VolVue API client (NDX IVs), CBOE VXN, Yahoo NDX OHLC and T-bill, cleaning and alignment |
| `short_put/strategy.py` | Expiry calendar, strike rule, TWAP pricing, daily mark-to-model backtest, next-day signal |
| `short_put/risk.py` | Performance and risk statistics, VXN-regime breakdown, stress episodes, NDX beta |
| `short_put/costs.py` | Venue cost models (spread fit x share paid + per-contract fees) and cash terms |
| `short_put/liquidity.py` | Cboe chain loader, quotes at tomorrow's strikes, spread calibration |
| `short_put/run.py` | Runs everything and writes `output/report.md`, charts and CSVs |

See `output/report.md` for the latest results and the modelling caveats.
