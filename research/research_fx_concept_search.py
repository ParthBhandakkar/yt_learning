#!/usr/bin/env python3
"""Concept search for a robust FX-basket edge.

Protocol for every concept, identical and fixed in advance:
  DEV  = EURUSD 1999-2014      (the only long history available)
  OOS-A= EURUSD 2015-2026      (unseen time)
  OOS-B= the other 6 FX pairs  (unseen instruments)
A concept is only interesting if OOS-A and OOS-B are BOTH positive.
No parameter is allowed to be chosen by looking at OOS.
"""
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import round_turn_cost_price
from strategy_200_fx_trend_breakout_atr import load_4h, _atr, _ema

FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
DF4 = {s: load_4h(os.path.join(THIS, "data", s, "4h", f"{s}_4h.csv")) for s in FX}
DFD = {s: DF4[s].resample("1D").agg({"open": "first", "high": "max",
                                     "low": "min", "close": "last"}).dropna()
       for s in FX}


def tstat(v):
    v = np.asarray(v, float)
    if len(v) < 3 or v.std(ddof=1) == 0:
        return 0.0
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))


def rep(name, per_sym):
    """per_sym: {symbol: [ (timestamp, netR), ... ]}"""
    dev = [r for (ts, r) in per_sym["EURUSD"] if ts < pd.Timestamp("2015-01-01", tz="UTC")]
    oa = [r for (ts, r) in per_sym["EURUSD"] if ts >= pd.Timestamp("2015-01-01", tz="UTC")]
    ob = [r for s in FX if s != "EURUSD" for (ts, r) in per_sym[s]]
    allv = [r for s in FX for (ts, r) in per_sym[s]]
    def f(v):
        return f"n={len(v):4d} exp={np.mean(v):+.3f} t={tstat(v):+5.2f}" if len(v) > 2 else "n/a"
    verdict = "PASS" if (len(oa) > 20 and len(ob) > 50 and np.mean(oa) > 0 and np.mean(ob) > 0) else "fail"
    print(f"  {name:44s} DEV {f(dev)} | OOS-A {f(oa)} | OOS-B {f(ob)} | ALL {f(allv)}  [{verdict}]")
    return verdict == "PASS"


