#!/usr/bin/env python3
"""Fetch gold history, compute the signal, write docs/data.json.

Runs in GitHub Actions (server side) so the published page only ever reads a
same-origin JSON file -- no API keys in the browser, no CORS.

The call is MACD-driven, then filtered:
  1. MACD(12,26,9) state  -> raw BUY / HOLD / SELL
  2. regime filter        -> a raw call fighting the 50-period MA is cut to HOLD
  3. RSI(14) guard        -> flags an extended entry without vetoing it
Every filter is causal (uses only bars <= i), so backtest() can replay the exact
same function over history without lookahead.
"""
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

FAST, SLOW, SIGNAL = 12, 26, 9
MA_LEN = 50          # regime filter length, in bars of whatever timeframe
RSI_LEN = 14
RSI_HOT, RSI_COLD = 70, 30
# Grid for the sensitivity map. The point is not to find the best cell -- it is to
# see whether the live setting sits on a plateau (robust) or a lone spike (curve-fit).
GRID_FAST = [5, 8, 10, 12, 15, 18, 21]
GRID_SLOW = [17, 21, 26, 30, 34, 40, 50]
# Drawdown-from-high buckets. Measured on 25y of weekly gold, proximity to the
# running high is the only tested input that beats baseline forward returns by a
# wide margin -- and it does so in the opposite direction to "buy the dip".
BUCKETS = [(0, 2, "at the highs"), (2, 5, "shallow dip"), (5, 10, "moderate dip"),
           (10, 20, "correction"), (20, 1e9, "bear market")]
# Calibration feedback. Raw bands drawn from matched history are too narrow: a
# p10-p90 range should hold 80% of outcomes and held 64%. The scale factor that
# fixes it is learned from resolved predictions and generalises -- fitted on the
# first half of the record it lifted unseen second-half coverage from 69% to 88%.
COVERAGE_TARGET = 80.0
CAL_SHRINK = 30      # samples before a fitted factor is trusted at full weight
CAL_MAX = 3.0        # hard ceiling; a band needing more than 3x is not a band
# Bars still inside their own period are excluded from the call. Measured on 25y
# of gold, a weekly verdict read one trading day into the week disagrees with
# what that same bar finally says 30.8% of the time, and is still wrong 20% of
# the time on the Friday. The forming bar is reported separately as provisional.
PERIOD_SECS = {"1wk": 7 * 86400, "1d": 86400}
# Signals are acted on one bar late, because the close that produces a signal is
# only known once the bar has closed. Same-bar fills flatter the record by 1.4
# points of CAGR (8.12% -> 6.76% on the weekly position rule).
EXEC_LAG = 1
# COMEX gold future first, spot as fallback. Ranges are per-symbol: Yahoo
# truncates "max" on weekly to a sparse 267 bars, and the spot symbol has no
# deep weekly history at all, so each source states what it can actually serve.
SOURCES = [
    ("GC=F", {"1wk": "30y", "1d": "10y"}),
    ("XAUUSD=X", {"1wk": "10y", "1d": "10y"}),
]
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
LEDGER = os.path.join(os.path.dirname(__file__), "..", "docs", "predictions.json")


# ---------------------------------------------------------------- fetching

def fetch(symbol, interval, rng):
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol)
        + f"?range={rng}&interval={interval}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return json.load(r)["chart"]["result"][0]
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            if attempt == 2:
                raise
            print(f"  {symbol} {interval} attempt {attempt + 1} failed: {exc}")
            time.sleep(5)


def candles(result):
    """Yahoo pads the arrays with nulls for gaps; drop those rows."""
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        out.append({"t": t, "c": c})
    return out


# ---------------------------------------------------------------- indicators

def ema(values, period):
    k = 2 / (period + 1)
    out, prev = [], None
    for i, v in enumerate(values):
        if i < period - 1:
            out.append(None)
            continue
        prev = sum(values[:period]) / period if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def sma(values, period):
    out, run = [], 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        out.append(run / period if i >= period - 1 else None)
    return out


def rsi(values, period=RSI_LEN):
    """Wilder's RSI."""
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd(closes, fast=FAST, slow=SLOW, signal=SIGNAL):
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i]
            for i in range(len(closes))]
    seed = next((i for i, v in enumerate(line) if v is not None), len(line))
    tail = line[seed:]
    sig = [None] * seed + (ema(tail, signal) if tail else [])
    hist = [None if (line[i] is None or sig[i] is None) else line[i] - sig[i]
            for i in range(len(closes))]
    return line, sig, hist


# ---------------------------------------------------------------- the rule

def raw_state(line, sig, hist, i):
    """MACD alone, before any filtering."""
    if i < 1 or hist[i] is None or hist[i - 1] is None:
        return None
    above = line[i] > sig[i]
    rising = hist[i] > hist[i - 1]
    if above and rising:
        return "BUY"
    if not above and not rising:
        return "SELL"
    return "HOLD"


