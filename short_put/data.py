"""Market data loaders.

Two underlyings are supported (see ``UNDERLYINGS``):

* SPX: S&P 500 index, strike driver VIX  (traded via XSP / SPY / SPXW options)
* NDX: Nasdaq-100 index, strike driver VXN (traded via NDXP / QQQ options)

Sources:
* VolVue API  -> implied-vol surface summary of the index (ATM 10d put IV, 30d mean IV)
* CBOE        -> volatility index history (VIX / VXN)
* Yahoo chart -> index daily OHLC (patched from the ETF and futures), ^IRX (13w T-bill)

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
CBOE_VOL_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
UA = {"User-Agent": "Mozilla/5.0 (short-put-tracker)"}

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"

UNDERLYINGS = {
    "SPX": dict(name="S&P 500", yahoo="^GSPC", proxies=("SPY", "ES=F"), volvue="SPX", vol_index="VIX", vol_3m="VIX3M"),
    "NDX": dict(name="Nasdaq-100", yahoo="^NDX", proxies=("QQQ", "NQ=F"), volvue="NDX", vol_index="VXN", vol_3m=None),
}


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


def load_volvue(ticker: str, start: str = "2004-01-01", refresh: bool = False) -> pd.DataFrame:
    """Index implied vols (in vol points) from VolVue."""

    def _load():
        df = volvue_query(
            "SELECT date, iv_put_10, iv_mean_10, iv_put_20, iv_mean_30, iv_mean_90, iv_skew_10, iv_skew_30 "
            f'FROM data WHERE ticker = "{ticker}" AND date >= "{start}" ORDER BY date ASC'
        )
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date").apply(pd.to_numeric, errors="coerce")

    df = _cached(f"volvue_{ticker.lower()}", _load, refresh)
    if "iv_mean_90" not in df:  # cache written by an older version: re-download
        df = _cached(f"volvue_{ticker.lower()}", _load, True)
    return df


# --------------------------------------------------------------------------- CBOE vol index
def load_vol_index(name: str, refresh: bool = False) -> pd.DataFrame:
    """CBOE volatility index open and close (e.g. 'VIX', 'VXN', 'VIX3M')."""

    def _load():
        r = requests.get(CBOE_VOL_URL.format(name=name), headers=UA, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
        return df.set_index("DATE")[["OPEN", "CLOSE"]].rename(columns={"OPEN": "open", "CLOSE": "close"})

    df = _cached(name.lower(), _load, refresh)
    if "open" not in df:  # cache written by an older version: re-download
        df = _cached(name.lower(), _load, True)
    return df


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


def load_index(underlying: str, start: str = "2004-01-01", refresh: bool = False) -> pd.DataFrame:
    """Index daily OHLC. Occasional null Yahoo index bars are patched from the
    ETF (SPY / QQQ), then from index futures (ES=F / NQ=F), each scaled by the
    previous day's ratio."""
    cfg = UNDERLYINGS[underlying]

    def _load():
        ndx = _yahoo_daily(cfg["yahoo"], start)
        exchange_days = ndx.index
        for n, proxy_sym in enumerate(cfg["proxies"]):
            proxy = _yahoo_daily(proxy_sym, start)
            if n == 0:  # the ETF trades on exchange days only
                exchange_days = exchange_days.union(proxy.index)
            idx = ndx.index.union(proxy.index)
            ratio = (ndx["close"] / proxy["close"]).reindex(idx).ffill().shift(1)
            ndx = ndx.reindex(idx)
            for c in ("open", "high", "low", "close"):
                ndx[c] = ndx[c].fillna(proxy[c].reindex(idx) * ratio)
        # keep exchange days only (futures also trade on some exchange holidays)
        ndx = ndx.loc[ndx.index.isin(exchange_days)]
        return ndx.dropna(subset=["close"])

    return _cached(underlying.lower(), _load, refresh)


def load_tbill(start: str = "2004-01-01", refresh: bool = False) -> pd.Series:
    """13-week T-bill yield in decimal (e.g. 0.05 = 5%)."""
    df = _cached("irx", lambda: _yahoo_daily("^IRX", start), refresh)
    return df["close"] / 100.0


