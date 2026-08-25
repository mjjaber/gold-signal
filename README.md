# Gold Doctor Signal

A phone-friendly BUY / HOLD / SELL readout for gold, driven by MACD (12/26/9) on the
weekly close, filtered by trend regime and flagged by RSI — with an honest backtest
printed on the page next to it.

**Live:** https://mjjaber.github.io/gold-signal/

## How it works

GitHub Pages is static and Yahoo Finance sends no CORS headers, so the browser never
calls a price API. Instead:

1. A GitHub Action runs `scripts/build_data.py` on a schedule (weekday evenings after the
   COMEX close, plus Saturday for the closed weekly candle). The workflow file ships at
   `scripts/github-workflow.yml` — see the header comment there to activate it.
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

## Proximity to the high, and why it does not survive its own stress test

Bucketing all 1358 weekly bars by drawdown from the running high and measuring the next
52 weeks produces a clean, monotonic-looking result over the full window:

| Zone | All | 1st half | 2nd half | Same side? |
|---|---|---|---|---|
| At the highs (0–2%) | +20.1% | +13.3% | +37.1% | ✗ |
| Shallow dip (2–5%) | +20.7% | +18.4% | +25.4% | ✓ |
| Moderate dip (5–10%) | +13.6% | +17.0% | +4.4% | ✗ |
| Correction (10–20%) | +8.0% | +6.3% | +10.5% | ✓ |
| Bear market (20%+) | +5.7% | +10.7% | +5.2% | ✓ |
| *Baseline* | *+13.1%* | *+14.1%* | *+12.1%* | |

Read only the "All" column and it looks like a law: returns decline monotonically with
depth, so buying the dip is the worse trade. Split the record in half and that ordering
falls apart. "At the highs" **lagged** its era's baseline in 2000–2013 and beat it by 25
points in 2013–2026. "Moderate dip" beat baseline in the first half and badly lagged in
the second. Only 3 of 5 buckets stay on the same side of baseline in both halves, and two
of those three are the low-return zones.

The same test on other assets reorders the buckets outright: on silver the 10–20% zone was
the *best* (+30.6%), on the S&P the 20%+ zone beat baseline, and on crude the 0–2% zone was
*negative*. There is no general "near highs is good" effect here — there is one asset in
one 25-year window that was mostly a secular bull.

The page now shows the split columns next to the aggregate, because the aggregate on its
own is misleading. The honest residue is narrow: the 2–5% shallow-dip zone beat baseline in
both halves, and deep drawdowns were unexciting in both. That is all.

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

## The outlook card

The page leads with a summary: a plain-language read of current momentum, then the
distribution of what actually happened next from matching historical setups — 10th to 90th
percentile, converted to prices, with the median marked and today's price on the same bar.

It is deliberately **not** a price target. The match is made on signal state plus drawdown
zone where history supports it, loosened to signal state alone or the whole record when it
does not, and the card names which basis it used and how many samples backed it.

The card also prints the measured skill of the signal, which is the number that matters:
mean 26-week forward returns came out **BUY +5.85%, HOLD +6.79%, SELL +5.06%** — a spread of
1.73 points, with HOLD ahead of BUY. The signal barely separates outcomes at all. A point
forecast on top of that distribution would be invention, so the width of the band is shown
instead and left to speak for itself.

## Save & verify: the prediction ledger

Every rebuild appends the current call to `docs/predictions.json` — spot price, verdict,
the projection bands, and a due date per horizon (4/13/26/52 weeks, 21/63/126/252 days).
Each horizon scores itself once its date passes, and a scored horizon is never rewritten.
The record is append-only and automatic, so it cannot be curated after the fact.

The page also has a **Save this call** button that pins a prediction to the browser's
localStorage, and **Check what is due** to score the saved ones against the live price.
That is the on-demand version of the same thing, and it exists because it was asked for —
but the auto-logged ledger is the one that counts. A save button you press by choice
records the calls you felt good about; the ledger records all of them.