# ---------------------------------------------------------------- concept engine
def breakout_trend(df, sym, entry_n, ema_n, atr_n, k_init, k_trail,
                   vol_filter=False, vol_fast=84, vol_slow=360, cost_mult=1.0):
    o, h, l, c = (df[x].values.astype(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    a = _atr(h, l, c, atr_n)
    e = _ema(c, ema_n) if ema_n else None
    af = _atr(h, l, c, vol_fast)
    asl = pd.Series(_atr(h, l, c, 1)).rolling(vol_slow).mean().values
    hh = pd.Series(h).rolling(entry_n).max().shift(1).values
    ll = pd.Series(l).rolling(entry_n).min().shift(1).values
    warm = max(entry_n, ema_n or 0, atr_n, vol_slow if vol_filter else 0) + 5
    out = []
    i = warm
    while i < n - 1:
        if np.isnan(hh[i]) or a[i] <= 0:
            i += 1
            continue
        if vol_filter and (np.isnan(asl[i]) or af[i] <= asl[i]):
            i += 1
            continue
        up = c[i] > hh[i] and (e is None or c[i] > e[i])
        dn = c[i] < ll[i] and (e is None or c[i] < e[i])
        if not (up or dn):
            i += 1
            continue
        p = 1 if up else -1
        ei = i + 1
        entry = o[ei]
        risk = k_init * a[i]
        trail = entry - risk if p == 1 else entry + risk
        ex = entry
        j, xi, xp = ei, None, None
        while j < n:
            if p == 1:
                if l[j] <= trail:
                    xi, xp = j, trail
                    break
                ex = max(ex, h[j])
                trail = max(trail, ex - k_trail * a[j])
            else:
                if h[j] >= trail:
                    xi, xp = j, trail
                    break
                ex = min(ex, l[j])
                trail = min(trail, ex + k_trail * a[j])
            j += 1
        if xi is None:
            xi, xp = n - 1, c[n - 1]
        g = (xp - entry) / risk if p == 1 else (entry - xp) / risk
        out.append((df.index[ei], g - cost_mult * round_turn_cost_price(entry, symbol=sym) / risk))
        i = xi + 1
    return out


print("=" * 118)
print("CONCEPT 1 — same trend-continuation logic on DAILY bars (higher TF = classic time-series momentum domain)")
print("=" * 118)
for (en, em, ki, kt) in [(20, 50, 3.0, 6.0), (20, 50, 2.0, 4.0), (55, 100, 2.0, 4.0),
                         (55, 200, 3.0, 6.0), (10, 50, 3.0, 6.0), (40, 200, 2.0, 8.0)]:
    per = {s: breakout_trend(DFD[s], s, en, em, 14, ki, kt) for s in FX}
    rep(f"daily N={en} ema={em} k_init={ki} k_trail={kt}", per)

print()
print("=" * 118)
print("CONCEPT 2 — 4H breakout with a volatility-EXPANSION filter (trends need vol; ranges kill breakouts)")
print("=" * 118)
for (en, ki, kt) in [(30, 3.0, 6.0), (30, 2.0, 8.0), (60, 3.0, 6.0)]:
    per = {s: breakout_trend(DF4[s], s, en, 300, 84, ki, kt, vol_filter=True) for s in FX}
    rep(f"4H N={en} ki={ki} kt={kt} + ATR14d>ATR60d", per)

print()
print("=" * 118)
print("CONCEPT 3 — CROSS-SECTIONAL currency momentum (rank the 7 pairs, long strongest / short weakest)")
print("=" * 118)
# Build a common daily close panel of "foreign currency priced in USD"
panel = {}
for s in FX:
    px = DFD[s]["close"]
    panel[s[:3] if s.endswith("USD") else s[3:]] = px if s.endswith("USD") else 1.0 / px
P = pd.DataFrame(panel).dropna()
print(f"  panel: {list(P.columns)}  {P.index[0].date()} -> {P.index[-1].date()}  rows={len(P)}")
RET = P.pct_change()


def xsec_momentum(lookback, hold, n_side, start=None, end=None, cost_bps=1.2):
    """Long the n_side strongest currencies vs USD, short the n_side weakest.
    Signal from returns up to and including day t; positions held t+1..t+hold."""
    px = P if start is None else P[(P.index >= start) & (P.index < end)]
    ret = px.pct_change()
    dates = px.index
    pnl = pd.Series(0.0, index=dates)
    for k in range(lookback, len(dates) - hold, hold):
        mom = px.iloc[k] / px.iloc[k - lookback] - 1.0        # known at close of day k
        rank = mom.sort_values()
        shorts, longs = rank.index[:n_side], rank.index[-n_side:]
        seg = ret.iloc[k + 1:k + 1 + hold]                     # traded from k+1
        gross = seg[longs].mean(axis=1) - seg[shorts].mean(axis=1)
        pnl.iloc[k + 1:k + 1 + hold] = gross.values
        pnl.iloc[k + 1] -= cost_bps / 10000.0 * 2              # both legs, round turn
    return pnl[pnl != 0]


print(f"  {'lookback':>9} {'hold':>5} {'side':>5} {'n_days':>7} {'ann_ret':>8} {'ann_vol':>8} {'Sharpe':>7} "
      f"{'t':>6}   {'2015+ Sharpe':>12}")
for lb in (20, 60, 120, 250):
    for hold in (5, 20, 60):
        for side in (2, 3):
            p = xsec_momentum(lb, hold, side)
            if len(p) < 200:
                continue
            ann = p.mean() * 252
            vol = p.std() * np.sqrt(252)
            sh = ann / vol if vol > 0 else 0
            p2 = p[p.index >= pd.Timestamp("2015-01-01", tz="UTC")]
            sh2 = (p2.mean() * 252) / (p2.std() * np.sqrt(252)) if p2.std() > 0 else 0
            print(f"  {lb:9d} {hold:5d} {side:5d} {len(p):7d} {ann*100:7.2f}% {vol*100:7.2f}% "
                  f"{sh:7.2f} {tstat(p.values):+6.2f} {sh2:12.2f}")

print()
print("=" * 118)
print("CONCEPT 4 — time-series momentum on the currency panel (no ranking; own trend per currency)")
print("=" * 118)


def tsmom(lookback, hold, cost_bps=1.2, start=None):
    px = P if start is None else P[P.index >= start]
    ret = px.pct_change()
    vol = ret.rolling(60).std()
    dates = px.index
    pnl = pd.Series(0.0, index=dates)
    for k in range(max(lookback, 60), len(dates) - hold, hold):
        sig = np.sign(px.iloc[k] / px.iloc[k - lookback] - 1.0)
        w = sig / vol.iloc[k]                          # inverse-vol scaled
        w = w / np.abs(w).sum()
        seg = ret.iloc[k + 1:k + 1 + hold]
        pnl.iloc[k + 1:k + 1 + hold] = (seg * w).sum(axis=1).values
        pnl.iloc[k + 1] -= cost_bps / 10000.0
    return pnl[pnl != 0]


print(f"  {'lookback':>9} {'hold':>5} {'ann_ret':>8} {'ann_vol':>8} {'Sharpe':>7} {'t':>6}  "
      f"{'pre2015 Sh':>11} {'2015+ Sh':>9}")
for lb in (20, 60, 120, 250):
    for hold in (5, 20, 60):
        p = tsmom(lb, hold)
        if len(p) < 200:
            continue
        ann, vol = p.mean() * 252, p.std() * np.sqrt(252)
        a = p[p.index < pd.Timestamp("2015-01-01", tz="UTC")]
        b = p[p.index >= pd.Timestamp("2015-01-01", tz="UTC")]
        f = lambda x: (x.mean() * 252) / (x.std() * np.sqrt(252)) if len(x) > 20 and x.std() > 0 else 0
        print(f"  {lb:9d} {hold:5d} {ann*100:7.2f}% {vol*100:7.2f}% {ann/vol:7.2f} "
              f"{tstat(p.values):+6.2f} {f(a):11.2f} {f(b):9.2f}")
