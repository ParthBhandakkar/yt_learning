#!/usr/bin/env python3
"""DESIGN PHASE ONLY — refine on EURUSD 1999-2014. Confirm the plateau is
interior (not sitting on a grid edge) and stable across dev sub-periods."""
import os
import sys
import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from dev_200_param_sweep import load4h, backtest, summ  # noqa: E402

df = load4h("EURUSD")
dev = df[(df.index >= "1999-01-01") & (df.index < "2015-01-01")]
a = dev[dev.index < "2007-01-01"]
b = dev[dev.index >= "2007-01-01"]

print("A) k_trail plateau (entryN=120, emaN=300, atr=84)")
print(f"{'k_init':>6} {'k_trail':>7} {'n':>4} {'win%':>5} {'RR':>5} {'exp':>7} {'t':>6} {'ddR':>6}")
for k_init in (1.5, 2.0, 2.5, 3.0):
    for k_trail in (3, 4, 5, 6, 8, 10, 12):
        s = summ(backtest(dev, "EURUSD", 120, 300, 84, k_init, float(k_trail)))
        if s:
            print(f"{k_init:6.1f} {k_trail:7d} {s['n']:4d} {s['win']*100:5.1f} {s['rr']:5.2f} "
                  f"{s['exp']:+7.3f} {s['t']:+6.2f} {s['dd']:6.1f}")

print("\nB) entryN plateau (k_init=2.0, k_trail=6, emaN=300)")
for en in (20, 30, 45, 60, 80, 100, 120, 150, 200, 250):
    s = summ(backtest(dev, "EURUSD", en, 300, 84, 2.0, 6.0))
    if s:
        print(f"  entryN={en:3d} n={s['n']:3d} win%={s['win']*100:5.1f} RR={s['rr']:5.2f} "
              f"exp={s['exp']:+.3f} t={s['t']:+.2f} ddR={s['dd']:5.1f}")

print("\nC) atr_len plateau (entryN=120, k_init=2.0, k_trail=6, emaN=300)")
for al in (28, 42, 60, 84, 120, 168):
    s = summ(backtest(dev, "EURUSD", 120, 300, al, 2.0, 6.0))
    if s:
        print(f"  atr={al:3d} n={s['n']:3d} exp={s['exp']:+.3f} t={s['t']:+.2f} RR={s['rr']:.2f}")

print("\nD) dev sub-period stability of candidates")
cands = [(120, 300, 2.0, 6.0), (120, 300, 1.5, 6.0), (30, 300, 3.0, 6.0),
         (30, 150, 2.0, 6.0), (120, 0, 2.0, 6.0), (60, 300, 2.0, 6.0)]
for (en, em, ki, kt) in cands:
    sa, sb, sd = (summ(backtest(x, "EURUSD", en, em, 84, ki, kt)) for x in (a, b, dev))
    print(f"  N={en:3d} ema={em:3d} ki={ki} kt={kt}: "
          f"1999-06 exp={sa['exp']:+.3f}(n{sa['n']:3d}) | 2007-14 exp={sb['exp']:+.3f}(n{sb['n']:3d}) | "
          f"all exp={sd['exp']:+.3f} t={sd['t']:+.2f} RR={sd['rr']:.2f}")

print("\nE) ensemble of fast(30)+slow(120) breakouts, k_init=2.0 k_trail=6, ema=300")
for en in (30, 120):
    tr = backtest(dev, "EURUSD", en, 300, 84, 2.0, 6.0)
    s = summ(tr)
    print(f"  leg N={en}: n={s['n']} exp={s['exp']:+.3f} t={s['t']:+.2f}")
both = backtest(dev, "EURUSD", 30, 300, 84, 2.0, 6.0) + backtest(dev, "EURUSD", 120, 300, 84, 2.0, 6.0)
s = summ(both)
print(f"  combined  : n={s['n']} exp={s['exp']:+.3f} t={s['t']:+.2f} RR={s['rr']:.2f} ddR={s['dd']:.1f}")
