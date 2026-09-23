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
    skew_beta: float = 0.15          # IV(z) = ATM * (1 + beta * z), z = sd's OTM
    skew_cap: float = 2.0            # max IV multiple of ATM
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
    """Parametric put skew: vol rises linearly with the number of SDs OTM."""
    atm = np.asarray(atm_vol)
    z = np.log(np.asarray(S) / np.asarray(K)) / (atm * np.sqrt(np.maximum(T, 1e-8)))
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


# Per-underlying defaults. SPX skew calibrated on the 2026-09-22 XSP/SPY/SPXW chains
# (market IV about 1.37-1.51x ATM at 2.5-3.5 SDs OTM), NDX on the NDXP/QQQ chains.
UNDERLYING_DEFAULTS = {
    "SPX": dict(div_yield=0.013, strike_step=5.0, skew_beta=0.15, cut_vol_low=13.0, cut_vol_full=17.0),
    "NDX": dict(div_yield=0.008, strike_step=10.0, skew_beta=0.12, cut_vol_low=15.0, cut_vol_full=20.0),
}


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


# --------------------------------------------------------------------------- backtest
@dataclass
class Result:
    nav: pd.Series
    daily: pd.DataFrame
    trades: pd.DataFrame
    open_positions: pd.DataFrame
    params: Params = field(default_factory=Params)


def run_backtest(df: pd.DataFrame, p: Params | None = None, start: str | None = None) -> Result:
    p = p or Params()
    if start:
        df = df.loc[start:]
    cal_hist = df.index
    cal = extend_calendar(cal_hist)
    S_close = df["close"].to_numpy()
    twap = ((df["open"] + df["high"] + df["low"] + df["close"]) / 4).to_numpy()
    iv = (df["atm_vol"] / 100).to_numpy()
    vol_idx = df["vol_index"].to_numpy()
    rf = df["rf"].fillna(0).to_numpy()
    q = p.div_yield

    nav = 1.0
    cash = 1.0  # premium & collateral account
    margin_used = 0.0  # capital tied up as margin at the previous close
    positions: list[dict] = []
    trades, daily = [], []

    for i in range(1, len(cal_hist)):
        t = cal_hist[i]
        dt = (t - cal_hist[i - 1]).days / 365.0
        # 1) interest at the previous close's rate: unused capital earns the
        #    money-market rate, margin earns collateral_rate_mult x T-bill
        nav_prev = nav
        used = min(margin_used, max(nav_prev, 0.0))
        unused = max(nav_prev - used, 0.0)
        mm_interest = unused * max(rf[i - 1] - p.mm_fee, 0.0) * dt if p.include_cash else 0.0
        coll_rate = max(rf[i - 1] - p.collateral_fee, 0.0) * p.collateral_rate_mult
        coll_interest = used * coll_rate * dt if p.include_cash else 0.0
        interest = mm_interest + coll_interest
        cash += interest

        # 2) new sales at today's TWAP, sized off yesterday's NAV and vol index
        sigma_twap = 0.5 * (iv[i - 1] + iv[i])
        size_mult = size_multiplier(vol_idx[i - 1], p)
        premium_today = 0.0
        for exp_pos, bd in expiries_for(i, cal):
            adj_vix, adj_vol, mny, K = strike_for(vol_idx[i - 1], bd, twap[i], p)
            T = (bd + 0.5) / TRADING_DAYS  # half of today's session + bd sessions
            vol_k = float(skewed_vol(sigma_twap, twap[i], K, T, p))
            px = float(bs_put(twap[i], K, T, vol_k, rf[i], q))
            if px < p.min_premium:
                continue
            cost = trade_cost(px, twap[i], p)
            fill = px - cost
            if fill <= 0:
                continue
            notional = nav_prev * p.leverage * size_mult / p.tranche_divisor
            units = notional / twap[i]
            cash += units * fill
            premium_today += units * fill
            pos = dict(
                trade_date=t, expiry=cal[exp_pos], exp_pos=exp_pos, bdays=bd,
                spot_twap=twap[i], vol_prev=vol_idx[i - 1], adj_vix=adj_vix,
                adj_vol_pct=adj_vol, moneyness=mny, strike=K, iv=vol_k,
                price_mid=px, fill=fill, units=units, notional=notional, size_mult=size_mult,
            )
            positions.append(pos)
            trades.append(pos)

        # 3) settle expiring positions at today's close
        settled, payout_today = [], 0.0
        for pos in positions:
            if pos["exp_pos"] == i:
                payoff = max(pos["strike"] - S_close[i], 0.0)
                pos["payoff"] = payoff
                pos["pnl_pts"] = pos["fill"] - payoff
                pos["pnl_nav"] = pos["units"] * pos["pnl_pts"]
                cash -= pos["units"] * payoff
                payout_today += pos["units"] * payoff
                settled.append(pos)
        positions = [x for x in positions if x["exp_pos"] != i]

        # 4) mark remaining positions to model at the close
        liab = delta = gamma = vega = gross_notional = margin_used = 0.0
        for pos in positions:
            T = (pos["exp_pos"] - i) / TRADING_DAYS
            vol_k = float(skewed_vol(iv[i], S_close[i], pos["strike"], T, p))
            v = float(bs_put(S_close[i], pos["strike"], T, vol_k, rf[i], q))
            d, g, vg = bs_put_greeks(S_close[i], pos["strike"], T, vol_k, rf[i], q)
            liab += pos["units"] * v
            delta -= pos["units"] * d * S_close[i]          # short put => long delta ($ per NAV)
            gamma -= pos["units"] * g * S_close[i] ** 2 / 100  # $ delta change per 1% move
            vega -= pos["units"] * vg                          # NAV change per +1 vol pt
            gross_notional += pos["units"] * pos["strike"]
            margin_used += pos["units"] * margin_requirement(S_close[i], pos["strike"], v, p)

        nav = cash - liab
        daily.append(dict(
            date=t, nav=nav, ret=nav / nav_prev - 1, interest=interest / nav_prev,
            mm_interest=mm_interest / nav_prev, coll_interest=coll_interest / nav_prev,
            size_mult=size_mult, margin_used=margin_used / nav, unused_cash=max(nav - margin_used, 0) / nav,
            premium=premium_today / nav_prev, payout=payout_today / nav_prev,
            n_open=len(positions), gross_notional=gross_notional / nav,
            delta=delta / nav, gamma_1pct=gamma / nav, vega_1pt=vega / nav,
            vol_index=vol_idx[i], atm_vol=iv[i] * 100, spot=S_close[i],
        ))

    daily_df = pd.DataFrame(daily).set_index("date")
    nav_s = pd.concat([pd.Series([1.0], index=[cal_hist[0]]), daily_df["nav"]])
    trades_df = pd.DataFrame(trades)
    if len(trades_df):
        trades_df = trades_df.drop(columns=["exp_pos"])
    open_df = pd.DataFrame(positions).drop(columns=["exp_pos"], errors="ignore")
    return Result(nav=nav_s, daily=daily_df, trades=trades_df, open_positions=open_df, params=p)


