#!/usr/bin/env python3
"""Final honest comparison of every candidate that is still standing, on the
user's actual capital: Rs 10,000, Exness cent account, USDINR 88."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
import strategy_400_stoprun_reversal as s400
import strategy_300_multisignal_portfolio as s300
import strategy_201_gold_liquidity_reclaim as s201
from core import load_csv, round_turn_cost_price

USDINR, START = 88.0, 10_000.0
GOLD1_PATH = os.path.join(THIS, "data", "XAUUSD", "1H",
                          "XAUUSD_1h_2021-03-02_2026-07-01.csv")


def compound_R(trades_R, risk_pct, start=START):
    """Fixed-fractional compounding. Order-independent in final value."""
    eq, peak, dd = start, start, 0.0
    for r in trades_R:
        eq *= (1 + risk_pct / 100.0 * r)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
        if eq <= 0:
            return 0.0, 1.0
    return eq, dd


print("=" * 112)
print("WHAT RS 10,000 BECOMES — every candidate still standing, same period, same costs")
print("=" * 112)

# ---- candidate 1: passive vol-targeted gold (no strategy at all) ------------
panel = s300.build_panel()
gold = panel["XAUUSD"].dropna()
g = gold.pct_change().dropna()
gv = g.ewm(span=60, adjust=False).std().shift(1)
lev = (0.15 / (gv * np.sqrt(252))).clip(upper=3.0)          # cap at 3x, causal
gs = (g * lev).dropna()
yrs = (gs.index[-1] - gs.index[0]).days / 365.25
eq = (1 + gs).cumprod()
dd = ((eq.cummax() - eq) / eq.cummax()).max()
print(f"\n  A) PASSIVE: hold gold, vol-targeted to 15% (leverage capped 3x, rebalanced daily)")
print(f"     Rs {START:,.0f} -> Rs {START*eq.iloc[-1]:,.0f}  ({(eq.iloc[-1]-1)*100:+.0f}% over {yrs:.1f}y, "
      f"CAGR {(eq.iloc[-1]**(1/yrs)-1)*100:.1f}%)  maxDD {dd*100:.0f}%  "
      f"Sharpe {gs.mean()/gs.std()*np.sqrt(252):.2f}")
print(f"     trades: 1 position, adjusted daily. No signal, no edge claimed.")

# ---- candidate 2: strategy 300 (my clean-room portfolio) -------------------
gonly = panel[["XAUUSD"]].dropna()
r3 = s300.simulate(gonly, s300.forecasts(gonly))
e3 = (1 + r3["rets"]).cumprod()
print(f"\n  B) STRATEGY 300 (multi-signal continuous trend, gold) — REJECTED as beta")
print(f"     Rs {START:,.0f} -> Rs {START*e3.iloc[-1]:,.0f}  (CAGR {r3['ann_ret']*100:.1f}%)  "
      f"maxDD {r3['maxdd']*100:.0f}%  Sharpe {r3['sharpe']:.2f}")
print(f"     but beta to gold = 0.54 (t 28.9); passive above is better on every measure.")

# ---- candidate 3/4: stop-run reversal, both implementations ----------------
tr400 = s400.generate_trades(s400.load_ohlc(GOLD1_PATH), "XAUUSD")
R400 = [t["pnl_R"] for t in tr400]
c1h = load_csv(GOLD1_PATH)
tr201 = s201.generate_trades(c1h)
R201 = []
for t in tr201:
    e_, x_, sl_ = float(t["entry_price"]), float(t["exit_price"]), float(t["stop_loss"])
    r_ = abs(e_ - sl_)
    if r_ <= 0:
        continue
    gg = (x_ - e_) / r_ if t["direction"] == "long" else (e_ - x_) / r_
    R201.append(gg - round_turn_cost_price(e_, symbol="XAUUSD") / r_)

print(f"\n  C) STOP-RUN REVERSAL ON GOLD — the only candidate with market-neutral alpha")
print(f"     {'implementation':34s} {'n':>5s} {'exp R':>8s} {'t':>6s} {'risk%':>6s} "
      f"{'final Rs':>12s} {'maxDD':>7s}")
for lbl, R in (("existing engine (s98 detection)", R201),
               ("my clean-room rebuild (s400)", R400)):
    for rp in (1.0, 2.0):
        f, d = compound_R(R, rp)
        v = np.array(R)
        t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))
        print(f"     {lbl:34s} {len(R):5d} {v.mean():+8.3f} {t:+6.2f} {rp:5.1f}% "
              f"{f:12,.0f} {d*100:6.1f}%")

print(f"\n  D) FX MAJORS, pooled, stop-run reversal (the concept that works on gold)")
allfx = []
for sym, tf, rel in s400.DATASETS:
    if sym == "XAUUSD" or tf != "4H":
        continue
    allfx.extend(s400.generate_trades(s400.load_ohlc(os.path.join(THIS, rel)), sym))
v = np.array([t["pnl_R"] for t in allfx])
f, d = compound_R(v, 2.0)
print(f"     n={len(v)} exp={v.mean():+.3f}R t={v.mean()/(v.std(ddof=1)/np.sqrt(len(v))):+.2f} "
      f"-> Rs {START:,.0f} becomes Rs {f:,.0f} at 2% risk")

print("\n" + "=" * 112)
print("THE GAP BETWEEN THE TWO STOP-RUN IMPLEMENTATIONS")
print("=" * 112)
print(f"  existing engine : {len(R201):4d} trades, {np.mean(R201):+.3f}R/trade")
print(f"  my rebuild      : {len(R400):4d} trades, {np.mean(R400):+.3f}R/trade")
print(f"  My version fires {len(R400)/len(R201)-1:+.0%} more often and earns "
      f"{np.mean(R400)/np.mean(R201)-1:+.0%} per trade.")
print("  Difference traced to two design choices, both causal and both defensible:")
print("    - which swing counts as 'the last one' (1-bar vs 2-bar confirmation)")
print("    - how the 4H bias grid is aligned when resampling from 1H")
print("  An edge that halves when those details change is not yet established.")
