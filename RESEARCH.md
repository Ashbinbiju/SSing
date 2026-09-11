# Can stock selection rescue this strategy?

**Short answer: no.** The intuition that it "works on trending stocks" does
not survive contact with the data. The one filter that did survive the
hold-out test is mostly cost arithmetic, it stopped working in 2026, and it
still captures a fifth of what simply owning the same stocks returned.

Reproduce with `python research/fetch.py && python research/study.py &&
python research/validate.py`.

## Setup

* **Universe derived from candles, not a vendor list** — the top 400 NSE
  names by median daily turnover over 250 sessions (a ₹26 Cr/day floor).
  No index membership, no F&O list, no sector tags.
* **6,202 trades, 400 symbols, Mar 2023 → Sep 2026** on 1-hour bars.
* **Entry**: the unmodified `buySignal` from the Pine port.
* **Exit** (the script defines none, so it is fixed and never tuned):
  1.5×ATR stop, 3×ATR target, 140-bar time stop, fill at the next bar's
  open, stop assumed to fill first when a bar spans both.
* **Costs**: 0.35% round trip.
* **Protocol**: split in half by time. Features examined on the first half
  only; rules then run once on the second half. Every rule tried is
  reported, including the failures.

## The baseline

| | trades | win% | avg net | PF | total |
| --- | --- | --- | --- | --- | --- |
| in-sample | 3,115 | 39.4% | +0.063% | 1.04 | +196.5% |
| **out-of-sample** | 3,087 | 36.9% | **−0.011%** | **0.99** | **−35.4%** |

A coin flip that loses to friction, which is what the repo's earlier study
found on the F&O universe. Selection has to overcome this, not just sort it.

## What failed

Examined in-sample, then tested once on the hold-out:

| Rule | in-sample PF | out-of-sample PF | |
| --- | --- | --- | --- |
| 6-month return > 25% | 1.19 | **0.91** | failed |
| above the 200 DMA | 1.10 | **0.89** | failed |
| momentum + volatility | 1.56 | 1.09 | degraded |
| all three | 1.57 | 1.06 | degraded |

**The "trending stock" thesis is the thing that failed hardest.** Momentum
and the 200-DMA filter both looked good in-sample and went *negative* out of
sample. Trend straightness — R² of a 60-session log-price fit, the most
direct way to measure "is this stock trending" — was U-shaped in-sample, not
monotonic, so it never even made it to the hold-out. Trend slope was U-shaped
too. Distance from the 52-week high, turnover, daily ADX, 1-hour ADX and the
DI spread showed no ordering at all.

Adding momentum to the volatility filter made it *worse* out-of-sample
(PF 1.45 → 1.09), which is what you expect when the added variable is noise.

## What survived, and why it is not what it looks like

`ATR% > 1.6` held up: in-sample PF 1.49 → out-of-sample PF 1.45, win rate
45.3% → 45.4%. Stable. But:

**Much of it is friction, not edge.** With a 1.5×ATR stop, the 0.35% round
trip costs `0.35 / (1.5 × ATR%)` in R:

| ATR % of price | cost in R |
| --- | --- |
| 0.6 | **0.39** |
| 1.0 | 0.23 |
| 1.6 | 0.15 |
| 2.5 | 0.09 |

A low-volatility stock has to overcome a third of a unit of risk before the
signal does anything. Gross expectancy does rise with volatility too
(−0.12R → +0.33R out-of-sample), so it is not *only* costs — but you are
mostly locating where the friction is survivable.

**It stopped working in 2026.** Year by year, against the equal-weight
market return of the same 400 names:

| year | market EW | rule PF | rule expR | baseline PF |
| --- | --- | --- | --- | --- |
| 2023 | +69.6% | 1.47 | +0.26 | 1.10 |
| 2024 | +44.2% | 1.60 | +0.30 | 1.02 |
| 2025 | +11.2% | 1.68 | +0.37 | 1.16 |
| **2026** | **+13.1%** | **1.00** | **−0.04** | 0.78 |

236 trades in 2026, expectancy zero — in a market that rose 13%. Not a bear
market excuse.

**It loses badly to doing nothing.** Out-of-sample, on the same 216
high-volatility names:

| | return | CAGR | max DD |
| --- | --- | --- | --- |
| the rule | +11.0% | +6.7% | −19.0% |
| **buy and hold those same names** | **+62.7%** | **+35.2%** | — |

The selection rule is identifying stocks that went up a lot, then trading in
and out of them and capturing a fifth of the move, with a 19% drawdown for
the privilege.

## What this means