def next_day_signal(df: pd.DataFrame, p: Params | None = None, nav: float | None = None) -> pd.DataFrame:
    """Trades to execute on the next trading day using the latest close data.

    If ``p.venue`` is set, the strike is also expressed in the venue's instrument
    (e.g. XSP or SPY, $1 strike grid, rounded down) and, given ``nav`` in USD, the
    number of contracts per expiry is computed (rounded to the nearest whole contract).
    """
    p = p or Params()
    cal = extend_calendar(df.index)
    i = len(df.index)  # position of the next (not yet observed) trading day in `cal`
    last = df.iloc[-1]
    spot_ref = last["close"]  # TWAP unknown ex-ante: reference = last close
    size_mult = size_multiplier(last["vol_index"], p)
    rows = []
    for exp_pos, bd in expiries_for(i, cal):
        adj_vix, adj_vol, mny, K = strike_for(last["vol_index"], bd, spot_ref, p)
        T = (bd + 0.5) / TRADING_DAYS
        vol_k = float(skewed_vol(last["atm_vol"] / 100, spot_ref, K, T, p))
        px = float(bs_put(spot_ref, K, T, vol_k, last["rf"], p.div_yield))
        d, _, _ = bs_put_greeks(spot_ref, K, T, vol_k, last["rf"], p.div_yield)
        rows.append(dict(
            trade_date=cal[i].date(), expiry=cal[exp_pos].date(),
            expiry_day=cal[exp_pos].day_name(), bdays=bd, vol_index=last["vol_index"],
            adj_vix_pct=round(adj_vix, 3), adj_vol_pct=round(adj_vol, 3),
            target_moneyness_pct=round(mny * 100, 2), ref_spot=round(spot_ref, 2),
            strike=K, model_iv_pct=round(vol_k * 100, 2), est_premium_pts=round(px, 2),
            est_premium_bps=round(px / spot_ref * 1e4, 2), delta=round(float(d), 4),
            size_mult=round(size_mult, 3),
            notional_pct_nav=round(100 * p.leverage * size_mult / p.tranche_divisor, 2),
        ))
        if p.venue is not None:
            v = p.venue
            inst_spot = spot_ref * v.spot_ratio
            rows[-1]["instrument"] = v.instrument
            rows[-1]["instrument_strike"] = float(np.floor(mny * inst_spot / v.strike_step) * v.strike_step)
            rows[-1]["est_premium_instr"] = round(px * v.spot_ratio, 3)
            rows[-1]["est_cost_instr"] = round(trade_cost(px, spot_ref, p) * v.spot_ratio, 3)
            if nav:
                notional = nav * p.leverage * size_mult / p.tranche_divisor
                rows[-1]["contracts"] = int(round(notional / v.contract_notional(spot_ref)))
    return pd.DataFrame(rows)
