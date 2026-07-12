#!/usr/bin/env python3
"""Generic trend-following research engine (strictly causal, ATR-normalized).

Concept: time-series momentum via Donchian breakout, volatility-normalized so
every parameter is unitless and therefore transferable across instruments (the
structural defense against per-pair overfitting).

Causality rules (NO lookahead):
  - All indicators at bar i use only bars <= i.
  - Entry SIGNAL is evaluated on the CLOSE of bar i; the trade is entered at the
    OPEN of bar i+1.
  - After entry, stop/target/trailing are checked on subsequent bars. Within a
    bar the STOP is assumed hit before the target (conservative).
  - Trailing stop is updated using CLOSED bars only.

Everything is measured in R = initial risk = k_sl * ATR(at entry).
Costs: round-turn cost (price) / risk, deducted once per trade.

Validation: pooled across all instruments + leave-one-instrument-out.
"""
import glob, os, sys
import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import load_csv, round_turn_cost_price

PAIRS = {p: f"data/{p}" for p in
         ["GBPUSD", "AUDUSD", "EURUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY", "XAUUSD"]}


def load_tf(d, tf):
    # tf may be a raw file tf ("4h","1h") optionally with resample suffix ">1D"/">1W"
    base, _, rule = tf.partition(">")
    f = (glob.glob(os.path.join(d, base, "*.csv")) or glob.glob(os.path.join(d, f"*_{base}_*.csv"))
         or glob.glob(os.path.join(d, f"*_{base}.csv")))
    if not f:
        return None
    c = load_csv(sorted(f, key=len)[0])
    idx = pd.to_datetime([x.timestamp for x in c], unit="s", utc=True)
    df = pd.DataFrame({"open":[x.open for x in c],"high":[x.high for x in c],
                       "low":[x.low for x in c],"close":[x.close for x in c]}, index=idx)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    if rule:
        df = df.resample(rule, label="right", closed="right").agg(
            {"open":"first","high":"max","low":"min","close":"last"}).dropna()
    return df


def atr(df, n=14):
    h,l,c = df["high"].values, df["low"].values, df["close"].values
    pc = np.roll(c,1); pc[0]=c[0]
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    a = pd.Series(tr).ewm(alpha=1/n, adjust=False).mean().values
    return a


def backtest(df, entry_n=20, exit_n=10, k_sl=2.0, k_trail=3.0, use_trail=True,
             trend_ema=0, ref_price=None, cost_price=0.0):
    """Donchian breakout trend follower. Returns list of trade R (net of cost)."""
    o,h,l,c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    a = atr(df)
    n = len(df)
    # Donchian channels using PRIOR bars only (exclude current bar): shift by 1
    hh = pd.Series(h).rolling(entry_n).max().shift(1).values   # highest high of prior entry_n bars
    ll = pd.Series(l).rolling(entry_n).min().shift(1).values
    ex_hh = pd.Series(h).rolling(exit_n).max().shift(1).values
    ex_ll = pd.Series(l).rolling(exit_n).min().shift(1).values
    # long-term trend filter (EMA of close); causal (value at i uses <= i)
    if trend_ema and trend_ema > 0:
        ema = pd.Series(c).ewm(span=trend_ema, adjust=False).mean().values
    else:
        ema = None

    trades = []
    pos = 0            # 0 flat, 1 long, -1 short
    entry = stop = risk = 0.0
    best = 0.0
    i = max(entry_n, 20, trend_ema) + 1
    while i < n - 1:
        if pos == 0:
            # signal on close of bar i, enter at open of i+1
            long_sig = c[i] > hh[i] and not np.isnan(hh[i])
            short_sig = c[i] < ll[i] and not np.isnan(ll[i])
            if ema is not None:
                long_sig = long_sig and c[i] > ema[i]
                short_sig = short_sig and c[i] < ema[i]
            if long_sig or short_sig:
                pos = 1 if long_sig else -1
                entry = o[i+1]
                risk = k_sl * a[i]
                if risk <= 0:
                    pos = 0; i += 1; continue
                stop = entry - risk if pos==1 else entry + risk
                best = entry
                i += 1
                continue
        else:
            # manage open position on bar i
            is_long = pos == 1
            # 1) hard stop (conservative: before anything else)
            if (l[i] <= stop) if is_long else (h[i] >= stop):
                r = ((stop-entry)/risk if is_long else (entry-stop)/risk) - cost_price/risk
                trades.append(r); pos = 0; i += 1; continue
            # 2) update trailing using this (closed) bar
            if is_long:
                best = max(best, h[i])
                if use_trail:
                    stop = max(stop, best - k_trail*a[i])
            else:
                best = min(best, l[i])
                if use_trail:
                    stop = min(stop, best + k_trail*a[i])
            # 3) opposite Donchian exit signal on close -> exit next open
            exit_sig = (c[i] < ex_ll[i]) if is_long else (c[i] > ex_hh[i])
            if exit_sig and not np.isnan(ex_ll[i] if is_long else ex_hh[i]):
                px = o[i+1]
                r = ((px-entry)/risk if is_long else (entry-px)/risk) - cost_price/risk
                trades.append(r); pos = 0; i += 1; continue
        i += 1
    return trades


