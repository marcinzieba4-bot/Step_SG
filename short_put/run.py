"""Run the short-put ladder backtest and write the risk report.

Usage:
    export VOLVUE_API_KEY=...
    python -m short_put.run [--underlying SPX] [--venue ibkr_xsp] [--venue-inst gs_spy]
                            [--nav 1000000] [--nav-inst 100000000] [--start 2005-01-03] [--refresh]

Default: S&P 500 puts, strikes from VIX, executed as XSP at IBKR (private account)
and SPY at Goldman Sachs (institutional). Returns are option P&L only: no
money-market or collateral interest is credited.

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
from .costs import VENUES, params_for  # noqa: E402
from .data import load_all  # noqa: E402
from .strategy import default_params, next_day_signal, run_backtest  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "output"

# Reference palette (dataviz skill), light mode
C1, C2, C3, C8 = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _style():
    plt.rcParams.update({
        "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb",
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
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


def short_name(key: str) -> str:
    v = VENUES[key]
    return f"{key.split('_')[0].upper()} {v.instrument}"


# --------------------------------------------------------------------------- charts
def charts(results: dict, df: pd.DataFrame, vol_name: str, und_name: str):
    """results: {label: Result}; the first entry is the primary venue."""
    _style()
    colors = [C1, C2, C3]
    primary = next(iter(results.values()))
    d = primary.daily

    # 1) Growth of $1, option P&L only
    fig, ax = plt.subplots(figsize=(10, 4.2))
    for (label, r), c in zip(results.items(), colors):
        nav = (1 + r.daily["ret"]).cumprod()
        ax.plot(nav.index, nav, color=c, label=label)
        ax.annotate(f"{nav.iloc[-1]:.2f}x", (nav.index[-1], nav.iloc[-1]), xytext=(4, 0),
                    textcoords="offset points", va="center", color=INK2, fontsize=8.5)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.2f}x"))
    ax.set_title(f"Growth of $1, {und_name} 1-5 day short-put ladder (option P&L only, no interest)")
    ax.legend(loc="upper left")
    fig.tight_layout(); fig.savefig(OUT / "equity_curve.png", dpi=140); plt.close(fig)

    # 2) Drawdown
    fig, ax = plt.subplots(figsize=(10, 3.2))
    for (label, r), c in zip(results.items(), colors):
        nav = (1 + r.daily["ret"]).cumprod()
        dd = nav / nav.cummax() - 1
        ax.plot(dd.index, dd * 100, color=c, linewidth=1, label=f"{label} (max {dd.min() * 100:.1f}%)")
    ax.set_ylabel("Drawdown (%)")
    ax.set_title("Strategy drawdown")
    ax.legend(loc="lower left")
    fig.tight_layout(); fig.savefig(OUT / "drawdown.png", dpi=140); plt.close(fig)

    # 3) Strike selection: vol index and target moneyness (two panels, one axis each)
    t = primary.trades.copy()
    t["trade_date"] = pd.to_datetime(t["trade_date"])
    m = t.groupby("trade_date")["moneyness"].mean().rolling(5).mean()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(d.index, d["vol_index"], color=C1, linewidth=1)
    a1.set_title(f"{vol_name} (strike driver); dotted lines = regime boundaries")
    a1.set_ylabel(vol_name)
    for lo, _, _ in risk.REGIMES[vol_name][1:]:
        a1.axhline(lo, color=INK2, linewidth=0.6, linestyle=":")
    a2.plot(m.index, m * 100, color=C2, linewidth=1)
    a2.set_title("Average target moneyness of puts sold (5d avg)")
    a2.set_ylabel("Strike / spot (%)")
    fig.tight_layout(); fig.savefig(OUT / "vol_moneyness.png", dpi=140); plt.close(fig)

    # 4) Rolling 1y vol and 1y return
    ret = d["ret"]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(ret.index, ret.rolling(252).std() * np.sqrt(252) * 100, color=C1, linewidth=1)
    a1.set_title("Rolling 1-year volatility (%)")
    roll = (1 + ret).rolling(252).apply(np.prod, raw=True) - 1
    a2.plot(roll.index, roll * 100, color=C1, linewidth=1)
    a2.axhline(0, color=INK2, linewidth=0.8)
    a2.set_title("Rolling 1-year return (%)")
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

    # 6) Size cut and margin usage (two panels, one axis each)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(d.index, d["size_mult"].rolling(5).mean(), color=C1, linewidth=1)
    a1.set_ylim(0, 1.05)
    a1.set_title(f"Size multiplier: low-{vol_name} cut x term-structure filter (5d avg; 1.0 = full size)")
    a2.plot(d.index, d["margin_used"].rolling(5).mean() * 100, color=C3, linewidth=1)
    a2.set_title("Margin used (CBOE index rule, % of NAV, 5d avg)")
    fig.tight_layout(); fig.savefig(OUT / "size_cut_margin.png", dpi=140); plt.close(fig)

    # 7) Exposure: delta and gamma
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(d.index, d["delta"] * 100, color=C1, linewidth=0.8)
    a1.set_title("Net delta (equity-equivalent exposure, % of NAV)")
    a2.plot(d.index, d["gamma_1pct"] * 100, color=C2, linewidth=0.8)
    a2.set_title("Gamma: change in delta (% NAV) for a 1% index move")
    fig.tight_layout(); fig.savefig(OUT / "greeks.png", dpi=140); plt.close(fig)


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", default="SPX", choices=["SPX", "NDX"])
    ap.add_argument("--venue", default="ibkr_xsp", choices=list(VENUES), help="private-account route")
    ap.add_argument("--venue-inst", default="gs_spy", choices=list(VENUES), help="institutional route")
    ap.add_argument("--nav", type=float, default=1_000_000.0, help="private account NAV (USD)")
    ap.add_argument("--nav-inst", type=float, default=100_000_000.0, help="institutional NAV (USD)")
    ap.add_argument("--start", default="2005-01-03")
    ap.add_argument("--recent-start", default="2022-05-02", help="start of the 'current market structure' window")
    ap.add_argument("--no-compare", action="store_true", help="skip the S&P vs Nasdaq comparison")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    und = args.underlying
    df = load_all(und, refresh=args.refresh)
    vol_name, und_name = df.attrs["vol_index_name"], df.attrs["underlying_name"]
    keys = [args.venue, args.venue_inst]
    assert all(VENUES[k].underlying == und for k in keys), "venues must match --underlying"
    P = {k: params_for(k) for k in keys}
    base = P[args.venue]
    R = {k: run_backtest(df, P[k], start=args.start) for k in keys}
    res = R[args.venue]
    d = res.daily
    rf = df["rf"].reindex(d.index)
    idx_ret = df["close"].pct_change().reindex(d.index)
    labels = {k: short_name(k) for k in keys}

    # ---------------- headline statistics (option P&L = excess return; index vs T-bill)
    cols = {labels[k]: fmt_stats(risk.perf_stats(R[k].daily["ret"], None)) for k in keys}
    cols[f"{und_name} (price)"] = fmt_stats(risk.perf_stats(idx_ret, rf))
    stats = pd.DataFrame(cols).drop(index="Label")
    stats.index.name = "Metric"
    tstats = pd.DataFrame({labels[k]: fmt_stats(risk.trade_stats(R[k].trades)) for k in keys})
    tstats.index.name = "Trade statistic"
    beta = pd.DataFrame({labels[k]: fmt_stats(risk.market_beta(R[k].daily["ret"], df["close"])) for k in keys})
    beta.index.name = "Market sensitivity"

    reg = risk.regime_stats(d, res.trades, index=vol_name)
    reg_fmt = reg.copy()
    for c in reg_fmt.columns:
        reg_fmt[c] = reg[c].map(lambda v, c=c: f"{v:.2f}" if ("Sharpe" in c or "bps" in c) else pct(v))
    reg_fmt.index.name = f"{vol_name} regime"
    stress = pd.concat({labels[k]: risk.stress_table(R[k].daily["ret"], df["close"])["Strategy"] for k in keys}, axis=1)
    stress[f"{und_name}"] = risk.stress_table(d["ret"], df["close"])["Index"]
    stress[f"Worst day ({labels[args.venue]})"] = risk.stress_table(d["ret"], df["close"])["Worst day"]
    stress = stress.map(pct)
    cal = pd.DataFrame({labels[k]: risk.calendar_returns(R[k].daily["ret"]) for k in keys})
    cal[und_name] = risk.calendar_returns(idx_ret.fillna(0))
    cal = cal.map(pct)
    cal.index.name = "Year"

    last = d.iloc[-1]
    exposure = pd.Series({
        "Open put tranches": int(last["n_open"]),
        "Gross put notional / NAV": pct(last["gross_notional"], 1),
        "Margin used / NAV (CBOE index rule)": pct(last["margin_used"], 1),
        "Net delta (equity-equivalent % NAV)": pct(last["delta"], 2),
        "Gamma (Δdelta per 1% move, % NAV)": pct(last["gamma_1pct"], 2),
        "Vega (NAV per +1 vol pt)": pct(last["vega_1pt"], 3),
        "Avg gross notional over history": pct(d["gross_notional"].mean(), 1),
        "Max net delta over history": pct(d["delta"].max(), 1),
    }, name="Value").to_frame()
    exposure.index.name = f"Exposure as of {d.index[-1].date()} ({labels[args.venue]})"

    # ---------------- managed vs unmanaged (primary venue)
    unmanaged = run_backtest(df, replace(base, ts_threshold=0.0, stop_mult=0.0), start=args.start)
    mgmt_cmp = pd.DataFrame({
        "Managed (default)": fmt_stats(risk.perf_stats(d["ret"], None)),
        "Unmanaged": fmt_stats(risk.perf_stats(unmanaged.daily["ret"], None)),
        f"Managed, since {args.recent_start[:4]}": fmt_stats(risk.perf_stats(d["ret"].loc[args.recent_start:], None)),
        f"Unmanaged, since {args.recent_start[:4]}": fmt_stats(risk.perf_stats(unmanaged.daily["ret"].loc[args.recent_start:], None)),
    }).loc[["CAGR", "Ann. volatility", "Sharpe", "Max drawdown", "Worst day", "Worst month"]]
    mgmt_cmp.index.name = labels[args.venue]
    apr = {n: (1 + r.daily["ret"].loc["2025-03-25":"2025-04-08"]).prod() - 1 for n, r in (("Managed", res), ("Unmanaged", unmanaged))}

    # ---------------- low-vol size cut by regime
    d_reg = d.assign(regime=d["vol_index"].shift(1).apply(risk.regime_of, index=vol_name))
    cut = d_reg.groupby("regime").agg(
        days=("ret", "size"), size_mult=("size_mult", "mean"), margin=("margin_used", "mean"), ret=("ret", "mean"),
    ).reindex([n for _, _, n in risk.REGIMES[vol_name]]).dropna(how="all")
    cut_tbl = pd.DataFrame({
        "% of days": (cut["days"] / len(d)).map(pct),
        "Avg size multiplier": cut["size_mult"].map(lambda v: f"{v:.2f}"),
        "Margin used (% NAV)": cut["margin"].map(lambda v: pct(v, 1)),
        "Return (ann., arith.)": (cut["ret"] * 252).map(pct),
    })
    cut_tbl.index.name = f"{vol_name} regime"
    nocut = run_backtest(df, replace(base, size_cut=False), start=args.start)
    cut_cmp = pd.DataFrame({
        f"With cut ({base.cut_vol_low:.0f}-{base.cut_vol_full:.0f})": fmt_stats(risk.perf_stats(d["ret"], None)),
        "No cut": fmt_stats(risk.perf_stats(nocut.daily["ret"], None)),
        f"With cut, since {args.recent_start[:4]}": fmt_stats(risk.perf_stats(d["ret"].loc[args.recent_start:], None)),
        f"No cut, since {args.recent_start[:4]}": fmt_stats(risk.perf_stats(nocut.daily["ret"].loc[args.recent_start:], None)),
    }).loc[["CAGR", "Ann. volatility", "Sharpe", "Max drawdown", "Worst month"]]
    cut_cmp.index.name = f"{labels[args.venue]}"

    # ---------------- sensitivities (primary venue)
    variants = [
        ("Base (managed default)", base),
        ("Unmanaged (no term-structure filter, no stop-loss)", replace(base, ts_threshold=0.0, stop_mult=0.0)),
        ("No term-structure filter", replace(base, ts_threshold=0.0)),
        ("No stop-loss", replace(base, stop_mult=0.0)),
        ("Stop-loss at 5x premium", replace(base, stop_mult=5.0)),
        ("Term-structure threshold 1.00", replace(base, ts_threshold=1.0)),
        ("No low-vol size cut", replace(base, size_cut=False)),
        (f"Size cut ramp {base.cut_vol_low - 1:.0f}-{base.cut_vol_full - 1:.0f}",
         replace(base, cut_vol_low=base.cut_vol_low - 1, cut_vol_full=base.cut_vol_full - 1)),
        (f"Size cut ramp {base.cut_vol_low + 2:.0f}-{base.cut_vol_full + 3:.0f}",
         replace(base, cut_vol_low=base.cut_vol_low + 2, cut_vol_full=base.cut_vol_full + 3)),
        ("Size cut floor 0 (stop at low vol)", replace(base, cut_floor=0.0)),
        ("Multiplier 2.0x", replace(base, sigma_mult=2.0)),
        ("Multiplier 3.0x", replace(base, sigma_mult=3.0)),
        ("No skew (flat ATM vol)", replace(base, skew_beta=0.0)),
        ("Flatter skew 0.10", replace(base, skew_beta=0.10)),
        ("Steeper skew 0.20", replace(base, skew_beta=0.20)),
        ("Double t-costs", replace(base, venue=replace(base.venue, pay_frac=min(1.0, 2 * base.venue.pay_frac),
                                                        fee_per_contract=2 * base.venue.fee_per_contract))),
        ("Sell on the bid (pay full half-spread)", replace(base, venue=replace(base.venue, pay_frac=1.0))),
        ("Leverage 2x", replace(base, leverage=2.0)),
        ("Leverage 3x", replace(base, leverage=3.0)),
    ]
    sens_rows = []
    for name, p in variants:
        reuse = {"No low-vol size cut": nocut, "Unmanaged (no term-structure filter, no stop-loss)": unmanaged}
        r = res if p is base else reuse.get(name) or run_backtest(df, p, start=args.start)
        s = risk.perf_stats(r.daily["ret"], None)
        ts = risk.trade_stats(r.trades)
        sens_rows.append({
            "Variant": name, "CAGR": pct(s["CAGR"]), "Vol": pct(s["Ann. volatility"]),
            "Sharpe": f"{s['Sharpe']:.2f}", "Max DD": pct(s["Max drawdown"]), "Worst day": pct(s["Worst day"]),
            "Avg moneyness": pct(ts["Avg target moneyness"], 1), "% ITM or stopped": pct(ts["% expiring ITM or stopped"]),
        })
        print(f"  ran {name}")
    sens = pd.DataFrame(sens_rows).set_index("Variant")

    # ---------------- execution venues for this underlying
    vrows = []
    for k, v in VENUES.items():
        if v.underlying != und:
            continue
        p = P.get(k) or params_for(k)
        row = {"Setup": v.name}
        for label, st in (("full", args.start), ("recent", args.recent_start)):
            r = R[k] if (k in R and label == "full") else run_backtest(df, p, start=st)
            s_ = risk.perf_stats(r.daily["ret"], None)
            t = r.trades
            cost_share = (t["price_mid"] - t["fill"]).sum() / t["price_mid"].sum()
            yr = "" if label == "full" else f" since {st[:4]}"
            row.update({f"CAGR{yr}": pct(s_["CAGR"]), f"Sharpe{yr}": f"{s_['Sharpe']:.2f}",
                        f"Max DD{yr}": pct(s_["Max drawdown"]), f"Cost % of premium{yr}": pct(cost_share, 1)})
        vrows.append(row)
        print(f"  ran venue {v.name}")
    venue_tbl = pd.DataFrame(vrows).set_index("Setup")
    model_tbl = pd.DataFrame([{
        "Setup": v.name, "Instrument": v.instrument, "Settlement": v.settlement,
        "Quoted half-spread (bps of spot)": f"{v.hs_fixed_bps:.3f} + {v.hs_pct_mid:.3f} x premium",
        "Share of half-spread paid": pct(v.pay_frac, 0), "Fees per contract": f"${v.fee_per_contract:.2f}",
    } for v in VENUES.values() if v.underlying == und]).set_index("Setup")

    spot_now = df["close"].iloc[-1]
    per_tranche = base.leverage / base.tranche_divisor
    size_tbl = pd.DataFrame([{
        "Instrument": v.instrument,
        "Contract notional": f"${v.contract_notional(spot_now):,.0f}",
        "Min NAV, 1 contract per tranche at full size": f"${v.contract_notional(spot_now) / per_tranche:,.0f}",
        f"Min NAV, 1 contract at the {base.cut_floor}x floor": f"${v.contract_notional(spot_now) / (per_tranche * base.cut_floor):,.0f}",
    } for v in VENUES.values() if v.underlying == und]).drop_duplicates("Instrument").set_index("Instrument")

    # market check at tomorrow's strikes
    try:
        from .liquidity import market_check
        mkt_tbl, mkt_asof = market_check(next_day_signal(df, base), und, index_spot=spot_now)
    except Exception as exc:  # network / format issues should not kill the report
        mkt_tbl, mkt_asof = pd.DataFrame({"note": [f"Cboe chain unavailable: {exc}"]}), "n/a"

    # ---------------- S&P 500 vs Nasdaq-100 comparison (same rules, realistic venues)
    cmp_tbl = None
    if not args.no_compare:
        cmp_rows = []
        other = "NDX" if und == "SPX" else "SPX"
        df_other = load_all(other)
        for u, dfu, ks in ((und, df, keys), (other, df_other, [k for k, v in VENUES.items()
                                                              if v.underlying == other and k in ("ibkr_qqq", "gs_ndxp", "ibkr_xsp", "gs_spy")])):
            for k in ks:
                r = R[k] if u == und else run_backtest(dfu, params_for(k), start=args.start)
                rr = r.daily["ret"]
                s_ = risk.perf_stats(rr, None)
                s2 = risk.perf_stats(rr.loc[args.recent_start:], None)
                cmp_rows.append({
                    "Setup": f"{dfu.attrs['underlying_name']} via {VENUES[k].name}",
                    "Strike driver": dfu.attrs["vol_index_name"], "CAGR": pct(s_["CAGR"]),
                    "Vol": pct(s_["Ann. volatility"]), "Sharpe": f"{s_['Sharpe']:.2f}",
                    "Max DD": pct(s_["Max drawdown"]), "Worst day": pct(s_["Worst day"]),
                    f"CAGR since {args.recent_start[:4]}": pct(s2["CAGR"]),
                    f"Sharpe since {args.recent_start[:4]}": f"{s2['Sharpe']:.2f}",
                })
                print(f"  ran comparison {k}")
        cmp_tbl = pd.DataFrame(cmp_rows).set_index("Setup")

    # ---------------- next-day orders
    sig_cols = ["expiry_day", "bdays", "adj_vix_pct", "adj_vol_pct", "target_moneyness_pct", "strike",
                "est_premium_pts", "regime_mult", "size_mult", "notional_pct_nav", "instrument", "instrument_strike",
                "est_premium_instr", "est_cost_instr", "contracts"]
    signals = {k: next_day_signal(df, P[k], nav=nav) for k, nav in ((args.venue, args.nav), (args.venue_inst, args.nav_inst))}
    sig_tbls = {k: s.set_index("expiry")[sig_cols].rename(columns={"adj_vix_pct": f"adj_{vol_name.lower()}_pct"})
                for k, s in signals.items()}

    # ---------------- outputs
    charts({labels[k]: R[k] for k in keys}, df, vol_name, und_name)
    d.to_csv(OUT / "daily.csv")
    res.trades.to_csv(OUT / "trades.csv", index=False)
    pd.concat(signals.values()).to_csv(OUT / "next_day_signal.csv", index=False)

    proxy_days = (df.loc[args.start:, "vol_src"] != "CBOE").sum()
    V = vol_name
    report = f"""# {und_name} short-put ladder: systematic 1-5 day put selling

