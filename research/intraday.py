"""
Intraday stock selection: which signals to take, decided from what is
knowable the moment the signal bar closes.

Same protocol as research/study.py - features examined on the first half by
time, rules run once on the second half, every rule reported including the
failures.

    python research/intraday.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.engine import (BARS, Exits, market_features, market_index,  # noqa: E402
                             summarise, trades_for)
from research.portfolio import buy_hold, simulate  # noqa: E402
from screener.strategy import Params  # noqa: E402

TRADES = ROOT / "research" / "trades_intraday.parquet"

FAST = [
    ("bar_of_day", "bar of day (0=09:15 ... 5=14:15)"),
    ("rel_vol", "relative volume vs 20-session norm"),
    ("gap_pct", "opening gap %"),
    ("day_move", "move so far today %"),
    ("range_pos", "position in the day's range (100=high)"),
    ("day_range_pct", "day's range so far, % of price"),
    ("dist_vwap", "distance from session VWAP %"),
    ("range_exp", "range expansion vs ATR"),
    ("consec_up", "consecutive up bars"),
    ("mkt_ret_5d", "market 5-day return %"),
    ("mkt_ret_20d", "market 20-day return %"),
]


def build(force: bool = False) -> pd.DataFrame:
    if TRADES.exists() and not force:
        return pd.read_parquet(TRADES)
    print("building market index ...", flush=True)
    mf = market_features(market_index())
    syms = sorted(p.stem for p in BARS.glob("*.parquet"))
    print(f"extracting trades from {len(syms)} symbols ...", flush=True)
    t0, frames = time.time(), []
    for i, s in enumerate(syms, 1):
        t = trades_for(s, Params(), Exits(), mf)
        if not t.empty:
            frames.append(t)
        if i % 100 == 0:
            print(f"  {i}/{len(syms)}  {time.time()-t0:.0f}s", flush=True)
    t = pd.concat(frames, ignore_index=True)
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t["exit_time"] = pd.to_datetime(t["exit_time"], utc=True)
    t = t.sort_values("entry_time").reset_index(drop=True)
    t.to_parquet(TRADES, index=False)
    return t


def buckets(t, col, label, n=5):
    d = t[np.isfinite(t[col])].copy()
    if len(d) < n * 20:
        return
    uniq = d[col].nunique()
    if uniq <= 8:                      # discrete, e.g. bar_of_day
        d["b"] = d[col]
    else:
        d["b"] = pd.qcut(d[col], n, labels=False, duplicates="drop")
    print(f"\n  {label}")
    print(f"    {'range':>22}{'trades':>8}{'win%':>8}{'avg net%':>10}{'expR':>8}{'PF':>7}")
    for b, g in d.groupby("b"):
        w = g.ret_net > 0
        gp, gl = g.ret_net[w].sum(), -g.ret_net[~w].sum()
        pf = gp / gl if gl > 0 else np.inf
        rng = f"{g[col].min():.2f} .. {g[col].max():.2f}"
        print(f"    {rng:>22}{len(g):>8}{100*w.mean():>8.1f}{g.ret_net.mean():>10.3f}"
              f"{g.r_net.mean():>8.2f}{pf:>7.2f}")


def report(t, rules, title):
    print(f"\n{title}")
    print(f"  {'rule':<38}{'trades':>7}{'win%':>7}{'avg net%':>10}{'expR':>7}{'PF':>6}{'total%':>9}")
    print("  " + "-" * 77)
    for name, fn in rules.items():
        sel = t[fn(t).fillna(False)]
        if len(sel) < 25:
            print(f"  {name:<38}{len(sel):>7}   too few")
            continue
        s = summarise(sel)
        print(f"  {name:<38}{s['trades']:>7}{s['win_pct']:>7.1f}{s['avg_net']:>10.3f}"
              f"{s['expR_net']:>7.2f}{s['profit_factor']:>6.2f}{s['total_net']:>9.1f}")


def main():
    t = build()
    cut = t["entry_time"].quantile(0.5)
    ins, oos = t[t.entry_time <= cut], t[t.entry_time > cut]
    print("=" * 78)
    print(f"{len(t)} trades, {t.symbol.nunique()} symbols, "
          f"{t.entry_time.min().date()} -> {t.entry_time.max().date()}")
    print(f"in-sample to {cut.date()}: {len(ins)}   out-of-sample: {len(oos)}")
    print("=" * 78)
    print("\nIN-SAMPLE ONLY - fast features in buckets")
    for col, label in FAST:
        buckets(ins, col, label)
    return t, ins, oos, cut


if __name__ == "__main__":
    main()
