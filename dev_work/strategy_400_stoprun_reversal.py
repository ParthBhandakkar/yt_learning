#!/usr/bin/env python3
"""
Strategy 400: Stop-Run Reversal — clean-room implementation, all instruments

WRITTEN FROM SCRATCH. No function is imported from any existing strategy file in
this repository; only the OHLC loader and my own explicit cost table are used.

WHY THIS CONCEPT AND NOTHING ELSE
---------------------------------
Across ~390 tested hypotheses on this dataset, exactly one thing produced
market-NEUTRAL alpha rather than disguised beta:

  candidate                        beta vs gold   R^2     alpha t   in gold's DD
  continuous multi-signal trend    +0.54 (t 28.9) 0.34    +1.51     -52.6%
  stop-run reversal                -0.03 (t -0.5) 0.000   +2.23     +84.9%

The trend system was levered gold exposure - vol-targeted buy-and-hold beat it.
The stop-run reversal is uncorrelated with gold (R^2 = 0.000) and made money
during the 705 days gold spent in drawdown. That is the signature of an edge
rather than a risk premium.

THE MECHANISM
-------------
Resting stop orders cluster just beyond obvious swing highs and lows. Price is
drawn there, triggers them, and if there is no genuine supply/demand behind the
move it snaps back. The tradeable event is not the break - it is the FAILURE of
the break, confirmed by the close reclaiming the level.

  bias    : EMA(50) of the 4x-higher timeframe, completed bars only
  trigger : bar's low pierces the last confirmed swing low, the CLOSE comes back
            above that level, the bar closes up, and the sweep happened in the
            lower half of the recent range (discount). Mirror for shorts.
  stop    : just beyond the sweep extreme, floored at max(1.5 x ATR,
            4 x round-turn cost) so the stop is never inside the noise/cost band
  exit    : chandelier trail, extreme since entry -/+ 3.0 x ATR, advanced ONLY
            on closed bars
  one position per instrument at a time; entry at the next bar's open

CAUSALITY
---------
- Swing points require the following bar to confirm, so a swing at index k is
  only usable from index k+2 onward.
- The higher-timeframe EMA at bar i only uses HTF bars whose close time is <= the
  close time of bar i.
- Signal read at the close of bar i, filled at the open of bar i+1.
- The trail is advanced with bar j's extreme and ATR only AFTER bar j has been
  tested against the trail value carried in from bar j-1.

Usage:
    python strategy_400_stoprun_reversal.py            # all instruments
    python strategy_400_stoprun_reversal.py --verbose
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))

# my own round-turn cost table, in PRICE units (Exness Standard, spread + slippage)
COST_PRICE = {"EURUSD": 0.00012, "GBPUSD": 0.00012, "AUDUSD": 0.00012,
              "NZDUSD": 0.00012, "USDCHF": 0.00015, "USDCAD": 0.00015,
              "USDJPY": 0.030, "XAUUSD": 0.40}

# ---- FROZEN parameters (mirrored from the validated gold configuration) -----
HTF_MULT = 4            # higher timeframe = 4 x the trading timeframe
HTF_EMA = 50
RANGE_N = 20            # bars for the premium/discount midpoint
ATR_N = 14
K_STOP_ATR = 1.5        # ATR floor on the initial stop
K_TRAIL_ATR = 3.0       # chandelier trail width
SWEEP_BUF = 0.0003      # place the stop this fraction beyond the sweep extreme
MIN_COST_MULT = 4.0     # never risk less than 4x the round-turn cost


def load_ohlc(path: str) -> pd.DataFrame:
    d = pd.read_csv(path)
    idx = pd.to_datetime(d["time"], unit="s", utc=True)
    df = pd.DataFrame({"open": d["open"].values, "high": d["high"].values,
                       "low": d["low"].values, "close": d["close"].values}, index=idx)
    return df[~df.index.duplicated(keep="first")].sort_index()


def wilder_atr(h, l, c, n):
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.empty(len(tr))
    out[0] = tr[0]
    k = 1.0 / n
    for i in range(1, len(tr)):
        out[i] = tr[i] * k + out[i - 1] * (1 - k)
    return out


def htf_bias(df: pd.DataFrame, mult: int, ema_n: int) -> np.ndarray:
    """+1/-1/0 from the EMA of the higher timeframe, using only HTF bars that had
    already CLOSED by the close of the current lower-timeframe bar."""
    step = int((df.index[1] - df.index[0]).total_seconds())
    htf = df.resample(f"{step*mult}s").agg({"open": "first", "high": "max",
                                            "low": "min", "close": "last"}).dropna()
    if len(htf) < ema_n + 2:
        return np.zeros(len(df), dtype=np.int8)
    ema = htf["close"].ewm(span=ema_n, adjust=False).mean()
    sign = np.sign(htf["close"] - ema).astype(np.int8)
    htf_close_time = htf.index + pd.Timedelta(seconds=step * mult)   # when it became known
    bias = np.zeros(len(df), dtype=np.int8)
    lt_close = df.index + pd.Timedelta(seconds=step)
    pos = np.searchsorted(htf_close_time.values, lt_close.values, side="right") - 1
    ok = pos >= 0
    bias[ok] = sign.values[pos[ok]]
    return bias


def swing_flags(h: np.ndarray, l: np.ndarray):
    """Confirmed swing highs/lows. Index k is a swing only once bar k+1 exists."""
    n = len(h)
    sh = np.zeros(n, dtype=bool)
    sl = np.zeros(n, dtype=bool)
    sh[1:n - 1] = (h[1:n - 1] > h[0:n - 2]) & (h[1:n - 1] > h[2:n])
    sl[1:n - 1] = (l[1:n - 1] < l[0:n - 2]) & (l[1:n - 1] < l[2:n])
    return sh, sl


def generate_trades(df: pd.DataFrame, symbol: str, *, cost_mult=1.0,
                    k_stop=K_STOP_ATR, k_trail=K_TRAIL_ATR, range_n=RANGE_N,
                    htf_ema=HTF_EMA, use_pd=True) -> list[dict]:
    o, h, l, c = (df[x].values.astype(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    a = wilder_atr(h, l, c, ATR_N)
    bias = htf_bias(df, HTF_MULT, htf_ema)
    sh, sl = swing_flags(h, l)
    # last confirmed swing index at or before i-2
    last_sh = np.full(n, -1, dtype=int)
    last_sl = np.full(n, -1, dtype=int)
    cur_h = cur_l = -1
    for i in range(n):
        if i >= 2:
            if sh[i - 2]:
                cur_h = i - 2
            if sl[i - 2]:
                cur_l = i - 2
        last_sh[i] = cur_h
        last_sl[i] = cur_l

    rt_cost = COST_PRICE[symbol] * cost_mult
    warm = max(htf_ema * HTF_MULT, range_n, ATR_N) + 5
    trades: list[dict] = []
    i = warm
    while i < n - 1:
        b = bias[i]
        if b == 0 or a[i] <= 0:
            i += 1
            continue
        rng_hi = h[i - range_n:i].max()
        rng_lo = l[i - range_n:i].min()
        mid = (rng_hi + rng_lo) / 2.0

        direction = None
        sweep = None
        if b == 1 and last_sl[i] >= 0:
            lvl = l[last_sl[i]]
            if l[i] < lvl and c[i] > lvl and c[i] > o[i] and ((not use_pd) or l[i] <= mid):
                direction, sweep = 1, l[i]
        elif b == -1 and last_sh[i] >= 0:
            lvl = h[last_sh[i]]
            if h[i] > lvl and c[i] < lvl and c[i] < o[i] and ((not use_pd) or h[i] >= mid):
                direction, sweep = -1, h[i]
        if direction is None:
            i += 1
            continue

        ei = i + 1
        entry = o[ei]
        # Risk floor: never let the stop sit inside the noise band or the cost
        # band. The 0.5*ATR term is the important half - without it the stop can
        # be placed a few cents beyond a sweep and gets picked off immediately.
        min_risk = max(MIN_COST_MULT * rt_cost, 0.5 * a[i])
        if direction == 1:
            structural = sweep * (1.0 - SWEEP_BUF)
            stop = min(structural, entry - k_stop * a[i])
            risk = entry - stop
            if risk < min_risk:
                risk = min_risk
                stop = entry - risk
        else:
            structural = sweep * (1.0 + SWEEP_BUF)
            stop = max(structural, entry + k_stop * a[i])
            risk = stop - entry
            if risk < min_risk:
                risk = min_risk
                stop = entry + risk
        if risk <= 0:
            i += 1
            continue

        trail = stop
        extreme = entry
        xi = xp = None
        for j in range(ei, n):
            if direction == 1:
                if l[j] <= trail:                       # tested BEFORE tightening
                    xi, xp = j, trail
                    break
                extreme = max(extreme, h[j])
                trail = max(trail, extreme - k_trail * a[j])
            else:
                if h[j] >= trail:
                    xi, xp = j, trail
                    break
                extreme = min(extreme, l[j])
                trail = min(trail, extreme + k_trail * a[j])
        if xi is None:
            xi, xp = n - 1, c[n - 1]

        gross = (xp - entry) / risk if direction == 1 else (entry - xp) / risk
        trades.append(dict(
            symbol=symbol, direction="long" if direction == 1 else "short",
            entry_time=df.index[ei], exit_time=df.index[xi],
            entry_price=float(entry), stop_loss=float(stop), exit_price=float(xp),
            risk_price=float(risk), held_bars=int(xi - ei + 1),
            gross_R=float(gross), cost_R=float(rt_cost / risk),
            pnl_R=float(gross - rt_cost / risk),
        ))
        i = xi + 1
    return trades


def stats(trades, rng=None):
    v = np.array([t["pnl_R"] for t in trades], float)
    if len(v) < 8:
        return None
    sd = v.std(ddof=1)
    w, l = v[v > 0], v[v <= 0]
    eq = np.cumsum(v)
    dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
    d = dict(n=len(v), win=(v > 0).mean(), exp=v.mean(), total=v.sum(),
             t=v.mean() / (sd / np.sqrt(len(v))) if sd > 0 else 0.0,
             rr=(w.mean() / abs(l.mean())) if len(w) and len(l) else np.nan, dd=dd)
    if rng is not None:
        d["p"] = float((rng.choice(v, size=(20000, len(v)), replace=True).mean(axis=1) <= 0).mean())
    return d


DATASETS = [
    ("XAUUSD", "1H", os.path.join("data", "XAUUSD", "1H", "XAUUSD_1h_2021-03-02_2026-07-01.csv")),
    ("XAUUSD", "4H", os.path.join("data", "XAUUSD", "4h", "XAUUSD_4h.csv")),
    ("GBPUSD", "1H", os.path.join("data", "GBPUSD", "1h", "GBPUSD_1h.csv")),
    ("GBPUSD", "4H", os.path.join("data", "GBPUSD", "4h", "GBPUSD_4h.csv")),
    ("EURUSD", "4H", os.path.join("data", "EURUSD", "4h", "EURUSD_4h.csv")),
    ("USDJPY", "4H", os.path.join("data", "USDJPY", "4h", "USDJPY_4h.csv")),
    ("USDCHF", "4H", os.path.join("data", "USDCHF", "4h", "USDCHF_4h.csv")),
    ("AUDUSD", "4H", os.path.join("data", "AUDUSD", "4h", "AUDUSD_4h.csv")),
    ("NZDUSD", "4H", os.path.join("data", "NZDUSD", "4h", "NZDUSD_4h.csv")),
    ("USDCAD", "4H", os.path.join("data", "USDCAD", "4h", "USDCAD_4h.csv")),
]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    rng = np.random.default_rng(5)
    SPLIT = pd.Timestamp("2024-03-01", tz="UTC")

    print("=" * 126)
    print("STRATEGY 400 — stop-run reversal, clean-room build, every instrument and timeframe available")
    print("=" * 126)
    print(f"  {'instrument':12s} {'n':>5s} {'win%':>6s} {'RR':>5s} {'exp R':>8s} {'total':>8s} "
          f"{'t':>6s} {'DD_R':>7s} {'P(<=0)':>7s} {'cost/R':>7s}")
    all_fx = []
    for sym, tf, rel in DATASETS:
        df = load_ohlc(os.path.join(THIS, rel))
        tr = generate_trades(df, sym)
        s = stats(tr, rng)
        if not s:
            print(f"  {sym+' '+tf:12s} too few trades")
            continue
        cr = np.mean([t["cost_R"] for t in tr])
        print(f"  {sym+' '+tf:12s} {s['n']:5d} {s['win']*100:5.1f}% {s['rr']:5.2f} "
              f"{s['exp']:+8.3f} {s['total']:+8.1f} {s['t']:+6.2f} {s['dd']:7.1f} "
              f"{s['p']*100:6.2f}% {cr:7.4f}")
        if sym != "XAUUSD" and tf == "4H":
            all_fx.extend(tr)

    s = stats(all_fx, rng)
    print(f"  {'-'*116}")
    print(f"  {'FX 4H pooled':12s} {s['n']:5d} {s['win']*100:5.1f}% {s['rr']:5.2f} "
          f"{s['exp']:+8.3f} {s['total']:+8.1f} {s['t']:+6.2f} {s['dd']:7.1f} {s['p']*100:6.2f}%")

    print("\n  time split and direction split, gold 1H (the validated configuration):")
    tr = generate_trades(load_ohlc(os.path.join(THIS, DATASETS[0][2])), "XAUUSD")
    for lbl, sel in (("2021-03..2024-03", lambda t: t["entry_time"] <= SPLIT),
                     ("2024-03..2026-07", lambda t: t["entry_time"] > SPLIT),
                     ("long only", lambda t: t["direction"] == "long"),
                     ("short only", lambda t: t["direction"] == "short")):
        ss = stats([t for t in tr if sel(t)], rng)
        if ss:
            print(f"    {lbl:20s} n={ss['n']:4d} exp={ss['exp']:+.3f}R t={ss['t']:+5.2f} "
                  f"RR={ss['rr']:.2f} P(<=0)={ss['p']*100:5.2f}%")

    print("\n  cost stress, gold 1H:")
    for m in (1, 2, 3, 5):
        ss = stats(generate_trades(load_ohlc(os.path.join(THIS, DATASETS[0][2])),
                                   "XAUUSD", cost_mult=m))
        print(f"    cost x{m}: exp={ss['exp']:+.3f}R total={ss['total']:+7.1f}R t={ss['t']:+5.2f}")

    print("\n  parameter sensitivity, gold 1H (one axis at a time):")
    for nm, kw, vals in (("k_trail", "k_trail", [2.0, 3.0, 4.0, 5.0]),
                         ("k_stop", "k_stop", [1.0, 1.5, 2.0, 2.5]),
                         ("range_n", "range_n", [10, 20, 30, 40]),
                         ("htf_ema", "htf_ema", [20, 50, 100, 200])):
        parts = []
        for v in vals:
            ss = stats(generate_trades(load_ohlc(os.path.join(THIS, DATASETS[0][2])),
                                       "XAUUSD", **{kw: v}))
            parts.append(f"{v}:{ss['exp']:+.2f}(t{ss['t']:+.1f})" if ss else f"{v}:n/a")
        print(f"    {nm:8s} " + "  ".join(parts))
