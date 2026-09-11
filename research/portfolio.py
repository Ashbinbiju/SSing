"""
Portfolio simulation over a set of trades, so a selection rule is judged
on what it would actually have returned rather than on per-trade averages.

Deliberately simple and stated plainly:

* Risk a fixed % of equity per trade against the ATR stop, so a wide-stop
  trade takes a smaller position. Capped so one name cannot dominate.
* At most N positions at once. When more signals arrive than slots, they
  are taken in timestamp order - no cherry-picking the best.
* Equity is marked at exits, not daily, so the drawdown here is a
  closed-trade drawdown and will understate the intraday pain. Labelled
  as such rather than dressed up.
* Costs are already inside `ret_net` (0.35% round trip).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def simulate(trades: pd.DataFrame, risk_pct: float = 1.0, max_positions: int = 5,
             max_weight: float = 0.20, start_equity: float = 1_000_000.0) -> dict:
    if trades.empty:
        return {"trades": 0, "cagr": 0.0, "total_pct": 0.0, "max_dd": 0.0,
                "equity": pd.Series(dtype=float), "taken": 0}

    t = trades.sort_values("entry_time").reset_index(drop=True)
    equity = start_equity
    open_until: list[pd.Timestamp] = []
    curve = []
    taken = 0

    for _, r in t.iterrows():
        now = r["entry_time"]
        open_until = [x for x in open_until if x > now]
        if len(open_until) >= max_positions:
            continue                                  # no slot, signal skipped

        # size from the recorded stop distance: risk_pct of equity if it stops out
        stop_dist = r["stop_dist"]
        if not np.isfinite(stop_dist) or stop_dist <= 0:
            stop_dist = r["entry"] * 0.015             # fallback: 1.5%
        risk_cash = equity * risk_pct / 100.0
        qty_value = min(risk_cash / (stop_dist / r["entry"]), equity * max_weight)

        pnl = qty_value * r["ret_net"] / 100.0
        equity += pnl
        taken += 1
        open_until.append(r["exit_time"])
        curve.append({"time": r["exit_time"], "equity": equity})

    if not curve:
        return {"trades": 0, "cagr": 0.0, "total_pct": 0.0, "max_dd": 0.0,
                "equity": pd.Series(dtype=float), "taken": 0}

    c = pd.DataFrame(curve).sort_values("time")
    eq = c.set_index("time")["equity"]
    peak = eq.cummax()
    dd = (eq / peak - 1) * 100
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = (equity / start_equity - 1) * 100
    cagr = ((equity / start_equity) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    return {
        "trades": len(t),
        "taken": taken,
        "skipped_no_slot": len(t) - taken,
        "total_pct": total,
        "cagr": cagr,
        "max_dd": dd.min(),
        "years": years,
        "equity": eq,
    }


def buy_hold(bars_dir, symbols, start, end) -> float:
    """Equal-weight buy and hold of the same names, as the honest benchmark."""
    import pandas as pd
    rets = []
    for s in symbols:
        p = bars_dir / f"{s}.parquet"
        if not p.exists():
            continue
        d = pd.read_parquet(p)
        d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
        d = d[(d.timestamp >= start) & (d.timestamp <= end)]
        if len(d) < 50:
            continue
        rets.append(d["close"].iloc[-1] / d["close"].iloc[0] - 1)
    return float(np.mean(rets) * 100) if rets else float("nan")