The ledger was seeded with 196 causal replay entries so the scorecard had something to say
on day one. Standing at each past bar, the bands were rebuilt from only the data available
at that bar — matching rows drawn from `j < i`, and a match contributing a forward return
for horizon h only when `j + h <= i`. Replay entries are tagged and dated so they are never
confused with live calls.

### What the scorecard says

| Horizon | In band (target 80%) | Direction | Median error | Mean abs error |
|---|---|---|---|---|
| 4w | 75% | 50% | −0.8pp | 4.0pp |
| 13w | 68% | 58% | −0.2pp | 7.0pp |
| 26w | 62% | 53% | +0.0pp | 10.7pp |
| 52w | 49% | 57% | +2.7pp | 16.8pp |

Two honest failures worth reading:

**The bands are too narrow.** A 10th–90th percentile range should contain ~80% of outcomes.
It contains 64% overall and degrades badly with horizon — by 52 weeks only 49% of outcomes
land inside a band advertised as covering 80%. The projection understates how wide gold's
real distribution is, and understates it more the further out it reaches.

**Direction is a coin flip.** 54% across 180 resolved BUY/SELL calls, and exactly 50% at the
4-week horizon. This is the same conclusion the backtest reached, now measured a second and
independent way.

A note on why the first version of this scorecard was wrong: weekly entries were being
resolved against a 10-year daily series, so any due date before 2016 silently matched the
earliest available bar — scoring a 2005 prediction against a 2016 price and reporting a
+209% return and a 24-point mean error on a 4-week horizon. `_price_at` now refuses a
timestamp before the series starts and refuses a bar more than a tolerance past due, and
each timeframe scores against its own series.

## The learning loop

The ledger measures; this closes the loop. Each resolved prediction feeds back into the
band width for future ones.

The mechanism is calibration scaling: find the factor k that would have made the p10–p90
band hold 80% of resolved outcomes, and widen future bands by it. k is fitted per horizon,
shrunk toward 1.0 while the sample is thin (`k_eff = 1 + (k_fit − 1) · n/(n+30)`) so an
early streak cannot swing the bands, and capped at 3×.

| Horizon | Widen | Coverage was | Coverage now | Δ |
|---|---|---|---|---|
| 4w | 1.13× | 75% | 79% | +4 |
| 13w | 1.34× | 68% | 78% | +10 |
| 26w | 1.34× | 62% | 74% | +12 |
| 52w | 1.72× | 49% | 75% | +26 |

**It generalises, which is the part that matters.** Fitted on the first half of the resolved
record and applied to the unseen second half, coverage went from 69% to 88% — overshooting
the 80% target, because the second half was calmer, but far closer than the 69% it started
at. Per horizon on unseen data: 26w went 63% → 91%, 52w went 61% → 84%. The factor is not
merely fitting its own history.

Each entry records the k it was issued under, so the scorecard separates calls made with
calibrated bands from those made before the loop existed. That comparison is the real proof
and it needs live calls to accumulate; until then the card says so rather than claiming a
win.

### What the loop cannot do

It makes the *uncertainty* honest, not the *call* better. Direction accuracy across the
same resolved record was 57% in the first half and 52% in the second — decaying toward
chance, not improving. No band adjustment touches that number, because band width encodes
how sure the model is, not which way it leans.

That split is worth stating plainly: **the spread is learnable, the direction is not.** A
system that widens its error bars until it is rarely surprised has learned something real
about its own limits. It has not learned to predict the price of gold, and no amount of
this loop running will get it there.

## Local development

```bash
python scripts/build_data.py     # rebuild docs/data.json
python -m http.server -d docs    # then open http://localhost:8000
```

Tuning lives in the constants at the top of `scripts/build_data.py`: `FAST/SLOW/SIGNAL`,
`MA_LEN`, `RSI_LEN`, `RSI_HOT/RSI_COLD`. The page reads them out of `data.json`, so the
footer and the labels follow along automatically.

