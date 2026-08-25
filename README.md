# Gold Signal

A phone-friendly BUY / HOLD / SELL readout for gold, driven by MACD (12/26/9) on the
weekly close, with a daily timeframe as a cross-check.

**Live:** https://mjjaber.github.io/gold-signal/

## How it works

GitHub Pages is static and Yahoo Finance sends no CORS headers, so the browser never
calls a price API. Instead:

1. `.github/workflows/update.yml` runs `scripts/build_data.py` on a schedule
   (weekday evenings after the COMEX close, plus Saturday for the closed weekly candle).
2. The script pulls 10y weekly + 3y daily candles for `GC=F` (falling back to `XAUUSD=X`),
   computes the MACD line / signal / histogram, classifies the state, and writes
   `docs/data.json`.
3. The Action commits that file. `docs/index.html` reads it same-origin — no keys,
   no CORS, nothing to break on the phone.

## Signal logic

Reading the MACD line against its signal line, plus whether the histogram is expanding
or contracting:

| MACD vs signal | Histogram | Call |
|---|---|---|
| Above | Expanding | **BUY** — upside momentum building |
| Above | Contracting | **HOLD** — up-move losing steam |
| Below | Expanding (down) | **SELL** — downside momentum building |
| Below | Contracting | **HOLD** — down-move losing steam |

Contracting histograms are deliberately HOLD rather than a flip: that's the zone where
MACD whipsaws most.

## Local development

```bash
python scripts/build_data.py     # rebuild docs/data.json
python -m http.server -d docs    # then open http://localhost:8000
```

Changing MACD settings: edit `FAST, SLOW, SIGNAL` at the top of `scripts/build_data.py`
and rerun. The page reads the values out of `data.json`, so the footer follows along.

## Not financial advice

This is a technical-indicator readout. MACD lags price by construction and says nothing
about rates, the dollar, or geopolitics.
