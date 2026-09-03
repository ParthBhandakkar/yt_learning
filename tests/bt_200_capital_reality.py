#!/usr/bin/env python3
"""Final section: what survives, and what Rs 10,000 can actually trade."""
import os
import sys
import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from strategy_200_fx_trend_breakout_atr import generate_trades, load_4h

USDINR, LEV, START = 88.0, 2000.0, 10_000.0
FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
USD_BASE = {"USDJPY", "USDCHF", "USDCAD"}
rng = np.random.default_rng(3)


def vpu(sym, lots, price, contract):
    if sym == "XAUUSD":
        return contract * lots
    if sym in USD_BASE:
        return contract * lots / price
    return contract * lots


def notl(sym, lots, price, contract):
    if sym == "XAUUSD":
        return contract * lots * price
    if sym in USD_BASE:
        return contract * lots
    return contract * lots * price


TR = {}
for s in FX + ["XAUUSD"]:
    TR[s] = generate_trades(load_4h(os.path.join(THIS, "data", s, "4h", f"{s}_4h.csv")), s)

print("=" * 104)
print("1) MINIMUM VIABLE CAPITAL — what the smallest tradeable position already risks")
print("=" * 104)
print(f"   {'symbol':8s} {'med stop':>10s} {'std 0.01lot':>12s} {'cent 0.01lot':>13s} "
      f"{'cap for 2% (std)':>17s} {'cap for 2% (cent)':>18s}")
for s in FX + ["XAUUSD"]:
    med = np.median([t["risk_price"] for t in TR[s]])
    px = np.median([t["entry_price"] for t in TR[s]])
    base = 100.0 if s == "XAUUSD" else 100_000.0
    std_risk = med * vpu(s, 0.01, px, base) * USDINR
    cent_risk = med * vpu(s, 0.01, px, base * 0.01) * USDINR
    print(f"   {s:8s} {med:10.5f} {std_risk:11,.0f}R {cent_risk:12,.0f}R "
          f"{std_risk/0.02:16,.0f}R {cent_risk/0.02:17,.0f}R")

print("\n" + "=" * 104)
print("2) THE LITERAL BRIEF, step by step: Rs 1,000 margin per trade at 1:2000")
print("=" * 104)
px = np.median([t["entry_price"] for t in TR["EURUSD"]])
med = np.median([t["risk_price"] for t in TR["EURUSD"]])
notional_inr = 1000 * LEV
lots = (notional_inr / USDINR) / (100_000 * px)
print(f"   margin Rs 1,000 x 2000            = Rs {notional_inr:,.0f} notional (${notional_inr/USDINR:,.0f})")
print(f"   on EURUSD @ {px:.4f}              = {lots:.3f} standard lots")
print(f"   value of a 1-pip move             = Rs {0.0001*100_000*lots*USDINR:,.0f}")
print(f"   strategy-200 median stop          = {med/0.0001:.0f} pips")
print(f"   => loss if that ONE stop is hit   = Rs {med*100_000*lots*USDINR:,.0f} "
      f"({med*100_000*lots*USDINR/START*100:.0f}% of Rs 10,000 capital)")
print(f"   => a single losing trade wipes the account, regardless of strategy quality.")

print("\n" + "=" * 104)
print("3) THE ONE THING THAT SURVIVED: same frozen strategy applied to XAUUSD")
print("=" * 104)
v = np.array([t["pnl_R"] for t in TR["XAUUSD"]])
w, l = v[v > 0], v[v <= 0]
eq = np.cumsum(v)
dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
boot = (rng.choice(v, size=(20000, len(v)), replace=True).mean(axis=1) <= 0).mean()
print(f"   n={len(v)}  win {100*(v>0).mean():.1f}%  RR {w.mean()/abs(l.mean()):.2f}  "
      f"net {v.mean():+.3f}R/trade  total {v.sum():+.1f}R  t={v.mean()/(v.std(ddof=1)/np.sqrt(len(v))):+.2f}  "
      f"maxDD {dd:.1f}R  P(exp<=0)={boot*100:.1f}%")
byy = {}
for t in TR["XAUUSD"]:
    byy.setdefault(t["entry_time"][:4], []).append(t["pnl_R"])
print("   by year: " + "  ".join(f"{y}:{np.sum(byy[y]):+.0f}R(n{len(byy[y])})" for y in sorted(byy)))
for m in (1, 2, 3, 5, 10):
    vv = np.array([t["gross_R"] - m * t["cost_R"] for t in TR["XAUUSD"]])
    print(f"   cost x{m:<3d} net {vv.mean():+.3f}R  total {vv.sum():+.1f}R")

print("\n   capital simulation, XAUUSD only, compounding, standard account (min 0.01 lot = 1 oz):")
print(f"   {'start Rs':>10} {'risk%':>6} {'taken':>6} {'final Rs':>13} {'CAGR':>7} {'maxDD':>7}")
span = (pd.Timestamp(TR["XAUUSD"][0]["entry_time"]), pd.Timestamp(TR["XAUUSD"][-1]["exit_time"]))
yrs = (span[1] - span[0]).days / 365.25
for start in (10_000.0, 50_000.0, 200_000.0, 1_000_000.0):
    for rp in (2.0,):
        eqv, peak, mdd, taken = start, start, 0.0, 0
        for t in sorted(TR["XAUUSD"], key=lambda z: z["entry_time"]):
            if eqv <= 0:
                break
            risk_inr = eqv * rp / 100.0
            lots = np.floor((risk_inr / (t["risk_price"] * 100.0 * USDINR)) / 0.01) * 0.01
            if lots < 0.01:
                continue
            taken += 1
            eqv += t["pnl_R"] * t["risk_price"] * 100.0 * lots * USDINR
            peak = max(peak, eqv)
            mdd = max(mdd, (peak - eqv) / peak)
        cagr = ((eqv / start) ** (1 / yrs) - 1) * 100 if eqv > 0 else -100
        print(f"   {start:10,.0f} {rp:6.1f} {taken:6d} {eqv:13,.0f} {cagr:6.1f}% {mdd*100:6.1f}%")
print(f"   period {span[0].date()} -> {span[1].date()} ({yrs:.1f}y)")
