#!/usr/bin/env python3
"""
Independent confirmation tests for the one edge that keeps passing: gold trend.

Tests
  1. s200 logic (frozen FX parameters, unchanged) on XAUUSD 4H  -> already +0.542R
  2. s200 logic on XAUUSD 1H (different data, 31k bars) -> independent check
  3. s200 parameter surface on gold: is it a plateau or a spike?
  4. s98 liq_reclaim vs donchian legs, and correlation with s200
  5. Combined 2-engine gold portfolio: equity, drawdown, correlation
  6. Cost stress and a bull-market control (does the SHORT side make money?)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import load_csv, round_turn_cost_price
from strategy_200_fx_trend_breakout_atr import generate_trades as s200_trades, load_4h
import strategy_98_xau_trend_liquidity_trail as s98

rng = np.random.default_rng(77)
XAU4 = load_4h(os.path.join(THIS, "data", "XAUUSD", "4h", "XAUUSD_4h.csv"))
XAU1_C = load_csv(os.path.join(THIS, "data", "XAUUSD", "1H",
                               "XAUUSD_1h_2021-03-02_2026-07-01.csv"))
XAU1 = pd.DataFrame({"open": [c.open for c in XAU1_C], "high": [c.high for c in XAU1_C],
                     "low": [c.low for c in XAU1_C], "close": [c.close for c in XAU1_C]},
                    index=pd.to_datetime([c.timestamp for c in XAU1_C], unit="s", utc=True))


def st(v):
    v = np.asarray(v, float)
    if len(v) < 5:
        return dict(n=len(v), exp=np.nan, t=0.0, win=np.nan, total=np.nan,
                    dd=np.nan, rr=np.nan, p=np.nan)
    sd = v.std(ddof=1)
    t = v.mean() / (sd / np.sqrt(len(v))) if sd > 0 else 0.0
    w, l = v[v > 0], v[v <= 0]
    eq = np.cumsum(v)
    dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
    p = float((rng.choice(v, size=(20000, len(v)), replace=True).mean(axis=1) <= 0).mean())
    return dict(n=len(v), exp=v.mean(), t=t, win=(v > 0).mean(), total=v.sum(), dd=dd,
                rr=(w.mean() / abs(l.mean())) if len(w) and len(l) else np.nan, p=p)


def line(lbl, v):
    s = st(v)
    print(f"  {lbl:44s} n={s['n']:4d} win={s['win']*100:5.1f}% RR={s['rr']:5.2f} "
          f"exp={s['exp']:+.3f}R total={s['total']:+7.1f}R t={s['t']:+5.2f} "
          f"maxDD={s['dd']:5.1f}R P(<=0)={s['p']*100:5.2f}%")
    return s


print("=" * 122)
print("1-2) THE FROZEN FX PARAMETERS, APPLIED UNCHANGED TO GOLD ON TWO DIFFERENT BAR SIZES")
print("=" * 122)
t4 = s200_trades(XAU4, "XAUUSD")
line("s200 on XAUUSD 4H (30-bar, EMA300, 3/6 ATR)", [t["pnl_R"] for t in t4])
# 1H: scale the lookbacks x4 so the horizon in wall-clock time is the same
t1 = s200_trades(XAU1, "XAUUSD", entry_n=120, ema_n=1200, atr_n=336)
line("s200 on XAUUSD 1H (same horizon, x4 bars)", [t["pnl_R"] for t in t1])
t1b = s200_trades(XAU1, "XAUUSD")          # literally identical params, faster horizon
line("s200 on XAUUSD 1H (identical params)", [t["pnl_R"] for t in t1b])

print("\n" + "=" * 122)
print("3) PARAMETER SURFACE ON GOLD 4H — plateau or spike?")
print("=" * 122)
print(f"  {'entry_n':>8} " + "".join(f"{f'kt={k}':>13}" for k in (4.0, 6.0, 8.0, 10.0)))
for en in (20, 30, 45, 60, 120):
    row = []
    for kt in (4.0, 6.0, 8.0, 10.0):
        v = [t["pnl_R"] for t in s200_trades(XAU4, "XAUUSD", entry_n=en, k_trail=kt)]
        s = st(v)
        row.append(f"{s['exp']:+.2f}(t{s['t']:+.1f})")
    print(f"  {en:8d} " + "".join(f"{x:>13}" for x in row))
print(f"\n  {'k_init':>8} " + "".join(f"{f'ema={e}':>13}" for e in (2, 150, 300, 600)))
for ki in (1.5, 2.0, 3.0, 4.0):
    row = []
    for em in (2, 150, 300, 600):
        v = [t["pnl_R"] for t in s200_trades(XAU4, "XAUUSD", k_init=ki, ema_n=em)]
        s = st(v)
        row.append(f"{s['exp']:+.2f}(t{s['t']:+.1f})")
    print(f"  {ki:8.1f} " + "".join(f"{x:>13}" for x in row))

print("\n" + "=" * 122)
print("4) DIRECTION AND REGIME CONTROLS — is this just the 2021-2026 gold bull market?")
print("=" * 122)
print(f"  gold went {XAU4['close'].iloc[0]:.0f} -> {XAU4['close'].iloc[-1]:.0f} "
      f"({(XAU4['close'].iloc[-1]/XAU4['close'].iloc[0]-1)*100:+.0f}%) over the sample")
for d in ("long", "short"):
    line(f"s200 gold 4H, {d} only", [t["pnl_R"] for t in t4 if t["direction"] == d])
s98_tr = s98.generate_trades(XAU1_C)
s98R = []
for t in s98_tr:
    e, x, sl = float(t["entry_price"]), float(t["exit_price"]), float(t["stop_loss"])
    r = abs(e - sl)
    if r <= 0:
        continue
    g = (x - e) / r if t["direction"] == "long" else (e - x) / r
    s98R.append(dict(ts=pd.Timestamp(t["entry_time"]), setup=t["setup"], dirn=t["direction"],
                     netR=g - round_turn_cost_price(e, symbol="XAUUSD") / r))
line("s98 all setups (1H)", [r["netR"] for r in s98R])
line("s98 liq_reclaim only", [r["netR"] for r in s98R if r["setup"] == "liq_reclaim"])
line("s98 donchian_breakout only", [r["netR"] for r in s98R if r["setup"] == "donchian_breakout"])
for d in ("long", "short"):
    line(f"s98 liq_reclaim, {d} only",
         [r["netR"] for r in s98R if r["setup"] == "liq_reclaim" and r["dirn"] == d])

print("\n" + "=" * 122)
print("5) TWO-ENGINE GOLD PORTFOLIO — are the engines actually diversifying?")
print("=" * 122)
mA = pd.Series([t["pnl_R"] for t in t4],
               index=pd.DatetimeIndex([pd.Timestamp(t["entry_time"]) for t in t4])
               ).resample("MS").sum()
mB = pd.Series([r["netR"] for r in s98R if r["setup"] == "liq_reclaim"],
               index=pd.DatetimeIndex([r["ts"] for r in s98R if r["setup"] == "liq_reclaim"])
               ).resample("MS").sum()
j = pd.concat([mA.rename("s200"), mB.rename("s98_liq")], axis=1).fillna(0.0)
print(f"  monthly R correlation between the two engines: {j['s200'].corr(j['s98_liq']):+.2f}")
print(f"  months profitable: s200 {(j['s200']>0).mean()*100:.0f}%  s98_liq {(j['s98_liq']>0).mean()*100:.0f}%  "
      f"combined {((j['s200']+j['s98_liq'])>0).mean()*100:.0f}%")
comb = (j["s200"] * 0.5 + j["s98_liq"] * 0.5)
eq = comb.cumsum()
dd = (eq.cummax() - eq).max()
print(f"  50/50 blend: {comb.sum():+.1f}R total, monthly mean {comb.mean():+.2f}R, "
       f"monthly sd {comb.std():.2f}R, worst month {comb.min():+.1f}R, maxDD {dd:.1f}R")
print(f"  annualised R-Sharpe of the blend: {comb.mean()/comb.std()*np.sqrt(12):.2f}")

print("\n" + "=" * 122)
print("6) COST STRESS — how wrong can the gold spread assumption be?")
print("=" * 122)
print(f"  {'x cost':>7} {'s200 4H':>18} {'s98 liq_reclaim':>20}")
for m in (1, 2, 3, 5, 10, 20):
    a = np.mean([t["gross_R"] - m * t["cost_R"] for t in t4])
    bb = []
    for t in s98_tr:
        if t["setup"] != "liq_reclaim":
            continue
        e, x, sl = float(t["entry_price"]), float(t["exit_price"]), float(t["stop_loss"])
        r = abs(e - sl)
        if r <= 0:
            continue
        g = (x - e) / r if t["direction"] == "long" else (e - x) / r
        bb.append(g - m * round_turn_cost_price(e, symbol="XAUUSD") / r)
    print(f"  {m:7d} {a:+18.3f}R {np.mean(bb):+20.3f}R")
print("\n  (x20 = a $8.00 round-turn on gold, ~25x a normal Exness spread)")
