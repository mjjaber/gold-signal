#!/usr/bin/env python3
"""Fetch gold history, compute MACD, write docs/data.json.

Runs in GitHub Actions (server side) so the published page only ever reads a
same-origin JSON file -- no API keys in the browser, no CORS.
"""
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request

FAST, SLOW, SIGNAL = 12, 26, 9
SYMBOLS = ["GC=F", "XAUUSD=X"]  # COMEX gold future, spot fallback
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")


def fetch(symbol, interval, rng):
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?range={rng}&interval={interval}"
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
            print(f"  {symbol} {interval} attempt {attempt+1} failed: {exc}")
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
        out.append({
            "t": t,
            "o": q["open"][i] or c,
            "h": q["high"][i] or c,
            "l": q["low"][i] or c,
            "c": c,
        })
    return out


def ema(values, period):
    k = 2 / (period + 1)
    out = []
    prev = None
    for i, v in enumerate(values):
        if i < period - 1:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[: period]) / period
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def macd(closes, fast=FAST, slow=SLOW, signal=SIGNAL):
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i]
            for i in range(len(closes))]
    seed = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_in = line[seed:]
    sig_raw = ema(sig_in, signal) if sig_in else []
    sig = [None] * seed + sig_raw
    hist = [None if (line[i] is None or sig[i] is None) else line[i] - sig[i]
            for i in range(len(closes))]
    return line, sig, hist


def classify(line, sig, hist):
    """MACD state -> BUY / HOLD / SELL, plus the reasoning shown in the UI."""
    i = len(hist) - 1
    while i >= 0 and hist[i] is None:
        i -= 1
    if i < 1:
        return "HOLD", "Not enough history to compute MACD yet.", {}

    h, hp = hist[i], hist[i - 1]
    above = line[i] > sig[i]
    rising = h > hp
    zero_side = "above" if line[i] > 0 else "below"

    # How many bars since the last MACD/signal cross.
    bars = 0
    j = i
    while j > 0 and (hist[j] > 0) == (hist[j - 1] > 0):
        bars += 1
        j -= 1

    if above and rising:
        verdict = "BUY"
        why = ("MACD is above its signal line and the histogram is still "
               "expanding - momentum is building to the upside.")
    elif above and not rising:
        verdict = "HOLD"
        why = ("MACD is still above its signal line but the histogram is "
               "shrinking - the up-move is losing steam.")
    elif not above and not rising:
        verdict = "SELL"
        why = ("MACD is below its signal line and the histogram is still "
               "falling - momentum is building to the downside.")
    else:
        verdict = "HOLD"
        why = ("MACD is below its signal line but the histogram is "
               "contracting - the down-move is losing steam.")

    detail = {
        "macd": round(line[i], 2),
        "signal": round(sig[i], 2),
        "hist": round(h, 2),
        "prevHist": round(hp, 2),
        "crossAge": bars,
        "zeroSide": zero_side,
        "trend": "rising" if rising else "falling",
    }
    return verdict, why, detail


def series(interval, rng, points):
    last_err = None
    for sym in SYMBOLS:
        try:
            res = fetch(sym, interval, rng)
            rows = candles(res)
            if len(rows) < SLOW + SIGNAL:
                raise ValueError(f"only {len(rows)} candles")
            closes = [r["c"] for r in rows]
            line, sig, hist = macd(closes)
            verdict, why, detail = classify(line, sig, hist)
            n = min(points, len(rows))
            r3 = lambda v: None if v is None else round(v, 3)  # noqa: E731
            return {
                "symbol": sym,
                "source": res["meta"].get("fullExchangeName", "Yahoo Finance"),
                "verdict": verdict,
                "why": why,
                "detail": detail,
                "last": round(closes[-1], 2),
                "prev": round(closes[-2], 2),
                "lastBar": rows[-1]["t"],
                "candles": [
                    {"t": rows[i]["t"], "c": round(rows[i]["c"], 2),
                     "m": r3(line[i]), "s": r3(sig[i]), "h": r3(hist[i])}
                    for i in range(len(rows) - n, len(rows))
                ],
            }
        except Exception as exc:  # noqa: BLE001 - try the next symbol
            last_err = exc
            print(f"  {sym} {interval} unusable: {exc}")
    raise SystemExit(f"all sources failed for {interval}: {last_err}")


def main():
    print("building weekly...")
    weekly = series("1wk", "10y", 160)
    print("building daily...")
    daily = series("1d", "3y", 260)

    spot = weekly["last"]
    try:
        spot = round(fetch(SYMBOLS[0], "1d", "5d")["meta"]["regularMarketPrice"], 2)
    except Exception as exc:  # noqa: BLE001 - spot is cosmetic
        print(f"  spot quote unavailable: {exc}")

    payload = {
        "generated": int(time.time()),
        "params": {"fast": FAST, "slow": SLOW, "signal": SIGNAL},
        "spot": spot,
        "primary": "weekly",
        "weekly": weekly,
        "daily": daily,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"weekly {weekly['verdict']} @ {weekly['last']} | "
          f"daily {daily['verdict']} @ {daily['last']} -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
