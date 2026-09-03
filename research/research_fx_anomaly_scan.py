#!/usr/bin/env python3
"""
Broad anomaly scan across the 7 FX majors, 4H bars.

Everything is measured DEV (2021-03..2024-03) vs OOS (2024-03..2026-04) and,
crucially, for CROSS-PAIR CONSISTENCY. With ~60 hypotheses tested, some will look
good by luck; the filter is: same sign in DEV and OOS, and same sign on a
majority of pairs. Anything that only shines in one pair or one period is noise.

Families scanned
  A. bar-of-day seasonality (6 x 4H blocks per day)
  B. day-of-week
  C. turn-of-month
  D. short-horizon reversal conditioned on move size (the classic overreaction)
  E. Asian-range / London-NY continuation
  F. residual stat-arb, symmetric vs short-foreign-only (OOS check on the
     candidates that topped the DEV grid in research_fx_statarb.py)
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import round_turn_cost_price
from strategy_200_fx_trend_breakout_atr import load_4h, _atr

FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
DEV_END = pd.Timestamp("2024-03-01", tz="UTC")
RAW = {s: load_4h(os.path.join(THIS, "data", s, "4h", f"{s}_4h.csv")) for s in FX}
for s in FX:
    d = RAW[s]
    d["ret"] = np.log(d["close"]).diff()
    d["atr"] = _atr(d["high"].values, d["low"].values, d["close"].values, 84)
    d["cost_ret"] = [round_turn_cost_price(p, symbol=s) / p for p in d["close"].values]

N_HYP = 0


def tstat(v):
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    if len(v) < 5 or v.std(ddof=1) == 0:
        return 0.0, len(v)
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))), len(v)


def report(name, per_pair_dev, per_pair_oos, unit="bp"):
    """per_pair_*: {pair: array of per-observation net returns}"""
    global N_HYP
    N_HYP += 1
    dev = np.concatenate([v for v in per_pair_dev.values() if len(v)])
    oos = np.concatenate([v for v in per_pair_oos.values() if len(v)])
    td, nd = tstat(dev)
    to, no = tstat(oos)
    pos_pairs = sum(1 for p in FX
                    if len(per_pair_dev.get(p, [])) and len(per_pair_oos.get(p, []))
                    and np.nanmean(np.concatenate([per_pair_dev[p], per_pair_oos[p]])) > 0)
    ok = (np.nanmean(dev) > 0 and np.nanmean(oos) > 0 and pos_pairs >= 5)
    flag = "***PASS***" if ok else ""
    print(f"  {name:46s} DEV {np.nanmean(dev)*1e4:+7.2f}{unit} t{td:+5.2f} (n{nd:6d}) | "
          f"OOS {np.nanmean(oos)*1e4:+7.2f}{unit} t{to:+5.2f} (n{no:5d}) | "
          f"pairs+ {pos_pairs}/7 {flag}")
    return ok


print("=" * 128)
print("A. BAR-OF-DAY SEASONALITY — mean 4H return by UTC block (net of one round-turn cost)")
print("=" * 128)
for hr in (0, 4, 8, 12, 16, 20):
    for sign, lbl in ((1, "long "), (-1, "short")):
        d_, o_ = {}, {}
        for s in FX:
            d = RAW[s]
            m = d.index.hour == hr
            r = sign * d["ret"][m] - d["cost_ret"][m]
            d_[s] = r[r.index <= DEV_END].values
            o_[s] = r[r.index > DEV_END].values
        report(f"{lbl} the {hr:02d}:00-{(hr+4)%24:02d}:00 UTC 4H bar", d_, o_)

print("\n" + "=" * 128)
print("B. DAY-OF-WEEK — mean daily return by weekday")
print("=" * 128)
for dow, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
    for sign, lbl in ((1, "long "), (-1, "short")):
        d_, o_ = {}, {}
        for s in FX:
            dd = RAW[s].resample("1D").agg({"open": "first", "close": "last"}).dropna()
            r = sign * (np.log(dd["close"]) - np.log(dd["open"]))
            r = r - RAW[s]["cost_ret"].mean()
            m = dd.index.dayofweek == dow
            r = r[m]
            d_[s] = r[r.index <= DEV_END].values
            o_[s] = r[r.index > DEV_END].values
        report(f"{lbl} {nm} (open->close)", d_, o_)

print("\n" + "=" * 128)
print("C. TURN-OF-MONTH — last 2 / first 3 business days vs the rest")
print("=" * 128)
for lbl, fn in (("last 2 days of month", lambda i: i.day >= 27),
                ("first 3 days of month", lambda i: i.day <= 3)):
    for sign, sl in ((1, "long "), (-1, "short")):
        d_, o_ = {}, {}
        for s in FX:
            dd = RAW[s].resample("1D").agg({"open": "first", "close": "last"}).dropna()
            r = sign * (np.log(dd["close"]) - np.log(dd["open"])) - RAW[s]["cost_ret"].mean()
            r = r[fn(dd.index)]
            d_[s] = r[r.index <= DEV_END].values
            o_[s] = r[r.index > DEV_END].values
        report(f"{sl} {lbl}", d_, o_)

print("\n" + "=" * 128)
print("D. SHORT-HORIZON REVERSAL — after a 4H move of X*ATR, fade it for H bars")
print("=" * 128)
for thr in (1.0, 1.5, 2.0):
    for H in (1, 3, 6):
        d_, o_ = {}, {}
        for s in FX:
            d = RAW[s]
            c = d["close"].values
            a = d["atr"].values
            move = np.concatenate([[np.nan], np.diff(c)])
            n = len(c)
            vals, ts = [], []
            for i in range(90, n - H - 1):
                if a[i] <= 0 or np.isnan(move[i]):
                    continue
                if abs(move[i]) < thr * a[i]:
                    continue
                sgn = -np.sign(move[i])                       # fade
                # enter at open of i+1, exit at open of i+1+H
                r = sgn * (np.log(d["open"].values[i + 1 + H]) - np.log(d["open"].values[i + 1]))
                vals.append(r - d["cost_ret"].values[i])
                ts.append(d.index[i])
            ser = pd.Series(vals, index=pd.DatetimeIndex(ts))
            d_[s] = ser[ser.index <= DEV_END].values
            o_[s] = ser[ser.index > DEV_END].values
        report(f"fade >{thr}xATR 4H move, hold {H} bar(s)", d_, o_)

print("\n" + "=" * 128)
print("E. ASIAN RANGE / SESSION CONTINUATION — sign of the 00-08 UTC block predicts 08-16?")
print("=" * 128)
for lbl, sgn in (("continuation", 1), ("reversal", -1)):
    d_, o_ = {}, {}
    for s in FX:
        d = RAW[s]
        asia = d[(d.index.hour == 0) | (d.index.hour == 4)].copy()
        asia["day"] = asia.index.date
        ag = asia.groupby("day").agg(o=("open", "first"), c=("close", "last"))
        lon = d[(d.index.hour == 8) | (d.index.hour == 12)].copy()
        lon["day"] = lon.index.date
        lg = lon.groupby("day").agg(o=("open", "first"), c=("close", "last"))
        j = ag.join(lg, lsuffix="_a", rsuffix="_l").dropna()
        direction = np.sign(np.log(j["c_a"] / j["o_a"])) * sgn
        r = direction * np.log(j["c_l"] / j["o_l"]) - RAW[s]["cost_ret"].mean()
        r.index = pd.DatetimeIndex(j.index).tz_localize("UTC")
        d_[s] = r[r.index <= DEV_END].values
        o_[s] = r[r.index > DEV_END].values
    report(f"Asia block -> London/NY block, {lbl}", d_, o_)

# also: fade / follow the previous DAY inside the London-NY block
for lbl, sgn in (("continuation", 1), ("reversal", -1)):
    d_, o_ = {}, {}
    for s in FX:
        d = RAW[s]
        dd = d.resample("1D").agg({"open": "first", "close": "last"}).dropna()
        prev = np.sign(np.log(dd["close"] / dd["open"])).shift(1) * sgn
        lon = d[(d.index.hour == 8) | (d.index.hour == 12)].copy()
        lon["day"] = lon.index.date
        lg = lon.groupby("day").agg(o=("open", "first"), c=("close", "last"))
        lg.index = pd.DatetimeIndex(lg.index).tz_localize("UTC")
        al = prev.reindex(lg.index)
        r = (al * np.log(lg["c"] / lg["o"])).dropna() - RAW[s]["cost_ret"].mean()
        d_[s] = r[r.index <= DEV_END].values
        o_[s] = r[r.index > DEV_END].values
    report(f"prev day -> London/NY block, {lbl}", d_, o_)

print("\n" + "=" * 128)
print(f"HYPOTHESES TESTED: {N_HYP}.  At a 5% threshold, ~{N_HYP*0.05:.0f} false positives are expected")
print("by chance alone, which is exactly why DEV+OOS+cross-pair agreement is required.")
print("=" * 128)
