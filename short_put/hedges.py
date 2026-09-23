"""Hedge and regime-filter study for the S&P 500 short-put ladder.

Tests, on the XSP-at-IBKR cost model and the full history:
* hedges: put-spread wings, stop-loss buy-backs, a rolling 1-month tail put;
* regime filters: VIX term structure (VIX/VIX3M), trend (moving averages),
  post-shock pauses, opening-gap skips, VIX-open strikes;
* combinations, with a split-sample check (2005-2015 vs 2016-2026).

Usage:
    python -m short_put.hedges [--venue ibkr_xsp] [--start 2005-01-03]

Writes output/hedges.md and output/hedges_*.png.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from . import risk  # noqa: E402
from .costs import params_for  # noqa: E402
from .data import load_all  # noqa: E402
from .strategy import UNMANAGED, run_backtest  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "output"
EPISODES = {
    "Aug 2011": ("2011-07-25", "2011-08-31"),
    "Aug 2015": ("2015-08-17", "2015-08-31"),
    "Feb 2018": ("2018-01-29", "2018-02-09"),
    "COVID 2020": ("2020-02-19", "2020-03-23"),
    "Apr 2025": ("2025-03-25", "2025-04-08"),
}
TS = dict(ts_threshold=0.95, ts_mult=0.0)
U = UNMANAGED  # every variant starts from the unmanaged baseline
VARIANTS = {
    "Baseline (unmanaged)": {**U},
    # regime filters
    "Regime: VIX/VIX3M > 1.00, no new sales": {**U, "ts_threshold": 1.0, "ts_mult": 0.0},
    "Regime: VIX/VIX3M > 0.95, no new sales": {**U, **TS},
    "Regime: VIX/VIX3M > 0.95, half size": {**U, "ts_threshold": 0.95, "ts_mult": 0.5},
    "Regime: below 200-day average, no new sales": {**U, "trend_ma": 200, "trend_mult": 0.0},
    "Regime: below 200-day average, half size": {**U, "trend_ma": 200, "trend_mult": 0.5},
    "Regime: below 50-day average, half size": {**U, "trend_ma": 50, "trend_mult": 0.5},
    "Regime: pause 3 days after a -1.5% day": {**U, "shock_ret": 0.015, "shock_days": 3, "shock_mult": 0.0},
    "Regime: skip days opening -1% or worse": {**U, "gap_ret": 0.01, "gap_mult": 0.0},
    "Regime: strikes from max(VIX close, VIX open)": {**U, "vol_open_strikes": True},
    # hedges
    "Hedge: wing at 1.3x distance (put spread)": {**U, "wing_mult": 1.3},
    "Hedge: wing at 1.5x distance (put spread)": {**U, "wing_mult": 1.5},
    "Hedge: wing at 2.0x distance (put spread)": {**U, "wing_mult": 2.0},
    "Hedge: stop-loss at 3x premium": {**U, "stop_mult": 3.0},
    "Hedge: stop-loss at 5x premium": {**U, "stop_mult": 5.0},
    "Hedge: 1M 90% tail put, 25% of NAV": {**U, "tail_notional": 0.25},
    "Hedge: 1M 95% tail put, 25% of NAV": {**U, "tail_notional": 0.25, "tail_mny": 0.95},
    # combinations
    "Combo: VIX/VIX3M > 0.95 + stop 3x (new default)": {**TS, "stop_mult": 3.0},
    "Combo: VIX/VIX3M > 0.95 + stop 5x": {**TS, "stop_mult": 5.0},
    "Combo: VIX/VIX3M > 1.00 + stop 5x": {"ts_threshold": 1.0, "ts_mult": 0.0, "stop_mult": 5.0},
    "Combo: default, stops cost 3x spread": {**TS, "stop_mult": 3.0, "stop_cost_mult": 3.0},
    "Combo: default at 2x leverage": {**TS, "stop_mult": 3.0, "leverage": 2.0},
    "Combo: default at 3x leverage": {**TS, "stop_mult": 3.0, "leverage": 3.0},
}

_DF = None


def _run(args):
    name, kw, venue, start = args
    global _DF
    if _DF is None:
        _DF = load_all("SPX")
    r = run_backtest(_DF, params_for(venue, **kw), start=start)
    d = r.daily
    s = risk.perf_stats(d["ret"], None)
    a = risk.perf_stats(d["ret"].loc[:"2015-12-31"], None)
    b = risk.perf_stats(d["ret"].loc["2016-01-01":], None)
    c = risk.perf_stats(d["ret"].loc["2022-05-02":], None)
    yrs = len(d) / 252
    row = {
        "Variant": name, "CAGR": s["CAGR"], "Vol": s["Ann. volatility"], "Sharpe": s["Sharpe"],
        "Max DD": s["Max drawdown"], "Worst day": s["Worst day"], "Worst month": s["Worst month"],
        "Sharpe 2005-15": a["Sharpe"], "Sharpe 2016-26": b["Sharpe"], "Sharpe since 2022": c["Sharpe"],
        "CAGR since 2022": c["CAGR"], "Hedge spend p.a.": d["hedge_paid"].sum() / yrs,
        "Stops p.a.": d["stops"].sum() / yrs, "Days with no new sales": (d["size_mult"] == 0).mean(),
    }
    for k, (x, y) in EPISODES.items():
        row[k] = (1 + d["ret"].loc[x:y]).prod() - 1
    return row, d["ret"]


def _fmt(df: pd.DataFrame) -> pd.DataFrame:
    f = df.copy()
    for c in f.columns:
        if c.startswith("Sharpe"):
            f[c] = f[c].map(lambda v: f"{v:.2f}")
        elif c == "Stops p.a.":
            f[c] = f[c].map(lambda v: f"{v:.0f}")
        else:
            f[c] = f[c].map(lambda v: f"{v * 100:.2f}%")
    return f


def main():
    from .run import C1, C2, C3, INK2, _style, md_table

    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", default="ibkr_xsp")
    ap.add_argument("--start", default="2005-01-03")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    jobs = [(n, kw, args.venue, args.start) for n, kw in VARIANTS.items()]
    with ProcessPoolExecutor(4) as ex:
        results = list(ex.map(_run, jobs))
    rows = pd.DataFrame([r for r, _ in results]).set_index("Variant")
    rets = {n: ret for (n, _), (_, ret) in zip(VARIANTS.items(), results)}

    main_cols = ["CAGR", "Vol", "Sharpe", "Max DD", "Worst day", "Worst month", "Hedge spend p.a.", "Stops p.a.",
                 "Days with no new sales"]
    robust_cols = ["Sharpe 2005-15", "Sharpe 2016-26", "Sharpe since 2022", "CAGR since 2022"]
    epi_cols = list(EPISODES)

    # charts: baseline vs default vs default at 3x
    _style()
    show = {"Baseline (unmanaged)": C1, "Combo: VIX/VIX3M > 0.95 + stop 3x (new default)": C2,
            "Combo: default at 3x leverage": C3}
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    for n, c in show.items():
        nav = (1 + rets[n]).cumprod()
        a1.plot(nav.index, nav, color=c, label=n.replace("Combo: ", ""))
        a1.annotate(f"{nav.iloc[-1]:.2f}x", (nav.index[-1], nav.iloc[-1]), xytext=(4, 0),
                    textcoords="offset points", va="center", color=INK2, fontsize=8.5)
        dd = nav / nav.cummax() - 1
        a2.plot(dd.index, dd * 100, color=c, linewidth=1)
    a1.set_title("Growth of $1 (option P&L only)")
    a1.legend(loc="upper left")
    a2.set_title("Drawdown (%)")
    fig.tight_layout(); fig.savefig(OUT / "hedges_equity.png", dpi=140); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.6))
    for n, c in show.items():
        r = rets[n].loc["2025-03-20":"2025-04-30"]
        ax.plot(r.index, ((1 + r).cumprod() - 1) * 100, color=c, label=n.replace("Combo: ", ""), marker="o", markersize=3)
    ax.axhline(0, color=INK2, linewidth=0.8)
    ax.set_title("April 2025 tariff shock: cumulative return from 20 Mar 2025 (%)")
    ax.legend(loc="lower left")
    fig.tight_layout(); fig.savefig(OUT / "hedges_apr2025.png", dpi=140); plt.close(fig)

    df = load_all("SPX")
    ratio = (df["vol_index"] / df["vol_3m"]).loc["2025-03-24":"2025-04-10"].round(3)
    ts_tbl = pd.DataFrame({"VIX": df["vol_index"].loc[ratio.index], "VIX3M": df["vol_3m"].loc[ratio.index].round(2),
                           "VIX / VIX3M": ratio, "New sales next day": ["no" if v > 0.95 else "yes" for v in ratio]})
    ts_tbl.index = ts_tbl.index.date
    ts_tbl.index.name = "Close"

    report = f"""# Cheap hedges and regime filters for the S&P 500 short-put ladder

