"""
Backtest driver.

    python run_backtest.py

Outputs into results/:
    trades_<config>.csv     every trade
    equity_<config>.csv     portfolio curve
    summary.json            all metrics
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

import upstox_data as ud
import strategy as st
import backtest as bt
import report as rp
from download import universe, START, END, DAILY_START

RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

# Exit styles compared. The Pine script defines none of these; they are
# this study's assumption and the comparison shows how much they matter.
CONFIGS = {
    "A_atr_2R": bt.ExitRules("A_atr_2R", 1.5, 3.0, False, 140),
    "B_atr_1R5": bt.ExitRules("B_atr_1R5", 1.5, 2.25, False, 140),
    "C_ema_exit": bt.ExitRules("C_ema_exit", 1.5, 0.0, True, 140),
    "D_trail": bt.ExitRules("D_trail", 1.5, 0.0, False, 140, trail_atr_mult=2.5),
}


SIGCACHE = ROOT / "data" / "signals"
SIGCACHE.mkdir(parents=True, exist_ok=True)


def load_signals(u: pd.DataFrame, params: st.Params, use_cache: bool = True):
    """Compute (or reload) per-symbol signal frames.

    The indicator recursions and pivot scans are pure Python loops and
    cost ~1.3s per symbol, so the finished frames are cached.
    """
    sig_map, daily_map = {}, {}
    for _, r in u.iterrows():
        key, sym = r["instrument_key"], r["tradingsymbol"]
        try:
            h = ud.hourly(key, START, END)
            d = ud.daily(key, DAILY_START, END)
        except Exception:
            continue
        if len(h) < 400 or len(d) < 250:
            continue
        cp = SIGCACHE / f"{sym}.parquet"
        if use_cache and cp.exists():
            s = pd.read_parquet(cp)
        else:
            s = st.compute(h, params)
            s = bt.attach_uptrend(s, d)
            s.to_parquet(cp, index=False)
        sig_map[sym] = s
        daily_map[sym] = d
    return sig_map, daily_map


def main():
    t0 = time.time()
    u = universe()
    params = st.Params()
    costs = bt.Costs()

    print(f"loading signals for {len(u)} symbols ...", flush=True)
    sig_map, daily_map = load_signals(u, params)
    print(f"usable symbols: {len(sig_map)}  ({time.time()-t0:.0f}s)", flush=True)

    summary = {
        "window": {"start": str(START), "end": str(END), "timeframe": "1 hour"},
        "universe": {"source": "NSE F&O underlyings (liquid)",
                     "requested": len(u), "usable": len(sig_map)},
        "costs": {"round_trip_pct": costs.round_trip_pct},
        "params": params.__dict__,
    }

    # ---- raw signal census
    total_bars = sum(len(s) for s in sig_map.values())
    census = {
        "total_hourly_bars": int(total_bars),
        "bars_ema_cross": int(sum(s["ema_bull_cross"].sum() for s in sig_map.values())),
        "bars_buy_signal": int(sum(s["buy_signal"].sum() for s in sig_map.values())),
        "bars_strong_signal": int(sum(s["strong_signal"].sum() for s in sig_map.values())),
        "bars_new_divergence": int(sum(s["new_bullish_divergence"].sum() for s in sig_map.values())),
        "bars_uptrend": int(sum(s["uptrend"].sum() for s in sig_map.values())),
    }
    summary["signal_census"] = census
    print("signal census:", json.dumps(census, indent=2), flush=True)

    # ---- run every exit config, uptrend-filtered and unfiltered
    results = {}
    for name, ex in CONFIGS.items():
        for filt in (True, False):
            tag = f"{name}{'_uptrend' if filt else '_all'}"
            trades = []
            for sym, s in sig_map.items():
                trades += bt.run_symbol(s, ex, costs, sym, require_uptrend=filt)
            tr = pd.DataFrame(trades)
            if tr.empty:
                results[tag] = {"trades": 0}
                continue
            tr.to_csv(RESULTS / f"trades_{tag}.csv", index=False)

            stats = rp.trade_stats(tr, tag)
            curve, pstats = rp.portfolio(tr, price_map=daily_map,
                                         round_trip_pct=costs.round_trip_pct)
            if not curve.empty:
                curve.to_csv(RESULTS / f"equity_{tag}.csv", index=False)
            strong = rp.trade_stats(tr[tr["strong"]], tag + "_STRONGonly")
            results[tag] = {"trade_stats": stats,
                            "portfolio": pstats,
                            "strong_only": strong}
            print(f"  {tag:26s} n={stats['trades']:5d} "
                  f"win={stats['win_rate_%']:5.1f}% "
                  f"avgR={stats['expectancy_R']:+.3f} "
                  f"PF={stats['profit_factor']:.2f} "
                  f"CAGR={pstats.get('CAGR_%', float('nan')):+.2f}% "
                  f"DD={pstats.get('max_drawdown_%', float('nan')):.1f}%", flush=True)
            results[tag]["trade_stats"] = stats

    summary["results"] = results

    # ---- cost sensitivity on the single best-performing exit style
    best = bt.ExitRules("C_ema_exit", 1.5, 0.0, True, 140)
    sens = {}
    for rt in (0.0, 0.20, 0.35, 0.50):
        c = bt.Costs(round_trip_pct=rt)
        trades = []
        for sym, s in sig_map.items():
            trades += bt.run_symbol(s, best, c, sym, require_uptrend=True)
        tr = pd.DataFrame(trades)
        stt = rp.trade_stats(tr, f"cost_{rt}")
        _, pst = rp.portfolio(tr, price_map=daily_map, round_trip_pct=rt)
        sens[f"round_trip_{rt}%"] = {
            "profit_factor": stt.get("profit_factor"),
            "avg_net_%": stt.get("avg_net_%"),
            "expectancy_R": stt.get("expectancy_R"),
            "CAGR_%": pst.get("CAGR_%"),
        }
        print(f"  cost {rt:.2f}%  PF={stt.get('profit_factor')}  "
              f"avg={stt.get('avg_net_%')}%  CAGR={pst.get('CAGR_%')}%", flush=True)
    summary["cost_sensitivity"] = sens

    # ---- benchmarks
    summary["benchmark"] = rp.buy_and_hold(daily_map, START, END)
    try:
        nif = ud.daily("NSE_INDEX|Nifty 50", DAILY_START, END)
        m = (nif["ts"].dt.date >= START) & (nif["ts"].dt.date <= END)
        ns = nif.loc[m, "close"]
        yrs = (END - START).days / 365.25
        tot = float(ns.iloc[-1] / ns.iloc[0] - 1)
        summary["benchmark"]["nifty50_total_return_%"] = round(tot * 100, 1)
        summary["benchmark"]["nifty50_CAGR_%"] = round(
            ((1 + tot) ** (1 / yrs) - 1) * 100, 2)
    except Exception as e:
        summary["benchmark"]["nifty50_error"] = repr(e)
    print("benchmark:", summary["benchmark"], flush=True)

    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nwrote results/summary.json  ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
