"""
Trade-level backtest of the Pine entry, plus the point-in-time features a
stock-selection rule could use.

Design rules, all chosen so that *selection* is the only thing that varies
between runs:

* The entry is the unmodified `buySignal` from screener/strategy.py.
* The exit is fixed and stated up front. The script defines no exits, and
  the repo's earlier study found exit style swings CAGR by >30 points, so
  it is held constant here rather than tuned.
* A signal on bar t fills at the OPEN of bar t+1. No same-bar fills.
* When a bar's range spans both stop and target, the STOP is assumed to
  fill first.
* Every selection feature attached to a trade is computed from bars
  strictly BEFORE the entry bar. No look-ahead.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screener import indicators as ta  # noqa: E402
from screener.strategy import Params, WARMUP_BARS, compute  # noqa: E402

BARS = ROOT / "research" / "bars"

# 0.10% STT each leg + ~0.09% brokerage/GST/stamp + ~0.10% slippage
COST_ROUND_TRIP = 0.0035

BARS_PER_DAY = 6


@dataclass
class Exits:
    """Fixed exit rule. Not tuned - held constant across every test."""
    atr_len: int = 14
    stop_atr: float = 1.5
    target_atr: float = 3.0      # 2R
    max_bars: int = 140          # ~23 sessions


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return ta.rma(ta.true_range(df["high"], df["low"], df["close"]), n)


def load(sym: str) -> pd.DataFrame | None:
    p = BARS / f"{sym}.parquet"
    if not p.exists():
        return None
    d = pd.read_parquet(p)
    if len(d) < WARMUP_BARS + 300:
        return None
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    return d.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def features(r: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time selection features, all shifted so bar t sees only t-1.

    Everything here is computable from the stock's own candles - no index
    membership, no sector tags, no vendor lists.
    """
    c, h, l = r["close"], r["high"], r["low"]
    f = pd.DataFrame(index=r.index)

    # --- trend location (daily equivalents on a 6-bar session) ---------
    f["sma50d"] = c.rolling(50 * BARS_PER_DAY).mean()
    f["sma200d"] = c.rolling(200 * BARS_PER_DAY).mean()
    f["above50"] = c > f["sma50d"]
    f["above200"] = c > f["sma200d"]

    # --- momentum -----------------------------------------------------
    f["ret_1m"] = c.pct_change(21 * BARS_PER_DAY) * 100
    f["ret_3m"] = c.pct_change(63 * BARS_PER_DAY) * 100
    f["ret_6m"] = c.pct_change(126 * BARS_PER_DAY) * 100
    f["ret_12m"] = c.pct_change(252 * BARS_PER_DAY) * 100

    # --- trend quality: how straight is the advance -------------------
    # R^2 of a linear fit to log price over 60 sessions. High = orderly
    # trend, low = chop. This is the "trending stock" idea made numeric.
    win = 60 * BARS_PER_DAY
    lg = np.log(c.clip(lower=1e-9))
    x = np.arange(win)
    xm = x.mean()
    sxx = ((x - xm) ** 2).sum()
    ym = lg.rolling(win).mean()
    sxy = lg.rolling(win).apply(lambda v: ((x - xm) * (v - v.mean())).sum(), raw=True)
    syy = lg.rolling(win).apply(lambda v: ((v - v.mean()) ** 2).sum(), raw=True)
    f["r2"] = (sxy ** 2) / (sxx * syy.replace(0, np.nan))
    f["slope_ann"] = (sxy / sxx) * (252 * BARS_PER_DAY) * 100   # % per year

    # --- volatility ---------------------------------------------------
    a = atr(r, 14)
    f["atr_pct"] = a / c * 100
    f["atr"] = a

    # --- distance from the 52-week high -------------------------------
    f["off_high"] = (c / c.rolling(252 * BARS_PER_DAY).max() - 1) * 100

    # --- liquidity ----------------------------------------------------
    f["turnover_cr"] = (c * r["volume"] / 1e7).rolling(20 * BARS_PER_DAY).mean() * BARS_PER_DAY

    # --- daily-equivalent ADX (the entry already uses the 1H one) -----
    _, _, adx_d = ta.dmi(h, l, c, 14 * BARS_PER_DAY, 14 * BARS_PER_DAY)
    f["adx_slow"] = adx_d

    # nothing may be read on the bar it is computed from
    return f.shift(1)


