"""
Pull the backtest panel: multi-year 1-hour bars for a liquidity-ranked
universe.

The universe is derived from the candles themselves - median daily
turnover over the last 250 sessions - not from any vendor's index or
derivatives list. That is the point: the selection rule this research
builds has to stand on data we can compute, not on someone else's
membership decision.

    python research/fetch.py --top 400 --from 2023-01-01
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from screener import upstox as ux  # noqa: E402

BARS = ROOT / "research" / "bars"
BARS.mkdir(parents=True, exist_ok=True)


def load_universe(top: int) -> pd.DataFrame:
    u = pd.read_parquet(ROOT / "research" / "universe_daily.parquet")
    return u.head(top).reset_index(drop=True)


def fetch_one(rec, start: date, end: date, force: bool = False) -> tuple[str, int, str]:
    sym, key = rec["sym"], rec["key"]
    path = BARS / f"{sym}.parquet"
    if path.exists() and not force:
        try:
            have = pd.read_parquet(path)
            if len(have) > 100:
                return sym, len(have), "cached"
        except Exception:
            pass
    try:
        df = ux._fetch_range(key, start, end)
        if df.empty:
            return sym, 0, "empty"
        df = ux.fold_session_stub(df)          # six bars a session, as TradingView draws them
        df.to_parquet(path, index=False)
        return sym, len(df), "ok"
    except Exception as exc:
        return sym, 0, f"{type(exc).__name__}: {exc}"[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=400)
    ap.add_argument("--from", dest="start", default="2023-01-01")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    u = load_universe(a.top)
    start = date.fromisoformat(a.start)
    end = ux.today_ist()
    print(f"fetching {len(u)} symbols, {start} -> {end}", flush=True)

    t0 = time.time()
    done = ok = 0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = [pool.submit(fetch_one, r, start, end, a.force) for r in u.to_dict("records")]
        for f in futs:
            sym, n, how = f.result()
            done += 1
            if n:
                ok += 1
            if done % 25 == 0 or done == len(u):
                print(f"  {done}/{len(u)}  ok={ok}  {time.time()-t0:.0f}s", flush=True)
            if how not in ("ok", "cached"):
                print(f"    {sym}: {how}", flush=True)
    print(f"done in {time.time()-t0:.0f}s, {ok} symbols on disk", flush=True)


if __name__ == "__main__":
    main()
