#!/usr/bin/env python3
"""
Unbiased multi-pair walk-forward for strategy_91.

For every forex pair (1H data):
  - Anchored walk-forward: optimize a small param grid on ALL prior data, then
    trade the next unseen segment; stitch the test segments into one OOS curve.
  - Also report full-sample with default params.
Then aggregate across all pairs. No pair-specific hand tuning; the grid is the
same for everyone and selection uses only past data.

Usage: python wf_s91.py [--segments 5]
"""
import argparse
import bisect
import glob
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import load_csv, enrich_trades_pnl
from bt_metrics import compute_metrics
from strategy_91_mtf_liquidity_reversion import generate_trades

GRID = [
    {"htf_ema": e, "rr": r, "range_lookback": rl, "use_pd_filter": pd}
    for e in (30, 50)
    for r in (1.5, 2.0, 3.0)
    for rl in (20, 50)
    for pd in (True, False)
]
DEFAULT = {"htf_ema": 50, "rr": 2.0, "range_lookback": 20, "use_pd_filter": True}
WARMUP = 450   # 1H bars (>= 4H EMA50 warmup + range lookback)
MIN_IS_TRADES = 30


def _ts(t):
    return int(datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00")).timestamp())


def trades_in_range(candles, lo, hi, params):
    start = max(0, lo - WARMUP)
    seg = candles[start:hi]
    if len(seg) < WARMUP + 20:
        return []
    lo_ts, hi_ts = candles[lo].timestamp, candles[hi - 1].timestamp
    return [t for t in generate_trades(seg, **params) if lo_ts <= _ts(t) <= hi_ts]


def metrics_of(trades):
    enrich_trades_pnl(trades)
    return compute_metrics(trades)


def walk_forward(candles, segments):
    n = len(candles)
    b = [int(n * k / segments) for k in range(segments + 1)]
    oos = []
    for k in range(1, segments):
        best, best_score = None, -1e18
        for p in GRID:
            m = metrics_of(trades_in_range(candles, 0, b[k], p))
            if m["closed_trades"] < MIN_IS_TRADES:
                continue
            if m["total_R"] > best_score:
                best_score, best = m["total_R"], p
        if best is None:
            best = DEFAULT
        oos.extend(trades_in_range(candles, b[k], b[k + 1], best))
    return metrics_of(oos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", type=int, default=5)
    args = ap.parse_args()
    files = sorted(glob.glob("data/*/1h/*_1h.csv"))
    # forex only (skip metals/crypto which aren't 6-letter FX)
    files = [f for f in files if len(f.split("/")[1]) == 6 and "USD" in f.split("/")[1] or
             f.split("/")[1] in ("EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPAUD","GBPCHF","GBPJPY")]
    print(f"{'pair':>8} | {'FULL default':^34} | {'WALK-FWD OOS':^34}")
    print(f"{'':>8} | {'trd':>5} {'win%':>5} {'expR':>7} {'totR':>7} {'PF':>5} | "
          f"{'trd':>5} {'win%':>5} {'expR':>7} {'totR':>7} {'PF':>5}")
    rows = []
    for f in files:
        sym = f.split("/")[1]
        candles = load_csv(f)
        if len(candles) < 2000:
            print(f"{sym:>8} | only {len(candles)} bars, skipped"); continue
        full = metrics_of(trades_in_range(candles, 0, len(candles), DEFAULT))
        wf = walk_forward(candles, args.segments)
        rows.append((sym, full, wf))
        print(f"{sym:>8} | {full['closed_trades']:>5} {full['win_rate']:>5} {full['expectancy_R']:>7} "
              f"{full['total_R']:>7} {str(full['profit_factor']):>5} | "
              f"{wf['closed_trades']:>5} {wf['win_rate']:>5} {wf['expectancy_R']:>7} "
              f"{wf['total_R']:>7} {str(wf['profit_factor']):>5}", flush=True)
    pos_full = sum(1 for _, fl, _ in rows if (fl['total_R'] or 0) > 0)
    pos_wf = sum(1 for _, _, w in rows if (w['total_R'] or 0) > 0)
    tot_full = sum((fl['total_R'] or 0) for _, fl, _ in rows)
    tot_wf = sum((w['total_R'] or 0) for _, _, w in rows)
    exp_wf = sum((w['expectancy_R'] or 0) * w['closed_trades'] for _, _, w in rows)
    n_wf = sum(w['closed_trades'] for _, _, w in rows)
    print(f"\nNet-positive pairs: full {pos_full}/{len(rows)} | walk-fwd OOS {pos_wf}/{len(rows)}")
    print(f"Summed total_R: full {tot_full:.1f} | OOS {tot_wf:.1f}")
    print(f"Pooled OOS expectancy: {exp_wf/max(1,n_wf):.4f} R/trade over {n_wf} trades")


if __name__ == "__main__":
    main()