def load_all(underlying: str = "SPX", start: str = "2004-01-01", refresh: bool = False) -> pd.DataFrame:
    """Aligned daily panel on the index's trading days.

    Columns: open/high/low/close, vol_index (VIX or VXN), atm_vol (VolVue 10d
    ATM put IV, cleaned), rf (13w T-bill), plus raw VolVue fields.
    """
    cfg = UNDERLYINGS[underlying]
    px = load_index(underlying, start, refresh)
    vv = load_volvue(cfg["volvue"], start, refresh)
    vol = load_vol_index(cfg["vol_index"], refresh).rename(columns={"open": "vol_index_open", "close": "vol_index"})
    rf = load_tbill(start, refresh)

    df = px.join(vv, how="left").join(vol, how="left").join(rf.rename("rf"), how="left")
    if cfg["vol_3m"]:
        df = df.join(load_vol_index(cfg["vol_3m"], refresh)["close"].rename("vol_3m"), how="left")
    else:
        df["vol_3m"] = float("nan")
    # Gaps in the CBOE history: use VolVue 30d mean IV rescaled by the overlap
    # ratio (the vol index is a 30d variance-swap-style index on the same underlying).
    overlap = df[["vol_index", "iv_mean_30"]].dropna()
    ratio = (overlap["vol_index"] / overlap["iv_mean_30"]).median() if len(overlap) else 1.0
    df["vol_src"] = "CBOE"
    miss = df["vol_index"].isna()
    df.loc[miss, "vol_index"] = df.loc[miss, "iv_mean_30"] * ratio
    df.loc[miss, "vol_src"] = "VolVue proxy"

    # ATM short-dated vol used for option pricing: 10d ATM put IV -> fallbacks
    df["atm_vol"] = (
        df["iv_put_10"].fillna(df["iv_mean_10"]).fillna(df["iv_put_20"]).fillna(df["iv_mean_30"])
    )
    df[["vol_index", "rf"]] = df[["vol_index", "rf"]].ffill()
    df["vol_index_open"] = df["vol_index_open"].fillna(df["vol_index"].shift(1))
    # 3-month vol index (term structure). Before its CBOE history starts, proxy it
    # with vol_index x VolVue 90d/30d mean IV, scaled on the overlap period.
    proxy_3m = df["vol_index"] * df["iv_mean_90"] / df["iv_mean_30"]
    ov = df[["vol_3m"]].join(proxy_3m.rename("p")).dropna()
    if len(ov):
        df["vol_3m"] = df["vol_3m"].fillna(proxy_3m * (ov["vol_3m"] / ov["p"]).median())
    df["vol_3m"] = df["vol_3m"].ffill()
    # Clean bad prints (e.g. 0.15 or 157 vol near monthly expiries): if the ATM
    # vol / vol-index ratio is outside [0.45, 1.6], use vol index x rolling median ratio.
    ratio_iv = df["atm_vol"] / df["vol_index"]
    ok = ratio_iv.between(0.45, 1.6)
    med = ratio_iv.where(ok).rolling(60, min_periods=5).median().ffill().fillna(ratio_iv[ok].median())
    df["atm_vol_raw"] = df["atm_vol"]
    df["atm_vol"] = df["atm_vol"].where(ok, df["vol_index"] * med)
    # Some early Yahoo index rows have open==0 / missing: fall back to previous close
    df.loc[(df["open"] <= 0) | df["open"].isna(), "open"] = df["close"].shift(1)
    df[["high", "low"]] = df[["high", "low"]].where(df[["high", "low"]] > 0)
    df["high"] = df["high"].fillna(df[["open", "close"]].max(axis=1))
    df["low"] = df["low"].fillna(df[["open", "close"]].min(axis=1))
    df = df.dropna(subset=["close", "atm_vol", "vol_index"])
    df.attrs.update(
        underlying=underlying, underlying_name=cfg["name"], vol_index_name=cfg["vol_index"],
        vol_proxy_ratio=float(ratio), atm_vol_cleaned_days=int((~ok).sum()),
    )
    return df


if __name__ == "__main__":  # quick smoke test
    panel = load_all("SPX")
    print(panel[["open", "close", "vol_index", "atm_vol", "rf"]].tail())
    print(json.dumps(panel.attrs, indent=2))
