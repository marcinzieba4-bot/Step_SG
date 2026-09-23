"""Systematic short-put ladder on an equity index (S&P 500 or Nasdaq-100).

The strike driver is the index's own volatility index: VIX for the S&P 500
(SPX, traded via XSP / SPY options), VXN for the Nasdaq-100 (NDX). Below,
"VIX" stands for whichever applies.

Rules
-----
Every trading day t:

1. Identify the three nearest Monday / Wednesday / Friday expiries after t
   (holiday expiries roll to the previous trading day). Each is 1-5 business
   days away, e.g. on a Tuesday: Wed (1bd), Fri (3bd), next Mon (4bd).
2. For each expiry with n business days to maturity:
       Adjusted VIX  = VIX_{t-1} * sqrt(n / 252)      (VIX rescaled to the tenor)
       Adj. vol %    = 2.5 * Adjusted VIX              (2.5 "sigma" buffer)
       Moneyness     = 100% - Adj. vol %
       Strike        = Moneyness * S_TWAP, rounded down to the strike grid
   The previous close of the vol index is used, so the strike is known before the open.
3. Sell the put at the full-day TWAP, approximated as the Black-Scholes price
   at S_TWAP = (O+H+L+C)/4 with the average of yesterday's and today's
   implied vol (VolVue 10d ATM put IV of the index plus a parametric skew),
   minus a venue-specific transaction cost (costs.py).
4. Hold to expiry, cash-settled against the closing level (PM settlement).
   Open positions are marked to model daily.

Sizing: each (day, expiry) tranche sells notional = NAV * leverage * size_mult / 9.
On average the three open expiries sum to ~9 business days, so the ladder
keeps roughly ``leverage`` x NAV of put notional outstanding.

Low-vol size cut: ``size_mult`` ramps linearly from ``cut_floor`` (VIX_{t-1} at
or below ``cut_vol_low``) to 1.0 (VIX_{t-1} at or above ``cut_vol_full``). At low
VIX the strikes sit close to spot and premia barely cover costs.

Returns are the option P&L only (``include_cash=False`` by default): no interest
is credited on cash or collateral. Margin (CBOE short index put rule: option
value + max(15% x S - OTM amount, 10% x K)) is tracked for reporting only.
Setting ``include_cash=True`` credits T-bill - ``mm_fee`` on unused capital and
``collateral_rate_mult`` x (T-bill - ``collateral_fee``) on margin.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .costs import Venue

import numpy as np
import pandas as pd
from scipy.stats import norm

TRADING_DAYS = 252
EXPIRY_WEEKDAYS = (0, 2, 4)  # Mon, Wed, Fri


@dataclass
class Params:
    underlying: str = "SPX"          # SPX (vol index VIX) or NDX (vol index VXN)
    sigma_mult: float = 2.5          # Adj. vol % = sigma_mult * Adjusted VIX
    leverage: float = 1.0            # average outstanding put notional / NAV
    skew_model: str = "linear"       # "linear": 1 + beta*z (cap) | "kink": 1 + slope*max(z - z0(T), 0)
    skew_beta: float = 0.15          # linear model: IV(z) = ATM * (1 + beta * z), z = SDs OTM
    skew_cap: float = 2.0            # max IV multiple of ATM (linear model)
    kink_slope: float = 0.28         # kink model slope per SD
    kink_z0_short: float = 1.45      # kink start for 1-5 day options
    kink_z0_month: float = 0.60      # kink start for ~1-month options
    kink_cap: float = 4.0
    div_yield: float = 0.013         # index dividend yield
    strike_step: float = 5.0         # index strike grid (index points)
    tc_pct_premium: float = 0.05     # generic cost as % of premium (used when venue is None)
    tc_min_points: float = 0.10      # generic minimum cost per option (index points)
    venue: Venue | None = None       # venue-specific spread + fee model (costs.py)
    min_premium: float = 0.05        # skip trades below this premium (points)
    tranche_divisor: float = 9.0     # avg sum of bdays to expiry across the 3 expiries
    # low-vol size cut (levels of the vol index, VIX for SPX)
    size_cut: bool = True
    cut_vol_low: float = 13.0        # at/below this level: size = cut_floor
    cut_vol_full: float = 17.0       # at/above this level: full size
    cut_floor: float = 0.25
    # cash management (off: returns are option P&L only)
    include_cash: bool = False       # True -> credit money-market / collateral interest
    mm_fee: float = 0.0010           # money-market rate = T-bill - fee (floored at 0)
    collateral_rate_mult: float = 0.0  # share of (T-bill - collateral_fee) earned on margin (0 = none)
    collateral_fee: float = 0.0        # spread below T-bill earned on margin collateral
    margin_pct_spot: float = 0.15    # CBOE broad-index short put margin parameters
    margin_min_pct_strike: float = 0.10
    # ---- regime filters (multiply the size of new sales; only information known before the TWAP)
    vol_open_strikes: bool = False   # strikes from max(vol index prev close, today's open)
    trend_ma: int = 0                # >0: index prev close below its N-day average -> trend_mult
    trend_mult: float = 0.5
    ts_threshold: float = 0.0        # >0: VIX/VIX3M (prev close) above this -> ts_mult (term-structure stress)
    ts_mult: float = 0.0
    shock_ret: float = 0.0           # >0: an index close-to-close fall worse than this within shock_days -> shock_mult
    shock_days: int = 2
    shock_mult: float = 0.0
    gap_ret: float = 0.0             # >0: today's open below prev close by more than this -> gap_mult today
    gap_mult: float = 0.0
    # ---- hedges
    wing_mult: float = 0.0           # >0: buy a put at 100% - wing_mult x Adj. vol % (same expiry): put spread
    stop_mult: float = 0.0           # >0: buy back a short put at the close once it is worth stop_mult x premium
    stop_cost_mult: float = 2.0      # stressed execution: cost multiple on stop buy-backs
    tail_notional: float = 0.0       # >0: rolling long put, notional as a share of NAV
    tail_mny: float = 0.90           # tail put moneyness
    tail_tenor_bd: int = 21          # tail put tenor (bought every tenor, held to expiry)


# --------------------------------------------------------------------------- pricing
def bs_put(S, K, T, sigma, r, q):
    S, K, T, sigma = map(np.asarray, (S, K, T, sigma))
    T = np.maximum(T, 1e-8)
    sq = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / sq
    d2 = d1 - sq
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)


def bs_put_greeks(S, K, T, sigma, r, q):
    T = np.maximum(T, 1e-8)
    sq = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / sq
    delta = -np.exp(-q * T) * norm.cdf(-d1)
    gamma = np.exp(-q * T) * norm.pdf(d1) / (S * sq)
    vega = S * np.exp(-q * T) * norm.pdf(d1) * np.sqrt(T) / 100.0  # per vol point
    return delta, gamma, vega


def skewed_vol(atm_vol, S, K, T, p: Params):
    """Parametric put skew as a function of z = SDs out-of-the-money.

    linear: IV = ATM x (1 + beta x z), capped
    kink:   IV = ATM x (1 + slope x max(z - z0(T), 0)); z0 falls from kink_z0_short
            (1-5 day options) to kink_z0_month (~21 trading days), i.e. longer-dated
            puts carry more skew per SD. Calibrated on the SPXW chain of 2026-09-22.
    """
    atm = np.asarray(atm_vol)
    Tm = np.maximum(T, 1e-8)
    z = np.log(np.asarray(S) / np.asarray(K)) / (atm * np.sqrt(Tm))
    if p.skew_model == "kink":
        w = np.clip((Tm * TRADING_DAYS - 5.0) / 16.0, 0.0, 1.0)
        z0 = p.kink_z0_short + w * (p.kink_z0_month - p.kink_z0_short)
        mult = np.clip(1.0 + p.kink_slope * np.maximum(z - z0, 0.0), 1.0, p.kink_cap)
    else:
        mult = np.clip(1.0 + p.skew_beta * np.maximum(z, 0.0), 1.0, p.skew_cap)
    return atm * mult


# --------------------------------------------------------------------------- calendar
def extend_calendar(trading_days: pd.DatetimeIndex, extra: int = 15) -> pd.DatetimeIndex:
    """Historical trading days + a few projected business days (US holidays
    removed) so that expiries after the last data point can be scheduled."""
    from pandas.tseries.holiday import USFederalHolidayCalendar

    last = trading_days[-1]
    hol = USFederalHolidayCalendar().holidays(last, last + pd.Timedelta(days=40))
    fut = pd.bdate_range(last + pd.Timedelta(days=1), periods=extra + 10)
    fut = fut[~fut.isin(hol)][:extra]
    return trading_days.append(fut)


def expiries_for(t_pos: int, cal: pd.DatetimeIndex, n: int = 3, max_bd: int = 5):
    """Nearest `n` Mon/Wed/Fri expiries after cal[t_pos] -> list[(expiry_pos, bdays)].
    A scheduled expiry falling on a holiday moves to the previous trading day."""
    t = cal[t_pos]
    out, seen = [], set()
    d = t
    while len(out) < n and (d - t).days < 21:
        d = d + pd.Timedelta(days=1)
        if d.dayofweek not in EXPIRY_WEEKDAYS:
            continue
        pos = cal.searchsorted(d, side="right") - 1  # last trading day <= d
        if pos <= t_pos or pos in seen:
            continue
        bd = pos - t_pos
        if 1 <= bd <= max_bd:
            seen.add(pos)
            out.append((pos, bd))
    return out


# Per-underlying defaults. SPX skew calibrated on the 2026-09-22 SPXW chain (1-5 day
# and ~1-month puts out to 9 SDs OTM), NDX on the NDXP/QQQ chains.
UNDERLYING_DEFAULTS = {
    # SPX risk management (see hedges.py): no new sales while VIX/VIX3M > 0.95
    # (term-structure stress), and buy back any short put worth 3x its premium.
    "SPX": dict(div_yield=0.013, strike_step=5.0, skew_model="kink", skew_beta=0.15, cut_vol_low=13.0, cut_vol_full=17.0,
                ts_threshold=0.95, ts_mult=0.0, stop_mult=3.0),
    "NDX": dict(div_yield=0.008, strike_step=10.0, skew_beta=0.12, cut_vol_low=15.0, cut_vol_full=20.0),
}


UNMANAGED = dict(ts_threshold=0.0, stop_mult=0.0)  # overrides that switch the SPX risk management off


def default_params(underlying: str = "SPX", **overrides) -> Params:
    return Params(underlying=underlying, **{**UNDERLYING_DEFAULTS[underlying], **overrides})


def size_multiplier(vol_prev: float, p: Params) -> float:
    """Low-vol size cut: linear ramp from cut_floor to 1 between the two vol-index levels."""
    if not p.size_cut:
        return 1.0
    w = (vol_prev - p.cut_vol_low) / max(p.cut_vol_full - p.cut_vol_low, 1e-9)
    return float(p.cut_floor + (1.0 - p.cut_floor) * np.clip(w, 0.0, 1.0))


def trade_cost(premium_pts: float, spot: float, p: Params) -> float:
    """Execution cost per option in index points."""
    if p.venue is not None:
        return p.venue.cost_points(premium_pts, spot)
    return max(p.tc_pct_premium * premium_pts, p.tc_min_points)


def margin_requirement(S: float, K: float, option_value: float, p: Params) -> float:
    """CBOE-style margin per unit for a short broad-index put."""
    otm = max(S - K, 0.0)
    return option_value + max(p.margin_pct_spot * S - otm, p.margin_min_pct_strike * K)


def strike_for(vol_prev: float, bdays: int, spot: float, p: Params):
    adj_vix = vol_prev * np.sqrt(bdays / TRADING_DAYS)           # in vol points (%)
    adj_vol_pct = p.sigma_mult * adj_vix                           # e.g. 2.5 * 1.6 = 4.0%
    moneyness = 1.0 - adj_vol_pct / 100.0
    strike = np.floor(moneyness * spot / p.strike_step) * p.strike_step
    return adj_vix, adj_vol_pct, moneyness, strike


def regime_signals(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Per-day size multiplier from the regime filters, using only information
    available before the day's TWAP (previous closes and today's open)."""
    close = df["close"]
    out = pd.DataFrame(index=df.index)
    prev_close = close.shift(1)
    out["trend"] = 1.0
    if p.trend_ma > 0:
        below = prev_close < close.rolling(p.trend_ma).mean().shift(1)
        out.loc[below, "trend"] = p.trend_mult
    out["term"] = 1.0
    if p.ts_threshold > 0:
        ratio = (df["vol_index"] / df["vol_3m"]).shift(1)
        out.loc[ratio > p.ts_threshold, "term"] = p.ts_mult
    out["shock"] = 1.0
    if p.shock_ret > 0:
        ret = close.pct_change()
        worst = ret.shift(1).rolling(max(p.shock_days, 1)).min()
        out.loc[worst < -p.shock_ret, "shock"] = p.shock_mult
    out["gap"] = 1.0
    if p.gap_ret > 0:
        gap = df["open"] / prev_close - 1
        out.loc[gap < -p.gap_ret, "gap"] = p.gap_mult
    out["regime_mult"] = out[["trend", "term", "shock", "gap"]].prod(axis=1)
    return out


