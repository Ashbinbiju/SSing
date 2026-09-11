"""
The screener itself.

For every symbol in the universe it pulls 1-hour candles, runs the
unmodified indicator over them, and reports the state of the last closed
bar plus the most recent signal inside the lookback window.

Nothing here filters or re-weights the signal - the classification is a
direct read of the Pine script's own `strongSignal` / `buySignal` /
`newBullishDivergence` series. The `score` column is a display-only
ranking aid and is marked as such in the UI.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ta
from . import universe as un
from . import upstox as ux
from .strategy import Params, WARMUP_BARS, compute

MIN_BARS = WARMUP_BARS + 40


def _f(x):
    """JSON-safe float."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(v) else round(v, 4)


def _b(x) -> bool:
    return bool(x)


def _score(last) -> float:
    """Display-only 0-100 ranking aid. Not part of the strategy."""
    adx = float(last["adx"]) if np.isfinite(last["adx"]) else 0.0
    di = float(last["plusDI"] - last["minusDI"])
    gap = float((last["ema4"] - last["ema9"]) / last["ema9"] * 100) if last["ema9"] else 0.0
    hist = float(last["macdHistogram"]) if np.isfinite(last["macdHistogram"]) else 0.0
    px = float(last["close"]) or 1.0
    s = (
        min(adx, 60) / 60 * 40            # trend strength
        + max(min(di, 40), -40) / 40 * 25  # directional dominance
        + max(min(gap, 3), -3) / 3 * 20    # EMA separation
        + max(min(hist / px * 100, 1.5), -1.5) / 1.5 * 15  # MACD momentum
    )
    return round(max(0.0, min(100.0, s)), 1)


def _day_change(df: pd.DataFrame) -> float | None:
    dates = df["timestamp"].dt.date
    today = dates.iloc[-1]
    prev = df[dates < today]
    if prev.empty:
        return None
    return (df["close"].iloc[-1] / prev["close"].iloc[-1] - 1.0) * 100.0


def daily_trend(key: str, upto=None) -> dict:
    """Higher-timeframe context for one symbol.

    The Pine script's own uptrend test is `EMA 4 > EMA 9 and close > EMA 9`
    on the 1-hour chart - four and nine hourly bars, about half a trading
    day. That is a momentum condition, and it fires just as readily on a
    bounce inside a downtrend. This adds the daily picture so the screener
    can say which signals are actually in an uptrend.

    Nothing here feeds the signal. It is a column and an optional filter,
    exactly like turnover.
    """
    blank = {"trend": None, "above_sma50": None, "above_sma200": None,
             "ret_3m": None, "ret_6m": None, "off_52w_high": None}
    try:
        d = ux.daily(key, upto=upto)
    except Exception:
        return blank
    if len(d) < 200:
        return blank

    c = d["close"]
    px = float(c.iloc[-1])
    sma50 = float(c.rolling(50).mean().iloc[-1])
    sma200 = float(c.rolling(200).mean().iloc[-1])
    above50, above200 = px > sma50, px > sma200
    ret6 = (px / float(c.iloc[-126]) - 1) * 100 if len(c) > 126 else None
    ret3 = (px / float(c.iloc[-63]) - 1) * 100 if len(c) > 63 else None

    if above50 and above200 and (ret6 or 0) > 0:
        trend = "UP"
    elif not above50 and not above200:
        trend = "DOWN"
    else:
        trend = "MIXED"

    return {
        "trend": trend,
        "above_sma50": above50,
        "above_sma200": above200,
        "ret_3m": _f(ret3),
        "ret_6m": _f(ret6),
        "off_52w_high": _f((px / float(c.tail(250).max()) - 1) * 100),
    }


def _bars_since_true(series: pd.Series) -> int | None:
    arr = series.to_numpy(dtype=bool)
    if not arr.any():
        return None
    return int(len(arr) - 1 - np.flatnonzero(arr)[-1])


