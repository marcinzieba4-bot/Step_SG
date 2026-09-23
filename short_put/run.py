"""Run the NDX short-put ladder backtest and write the risk report.

Usage:
    export VOLVUE_API_KEY=...
    python -m short_put.run [--start 2005-01-03] [--refresh]

Outputs go to ``output/``: report.md, charts (*.png) and CSVs.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import risk  # noqa: E402
from .data import load_all  # noqa: E402
from .strategy import Params, next_day_signal, run_backtest  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "output"

# Reference palette (dataviz skill), light mode
C1, C2, C3, C8 = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _style():
    plt.rcParams.update({
        "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb",
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "semibold", "axes.titlelocation": "left",
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.color": INK2, "ytick.color": INK2, "font.size": 9.5,
        "legend.frameon": False, "lines.linewidth": 1.6,
    })


def pct(x, d=2):
    return "n/a" if pd.isna(x) else f"{x * 100:.{d}f}%"


def fmt_stats(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, (float, np.floating)):
            if any(s in k for s in ("Sharpe", "Sortino", "Calmar", "skew", "kurtosis", "Beta", "beta",
                                    "Correlation", "premium units", "x premium", "bdays", "index pts", "bps")):
                out[k] = f"{v:.2f}"
            else:
                out[k] = pct(v)
        else:
            out[k] = v
    return out


def md_table(df: pd.DataFrame) -> str:
    cols = [df.index.name or ""] + list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for idx, row in df.iterrows():
        lines.append("| " + " | ".join([str(idx)] + [str(v) for v in row.values]) + " |")
    return "\n".join(lines)


def charts(res, cash_ret, ex_ret, df):
    _style()
    d = res.daily
    ret = d["ret"]

    # 1) Growth of $1 (log): strategy total return vs T-bill cash
    fig, ax = plt.subplots(figsize=(10, 4.2))
    nav = (1 + ret).cumprod()
    cash_nav = (1 + cash_ret).cumprod()
    ex_nav = (1 + ex_ret).cumprod()
    ax.plot(nav.index, nav, color=C1, label="Short-put ladder (total return)")
    ax.plot(ex_nav.index, ex_nav, color=C2, label="Option overlay only (excess of cash)")
    ax.plot(cash_nav.index, cash_nav, color=C3, label="T-bill collateral")
    for s, lab in ((nav, "total"), (ex_nav, "overlay"), (cash_nav, "cash")):
        ax.annotate(f"{s.iloc[-1]:.2f}x", (s.index[-1], s.iloc[-1]), xytext=(4, 0),
                    textcoords="offset points", va="center", color=INK2, fontsize=8.5)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.1f}x"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title("Growth of $1, NDX 1-5 day short-put ladder (1x notional)")
    ax.legend(loc="upper left")
    fig.tight_layout(); fig.savefig(OUT / "equity_curve.png", dpi=140); plt.close(fig)

    # 2) Drawdown
    fig, ax = plt.subplots(figsize=(10, 3.2))
    dd = nav / nav.cummax() - 1
    ndx_dd = df["close"].reindex(nav.index) / df["close"].reindex(nav.index).cummax() - 1
    ax.fill_between(dd.index, dd * 100, 0, color=C1, alpha=0.35, linewidth=0)
    ax.plot(dd.index, dd * 100, color=C1, linewidth=1, label="Strategy")
    ax.set_ylabel("Drawdown (%)")
    ax.set_title(f"Strategy drawdown (max {dd.min() * 100:.1f}%; NDX max {ndx_dd.min() * 100:.0f}% for reference)")
    fig.tight_layout(); fig.savefig(OUT / "drawdown.png", dpi=140); plt.close(fig)

    # 3) Strike selection: VXN and target moneyness (two panels, one axis each)
    t = res.trades.copy()
    t["trade_date"] = pd.to_datetime(t["trade_date"])
    m = t.groupby("trade_date")["moneyness"].mean().rolling(5).mean()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(d.index, d["vxn"], color=C1, linewidth=1)
    a1.set_title("VXN (strike driver)")
    a1.set_ylabel("VXN")
    for lo, _, _ in risk.VXN_REGIMES[1:]:
        a1.axhline(lo, color=INK2, linewidth=0.6, linestyle=":")
    a2.plot(m.index, m * 100, color=C2, linewidth=1)
    a2.set_title("Average target moneyness of puts sold (5d avg)")
    a2.set_ylabel("Strike / spot (%)")
    fig.tight_layout(); fig.savefig(OUT / "vxn_moneyness.png", dpi=140); plt.close(fig)

    # 4) Rolling 1y vol and 1y return
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(ret.index, ret.rolling(252).std() * np.sqrt(252) * 100, color=C1, linewidth=1)
    a1.set_title("Rolling 1-year volatility (%)")
    roll = (1 + ret).rolling(252).apply(np.prod, raw=True) - 1
    a2.plot(roll.index, roll * 100, color=C1, linewidth=1)
    a2.axhline(0, color=INK2, linewidth=0.8)
    a2.set_title("Rolling 1-year total return (%)")
    fig.tight_layout(); fig.savefig(OUT / "rolling.png", dpi=140); plt.close(fig)

    # 5) Daily return distribution
    fig, ax = plt.subplots(figsize=(10, 3.4))
    ax.hist(ret * 1e4, bins=200, color=C1, alpha=0.85, edgecolor="#fcfcfb", linewidth=0.3)
    ax.set_yscale("log")
    ax.set_xlabel("Daily return (bps)")
    ax.set_ylabel("Days (log)")
    ax.axvline(ret.quantile(0.01) * 1e4, color=C8, linewidth=1.2, linestyle="--")
    ax.annotate("VaR 99%", (ret.quantile(0.01) * 1e4, ax.get_ylim()[1] * 0.3), xytext=(-48, 0),
                textcoords="offset points", color=INK2, fontsize=8.5)
    ax.set_title("Daily return distribution: small steady gains, fat left tail")
    fig.tight_layout(); fig.savefig(OUT / "return_distribution.png", dpi=140); plt.close(fig)

    # 6) Exposure: $delta and $gamma per 1% move
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(d.index, d["delta"] * 100, color=C1, linewidth=0.8)
    a1.set_title("Net delta (% of NAV per 100% NDX move; i.e. equity-equivalent exposure)")
    a2.plot(d.index, d["gamma_1pct"] * 100, color=C2, linewidth=0.8)
    a2.set_title("Gamma: change in delta (% NAV) for a 1% NDX move")
    fig.tight_layout(); fig.savefig(OUT / "greeks.png", dpi=140); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2005-01-03")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    df = load_all(refresh=args.refresh)
    base = Params()
    res = run_backtest(df, base, start=args.start)
    d = res.daily
    ret = d["ret"]
    rf = df["rf"].reindex(d.index)
    cash_ret = d["interest"]
    ex_ret = ret - cash_ret  # option-overlay-only return

    # ---------------- statistics
    stats = pd.DataFrame({
        "Strategy (total return)": fmt_stats(risk.perf_stats(ret, rf)),
        "Option overlay (excess)": fmt_stats(risk.perf_stats(ex_ret, None)),
        "NDX (price)": fmt_stats(risk.perf_stats(df["close"].pct_change().reindex(d.index), rf)),
    })
    stats.index.name = "Metric"
    stats = stats.drop(index="Label")
    tstats = pd.Series(fmt_stats(risk.trade_stats(res.trades)), name="Value").to_frame()
    tstats.index.name = "Trade statistic"
    beta = pd.Series(fmt_stats(risk.ndx_beta(ret, df["close"])), name="Value").to_frame()
    beta.index.name = "Market sensitivity"

    reg = risk.regime_stats(d, res.trades)
    reg_fmt = reg.copy()
    for c in reg_fmt.columns:
        reg_fmt[c] = reg[c].map(lambda v, c=c: f"{v:.2f}" if ("Sharpe" in c or "bps" in c) else pct(v))
    stress = risk.stress_table(ret, df["close"]).map(pct)
    cal = pd.DataFrame({
        "Strategy": risk.calendar_returns(ret),
        "Overlay (excess)": risk.calendar_returns(ex_ret),
        "NDX": risk.calendar_returns(df["close"].pct_change().reindex(d.index).fillna(0)),
    }).map(pct)
    cal.index.name = "Year"

    # current exposures (last day)
    last = d.iloc[-1]
    exposure = pd.Series({
        "Open put tranches": int(last["n_open"]),
        "Gross put notional / NAV": pct(last["gross_notional"], 1),
        "Net delta (equity-equivalent % NAV)": pct(last["delta"], 2),
        "Gamma (Δdelta per 1% NDX, % NAV)": pct(last["gamma_1pct"], 2),
        "Vega (NAV per +1 vol pt)": pct(last["vega_1pt"], 3),
        "Avg gross notional over history": pct(d["gross_notional"].mean(), 1),
        "Max net delta over history": pct(d["delta"].max(), 1),
    }, name="Value").to_frame()
    exposure.index.name = f"Exposure as of {d.index[-1].date()}"

    # ---------------- sensitivities
    sens_rows = []
    variants = [
        ("Base: 2.5x, skew 0.12, 1x", base),
        ("Multiplier 2.0x", replace(base, sigma_mult=2.0)),
        ("Multiplier 3.0x", replace(base, sigma_mult=3.0)),
        ("No skew (flat ATM vol)", replace(base, skew_beta=0.0)),
        ("Steeper skew 0.20", replace(base, skew_beta=0.20)),
        ("Double t-costs", replace(base, tc_pct_premium=0.10, tc_min_points=0.20)),
        ("Leverage 2x", replace(base, leverage=2.0)),
        ("Leverage 3x", replace(base, leverage=3.0)),
    ]
    for name, p in variants:
        r = res if p is base else run_backtest(df, p, start=args.start)
        rr = r.daily["ret"]
        ex = rr - r.daily["interest"]
        s = risk.perf_stats(rr, rf)
        se = risk.perf_stats(ex, None)
        ts = risk.trade_stats(r.trades)
        sens_rows.append({
            "Variant": name,
            "CAGR": pct(s["CAGR"]), "Overlay CAGR": pct(se["CAGR"]),
            "Vol": pct(s["Ann. volatility"]), "Sharpe": f"{s['Sharpe (vs T-bill)']:.2f}",
            "Max DD": pct(s["Max drawdown"]), "Worst day": pct(s["Worst day"]),
            "Avg moneyness": pct(ts["Avg target moneyness"], 1),
            "% ITM": pct(ts["% expiring ITM (breached)"]),
        })
        print(f"  ran {name}")
    sens = pd.DataFrame(sens_rows).set_index("Variant")

    signal = next_day_signal(df, base)

    # ---------------- outputs
    charts(res, cash_ret, ex_ret, df)
    d.to_csv(OUT / "daily.csv")
    res.trades.to_csv(OUT / "trades.csv", index=False)
    signal.to_csv(OUT / "next_day_signal.csv", index=False)

    proxy_days = (df.loc[args.start:, "vxn_src"] != "CBOE").sum()
    sig_tbl = signal.set_index("expiry")[[
        "expiry_day", "bdays", "adj_vxn_pct", "adj_vol_pct", "target_moneyness_pct",
        "strike", "model_iv_pct", "est_premium_pts", "est_premium_bps", "delta"]]

    report = f"""# NDX short-put ladder: systematic 1-5 day put selling

