"""Run the NDX short-put ladder backtest and write the risk report.

Usage:
    export VOLVUE_API_KEY=...
    python -m short_put.run [--start 2005-01-03] [--refresh] [--venue ibkr_qqq] [--nav 1000000]

--venue picks the execution/cash model for the headline results
(generic | ibkr_qqq | ibkr_ndxp | gs_ndxp | gs_qqq); --nav sizes the
next-day orders in contracts.

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
from .costs import IBKR_CASH_BALANCE, VENUES, params_for  # noqa: E402
from .strategy import Params, next_day_signal, run_backtest  # noqa: E402

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
    ax.plot(ex_nav.index, ex_nav, color=C2, label="Option overlay only (excluding interest)")
    ax.plot(cash_nav.index, cash_nav, color=C3, label="Interest only (money market + collateral)")
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

    # 6) Size cut and capital usage (two panels, one axis each)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    a1.plot(d.index, d["size_mult"].rolling(5).mean(), color=C1, linewidth=1)
    a1.set_ylim(0, 1.05)
    a1.set_title("Low-VXN size multiplier (5d avg; 1.0 = full size)")
    a2.plot(d.index, d["unused_cash"].rolling(5).mean() * 100, color=C3, linewidth=1)
    a2.set_title("Unused capital earning the money-market rate (% of NAV, 5d avg)")
    fig.tight_layout(); fig.savefig(OUT / "size_cut_cash.png", dpi=140); plt.close(fig)

    # 7) Exposure: $delta and $gamma per 1% move
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
    ap.add_argument("--venue", default="ibkr_qqq", choices=["generic", *VENUES])
    ap.add_argument("--nav", type=float, default=1_000_000.0)
    ap.add_argument("--recent-start", default="2022-05-02", help="start of the 'current market structure' window")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    df = load_all(refresh=args.refresh)
    base = Params() if args.venue == "generic" else params_for(args.venue)
    venue_label = "generic cost model (5% of premium, min 0.10 pt)" if base.venue is None else base.venue.name
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
        ("Base: 2.5x, skew 0.12, 1x, cut 15-20", base),
        ("No low-VXN size cut", replace(base, size_cut=False)),
        ("Size cut ramp 14-18", replace(base, cut_vxn_low=14.0, cut_vxn_full=18.0)),
        ("Size cut floor 0 (stop below VXN 15)", replace(base, cut_floor=0.0)),
        ("Margin collateral earns nothing", replace(base, collateral_rate_mult=0.0)),
        ("No interest at all (pure overlay)", replace(base, include_cash=False)),
        ("Multiplier 2.0x", replace(base, sigma_mult=2.0)),
        ("Multiplier 3.0x", replace(base, sigma_mult=3.0)),
        ("No skew (flat ATM vol)", replace(base, skew_beta=0.0)),
        ("Steeper skew 0.20", replace(base, skew_beta=0.20)),
        ("Double t-costs", replace(base, tc_pct_premium=0.10, tc_min_points=0.20) if base.venue is None else
         replace(base, venue=replace(base.venue, pay_frac=min(1.0, 2 * base.venue.pay_frac),
                                     fee_per_contract=2 * base.venue.fee_per_contract))),
        ("Sell on the bid (pay full half-spread)", base if base.venue is None else
         replace(base, venue=replace(base.venue, pay_frac=1.0))),
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

    signal = next_day_signal(df, base, nav=args.nav)

    # ---------------- execution venues
    venue_cases = [("Generic model (old: 5% of premium)", Params())]
    venue_cases += [(v.name, params_for(k)) for k, v in VENUES.items()]
    venue_cases += [("IBKR private - QQQ, cash left at IBKR (BM - 0.5%)", params_for("ibkr_qqq", **IBKR_CASH_BALANCE))]
    vrows = []
    for name, p in venue_cases:
        row = {"Setup": name}
        for label, st in (("full", args.start), ("recent", args.recent_start)):
            r = res if (p == base and st == args.start) else run_backtest(df, p, start=st)
            rr = r.daily["ret"]
            s_ = risk.perf_stats(rr, rf)
            e_ = risk.perf_stats(rr - r.daily["interest"], None)
            t = r.trades
            if label == "full":
                row.update({"CAGR": pct(s_["CAGR"]), "Overlay CAGR": pct(e_["CAGR"]), "Sharpe": f"{s_['Sharpe (vs T-bill)']:.2f}"})
            else:
                cost_share = (t["price_mid"] - t["fill"]).sum() / t["price_mid"].sum()
                row.update({f"CAGR since {st[:4]}": pct(s_["CAGR"]), f"Overlay since {st[:4]}": pct(e_["CAGR"]),
                            f"Sharpe since {st[:4]}": f"{s_['Sharpe (vs T-bill)']:.2f}",
                            f"Cost % of premium since {st[:4]}": pct(cost_share, 1)})
        vrows.append(row)
        print(f"  ran venue {name}")
    venue_tbl = pd.DataFrame(vrows).set_index("Setup")

    spot_now = df["close"].iloc[-1]
    size_rows = []
    for k, v in VENUES.items():
        cn = v.contract_notional(spot_now)
        per_tranche_full = base.leverage / base.tranche_divisor
        size_rows.append({
            "Instrument": f"{v.instrument} ({k.split('_')[0].upper()})",
            "Contract notional": f"${cn:,.0f}",
            "Min NAV, 1 contract per tranche at full size": f"${cn / per_tranche_full:,.0f}",
            "Min NAV, 1 contract at the 0.25x size-cut floor": f"${cn / (per_tranche_full * base.cut_floor):,.0f}",
        })
    size_tbl = pd.DataFrame(size_rows).drop_duplicates("Instrument").set_index("Instrument")

    # market check of the premium/spread assumptions at tomorrow's strikes
    try:
        from .liquidity import load_chain, quotes_near
        ndx_ch, qqq_ch = load_chain("_NDX"), load_chain("QQQ")
        ratio = qqq_ch["spot"].iloc[0] / ndx_ch["spot"].iloc[0]
        chk = []
        for _, sgl in next_day_signal(df, Params()).iterrows():
            n_ = quotes_near(ndx_ch, "NDXP", sgl["expiry"], sgl["strike"], width=0)
            q_ = quotes_near(qqq_ch, "QQQ", sgl["expiry"], np.floor(sgl["strike"] * ratio), width=0)
            if n_.empty or q_.empty:
                continue
            n_, q_ = n_.iloc[0], q_.iloc[0]
            chk.append({
                "Expiry": sgl["expiry"], "Model premium (NDX pts)": f"{sgl['est_premium_pts']:.2f}",
                "Model cost, generic 5%": f"{max(0.05 * sgl['est_premium_pts'], 0.10):.2f}",
                "NDXP strike": f"{n_['strike']:.0f}",
                "NDXP bid / ask": f"{n_['bid']:.2f} / {n_['ask']:.2f}",
                "NDXP half-spread % mid": pct(n_["half_pct_mid"], 0),
                "QQQ strike": f"{q_['strike']:.0f}",
                "QQQ bid / ask": f"{q_['bid']:.2f} / {q_['ask']:.2f}",
                "QQQ mid in NDX pts": f"{q_['mid'] / ratio:.2f}",
                "QQQ half-spread % mid": pct(q_["half_pct_mid"], 0),
            })
        mkt_tbl = pd.DataFrame(chk).set_index("Expiry")
        mkt_asof = str(ndx_ch["asof"].iloc[0])
    except Exception as exc:  # network / format issues should not kill the report
        mkt_tbl, mkt_asof = pd.DataFrame({"note": [f"Cboe chain unavailable: {exc}"]}), "n/a"

    # ---------------- size cut & cash usage
    d_reg = d.assign(regime=d["vxn"].shift(1).apply(risk.regime_of))
    cut_tbl = d_reg.groupby("regime").agg(
        days=("ret", "size"), size_mult=("size_mult", "mean"),
        margin=("margin_used", "mean"), unused=("unused_cash", "mean"),
        mm=("mm_interest", "mean"), ret=("ret", "mean"),
    ).reindex([n for _, _, n in risk.VXN_REGIMES]).dropna(how="all")
    cut_tbl = pd.DataFrame({
        "% of days": (cut_tbl["days"] / len(d)).map(pct),
        "Avg size multiplier": cut_tbl["size_mult"].map(lambda v: f"{v:.2f}"),
        "Margin used (% NAV)": cut_tbl["margin"].map(lambda v: pct(v, 1)),
        "Unused in money market (% NAV)": cut_tbl["unused"].map(lambda v: pct(v, 1)),
        "MM interest (ann.)": (cut_tbl["mm"] * 252).map(pct),
        "Total return (ann., arith.)": (cut_tbl["ret"] * 252).map(pct),
    })
    cut_tbl.index.name = "VXN regime"
    yrs = len(d) / 252
    cash_tbl = pd.Series({
        "Money-market interest on unused capital (ann.)": pct(d["mm_interest"].sum() / yrs),
        "Interest on margin collateral (ann.)": pct(d["coll_interest"].sum() / yrs),
        "Option overlay P&L (ann., arith.)": pct(ex_ret.sum() / yrs),
        "Avg margin used (% NAV)": pct(d["margin_used"].mean(), 1),
        "Peak margin used (% NAV)": pct(d["margin_used"].max(), 1),
        "Avg unused capital in money market (% NAV)": pct(d["unused_cash"].mean(), 1),
        "Avg size multiplier": f"{d['size_mult'].mean():.2f}",
        "% of days with a size cut": pct((d["size_mult"] < 1).mean(), 1),
    }, name="Value").to_frame()
    cash_tbl.index.name = "Return source / capital usage"

    # ---------------- outputs
    charts(res, cash_ret, ex_ret, df)
    d.to_csv(OUT / "daily.csv")
    res.trades.to_csv(OUT / "trades.csv", index=False)
    signal.to_csv(OUT / "next_day_signal.csv", index=False)

    proxy_days = (df.loc[args.start:, "vxn_src"] != "CBOE").sum()
    sig_cols = ["expiry_day", "bdays", "adj_vxn_pct", "adj_vol_pct", "target_moneyness_pct",
                "strike", "model_iv_pct", "est_premium_pts", "est_premium_bps", "delta",
                "size_mult", "notional_pct_nav"]
    sig_cols += [c for c in ("instrument", "instrument_strike", "contracts", "est_cost_pts_ndx") if c in signal]
    sig_tbl = signal.set_index("expiry")[sig_cols]

    report = f"""# NDX short-put ladder: systematic 1-5 day put selling

