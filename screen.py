#!/usr/bin/env python
"""
1-hour swing screener for the EMA 4/9 + MACD + DMI/ADX indicator.

    python screen.py serve                 # web UI on http://127.0.0.1:8777
    python screen.py scan                  # terminal scan of the F&O universe
    python screen.py scan -u nse -l 7      # all NSE equity, 7-bar window
    python screen.py scan -s RELIANCE TCS  # just these symbols
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screener.strategy import Params

BADGE = {
    "STRONG BUY": "\033[1;92m",
    "BUY": "\033[92m",
    "DIVERGENCE": "\033[96m",
    "WATCH": "\033[93m",
    "NONE": "\033[90m",
}
RESET = "\033[0m"


def cmd_scan(a):
    from screener import scan as scanner

    rows = scanner.run(
        universe_id=a.universe,
        symbols=a.symbols,
        params=Params(),
        lookback=a.lookback,
        months=a.months,
        workers=a.workers,
        refresh=not a.no_refresh,
        min_price=0.0 if a.no_floor else a.min_price,
        min_turnover_cr=0.0 if a.no_floor else a.min_turnover,
    )
    if a.only:
        rows = [r for r in rows if r["bucket"] in a.only]
    elif not a.all:
        rows = [r for r in rows if r["bucket"] != "NONE"]

    if a.csv:
        import csv as _csv

        keys = [k for k in rows[0] if k != "spark"] if rows else []
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {a.csv}")

    head = f"{'SYMBOL':<13}{'SIGNAL':<12}{'AGO':>4} {'LTP':>10} {'DAY%':>7} {'ADX':>6} {'+DI-DI':>7} {'E4>E9':>6} {'PX>E9':>6} {'MACD':>5} {'DIV':>4} {'SCORE':>6}"
    print(head)
    print("-" * len(head))
    for r in rows[: a.top]:
        c = BADGE[r["bucket"]]
        yn = lambda b: " y " if b else " . "
        print(
            f"{c}{r['symbol']:<13}{r['bucket']:<12}{(r['bars_ago'] if r['bars_ago'] is not None else '-'):>4}"
            f" {r['ltp']:>10,.2f} {(r['day_chg_pct'] or 0):>6.2f}% {r['adx']:>6.1f} {r['di_spread']:>7.1f}"
            f" {yn(r['emaBullTrend']):>6} {yn(r['priceAboveEMA']):>6} {yn(r['macdAboveSignal']):>5}"
            f" {yn(r['bullishDivergence']):>4} {r['score']:>6.0f}{RESET}"
        )
    floor = ("no price/turnover floor" if a.no_floor
             else f"price >= {a.min_price:g}, turnover >= {a.min_turnover:g} Cr/day")
    print(f"\n{len(rows)} listed. Timeframe 1H, signal window {a.lookback} bar(s), {floor}.")


def cmd_serve(a):
    import uvicorn

    url = f"http://{a.host}:{a.port}"
    print(f"screener UI -> {url}")
    if a.open:
        webbrowser.open(url)
    uvicorn.run("screener.server:app", host=a.host, port=a.port, log_level="warning")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="run a scan in the terminal")
    s.add_argument("-u", "--universe", default="fno", choices=["fno", "nse"])
    s.add_argument("-s", "--symbols", nargs="*", help="scan just these symbols")
    s.add_argument("-l", "--lookback", type=int, default=3, help="signal window in bars")
    s.add_argument("-m", "--months", type=int, default=6, help="history depth")
    s.add_argument("-w", "--workers", type=int, default=10)
    s.add_argument("-t", "--top", type=int, default=40)
    s.add_argument("--only", nargs="*", help="keep only these buckets")
    s.add_argument("--all", action="store_true", help="include symbols with no setup")
    s.add_argument("--min-price", type=float, default=10.0,
                   help="drop stocks under this price (default 10)")
    s.add_argument("--min-turnover", type=float, default=5.0,
                   help="drop stocks under this daily turnover in Cr (default 5)")
    s.add_argument("--no-floor", action="store_true",
                   help="keep penny stocks: no price or turnover floor")
    s.add_argument("--no-refresh", action="store_true", help="use cached candles only")
    s.add_argument("--csv", help="also write results to this csv")
    s.set_defaults(fn=cmd_scan)

    v = sub.add_parser("serve", help="start the web UI")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8777)
    v.add_argument("--open", action="store_true", help="open a browser window")
    v.set_defaults(fn=cmd_serve)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