def signal_vol(df: pd.DataFrame, p: Params) -> pd.Series:
    """Vol-index level used for strikes and the low-vol size cut on each day."""
    v = df["vol_index"].shift(1)
    if p.vol_open_strikes:
        v = np.maximum(v, df["vol_index_open"])
    return v


# --------------------------------------------------------------------------- backtest
@dataclass
class Result:
    nav: pd.Series
    daily: pd.DataFrame
    trades: pd.DataFrame
    open_positions: pd.DataFrame
    params: Params = field(default_factory=Params)


def _round_down(x: float, step: float) -> float:
    return float(np.floor(x / step) * step)


def run_backtest(df: pd.DataFrame, p: Params | None = None, start: str | None = None) -> Result:
    """Daily simulation. Positions carry a sign: -1 short put (the strategy),
    +1 long put (wing of a put spread, or the rolling tail hedge)."""
    p = p or Params()
    reg = regime_signals(df, p)
    vsig = signal_vol(df, p)
    if start:
        df, reg, vsig = df.loc[start:], reg.loc[start:], vsig.loc[start:]
    cal_hist = df.index
    cal = extend_calendar(cal_hist)
    S_close = df["close"].to_numpy()
    twap = ((df["open"] + df["high"] + df["low"] + df["close"]) / 4).to_numpy()
    iv = (df["atm_vol"] / 100).to_numpy()
    iv_month = (df["iv_mean_30"].fillna(df["atm_vol"]) / 100).to_numpy() if "iv_mean_30" in df else iv
    vol_idx = df["vol_index"].to_numpy()
    vol_sig = vsig.to_numpy()
    reg_mult = reg["regime_mult"].to_numpy()
    rf = df["rf"].fillna(0).to_numpy()
    q = p.div_yield

    nav = 1.0
    cash = 1.0
    margin_used = 0.0
    positions: list[dict] = []
    trades, daily = [], []
    last_tail_expiry = -1

    def value(pos, i, S, atm):
        T = (pos["exp_pos"] - i) / TRADING_DAYS
        vol_k = float(skewed_vol(atm if pos["kind"] != "tail" else iv_month[i], S, pos["strike"], T, p))
        return float(bs_put(S, pos["strike"], T, vol_k, rf[i], q)), vol_k, T

    for i in range(1, len(cal_hist)):
        t = cal_hist[i]
        dt = (t - cal_hist[i - 1]).days / 365.0
        nav_prev = nav
        # 1) optional interest (off by default)
        used = min(margin_used, max(nav_prev, 0.0))
        unused = max(nav_prev - used, 0.0)
        mm_interest = unused * max(rf[i - 1] - p.mm_fee, 0.0) * dt if p.include_cash else 0.0
        coll_rate = max(rf[i - 1] - p.collateral_fee, 0.0) * p.collateral_rate_mult
        coll_interest = used * coll_rate * dt if p.include_cash else 0.0
        interest = mm_interest + coll_interest
        cash += interest

        # 2) new sales at today's TWAP
        sigma_twap = 0.5 * (iv[i - 1] + iv[i])
        v_sig = vol_sig[i] if np.isfinite(vol_sig[i]) else vol_idx[i - 1]
        size_mult = size_multiplier(v_sig, p) * reg_mult[i]
        premium_today = hedge_paid = 0.0
        if size_mult > 0:
            for exp_pos, bd in expiries_for(i, cal):
                adj_vix, adj_vol, mny, K = strike_for(v_sig, bd, twap[i], p)
                T = (bd + 0.5) / TRADING_DAYS  # half of today's session + bd sessions
                vol_k = float(skewed_vol(sigma_twap, twap[i], K, T, p))
                px = float(bs_put(twap[i], K, T, vol_k, rf[i], q))
                if px < p.min_premium:
                    continue
                fill = px - trade_cost(px, twap[i], p)
                if fill <= 0:
                    continue
                notional = nav_prev * p.leverage * size_mult / p.tranche_divisor
                units = notional / twap[i]
                pid = len(trades)
                wing = None
                if p.wing_mult > 0:
                    Kw = _round_down((1.0 - p.wing_mult * adj_vol / 100.0) * twap[i], p.strike_step)
                    vol_w = float(skewed_vol(sigma_twap, twap[i], Kw, T, p))
                    pw = float(bs_put(twap[i], Kw, T, vol_w, rf[i], q))
                    pay = pw + trade_cost(pw, twap[i], p) if pw >= 0.01 else 0.0
                    if pay > 0:
                        wing = dict(kind="wing", sign=1, strike=Kw, exp_pos=exp_pos, units=units, pid=pid)
                        cash -= units * pay
                        hedge_paid += units * pay
                        positions.append(wing)
                cash += units * fill
                premium_today += units * fill
                pos = dict(
                    kind="short", sign=-1, pid=pid, trade_date=t, expiry=cal[exp_pos], exp_pos=exp_pos, bdays=bd,
                    spot_twap=twap[i], vol_prev=v_sig, adj_vix=adj_vix, adj_vol_pct=adj_vol, moneyness=mny,
                    strike=K, iv=vol_k, price_mid=px, fill=fill, units=units, notional=notional,
                    size_mult=size_mult, wing_strike=wing["strike"] if wing else np.nan,
                    wing_cost=(pay if wing else 0.0), stopped=False,
                )
                positions.append(pos)
                trades.append(pos)

        # 2b) rolling tail hedge: buy a new put when the previous one has expired
        if p.tail_notional > 0 and i >= last_tail_expiry:
            exp_pos = min(i + p.tail_tenor_bd, len(cal) - 1)
            T = (exp_pos - i + 0.5) / TRADING_DAYS
            Kt = _round_down(p.tail_mny * twap[i], p.strike_step)
            vol_t = float(skewed_vol(0.5 * (iv_month[i - 1] + iv_month[i]), twap[i], Kt, T, p))
            pt = float(bs_put(twap[i], Kt, T, vol_t, rf[i], q))
            pay = pt + trade_cost(pt, twap[i], p)
            units = nav_prev * p.tail_notional / twap[i]
            cash -= units * pay
            hedge_paid += units * pay
            positions.append(dict(kind="tail", sign=1, strike=Kt, exp_pos=exp_pos, units=units, pid=-1))
            last_tail_expiry = exp_pos

        # 3) settle everything expiring today at the close
        payout_today = hedge_payout = 0.0
        for pos in positions:
            if pos["exp_pos"] == i:
                payoff = max(pos["strike"] - S_close[i], 0.0)
                cash += pos["sign"] * pos["units"] * payoff
                if pos["kind"] == "short":
                    pos["payoff"] = payoff
                    pos["pnl_pts"] = pos["fill"] - payoff
                    payout_today += pos["units"] * payoff
                else:
                    hedge_payout += pos["units"] * payoff
        positions = [x for x in positions if x["exp_pos"] != i]

        # 4) stop-loss: buy back short puts (and sell their wings) at the close
        n_stops = 0
        if p.stop_mult > 0:
            stop_ids = set()
            for pos in positions:
                if pos["kind"] == "short":
                    v, _, _ = value(pos, i, S_close[i], iv[i])
                    if v >= p.stop_mult * pos["fill"]:
                        exit_px = v + p.stop_cost_mult * trade_cost(v, S_close[i], p)
                        cash -= pos["units"] * exit_px
                        pos.update(stopped=True, payoff=exit_px, pnl_pts=pos["fill"] - exit_px)
                        payout_today += pos["units"] * exit_px
                        stop_ids.add(pos["pid"])
                        n_stops += 1
            for pos in positions:
                if pos["kind"] == "wing" and pos["pid"] in stop_ids:
                    v, _, _ = value(pos, i, S_close[i], iv[i])
                    proceeds = max(v - p.stop_cost_mult * trade_cost(v, S_close[i], p), 0.0)
                    cash += pos["units"] * proceeds
                    hedge_payout += pos["units"] * proceeds
            positions = [x for x in positions if x["pid"] not in stop_ids or x["kind"] == "tail"]

        # 5) mark to model at the close
        book = delta = gamma = vega = gross_notional = margin_used = 0.0
        wings = {x["pid"]: x for x in positions if x["kind"] == "wing"}
        for pos in positions:
            v, vol_k, T = value(pos, i, S_close[i], iv[i])
            d, g, vg = bs_put_greeks(S_close[i], pos["strike"], T, vol_k, rf[i], q)
            u = pos["sign"] * pos["units"]
            book += u * v
            delta += u * d * S_close[i]
            gamma += u * g * S_close[i] ** 2 / 100
            vega += u * vg
            if pos["kind"] == "short":
                gross_notional += pos["units"] * pos["strike"]
                m = margin_requirement(S_close[i], pos["strike"], v, p)
                w = wings.get(pos["pid"])
                if w is not None:  # put spread: margin capped at the strike width
                    m = min(m, pos["strike"] - w["strike"])
                margin_used += pos["units"] * m

        nav = cash + book
        daily.append(dict(
            date=t, nav=nav, ret=nav / nav_prev - 1, interest=interest / nav_prev,
            mm_interest=mm_interest / nav_prev, coll_interest=coll_interest / nav_prev,
            size_mult=size_mult, regime_mult=reg_mult[i], margin_used=margin_used / nav,
            unused_cash=max(nav - margin_used, 0) / nav,
            premium=premium_today / nav_prev, payout=payout_today / nav_prev,
            hedge_paid=hedge_paid / nav_prev, hedge_payout=hedge_payout / nav_prev, stops=n_stops,
            n_open=sum(1 for x in positions if x["kind"] == "short"), gross_notional=gross_notional / nav,
            delta=delta / nav, gamma_1pct=gamma / nav, vega_1pt=vega / nav,
            vol_index=vol_idx[i], atm_vol=iv[i] * 100, spot=S_close[i],
        ))

    daily_df = pd.DataFrame(daily).set_index("date")
    nav_s = pd.concat([pd.Series([1.0], index=[cal_hist[0]]), daily_df["nav"]])
    drop = ["exp_pos", "kind", "sign", "pid"]
    trades_df = pd.DataFrame(trades).drop(columns=drop, errors="ignore")
    open_df = pd.DataFrame(positions).drop(columns=drop, errors="ignore")
    return Result(nav=nav_s, daily=daily_df, trades=trades_df, open_positions=open_df, params=p)