Backtest {d.index[0].date()} to {d.index[-1].date()} ({len(d):,} trading days, {len(res.trades):,} puts sold).
Data: VolVue API (NDX implied vols), CBOE VXN, Yahoo (NDX OHLC, 13w T-bill), Cboe delayed option chains (spread calibration).
**Headline execution and cash model: {venue_label}.**

## Rules

* Every trading day, sell NDX puts on the **three nearest Monday / Wednesday / Friday expiries** (each 1-5 business days out).
* **Adjusted VXN** = VXN(prev close) x sqrt(bdays / 252): VXN rescaled from 30-day to the option's own tenor.
* **Adj. vol %** = {base.sigma_mult} x Adjusted VXN; **target moneyness** = 100% - Adj. vol %; strike rounded down to a {base.strike_step:.0f}-pt grid.
  When VXN rises, strikes automatically move further out-of-the-money (the volatility-regime adjustment).
* Executed at the **full-day TWAP**, approximated as Black-Scholes at spot (O+H+L+C)/4 and the average of the previous and current day's
  VolVue 10-day ATM put IV, with a parametric put skew (IV x (1 + {base.skew_beta} x SDs OTM), cap {base.skew_cap}x).
  Costs follow the venue model below (quoted half-spread fitted on Cboe chains x share paid, plus per-contract fees).