Cost model: {args.venue}. Backtest {args.start} to {rets['Baseline (unmanaged)'].index[-1].date()}, option P&L only (no interest).
Every variant changes one thing relative to the unmanaged baseline, except the combinations.

## Summary

{md_table(_fmt(rows[main_cols]))}

## Robustness (split sample and recent period)

{md_table(_fmt(rows[robust_cols]))}

## Stress episodes

{md_table(_fmt(rows[epi_cols]))}

![Baseline vs default](hedges_equity.png)
![April 2025](hedges_apr2025.png)

## Why the term-structure filter caught April 2025

VIX/VIX3M above 1 means the market prices more volatility over the next month than over the next three, which is the typical shape just before
and during sell-offs. It was already about 1.0 on 1-2 April 2025, before the 3-4 April crash:

{md_table(ts_tbl)}

## Notes

* Wings (buying a further out-of-the-money put, turning each sale into a put spread) and tail puts are priced with the skew calibrated on the
  SPXW chain, which gets much steeper further out. That is why they cost 0.5-1.6% a year, more than the strategy earns, so they are not cheap here.
* Stop-losses are executed at the day's close at model value plus {2}x the normal execution cost. A real stop in a fast market can fill worse,
  and a gap through the strike overnight is not protected. The row with 3x cost shows the sensitivity.
* The thresholds (0.95, 3x) were chosen after looking at this history. The split-sample columns show the effect holds in both halves, but
  treat the exact numbers as in-sample.
"""
    (OUT / "hedges.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