Backtest {d.index[0].date()} to {d.index[-1].date()} ({len(d):,} trading days, {len(res.trades):,} puts sold per route).
**Routes: {VENUES[args.venue].name} (private) and {VENUES[args.venue_inst].name} (institutional).**
**Returns are option P&L only: no money-market or collateral interest is included.** Add your cash yield on top if the account holds T-bills.
Data: VolVue API ({und} implied vols), CBOE {V}, Yahoo ({und} OHLC), Cboe delayed option chains (spread and skew calibration).

## Rules

* Every trading day, sell {und} puts on the **three nearest Monday / Wednesday / Friday expiries** (each 1-5 business days out).
* **Adjusted {V}** = {V}(prev close) x sqrt(bdays / 252): {V} rescaled from 30 days to the option's own tenor.
* **Adj. vol %** = {base.sigma_mult} x Adjusted {V}; **target moneyness** = 100% - Adj. vol %. The strike is rounded down to the instrument's grid.
  When {V} rises, strikes automatically move further out-of-the-money (the volatility-regime adjustment).
* Executed at the **full-day TWAP**, approximated as Black-Scholes at spot (O+H+L+C)/4 and the average of the previous and current day's
  VolVue 10-day ATM put IV, with a put skew of IV x (1 + {base.skew_beta} x SDs OTM), cap {base.skew_cap}x, calibrated on the {und} option chains.
  Costs per option: share of the quoted half-spread paid + per-contract fees (venue table below).