## The basis signal

The one input in this project that survived every test thrown at it. The gap between the
COMEX settle and the LBMA PM bullion fix, averaged over 20 sessions, predicts spot bullion's
next 21 sessions.

| Zone (20d avg basis) | Next 21d | Win | p10 | p90 | n |
|---|---|---|---|---|---|
| Wide contango (top quintile) | **+3.25%** | 73% | −2.76% | +10.12% | 293 |
| Neutral | +1.12% | 57% | −3.81% | +6.80% | 1558 |
| Backwardation (bottom quintile) | +0.15% | 52% | −4.73% | +5.37% | 553 |
| *Any day* | *+1.16%* | *58%* | | | 2404 |

**Wide contango is the bullish setup** — the opposite of the usual "backwardation means
scarcity means bullish" story.

### Why this one is believed and the others were not

**The entry is delayed by one session, and that is not a detail — it is the whole result.**
The LBMA fix is struck at 15:00 London and the COMEX settle prints hours later, so a wide
basis partly just means gold rallied after the fix was taken. Score the forward return from
that same stale fix and a huge, monotonic, out-of-sample-stable signal appears that is pure
arithmetic:

| z > +2 (futures rich) | 5d | 10d |
|---|---|---|
| entered at the same (stale) fix | +1.76% | +1.54% |
| entered at the next fix | +0.32% | +0.33% |
| *baseline* | *+0.31%* | *+0.61%* |

Everything in `basis_signal()` measures from the next fix.

**It is not momentum in disguise.** Within each trailing-momentum tier, high basis beat low
basis by +1.13 / +1.58 / +1.70pp. The mirror test — momentum within each basis tier — gives
+0.08pp. The basis subsumes momentum, not the other way round.

**It survives walk-forward.** With the zone threshold drawn only from the trailing 500
sessions, high-basis days returned +2.85% against +0.84% for everything else: a +2.02pp edge
over 464 days, using a cutoff that could have been computed on the day.

**It survives a block permutation test** that preserves autocorrelation: p = 0.0038.

### What was rejected along the way

- **Single-day basis extremes** — 78 days collapse to 48 distinct episodes; episode-weighted
  mean +0.93% against a +1.12% baseline, permutation p = 0.61. The day-weighted version only
  looked good because long episodes cluster inside rallies.
- **Sustained backwardation as a scarcity signal** — 35 regimes, mean +2.33% against a +3.66%
  baseline, p = 0.84.
- **Sustained steep contango** — +14.99% against +3.66% and 3-for-3, but n = 3 regimes. An
  anecdote, not evidence.

### The limits, stated on the card itself

The edge is **absent in 4 of the 10 individual years** (2018, 2023, and flat in 2017 and
2026). There are only ten years of usable overlap. Part of the mechanism is probably a
persistent intraday drift regime rather than anything about gold fundamentals, which could
stop working if market structure changes.

Every reading is logged to the same prediction ledger as the MACD calls and scored the same
way, so it will be held to its own record rather than to this README.

## Futures vs bullion

The verdict card shows two prices stacked: the COMEX front-month future and spot bullion
(XAU/USD). They are genuinely different numbers — the future currently trades about 1.25%
above spot, which is financing carry, not a disagreement between feeds.

**Every signal on the page is computed from the future**, because that is the series with
25 years of clean weekly history behind it. The bullion quote is displayed for reference.

Sources, after testing what actually works:

| Source | Status |
|---|---|
| `api.gold-api.com` | **primary** — keyless JSON, refreshes each minute |
| Swissquote public quotes | **fallback** — a broker's own dealable bid/ask, mid taken |
| Yahoo `XAUUSD=X` | rejected — returns a null result consistently |
| goldprice.org | rejected — 403 |

