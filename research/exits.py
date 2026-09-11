"""
Exit grid. The Pine script defines entries only, and the repo's first study
found exit style swings CAGR by more than 30 points - far more than any
selection rule moved. So this varies the exit while holding the entry and
the universe fixed.

Reported on the out-of-sample half only, for the raw signal and for the
best selection rule the intraday study produced.

    python research/exits.py
"""
from __future__ import annotations

import sys
import time
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.engine import (BARS, Exits, market_features, market_index,  # noqa: E402
                             summarise, trades_for)
from research.portfolio import simulate  # noqa: E402
from screener.strategy import Params  # noqa: E402

OUT = ROOT / "research" / "exit_grid.parquet"

STOPS = (1.0, 1.5, 2.5)
TARGETS = (2.0, 3.0, 5.0)


def run_config(syms, mf, stop, target):
    ex = Exits(stop_atr=stop, target_atr=target)
    frames = []
    for s in syms:
        t = trades_for(s, Params(), ex, mf)
        if not t.empty:
            frames.append(t)
    t = pd.concat(frames, ignore_index=True)
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t["exit_time"] = pd.to_datetime(t["exit_time"], utc=True)
    t["stop_atr"], t["target_atr"] = stop, target
    return t


def main():
    mf = market_features(market_index())
    syms = sorted(p.stem for p in BARS.glob("*.parquet"))
    rows = []
    t0 = time.time()
    for stop, target in product(STOPS, TARGETS):
        t = run_config(syms, mf, stop, target)
        cut = t["entry_time"].quantile(0.5)
        oos = t[t["entry_time"] > cut]
        sel = oos[(oos.range_exp < 2.21) & (oos.bar_of_day == 0) & (oos.atr_pct > 1.6)]
        for label, part in (("all signals", oos), ("A+B+F selection", sel)):
            if len(part) < 25:
                continue
            s = summarise(part)
            p = simulate(part)
            rows.append({
                "stop_atr": stop, "target_atr": target, "R": target / stop,
                "set": label, "trades": s["trades"], "win": s["win_pct"],
                "avg_net": s["avg_net"], "expR": s["expR_net"], "pf": s["profit_factor"],
                "taken": p["taken"], "cagr": p["cagr"], "max_dd": p["max_dd"],
            })
        print(f"  stop {stop} target {target} done  {time.time()-t0:.0f}s", flush=True)
    g = pd.DataFrame(rows)
    g.to_parquet(OUT, index=False)

    for label in ("all signals", "A+B+F selection"):
        d = g[g["set"] == label]
        print(f"\n=== {label}, OUT-OF-SAMPLE ===")
        print(f"{'stop':>6}{'target':>8}{'R:R':>6}{'trades':>8}{'win%':>7}"
              f"{'avg net%':>10}{'PF':>6}{'taken':>7}{'CAGR%':>8}{'maxDD%':>9}")
        for _, r in d.sort_values("cagr", ascending=False).iterrows():
            print(f"{r.stop_atr:>6.1f}{r.target_atr:>8.1f}{r.R:>6.1f}{int(r.trades):>8}"
                  f"{r.win:>7.1f}{r.avg_net:>10.3f}{r.pf:>6.2f}{int(r.taken):>7}"
                  f"{r.cagr:>8.1f}{r.max_dd:>9.1f}")


if __name__ == "__main__":
    main()