* Held to expiry, cash-settled at the close. Each (day, expiry) tranche sells notional = NAV x {base.leverage} x size multiplier / {base.tranche_divisor:.0f},
  so at full size about {base.leverage:.0%} of NAV is outstanding on average.
* **Low-{V} size cut:** the size multiplier is {base.cut_floor} when {V}(prev close) <= {base.cut_vol_low:.0f}, 1.0 when {V} >= {base.cut_vol_full:.0f}, linear in between.
* **Term-structure filter:** no new sales on a day when {V}/{V}3M at the previous close is above {base.ts_threshold:.2f} (the curve is flat or
  inverted, the usual shape before and during sell-offs).{" (Inactive: no 3-month index for " + V + ".)" if base.ts_threshold > 0 and und != "SPX" else ""}
* **Stop-loss:** at each close, buy back any short put worth {base.stop_mult:.0f}x or more its premium, at model value plus {base.stop_cost_mult:.0f}x the normal execution cost.
  (A stop multiple of 0 means off.) The study in [hedges.md](hedges.md) compares these rules with put-spread wings, tail puts, trend and shock filters.

## Headline risk statistics

Strategy Sharpe ratios are computed on option P&L, which is already an excess return. The index Sharpe is measured against T-bills.

{md_table(stats)}

![Equity curve](equity_curve.png)
![Drawdown](drawdown.png)