def evaluate(sym: str, name: str, key: str, df: pd.DataFrame, p: Params, lookback: int,
             with_trend: bool = True) -> dict:
    r = compute(df, p)
    r = r.iloc[WARMUP_BARS:].reset_index(drop=True)
    if r.empty:
        raise ValueError("not enough bars after warm-up")

    last = r.iloc[-1]
    tail = r.tail(max(1, lookback))

    # Most recent signal inside the window, exactly as the script defines it.
    sig_type, bars_ago, sig_at, sig_px = None, None, None, None
    for kind, col in (("STRONG BUY", "strongSignal"), ("BUY", "buySignal"), ("DIVERGENCE", "newBullishDivergence")):
        hits = np.flatnonzero(tail[col].to_numpy(dtype=bool))
        if len(hits):
            i = int(hits[-1])
            sig_type = kind
            bars_ago = int(len(tail) - 1 - i)
            sig_at = tail["timestamp"].iloc[i]
            sig_px = float(tail["close"].iloc[i])
            break

    # Info-table status, read off the last closed bar.
    status = "STRONG BUY" if last["strongSignal"] else ("BUY" if last["buySignal"] else "WAIT")

    if sig_type in ("STRONG BUY", "BUY"):
        bucket = sig_type
    elif sig_type == "DIVERGENCE":
        bucket = "DIVERGENCE"
    elif last["strongTrend"]:
        bucket = "WATCH"
    else:
        bucket = "NONE"

    # average daily turnover over the last five sessions; count sessions
    # rather than bars, since a session is six bars, not seven
    days = r["timestamp"].dt.date
    recent = days.drop_duplicates().tail(5)
    vol5 = r.loc[days.isin(recent), "volume"].sum() / max(len(recent), 1)
    px = float(last["close"])

    atr14 = ta.rma(ta.true_range(r["high"], r["low"], r["close"]), 14).iloc[-1]
    atr_pct = float(atr14 / px * 100) if np.isfinite(atr14) and px else 0.0

    # How far the current session has already travelled, in ATRs. Backtesting
    # found this monotonic (PF 1.21 -> 0.88 across quintiles): once the day's
    # range is 3-9x ATR the move is spent and the entry is a chase.
    today_bars = r[r["timestamp"].dt.date == r["timestamp"].iloc[-1].date()]
    day_rng = float(today_bars["high"].max() - today_bars["low"].min()) if len(today_bars) else 0.0
    range_exp = float(day_rng / atr14) if np.isfinite(atr14) and atr14 > 0 else None

    # The 09:15 bar was the only session slot with a positive read in both
    # halves (PF 1.11 in, 1.15 out), and 54% of signals fire there anyway.
    opening_bar = bool(sig_at is not None and sig_at.hour == 9)

    return {
        "symbol": sym,
        "name": name,
        "instrument_key": key,
        "bucket": bucket,
        "status": status,
        "signal": sig_type,
        "bars_ago": bars_ago,
        "signal_at": sig_at.isoformat() if sig_at is not None else None,
        "signal_price": _f(sig_px),
        "ltp": _f(px),
        "bar_time": last["timestamp"].isoformat(),
        "chg_pct": _f((last["close"] / r["close"].iloc[-2] - 1) * 100) if len(r) > 1 else None,
        "day_chg_pct": _f(_day_change(r)),
        "volume": int(last["volume"]) if np.isfinite(last["volume"]) else 0,
        "turnover_cr": _f(vol5 * px / 1e7),
        # --- the six info-table rows, verbatim -----------------------
        "emaBullTrend": _b(last["emaBullTrend"]),
        "priceAboveEMA": _b(last["priceAboveEMA"]),
        "macdAboveSignal": _b(last["macdAboveSignal"]),
        "adx": _f(last["adx"]),
        "adxOK": _b(last["adx"] > p.minimumADX),
        "diBull": _b(last["plusDI"] > last["minusDI"]),
        "bullishDivergence": _b(last["bullishDivergence"]),
        # --- supporting numbers --------------------------------------
        "ema4": _f(last["ema4"]),
        "ema9": _f(last["ema9"]),
        "ema_gap_pct": _f((last["ema4"] - last["ema9"]) / last["ema9"] * 100),
        "px_vs_ema9_pct": _f((px - last["ema9"]) / last["ema9"] * 100),
        "macdLine": _f(last["macdLine"]),
        "signalLine": _f(last["signalLine"]),
        "macdHistogram": _f(last["macdHistogram"]),
        "plusDI": _f(last["plusDI"]),
        "minusDI": _f(last["minusDI"]),
        "di_spread": _f(last["plusDI"] - last["minusDI"]),
        "adx_rising": _b(last["adx"] > r["adx"].iloc[-2]) if len(r) > 1 else False,
        # ATR as % of price, and what a 0.35% round trip costs against a
        # 1.5*ATR stop. Backtesting found this is the only stock-selection
        # variable that survived a hold-out - and mostly because below
        # ~1.3% ATR friction eats the signal before it starts. See
        # RESEARCH.md; it is context, not a signal.
        "atr_pct": _f(atr_pct),
        "cost_r": _f(0.35 / (1.5 * atr_pct)) if atr_pct and atr_pct > 0 else None,
        "range_exp": _f(range_exp),
        "opening_bar": opening_bar,
        # All three filters that replicated out-of-sample AND have a
        # mechanical cause. Passing does not make a signal good - it means
        # it is not one of the structurally unprofitable ones. RESEARCH.md.
        "research_ok": bool(
            atr_pct > 1.6
            and (range_exp is not None and range_exp < 2.21)
            and opening_bar
        ),
        "trend_bars": _bars_since_true(r["emaBullCross"]),
        "div_bars_ago": _bars_since_true(r["newBullishDivergence"]),
        "strongTrend": _b(last["strongTrend"]),
        "score": _score(last),
        "spark": [_f(v) for v in r["close"].tail(60).tolist()],
        **(daily_trend(key, upto=last["timestamp"].date()) if with_trend
           else {"trend": None, "above_sma50": None, "above_sma200": None,
                 "ret_3m": None, "ret_6m": None, "off_52w_high": None}),
    }