* Held to expiry, cash-settled at the close. Each (day, expiry) tranche sells notional = NAV x {base.leverage} x size multiplier / {base.tranche_divisor:.0f},
  so at full size about {base.leverage:.0%} of NAV is outstanding on average.
* **Low-VXN size cut:** the size multiplier is {base.cut_floor} when VXN(prev close) <= {base.cut_vxn_low:.0f}, 1.0 when VXN >= {base.cut_vxn_full:.0f}, linear in between.
  At low VXN the strikes are close to spot and the premium barely covers costs, so exposure is cut there.
* **Cash:** margin is modelled with the CBOE short index put rule (option value + max({base.margin_pct_spot:.0%} x S - OTM amount, {base.margin_min_pct_strike:.0%} x K)).
  Margin collateral earns {base.collateral_rate_mult:.0%} x (T-bill - {base.collateral_fee * 1e4:.0f} bp). **Unused capital earns the money-market rate** (13w T-bill - {base.mm_fee * 1e4:.0f} bp).

## Headline risk statistics

{md_table(stats)}

![Equity curve](equity_curve.png)
![Drawdown](drawdown.png)

## Low-VXN size cut and money-market cash

{md_table(cash_tbl)}

{md_table(cut_tbl)}

![Size cut and cash](size_cut_cash.png)

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

