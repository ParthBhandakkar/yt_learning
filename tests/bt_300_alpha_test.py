#!/usr/bin/env python3
"""
The decisive test: is any of this ALPHA, or just gold beta?

Gold rose ~129% over the sample at ~15% annual volatility, which is a
buy-and-hold Sharpe close to 1.0 on its own. Any gold strategy must be measured
AGAINST that benchmark, not against zero. This script regresses each candidate's
returns on gold's returns and reports alpha with a t-stat, plus the behaviour of
each candidate during gold's own drawdowns.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
import strategy_300_multisignal_portfolio as s300
import strategy_201_gold_liquidity_reclaim as s201
from core import load_csv, round_turn_cost_price

SPLIT = pd.Timestamp("2024-03-01", tz="UTC")


def ols(y, x):
    """y = a + b*x. Returns alpha (annualised), beta, t-stats, R^2."""
    y, x = np.asarray(y, float), np.asarray(x, float)
    m = np.isfinite(y) & np.isfinite(x)
    y, x = y[m], x[m]
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    s2 = resid @ resid / (n - 2)
    cov = s2 * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    r2 = 1 - (resid @ resid) / ((y - y.mean()) @ (y - y.mean()))
    return dict(alpha_ann=coef[0] * 252, beta=coef[1],
                t_alpha=coef[0] / se[0], t_beta=coef[1] / se[1], r2=r2, n=n)


def perf(r: pd.Series, lbl: str):
    r = r.dropna()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    eq = (1 + r).cumprod()
    dd = ((eq.cummax() - eq) / eq.cummax()).max()
    ann = eq.iloc[-1] ** (1 / yrs) - 1
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    print(f"  {lbl:38s} ann {ann*100:+7.2f}%  vol {r.std()*np.sqrt(252)*100:5.2f}%  "
          f"Sharpe {sh:+5.2f}  maxDD {dd*100:5.1f}%")
    return dict(ann=ann, sharpe=sh, dd=dd)


print("=" * 118)
print("1) THE BENCHMARK — what does simply holding gold do over this exact period?")
print("=" * 118)
panel = s300.build_panel()
sub = panel.dropna()
gold = sub["XAUUSD"]
g_ret = gold.pct_change().dropna()
perf(g_ret, "gold buy & hold (unlevered)")
# vol-matched buy and hold, so the comparison is apples to apples
tgt = 0.15
gv = g_ret.ewm(span=60, adjust=False).std().shift(1)          # causal scaling
g_scaled = (g_ret * (tgt / (gv * np.sqrt(252))).clip(upper=5)).dropna()
perf(g_scaled, "gold buy & hold, vol-targeted 15%")

print("\n" + "=" * 118)
print("2) STRATEGY 300 (continuous multi-signal trend) vs gold")
print("=" * 118)
gold_only = panel[["XAUUSD"]].dropna()
r300g = s300.simulate(gold_only, s300.forecasts(gold_only))
s = r300g["rets"]
perf(s, "s300 gold-only")
common = s.index.intersection(g_ret.index)
o = ols(s.reindex(common), g_ret.reindex(common))
print(f"  regression vs gold: alpha {o['alpha_ann']*100:+6.2f}%/yr (t {o['t_alpha']:+5.2f})  "
      f"beta {o['beta']:+5.2f} (t {o['t_beta']:+6.2f})  R2 {o['r2']:.2f}  n {o['n']}")
pos = r300g["positions"]["XAUUSD"]
print(f"  average net position {pos.mean():+.2f} (long {(pos>0).mean()*100:.0f}% of days, "
      f"short {(pos<0).mean()*100:.0f}%)")
print(f"  -> {'MOSTLY BETA' if o['t_alpha'] < 1.5 else 'has alpha'}: it is long gold "
      f"{(pos>0).mean()*100:.0f}% of the time with beta {o['beta']:.2f}")

r300fx = s300.simulate(panel[s300.FX].dropna(), s300.forecasts(panel[s300.FX].dropna()))
perf(r300fx["rets"], "s300 FX-only (7 pairs)")

print("\n" + "=" * 118)
print("3) STRATEGY 201 (discrete liquidity reclaim) vs gold")
print("=" * 118)
c1h = load_csv(os.path.join(THIS, "data", "XAUUSD", "1H",
                            "XAUUSD_1h_2021-03-02_2026-07-01.csv"))
tr = s201.generate_trades(c1h)
rows = []
for t in tr:
    e, x, sl = float(t["entry_price"]), float(t["exit_price"]), float(t["stop_loss"])
    r = abs(e - sl)
    if r <= 0:
        continue
    g = (x - e) / r if t["direction"] == "long" else (e - x) / r
    rows.append(dict(xt=pd.Timestamp(t["exit_time"]),
                     netR=g - round_turn_cost_price(e, symbol="XAUUSD") / r,
                     dirn=t["direction"]))
# convert R-per-trade into a daily return stream at 2% risk per trade
d = pd.Series({r["xt"]: r["netR"] for r in rows}).groupby(level=0).sum()
daily = (d * 0.02).resample("1D").sum().reindex(g_ret.index).fillna(0.0)
perf(daily[daily.index >= d.index[0]], "s201 @2% risk/trade")
o2 = ols(daily, g_ret)
print(f"  regression vs gold: alpha {o2['alpha_ann']*100:+6.2f}%/yr (t {o2['t_alpha']:+5.2f})  "
      f"beta {o2['beta']:+5.2f} (t {o2['t_beta']:+6.2f})  R2 {o2['r2']:.3f}  n {o2['n']}")
nl = sum(1 for r in rows if r["dirn"] == "long")
print(f"  trade mix: {nl} long / {len(rows)-nl} short "
      f"({nl/len(rows)*100:.0f}% long) -> near-balanced, so beta is structurally low")
print(f"  -> {'HAS ALPHA' if o2['t_alpha'] > 1.5 else 'mostly beta'}")

print("\n" + "=" * 118)
print("4) BEHAVIOUR DURING GOLD'S OWN DRAWDOWNS (the test buy-and-hold fails)")
print("=" * 118)
geq = (1 + g_ret).cumprod()
gdd = (geq.cummax() - geq) / geq.cummax()
bad = gdd > 0.05
print(f"  days with gold >5% below its high: {bad.sum()} of {len(bad)} ({bad.mean()*100:.0f}%)")
for lbl, ser in (("gold buy & hold", g_ret), ("s300 gold-only", s.reindex(g_ret.index).fillna(0)),
                 ("s201 @2% risk", daily)):
    v = ser[bad.reindex(ser.index).fillna(False)]
    tot = (1 + v).prod() - 1
    print(f"  {lbl:24s} cumulative return during those days: {tot*100:+7.2f}%")

print("\n" + "=" * 118)
print("5) TIME SPLIT — both candidates, dev vs held-back")
print("=" * 118)
for lbl, ser in (("gold buy & hold", g_ret), ("s300 gold-only", s), ("s201 @2% risk", daily)):
    a = ser[ser.index <= SPLIT]
    b = ser[ser.index > SPLIT]
    f = lambda x: (x.mean() / x.std() * np.sqrt(252)) if len(x) > 30 and x.std() > 0 else np.nan
    print(f"  {lbl:24s} Sharpe  2021-24 {f(a):+5.2f}   2024-26 {f(b):+5.2f}")