def backtest_mr(df, sma_n=50, z_entry=2.0, z_exit=0.5, k_sl=2.5, trend_ema=0,
                max_hold=48, cost_price=0.0, frac_lo=0.0, frac_hi=1.0):
    """Mean-reversion: fade price when stretched z_entry*ATR from SMA; exit on
    revert to z_exit or ATR stop or time. Optional trend filter: only fade
    WITH the higher-degree trend (buy dips in uptrend, sell rips in downtrend).
    Causal: signal on close of i, enter open i+1; stop intrabar (before target).
    """
    o,h,l,c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    a = atr(df); n = len(df)
    sma = pd.Series(c).rolling(sma_n).mean().values
    ema = pd.Series(c).ewm(span=trend_ema, adjust=False).mean().values if trend_ema else None
    trades = []; pos = 0; entry=stop=risk=0.0; held=0; tgt=0.0
    lo_i = max(int(n*frac_lo), max(sma_n, trend_ema or 0) + 1)
    hi_i = int(n*frac_hi)
    i = lo_i
    while i < n - 1:
        if pos == 0:
            if i >= hi_i:
                break
            if a[i] <= 0 or np.isnan(sma[i]):
                i += 1; continue
            z = (c[i] - sma[i]) / a[i]
            long_sig = z <= -z_entry
            short_sig = z >= z_entry
            if ema is not None:
                long_sig = long_sig and c[i] > ema[i]   # buy dips only in uptrend
                short_sig = short_sig and c[i] < ema[i]
            if long_sig or short_sig:
                pos = 1 if long_sig else -1
                entry = o[i+1]; risk = k_sl*a[i]
                stop = entry-risk if pos==1 else entry+risk
                tgt = sma[i]  # revert-to-mean target (dynamic, recomputed below)
                held = 0; i += 1; continue
        else:
            is_long = pos==1; held += 1
            if (l[i] <= stop) if is_long else (h[i] >= stop):
                trades.append((((stop-entry)/risk) if is_long else ((entry-stop)/risk)) - cost_price/risk)
                pos=0; i+=1; continue
            # target = revert to mean; use PRIOR-bar indicators (known at the
            # open of bar i) to avoid intrabar lookahead.
            zt = z_exit
            sma_ref = sma[i-1]; a_ref = a[i-1]
            if np.isnan(sma_ref) or a_ref <= 0:
                i += 1; continue
            tgt_px = sma_ref - zt*a_ref if is_long else sma_ref + zt*a_ref
            hit = (h[i] >= tgt_px) if is_long else (l[i] <= tgt_px)
            if hit:
                px = tgt_px
                trades.append((((px-entry)/risk) if is_long else ((entry-px)/risk)) - cost_price/risk)
                pos=0; i+=1; continue
            if held >= max_hold:
                px = o[i+1] if i+1 < n else c[i]
                trades.append((((px-entry)/risk) if is_long else ((entry-px)/risk)) - cost_price/risk)
                pos=0; i+=1; continue
        i += 1
    return trades


def evaluate_mr(cfg, tf, verbose=False):
    per = {}
    for p, d in PAIRS.items():
        df = load_tf(d, tf)
        if df is None or len(df) < 200:
            continue
        cost = round_turn_cost_price(float(df["close"].iloc[-1]))
        per[p] = backtest_mr(df, cost_price=cost, **cfg)
    pooled = [r for tr in per.values() for r in tr]
    if not pooled:
        return None
    pos_pairs = sum(1 for tr in per.values() if tr and np.mean(tr) > 0)
    line = (f"n={len(pooled):5d} exp={np.mean(pooled):+.3f}R win={np.mean([r>0 for r in pooled])*100:2.0f}% "
            f"pairs_pos={pos_pairs}/{len(per)} totalR={sum(pooled):+.0f}")
    if verbose:
        for p, tr in per.items():
            if tr:
                print(f"    {p}: n={len(tr):4d} exp={np.mean(tr):+.3f}R win={np.mean([r>0 for r in tr])*100:.0f}% total={sum(tr):+.1f}")
    return line, per