def intraday_features(r: pd.DataFrame) -> pd.DataFrame:
    """Fast features, known the moment the signal bar closes.

    These are NOT shifted. The signal is computed from bar i's close and the
    fill happens at bar i+1's open, so everything bar i knows about itself is
    legitimately available. Shifting them would throw away exactly the
    information an intraday selection rule is supposed to use.
    """
    ts = r["timestamp"]
    day = ts.dt.normalize()
    g = r.groupby(day)
    f = pd.DataFrame(index=r.index)

    f["bar_of_day"] = g.cumcount()                      # 0 = 09:15 ... 5 = 14:15
    f["hour"] = ts.dt.hour

    day_open = g["open"].transform("first")
    first = f["bar_of_day"] == 0
    prev_close = r["close"].shift(1).where(first)
    f["gap_pct"] = (day_open / prev_close.groupby(day).transform("first") - 1) * 100
    f["day_move"] = (r["close"] / day_open - 1) * 100   # move so far today

    dh = g["high"].cummax()
    dl = g["low"].cummin()
    rng = (dh - dl).replace(0, np.nan)
    f["range_pos"] = (r["close"] - dl) / rng * 100      # 100 = at the day's high
    f["day_range_pct"] = rng / r["close"] * 100

    pv = (r["close"] * r["volume"]).groupby(day).cumsum()
    vv = r["volume"].groupby(day).cumsum().replace(0, np.nan)
    f["dist_vwap"] = (r["close"] / (pv / vv) - 1) * 100

    # volume on this bar against its own recent norm (20 sessions)
    f["rel_vol"] = r["volume"] / r["volume"].rolling(20 * BARS_PER_DAY).mean()

    # range expansion: today's range so far against the usual bar range
    f["range_exp"] = rng / atr(r, 14)

    up = (r["close"] > r["close"].shift(1)).astype(int)
    f["consec_up"] = up * (up.groupby((up != up.shift()).cumsum()).cumcount() + 1)

    return f


def market_index(bars_dir=BARS, min_symbols: int = 50) -> pd.Series:
    """Equal-weight hourly index of the panel, for regime features.

    Built from the same 400 names, which are today's most liquid - so it
    carries survivorship bias and is only used as a coarse up/down regime
    read, never as a return benchmark.
    """
    frames = []
    for p in sorted(bars_dir.glob("*.parquet")):
        d = pd.read_parquet(p, columns=["timestamp", "close"])
        d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
        s = d.set_index("timestamp")["close"]
        s = s[~s.index.duplicated()]
        frames.append(s.pct_change())
    wide = pd.concat(frames, axis=1)
    ew = wide.mean(axis=1, skipna=True)
    ew = ew[wide.notna().sum(axis=1) >= min_symbols]
    return (1 + ew.fillna(0)).cumprod()


def market_features(mkt: pd.Series) -> pd.DataFrame:
    f = pd.DataFrame(index=mkt.index)
    f["mkt_ret_5d"] = mkt.pct_change(5 * BARS_PER_DAY) * 100
    f["mkt_ret_20d"] = mkt.pct_change(20 * BARS_PER_DAY) * 100
    f["mkt_above_50d"] = (mkt > mkt.rolling(50 * BARS_PER_DAY).mean()).astype(float)
    return f.shift(1)


