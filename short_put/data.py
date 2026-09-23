"""Market data loaders.

* VolVue API  -> NDX implied-vol surface summary (ATM 10d put IV, 30d mean IV, skew)
* CBOE        -> VXN index history (Nasdaq-100 volatility index)
* Yahoo chart -> ^NDX daily OHLC and ^IRX (13w T-bill) for the cash rate

All downloads are cached as CSV under ``cache/`` (git-ignored) so reruns are
fast and do not burn API quota. Pass ``refresh=True`` to re-download.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pandas as pd
import requests

VOLVUE_URL = "https://api.volvue.com/query"
CBOE_VXN_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
UA = {"User-Agent": "Mozilla/5.0 (short-put-tracker)"}

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


def _cached(name: str, loader, refresh: bool) -> pd.DataFrame:
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{name}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    df = loader()
    df.to_csv(path)
    return df


# --------------------------------------------------------------------------- VolVue
def volvue_query(sql: str, api_key: str | None = None) -> pd.DataFrame:
    """Run an SQL query against the VolVue API and return a DataFrame."""
    api_key = api_key or os.environ.get("VOLVUE_API_KEY")
    if not api_key:
        raise RuntimeError("Set the VOLVUE_API_KEY environment variable.")
    r = requests.get(
        VOLVUE_URL,
        params={"apiKey": api_key, "format": "json", "data": sql},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"VolVue API error {r.status_code}: {r.text[:300]}")
    payload = r.json()
    return pd.DataFrame(payload["records"], columns=payload["columnNames"])


def load_volvue_ndx(start: str = "2004-01-01", refresh: bool = False) -> pd.DataFrame:
    """NDX implied vols (in vol points) from VolVue."""

    def _load():
        df = volvue_query(
            "SELECT date, iv_put_10, iv_mean_10, iv_put_20, iv_mean_30, iv_skew_10, iv_skew_30 "
            f'FROM data WHERE ticker = "NDX" AND date >= "{start}" ORDER BY date ASC'
        )
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date").apply(pd.to_numeric, errors="coerce")

    return _cached("volvue_ndx", _load, refresh)


# --------------------------------------------------------------------------- CBOE VXN
def load_vxn(refresh: bool = False) -> pd.Series:
    def _load():
        r = requests.get(CBOE_VXN_URL, headers=UA, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
        return df.set_index("DATE")[["CLOSE"]].rename(columns={"CLOSE": "vxn"})

    return _cached("vxn", _load, refresh)["vxn"]


# --------------------------------------------------------------------------- Yahoo
def _yahoo_daily(symbol: str, start: str) -> pd.DataFrame:
    p1 = int(pd.Timestamp(start).timestamp())
    p2 = int(pd.Timestamp.now().timestamp()) + 86400
    r = requests.get(
        YAHOO_URL.format(symbol=symbol),
        params={"period1": p1, "period2": p2, "interval": "1d", "events": "history"},
        headers=UA,
        timeout=60,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    idx = (
        pd.to_datetime(res["timestamp"], unit="s", utc=True)
        .tz_convert("America/New_York")
        .tz_localize(None)
        .normalize()
    )
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close")}, index=idx)
    df = df[~df.index.duplicated(keep="last")]
    # Drop today's bar while the session is still running (incomplete OHLC)
    now_ny = pd.Timestamp.now(tz="America/New_York")
    if now_ny.hour < 17 and len(df) and df.index[-1] == now_ny.tz_localize(None).normalize():
        df = df.iloc[:-1]
    return df


def load_ndx(start: str = "2004-01-01", refresh: bool = False) -> pd.DataFrame:
    """^NDX daily OHLC. Occasional null Yahoo index bars are patched from QQQ,
    then from Nasdaq-100 futures (NQ=F), each scaled by the previous day's ratio."""

    def _load():
        ndx = _yahoo_daily("^NDX", start)
        exchange_days = ndx.index
        for proxy_sym in ("QQQ", "NQ=F"):
            proxy = _yahoo_daily(proxy_sym, start)
            if proxy_sym == "QQQ":
                exchange_days = exchange_days.union(proxy.index)
            idx = ndx.index.union(proxy.index)
            ratio = (ndx["close"] / proxy["close"]).reindex(idx).ffill().shift(1)
            ndx = ndx.reindex(idx)
            for c in ("open", "high", "low", "close"):
                ndx[c] = ndx[c].fillna(proxy[c].reindex(idx) * ratio)
        # keep exchange days only (futures also trade on some exchange holidays)
        ndx = ndx.loc[ndx.index.isin(exchange_days)]
        return ndx.dropna(subset=["close"])

    return _cached("ndx", _load, refresh)


