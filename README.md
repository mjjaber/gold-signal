# Gold Signal

A phone-friendly BUY / HOLD / SELL readout for gold, driven by MACD (12/26/9) on the
weekly close, filtered by trend regime and flagged by RSI — with an honest backtest
printed on the page next to it.

**Live:** https://mjjaber.github.io/gold-signal/

## How it works

GitHub Pages is static and Yahoo Finance sends no CORS headers, so the browser never
calls a price API. Instead:

1. `.github/workflows/update.yml` runs `scripts/build_data.py` on a schedule
   (weekday evenings after the COMEX close, plus Saturday for the closed weekly candle).
2. The script pulls weekly (30y) and daily (10y) candles for `GC=F`, falling back to
   `XAUUSD=X`, computes the indicators, replays the backtest, and writes `docs/data.json`.
3. The Action commits that file. `docs/index.html` reads it same-origin — no keys,
   no CORS, nothing to break on the phone.

Yahoo quirk worth knowing: `range=max` on the weekly interval returns a **sparse,
truncated 267 bars**. `range=30y` returns 1358 clean ones. Using `max` silently
restricted the backtest to the recent bull market and inflated the results — hence the
explicit per-symbol ranges in `SOURCES`.

## The rule

Three causal stages, so `backtest()` can replay the exact live function over history
with no lookahead:

