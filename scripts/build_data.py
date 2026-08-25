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
# COMEX gold future first, spot as fallback. Ranges are per-symbol: Yahoo
# truncates "max" on weekly to a sparse 267 bars, and the spot symbol has no
# deep weekly history at all, so each source states what it can actually serve.
SOURCES = [
    ("GC=F", {"1wk": "30y", "1d": "10y"}),
    ("XAUUSD=X", {"1wk": "10y", "1d": "10y"}),
]
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")


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

    # 2. RSI guard -- flag an extended entry, never veto it. Overbought can
    #    stay overbought for months in a real trend.
    r = rs[i]
    if r is not None:
        if verdict == "BUY" and r >= RSI_HOT:
            notes.append("extended")
        elif verdict == "SELL" and r <= RSI_COLD:
            notes.append("washed-out")

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
    if "extended" in notes:
        return (base + f" RSI is {detail['rsi']}, already overbought, so this is a "
                "late entry rather than a fresh one.")
    if "washed-out" in notes:
        return (base + f" RSI is {detail['rsi']}, deeply oversold, so a bounce is "
                "as likely as follow-through.")
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


def _equity(closes, states, start):
    """Long-only: compounded while the state is BUY, flat otherwise. Returns the
    curve plus how many bars were spent in the market."""
    eq, curve, exposure = 1.0, [], 0
    for i in range(start, len(closes) - 1):
        if states[i] == "BUY":
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
    span = len(closes) - 1 - start
    years = span / bars_per_year
    rets = sorted(e["ret"] for e in eps)
    stats = {
        "n": len(eps),
        "cagr": _cagr(curve[-1], years) if curve else None,
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


# ---------------------------------------------------------------- assembly

def series(interval, points):
    last_err = None
    for sym, ranges in SOURCES:
        try:
            res = fetch(sym, interval, ranges[interval])
            rows = candles(res)
            if len(rows) < MA_LEN + SLOW:
                raise ValueError(f"only {len(rows)} candles")

            closes = [r["c"] for r in rows]
            line, sig, hist = macd(closes)
            ma = sma(closes, MA_LEN)
            rs = rsi(closes)
            n = len(closes) - 1

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

            keep = min(points, len(rows))
            r3 = lambda v: None if v is None else round(v, 3)  # noqa: E731
            return {
                "symbol": sym,
                "source": res["meta"].get("fullExchangeName", "Yahoo Finance"),
                "verdict": verdict,
                "raw": raw,
                "notes": notes,
                "why": explain(verdict, raw, notes, detail),
                "detail": detail,
                "last": round(closes[n], 2),
                "prev": round(closes[n - 1], 2),
                "history": len(rows),
                "test": backtest(closes, line, sig, hist, ma, rs,
                                 52 if interval == "1wk" else 252),
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

    payload = {
        "generated": int(time.time()),
        "params": {"fast": FAST, "slow": SLOW, "signal": SIGNAL,
                   "ma": MA_LEN, "rsi": RSI_LEN},
        "spot": spot,
        "primary": "weekly",
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
    print(f"-> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    sys.exit(main())