## Risk management: term-structure filter and stop-loss

{md_table(mgmt_cmp)}

April 2025 tariff shock (25 Mar - 8 Apr 2025): managed {pct(apr['Managed'])}, unmanaged {pct(apr['Unmanaged'])}.
Stops were triggered {d['stops'].sum() / (len(d) / 252):.0f} times a year on average, and new sales were paused on {pct((d['regime_mult'] == 0).mean(), 0)} of days.

## Low-{V} size cut

{md_table(cut_cmp)}

{md_table(cut_tbl)}

![Size cut and margin](size_cut_margin.png)

## Trade statistics

{md_table(tstats)}

## Market sensitivity and current exposure

{md_table(beta)}

{md_table(exposure)}

![Greeks](greeks.png)

## Risk by {V} regime ({labels[args.venue]}, regime at trade time)

{md_table(reg_fmt)}

![{V} and moneyness](vol_moneyness.png)

## Stress episodes

{md_table(stress)}

## Calendar-year returns

{md_table(cal)}

![Rolling](rolling.png)
![Distribution](return_distribution.png)

## Execution venues

**Market check at tomorrow's strikes** (Cboe end-of-day quotes, {mkt_asof}; mids converted to {und} points):

{md_table(mkt_tbl)}

End-of-day quotes are wider than during the session, so these spreads are an upper bound for a TWAP. The quotes also have one more session to run
than a trade placed tomorrow midday, so market mids for the longer expiries tend to exceed the model premium.