def evaluate(closes, line, sig, hist, ma, rs, i):
    """The full rule at bar i, using only bars <= i. Returns (verdict, notes)."""
    raw = raw_state(line, sig, hist, i)
    if raw is None or ma[i] is None:
        return None, None

    price_above_ma = closes[i] > ma[i]
    verdict, notes = raw, []

    # 1. Regime filter -- don't fight the 50-bar trend.
    if raw == "BUY" and not price_above_ma:
        verdict = "HOLD"
        notes.append("regime-veto-buy")
    elif raw == "SELL" and price_above_ma:
        verdict = "HOLD"
        notes.append("regime-veto-sell")

    # 2. RSI note. This started life as an "overbought = late entry" warning and
    #    the data said the opposite: bucketed over 25y, weekly RSI 65+ preceded
    #    +17.7% mean 52w returns (82% positive) against +8.2% (67%) for RSI < 35.
    #    On gold, strong momentum has been confirmation, not a reason to hesitate.
    r = rs[i]
    if r is not None:
        if verdict == "BUY" and r >= RSI_HOT:
            notes.append("momentum-strong")
        elif r <= RSI_COLD:
            notes.append("momentum-weak")

    return verdict, notes


def explain(verdict, raw, notes, detail):
    """Plain-English reasoning shown on the card."""
    base = {
        "BUY": "MACD is above its signal line and the histogram is still expanding, "
               "so upside momentum is building.",
        "SELL": "MACD is below its signal line and the histogram is still falling, "
                "so downside momentum is building.",
    }.get(raw, "MACD's histogram is contracting, so the current move is losing steam.")

    if "regime-veto-buy" in notes:
        return (base + " But price is below its 50-bar average, so this is a bounce "
                "inside a downtrend - cut to HOLD.")
    if "regime-veto-sell" in notes:
        return (base + " But price is above its 50-bar average, so this reads as a "
                "pullback inside an uptrend - cut to HOLD.")
    if "momentum-strong" in notes:
        return (base + f" RSI is {detail['rsi']} - historically that has been "
                "confirmation on gold, not a reason to hesitate.")
    if "momentum-weak" in notes:
        return (base + f" RSI is {detail['rsi']}, and weak momentum has been the "
                "worse setup on gold, not the bargain it looks like.")
    if verdict == "HOLD":
        return base + " Trend and momentum disagree, so there is no clean edge here."
    return base + " Price is on the right side of its 50-bar average, so the trend agrees."


# ---------------------------------------------------------------- backtest

def _series_states(closes, line, sig, hist, ma, rs):
    return [evaluate(closes, line, sig, hist, ma, rs, i)[0] for i in range(len(closes))]


def _as_position(states):
    """HOLD means 'keep what you had' rather than 'go flat'. This is how a person
    actually trades the light, and it tests very differently from the raw state."""
    out, cur = [], None
    for v in states:
        if v is None:
            out.append(None)
            continue
        if v in ("BUY", "SELL"):
            cur = v
        out.append(cur)
    return out


def _equity(closes, states, start, lag=EXEC_LAG):
    """Long-only: compounded while the state is BUY, flat otherwise.

    `lag` bars of delay between the signal and the fill. A signal is produced by
    a bar's close, so the earliest you could act on it is the next bar -- lag=0
    would be buying at a price you did not yet know.
    """
    eq, curve, exposure = 1.0, [], 0
    for i in range(start, len(closes) - 1):
        j = i - lag
        if j >= start and states[j] == "BUY":
            eq *= closes[i + 1] / closes[i]
            exposure += 1
        curve.append(eq)
    return curve, exposure


def _max_drawdown(curve):
    peak, worst = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return round(worst * 100, 1)


def _cagr(total, years):
    return round((total ** (1 / years) - 1) * 100, 2) if years > 0 and total > 0 else None


def _episodes(closes, states, start):
    eps, i = [], start
    while i < len(states):
        s, j = states[i], i
        while j + 1 < len(states) and states[j + 1] == s:
            j += 1
        if s == "BUY" and j + 1 < len(states):
            eps.append({"ret": (closes[j + 1] - closes[i]) / closes[i] * 100,
                        "bars": j + 1 - i})
        i = j + 1
    return eps


