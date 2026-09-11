"""
The out-of-sample test.

Rules below were fixed by reading the IN-SAMPLE quintile tables only
(research/study.py), using quintile boundaries observed there rather than
thresholds tuned for the result. They are then run once on the
out-of-sample half.

Every rule tried is reported, not just the one that looks best - testing
six rules on one hold-out is itself multiple comparisons, and hiding the
failures would be the easiest way to manufacture an edge that is not
there.

    python research/validate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.engine import BARS, COST_ROUND_TRIP, summarise  # noqa: E402
from research.portfolio import buy_hold, simulate  # noqa: E402

TRADES = ROOT / "research" / "trades.parquet"

# What survived the in-sample screen, and why:
#
#   ret_6m      the only cleanly monotonic feature: expR -0.14 -> +0.06
#               across quintiles, win% 35.2 -> 40.3. Classic momentum.
#   atr_pct     top quintile jumps to +0.21R. This one has a mechanical
#               cause rather than being a pattern in noise: with a
#               1.5*ATR stop, the 0.35% round trip costs
#               0.35 / (1.5 * atr_pct) in R, so 0.23R on a 1% ATR name
#               but only 0.09R on a 2.5% one. Low-volatility stocks lose
#               to friction before the signal does anything.
#   above200    mild but consistent with the momentum story
#               (PF 1.10 vs 0.99).
#
# Discarded: r2 and slope were U-shaped not monotonic; off_high, turnover,
# adx_slow, adx_1h and di_spread showed no ordering at all.
RULES = {
    "baseline (every signal)": lambda d: pd.Series(True, index=d.index),
    "momentum: 6m return > 25%": lambda d: d["ret_6m"] > 25,
    "volatility: ATR% > 1.6": lambda d: d["atr_pct"] > 1.6,
    "trend: above 200 DMA": lambda d: d["above200"],
    "momentum + volatility": lambda d: (d["ret_6m"] > 25) & (d["atr_pct"] > 1.6),
    "all three": lambda d: (d["ret_6m"] > 25) & (d["atr_pct"] > 1.6) & d["above200"],
    "looser: 6m > 10% & ATR% > 1.3": lambda d: (d["ret_6m"] > 10) & (d["atr_pct"] > 1.3),
}


def cost_in_R(atr_pct: float, stop_atr: float = 1.5) -> float:
    return COST_ROUND_TRIP * 100 / (stop_atr * atr_pct)


def table(t: pd.DataFrame, title: str):
    print(f"\n{title}")
    print(f"  {'rule':<32}{'trades':>7}{'win%':>7}{'avg net%':>10}{'expR':>7}{'PF':>6}{'total%':>9}")
    print("  " + "-" * 71)
    for name, fn in RULES.items():
        sel = t[fn(t).fillna(False)]
        if len(sel) < 25:
            print(f"  {name:<32}{len(sel):>7}   too few trades")
            continue
        s = summarise(sel)
        print(f"  {name:<32}{s['trades']:>7}{s['win_pct']:>7.1f}{s['avg_net']:>10.3f}"
              f"{s['expR_net']:>7.2f}{s['profit_factor']:>6.2f}{s['total_net']:>9.1f}")


def main():
    t = pd.read_parquet(TRADES)
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t["exit_time"] = pd.to_datetime(t["exit_time"], utc=True)
    cut = t["entry_time"].quantile(0.5)
    ins, oos = t[t["entry_time"] <= cut], t[t["entry_time"] > cut]

    print("=" * 75)
    print("WHY LOW-VOLATILITY NAMES LOSE: cost of the 0.35% round trip, in R")
    print("=" * 75)
    print(f"  {'ATR % of price':>16}{'cost in R':>12}")
    for a in (0.6, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.5):
        print(f"  {a:>16.1f}{cost_in_R(a):>12.2f}")

    print("\n" + "=" * 75)
    print("IN-SAMPLE  (rules were chosen by looking at this half)")
    print("=" * 75)
    table(ins, "  to " + str(cut.date()))

    print("\n" + "=" * 75)
    print("OUT-OF-SAMPLE  (never examined until now)")
    print("=" * 75)
    table(oos, "  after " + str(cut.date()))

    # --- portfolio, out-of-sample ------------------------------------
    print("\n" + "=" * 75)
    print("PORTFOLIO, OUT-OF-SAMPLE: 1% risk per trade, max 5 positions")
    print("closed-trade drawdown, so it understates the intraday pain")
    print("=" * 75)
    print(f"  {'rule':<32}{'taken':>7}{'total%':>9}{'CAGR%':>8}{'maxDD%':>9}")
    print("  " + "-" * 65)
    for name, fn in RULES.items():
        sel = oos[fn(oos).fillna(False)]
        if len(sel) < 25:
            continue
        r = simulate(sel)
        print(f"  {name:<32}{r['taken']:>7}{r['total_pct']:>9.1f}{r['cagr']:>8.1f}{r['max_dd']:>9.1f}")

    syms = sorted(p.stem for p in BARS.glob("*.parquet"))
    bh = buy_hold(BARS, syms, oos["entry_time"].min(), oos["exit_time"].max())
    years = (oos["exit_time"].max() - oos["entry_time"].min()).days / 365.25
    print(f"\n  benchmark: equal-weight buy & hold of the same 400 names over the")
    print(f"  same {years:.2f} years -> {bh:+.1f}%  ({((1+bh/100)**(1/years)-1)*100:+.1f}% CAGR)")


if __name__ == "__main__":
    main()