1. **MACD state** — above signal + histogram expanding = BUY; below + expanding down =
   SELL; either side with a contracting histogram = HOLD (that's where MACD whipsaws most).
2. **Regime veto** — a BUY fired below the 50-bar MA, or a SELL fired above it, is cut
   to HOLD. Don't fight the trend you're standing in.
3. **RSI guard** — RSI ≥ 70 on a BUY flags it *extended*; RSI ≤ 30 on a SELL flags it
   *washed out*. These annotate, they never veto: overbought stays overbought for months
   in a real trend.

## What the backtest actually says

Measured over 25.2 years of weekly bars, entries and exits at the close of the bar the
state changed on, no costs or slippage:

| Rule | CAGR | Max drawdown | In market |
|---|---|---|---|
| Position (BUY until SELL, HOLD sits tight) | +8.11% | −43.6% | 73% |
| Literal signal (long only while it says BUY) | +3.29% | −17.5% | 24% |
| **Buy & hold** | **+11.96%** | −43.6% | 100% |

**No variant beat simply holding gold.** Gold spent most of this window trending up, and
a persistent uptrend is exactly the regime trend-following gives the most back in. The
one honest bright spot: the literal-signal rule took a −17.5% worst drawdown against
buy-and-hold's −43.6%, so it bought a much smoother ride for a third of the return.

This table is rendered on the page on purpose. An indicator app that hides its own track
record is the failure mode worth avoiding.

Adding the regime filter was measured, not assumed. On the position rule it raised CAGR
(6.22% → 8.11%) but worsened max drawdown (−33.9% → −43.6%); on the literal-signal rule
it made both worse. It is kept because it cuts counter-trend entries, but the numbers
are mixed and it is one constant away from being switched off.

## Parameter sensitivity

The page also sweeps a 7x7 grid of MACD fast/slow settings and scores each with the same
position rule. Over 25 years of weekly bars, all 46 valid combinations land between
**+7.44% and +8.64% CAGR** — a spread of 1.2 points, and **none of them beat buy-and-hold
at +11.95%**. The live 12/26/9 ranks 24th of 46, dead mid-pack.

The tight clustering is the useful part. A lone profitable spike surrounded by losers
would mean the setting was curve-fit to noise; a flat plateau means the result is stable
and real. Here it is stably mediocre, which says the underperformance is structural — the
cost of sitting out ~27% of the time in an asset that trended up — and not something
another parameter sweep will fix.

## Rejected: the dollar filter

The dollar index correlates **−0.46** with weekly gold returns, far and away the
strongest external candidate tested (10y real yields managed −0.06, silver and miners are
so correlated with gold at +0.78/+0.80 that they are echoes rather than inputs). It still
did not ship, because that correlation is entirely contemporaneous:

| Dollar move at week t vs gold return at | Correlation |
|---|---|
| week t | −0.462 |
| week t+1 | +0.026 |
| week t+2 | −0.005 |
| week t+4 | −0.010 |

And conditioning next-week gold returns on dollar regime splits them +0.249% (dollar
below its 50MA) versus +0.244% (above) — identical, both 57% win rate. The dollar
*explains* gold after the fact and *forecasts* nothing. Wired in as a veto it made the
backtest worse: 6.22% → 4.94% CAGR alone, 8.11% → 7.88% alongside the regime filter.

## A note on stacking indicators

Measured over the same window, the things this app already shows are substantially the
same signal:

| Pair | Correlation |
|---|---|
| RSI vs %-from-50MA | +0.88 |
| MACD histogram vs RSI | +0.52 |
| MACD histogram vs %-from-50MA | +0.36 |

MACD, RSI and moving averages are all transforms of one close-price series. Agreement
between them reads like corroboration and is closer to double-counting, which is why
adding the regime filter and RSI guard moved the backtest so little.

## What finally beat the baseline: proximity to the high

Every price-derived *timing* rule tested here underperformed buy-and-hold. One
*conditional* reading did not. Bucketing all 1358 weekly bars by drawdown from the
running high, and measuring the mean return over the following 52 weeks:

| Zone | Next 52w | Win rate | n |
|---|---|---|---|
| At the highs (0–2% off) | **+20.06%** | 91% | 275 |
| Shallow dip (2–5% off) | **+20.69%** | 93% | 216 |
| Moderate dip (5–10% off) | +13.60% | 74% | 260 |
| Correction (10–20% off) | +8.04% | 74% | 204 |
| Bear market (20%+ off) | +5.68% | 57% | 351 |
| *Any week (baseline)* | *+13.13%* | *76%* | *1306* |

Gold near its high kept going; gold far below its high stayed weak. **Buying the dip has
been the worse trade on gold** — the deeper the dip, the worse the next year, monotonically
across every bucket. Adding the uptrend filter sharpens it further: the 2–5% zone above the
50-week MA ran +21.93% with a 92% win rate.

Caveat that matters: the forward windows overlap heavily, so the effective sample is far
smaller than n suggests, and this is one asset over one 25-year window that was mostly a
secular bull. It is a description of the record, not a significance test.

## Corrected: the RSI guard was backwards

The first version flagged RSI ≥ 70 as "overbought — late entry", on the standard reading.
Bucketing the same 25 years says the opposite:

| Weekly RSI at entry | Next 52w | Win rate | n |
|---|---|---|---|
| 65–100 | **+17.71%** | 82% | 283 |
| 55–65 | +15.34% | 81% | 402 |
| 45–55 | +11.57% | 72% | 431 |
| 35–45 | +7.15% | 67% | 180 |
| 0–35 | +8.23% | 67% | 48 |

Forward returns rise monotonically with RSI. On gold, strong momentum has been
confirmation, not a warning. The flag now reads "strong momentum · confirming", and the
caution has moved to the *low* end where the record actually supports it.

## Rejected: MACD divergence

Divergence between price pivots and the MACD histogram was implemented with causal pivot
confirmation (a pivot at bar i is only known at i+k) and measured across k = 3, 4, 5 on
weekly and 5, 8, 10 on daily. Bearish divergences were followed by gold going *up* more
than baseline (weekly k=4: +10.55% over 26 weeks against a +6.26% baseline), and daily
bullish divergences underperformed baseline at short horizons. With only 13–30 events per
configuration, the honest reading is that there is no detectable effect either way — not
enough signal to justify putting an arrow on the chart.

## Local development

```bash
python scripts/build_data.py     # rebuild docs/data.json
python -m http.server -d docs    # then open http://localhost:8000
```

Tuning lives in the constants at the top of `scripts/build_data.py`: `FAST/SLOW/SIGNAL`,
`MA_LEN`, `RSI_LEN`, `RSI_HOT/RSI_COLD`. The page reads them out of `data.json`, so the
footer and the labels follow along automatically.

## Not financial advice

This is a technical-indicator readout. MACD lags price by construction, the backtest
excludes costs and slippage, and none of it knows anything about rates, the dollar, or
geopolitics.
