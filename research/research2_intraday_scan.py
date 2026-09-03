#!/usr/bin/env python3
"""
Clean-slate intraday research. Nothing is imported from the existing strategy
code: own loader, own cost model, own statistics.

Two dimensions of this dataset have never been used:
  (a) tick_volume  - a participation proxy
  (b) precise intraday session structure (1H / 15m granularity)

And one instrument class has the cost headroom to let a small edge survive:
gold's 1H ATR is ~$8 against a ~$0.40 round turn (5%), whereas GBPUSD's 4H ATR is
~30 pips against ~1.2 pips (4%) but its per-bar signal is far smaller. The FX
scan already showed the gross signal there is below the spread; gold is where an
intraday effect can clear costs.

Protocol: DEV 2021-03..2024-03, OOS 2024-03..2026-07. Report both, always.
An effect only counts if DEV and OOS agree in sign AND the size clears the cost.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
DEV_END = pd.Timestamp("2024-03-01", tz="UTC")

# --- my own cost model, stated explicitly (round turn, in PRICE units) -------
# XAUUSD: Exness Standard typical spread ~$0.20-0.30 + slippage -> $0.40
# GBPUSD: ~1.0 pip spread + slippage -> 1.2 pips
COST = {"XAUUSD": 0.40, "GBPUSD": 0.00012}
N_TESTS = 0


def load(path: str) -> pd.DataFrame:
    d = pd.read_csv(path)
    idx = pd.to_datetime(d["time"], unit="s", utc=True)
    out = pd.DataFrame({"open": d["open"].values, "high": d["high"].values,
                        "low": d["low"].values, "close": d["close"].values,
                        "vol": d["tick_volume"].values}, index=idx)
    return out[~out.index.duplicated(keep="first")].sort_index()


GOLD1 = load(os.path.join(THIS, "data", "XAUUSD", "1H",
                          "XAUUSD_1h_2021-03-02_2026-07-01.csv"))
GBP1 = load(os.path.join(THIS, "data", "GBPUSD", "1h", "GBPUSD_1h.csv"))
print(f"gold 1H : {len(GOLD1)} bars {GOLD1.index[0]} -> {GOLD1.index[-1]}")
print(f"gbp  1H : {len(GBP1)} bars {GBP1.index[0]} -> {GBP1.index[-1]}")


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


for df in (GOLD1, GBP1):
    df["atr"] = atr(df)
    df["ret"] = np.log(df["close"]).diff()
    df["relvol"] = df["vol"] / df["vol"].rolling(120).median()


def tstat(v):
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if len(v) < 8 or v.std(ddof=1) == 0:
        return 0.0, len(v), np.nan
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))), len(v), v.mean()


def check(name, ser, sym, min_n=40):
    """ser: pd.Series of NET returns (already cost-adjusted), indexed by entry time."""
    global N_TESTS
    N_TESTS += 1
    d = ser[ser.index <= DEV_END]
    o = ser[ser.index > DEV_END]
    td, nd, md = tstat(d.values)
    to, no, mo = tstat(o.values)
    ok = (nd >= min_n and no >= min_n and md > 0 and mo > 0 and td > 1.0 and to > 1.0)
    print(f"  {name:52s} DEV {md*1e4:+8.2f}bp t{td:+5.2f} n{nd:5d} | "
          f"OOS {mo*1e4:+8.2f}bp t{to:+5.2f} n{no:5d} {'  <<< PASS' if ok else ''}")
    return ok


passes = []

print("\n" + "=" * 124)
print("PART 1 — HOUR OF DAY, gold 1H (net of one round turn each way)")
print("=" * 124)
for sym, df in (("XAUUSD", GOLD1), ("GBPUSD", GBP1)):
    c = COST[sym]
    print(f"\n  {sym}")
    for hr in range(24):
        m = df.index.hour == hr
        if m.sum() < 200:
            continue
        gross = np.log(df["close"][m] / df["open"][m])
        cost_rel = c / df["open"][m]
        for sgn, lbl in ((1, "long "), (-1, "short")):
            r = sgn * gross - cost_rel
            if check(f"{lbl}{sym} {hr:02d}:00-{hr:02d}:59 UTC", r, sym, min_n=150):
                passes.append((f"hour {hr} {lbl}{sym}", r))

print("\n" + "=" * 124)
print("PART 2 — ASIAN-RANGE BREAKOUT ON GOLD (session ORB, first-touch, stop = other side)")
print("=" * 124)


@dataclass
class ORBCfg:
    range_start: int
    range_end: int          # exclusive; range built from bars [start, end)
    trade_end: int          # stop looking for a breakout after this hour
    flat_hour: int          # close the position at this hour's close
    k_stop: float           # stop = k_stop x range width, beyond the opposite side
    buf_atr: float          # breakout must clear the range by this many ATR


def orb(df, sym, cfg: ORBCfg):
    c = COST[sym]
    out = {}
    for day, g in df.groupby(df.index.date):
        rng = g[(g.index.hour >= cfg.range_start) & (g.index.hour < cfg.range_end)]
        if len(rng) < (cfg.range_end - cfg.range_start) - 1:
            continue
        hi, lo = rng["high"].max(), rng["low"].min()
        width = hi - lo
        if width <= 0:
            continue
        sess = g[(g.index.hour >= cfg.range_end) & (g.index.hour <= cfg.trade_end)]
        if sess.empty:
            continue
        a = rng["atr"].iloc[-1]
        if not np.isfinite(a) or a <= 0:
            continue
        buf = cfg.buf_atr * a
        pos = entry = stop = None
        for ts, row in sess.iterrows():
            if pos is None:
                if row["close"] > hi + buf:
                    pos, entry = 1, row["close"]
                    stop = entry - cfg.k_stop * width
                elif row["close"] < lo - buf:
                    pos, entry = -1, row["close"]
                    stop = entry + cfg.k_stop * width
                if pos is not None:
                    etime = ts
                continue
            if pos == 1 and row["low"] <= stop:
                out[etime] = (stop - entry) / entry - c / entry
                pos = None
                break
            if pos == -1 and row["high"] >= stop:
                out[etime] = (entry - stop) / entry - c / entry
                pos = None
                break
        if pos is not None:
            rest = g[g.index.hour <= cfg.flat_hour]
            px = rest["close"].iloc[-1] if len(rest) else sess["close"].iloc[-1]
            out[etime] = pos * (px - entry) / entry - c / entry
    return pd.Series(out).sort_index()


cfgs = [
    ("Asia 00-06 -> break 06-14, flat 20", ORBCfg(0, 6, 14, 20, 1.0, 0.0)),
    ("Asia 00-06 -> break 06-14, flat 20, buf .25atr", ORBCfg(0, 6, 14, 20, 1.0, 0.25)),
    ("Asia 00-07 -> break 07-15, flat 21", ORBCfg(0, 7, 15, 21, 1.0, 0.0)),
    ("Asia 22-06 -> break 06-14, flat 20", ORBCfg(0, 6, 14, 20, 0.5, 0.0)),
    ("Lon 07-12 -> break 12-17, flat 21", ORBCfg(7, 12, 17, 21, 1.0, 0.0)),
    ("Asia 00-08 -> break 08-16, flat 21", ORBCfg(0, 8, 16, 21, 1.0, 0.0)),
]
for sym, df in (("XAUUSD", GOLD1), ("GBPUSD", GBP1)):
    print(f"\n  {sym}")
    for lbl, cfg in cfgs:
        s = orb(df, sym, cfg)
        if len(s) < 80:
            continue
        if check(lbl, s, sym):
            passes.append((f"ORB {sym} {lbl}", s))
        # and the fade of the same breakout
        if check("FADE " + lbl, -s - 2 * COST[sym] / df["close"].mean(), sym):
            passes.append((f"ORB-FADE {sym} {lbl}", -s))

print("\n" + "=" * 124)
print("PART 3 — TICK-VOLUME CONFIRMATION: does a high-participation 1H move continue?")
print("=" * 124)
for sym, df in (("XAUUSD", GOLD1), ("GBPUSD", GBP1)):
    c = COST[sym]
    o, h, l, cl = (df[x].values for x in ("open", "high", "low", "close"))
    a, rv = df["atr"].values, df["relvol"].values
    idx = df.index
    print(f"\n  {sym}")
    for vthr in (1.5, 2.0, 3.0):
        for mthr in (0.75, 1.25):
            for H in (4, 12, 24):
                rows, ts = [], []
                for i in range(200, len(cl) - H - 1):
                    if not np.isfinite(rv[i]) or rv[i] < vthr or a[i] <= 0:
                        continue
                    mv = cl[i] - o[i]
                    if abs(mv) < mthr * a[i]:
                        continue
                    sgn = np.sign(mv)
                    r = sgn * (cl[i + H] - o[i + 1]) / o[i + 1] - c / o[i + 1]
                    rows.append(r)
                    ts.append(idx[i])
                if len(rows) < 100:
                    continue
                s = pd.Series(rows, index=pd.DatetimeIndex(ts))
                if check(f"follow vol>{vthr}x move>{mthr}atr hold {H}h", s, sym):
                    passes.append((f"VOL {sym} {vthr}/{mthr}/{H}", s))
                if check(f"fade   vol>{vthr}x move>{mthr}atr hold {H}h", -s - 2 * c / o[200], sym):
                    passes.append((f"VOLFADE {sym} {vthr}/{mthr}/{H}", -s))

print("\n" + "=" * 124)
print("PART 4 — DAY OF WEEK, gold")
print("=" * 124)
dg = GOLD1.resample("1D").agg({"open": "first", "close": "last"}).dropna()
for dow, nm in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
    m = dg.index.dayofweek == dow
    if m.sum() < 100:
        continue
    gross = np.log(dg["close"][m] / dg["open"][m])
    for sgn, lbl in ((1, "long "), (-1, "short")):
        r = sgn * gross - COST["XAUUSD"] / dg["open"][m]
        if check(f"{lbl}gold {nm} (open->close)", r, "XAUUSD", min_n=80):
            passes.append((f"DOW gold {nm} {lbl}", r))

print("\n" + "=" * 124)
print(f"TOTAL HYPOTHESES: {N_TESTS}   PASSES (DEV & OOS both t>1.0 and positive): {len(passes)}")
print("=" * 124)
for nm, s in passes:
    d = s[s.index <= DEV_END]
    o = s[s.index > DEV_END]
    print(f"  {nm:46s} DEV {d.mean()*1e4:+8.2f}bp n{len(d):5d} | OOS {o.mean()*1e4:+8.2f}bp n{len(o):5d} "
          f"| ALL {s.mean()*1e4:+8.2f}bp n{len(s):5d}")
if not passes:
    print("  none.")
print(f"\n  With {N_TESTS} tests, ~{N_TESTS*0.05:.0f} would clear a 5% bar by luck; requiring BOTH")
print("  halves to independently clear t>1.0 makes a chance pass much less likely but not impossible.")