def load_tbill(start: str = "2004-01-01", refresh: bool = False) -> pd.Series:
    """13-week T-bill yield in decimal (e.g. 0.05 = 5%)."""
    df = _cached("irx", lambda: _yahoo_daily("^IRX", start), refresh)
    return df["close"] / 100.0


def load_all(start: str = "2004-01-01", refresh: bool = False) -> pd.DataFrame:
    """Aligned daily panel on NDX trading days."""
    ndx = load_ndx(start, refresh)
    vv = load_volvue_ndx(start, refresh)
    vxn = load_vxn(refresh)
    rf = load_tbill(start, refresh)

    df = ndx.join(vv, how="left").join(vxn, how="left").join(rf.rename("rf"), how="left")
    # VXN before CBOE's CSV history (Sep-2009) or on gaps: use VolVue NDX 30d mean IV
    # rescaled by the overlap-period ratio (VXN is a 30d NDX variance-swap-style index).
    overlap = df[["vxn", "iv_mean_30"]].dropna()
    ratio = (overlap["vxn"] / overlap["iv_mean_30"]).median() if len(overlap) else 1.0
    df["vxn_src"] = "CBOE"
    miss = df["vxn"].isna()
    df.loc[miss, "vxn"] = df.loc[miss, "iv_mean_30"] * ratio
    df.loc[miss, "vxn_src"] = "VolVue proxy"
    df.attrs["vxn_proxy_ratio"] = float(ratio)

    # ATM short-dated vol used for option pricing: 10d ATM put IV -> fallbacks
    df["atm_vol"] = (
        df["iv_put_10"].fillna(df["iv_mean_10"]).fillna(df["iv_put_20"]).fillna(df["iv_mean_30"])
    )
    df[["vxn", "rf"]] = df[["vxn", "rf"]].ffill()
    # Clean bad prints (e.g. 0.15 or 157 vol near monthly expiries): if the ATM
    # vol / VXN ratio is outside [0.45, 1.6], use VXN x rolling median ratio.
    ratio_iv = df["atm_vol"] / df["vxn"]
    ok = ratio_iv.between(0.45, 1.6)
    med = ratio_iv.where(ok).rolling(60, min_periods=5).median().ffill().fillna(ratio_iv[ok].median())
    df["atm_vol_raw"] = df["atm_vol"]
    df["atm_vol"] = df["atm_vol"].where(ok, df["vxn"] * med)
    df.attrs["atm_vol_cleaned_days"] = int((~ok).sum())
    # Some early Yahoo index rows have open==0 / missing: fall back to previous close
    df.loc[(df["open"] <= 0) | df["open"].isna(), "open"] = df["close"].shift(1)
    df[["high", "low"]] = df[["high", "low"]].where(df[["high", "low"]] > 0)
    df["high"] = df["high"].fillna(df[["open", "close"]].max(axis=1))
    df["low"] = df["low"].fillna(df[["open", "close"]].min(axis=1))
    return df.dropna(subset=["close", "atm_vol", "vxn"])


if __name__ == "__main__":  # quick smoke test
    panel = load_all()
    print(panel.tail())
    print(json.dumps(panel.attrs, indent=2))