## Execution venues: are the bid/ask assumptions realistic?

**Market check at tomorrow's strikes** (Cboe end-of-day quotes, {mkt_asof}):

{md_table(mkt_tbl)}

End-of-day quotes are wider than during the session, especially for NDXP, which quotes until 16:15 ET, so these spreads are an upper bound for a TWAP.
The quotes are from the previous close, so they have one more session to run than a trade placed tomorrow midday. That is why "QQQ mid in NDX pts"
tends to exceed "Model premium". On the 2026-09-22 calibration day, repricing the model on the same horizon put it 23% above the market for the
nearest expiry and 16-89% below for the other two. Market IV about 3 SDs out-of-the-money was 1.3-1.4x ATM, versus 1.36x in the model. So the premium
model looks roughly right to conservative, while real quoted spreads are wider than the old generic 5% rule.

**Venue cost and cash models** (per option: share of the fitted quoted half-spread paid + per-contract fees; cash in T-bills unless stated):

| Setup | Instrument | Share of half-spread paid | Fees per contract | Cash drag vs T-bill |
|---|---|---|---|---|
""" + "\n".join(
        f"| {v.name} | {v.instrument} | {v.pay_frac:.0%} | ${v.fee_per_contract:.2f} | {params_for(k).mm_fee * 1e4:.0f} bp |"
        for k, v in VENUES.items()) + f"""

{md_table(venue_tbl)}

The full-history columns apply today's cost structure (spreads in bps of spot, today's fees) to the whole period. The columns from 2022 onward,
when daily NDXP expiries existed, are the better guide to live trading costs. Sharpe differences across setups also reflect the cash terms:
the venue setups hold margin in T-bills, while the generic model's margin earns nothing.

**Minimum account size** (tranche = {base.leverage / base.tranche_divisor:.1%} of NAV at full size; contracts are whole numbers):

{md_table(size_tbl)}

## Sensitivity to design choices and model assumptions

{md_table(sens)}

## Next trading day: trades to place

Based on the {d.index[-1].date()} close (NDX {df['close'].iloc[-1]:,.2f}, VXN {df['vxn'].iloc[-1]:.2f}, NDX 10d ATM put IV {df['atm_vol'].iloc[-1]:.2f}),
sized for NAV ${args.nav:,.0f} with {venue_label}.
The strike is computed off the last close; recompute against the live TWAP level when executing.

{md_table(sig_tbl)}

## Caveats

* **Option prices are modelled, not traded quotes.** VolVue provides constant-maturity ATM IV but no strike-level quotes, so OTM premia rely on the
  parametric skew. The sensitivity table shows how results move with the skew assumption, so treat absolute premia as approximate.
* NDX Monday/Wednesday expiries were only listed from the late 2010s (daily NDXP from 2022). Earlier years assume those expiries existed.
* VXN before the CBOE CSV history (and on {proxy_days} days in this sample) uses VolVue NDX 30d mean IV x {df.attrs.get('vxn_proxy_ratio', float('nan')):.3f}.
  {df.attrs.get('atm_vol_cleaned_days', 0)} bad VolVue ATM IV prints were replaced by VXN x rolling median ratio.
* TWAP uses (O+H+L+C)/4 and interpolated IV. Intraday path, early-close days and settlement-price nuances are ignored.
* Margin uses the CBOE minimum for short broad-index puts. A broker may charge more (house margin), which leaves less capital in the money market.
  The money-market rate is proxied by the 13w T-bill (^IRX) minus a fee.
* The size-cut thresholds (15/20) were chosen after looking at P&L by VXN bucket, so they are in-sample. The sensitivity rows show that nearby choices behave similarly.
"""
    (OUT / "report.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
