"""Execution-cost model per venue / instrument.

Calibrated on the Cboe end-of-day option chains of 2026-09-22 (puts 1-5
business days out, 2.3-3.7 SDs out-of-the-money, i.e. where this strategy's
strikes sit). All quantities are expressed in basis points of the underlying
spot, so the model scales across the whole backtest history:

    quoted half-spread (bps of spot) = hs_fixed_bps + hs_pct_mid * premium_bps
    execution cost per option       = pay_frac * half-spread + fees

``pay_frac`` is the share of the quoted half-spread paid versus mid:
0 = filled at mid, 1 = selling on the bid. End-of-day quotes are wider than
intraday ones (NDX options quote until 16:15 ET), so the fitted spreads are
an upper bound for a TWAP executed during the session.

Fees are per contract and converted to bps of the instrument's notional
(multiplier x instrument spot).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    name: str
    instrument: str               # NDXP (NDX PM-settled), QQQ, XND
    multiplier: float             # $ per point of the instrument
    ndx_ratio: float              # instrument spot / NDX spot
    hs_fixed_bps: float           # fitted quoted half-spread, fixed part (bps of spot)
    hs_pct_mid: float             # fitted quoted half-spread, share of mid premium
    pay_frac: float               # share of the quoted half-spread paid
    fee_per_contract: float       # commission + exchange + clearing + regulatory ($)
    note: str = ""

    def cost_points(self, premium_pts: float, spot_ndx: float) -> float:
        """Execution cost per option in NDX points (premium given in NDX points)."""
        premium_bps = premium_pts / spot_ndx * 1e4
        half_bps = self.hs_fixed_bps + self.hs_pct_mid * premium_bps
        inst_spot_now = 30732.40 * self.ndx_ratio  # snapshot notional, for the $ fee
        fee_bps = self.fee_per_contract / (self.multiplier * inst_spot_now) * 1e4
        return (self.pay_frac * half_bps + fee_bps) * spot_ndx / 1e4

    def contract_notional(self, spot_ndx: float) -> float:
        return self.multiplier * spot_ndx * self.ndx_ratio


# Quoted-spread fits from the 2026-09-22 Cboe chains (see liquidity.py)
_NDXP = dict(instrument="NDXP", multiplier=100.0, ndx_ratio=1.0, hs_fixed_bps=0.658, hs_pct_mid=0.132)
_QQQ = dict(instrument="QQQ", multiplier=100.0, ndx_ratio=748.32 / 30732.40, hs_fixed_bps=0.068, hs_pct_mid=0.034)

VENUES: dict[str, Venue] = {
    # --- private account at Interactive Brokers (IBKR Pro, customer capacity)
    "ibkr_qqq": Venue(
        name="IBKR private - QQQ options", **_QQQ, pay_frac=0.5,
        fee_per_contract=0.65 + 0.027 + 0.02 + 0.03,   # commission + ORF + OCC + exch/TAF (approx.)
        note="Penny-wide, deep liquidity. American-style, physically settled.",
    ),
    "ibkr_ndxp": Venue(
        name="IBKR private - NDXP options", **_NDXP, pay_frac=0.5,
        fee_per_contract=0.65 + 0.50 + 0.027 + 0.02,   # commission + PHLX customer fee + ORF + OCC
        note="Cash-settled, European. ~$3M per contract.",
    ),
    # --- institutional execution via Goldman Sachs (non-customer capacity)
    "gs_ndxp": Venue(
        name="GS institutional - NDXP options", **_NDXP, pay_frac=0.25,
        fee_per_contract=0.20 + 0.75 + 0.02,           # negotiated comm. + PHLX non-customer fee + OCC
        note="Negotiated / algo execution inside the screen spread.",
    ),
    "gs_qqq": Venue(
        name="GS institutional - QQQ options", **_QQQ, pay_frac=0.25,
        fee_per_contract=0.15 + 0.30 + 0.02,           # negotiated comm. + non-customer exch. fee + OCC
        note="Cheapest listed route. Physical settlement handled by the prime broker.",
    ),
}


# Cash terms per setup. Margin at both venues can be met with T-bills, so the
# collateral keeps earning; idle cash sits in T-bills / a T-bill fund.
CASH_TERMS = {
    # IBKR: hold T-bills or a T-bill ETF (marginable); ~10bp all-in drag
    "ibkr_qqq": dict(mm_fee=0.0010, collateral_rate_mult=1.0, collateral_fee=0.0010),
    "ibkr_ndxp": dict(mm_fee=0.0010, collateral_rate_mult=1.0, collateral_fee=0.0010),
    # GS prime brokerage: T-bills posted as collateral; ~5bp drag
    "gs_ndxp": dict(mm_fee=0.0005, collateral_rate_mult=1.0, collateral_fee=0.0005),
    "gs_qqq": dict(mm_fee=0.0005, collateral_rate_mult=1.0, collateral_fee=0.0005),
}
# Leaving cash uninvested at IBKR earns benchmark - 0.5% (and nothing on the first $10k)
IBKR_CASH_BALANCE = dict(mm_fee=0.0050, collateral_rate_mult=1.0, collateral_fee=0.0050)


def params_for(venue_key: str, base=None, **overrides):
    """Strategy Params for a venue: its cost model plus its cash terms."""
    from dataclasses import replace

    from .strategy import Params

    base = base or Params()
    return replace(base, venue=VENUES[venue_key], **{**CASH_TERMS[venue_key], **overrides})
