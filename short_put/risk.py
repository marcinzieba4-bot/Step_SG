"""Risk / performance statistics for the short-put ladder."""
from __future__ import annotations

import numpy as np
import pandas as pd

TD = 252

# Vol-index regimes: VIX for SPX, VXN for NDX (VXN runs roughly 1.2-1.3x VIX)
REGIMES = {
    "VIX": [(0, 14, "Low (<14)"), (14, 20, "Normal (14-20)"), (20, 28, "Elevated (20-28)"), (28, 1e9, "Crisis (>28)")],
    "VXN": [(0, 18, "Low (<18)"), (18, 25, "Normal (18-25)"), (25, 35, "Elevated (25-35)"), (35, 1e9, "Crisis (>35)")],
}

STRESS_WINDOWS = {
    "GFC (Sep-Nov 2008)": ("2008-09-01", "2008-11-30"),
    "Flash crash (May 2010)": ("2010-05-01", "2010-05-31"),
    "US downgrade (Aug 2011)": ("2011-07-25", "2011-08-31"),
    "China deval. (Aug 2015)": ("2015-08-17", "2015-08-31"),
    "Volmageddon (Feb 2018)": ("2018-01-29", "2018-02-09"),
    "Q4 2018 selloff": ("2018-10-01", "2018-12-24"),
    "COVID crash (Feb-Mar 2020)": ("2020-02-19", "2020-03-23"),
    "2022 bear market": ("2022-01-03", "2022-10-14"),
    "Yen carry unwind (Aug 2024)": ("2024-07-10", "2024-08-07"),
    "DeepSeek (Jan 2025)": ("2025-01-24", "2025-01-31"),
    "Tariff shock (Apr 2025)": ("2025-03-25", "2025-04-08"),
}


def regime_of(vol: float, index: str = "VIX") -> str:
    for lo, hi, name in REGIMES[index]:
        if lo <= vol < hi:
            return name
    return "n/a"


def max_drawdown(nav: pd.Series):
    peak = nav.cummax()
    dd = nav / peak - 1
    trough = dd.idxmin()
    start = nav.loc[:trough].idxmax()
    rec = nav.loc[trough:]
    rec = rec[rec >= nav.loc[start]]
    end = rec.index[0] if len(rec) else pd.NaT
    return dd.min(), start, trough, end, dd


def perf_stats(ret: pd.Series, rf: pd.Series | None = None, label: str = "") -> dict:
    ret = ret.dropna()
    nav = (1 + ret).cumprod()
    years = len(ret) / TD
    cagr = nav.iloc[-1] ** (1 / years) - 1
    vol = ret.std() * np.sqrt(TD)
    ex = ret - (rf.reindex(ret.index).fillna(0) / TD if rf is not None else 0)
    sharpe = ex.mean() / ret.std() * np.sqrt(TD) if ret.std() > 0 else np.nan
    downside = np.sqrt((np.minimum(ret, 0) ** 2).mean()) * np.sqrt(TD)
    sortino = ex.mean() * TD / downside if downside > 0 else np.nan
    mdd, dd_start, dd_trough, dd_end, dd = max_drawdown(pd.concat([pd.Series([1.0]), nav]).set_axis(
        [ret.index[0] - pd.Timedelta(days=1)] + list(ret.index)))
    q05, q01 = ret.quantile(0.05), ret.quantile(0.01)
    wk = (1 + ret).resample("W-FRI").prod() - 1
    mo = (1 + ret).resample("ME").prod() - 1
    # longest time under water (calendar days)
    under = dd < 0
    grp = (~under).cumsum()
    longest = under.groupby(grp).apply(lambda s: (s.index[-1] - s.index[0]).days if s.any() else 0).max()
    return {
        "Label": label,
        "Start": ret.index[0].date(),
        "End": ret.index[-1].date(),
        "CAGR": cagr,
        "Ann. volatility": vol,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Max drawdown": mdd,
        "Max DD peak": dd_start.date(),
        "Max DD trough": dd_trough.date(),
        "Max DD recovered": dd_end.date() if pd.notna(dd_end) else "not yet",
        "Longest underwater (days)": int(longest),
        "Calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "Daily skew": ret.skew(),
        "Daily excess kurtosis": ret.kurt(),
        "Daily VaR 95%": q05,
        "Daily CVaR 95%": ret[ret <= q05].mean(),
        "Daily VaR 99%": q01,
        "Daily CVaR 99%": ret[ret <= q01].mean(),
        "Worst day": ret.min(),
        "Worst day date": ret.idxmin().date(),
        "Worst week": wk.min(),
        "Worst month": mo.min(),
        "Best month": mo.max(),
        "% positive days": (ret > 0).mean(),
        "% positive months": (mo > 0).mean(),
    }