Backtest {d.index[0].date()} to {d.index[-1].date()} ({len(d):,} trading days, {len(res.trades):,} puts sold).
Data: VolVue API (NDX implied vols), CBOE VXN, Yahoo (NDX OHLC, 13w T-bill).

## Rules

* Every trading day, sell NDX puts on the **three nearest Monday / Wednesday / Friday expiries** (each 1-5 business days out).
* **Adjusted VXN** = VXN(prev close) x sqrt(bdays / 252): VXN rescaled from 30-day to the option's own tenor.
* **Adj. vol %** = {base.sigma_mult} x Adjusted VXN; **target moneyness** = 100% - Adj. vol %; strike rounded down to a {base.strike_step:.0f}-pt grid.
  When VXN rises, strikes automatically move further out-of-the-money (the volatility-regime adjustment).
* Executed at the **full-day TWAP**, approximated as Black-Scholes at spot (O+H+L+C)/4 and the average of the previous and current day's
  VolVue 10-day ATM put IV, with a parametric put skew (IV x (1 + {base.skew_beta} x SDs OTM), cap {base.skew_cap}x). Cost: {base.tc_pct_premium:.0%} of premium, min {base.tc_min_points} pts.
* Held to expiry, cash-settled at the close. Each (day, expiry) tranche sells notional = NAV x {base.leverage} / {base.tranche_divisor:.0f},
  so about {base.leverage:.0%} of NAV is outstanding on average; collateral earns the T-bill rate.

