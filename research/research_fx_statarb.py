#!/usr/bin/env python3
"""
FX residual stat-arb research (Avellaneda-Lee style), 4H bars, 7 majors.

RATIONALE
---------
Every strategy tested so far (s97 fade, s200 breakout, daily trend, xs-momentum)
traded each pair's TOTAL return. But a G10 pair's total return is dominated by one
common factor - the US dollar - which behaves like a random walk. That is why
price-only directional models keep coming out at t~0.3.

Decompose instead:
    r_i(t) = beta_i * F(t) + e_i(t)
where F is the common (dollar) factor. F is unpredictable, but the IDIOSYNCRATIC
part e_i is driven by relative-value flows and is documented to mean-revert.
Trading only the residual removes the random-walk component from the bet.

Supporting clue from research_fx_concept_search.py: cross-sectional FX MOMENTUM
scored Sharpe -0.64 to -0.69 net of cost across many configs. A persistently
negative momentum Sharpe is a positive REVERSAL Sharpe. That is the same effect
seen from a cruder angle.

METHOD (all causal)
-------------------
1. Convert each pair to a log price of FOREIGN currency vs USD
   (XXXUSD -> +log(px);  USDXXX -> -log(px)), so all 7 series are comparable.
2. Common factor F(t) = cross-sectional mean log return of the 7 currencies
   (equal-weighted dollar factor). Uses only data at t.
3. Over a trailing window W ending at bar i, regress r_j on F -> beta_i,
   residuals e. Cumulate the residuals; s-score = last cumulative residual
   standardised by the window's own std.
4. Entry when |s| >= S_ENTRY (rich -> short the currency, cheap -> long),
   filled at the OPEN of bar i+1.
5. Exit when |s| <= S_EXIT, or an ATR stop is hit, or MAX_HOLD bars elapse.

Everything at bar i uses bars <= i only. Signal at close of i, fill at open i+1.

PROTOCOL
--------
DEV = 2021-03 .. 2024-03 (first ~57% of the common panel)
OOS = 2024-03 .. 2026-07 (untouched until parameters are frozen)
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import round_turn_cost_price
from strategy_200_fx_trend_breakout_atr import load_4h, _atr

FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
INVERTED = {"USDJPY", "USDCHF", "USDCAD"}      # USD is the base -> invert for the panel
DEV_END = pd.Timestamp("2024-03-01", tz="UTC")
rng = np.random.default_rng(23)


def build_panel():
    raw = {s: load_4h(os.path.join(THIS, "data", s, "4h", f"{s}_4h.csv")) for s in FX}
    common = None
    for s in FX:
        common = raw[s].index if common is None else common.intersection(raw[s].index)
    px = pd.DataFrame({s: raw[s].loc[common, "close"] for s in FX})
    # log price of the FOREIGN currency against USD
    lp = pd.DataFrame({s: (-np.log(px[s]) if s in INVERTED else np.log(px[s])) for s in FX})
    return raw, common, px, lp


RAW, IDX, PX, LP = build_panel()
RET = LP.diff()
ATR = {s: pd.Series(_atr(RAW[s].loc[IDX, "high"].values, RAW[s].loc[IDX, "low"].values,
                         RAW[s].loc[IDX, "close"].values, 84), index=IDX) for s in FX}
print(f"panel: {len(IDX)} aligned 4H bars, {IDX[0].date()} -> {IDX[-1].date()} "
      f"({(IDX[-1]-IDX[0]).days/365.25:.1f}y), {len(FX)} currencies")
print(f"DEV <= {DEV_END.date()}  ({(RET.index <= DEV_END).sum()} bars)   "
      f"OOS > {DEV_END.date()}  ({(RET.index > DEV_END).sum()} bars)")


def s_scores(window: int) -> pd.DataFrame:
    """s-score per currency per bar, using only bars <= i."""
    F = RET.mean(axis=1)                                  # equal-weighted dollar factor
    out = {}
    Fv = F.values
    for s in FX:
        r = RET[s].values
        z = np.full(len(r), np.nan)
        for i in range(window, len(r)):
            rw, fw = r[i - window + 1:i + 1], Fv[i - window + 1:i + 1]
            m = ~(np.isnan(rw) | np.isnan(fw))
            if m.sum() < window * 0.8:
                continue
            rw, fw = rw[m], fw[m]
            fvar = fw.var()
            beta = np.cov(rw, fw)[0, 1] / fvar if fvar > 0 else 0.0
            e = rw - beta * fw                            # idiosyncratic returns
            X = np.cumsum(e)                              # cumulative residual
            sd = X.std()
            if sd > 0:
                z[i] = (X[-1] - X.mean()) / sd
        out[s] = pd.Series(z, index=RET.index)
    return pd.DataFrame(out)


def backtest(window, s_entry, s_exit, k_stop, max_hold, cost_mult=1.0, S=None):
    """Trade the residual: rich currency -> short the pair's foreign leg."""
    S = s_scores(window) if S is None else S
    trades = []
    for sym in FX:
        z = S[sym].values
        o = RAW[sym].loc[IDX, "open"].values
        h = RAW[sym].loc[IDX, "high"].values
        l = RAW[sym].loc[IDX, "low"].values
        a = ATR[sym].values
        inv = sym in INVERTED
        n = len(z)
        i = window + 1
        while i < n - 1:
            if np.isnan(z[i]) or a[i] <= 0:
                i += 1
                continue
            if abs(z[i]) < s_entry:
                i += 1
                continue
            # residual rich (z>0) => foreign currency over-valued => short foreign.
            short_foreign = z[i] > 0
            # translate to the tradable pair: inverted pairs flip the sign
            pos = -1 if short_foreign else 1
            if inv:
                pos = -pos
            ei = i + 1
            entry = o[ei]
            risk = k_stop * a[i]
            if risk <= 0:
                i += 1
                continue
            stop = entry - risk if pos == 1 else entry + risk
            xi = xp = None
            for j in range(ei, n):
                if pos == 1 and l[j] <= stop:
                    xi, xp = j, stop
                    break
                if pos == -1 and h[j] >= stop:
                    xi, xp = j, stop
                    break
                if not np.isnan(z[j]) and abs(z[j]) <= s_exit:      # reverted
                    xi = min(j + 1, n - 1)
                    xp = o[xi]
                    break
                if j - ei + 1 >= max_hold:
                    xi = min(j + 1, n - 1)
                    xp = o[xi]
                    break
            if xi is None:
                xi, xp = n - 1, RAW[sym].loc[IDX, "close"].values[n - 1]
            g = (xp - entry) / risk if pos == 1 else (entry - xp) / risk
            c = cost_mult * round_turn_cost_price(entry, symbol=sym) / risk
            trades.append(dict(sym=sym, ts=IDX[ei], xts=IDX[xi], pos=pos,
                               netR=g - c, grossR=g, costR=c, held=xi - ei + 1))
            i = xi + 1
    return trades


