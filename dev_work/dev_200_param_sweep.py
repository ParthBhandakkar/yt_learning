#!/usr/bin/env python3
"""DESIGN PHASE ONLY — parameter sweep on EURUSD 1999-2014 4H bars.

Nothing here touches the other pairs or the post-2015 data. Whatever plateau
comes out is frozen and then applied unchanged to every instrument.
Concept: volatility-normalised trend continuation
  entry  = Donchian breakout of the prior N closed bars, in the direction of a
           long EMA trend filter
  stop   = k_init * ATR at entry  (= 1R)
  exit   = chandelier ATR trail (extreme since entry -/+ k_trail * ATR)
Positive RR by construction (trail lets winners run, stop caps losers).
"""
import os
import sys
import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import round_turn_cost_price


def load4h(pair):
    f = os.path.join(THIS, "data", pair, "4h", f"{pair}_4h.csv")
    d = pd.read_csv(f)
    idx = pd.to_datetime(d["time"], unit="s", utc=True)
    df = pd.DataFrame({"open": d["open"].values, "high": d["high"].values,
                       "low": d["low"].values, "close": d["close"].values}, index=idx)
    return df[~df.index.duplicated(keep="first")].sort_index()


def atr(h, l, c, n):
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().values


def ema(c, n):
    return pd.Series(c).ewm(span=n, adjust=False).mean().values


def backtest(df, symbol, entry_n, ema_n, atr_n, k_init, k_trail, max_hold=None):
    """Fully causal. Signal on close of bar i -> fill at open of i+1.
    While open: stop/trail checked against bar i using state known at open of i,
    then the trail is advanced with bar i's own extreme/ATR for use from i+1."""
    o, h, l, c = (df[x].values for x in ("open", "high", "low", "close"))
    n = len(c)
    a = atr(h, l, c, atr_n)
    e = ema(c, ema_n) if ema_n else None
    # prior-bar Donchian on CLOSED bars strictly before i+1 => uses bars <= i
    hh = pd.Series(h).rolling(entry_n).max().shift(1).values   # highest high of the N bars before i
    ll = pd.Series(l).rolling(entry_n).min().shift(1).values

    warm = max(entry_n, atr_n, ema_n or 0) + 5
    trades = []
    i = warm
    pos = 0
    while i < n - 1:
        if pos == 0:
            if np.isnan(hh[i]) or a[i] <= 0:
                i += 1
                continue
            up = c[i] > hh[i] and (e is None or c[i] > e[i])
            dn = c[i] < ll[i] and (e is None or c[i] < e[i])
            if not (up or dn):
                i += 1
                continue
            pos = 1 if up else -1
            ei = i + 1
            entry = o[ei]
            risk = k_init * a[i]
            if risk <= 0:
                pos = 0
                i += 1
                continue
            trail = entry - risk if pos == 1 else entry + risk
            extreme = entry
            held = 0
            i = ei
            continue
        # in position, bar i
        held += 1
        if pos == 1:
            if l[i] <= trail:
                px = trail
                trades.append((ei, i, pos, entry, px, risk, held))
                pos = 0
                i += 1
                continue
            extreme = max(extreme, h[i])
            trail = max(trail, extreme - k_trail * a[i])
        else:
            if h[i] >= trail:
                px = trail
                trades.append((ei, i, pos, entry, px, risk, held))
                pos = 0
                i += 1
                continue
            extreme = min(extreme, l[i])
            trail = min(trail, extreme + k_trail * a[i])
        if max_hold and held >= max_hold:
            px = o[i + 1] if i + 1 < n else c[i]
            trades.append((ei, min(i + 1, n - 1), pos, entry, px, risk, held))
            pos = 0
        i += 1

    out = []
    for (ei, xi, p, entry, px, risk, held) in trades:
        gross = (px - entry) / risk if p == 1 else (entry - px) / risk
        cost = round_turn_cost_price(entry, symbol=symbol) / risk
        out.append(dict(entry_time=df.index[ei], exit_time=df.index[xi], dirn=p,
                        entry=entry, exit=px, risk=risk, netR=gross - cost,
                        grossR=gross, held=held))
    return out


def summ(tr):
    if len(tr) < 5:
        return None
    v = np.array([t["netR"] for t in tr])
    w, lo = v[v > 0], v[v <= 0]
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if v.std(ddof=1) > 0 else 0
    rr = (w.mean() / abs(lo.mean())) if len(w) and len(lo) else np.nan
    eq = np.cumsum(v)
    dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
    return dict(n=len(v), exp=v.mean(), t=t, win=(v > 0).mean(), rr=rr,
                total=v.sum(), dd=dd, pf=(w.sum() / abs(lo.sum()) if len(lo) else np.nan))


df = load4h("EURUSD")
dev = df[(df.index >= "1999-01-01") & (df.index < "2015-01-01")]
print(f"DEV SET: EURUSD 4H {dev.index[0].date()} -> {dev.index[-1].date()}  bars={len(dev)}")
print()
print(f"{'entryN':>6} {'emaN':>5} {'k_init':>6} {'k_trl':>6} {'n':>4} {'win%':>5} {'RR':>5} "
      f"{'exp':>7} {'t':>6} {'totR':>7} {'PF':>5} {'ddR':>6}")
rows = []
for entry_n in (30, 60, 90, 120, 180):
    for ema_n in (0, 150, 300, 600):
        for k_init in (1.5, 2.0, 3.0):
            for k_trail in (3.0, 4.0, 6.0):
                tr = backtest(dev, "EURUSD", entry_n, ema_n, 84, k_init, k_trail)
                s = summ(tr)
                if not s:
                    continue
                rows.append((entry_n, ema_n, k_init, k_trail, s))
                print(f"{entry_n:6d} {ema_n:5d} {k_init:6.1f} {k_trail:6.1f} {s['n']:4d} "
                      f"{s['win']*100:5.1f} {s['rr']:5.2f} {s['exp']:+7.3f} {s['t']:+6.2f} "
                      f"{s['total']:+7.1f} {s['pf']:5.2f} {s['dd']:6.1f}")

print()
print("top 12 by t-stat:")
for r in sorted(rows, key=lambda r: -r[4]["t"])[:12]:
    print(f"  entryN={r[0]:3d} emaN={r[1]:3d} k_init={r[2]} k_trail={r[3]}  "
          f"n={r[4]['n']:3d} exp={r[4]['exp']:+.3f} t={r[4]['t']:+.2f} RR={r[4]['rr']:.2f} PF={r[4]['pf']:.2f}")
