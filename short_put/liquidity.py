"""Pre-trade liquidity check and spread calibration from Cboe option chains.

Cboe publishes a free delayed chain per underlying, refreshed after the close:
    https://cdn.cboe.com/api/global/delayed_quotes/options/{_XSP|SPY|_SPX|_NDX|QQQ}.json

Usage:
    python -m short_put.liquidity [SPX|NDX]   # quotes at tomorrow's strikes + spread fit

The fit (half-spread in bps of spot = a + b x mid) over puts 1-5 business days
out and 2.3-3.9 SDs OTM is what ``costs.py`` uses. Note that end-of-day quotes
are wider than quotes during the session.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import requests

from .data import UA

CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
# (option root, Cboe chain symbol) per underlying
CHAINS = {
    "SPX": [("XSP", "_XSP"), ("SPY", "SPY"), ("SPXW", "_SPX")],
    "NDX": [("NDXP", "_NDX"), ("QQQ", "QQQ")],
}
_OCC = re.compile(r"([A-Z]+)(\d{6})([CP])(\d{8})")


def load_chain(sym: str) -> pd.DataFrame:
    """sym: '_XSP', 'SPY', '_SPX' (SPX + SPXW), '_NDX' (NDX + NDXP), 'QQQ' or '_XND'."""
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
    x = x[x["z"].between(2.3, 3.9)]
    half = x["half_spread"] / x["spot"] * 1e4
    mid = x["mid"] / x["spot"] * 1e4
    a, b = np.linalg.lstsq(np.vstack([np.ones(len(x)), mid]).T, half, rcond=None)[0]
    return dict(root=root, n=len(x), hs_fixed_bps=round(a, 3), hs_pct_mid=round(b, 3),
                median_half_pct_mid=round(float(x["half_pct_mid"].median()), 3))


def market_check(signal: pd.DataFrame, underlying: str = "SPX", index_spot: float | None = None) -> tuple[pd.DataFrame, str]:
    """Listed puts closest to each of tomorrow's strikes, per instrument (long format).

    ``signal`` is the output of strategy.next_day_signal (index strikes and model
    premia). Mids are converted to index points so they compare with the model.
    """
    rows, asof = [], "n/a"
    for root, sym in CHAINS[underlying]:
        ch = load_chain(sym)
        asof = str(ch["asof"].iloc[0])
        ref = index_spot or signal["ref_spot"].iloc[0]
        ratio = ch["spot"].iloc[0] / ref if root not in ("SPXW", "NDXP") else 1.0
        step = 5.0 if root == "SPXW" else (10.0 if root == "NDXP" else 1.0)
        for _, sg in signal.iterrows():
            k = np.floor(sg["target_moneyness_pct"] / 100 * ch["spot"].iloc[0] / step) * step
            q = quotes_near(ch, root, sg["expiry"], k, width=0)
            if q.empty:
                continue
            q = q.iloc[0]
            rows.append({
                "Expiry": sg["expiry"], "Instrument": root, "Strike": f"{q['strike']:g}",
                "Bid / ask": f"{q['bid']:.2f} / {q['ask']:.2f}",
                "Mid (index pts)": f"{q['mid'] / ratio:.2f}",
                "Model premium (index pts)": f"{sg['est_premium_pts']:.2f}",
                "Half-spread % mid": f"{q['half_pct_mid'] * 100:.0f}%",
                "Open interest": int(q["open_interest"]), "Volume": int(q["volume"]),
            })
    out = pd.DataFrame(rows)
    if len(out):
        out = out.sort_values(["Expiry", "Instrument"]).set_index("Expiry")
    return out, asof


def main():
    import sys

    from .costs import VENUES
    from .data import load_all
    from .strategy import default_params, next_day_signal

    underlying = sys.argv[1] if len(sys.argv) > 1 else "SPX"
    df = load_all(underlying)
    atm = df["atm_vol"].iloc[-1] / 100
    sig = next_day_signal(df, default_params(underlying))
    pd.set_option("display.width", 200)
    tbl, asof = market_check(sig, underlying)
    print(f"Cboe chains as of {asof} | {underlying} close {df['close'].iloc[-1]:,.2f} | "
          f"{df.attrs['vol_index_name']} {df['vol_index'].iloc[-1]:.2f}\n")
    print(tbl.to_string())
    print("\nSpread fits (compare with costs.py):")
    fits = [fit_spread(load_chain(sym), root, atm) for root, sym in CHAINS[underlying]]
    print(pd.DataFrame(fits).to_string(index=False))
    print({k: (v.hs_fixed_bps, v.hs_pct_mid) for k, v in VENUES.items() if v.underlying == underlying})


if __name__ == "__main__":
    main()