def _score(closes, states, start, bars_per_year):
    eps = _episodes(closes, states, start)
    curve, exposure = _equity(closes, states, start)
    curve0, _ = _equity(closes, states, start, lag=0)
    span = len(closes) - 1 - start
    years = span / bars_per_year
    rets = sorted(e["ret"] for e in eps)
    stats = {
        "n": len(eps),
        "cagr": _cagr(curve[-1], years) if curve else None,
        "cagrNoLag": _cagr(curve0[-1], years) if curve0 else None,
        "maxDD": _max_drawdown(curve) if curve else None,
        "exposure": round(exposure / span * 100) if span else 0,
    }
    if rets:
        stats.update({
            "winRate": round(sum(1 for r in rets if r > 0) / len(rets) * 100),
            "avg": round(sum(rets) / len(rets), 2),
            "median": round(rets[len(rets) // 2], 2),
            "best": round(rets[-1], 2),
            "worst": round(rets[0], 2),
            "avgBars": round(sum(e["bars"] for e in eps) / len(eps), 1),
        })
    return stats


def backtest(closes, line, sig, hist, ma, rs, bars_per_year):
    """Replay the exact live rule bar by bar. Entry and exit both at the close of
    the bar the state changed on -- no lookahead, no intrabar fills, no costs.

    Two readings are scored because they answer different questions:
      signal   -- long only while the light literally says BUY
      position -- long from BUY until SELL, treating HOLD as 'stay put'
    Both are measured against buy-and-hold over the identical window.
    """
    states = _series_states(closes, line, sig, hist, ma, rs)
    start = next((i for i, s in enumerate(states) if s is not None), len(states))
    if start >= len(closes) - 2:
        return {"bars": 0}

    span = len(closes) - 1 - start
    years = span / bars_per_year
    hold_curve = []
    eq = 1.0
    for i in range(start, len(closes) - 1):
        eq *= closes[i + 1] / closes[i]
        hold_curve.append(eq)

    return {
        "bars": span,
        "years": round(years, 1),
        "signal": _score(closes, states, start, bars_per_year),
        "position": _score(closes, _as_position(states), start, bars_per_year),
        "hold": {"cagr": _cagr(hold_curve[-1], years),
                 "maxDD": _max_drawdown(hold_curve), "exposure": 100},
    }


# ---------------------------------------------------------------- sensitivity

def _position_cagr(closes, ma, fast, slow, signal, bars_per_year):
    """CAGR of the position rule for one parameter triple. Same rule as live."""
    line, sig, hist = macd(closes, fast, slow, signal)
    states, cur = [], None
    for i in range(len(closes)):
        raw = raw_state(line, sig, hist, i)
        if raw is None or ma[i] is None:
            states.append(None)
            continue
        v = raw
        if raw == "BUY" and closes[i] <= ma[i]:
            v = "HOLD"
        elif raw == "SELL" and closes[i] > ma[i]:
            v = "HOLD"
        if v in ("BUY", "SELL"):
            cur = v
        states.append(cur)
    start = next((i for i, v in enumerate(states) if v is not None), len(states))
    if start >= len(closes) - 2:
        return None
    # Same execution lag as the headline backtest, or the grid would flatter
    # every cell against a table it is meant to be comparable with.
    eq = 1.0
    for i in range(start, len(closes) - 1):
        j = i - EXEC_LAG
        if j >= start and states[j] == "BUY":
            eq *= closes[i + 1] / closes[i]
    return _cagr(eq, (len(closes) - 1 - start) / bars_per_year)


def sensitivity(closes, ma, hold_cagr, bars_per_year):
    """Sweep fast/slow at the live signal length, so the page can show whether
    12/26/9 is a plateau or a fluke."""
    matrix, flat = [], []
    for f in GRID_FAST:
        row = []
        for sl in GRID_SLOW:
            v = None if f >= sl else _position_cagr(closes, ma, f, sl, SIGNAL, bars_per_year)
            row.append(None if v is None else round(v, 2))
            if v is not None:
                flat.append(v)
        matrix.append(row)

    live = _position_cagr(closes, ma, FAST, SLOW, SIGNAL, bars_per_year)
    ranked = sorted(flat, reverse=True)
    ordered = sorted(flat)
    return {
        "fast": GRID_FAST,
        "slow": GRID_SLOW,
        "matrix": matrix,
        "live": None if live is None else round(live, 2),
        "liveRank": (ranked.index(live) + 1) if live in ranked else None,
        "n": len(flat),
        "best": round(max(flat), 2) if flat else None,
        "worst": round(min(flat), 2) if flat else None,
        "median": round(ordered[len(ordered) // 2], 2) if flat else None,
        "beatHold": sum(1 for v in flat if hold_cagr is not None and v >= hold_cagr),
        "spread": round(max(flat) - min(flat), 2) if flat else None,
    }


# ---------------------------------------------------------------- proximity

def _running_max(closes):
    out, m = [], closes[0]
    for c in closes:
        m = max(m, c)
        out.append(m)
    return out


def _bucket_of(dd):
    for lo, hi, label in BUCKETS:
        if lo <= dd < hi:
            return label
    return BUCKETS[-1][2]


def _fwd_stats(closes, rows, h, min_n):
    n = len(closes)
    xs = [(closes[i + h] - closes[i]) / closes[i] * 100 for i in rows if i + h < n]
    if len(xs) < min_n:
        return None
    return {"mean": round(sum(xs) / len(xs), 2),
            "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100),
            "n": len(xs)}


def proximity(closes, ma, horizon, min_n=20):
    """For each drawdown-from-high bucket, what actually happened next -- scored
    over the full window and over each half separately.

    The split is the point. Measured on the full window alone the buckets decline
    neatly with depth, which reads as a law; scored per half that ordering does not
    survive, so the page shows both rather than the flattering aggregate.
    """
    peaks = _running_max(closes)
    n = len(closes)
    dd = [(peaks[i] - closes[i]) / peaks[i] * 100 for i in range(n)]
    mid = n // 2
    windows = {"all": range(n), "first": range(mid), "second": range(mid, n)}

    base = {k: _fwd_stats(closes, list(w), horizon, min_n) for k, w in windows.items()}

    table = []
    for lo, hi, label in BUCKETS:
        entry = {"label": label, "lo": lo, "hi": None if hi > 1e8 else hi, "fwd": {}}
        for key, w in windows.items():
            rows = [i for i in w if lo <= dd[i] < hi]
            entry["fwd"][key] = _fwd_stats(closes, rows, horizon, min_n)
        # Stable only if it lands the same side of its own era's baseline in both halves.
        a, b = entry["fwd"]["first"], entry["fwd"]["second"]
        if a and b and base["first"] and base["second"]:
            entry["stable"] = (a["mean"] > base["first"]["mean"]) ==                               (b["mean"] > base["second"]["mean"])
        else:
            entry["stable"] = None
        table.append(entry)

    i = n - 1
    return {
        "horizon": horizon,
        "baseline": base,
        "table": table,
        "now": {
            "drawdown": round(dd[i], 1),
            "bucket": _bucket_of(dd[i]),
            "peak": round(peaks[i], 2),
            "uptrend": bool(ma[i] is not None and closes[i] > ma[i]),
        },
    }


# ---------------------------------------------------------------- outlook

def _pctile(sorted_xs, q):
    if not sorted_xs:
        return None
    k = (len(sorted_xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def outlook(closes, line, sig, hist, ma, rs, horizons, min_n=40):
    """What actually happened next, from setups that looked like today's.

    This is a base rate with its spread shown, not a forecast. Nothing here
    predicts -- it reports the distribution of outcomes from matching history and
    lets the width of that distribution speak for itself.
    """
    n = len(closes)
    states = _series_states(closes, line, sig, hist, ma, rs)
    peaks = _running_max(closes)
    dd = [(peaks[i] - closes[i]) / peaks[i] * 100 for i in range(n)]
    buckets = [_bucket_of(x) for x in dd]

    i = n - 1
    here_state, here_bucket = states[i], buckets[i]

    # Tighten the match if history allows it, loosen it when it doesn't, and say
    # which one was actually used.
    tiers = [
        ("state and drawdown zone",
         [j for j in range(n - 1) if states[j] == here_state and buckets[j] == here_bucket]),
        ("signal state alone",
         [j for j in range(n - 1) if states[j] == here_state]),
        ("the whole record", list(range(n - 1))),
    ]
    basis, rows = tiers[-1]
    for label, cand in tiers:
        if len(cand) >= min_n:
            basis, rows = label, cand
            break

    out = {"basis": basis, "matches": len(rows), "state": here_state,
           "bucket": here_bucket, "horizons": horizons, "bands": {}, "skill": {}}

    for h in horizons:
        xs = sorted((closes[j + h] - closes[j]) / closes[j] * 100
                    for j in rows if j + h < n)
        if len(xs) < 20:
            continue
        base = sorted((closes[j + h] - closes[j]) / closes[j] * 100
                      for j in range(n - h))
        out["bands"][str(h)] = {
            "n": len(xs),
            "p10": round(_pctile(xs, .10), 2),
            "p25": round(_pctile(xs, .25), 2),
            "p50": round(_pctile(xs, .50), 2),
            "p75": round(_pctile(xs, .75), 2),
            "p90": round(_pctile(xs, .90), 2),
            "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100),
            "baseMedian": round(_pctile(base, .50), 2),
            "baseWin": round(sum(1 for x in base if x > 0) / len(base) * 100),
        }

    # Measured skill: does the signal state separate forward returns at all?
    h = horizons[len(horizons) // 2]
    by = {}
    for st in ("BUY", "HOLD", "SELL"):
        xs = [(closes[j + h] - closes[j]) / closes[j] * 100
              for j in range(n - h) if states[j] == st]
        if len(xs) >= 20:
            by[st] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 2),
                      "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100)}
    if by:
        means = [v["mean"] for v in by.values()]
        out["skill"] = {"horizon": h, "byState": by,
                        "spread": round(max(means) - min(means), 2)}
    return out


# ---------------------------------------------------------------- calibration

def _coverage(samples, k):
    """Share of resolved outcomes inside the band once its half-widths are
    scaled by k about the median."""
    hit = 0
    for ret, p10, p50, p90 in samples:
        if p50 - (p50 - p10) * k <= ret <= p50 + (p90 - p50) * k:
            hit += 1
    return hit / len(samples) * 100


def _solve_k(samples, target=COVERAGE_TARGET):
    """Smallest scale factor reaching the target coverage, by bisection."""
    lo, hi = 0.5, CAL_MAX * 2
    for _ in range(40):
        mid = (lo + hi) / 2
        if _coverage(samples, mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def learn_calibration(ledger, timeframe, min_n=25):
    """Per-horizon band scale factors, learned only from horizons that have
    actually resolved.

    Shrunk toward 1.0 while the sample is thin, so an early run of luck cannot
    swing the bands, and capped. Nothing here touches direction: a wider band
    says 'less certain', it does not say 'more right'.
    """
    by_h = {}
    for e in ledger["entries"]:
        if e["timeframe"] != timeframe:
            continue
        for h, r in e["results"].items():
            b = e["bands"].get(h)
            if not b:
                continue
            # Train on what was shown at the time: an entry whose band was
            # already scaled is un-scaled first, so factors don't compound.
            prior = e.get("k", {}).get(h, 1.0) or 1.0
            p50 = b["p50"]
            by_h.setdefault(h, []).append(
                (r["ret"], p50 - (p50 - b["p10"]) / prior,
                 p50, p50 + (b["p90"] - p50) / prior))

    out = {}
    for h, samples in by_h.items():
        if len(samples) < min_n:
            continue
        raw = _solve_k(samples)
        weight = len(samples) / (len(samples) + CAL_SHRINK)
        k = 1.0 + (raw - 1.0) * weight
        out[h] = {
            "k": round(min(max(k, 1.0), CAL_MAX), 3),
            "raw": round(raw, 3),
            "n": len(samples),
            "before": round(_coverage(samples, 1.0)),
            "after": round(_coverage(samples, k)),
        }
    return out


def apply_calibration(outlook_obj, cal):
    """Widen the live bands by the learned factor and record which factor was
    used, so the next scorecard can tell calibrated calls from raw ones."""
    used = {}
    for h, band in outlook_obj["bands"].items():
        k = cal.get(h, {}).get("k", 1.0)
        used[h] = k
        if k == 1.0:
            continue
        p50 = band["p50"]
        band["p10"] = round(p50 - (p50 - band["p10"]) * k, 2)
        band["p25"] = round(p50 - (p50 - band["p25"]) * k, 2)
        band["p75"] = round(p50 + (band["p75"] - p50) * k, 2)
        band["p90"] = round(p50 + (band["p90"] - p50) * k, 2)
    outlook_obj["k"] = used
    outlook_obj["calibration"] = cal
    return outlook_obj


# ---------------------------------------------------------------- ledger

def _load_ledger():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return data
    except (OSError, ValueError):
        pass
    return {"entries": []}


def _price_at(rows, ts, tol):
    """Close on the first bar at or after ts, or None.

    Returns None when ts falls before the series starts, and when the nearest
    later bar is more than `tol` seconds past due. Without both guards a due date
    outside the data silently resolves against the first available bar, which
    scores a 2005 prediction against a 2016 price.
    """
    if not rows or ts < rows[0]["t"]:
        return None, None
    for row in rows:
        if row["t"] >= ts:
            return (row["c"], row["t"]) if row["t"] - ts <= tol else (None, None)
    return None, None


def log_prediction(ledger, timeframe, s, spot, bars_per_year):
    """One entry per timeframe per bar. Re-running the build on the same bar
    updates nothing -- the record is written once, when the call was live."""
    bar_ts = s["candles"][-1]["t"]
    key = f"{timeframe}-{bar_ts}"
    if any(e["id"] == key for e in ledger["entries"]):
        return None

    secs = int(365.25 * 24 * 3600 / bars_per_year)
    o = s["outlook"]
    entry = {
        "id": key,
        "timeframe": timeframe,
        "barTs": bar_ts,
        "made": int(time.time()),
        "spot": spot,
        "verdict": s["verdict"],
        "raw": s["raw"],
        "notes": s["notes"],
        "rsi": s["detail"]["rsi"],
        "maGap": s["detail"]["maGap"],
        "bucket": o["bucket"],
        "basis": o["basis"],
        "matches": o["matches"],
        "bands": {h: {"p10": b["p10"], "p50": b["p50"], "p90": b["p90"]}
                  for h, b in o["bands"].items()},
        "k": o.get("k", {}),
        "dueAt": {h: bar_ts + int(h) * secs for h in o["bands"]},
        "results": {},
    }
    ledger["entries"].append(entry)
    return entry


def score_ledger(ledger, rows_by_tf, tol_by_tf):
    """Resolve every horizon whose due date has passed. Idempotent: a horizon
    already scored is never rewritten, so the record can't drift."""
    scored = 0
    for e in ledger["entries"]:
        rows = rows_by_tf.get(e["timeframe"])
        if not rows:
            continue
        for h, due in e.get("dueAt", {}).items():
            if h in e["results"]:
                continue
            price, at = _price_at(rows, due, tol_by_tf[e["timeframe"]])
            if price is None:
                continue
            band = e["bands"][h]
            ret = (price - e["spot"]) / e["spot"] * 100
            up = e["verdict"] == "BUY"
            down = e["verdict"] == "SELL"
            e["results"][h] = {
                "actual": round(price, 2),
                "at": at,
                "ret": round(ret, 2),
                "inBand": band["p10"] <= ret <= band["p90"],
                "vsMedian": "above" if ret > band["p50"] else "below",
                # A HOLD makes no directional claim, so it is scored as such
                # rather than being quietly counted as a win.
                "direction": None if not (up or down) else ((ret > 0) == up),
                "error": round(ret - band["p50"], 2),
            }
            scored += 1
    return scored


def scorecard(ledger, timeframe):
    """How the calls have actually done. p10-p90 should contain ~80% of outcomes
    if the bands are honest; far more means they are too wide to say anything."""
    res = []
    for e in ledger["entries"]:
        if e["timeframe"] != timeframe:
            continue
        for h, r in e["results"].items():
            res.append((h, e, r))
    if not res:
        return {"n": 0, "pending": sum(1 for e in ledger["entries"]
                                       if e["timeframe"] == timeframe)}

    by_h = {}
    for h, _e, r in res:
        by_h.setdefault(h, []).append(r)

    horizons = {}
    for h, rs in sorted(by_h.items(), key=lambda kv: int(kv[0])):
        dirs = [r["direction"] for r in rs if r["direction"] is not None]
        errs = sorted(r["error"] for r in rs)
        horizons[h] = {
            "n": len(rs),
            "inBand": round(sum(1 for r in rs if r["inBand"]) / len(rs) * 100),
            "aboveMedian": round(sum(1 for r in rs if r["vsMedian"] == "above") / len(rs) * 100),
            "directionN": len(dirs),
            "direction": round(sum(1 for d in dirs if d) / len(dirs) * 100) if dirs else None,
            "medianError": round(errs[len(errs) // 2], 2),
            "meanAbsError": round(sum(abs(x) for x in errs) / len(errs), 2),
        }

    allr = [r for _h, _e, r in res]
    alld = [r["direction"] for r in allr if r["direction"] is not None]
    cal_r = [r for _h, e, r in res if (e.get("k") or {}) and
             max((e.get("k") or {}).values(), default=1.0) > 1.0]
    raw_r = [r for _h, e, r in res if r not in cal_r]
    return {
        "calibrated": {"n": len(cal_r),
                       "inBand": round(sum(1 for r in cal_r if r["inBand"]) / len(cal_r) * 100)
                       if cal_r else None},
        "uncalibrated": {"n": len(raw_r),
                         "inBand": round(sum(1 for r in raw_r if r["inBand"]) / len(raw_r) * 100)
                         if raw_r else None},
        "n": len(allr),
        "pending": sum(len(e.get("dueAt", {})) - len(e["results"])
                       for e in ledger["entries"] if e["timeframe"] == timeframe),
        "inBand": round(sum(1 for r in allr if r["inBand"]) / len(allr) * 100),
        "direction": round(sum(1 for d in alld if d) / len(alld) * 100) if alld else None,
        "directionN": len(alld),
        "horizons": horizons,
    }


def _bands_at(closes, states, buckets, i, horizons, min_n=40):
    """The outlook bands as they would have been computed standing at bar i.

    Matching rows are drawn from j < i, and a match only contributes a forward
    return for horizon h when j + h <= i -- i.e. when that outcome had already
    happened. Nothing here can see past bar i.
    """
    here_state, here_bucket = states[i], buckets[i]
    if here_state is None:
        return None, None, 0
    tiers = [
        ("state and drawdown zone",
         [j for j in range(i) if states[j] == here_state and buckets[j] == here_bucket]),
        ("signal state alone", [j for j in range(i) if states[j] == here_state]),
        ("the whole record", list(range(i))),
    ]
    basis, rows = tiers[-1]
    for label, cand in tiers:
        if len(cand) >= min_n:
            basis, rows = label, cand
            break

    bands = {}
    for h in horizons:
        xs = sorted((closes[j + h] - closes[j]) / closes[j] * 100
                    for j in rows if j + h <= i)
        if len(xs) < 20:
            continue
        bands[str(h)] = {"p10": round(_pctile(xs, .10), 2),
                         "p50": round(_pctile(xs, .50), 2),
                         "p90": round(_pctile(xs, .90), 2)}
    return bands, basis, len(rows)


def backfill(ledger, closes, rows_ts, line, sig, hist, ma, rs, horizons,
             timeframe, bars_per_year, step):
    """Replay the app over history so the scorecard has something to say today.

    Entries are marked backfilled so they are never mixed with live calls. The
    bands are causal, but this is still a replay -- it tests whether the bands
    are calibrated, not whether the app called anything in real time.
    """
    n = len(closes)
    states = _series_states(closes, line, sig, hist, ma, rs)
    peaks = _running_max(closes)
    buckets = [_bucket_of((peaks[k] - closes[k]) / peaks[k] * 100) for k in range(n)]
    secs = int(365.25 * 24 * 3600 / bars_per_year)
    existing = {e["id"] for e in ledger["entries"]}
    added = 0

    start = next((k for k in range(n) if states[k] is not None), n) + 200
    for i in range(start, n, step):
        key = f"{timeframe}-{rows_ts[i]}"
        if key in existing:
            continue
        bands, basis, matches = _bands_at(closes, states, buckets, i, horizons)
        if not bands:
            continue
        ledger["entries"].append({
            "id": key, "timeframe": timeframe, "barTs": rows_ts[i],
            "made": rows_ts[i], "backfilled": True,
            "spot": round(closes[i], 2), "verdict": states[i], "raw": states[i],
            "notes": [], "rsi": round(rs[i], 1) if rs[i] is not None else None,
            "maGap": round((closes[i] - ma[i]) / ma[i] * 100, 1) if ma[i] else None,
            "bucket": buckets[i], "basis": basis, "matches": matches,
            "bands": bands,
            "dueAt": {h: rows_ts[i] + int(h) * secs for h in bands},
            "results": {},
        })
        added += 1
    return added


def _recent(ledger, keep=14):
    """A trimmed slice of the ledger for the page, newest first. The full record
    stays in predictions.json."""
    out = []
    for e in sorted(ledger["entries"], key=lambda x: x["barTs"], reverse=True)[:keep]:
        out.append({
            "id": e["id"], "timeframe": e["timeframe"], "barTs": e["barTs"],
            "backfilled": bool(e.get("backfilled")), "spot": e["spot"],
            "verdict": e["verdict"], "bucket": e["bucket"],
            "bands": e["bands"], "dueAt": e["dueAt"], "results": e["results"],
        })
    return out


# ---------------------------------------------------------------- assembly

def series(interval, points):
    last_err = None
    for sym, ranges in SOURCES:
        try:
            res = fetch(sym, interval, ranges[interval])
            all_rows = candles(res)
            if len(all_rows) < MA_LEN + SLOW:
                raise ValueError(f"only {len(all_rows)} candles")

            # Everything below -- verdict, bands, ledger -- runs on closed bars.
            # Yahoo appends more than one unfinished bar: on a weekly series it
            # returns both the current week and a separate bar carrying the live
            # quote, so trailing partials are trimmed in a loop rather than once.
            period = PERIOD_SECS[interval]
            now = time.time()
            keep_n = len(all_rows)
            while keep_n > MA_LEN + SLOW + 1 and (now - all_rows[keep_n - 1]["t"]) < period:
                keep_n -= 1
            forming = keep_n < len(all_rows)
            rows = all_rows[:keep_n]
            closes = [r["c"] for r in rows]
            line, sig, hist = macd(closes)
            ma = sma(closes, MA_LEN)
            rs = rsi(closes)
            n = len(closes) - 1

            bpy = 52 if interval == "1wk" else 252
            test = backtest(closes, line, sig, hist, ma, rs, bpy)
            verdict, notes = evaluate(closes, line, sig, hist, ma, rs, n)
            if verdict is None:
                raise ValueError("indicators not warmed up")
            raw = raw_state(line, sig, hist, n)

            # bars since the MACD histogram last changed sign
            age, j = 0, n
            while j > 0 and hist[j] is not None and hist[j - 1] is not None \
                    and (hist[j] > 0) == (hist[j - 1] > 0):
                age += 1
                j -= 1

            detail = {
                "macd": round(line[n], 2),
                "signal": round(sig[n], 2),
                "hist": round(hist[n], 2),
                "crossAge": age,
                "rsi": round(rs[n], 1) if rs[n] is not None else None,
                "ma": round(ma[n], 2),
                "maGap": round((closes[n] - ma[n]) / ma[n] * 100, 1),
                "regime": "uptrend" if closes[n] > ma[n] else "downtrend",
            }

            # The provisional read includes the forming bar, and is shown as a
            # secondary number rather than driving anything.
            provisional = None
            if forming:
                pc = [r["c"] for r in all_rows]
                pl, ps, ph = macd(pc)
                pv, pn = evaluate(pc, pl, ps, ph, sma(pc, MA_LEN), rsi(pc), len(pc) - 1)
                provisional = {"verdict": pv, "notes": pn or [],
                               "hist": round(ph[-1], 2) if ph[-1] is not None else None,
                               "barTs": all_rows[-1]["t"]}

            keep = min(points, len(rows))
            raw_rows = rows
            r3 = lambda v: None if v is None else round(v, 3)  # noqa: E731
            return {
                "symbol": sym,
                "source": res["meta"].get("fullExchangeName", "Yahoo Finance"),
                "forming": bool(forming),
                "provisional": provisional,
                "asOf": rows[-1]["t"],
                "verdict": verdict,
                "raw": raw,
                "notes": notes,
                "why": explain(verdict, raw, notes, detail),
                "detail": detail,
                "last": round(closes[n], 2),
                "prev": round(closes[n - 1], 2),
                "history": len(rows),
                "test": test,
                "grid": sensitivity(closes, ma, test["hold"]["cagr"], bpy),
                "proximity": proximity(closes, ma, 52 if interval == "1wk" else 252),
                "outlook": outlook(closes, line, sig, hist, ma, rs,
                                   [4, 13, 26, 52] if interval == "1wk" else [21, 63, 126, 252]),
                "_rows": raw_rows,
                "_hist": raw_rows,
                "candles": [
                    {"t": rows[i]["t"], "c": round(rows[i]["c"], 2),
                     "m": r3(line[i]), "s": r3(sig[i]), "h": r3(hist[i]),
                     "a": r3(ma[i])}
                    for i in range(len(rows) - keep, len(rows))
                ],
            }
        except Exception as exc:  # noqa: BLE001 - try the next symbol
            last_err = exc
            print(f"  {sym} {interval} unusable: {exc}")
    raise SystemExit(f"all sources failed for {interval}: {last_err}")


def main():
    print("building weekly...")
    weekly = series("1wk", 160)
    print("building daily...")
    daily = series("1d", 260)

    spot = weekly["last"]
    try:
        spot = round(fetch(SOURCES[0][0], "1d", "5d")["meta"]["regularMarketPrice"], 2)
    except Exception as exc:  # noqa: BLE001 - spot is cosmetic
        print(f"  spot quote unavailable: {exc}")

    rows_by_tf = {"weekly": weekly["_rows"], "daily": daily["_rows"]}
    # A due date must land on a bar close to it: ~2 weeks for weekly, ~5 days for
    # daily. Anything further out means the series has a hole and the horizon
    # stays unscored rather than being resolved against the wrong price.
    tol_by_tf = {"weekly": 14 * 86400, "daily": 5 * 86400}

    ledger = _load_ledger()
    if not any(e.get("backfilled") for e in ledger["entries"]):
        filled = 0
        for tf, s_, bpy, hz, step in (("weekly", weekly, 52, [4, 13, 26, 52], 8),
                                      ("daily", daily, 252, [21, 63, 126, 252], 40)):
            c = [r["c"] for r in s_["_hist"]]
            ts = [r["t"] for r in s_["_hist"]]
            ln, sg, ht = macd(c)
            filled += backfill(ledger, c, ts, ln, sg, ht, sma(c, MA_LEN), rsi(c),
                               hz, tf, bpy, step)
        print(f"  ledger: backfilled {filled} historical entries")
    cal = {tf: learn_calibration(ledger, tf) for tf in ("weekly", "daily")}
    apply_calibration(weekly["outlook"], cal["weekly"])
    apply_calibration(daily["outlook"], cal["daily"])
    for tf, c in cal.items():
        if c:
            bits = ", ".join(f"{h}:{v['k']}x ({v['before']}%->{v['after']}%, n={v['n']})"
                             for h, v in sorted(c.items(), key=lambda kv: int(kv[0])))
            print(f"  calibration {tf}: {bits}")

    made = [log_prediction(ledger, "weekly", weekly, weekly["last"], 52),
            log_prediction(ledger, "daily", daily, daily["last"], 252)]
    resolved = score_ledger(ledger, rows_by_tf, tol_by_tf)
    ledger["updated"] = int(time.time())
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, separators=(",", ":"))
    print(f"  ledger: {sum(1 for m in made if m)} logged, {resolved} resolved, "
          f"{len(ledger['entries'])} total")

    for s_ in (weekly, daily):
        s_.pop("_rows", None)
        s_.pop("_hist", None)

    payload = {
        "generated": int(time.time()),
        "scorecard": {"weekly": scorecard(ledger, "weekly"),
                      "daily": scorecard(ledger, "daily")},
        "recent": _recent(ledger),
        "params": {"fast": FAST, "slow": SLOW, "signal": SIGNAL,
                   "ma": MA_LEN, "rsi": RSI_LEN},
        "spot": spot,
        "primary": "weekly",
        "anchor": {"weekly": weekly["last"], "daily": daily["last"]},
        "weekly": weekly,
        "daily": daily,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    for name, s in (("weekly", weekly), ("daily", daily)):
        t = s["test"]
        print(f"  {name}: {s['verdict']} (raw {s['raw']}, {s['notes']}) @ {s['last']}")
        print(f"    {s['history']} bars / {t['years']}y  "
              f"signal {t['signal']['cagr']}% CAGR dd{t['signal']['maxDD']}%  |  "
              f"position {t['position']['cagr']}% dd{t['position']['maxDD']}% "
              f"(n={t['position']['n']}, win {t['position'].get('winRate')}%)  |  "
              f"hold {t['hold']['cagr']}% dd{t['hold']['maxDD']}%")
        g = s["grid"]
        print(f"    grid: {g['n']} combos, {g['worst']}%..{g['best']}% "
              f"(spread {g['spread']}), live rank {g['liveRank']}, "
              f"{g['beatHold']} beat hold")
        pr = s["proximity"]
        cur = next(b for b in pr["table"] if b["label"] == pr["now"]["bucket"])
        o = s["outlook"]
        far = o["bands"].get(str(o["horizons"][-1]))
        if far:
            print(f"    outlook ({o['basis']}, n={o['matches']}): "
                  f"p10 {far['p10']}% / median {far['p50']}% / p90 {far['p90']}% "
                  f"| state spread {o['skill'].get('spread')}pp")
        stable = sum(1 for b in pr["table"] if b["stable"])
        rated = sum(1 for b in pr["table"] if b["stable"] is not None)
        f = cur["fwd"]["all"]
        print(f"    now {pr['now']['drawdown']}% off high -> '{pr['now']['bucket']}': "
              f"{f['mean'] if f else None}% fwd vs baseline "
              f"{pr['baseline']['all']['mean']}% | {stable}/{rated} buckets "
              f"hold their side of baseline in both halves")
    print(f"-> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    sys.exit(main())