The two working feeds were cross-checked against each other and agreed to within 0.03%
($4,648.50 vs a $4,646.99 mid). `audit.py` now asserts the bullion quote stays within 5% of
the front future, so a feed that stalls or switches units fails the build instead of
quietly publishing a wrong number.

## Accuracy audit

`scripts/audit.py` runs after every build (and in CI, before anything is committed) and
exits non-zero on failure. It checks indicator maths against independent reference
implementations, verifies that nothing computed at bar *i* moves when later bars arrive,
and re-checks the published files for the specific mistakes this project has actually made.

```bash
python scripts/build_data.py && python scripts/audit.py
```

The maths came back clean — EMA, SMA, Wilder RSI and the percentile interpolation all match
reference implementations exactly, and truncating the series leaves every earlier value,
verdict and backfilled band bit-identical. Three real defects turned up elsewhere.

**The call was being read off an unfinished candle.** Yahoo returns more than one trailing
partial bar on a weekly series — the current week *and* a separate bar carrying the live
quote — so trimming the last bar was not enough. Measured against 25 years of daily data,
a weekly verdict read one trading day into the week disagrees with what that same bar
finally says **30.8% of the time**, and is still wrong 20% of the time by Friday. Trailing
partials are now trimmed in a loop, the call is based on the last completed close, and the
forming bar is shown as a separate "this week so far" flag that drives nothing.

**Backtests filled at the signal bar's own close.** A close cannot be traded until it has
printed. Signals now fill one bar late, which costs the weekly position rule 1.4 points of
CAGR (8.12% → 6.71%) and deepens its worst drawdown from −43.6% to −50.1%. The sensitivity
grid uses the same lag, or its cells would not be comparable with the table above them. The
same-bar figure is still shown, in small type, as what the optimistic version claims.

**Logged predictions paired a live tick with a completed bar's timestamp.** An entry stored
the current spot as its t0 price while naming a bar that closed up to a week earlier, so
every horizon was measured from the wrong starting point. Entries now record the close of
the bar they name, and the audit verifies that price against the source series.

## Security posture

The site is a static page on GitHub Pages with no server, no database, no accounts, and no
user input. Notes for anyone auditing it:

- **No secrets anywhere.** Every data source is keyless (Yahoo Finance chart endpoints), so
  the public repo holds no credentials. Nothing needs rotating if the repo is forked.
- **Visitors never talk to a third party.** All price fetching happens server-side in the
  GitHub Action. The browser makes exactly one request — a relative `data.json` on its own
  origin — so no visitor IP ever reaches Yahoo, and there is no CDN, analytics, font host,
  or tracker on the page.
- **Saved predictions are per-device.** They live in `localStorage`, scoped to the origin.
  Nothing is uploaded, nothing is shared between visitors, and one person's saves are
  invisible to everyone else. Clearing site data erases them.
- **CSP is set via meta tag**: `default-src 'none'` with `connect-src 'self'`, so even a
  poisoned `data.json` has no route to send anything anywhere. `frame-ancestors` is omitted
  because it is ignored in meta form; clickjacking cover would need response headers, which
  GitHub Pages does not offer.
- **Stored state is treated as untrusted.** Everything read back from `localStorage` is
  coerced by type on load and the verdict is whitelisted to BUY/HOLD/SELL before it reaches
  `innerHTML`. Tested with a payload in the stored verdict field: it renders as the literal
  text `HOLD` and executes nothing.
- **The Action cannot be triggered by a fork.** It runs on `schedule`, `workflow_dispatch`,
  and `push` to `main` only. There is no `pull_request_target`, so the `contents: write`
  token is never exposed to an outside contributor's code.

What is *not* protected: the ledger in `docs/predictions.json` is world-readable by design,
and the repo is public. Neither contains anything personal — the only data in the project is
the price of gold.

## Not financial advice

This is a technical-indicator readout. MACD lags price by construction, the backtest
excludes costs and slippage, and none of it knows anything about rates, the dollar, or
geopolitics.
