#!/usr/bin/env python3
"""Accuracy audit. Run after build_data.py; exits non-zero on any failure.

Checks three classes of thing:
  1. the indicator maths against independent reference implementations
  2. causality -- that nothing computed at bar i moves when later bars are added
  3. the published data.json and predictions.json for the specific mistakes this
     project has actually made before

    python scripts/build_data.py && python scripts/audit.py
"""
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as B  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "..", "docs")
FAILS = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def near(a, b, eps=1e-9):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < eps)


# ---------------------------------------------------------------- 1. maths

def audit_indicators():
    print("=== indicator maths vs independent reference ===")
    v = [10., 11, 12, 11, 10, 9, 10, 12, 14, 13, 12, 11, 13, 15, 16, 15, 14, 16, 18, 17]

    def ema_ref(vals, n):
        k = 2 / (n + 1)
        out = [None] * (n - 1)
        prev = sum(vals[:n]) / n
        out.append(prev)
        for x in vals[n:]:
            prev = x * k + prev * (1 - k)
            out.append(prev)
        return out

    def sma_ref(vals, n):
        return [None] * (n - 1) + [sum(vals[i - n + 1:i + 1]) / n
                                   for i in range(n - 1, len(vals))]

    def rsi_ref(vals, n=14):
        out = [None] * len(vals)
        g = sum(max(vals[i] - vals[i - 1], 0) for i in range(1, n + 1)) / n
        loss = sum(max(vals[i - 1] - vals[i], 0) for i in range(1, n + 1)) / n
        out[n] = 100.0 if loss == 0 else 100 - 100 / (1 + g / loss)
        for i in range(n + 1, len(vals)):
            d = vals[i] - vals[i - 1]
            g = (g * (n - 1) + max(d, 0)) / n
            loss = (loss * (n - 1) + max(-d, 0)) / n
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + g / loss)
        return out

    check("EMA matches reference", all(near(a, b) for a, b in zip(B.ema(v, 5), ema_ref(v, 5))))
    check("SMA matches reference", all(near(a, b) for a, b in zip(B.sma(v, 5), sma_ref(v, 5))))
    check("RSI (Wilder) matches reference",
          all(near(a, b) for a, b in zip(B.rsi(v, 14), rsi_ref(v, 14))))
    up = [100 + i for i in range(40)]
    check("RSI = 100 on a pure uptrend", near(B.rsi(up, 14)[-1], 100))
    xs = [float(i) for i in range(1, 11)]
    check("percentiles interpolate correctly",
          near(B._pctile(xs, .5), 5.5) and near(B._pctile(xs, .1), 1.9)
          and near(B._pctile(xs, .9), 9.1))


# ---------------------------------------------------------------- 2. causality

def audit_causality(closes):
    print("\n=== causality: nothing at bar i may move when later bars arrive ===")
    n = len(closes)
    line, sig, hist = B.macd(closes)
    ma, rs = B.sma(closes, B.MA_LEN), B.rsi(closes)
    cut = n - 30
    l2, s2, h2 = B.macd(closes[:cut])
    ma2, rs2 = B.sma(closes[:cut], B.MA_LEN), B.rsi(closes[:cut])

    check("MACD unchanged when future bars removed",
          all(near(line[i], l2[i]) for i in range(cut)))
    check("MA unchanged when future bars removed", all(near(ma[i], ma2[i]) for i in range(cut)))
    check("RSI unchanged when future bars removed", all(near(rs[i], rs2[i]) for i in range(cut)))

    st = B._series_states(closes, line, sig, hist, ma, rs)
    st2 = B._series_states(closes[:cut], l2, s2, h2, ma2, rs2)
    check("verdict at bar i unchanged when future removed",
          all(st[i] == st2[i] for i in range(cut)))

    peaks = B._running_max(closes)
    buckets = [B._bucket_of((peaks[k] - closes[k]) / peaks[k] * 100) for k in range(n)]
    i = n - 200
    a, _, _ = B._bands_at(closes, st, buckets, i, [13])
    b, _, _ = B._bands_at(closes[:i + 1], st[:i + 1], buckets[:i + 1], i, [13])
    check("backfilled bands at bar i ignore every later bar", a == b, f"{a} vs {b}")


# ---------------------------------------------------------------- 3. published output