def trade_stats(trades: pd.DataFrame) -> dict:
    s = trades.dropna(subset=["payoff"])
    itm = s["payoff"] > 0
    losers = s[s["pnl_pts"] < 0]
    winners = s[s["pnl_pts"] >= 0]
    return {
        "Puts sold": len(trades),
        "Puts settled": len(s),
        "Avg bdays to expiry": trades["bdays"].mean(),
        "Avg target moneyness": trades["moneyness"].mean(),
        "Min target moneyness": trades["moneyness"].min(),
        "Avg Adj. vol %": trades["adj_vol_pct"].mean() / 100,
        "Avg premium (bps of spot)": (trades["fill"] / trades["spot_twap"]).mean() * 1e4,
        "Median premium (index pts)": trades["fill"].median(),
        "% expiring ITM or stopped": itm.mean(),
        "% stopped out (bought back)": s["stopped"].mean() if "stopped" in s else 0.0,
        "% losing trades (payoff > premium)": (s["pnl_pts"] < 0).mean(),
        "Avg loss / avg win (premium units)": (
            losers["pnl_pts"].mean() / winners["pnl_pts"].mean() if len(losers) and len(winners) else np.nan),
        "Worst trade (x premium)": (s["pnl_pts"] / s["fill"]).min(),
        "Worst breach (spot vs strike)": (s["strike"] / s["spot_twap"] - 1).min() if len(s) else np.nan,
    }


def regime_stats(daily: pd.DataFrame, trades: pd.DataFrame, index: str = "VIX") -> pd.DataFrame:
    d = daily.copy()
    d["regime"] = d["vol_index"].shift(1).apply(regime_of, index=index)  # regime known at trade time
    t = trades.dropna(subset=["payoff"]).copy()
    t["regime"] = t["vol_prev"].apply(regime_of, index=index)
    rows = []
    for _, _, name in REGIMES[index]:
        r = d.loc[d["regime"] == name, "ret"]
        tt = t[t["regime"] == name]
        if not len(r):
            continue
        rows.append({
            "Regime": name,
            "% of days": len(r) / len(d),
            "Ann. return (arith.)": r.mean() * TD,
            "Ann. vol": r.std() * np.sqrt(TD),
            "Sharpe (raw)": r.mean() / r.std() * np.sqrt(TD) if r.std() > 0 else np.nan,
            "Worst day": r.min(),
            "Avg moneyness": tt["moneyness"].mean(),
            "Avg premium (bps)": (tt["fill"] / tt["spot_twap"]).mean() * 1e4,
            "% ITM": (tt["payoff"] > 0).mean(),
        })
    return pd.DataFrame(rows).set_index("Regime")


def stress_table(ret: pd.Series, spot: pd.Series) -> pd.DataFrame:
    rows = []
    for name, (a, b) in STRESS_WINDOWS.items():
        r = ret.loc[a:b]
        if len(r) < 2:
            continue
        nav = (1 + r).cumprod()
        s = spot.loc[a:b]
        rows.append({
            "Episode": name,
            "Strategy": nav.iloc[-1] - 1,
            "Strategy max DD": (nav / nav.cummax().clip(lower=1) - 1).min(),
            "Worst day": r.min(),
            "Index": s.iloc[-1] / spot.loc[:a].iloc[-2] - 1 if len(spot.loc[:a]) > 1 else np.nan,
        })
    return pd.DataFrame(rows).set_index("Episode")


def calendar_returns(ret: pd.Series) -> pd.Series:
    return (1 + ret).groupby(ret.index.year).prod() - 1


def market_beta(ret: pd.Series, spot: pd.Series) -> dict:
    idx_ret = spot.pct_change().reindex(ret.index)
    ok = ret.notna() & idx_ret.notna()
    x, y = idx_ret[ok], ret[ok]
    beta = np.cov(y, x)[0, 1] / x.var()
    down = x < 0
    beta_dn = np.cov(y[down], x[down])[0, 1] / x[down].var()
    beta_up = np.cov(y[~down], x[~down])[0, 1] / x[~down].var()
    big = x < x.quantile(0.02)
    return {
        "Beta to index": beta,
        "Correlation to index": np.corrcoef(y, x)[0, 1],
        "Down-market beta": beta_dn,
        "Up-market beta": beta_up,
        "Avg return on the index's worst-2% days": y[big].mean(),
        "Avg index return on those days": x[big].mean(),
    }