def next_day_signal(df: pd.DataFrame, p: Params | None = None, nav: float | None = None) -> pd.DataFrame:
    """Trades to execute on the next trading day using the latest close data.

    Regime filters are evaluated on the latest close. The gap filter and the
    VIX-open strike rule need the next day's open, so apply them at execution.
    If ``p.venue`` is set, strikes are also expressed in the venue's instrument
    (e.g. XSP or SPY, $1 grid, rounded down) and, given ``nav`` in USD, the number
    of contracts per expiry is computed (rounded to the nearest whole contract).
    """
    p = p or Params()
    cal = extend_calendar(df.index)
    i = len(df.index)  # position of the next (not yet observed) trading day in `cal`
    nxt = pd.DataFrame(index=[cal[i]], columns=df.columns, dtype=float)
    reg = regime_signals(pd.concat([df, nxt]), p).iloc[-1]
    last = df.iloc[-1]
    spot_ref = last["close"]  # TWAP unknown ex-ante: reference = last close
    v_sig = last["vol_index"]
    size_mult = size_multiplier(v_sig, p) * reg["regime_mult"]
    rows = []
    for exp_pos, bd in expiries_for(i, cal):
        adj_vix, adj_vol, mny, K = strike_for(v_sig, bd, spot_ref, p)
        T = (bd + 0.5) / TRADING_DAYS
        vol_k = float(skewed_vol(last["atm_vol"] / 100, spot_ref, K, T, p))
        px = float(bs_put(spot_ref, K, T, vol_k, last["rf"], p.div_yield))
        d, _, _ = bs_put_greeks(spot_ref, K, T, vol_k, last["rf"], p.div_yield)
        row = dict(
            trade_date=cal[i].date(), expiry=cal[exp_pos].date(),
            expiry_day=cal[exp_pos].day_name(), bdays=bd, vol_index=v_sig,
            adj_vix_pct=round(adj_vix, 3), adj_vol_pct=round(adj_vol, 3),
            target_moneyness_pct=round(mny * 100, 2), ref_spot=round(spot_ref, 2),
            strike=K, model_iv_pct=round(vol_k * 100, 2), est_premium_pts=round(px, 2),
            est_premium_bps=round(px / spot_ref * 1e4, 2), delta=round(float(d), 4),
            regime_mult=round(float(reg["regime_mult"]), 3), size_mult=round(size_mult, 3),
            notional_pct_nav=round(100 * p.leverage * size_mult / p.tranche_divisor, 2),
        )
        wing_mny = None
        if p.wing_mult > 0:
            wing_mny = 1.0 - p.wing_mult * adj_vol / 100.0
            Kw = _round_down(wing_mny * spot_ref, p.strike_step)
            vw = float(skewed_vol(last["atm_vol"] / 100, spot_ref, Kw, T, p))
            row.update(wing_strike=Kw, est_wing_pts=round(float(bs_put(spot_ref, Kw, T, vw, last["rf"], p.div_yield)), 2))
        if p.venue is not None:
            v = p.venue
            inst_spot = spot_ref * v.spot_ratio
            row["instrument"] = v.instrument
            row["instrument_strike"] = _round_down(mny * inst_spot, v.strike_step)
            if wing_mny is not None:
                row["instrument_wing_strike"] = _round_down(wing_mny * inst_spot, v.strike_step)
            row["est_premium_instr"] = round(px * v.spot_ratio, 3)
            row["est_cost_instr"] = round(trade_cost(px, spot_ref, p) * v.spot_ratio, 3)
            if nav:
                notional = nav * p.leverage * size_mult / p.tranche_divisor
                row["contracts"] = int(round(notional / v.contract_notional(spot_ref)))
        rows.append(row)
    return pd.DataFrame(rows)
