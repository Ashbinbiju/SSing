# Swing Trading EMA 4/9 + MACD + DMI/ADX — 1-Hour Backtest

Backtest of the TradingView Pine v6 indicator on NSE stocks, 1-hour bars.

> **Looking for the live screener rather than the backtest?** See
> [SCREENER.md](SCREENER.md) — `python screen.py serve --open`.
>
> Note: the backtest's `src/` directory is not in this repository, so
> `run_backtest.py` will not run as-is. The screener under `screener/` is
> self-contained and carries its own Pine-faithful port of the indicator.

## Result

The signal has **no edge that survives transaction costs**. Best-case
configuration returns +7.3% CAGR against +28.1% for simply holding the
same 210 stocks. Full write-up: `results/summary.json`.

## Run it

    pip install -r requirements.txt
    python src/download.py       # ~60s, caches candles under data/cache
    python run_backtest.py       # ~5 min, writes results/

## Data

Upstox v3 `historical-candle`, 1-hour bars, **2022-03-01 → 2026-09-04**
(the full depth Upstox exposes at this resolution). The supplied token is
IP-restricted so account endpoints return UDAPI1221, but the historical
endpoints are open and that is all a backtest needs.

Universe: the 210 NSE F&O underlyings — a liquidity floor, so fills in
the backtest are not fantasies.

## Method

* Signal on bar close, fill at the **next bar's open**.
* Daily uptrend filter is **point-in-time**: an intraday bar on day D can
  only see daily data through D-1.
* When a bar's range spans both stop and target, the **stop** is assumed
  to fill first.
* Costs: 0.35% round trip (STT 0.10% each leg + brokerage/GST/stamp
  ~0.09% + ~0.10% slippage). Sensitivity run at 0 / 0.20 / 0.35 / 0.50%.
* Portfolio: unlevered, max 5 concurrent positions, 1% equity risk per
  trade against the ATR stop, 20% position cap. Drawdown measured on a
  **daily mark-to-market** curve, not just at exits.

## Layout

    src/indicators.py   Pine-faithful ta.ema / ta.rma / ta.macd / ta.dmi / ta.pivotlow
    src/strategy.py     literal port of the Pine script (+ DIAGNOSTICS)
    src/backtest.py     event-driven engine, exit rules, uptrend filter
    src/report.py       trade stats + unlevered MTM portfolio sim
    src/upstox_data.py  cached Upstox v3 client
    run_backtest.py     driver

## Caveat

The Pine script defines **entries only**. Exits are this study's
assumption, and the four styles compared differ by >30 percentage points
of CAGR — so the exit, not the entry, dominates the outcome.
