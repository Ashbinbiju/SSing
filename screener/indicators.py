"""
Pine Script v6 `ta.*` functions, reproduced exactly.

Every function here mirrors the behaviour of the TradingView built-in of
the same name, including its warm-up behaviour (leading `na` values) and
its seeding rule, so the screener fires on the same bars the indicator
does on the chart.

Notes on the two rules that actually matter:

* `ta.ema` / `ta.rma` return `na` until `length` values have arrived and
  seed the recursion with the SMA of those first `length` values.
* `ta.pivotlow` confirms a pivot `right` bars late and requires the
  candidate to be strictly lower than every bar on both sides.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _s(x) -> pd.Series:
    return x if isinstance(x, pd.Series) else pd.Series(x)


def sma(src, length: int) -> pd.Series:
    """ta.sma"""
    return _s(src).rolling(length, min_periods=length).mean()


def _recursive(src: pd.Series, alpha: float, length: int) -> pd.Series:
    """Shared body of ta.ema / ta.rma.

    Pine ignores leading `na`s, seeds with the SMA of the first `length`
    valid values and only then starts the recursion.  Building a seeded
    series and handing it to `ewm(adjust=False)` reproduces that exactly
    and stays vectorised.
    """
    s = _s(src).astype("float64")
    valid = s.notna().to_numpy()
    if valid.sum() < length:
        return pd.Series(np.nan, index=s.index, dtype="float64")

    first = int(np.argmax(valid))                  # first non-na position
    body = s.iloc[first:]
    seed_pos = first + length - 1                  # bar the SMA seed lands on

    seeded = s.copy()
    seeded.iloc[:seed_pos] = np.nan
    seeded.iloc[seed_pos] = body.iloc[:length].mean()
    return seeded.ewm(alpha=alpha, adjust=False, ignore_na=False).mean()


def ema(src, length: int) -> pd.Series:
    """ta.ema — alpha = 2 / (length + 1)"""
    return _recursive(src, 2.0 / (length + 1.0), length)


def rma(src, length: int) -> pd.Series:
    """ta.rma — Wilder smoothing, alpha = 1 / length"""
    return _recursive(src, 1.0 / length, length)


def macd(src, fast: int, slow: int, signal: int):
    """ta.macd -> (macdLine, signalLine, histogram)"""
    line = ema(src, fast) - ema(src, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def true_range(high, low, close) -> pd.Series:
    """`ta.tr` (the variable, i.e. ta.tr(false)) — na on the first bar."""
    h, l, c = _s(high), _s(low), _s(close)
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    return tr


def dmi(high, low, close, di_length: int, adx_smoothing: int):
    """ta.dmi -> (+DI, -DI, ADX), the Pine reference implementation."""
    h, l, c = _s(high), _s(low), _s(close)

    up = h.diff()
    down = -l.diff()

    plus_dm = np.where(up.isna(), np.nan, np.where((up > down) & (up > 0), up, 0.0))
    minus_dm = np.where(down.isna(), np.nan, np.where((down > up) & (down > 0), down, 0.0))
    plus_dm = pd.Series(plus_dm, index=h.index)
    minus_dm = pd.Series(minus_dm, index=h.index)

    trur = rma(true_range(h, l, c), di_length)
    # fixnan() holds the last non-na value forward
    plus = (100.0 * rma(plus_dm, di_length) / trur).ffill()
    minus = (100.0 * rma(minus_dm, di_length) / trur).ffill()

    total = plus + minus
    dx = (plus - minus).abs() / total.where(total != 0, 1.0)
    adx = 100.0 * rma(dx, adx_smoothing)
    return plus, minus, adx


def pivotlow(src, left: int, right: int) -> pd.Series:
    """ta.pivotlow(src, left, right).

    Returns the pivot value on the bar where it is *confirmed*, i.e. the
    value of bar `t - right` appears at index `t`; every other bar is na.
    """
    s = _s(src).astype("float64")
    cand = s.shift(right)

    # strictly lower than the `left` bars before it
    left_min = s.shift(right + 1).rolling(left, min_periods=left).min()
    # strictly lower than the `right` bars after it
    right_min = s.rolling(right, min_periods=right).min()

    ok = (cand < left_min) & (cand < right_min)
    return cand.where(ok)


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """ta.crossover"""
    a, b = _s(a), _s(b)
    return (a > b) & (a.shift(1) <= b.shift(1))
