"""
Does a data-derived stock-selection rule rescue this entry?

Method, and the guards against fooling ourselves:

1. Extract every trade the unmodified entry produces across the universe.
2. Split the period in half by time. Everything is discovered on the FIRST
   half only.
3. Look at each candidate selection feature one at a time, in quintiles,
   in-sample. A feature is only interesting if the relationship is roughly
   monotonic and the buckets hold enough trades to mean anything.
4. Build a rule from what survived, then run it once on the SECOND half,
   which was never looked at.
5. Compare against the honest baseline: taking every signal.

A rule that only works in-sample is the null result, and is reported as
such rather than re-tuned until it passes.

    python research/study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.engine import BARS, Exits, summarise, trades_for  # noqa: E402
from screener.strategy import Params  # noqa: E402

TRADES = ROOT / "research" / "trades.parquet"

FEATURES = [
    ("r2", "trend straightness (R^2 of 60d log fit)"),
    ("slope_ann", "trend slope, % per year"),
    ("ret_1m", "1-month return %"),
    ("ret_3m", "3-month return %"),
    ("ret_6m", "6-month return %"),
    ("ret_12m", "12-month return %"),
    ("atr_pct", "ATR as % of price"),
    ("off_high", "% below 52-week high"),
    ("turnover_cr", "turnover, Rs Cr/day"),
    ("adx_slow", "daily-equivalent ADX"),
    ("adx_1h", "1-hour ADX at the signal"),
    ("di_spread", "+DI - -DI at the signal"),
]


def build(force: bool = False) -> pd.DataFrame:
    if TRADES.exists() and not force:
        return pd.read_parquet(TRADES)
    syms = sorted(p.stem for p in BARS.glob("*.parquet"))
    print(f"extracting trades from {len(syms)} symbols ...", flush=True)
    frames = []
    for i, s in enumerate(syms, 1):
        t = trades_for(s, Params(), Exits())
        if not t.empty:
            frames.append(t)
        if i % 50 == 0:
            print(f"  {i}/{len(syms)}", flush=True)
    t = pd.concat(frames, ignore_index=True)
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t = t.sort_values("entry_time").reset_index(drop=True)
    t.to_parquet(TRADES, index=False)
    return t


def quintiles(t: pd.DataFrame, col: str, label: str, n: int = 5) -> pd.DataFrame:
    d = t[np.isfinite(t[col])].copy()
    if len(d) < n * 20:
        return pd.DataFrame()
    d["bucket"] = pd.qcut(d[col], n, labels=False, duplicates="drop")
    g = d.groupby("bucket").agg(
        trades=("ret_net", "size"),
        lo=(col, "min"), hi=(col, "max"),
        win=("ret_net", lambda s: 100 * (s > 0).mean()),
        avg=("ret_net", "mean"),
        expR=("r_net", "mean"),
    ).reset_index()
    g["feature"] = label
    return g


def show(g: pd.DataFrame):
    if g.empty:
        return
    print(f"\n  {g['feature'].iloc[0]}")
    print(f"    {'bucket':<8}{'range':>22}{'trades':>8}{'win%':>8}{'avg net%':>10}{'exp R':>8}")
    for _, r in g.iterrows():
        rng = f"{r['lo']:.1f} .. {r['hi']:.1f}"
        print(f"    {int(r['bucket']):<8}{rng:>22}{int(r['trades']):>8}"
              f"{r['win']:>8.1f}{r['avg']:>10.3f}{r['expR']:>8.2f}")


def main():
    t = build()
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    cut = t["entry_time"].quantile(0.5)
    ins = t[t["entry_time"] <= cut]
    oos = t[t["entry_time"] > cut]

    print("=" * 74)
    print(f"{len(t)} trades, {t['symbol'].nunique()} symbols, "
          f"{t['entry_time'].min().date()} -> {t['entry_time'].max().date()}")
    print(f"in-sample  : {len(ins)} trades to {cut.date()}")
    print(f"out-of-sample: {len(oos)} trades after {cut.date()}")
    print("=" * 74)

    print("\nBASELINE - take every signal")
    for name, part in (("all", t), ("in-sample", ins), ("out-of-sample", oos)):
        s = summarise(part, name)
        print(f"  {name:<15} trades {s['trades']:>5}  win {s['win_pct']:>5.1f}%  "
              f"avg net {s['avg_net']:>7.3f}%  expR(net) {s['expR_net']:>6.2f}  "
              f"PF {s['profit_factor']:>5.2f}  total {s['total_net']:>9.1f}%")

    print("\n" + "=" * 74)
    print("IN-SAMPLE ONLY - each candidate feature in quintiles")
    print("=" * 74)
    for col, label in FEATURES:
        show(quintiles(ins, col, label))

    print("\n  binary flags (in-sample)")
    for col in ("above50", "above200"):
        for v in (False, True):
            part = ins[ins[col] == v]
            if len(part) < 20:
                continue
            s = summarise(part)
            print(f"    {col}={str(v):<6} trades {s['trades']:>5}  win {s['win_pct']:>5.1f}%  "
                  f"avg net {s['avg_net']:>7.3f}%  expR(net) {s['expR_net']:>6.2f}  PF {s['profit_factor']:>5.2f}")


if __name__ == "__main__":
    main()
