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
