# 1-Hour Swing Screener — EMA 4/9 + MACD + DMI/ADX

Scans NSE stocks on **1-hour bars** for the exact signal your TradingView
indicator plots, so you do not have to flip through charts to find them.

```
pip install -r requirements.txt
python screen.py serve --open        # UI on http://127.0.0.1:8777
python screen.py scan                # or just print it in the terminal
```

## The strategy is untouched

[screener/strategy.py](screener/strategy.py) is a line-for-line port of the
Pine script. Same inputs, same defaults, same order of evaluation:

| Pine | Port |
| --- | --- |
| `ta.ema`, `ta.rma` | SMA-seeded recursion, `na` until `length` bars |
| `ta.macd(close,12,26,9)` | same, signal EMA runs on the MACD line |
| `ta.dmi(14,14)` | the reference implementation, including `fixnan` |
| `ta.pivotlow(src,5,5)` | confirmed 5 bars late, strictly lower on both sides |
| the `var` divergence block | unrolled; state only advances on bars where a price pivot **and** a MACD pivot confirm together |
| `barstate.isconfirmed` | the screener only ever reads closed bars |

Every input is editable in the UI under **Inputs**, grouped exactly as the
script groups them, with **Reset to defaults** returning the shipped values.

Two consequences of the script worth knowing, neither of which is a bug:

* **Under the default inputs every BUY is also a STRONG BUY.** `strongSignal`
  adds `emaBullTrend`, `macdAboveSignal`, `adx > 25` and `+DI > -DI` on top of
  `buySignal`, and with the shipped settings `buySignal` already requires all
  four. The two separate only if you change MACD Confirmation, Primary Entry,
  or switch off Require ADX / Require Uptrend.
* **`trendOK` reduces to `close > ema9`** on a cross bar, because `ema4 > ema9`
  is true by definition the moment the cross happens.

## Validation

The port was cross-checked bar-for-bar against the independently written
implementation whose output the backtest cached in `data/signals/`, over ~620
overlapping 1-hour bars on nine symbols:

* `adx`, `ema4`, `ema9` — identical to the last bit.
* `macdLine`, `signalLine`, `plusDI`, `minusDI` — max absolute difference
  `4e-7`, i.e. float noise.
* `buySignal`, `strongSignal`, `emaBullCross`, `macdAboveSignal`,
  `dmiConfirmation`, `bullishDivergence` — zero disagreements.
* `newBullishDivergence` — disagreed, and the **older implementation was
  wrong**: it emitted the raw `bullishDivergence` level on every bar instead
  of applying Pine's `and not bullishDivergence[1]`, turning 5 events into
  797. This screener fires the rising edge, as the script does.

Because the script only advances its stored pivots on bars where a price pivot
low **and** a MACD pivot low confirm together, divergence events are genuinely
rare — on 6 months of hourly SAIL data, 50 price pivots and 38 MACD pivots
coincide on just 12 bars and produce 2 divergence signals.

## What the screener adds

The signal itself is a single-bar event, so a scan of only the newest bar
would nearly always come back empty. The screener therefore reports:

| Bucket | Meaning |
| --- | --- |
| **STRONG BUY** | `strongSignal` fired inside the signal window |
| **BUY** | `buySignal` fired, `strongSignal` did not |
| **DIVERGENCE** | `newBullishDivergence` fired — the script's early warning |
| **WATCH** | no fresh entry, but `strongTrend` is true right now: EMA 4 > EMA 9, MACD > signal, ADX > 25, +DI > -DI. This is the "in an uptrend" list |
| — | everything else |

The **signal window** (1 to 35 bars) only widens how far back it looks for the
event. It changes nothing about how the event is computed.

Note that the condition columns describe the **last closed bar**, while the
signal describes the bar it fired on. A stock can be `STRONG BUY, 4 bars ago`
and show `ADX 20.5` today — that is the trade aging, not a contradiction.

The **Score** column is a display-only ranking aid (ADX, DI spread, EMA
separation, MACD histogram) for sorting the WATCH list. It is not part of the
strategy and never gates a signal.

## Data