def st(v):
    v = np.asarray([x for x in v], float)
    if len(v) < 3:
        return None
    sd = v.std(ddof=1)
    t = v.mean() / (sd / np.sqrt(len(v))) if sd > 0 else 0.0
    w, l = v[v > 0], v[v <= 0]
    eq = np.cumsum(v)
    dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
    return dict(n=len(v), exp=v.mean(), t=t, win=(v > 0).mean(), total=v.sum(), dd=dd,
                rr=(w.mean() / abs(l.mean())) if len(w) and len(l) else np.nan)


def split(tr):
    return ([t["netR"] for t in tr if t["ts"] <= DEV_END],
            [t["netR"] for t in tr if t["ts"] > DEV_END])


print("\n" + "=" * 112)
print("STEP 1 — is the residual predictive at all? (DEV ONLY, 2021-03..2024-03)")
print("=" * 112)
print("  mean forward residual-adjusted return of the pair, conditioned on today's s-score")
for window in (60, 120, 240):
    S = s_scores(window)
    dev_mask = RET.index <= DEV_END
    print(f"\n  window={window} bars ({window*4/24:.0f} days)")
    print(f"    {'s-score bucket':>18} {'n':>6} " + "".join(f"{f'fwd{k}b':>9}" for k in (6, 12, 30, 60)))
    for lo, hi, lbl in [(-99, -2, "s <= -2"), (-2, -1, "-2..-1"), (-1, 1, "-1..+1"),
                        (1, 2, "+1..+2"), (2, 99, "s >= +2")]:
        rows = {k: [] for k in (6, 12, 30, 60)}
        cnt = 0
        for sym in FX:
            z = S[sym].reindex(RET.index)
            lpx = LP[sym]
            m = dev_mask & (z > lo) & (z <= hi)
            ii = np.where(m.values)[0]
            cnt += len(ii)
            for k in (6, 12, 30, 60):
                jj = ii[ii + k < len(lpx)]
                # forward return of the FOREIGN currency, sign-flipped so that a
                # positive number means "the residual reverted as predicted"
                fwd = (lpx.values[jj + k] - lpx.values[jj])
                rows[k].extend(-np.sign((lo + hi) / 2) * fwd)
        print(f"    {lbl:>18} {cnt:6d} " +
              "".join(f"{np.mean(rows[k])*10000:+9.1f}" for k in (6, 12, 30, 60)))
print("\n  (values are basis points; positive = residual reverted in the predicted direction)")

print("\n" + "=" * 112)
print("STEP 2 — parameter grid, DEV ONLY. Looking for a plateau, not a peak.")
print("=" * 112)
print(f"  {'win':>4} {'s_in':>5} {'s_out':>6} {'kstop':>6} {'hold':>5} {'n':>5} {'win%':>6} "
      f"{'RR':>5} {'exp':>7} {'t':>6} {'ddR':>6}")
cache = {}
best = []
for window in (60, 120, 240):
    S = cache.setdefault(window, s_scores(window))
    for s_entry in (1.5, 2.0, 2.5):
        for s_exit in (0.0, 0.5):
            for k_stop in (2.0, 3.0):
                for max_hold in (30, 60, 120):
                    tr = backtest(window, s_entry, s_exit, k_stop, max_hold, S=S)
                    d, _ = split(tr)
                    s = st(d)
                    if not s or s["n"] < 30:
                        continue
                    best.append((window, s_entry, s_exit, k_stop, max_hold, s))
                    print(f"  {window:4d} {s_entry:5.1f} {s_exit:6.1f} {k_stop:6.1f} {max_hold:5d} "
                          f"{s['n']:5d} {s['win']*100:5.1f}% {s['rr']:5.2f} {s['exp']:+7.3f} "
                          f"{s['t']:+6.2f} {s['dd']:6.1f}")

print("\n  DEV ranked by t-stat (top 10):")
for r in sorted(best, key=lambda x: -x[5]["t"])[:10]:
    print(f"    win={r[0]:3d} s_in={r[1]} s_out={r[2]} kstop={r[3]} hold={r[4]:3d} -> "
          f"n={r[5]['n']:4d} exp={r[5]['exp']:+.3f} t={r[5]['t']:+.2f} RR={r[5]['rr']:.2f}")
