"""Pre-trade liquidity check and spread calibration from Cboe option chains.

Cboe publishes a free delayed chain per underlying, refreshed after the close:
    https://cdn.cboe.com/api/global/delayed_quotes/options/{_NDX|QQQ|_XND}.json

Usage:
    python -m short_put.liquidity            # quotes at tomorrow's strikes + spread fit

The fit (half-spread in bps of spot = a + b x mid) over puts 1-5 business days
out and 2.3-3.7 SDs OTM is what ``costs.py`` uses. Note that end-of-day quotes
are wider than quotes during the session.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import requests

from .data import UA

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
_OCC = re.compile(r"([A-Z]+)(\d{6})([CP])(\d{8})")


def load_chain(sym: str) -> pd.DataFrame:
    """sym: '_NDX' (NDX + NDXP), 'QQQ' or '_XND'."""
    r = requests.get(CHAIN_URL.format(sym=sym), headers=UA, timeout=120)
    r.raise_for_status()
    js = r.json()["data"]
    rows = []
    for o in js["options"]:
        root, exp, cp, k = _OCC.match(o["option"]).groups()
        rows.append(dict(
            root=root, expiry=pd.Timestamp("20" + exp), cp=cp, strike=int(k) / 1000,
            bid=o["bid"], ask=o["ask"], iv=o["iv"], open_interest=o["open_interest"], volume=o["volume"],
        ))
    df = pd.DataFrame(rows)
    df["spot"] = js["current_price"]
    df["asof"] = pd.Timestamp(js.get("last_trade_time"))
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["half_spread"] = (df["ask"] - df["bid"]) / 2
    df["half_pct_mid"] = df["half_spread"] / df["mid"]
    df["moneyness"] = df["strike"] / df["spot"]
    return df


def quotes_near(chain: pd.DataFrame, root: str, expiry, strike: float, width: int = 2) -> pd.DataFrame:
    """The listed puts closest to `strike` for one expiry (width strikes each side)."""
    x = chain[(chain["root"] == root) & (chain["cp"] == "P") & (chain["expiry"] == pd.Timestamp(expiry))]
    if x.empty:
        return x
    x = x.iloc[(x["strike"] - strike).abs().argsort()[: 2 * width + 1]].sort_values("strike")
    return x[["root", "expiry", "strike", "moneyness", "bid", "ask", "mid", "half_pct_mid", "open_interest", "volume"]]


def fit_spread(chain: pd.DataFrame, root: str, atm_vol: float, max_bd: int = 5) -> dict:
    """Fit half-spread (bps of spot) = a + b * mid (bps of spot) around the strategy's strikes."""
    asof = chain["asof"].iloc[0].normalize()
    x = chain[(chain["root"] == root) & (chain["cp"] == "P") & (chain["bid"] > 0)].copy()
    x["bd"] = x["expiry"].map(lambda e: len(pd.bdate_range(asof + pd.Timedelta(days=1), e)))
    x = x[x["bd"].between(1, max_bd)]
    x["z"] = np.log(x["spot"] / x["strike"]) / (atm_vol * np.sqrt(x["bd"] / 252))
    x = x[x["z"].between(2.3, 3.7)]
    half = x["half_spread"] / x["spot"] * 1e4
    mid = x["mid"] / x["spot"] * 1e4
    a, b = np.linalg.lstsq(np.vstack([np.ones(len(x)), mid]).T, half, rcond=None)[0]
    return dict(root=root, n=len(x), hs_fixed_bps=round(a, 3), hs_pct_mid=round(b, 3),
                median_half_pct_mid=round(float(x["half_pct_mid"].median()), 3))


def main():
    from .costs import VENUES
    from .data import load_all
    from .strategy import Params, next_day_signal

    df = load_all()
    atm = df["atm_vol"].iloc[-1] / 100
    sig = next_day_signal(df, Params())
    ndx = load_chain("_NDX")
    qqq = load_chain("QQQ")
    ratio = qqq["spot"].iloc[0] / ndx["spot"].iloc[0]
    pd.set_option("display.width", 200)
    print(f"Chains as of {ndx['asof'].iloc[0]} | NDX {ndx['spot'].iloc[0]:,.2f} | QQQ {qqq['spot'].iloc[0]:.2f}")
    for _, s in sig.iterrows():
        print(f"\n=== {s['expiry']} ({s['expiry_day']}) NDX strike {s['strike']:.0f}, model premium {s['est_premium_pts']:.2f} pts")
        print(quotes_near(ndx, "NDXP", s["expiry"], s["strike"]).to_string(index=False, float_format=lambda v: f"{v:.3f}"))
        print(quotes_near(qqq, "QQQ", s["expiry"], np.floor(s["strike"] * ratio)).to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print("\nSpread fits (compare with costs.py):")
    print(pd.DataFrame([fit_spread(ndx, "NDXP", atm), fit_spread(qqq, "QQQ", atm)]).to_string(index=False))
    print({k: (v.hs_fixed_bps, v.hs_pct_mid) for k, v in VENUES.items()})


if __name__ == "__main__":
    main()
