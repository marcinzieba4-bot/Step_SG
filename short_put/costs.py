"""Execution-cost model per venue / instrument.

Calibrated on the Cboe end-of-day option chains of 2026-09-22 (puts 1-5
business days out, about 2.3-3.9 SDs out-of-the-money, i.e. where this
strategy's strikes sit). All quantities are expressed in basis points of the
index level, so the model scales across the whole backtest history:

    quoted half-spread (bps of spot) = hs_fixed_bps + hs_pct_mid * premium_bps
    execution cost per option       = pay_frac * half-spread + fees

``pay_frac`` is the share of the quoted half-spread paid versus mid:
0 = filled at mid, 1 = selling on the bid. End-of-day quotes are wider than
intraday ones (index options quote until 16:15 ET), so the fitted spreads are
an upper bound for a TWAP executed during the session.

Fees are per contract and converted to bps of the contract notional at the
calibration date (multiplier x instrument spot).

Fee sources: IBKR Pro options commissions ($0.15-0.65 per contract, $0.65 fixed);
Options Regulatory Fee ~$0.027; OCC clearing ~$0.02; Cboe fee schedule
(15 Sep 2026): XSP customer $0.07 per contract for orders of 10+ contracts
(rebate below 10), ETF options customer fees waived below 100 contracts;
Nasdaq PHLX NDX/NDXP customer $0.50, non-customer $0.75. Institutional
commissions and non-customer ETF option fees are assumptions to confirm.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    name: str
    underlying: str               # SPX or NDX
    instrument: str               # XSP, SPY, SPXW, NDXP, QQQ
    multiplier: float             # $ per point of the instrument
    spot_ratio: float             # instrument spot / index spot
    strike_step: float            # instrument strike grid
    calib_spot: float             # index level on the calibration date (for $ fees)
    hs_fixed_bps: float           # fitted quoted half-spread, fixed part (bps of spot)
    hs_pct_mid: float             # fitted quoted half-spread, share of mid premium
    pay_frac: float               # share of the quoted half-spread paid
    fee_per_contract: float       # commission + exchange + clearing + regulatory ($)
    settlement: str = ""
    note: str = ""

    def cost_points(self, premium_pts: float, spot: float) -> float:
        """Execution cost per option in index points (premium given in index points)."""
        premium_bps = premium_pts / spot * 1e4
        half_bps = self.hs_fixed_bps + self.hs_pct_mid * premium_bps
        fee_bps = self.fee_per_contract / (self.multiplier * self.calib_spot * self.spot_ratio) * 1e4
        return (self.pay_frac * half_bps + fee_bps) * spot / 1e4

    def contract_notional(self, spot: float) -> float:
        return self.multiplier * spot * self.spot_ratio


# Quoted-spread fits from the 2026-09-22 Cboe chains (see liquidity.py)
_SPX = 7764.64
_XSP = dict(underlying="SPX", instrument="XSP", multiplier=100.0, spot_ratio=776.46 / _SPX, strike_step=1.0,
            calib_spot=_SPX, hs_fixed_bps=0.167, hs_pct_mid=0.013,
            settlement="Cash-settled, European, PM")
_SPY = dict(underlying="SPX", instrument="SPY", multiplier=100.0, spot_ratio=773.38 / _SPX, strike_step=1.0,
            calib_spot=_SPX, hs_fixed_bps=0.065, hs_pct_mid=0.0,
            settlement="Physical (shares), American")
_SPXW = dict(underlying="SPX", instrument="SPXW", multiplier=100.0, spot_ratio=1.0, strike_step=5.0,
             calib_spot=_SPX, hs_fixed_bps=0.056, hs_pct_mid=0.010,
             settlement="Cash-settled, European, PM")
_NDX = 30732.40
_NDXP = dict(underlying="NDX", instrument="NDXP", multiplier=100.0, spot_ratio=1.0, strike_step=10.0,
             calib_spot=_NDX, hs_fixed_bps=0.658, hs_pct_mid=0.132,
             settlement="Cash-settled, European, PM")
_QQQ = dict(underlying="NDX", instrument="QQQ", multiplier=100.0, spot_ratio=748.32 / _NDX, strike_step=1.0,
            calib_spot=_NDX, hs_fixed_bps=0.068, hs_pct_mid=0.034,
            settlement="Physical (shares), American")

VENUES: dict[str, Venue] = {
    # ---------------- S&P 500
    "ibkr_xsp": Venue(
        name="IBKR private - XSP options", **_XSP, pay_frac=0.5,
        fee_per_contract=0.65 + 0.07 + 0.027 + 0.02,    # IBKR comm. + Cboe XSP customer fee (10+ lots) + ORF + OCC
        note="Mini-SPX (1/10 SPX). No assignment or share delivery.",
    ),
    "gs_spy": Venue(
        name="GS institutional - SPY options", **_SPY, pay_frac=0.25,
        fee_per_contract=0.15 + 0.30 + 0.02,            # negotiated comm. + non-customer exch. fee (approx.) + OCC
        note="Deepest options market. Assignment delivers SPY shares.",
    ),
    "ibkr_spy": Venue(
        name="IBKR private - SPY options (comparison)", **_SPY, pay_frac=0.5,
        fee_per_contract=0.65 + 0.00 + 0.027 + 0.02,    # IBKR comm. + customer exch. fee waived (<100 lots) + ORF + OCC
        note="Tighter than XSP, but American-style and physically settled.",
    ),
    "gs_spxw": Venue(
        name="GS institutional - SPXW options (comparison)", **_SPXW, pay_frac=0.25,
        fee_per_contract=0.20 + 0.60 + 0.02,            # negotiated comm. + Cboe SPX non-customer fee (approx.) + OCC
        note="Standard institutional route. About $0.78M per contract.",
    ),
    # ---------------- Nasdaq-100
    "ibkr_qqq": Venue(
        name="IBKR private - QQQ options", **_QQQ, pay_frac=0.5,
        fee_per_contract=0.65 + 0.027 + 0.02 + 0.03,
        note="Penny-wide, deep liquidity. American-style, physically settled.",
    ),
    "ibkr_ndxp": Venue(
        name="IBKR private - NDXP options", **_NDXP, pay_frac=0.5,
        fee_per_contract=0.65 + 0.50 + 0.027 + 0.02,
        note="Cash-settled, European. About $3M per contract.",
    ),
    "gs_ndxp": Venue(
        name="GS institutional - NDXP options", **_NDXP, pay_frac=0.25,
        fee_per_contract=0.20 + 0.75 + 0.02,
        note="Negotiated / algo execution inside the screen spread.",
    ),
    "gs_qqq": Venue(
        name="GS institutional - QQQ options", **_QQQ, pay_frac=0.25,
        fee_per_contract=0.15 + 0.30 + 0.02,
        note="Physical settlement handled by the prime broker.",
    ),
}


def params_for(venue_key: str, **overrides):
    """Strategy Params for a venue: the underlying's defaults plus the venue cost model."""
    from .strategy import default_params

    v = VENUES[venue_key]
    return default_params(v.underlying, venue=v, **overrides)