def evaluate(cfg, tf="4h", verbose=False):
    per = {}
    for p, d in PAIRS.items():
        df = load_tf(d, tf)
        if df is None or len(df) < 200:
            continue
        ref = float(df["close"].iloc[-1])
        cost = round_turn_cost_price(ref)
        tr = backtest(df, cost_price=cost, **cfg)
        per[p] = tr
    pooled = [r for tr in per.values() for r in tr]
    if not pooled:
        return None
    exp = np.mean(pooled)
    win = np.mean([1 for r in pooled if r>0]) if pooled else 0
    pos_pairs = sum(1 for tr in per.values() if tr and np.mean(tr) > 0)
    line = (f"n={len(pooled):4d} exp={exp:+.3f}R win={np.mean([r>0 for r in pooled])*100:2.0f}% "
            f"pairs_pos={pos_pairs}/{len(per)} totalR={sum(pooled):+.0f}")
    if verbose:
        for p, tr in per.items():
            if tr:
                print(f"    {p}: n={len(tr):3d} exp={np.mean(tr):+.3f}R total={sum(tr):+.1f}")
    return line, per


def oos_split(per):
    """Temporal split is not stored per-trade here; approximate by re-evaluating.
    Instead we report per-pair already. This returns pooled IS/OOS via order of
    trades is unavailable; handled in validate()."""
    pass


def validate(cfg, tf):
    """Per-pair breakdown + leave-one-pair-out pooled + temporal OOS."""
    # need timestamps for temporal split -> rerun with a timestamped backtest
    per = {}
    per_time = {}
    for p, d in PAIRS.items():
        df = load_tf(d, tf)
        if df is None or len(df) < 200:
            continue
        cost = round_turn_cost_price(float(df["close"].iloc[-1]))
        tr = backtest_mr(df, cost_price=cost, **cfg)
        per[p] = tr
    print(f"\nCONFIG {cfg} tf={tf}")
    print("  per-pair:")
    for p, tr in per.items():
        if tr:
            print(f"    {p}: n={len(tr):4d} exp={np.mean(tr):+.3f}R win={np.mean([r>0 for r in tr])*100:.0f}% total={sum(tr):+.1f}")
    pooled=[r for tr in per.values() for r in tr]
    print(f"  POOLED: n={len(pooled)} exp={np.mean(pooled):+.3f}R win={np.mean([r>0 for r in pooled])*100:.0f}% total={sum(pooled):+.0f}")
    # leave-one-pair-out: pooled expectancy excluding each pair
    print("  leave-one-out pooled exp (robustness):")
    worst = 1e9
    for excl in per:
        pl=[r for p,tr in per.items() if p!=excl for r in tr]
        e=np.mean(pl); worst=min(worst,e)
        print(f"    excl {excl}: exp={e:+.3f}R (n={len(pl)})")
    print(f"  worst-case LOO exp={worst:+.3f}R")


def temporal(cfg, tf):
    print(f"\nTEMPORAL OOS  cfg={cfg} tf={tf}")
    for label, lo, hi in [("IS(0-60%)",0.0,0.6),("OOS(60-100%)",0.6,1.0)]:
        per={}
        for p, d in PAIRS.items():
            df = load_tf(d, tf)
            if df is None or len(df) < 200:
                continue
            cost = round_turn_cost_price(float(df["close"].iloc[-1]))
            c2 = dict(cfg); c2.update(frac_lo=lo, frac_hi=hi)
            per[p] = backtest_mr(df, cost_price=cost, **c2)
        pooled=[r for tr in per.values() for r in tr]
        pos=sum(1 for tr in per.values() if tr and np.mean(tr)>0)
        print(f"  {label}: n={len(pooled):4d} exp={np.mean(pooled):+.3f}R "
              f"win={np.mean([r>0 for r in pooled])*100:.0f}% pairs_pos={pos}/{len(per)} total={sum(pooled):+.0f}")


def main():
    tf="4h"
    for z in [2.0, 2.5]:
        cfg = dict(sma_n=20, z_entry=z, z_exit=0.5, k_sl=2.5, trend_ema=200, max_hold=48)
        validate(cfg, tf)
        temporal(cfg, tf)


if __name__ == "__main__":
    main()