def trades_for(sym: str, p: Params, ex: Exits, mktf: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every entry the script fires on this symbol, with its outcome."""
    d = load(sym)
    if d is None:
        return pd.DataFrame()

    r = compute(d, p)
    f = features(r)
    intra = intraday_features(r)
    a = atr(r, ex.atr_len)

    if mktf is not None:
        mk = mktf.reindex(r["timestamp"].dt.tz_convert("UTC")).reset_index(drop=True)
    else:
        mk = pd.DataFrame(index=r.index)

    o = r["open"].to_numpy()
    h = r["high"].to_numpy()
    lo = r["low"].to_numpy()
    c = r["close"].to_numpy()
    ts = r["timestamp"].to_numpy()
    sig = r["buySignal"].to_numpy(dtype=bool)
    av = a.to_numpy()
    n = len(r)

    out = []
    i = WARMUP_BARS
    while i < n - 2:
        if not sig[i] or not np.isfinite(av[i]) or av[i] <= 0:
            i += 1
            continue
        entry_i = i + 1                       # fill at the next bar's open
        entry = o[entry_i]
        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue
        stop_dist = ex.stop_atr * av[i]
        stop = entry - stop_dist
        target = entry + ex.target_atr * av[i]

        exit_i, exit_px, why = None, None, None
        for j in range(entry_i, min(entry_i + ex.max_bars, n)):
            if lo[j] <= stop:                 # stop assumed to fill first
                exit_i, exit_px, why = j, stop, "stop"
                break
            if h[j] >= target:
                exit_i, exit_px, why = j, target, "target"
                break
        if exit_i is None:
            exit_i = min(entry_i + ex.max_bars, n - 1)
            exit_px, why = c[exit_i], "time"

        gross = (exit_px / entry - 1)
        out.append({
            "symbol": sym,
            "signal_time": ts[i],
            "entry_time": ts[entry_i],
            "exit_time": ts[exit_i],
            "bars_held": exit_i - entry_i,
            "entry": entry, "exit": exit_px, "why": why,
            "stop": stop, "target": target, "stop_dist": stop_dist,
            "stop_pct": stop_dist / entry * 100,
            "ret_gross": gross * 100,
            "ret_net": (gross - COST_ROUND_TRIP) * 100,
            # gross R, and R after the 0.35% round trip - the difference is
            # the whole argument, so both are carried rather than just one
            "r_multiple": (exit_px - entry) / stop_dist,
            "r_net": (gross - COST_ROUND_TRIP) * entry / stop_dist,
            **{k: (float(f[k].iloc[i]) if pd.notna(f[k].iloc[i]) else np.nan)
               for k in ("ret_1m", "ret_3m", "ret_6m", "ret_12m", "r2", "slope_ann",
                         "atr_pct", "off_high", "turnover_cr", "adx_slow")},
            "above50": bool(f["above50"].iloc[i]) if pd.notna(f["above50"].iloc[i]) else False,
            "above200": bool(f["above200"].iloc[i]) if pd.notna(f["above200"].iloc[i]) else False,
            "adx_1h": float(r["adx"].iloc[i]),
            "di_spread": float(r["plusDI"].iloc[i] - r["minusDI"].iloc[i]),
            **{k: (float(intra[k].iloc[i]) if pd.notna(intra[k].iloc[i]) else np.nan)
               for k in ("bar_of_day", "gap_pct", "day_move", "range_pos",
                         "day_range_pct", "dist_vwap", "rel_vol", "range_exp",
                         "consec_up")},
            **{k: (float(mk[k].iloc[i]) if (k in mk and pd.notna(mk[k].iloc[i])) else np.nan)
               for k in ("mkt_ret_5d", "mkt_ret_20d", "mkt_above_50d")},
        })
        i = exit_i + 1                        # one position per symbol at a time
    return pd.DataFrame(out)


def summarise(t: pd.DataFrame, label: str = "") -> dict:
    if t.empty:
        return {"label": label, "trades": 0}
    net = t["ret_net"]
    wins = net > 0
    gp = net[wins].sum()
    gl = -net[~wins].sum()
    return {
        "label": label,
        "trades": len(t),
        "win_pct": 100 * wins.mean(),
        "avg_net": net.mean(),
        "median_net": net.median(),
        "total_net": net.sum(),
        "expR_gross": t["r_multiple"].mean(),
        "expR_net": t["r_net"].mean(),
        "profit_factor": (gp / gl) if gl > 0 else np.inf,
        "avg_bars": t["bars_held"].mean(),
    }