## Headline risk statistics

{md_table(stats)}

![Equity curve](equity_curve.png)
![Drawdown](drawdown.png)

## Trade statistics

{md_table(tstats)}

## Market sensitivity and current exposure

{md_table(beta)}

{md_table(exposure)}

![Greeks](greeks.png)

## Risk by VXN regime (regime at trade time)

{md_table(reg_fmt)}

![VXN and moneyness](vxn_moneyness.png)

## Stress episodes

{md_table(stress)}

## Calendar-year returns

{md_table(cal)}

![Rolling](rolling.png)
![Distribution](return_distribution.png)

## Sensitivity to design choices and model assumptions

{md_table(sens)}

## Next trading day: trades to place

Based on the {d.index[-1].date()} close (NDX {df['close'].iloc[-1]:,.2f}, VXN {df['vxn'].iloc[-1]:.2f}, NDX 10d ATM put IV {df['atm_vol'].iloc[-1]:.2f}).
The strike is computed off the last close; recompute against the live TWAP level when executing.

{md_table(sig_tbl)}

## Caveats

* **Option prices are modelled, not traded quotes.** VolVue provides constant-maturity ATM IV but no strike-level quotes, so OTM premia rely on the
  parametric skew. The sensitivity table shows how results move with the skew assumption, so treat absolute premia as approximate.
* NDX Monday/Wednesday expiries were only listed from the late 2010s (daily NDXP from 2022). Earlier years assume those expiries existed.
* VXN before the CBOE CSV history (and on {proxy_days} days in this sample) uses VolVue NDX 30d mean IV x {df.attrs.get('vxn_proxy_ratio', float('nan')):.3f}.
  {df.attrs.get('atm_vol_cleaned_days', 0)} bad VolVue ATM IV prints were replaced by VXN x rolling median ratio.
* TWAP uses (O+H+L+C)/4 and interpolated IV. Intraday path, early-close days and settlement-price nuances are ignored.
* No margin model: the 1x version is fully cash-secured on average, but a day with several expiries can briefly exceed 1x notional.
"""
    (OUT / "report.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