def audit_output():
    print("\n=== published data ===")
    with open(os.path.join(DOCS, "data.json"), encoding="utf-8") as f:
        d = json.load(f)
    with open(os.path.join(DOCS, "predictions.json"), encoding="utf-8") as f:
        led = json.load(f)

    for tf, period in (("weekly", 7 * 86400), ("daily", 86400)):
        s = d[tf]
        age = time.time() - s["asOf"]
        # The call must be based on a bar whose period has ended. A weekly read
        # taken one day into the week disagrees with its own final value 31% of
        # the time, so a forming bar must never drive the headline.
        check(f"{tf} call is based on a completed bar", age >= period,
              f"asOf {datetime.date.fromtimestamp(s['asOf'])}, {age / 86400:.1f}d old")
        if s.get("forming"):
            check(f"{tf} forming bar is reported separately",
                  s.get("provisional") is not None and s["provisional"]["barTs"] > s["asOf"])

        t = s["test"]
        for name in ("position", "signal"):
            r = t[name]
            check(f"{tf} {name} backtest reports a lagged fill",
                  r.get("cagrNoLag") is not None,
                  f"lagged {r['cagr']}% vs same-bar {r.get('cagrNoLag')}%")

        check(f"{tf} projection anchors to the close it was measured from",
              near(d["anchor"][tf], s["last"], 0.01))

    b = d.get("bullion")
    if b:
        # Spot and the front future track each other closely. A feed that has
        # broken, stalled, or switched units shows up as an implausible gap long
        # before anyone notices the number on the page is wrong.
        gap = abs(b["price"] - d["spot"]) / d["spot"] * 100
        check("spot bullion within 5% of the front future", gap < 5,
              f"bullion {b['price']} vs future {d['spot']} = {gap:.2f}% via {b['source']}")
        check("bullion price is plausible", 100 < b["price"] < 100000)
    else:
        print("  WARN  no bullion quote in this build")

    print("\n=== ledger ===")
    live = [e for e in led["entries"] if not e.get("backfilled")]
    # The invariant that matters is that an entry's price IS the close of the bar
    # it names. `made` is legitimately later: a weekly call issued on a Monday
    # references the previous completed week, which is over a week old.
    closes_at = {}
    for tf, iv, rng in (("weekly", "1wk", "30y"), ("daily", "1d", "10y")):
        closes_at[tf] = {r["t"]: r["c"] for r in B.candles(B.fetch("GC=F", iv, rng))}
    mism = []
    for e in live:
        actual = closes_at[e["timeframe"]].get(e["barTs"])
        if actual is not None and abs(actual - e["spot"]) > 0.01:
            mism.append((e["id"], e["spot"], round(actual, 2)))
    check("every live entry's price is the close of the bar it names", not mism,
          f"checked {len(live)}" if not mism else str(mism[:3]))

    # Only the newest entry per timeframe should still match the current anchor;
    # older ones legitimately reference earlier closes.
    for tf in ("weekly", "daily"):
        same = [e for e in live if e["timeframe"] == tf]
        if not same:
            continue
        newest = max(same, key=lambda e: e["barTs"])
        check(f"newest live {tf} entry logged at its bar's close",
              abs(newest["spot"] - d["anchor"][tf]) < 0.01,
              f"entry {newest['spot']} vs anchor {d['anchor'][tf]}")

    tol = {"weekly": 14 * 86400, "daily": 5 * 86400}
    bad = [(e["id"], h) for e in led["entries"] for h, r in e["results"].items()
           if not (0 <= r["at"] - e["dueAt"][h] <= tol[e["timeframe"]])]
    total = sum(len(e["results"]) for e in led["entries"])
    check("every resolved horizon scored at/after due, within tolerance", not bad,
          f"checked {total}" if not bad else f"{len(bad)} bad: {bad[:3]}")

    ids = [e["id"] for e in led["entries"]]
    check("no duplicate ledger ids", len(ids) == len(set(ids)))
    check("no non-positive prices", not [e for e in led["entries"] if e["spot"] <= 0])
    check("no result rewritten with a future price",
          not [1 for e in led["entries"] for h, r in e["results"].items()
               if r["at"] > time.time() + 86400])


def main():
    audit_indicators()
    rows = B.candles(B.fetch("GC=F", "1wk", "30y"))
    audit_causality([r["c"] for r in rows])
    audit_output()
    print(f"\n{len(FAILS)} failure(s)" + (": " + ", ".join(FAILS) if FAILS else ""))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