Upstox v3 `historical-candle`, `hours/1`, rebuilt to TradingView's bar
layout: **six bars a session** — 09:15, 10:15, 11:15, 12:15, 13:15, 14:15 —
with the last one running to the 15:30 close.

The NSE session is 375 minutes, so an hourly grid leaves a 15-minute
remainder. Upstox returns that remainder as a separate 15:15 candle;
TradingView does not draw it, it extends the 14:15 bar to the close. Feeding
the raw seven-bar series to the indicator moves it a long way: on INOXWIND it
puts ADX at **50.10** where the chart reads **43.5**, and folding the stub
reproduces **43.51**. Across the F&O universe the median ADX shift is 3.0
points and 20 of 210 symbols land in a different bucket, so this is not
cosmetic. `SCREENER_FOLD_STUB=0` in `.env` reverts to Upstox's own bars.

* 6 months of history per symbol (~760 bars); the first 260 are burned as
  warm-up so the ADX and MACD recursions are fully settled.
* Candles cache to `data/cache/hourly/` and refresh incrementally. A cold
  F&O scan takes ~2 minutes, every scan after that ~20 seconds.
* A rescan does not ask every symbol whether anything is newer. The first
  symbols to refresh establish a watermark — the newest bar that exists — and
  any symbol whose cache already reaches it skips the round trip. Only symbols
  that really did refresh set the watermark, so a stale cache can never
  suppress a fetch. Without this a 3,149-name rescan cost 3,149 requests.
* While the market is open, today's bars come from the intraday endpoint and
  the still-forming bar is dropped, so signals are always close-confirmed.
* The token in `.env` is market-data only and IP-restricted; account
  endpoints return UDAPI1221. Nothing here places orders.

## Universes

* **F&O underlyings** (210) — default; having a futures contract is a
  liquidity floor.
* **All NSE equity** (3,149) — every cash-segment company share, plus REITs
  and InvITs. The first scan is long (rate limits), later ones are fast.
* `python screen.py scan -s RELIANCE TCS SBIN` for an ad-hoc list.

The NSE_EQ segment lists far more than shares. Of its 4,794 INE instruments
only 3,149 are equity: the rest are NCDs (1,148 — `0ABCL31`, `1003SCFL31`),
bonds (450 — `737IRFC29`), municipal debt and rights entitlements, none of
which have tradable candle history. Characters 8-9 of an Indian ISIN give the
security type, so the universe keeps `01` (shares), `23` (InvITs) and `25`
(REITs) and drops the rest.

About 8% of what remains is still skipped, and legitimately so — recent
listings and thin SME names without the ~300 bars the indicator needs to warm
up. The status bar breaks the skips down by reason. Use the **Min turnover**
filter to hide the illiquid tail: the median name in the full universe trades
only ~₹2 Cr a day.

Turnover and ADX filters in the toolbar are client-side, so you can tighten
them without rescanning.

## UI

* Bucket tabs with live counts, sortable columns, symbol search, CSV export.
* Click a row for the detail drawer: candles with EMA 4/9 and the BUY / STRONG
  / DIV markers, a MACD pane, a DMI/ADX pane with the ADX floor drawn in, the
  indicator's own status table verbatim, and the supporting numbers.
* `#SYMBOL` in the URL deep-links to a stock. Arrow keys walk the list,
  Escape closes.
* **Auto rescan** re-runs every 5/15/30/60 min while NSE is open and raises a
  toast plus a desktop notification when a new signal appears.
* Dark and light themes.

## Layout

    screener/indicators.py   Pine ta.ema / ta.rma / ta.macd / ta.dmi / ta.pivotlow
    screener/strategy.py     the port, with every Pine input on Params
    screener/upstox.py       cached v3 candle client, throttled
    screener/universe.py     F&O / NSE / custom symbol lists
    screener/scan.py         parallel scan, one result row per symbol
    screener/server.py       FastAPI: /api/scan, /api/results, /api/chart
    screener/web/            the UI
    screen.py                CLI entry point

## One honest caveat

`run_backtest.py` in this repo tested these entries on the same 1-hour data
over 2022-2026 and found no edge that survives transaction costs. The screener
faithfully finds what the indicator plots; whether to trade it is your call.
