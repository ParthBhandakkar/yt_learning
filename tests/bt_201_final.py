#!/usr/bin/env python3
"""
Final validation + capital simulation for the one edge that survived every test:
the liquidity sweep/reclaim engine on gold, with the near-zero Donchian leg
removed and the exit trail advanced ONLY on closed bars.

Important: removing the Donchian leg is not a filter on the output — it changes
which bars are available for entry (the engine holds one position at a time), so
the strategy is re-run with also_breakout=False rather than post-filtered.

Checks
  1. Re-run with/without the Donchian leg, full sample
  2. Time split 2021-03..2024-03 (dev) vs 2024-03..2026-07 (held back)
  3. Direction, year, and hold-time breakdown
  4. Cost stress and slippage stress
  5. Monte Carlo
  6. INR capital simulation for Rs 10,000 at 1:2000, incl. minimum-lot reality
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import load_csv, round_turn_cost_price
import strategy_98_xau_trend_liquidity_trail as s98

rng = np.random.default_rng(101)
USDINR, LEV, START = 88.0, 2000.0, 10_000.0
SPLIT = pd.Timestamp("2024-03-01", tz="UTC")
C1H = load_csv(os.path.join(THIS, "data", "XAUUSD", "1H",
                            "XAUUSD_1h_2021-03-02_2026-07-01.csv"))


def enrich(trades, cost_mult=1.0, slip_price=0.0):
    out = []
    for t in trades:
        e, x, sl = float(t["entry_price"]), float(t["exit_price"]), float(t["stop_loss"])
        r = abs(e - sl)
        if r <= 0:
            continue
        g = (x - e) / r if t["direction"] == "long" else (e - x) / r
        c = (round_turn_cost_price(e, symbol="XAUUSD") * cost_mult + slip_price) / r
        out.append(dict(ts=pd.Timestamp(t["entry_time"]), xts=pd.Timestamp(t["exit_time"]),
                        dirn=t["direction"], setup=t["setup"], risk=r, entry=e,
                        netR=g - c, grossR=g))
    return out


def st(v):
    v = np.asarray([x for x in v], float)
    if len(v) < 5:
        return None
    sd = v.std(ddof=1)
    t = v.mean() / (sd / np.sqrt(len(v))) if sd > 0 else 0.0
    w, l = v[v > 0], v[v <= 0]
    eq = np.cumsum(v)
    dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
    p = float((rng.choice(v, size=(20000, len(v)), replace=True).mean(axis=1) <= 0).mean())
    return dict(n=len(v), exp=v.mean(), t=t, win=(v > 0).mean(), total=v.sum(),
                dd=dd, rr=(w.mean() / abs(l.mean())) if len(w) and len(l) else np.nan, p=p)


def show(lbl, v):
    s = st(v)
    if not s:
        print(f"  {lbl:42s} too few trades")
        return None
    print(f"  {lbl:42s} n={s['n']:4d} win={s['win']*100:5.1f}% RR={s['rr']:5.2f} "
          f"exp={s['exp']:+.3f}R tot={s['total']:+7.1f}R t={s['t']:+5.2f} "
          f"DD={s['dd']:5.1f}R P(<=0)={s['p']*100:5.2f}%")
    return s


print("=" * 118)
print("1) DOES REMOVING THE DONCHIAN LEG HELP? (re-run, not post-filtered)")
print("=" * 118)
TR_ON = enrich(s98.generate_trades(C1H, also_breakout=True))
TR_OFF = enrich(s98.generate_trades(C1H, also_breakout=False))
show("with Donchian leg (original s98)", [t["netR"] for t in TR_ON])
show("liquidity reclaim ONLY  <- strategy 201", [t["netR"] for t in TR_OFF])

TR = TR_OFF
print("\n" + "=" * 118)
print("2) TIME SPLIT — first 2.9 years vs the last 2.3 years")
print("=" * 118)
show("2021-03 .. 2024-03", [t["netR"] for t in TR if t["ts"] <= SPLIT])
show("2024-03 .. 2026-07 (held back)", [t["netR"] for t in TR if t["ts"] > SPLIT])

print("\n" + "=" * 118)
print("3) DIRECTION / YEAR / HOLD BREAKDOWN")
print("=" * 118)
for d in ("long", "short"):
    show(f"{d} only", [t["netR"] for t in TR if t["dirn"] == d])
byy = defaultdict(list)
for t in TR:
    byy[t["ts"].year].append(t["netR"])
print(f"  {'year':6s} {'n':>4s} {'win%':>6s} {'exp':>8s} {'total':>8s}")
for y in sorted(byy):
    v = np.array(byy[y])
    print(f"  {y:<6d} {len(v):4d} {(v>0).mean()*100:5.1f}% {v.mean():+8.3f} {v.sum():+8.1f}R")
hold = np.array([(t["xts"] - t["ts"]).total_seconds() / 3600 for t in TR])
risk = np.array([t["risk"] for t in TR])
print(f"  hold hours: median {np.median(hold):.0f}  p90 {np.percentile(hold,90):.0f}  max {hold.max():.0f}")
print(f"  stop distance $: median {np.median(risk):.2f}  p10 {np.percentile(risk,10):.2f}  "
      f"p90 {np.percentile(risk,90):.2f}")
print(f"  trades per month: {len(TR)/((TR[-1]['ts']-TR[0]['ts']).days/30.4):.1f}")

print("\n" + "=" * 118)
print("4) COST AND SLIPPAGE STRESS")
print("=" * 118)
base = s98.generate_trades(C1H, also_breakout=False)
print(f"  {'assumption':44s} {'net exp':>10s} {'total':>9s} {'t':>6s}")
for m in (1, 2, 3, 5, 8):
    v = [t["netR"] for t in enrich(base, cost_mult=m)]
    s = st(v)
    print(f"  round-turn cost x{m} (${0.40*m:.2f} on gold){'':<12} {s['exp']:+10.3f}R "
          f"{s['total']:+9.1f}R {s['t']:+6.2f}")
for sp in (0.5, 1.0, 2.0):
    v = [t["netR"] for t in enrich(base, slip_price=sp)]
    s = st(v)
    print(f"  base cost + ${sp:.2f} slippage every trade{'':<10} {s['exp']:+10.3f}R "
          f"{s['total']:+9.1f}R {s['t']:+6.2f}")

print("\n" + "=" * 118)
print("5) MONTE CARLO (20,000 reshuffles, 2% risk per trade, compounding)")
print("=" * 118)
v = np.array([t["netR"] for t in TR])
finals, dds = [], []
for _ in range(20000):
    eq, peak, dd = START, START, 0.0
    for x in rng.permutation(v):
        eq *= (1 + 0.02 * x)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    finals.append(eq)
    dds.append(dd)
finals, dds = np.array(finals), np.array(dds)
print(f"  final equity: p5 Rs {np.percentile(finals,5):,.0f} | median Rs {np.percentile(finals,50):,.0f} "
      f"| p95 Rs {np.percentile(finals,95):,.0f}")
print(f"  max drawdown: median {np.median(dds)*100:.0f}% | p95 {np.percentile(dds,95)*100:.0f}%")
print(f"  P(final < start) = {(finals<START).mean()*100:.1f}%   P(DD>50%) = {(dds>0.5).mean()*100:.1f}%")

print("\n" + "=" * 118)
print("6) CAPITAL SIMULATION — Rs 10,000, 1:2000, gold, chronological & compounding")
print("=" * 118)
span = (TR[0]["ts"], TR[-1]["xts"])
yrs = (span[1] - span[0]).days / 365.25


def sim(start, risk_pct, min_lot, contract, label):
    eq, peak, mdd, taken, skipped = start, start, 0.0, 0, 0
    max_margin = 0.0
    for t in sorted(TR, key=lambda z: z["ts"]):
        if eq <= 0:
            break
        risk_inr = eq * risk_pct / 100.0
        risk_per_lot = t["risk"] * contract * USDINR
        lots = np.floor((risk_inr / risk_per_lot) / min_lot) * min_lot
        if lots < min_lot:
            skipped += 1
            continue
        margin = t["entry"] * contract * lots / LEV * USDINR
        if margin > eq:
            skipped += 1
            continue
        max_margin = max(max_margin, margin)
        taken += 1
        eq += t["netR"] * t["risk"] * contract * lots * USDINR
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    cagr = ((eq / start) ** (1 / yrs) - 1) * 100 if eq > 0 else -100
    print(f"  {label:34s} taken {taken:4d}/{len(TR)} skip {skipped:4d} | "
          f"final Rs {eq:>12,.0f} | CAGR {cagr:6.1f}% | DD {mdd*100:5.1f}% | "
          f"peak margin Rs {max_margin:,.0f}")
    return eq, mdd, taken


print(f"  period {span[0].date()} -> {span[1].date()} ({yrs:.1f}y), {len(TR)} signals\n")
print("  STANDARD account (1 lot = 100 oz, min 0.01 lot = 1 oz):")
for rp in (2.0, 5.0):
    for cap in (10_000.0, 50_000.0, 200_000.0, 500_000.0):
        sim(cap, rp, 0.01, 100.0, f"Rs {cap:,.0f} start, {rp:.0f}% risk")
print("\n  CENT account (1 lot = 1 oz, min 0.01 lot = 0.01 oz):")
for rp in (1.0, 2.0, 3.0):
    sim(START, rp, 0.01, 1.0, f"Rs 10,000 start, {rp:.0f}% risk")

print("\n  headline, cent account, 2% risk:")
eq, mdd, taken = sim(START, 2.0, 0.01, 1.0, "Rs 10,000 -> ?")
print(f"\n  net profit Rs {eq-START:,.0f}  ({(eq/START-1)*100:+.0f}% over {yrs:.1f}y, "
      f"CAGR {((eq/START)**(1/yrs)-1)*100:.1f}%)")
