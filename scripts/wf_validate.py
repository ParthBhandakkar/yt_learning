#!/usr/bin/env python3
"""
Walk-forward + multi-fold validation for strategy_90.

Two honest tests:
  1. FIXED-PARAM consistency: run default params over K sequential segments.
     Tests whether the edge persists across time without any tuning.
  2. ANCHORED WALK-FORWARD: for each test segment, grid-search params on ALL
     prior (in-sample) data, then apply the single best combo to the unseen
     test segment. Concatenate the test-segment trades into one out-of-sample
     (OOS) equity curve. This is the curve you could actually have traded.

No lookahead: indicators get a warmup prefix before each segment, and only
trades ENTERED inside the segment are counted.

Usage:
  python wf_validate.py data/XAUUSD/1d/XAUUSD_1d.csv [--long-only] [--segments 5]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import load_csv, to_iso, enrich_trades_pnl
from bt_metrics import compute_metrics
from strategy_90_trend_breakout_atr import generate_trades

# Small grid (kept deliberately small to limit overfitting)
GRID = [
    {"donchian": d, "trend_len": t, "atr_mult_trail": x}
    for d in (10, 20, 40)
    for t in (50, 100)
    for x in (2.5, 3.5)
]
WARMUP = 120  # bars of indicator warmup before a segment


def _entry_ts(trade) -> int:
    return int(datetime.fromisoformat(trade["entry_time"].replace("Z", "+00:00")).timestamp())


def trades_in_range(candles, lo, hi, params, long_only):
    """Trades entered in candle index [lo, hi), with WARMUP prefix for indicators."""
    start = max(0, lo - WARMUP)
    seg = candles[start:hi]
    if len(seg) < WARMUP + 10:
        return []
    lo_ts = candles[lo].timestamp
    hi_ts = candles[hi - 1].timestamp
    out = []
    for t in generate_trades(seg, long_only=long_only, **params):
        ts = _entry_ts(t)
        if lo_ts <= ts <= hi_ts:
            out.append(t)
    return out


def metrics_of(trades):
    enrich_trades_pnl(trades)
    return compute_metrics(trades)


def fmt(m):
    return (f"trades={m['closed_trades']:>4} win%={m['win_rate']:>5} "
            f"expR={m['expectancy_R']:>7} totalR={m['total_R']:>8} "
            f"PF={str(m['profit_factor']):>6} ddR={m['max_drawdown_R']:>6}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--segments", type=int, default=5)
    args = ap.parse_args()

    candles = load_csv(args.csv)
    n = len(candles)
    K = args.segments
    bounds = [int(n * k / K) for k in range(K + 1)]
    label = os.path.basename(args.csv)
    print(f"\n##### {label}  ({n} bars, {K} segments, long_only={args.long_only}) #####")

    # --- Test 1: fixed default params per segment ---
    print("\n[1] FIXED DEFAULT PARAMS per segment (donchian20/ema50/trail3.0):")
    for k in range(K):
        lo, hi = bounds[k], bounds[k + 1]
        seg_dates = f"{to_iso(candles[lo].timestamp)[:10]}..{to_iso(candles[hi-1].timestamp)[:10]}"
        m = metrics_of(trades_in_range(candles, lo, hi, {}, args.long_only))
        print(f"  seg{k+1} {seg_dates}: {fmt(m)}")

    # --- Test 2: anchored walk-forward ---
    print("\n[2] ANCHORED WALK-FORWARD (optimize on all prior data, trade next segment):")
    oos_trades = []
    for k in range(1, K):
        is_lo, is_hi = 0, bounds[k]
        oos_lo, oos_hi = bounds[k], bounds[k + 1]
        # optimize on in-sample
        best, best_score = None, -1e18
        for params in GRID:
            m = metrics_of(trades_in_range(candles, is_lo, is_hi, params, args.long_only))
            if m["closed_trades"] < 20:
                continue
            score = m["total_R"]
            if score > best_score:
                best_score, best = score, params
        if best is None:
            best = {"donchian": 20, "trend_len": 50, "atr_mult_trail": 3.0}
        seg_t = trades_in_range(candles, oos_lo, oos_hi, best, args.long_only)
        m = metrics_of(list(seg_t))
        oos_trades.extend(seg_t)
        seg_dates = f"{to_iso(candles[oos_lo].timestamp)[:10]}..{to_iso(candles[oos_hi-1].timestamp)[:10]}"
        print(f"  OOS seg{k+1} {seg_dates} best={best}: {fmt(m)}")

    m = metrics_of(oos_trades)
    print(f"\n  >>> STITCHED OUT-OF-SAMPLE: {fmt(m)}")


if __name__ == "__main__":
    main()