The screener finds what the indicator plots, faithfully. But no
selection rule built from price, volume, trend, momentum or volatility
turned the entry into something worth trading. The levers that actually
matter are the ones the study held fixed:

* **Costs.** At 0.35% round trip the signal is underwater at every
  volatility below ~1.3% ATR. Cheaper execution moves every number above.
* **Exits.** The script defines none. The earlier backtest found exit style
  swings CAGR by more than 30 points — far more than any selection rule
  here moved anything.

If you want to keep going, those are where the work is. Stock selection is
not.

## Honest limits

* One exit scheme. A different stop/target could reorder these results.
* 3.7 years, most of it a strong bull market for Indian mid-caps.
* Six rules tested on one hold-out is itself multiple comparisons; the
  survivor is the one I would trust least to keep working, and 2026 already
  suggests it has not.
* Closed-trade drawdowns understate intraday pain.


---

# Part 2: intraday selection, and the exit grid

Part 1 tested slow features (6-month momentum, 200 DMA). This tests what is
knowable **the moment the signal bar closes** — time of day, relative volume,
gap, position in the session range, distance from VWAP, range expansion, and
market regime — plus the exit rule that Part 1 held fixed.

Same protocol: features read on the first half by time, rules run once on the
second, every rule reported.

## The best rule found, and why it is a mirage

`range_exp < 2.21 AND opening bar (09:15) AND ATR% > 1.6`

Out-of-sample this looks excellent: **172 trades, 59.3% win, PF 2.56**, summed
net +374%. Then the portfolio simulation returns **+11.4%**.

That gap is the finding. Of those 172 trades, the 111 a 5-position portfolio
could take averaged **+0.51%**; the 61 it had to skip averaged **+5.21% at a
90% win rate**. The winners arrive together.

| profit concentration | |
| --- | --- |
| top 3 entry dates | **54% of all profit** |
| top 10 dates | **89% of all profit** |
| 2025-04-11 alone | 29 simultaneous trades, 22% of everything |

And those dates are the largest market-wide rallies in the sample:

| date | market, equal-weight | trades | summed net |
| --- | --- | --- | --- |
| 2025-05-12 | **+4.21%** (sample max +4.34%) | 24 | +131.6% |
| 2026-02-03 | +2.84% | 17 | +60.9% |
| 2024-06-06 | +2.83% (99th pct) | 11 | +88.0% |
| 2025-04-11 | +2.46% | 29 | +148.0% |

Remove them one at a time from the out-of-sample portfolio:

| best days excluded | trades | CAGR | PF |
| --- | --- | --- | --- |
| 0 | 172 | +7.1% | 2.56 |
| 1 | 143 | +4.8% | 1.97 |
| 2 | 119 | +2.4% | 1.41 |
| **3** | 102 | **−0.1%** | 1.16 |
| 5 | 93 | −2.6% | 0.91 |

Three days are the entire result. The effective sample is not 342 trades; it
is ~149 dates of which about four matter. The rule demands **29 positions in
one day** and exceeds any 5-slot limit on 6% of its active days.

What it really says: *on the handful of days the whole market gaps up 2.5–4%,
volatile stocks that gapped up go up more.* That is levered beta on four days
out of nine hundred, not stock selection.

**Benchmark**: buy and hold the same universe over the same window returned
**+18.8% CAGR with a −14.6% marked-to-market drawdown**. The rule returned
+7.1% with a −8.5% *closed-trade* drawdown, which flatters it.

## The exit grid

Nine stop/target combinations, out-of-sample.

**No exit rescues the raw signal.** All nine lose:

| stop×ATR | target×ATR | PF | CAGR |
| --- | --- | --- | --- |
| 2.5 | 5.0 | 1.07 | **−18.3%** |
| 1.5 | 3.0 (default) | 0.99 | −40.5% |
| 1.0 | 2.0 | 0.91 | −55.0% |

With the selection applied, all nine turn positive (CAGR +8.2% to +16.9%,
PF 2.25–2.89) and the ranking is fairly flat — the result is robust to exit
choice, which is a genuine point in its favour. But it is robust *on top of*
the same four days, so it inherits the same fragility.

## Verdict

The three filters that replicated out-of-sample and have mechanical reasons —
**ATR% > 1.6** (friction: 0.39R cost at 0.6% ATR vs 0.09R at 2.5%),
**range expansion < 2.21** (monotonic, PF 1.21 → 0.88: do not buy after the
day's range is already 3–9× ATR), and the **09:15 bar** (PF 1.11 in / 1.15
out, and 54% of signals fire there anyway) — are worth applying as filters.

They do not add up to a tradeable edge. Anything that looks like one here is
four days of market beta with a 5-position cap standing between you and it.
