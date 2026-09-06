"""
Literal port of "Swing Trading - EMA 4/9 + MACD + DMI/ADX" (Pine v6).

Nothing about the signal logic is changed, re-ordered or "improved".
Every input of the indicator is exposed on `Params` with the same default
the script ships with, and every intermediate series the info-table shows
is returned so the screener can display exactly what the chart displays.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import indicators as ta


@dataclass
class Params:
    # 1. EMA Trend & Entry
    fastEmaLength: int = 4
    slowEmaLength: int = 9
    requirePriceAboveEMA: bool = True
    useEMACross: bool = True
    # 2. MACD Confirmation
    macdFast: int = 12
    macdSlow: int = 26
    macdSignalLength: int = 9
    macdConfirmationMode: str = "MACD Above Signal"   # | MACD Bullish Cross | Either
    # 3. MACD Bullish Divergence
    useDivergence: bool = True
    divergencePivotLength: int = 5
    divergenceLookback: int = 60
    requirePriceHigherLow: bool = True
    requireMACDLowerLow: bool = True
    # 4. DMI / ADX
    dmiLength: int = 14
    adxSmoothing: int = 14
    minimumADX: float = 25.0
    requireDIConfirmation: bool = True
    # 5. Signal Logic
    signalMode: str = "EMA Cross + MACD"              # | MACD Divergence + EMA | Either
    requireADX: bool = True
    requireTrend: bool = True
    confirmOnClose: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d) -> "Params":
        if not d:
            return cls()
        fields = cls.__dataclass_fields__
        clean = {}
        for k, v in d.items():
            if k not in fields:
                continue
            kind = fields[k].type
            if kind is int or kind == "int":
                v = int(v)
            elif kind is float or kind == "float":
                v = float(v)
            elif kind is bool or kind == "bool":
                v = v.lower() == "true" if isinstance(v, str) else bool(v)
            clean[k] = v
        return cls(**clean)


# Bars of history discarded before any signal is trusted, so the ADX and
# MACD recursions are fully warmed up.
WARMUP_BARS = 260


def _divergence_state(price_pivot, macd_pivot, pivot_len):
    """The `var` block of the script, unrolled.

    The Pine code only updates its stored pivots on bars where *both* a
    price pivot low and a MACD pivot low confirm, so those bars are the
    only state transitions; every bar in between just holds the last
    value.
    """
    n = len(price_pivot)
    idx = price_pivot.index
    both = price_pivot.notna().to_numpy() & macd_pivot.notna().to_numpy()
    events = np.flatnonzero(both)

    cur_p = np.full(n, np.nan)
    cur_m = np.full(n, np.nan)
    cur_b = np.full(n, np.nan)
    prev_p = np.full(n, np.nan)
    prev_m = np.full(n, np.nan)
    prev_b = np.full(n, np.nan)

    pv = price_pivot.to_numpy()
    mv = macd_pivot.to_numpy()

    for k, t in enumerate(events):
        end = events[k + 1] if k + 1 < len(events) else n
        cur_p[t:end] = pv[t]
        cur_m[t:end] = mv[t]
        cur_b[t:end] = t - pivot_len
        if k > 0:
            q = events[k - 1]
            prev_p[t:end] = pv[q]
            prev_m[t:end] = mv[q]
            prev_b[t:end] = q - pivot_len

    def mk(a):
        return pd.Series(a, index=idx)

    return mk(cur_p), mk(cur_m), mk(cur_b), mk(prev_p), mk(prev_m), mk(prev_b)


def compute(df, p=None):
    """Run the indicator over an OHLCV frame (oldest bar first).

    Expects columns: timestamp, open, high, low, close, volume.
    Returns the frame plus every series the Pine script computes.
    """
    p = p or Params()
    out = df.copy().reset_index(drop=True)
    close, high, low = out["close"], out["high"], out["low"]

    # ---- EMA -------------------------------------------------------
    out["ema4"] = ta.ema(close, p.fastEmaLength)
    out["ema9"] = ta.ema(close, p.slowEmaLength)
    out["emaBullCross"] = ta.crossover(out["ema4"], out["ema9"])
    out["emaBullTrend"] = out["ema4"] > out["ema9"]
    out["priceAboveEMA"] = close > out["ema9"]
    out["trendOK"] = (not p.requireTrend) | (
        out["emaBullTrend"] & ((not p.requirePriceAboveEMA) | out["priceAboveEMA"])
    )

    # ---- MACD ------------------------------------------------------
    line, sig, hist = ta.macd(close, p.macdFast, p.macdSlow, p.macdSignalLength)
    out["macdLine"], out["signalLine"], out["macdHistogram"] = line, sig, hist
    out["macdAboveSignal"] = line > sig
    out["macdBullCross"] = ta.crossover(line, sig)

    if p.macdConfirmationMode == "MACD Above Signal":
        out["macdOK"] = out["macdAboveSignal"]
    elif p.macdConfirmationMode == "MACD Bullish Cross":
        out["macdOK"] = out["macdBullCross"]
    else:
        out["macdOK"] = out["macdAboveSignal"] | out["macdBullCross"]

    # ---- DMI / ADX -------------------------------------------------
    plus, minus, adx = ta.dmi(high, low, close, p.dmiLength, p.adxSmoothing)
    out["plusDI"], out["minusDI"], out["adx"] = plus, minus, adx
    out["adxOK"] = (not p.requireADX) | (adx > p.minimumADX)
    out["diOK"] = (not p.requireDIConfirmation) | (plus > minus)
    out["dmiConfirmation"] = out["adxOK"] & out["diOK"]

    # ---- MACD bullish divergence -----------------------------------
    L = p.divergencePivotLength
    price_pivot = ta.pivotlow(low, L, L)
    macd_pivot = ta.pivotlow(line, L, L)
    cur_p, cur_m, cur_b, prev_p, prev_m, prev_b = _divergence_state(price_pivot, macd_pivot, L)

    out["currentPriceLow"], out["previousPriceLow"] = cur_p, prev_p
    out["currentMACDLow"], out["previousMACDLow"] = cur_m, prev_m

    bars_between = (cur_b - prev_b).where(cur_b.notna() & prev_b.notna())
    out["barsBetweenLows"] = bars_between

    out["priceHigherLow"] = prev_p.notna() & cur_p.notna() & (cur_p > prev_p)
    out["macdLowerLow"] = prev_m.notna() & cur_m.notna() & (cur_m < prev_m)

    in_range = bars_between.notna() & (bars_between <= p.divergenceLookback)
    out["bullishDivergence"] = (
        in_range
        & ((not p.requirePriceHigherLow) | out["priceHigherLow"])
        & ((not p.requireMACDLowerLow) | out["macdLowerLow"])
    )
    prev_div = out["bullishDivergence"].shift(1)
    prev_div = prev_div.where(prev_div.notna(), False).astype(bool)
    out["newBullishDivergence"] = out["bullishDivergence"] & ~prev_div

    # ---- entries ---------------------------------------------------
    out["emaMACDEntry"] = out["emaBullCross"] & out["macdOK"]
    out["divergenceEMAEntry"] = out["newBullishDivergence"] & out["emaBullTrend"]

    if p.signalMode == "EMA Cross + MACD":
        out["primarySignal"] = out["emaMACDEntry"]
    elif p.signalMode == "MACD Divergence + EMA":
        out["primarySignal"] = out["divergenceEMAEntry"]
    else:
        out["primarySignal"] = out["emaMACDEntry"] | out["divergenceEMAEntry"]

    raw = out["primarySignal"] & out["trendOK"] & out["dmiConfirmation"]
    out["rawBuySignal"] = raw
    # barstate.isconfirmed is true on every closed bar, and the screener
    # only ever evaluates closed bars.
    out["buySignal"] = raw

    out["strongSignal"] = (
        out["buySignal"]
        & out["emaBullTrend"]
        & out["macdAboveSignal"]
        & (adx > p.minimumADX)
        & (plus > minus)
    )
    out["strongTrend"] = (
        out["emaBullTrend"] & out["macdAboveSignal"] & (adx > p.minimumADX) & (plus > minus)
    )
    return out