**Venue cost models** (spreads fitted on the 2026-09-22 Cboe chains around the strategy's strikes):

{md_table(model_tbl)}

{md_table(venue_tbl)}

The full-history columns apply today's cost structure to the whole period. The columns from {args.recent_start[:4]} onward are the better guide to live trading.

**Minimum account size** (tranche = {per_tranche:.1%} of NAV at full size; contracts are whole numbers):

{md_table(size_tbl)}
"""
    if cmp_tbl is not None:
        report += f"""
## S&P 500 vs Nasdaq-100 (same rules, realistic venues, no interest)

{md_table(cmp_tbl)}
"""
    report += f"""
## Sensitivity to design choices and model assumptions ({labels[args.venue]})

{md_table(sens)}

## Next trading day: orders to place

Based on the {d.index[-1].date()} close ({und} {df['close'].iloc[-1]:,.2f}, {V} {df['vol_index'].iloc[-1]:.2f}, {und} 10d ATM put IV {df['atm_vol'].iloc[-1]:.2f}).
Strikes are computed off the last close; recompute against the live TWAP level when executing. Premium and cost are per option, in instrument points.

**{VENUES[args.venue].name}, NAV ${args.nav:,.0f}:**

{md_table(sig_tbls[args.venue])}

**{VENUES[args.venue_inst].name}, NAV ${args.nav_inst:,.0f}:**

{md_table(sig_tbls[args.venue_inst])}

## Caveats

* **Option prices are modelled, not traded quotes.** VolVue provides constant-maturity ATM IV but no strike-level quotes, so OTM premia rely on the
  parametric skew, calibrated on one day's chains. The sensitivity table shows how results move with the skew assumption.
* Monday/Wednesday expiries were only listed from the mid-2010s (SPXW) and later for XSP and SPY. Earlier years assume those expiries existed.
* {V} gaps in the CBOE history ({proxy_days} days in this sample) are filled with VolVue 30d mean IV x {df.attrs.get('vol_proxy_ratio', float('nan')):.3f}.
  {df.attrs.get('atm_vol_cleaned_days', 0)} bad VolVue ATM IV prints were replaced by {V} x rolling median ratio.
* The model settles every option in cash at the close. SPY options are American-style and physically settled. Assignment delivers shares, which then
  carry overnight gap risk until sold. Close positions that are near the money before expiry-day close.
* TWAP uses (O+H+L+C)/4 and interpolated IV. Intraday path, early-close days and settlement-price nuances are ignored.
* The size-cut, term-structure (0.95) and stop-loss (3x) thresholds were chosen after looking at this history, so they are in-sample.
  hedges.md shows the effect holds in both halves of the sample (2005-15 and 2016-26).
* The put skew is calibrated on one day's SPXW chain (2026-09-22), extended further out so hedge legs are priced realistically. That
  calibration also lowered the modelled premium at the short strikes versus the earlier linear skew.
* Stop-losses fill at the close at model value plus a stressed cost; a real stop in a fast market can fill worse, and overnight gaps are not protected.
"""
    (OUT / "report.md").write_text(report)
    for old in ("size_cut_cash.png", "vxn_moneyness.png"):
        (OUT / old).unlink(missing_ok=True)
    print(report)


if __name__ == "__main__":
    main()