@dataclass
class Progress:
    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    state: str = "idle"          # idle | running | done | error
    message: str = ""
    started: float = 0.0
    finished: float = 0.0
    results: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "total": self.total,
                "done": self.done,
                "ok": self.ok,
                "failed": self.failed,
                "state": self.state,
                "message": self.message,
                "started": self.started,
                "finished": self.finished,
                "errors": self.errors[:25],
            }


def run(
    universe_id: str = "fno",
    symbols=None,
    params: Params | None = None,
    lookback: int = 3,
    months: int = 6,
    workers: int = 10,
    refresh: bool = True,
    progress: Progress | None = None,
    min_price: float = 0.0,
    min_turnover_cr: float = 0.0,
    with_trend: bool = True,
):
    """Scan a universe and return the result rows."""
    p = params or Params()
    u = un.resolve(universe_id, symbols)
    prog = progress or Progress()
    # each scan re-establishes what the newest available bar is
    ux.reset_watermark()

    with prog.lock:
        prog.total = len(u)
        prog.done = prog.ok = prog.failed = 0
        prog.state = "running"
        prog.results = []
        prog.errors = []

    rows: list[dict] = []

    def work(rec):
        sym, key, name = rec["tradingsymbol"], rec["instrument_key"], rec["name"]
        try:
            df = ux.hourly(key, months=months, refresh=refresh)
            if len(df) < MIN_BARS:
                raise ValueError(
                    "no candle history" if len(df) == 0
                    else f"only {len(df)} bars, needs {MIN_BARS}"
                )
            return evaluate(sym, str(name), key, df, p, lookback, with_trend), None
        except Exception as exc:  # one bad symbol must not stop the scan
            return None, {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"[:180]}

    records = u.to_dict("records")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row, err in pool.map(work, records):
            with prog.lock:
                prog.done += 1
                if row is not None:
                    prog.ok += 1
                    rows.append(row)
                else:
                    prog.failed += 1
                    prog.errors.append(err)

    if min_price or min_turnover_cr:
        rows = [
            r for r in rows
            if (r["ltp"] or 0) >= min_price and (r["turnover_cr"] or 0) >= min_turnover_cr
        ]

    order = {"STRONG BUY": 0, "BUY": 1, "DIVERGENCE": 2, "WATCH": 3, "NONE": 4}
    # within a bucket: signals whose conditions still hold first, then the
    # freshest, so a setup that has already broken down sinks
    rows.sort(key=lambda r: (
        order[r["bucket"]],
        0 if r["strongTrend"] else 1,
        r["bars_ago"] if r["bars_ago"] is not None else 99,
        -r["score"],
    ))

    with prog.lock:
        prog.results = rows
        prog.state = "done"
    return rows


def series_for(key: str, params: Params | None = None, bars: int = 220, months: int = 6) -> dict:
    """Chart payload for one symbol: candles plus every plotted series."""
    p = params or Params()
    df = ux.hourly(key, months=months)
    r = compute(df, p).tail(bars).reset_index(drop=True)
    return {
        "t": [t.isoformat() for t in r["timestamp"]],
        "o": [_f(v) for v in r["open"]],
        "h": [_f(v) for v in r["high"]],
        "l": [_f(v) for v in r["low"]],
        "c": [_f(v) for v in r["close"]],
        "v": [int(v) if np.isfinite(v) else 0 for v in r["volume"]],
        "ema4": [_f(v) for v in r["ema4"]],
        "ema9": [_f(v) for v in r["ema9"]],
        "macd": [_f(v) for v in r["macdLine"]],
        "signal": [_f(v) for v in r["signalLine"]],
        "hist": [_f(v) for v in r["macdHistogram"]],
        "adx": [_f(v) for v in r["adx"]],
        "plusDI": [_f(v) for v in r["plusDI"]],
        "minusDI": [_f(v) for v in r["minusDI"]],
        "buy": [i for i, v in enumerate(r["buySignal"]) if v and not r["strongSignal"].iloc[i]],
        "strong": [i for i, v in enumerate(r["strongSignal"]) if v],
        "div": [i for i, v in enumerate(r["newBullishDivergence"]) if v],
        "minADX": p.minimumADX,
    }
