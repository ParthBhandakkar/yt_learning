#!/usr/bin/env python3
"""Unbiased multi-pair walk-forward for strategy_92 (mean reversion). See wf_s91."""
import argparse, bisect, glob, os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import load_csv, enrich_trades_pnl
from bt_metrics import compute_metrics
from strategy_92_mtf_mean_reversion import generate_trades

GRID = [{"htf_ema": e, "stretch": s, "min_rr": r}
        for e in (30, 50) for s in (1.0, 1.5, 2.0) for r in (1.0, 1.5, 2.0)]
DEFAULT = {"htf_ema": 50, "stretch": 1.5, "min_rr": 1.0}
WARMUP = 450
MIN_IS_TRADES = 30


def _ts(t): return int(datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00")).timestamp())


def trades_in_range(candles, lo, hi, p):
    start = max(0, lo - WARMUP); seg = candles[start:hi]
    if len(seg) < WARMUP + 20: return []
    lo_ts, hi_ts = candles[lo].timestamp, candles[hi - 1].timestamp
    return [t for t in generate_trades(seg, **p) if lo_ts <= _ts(t) <= hi_ts]


def metrics_of(t): enrich_trades_pnl(t); return compute_metrics(t)


def walk_forward(candles, segments):
    n = len(candles); b = [int(n * k / segments) for k in range(segments + 1)]; oos = []
    for k in range(1, segments):
        best, sc = None, -1e18
        for p in GRID:
            m = metrics_of(trades_in_range(candles, 0, b[k], p))
            if m["closed_trades"] < MIN_IS_TRADES: continue
            if m["total_R"] > sc: sc, best = m["total_R"], p
        best = best or DEFAULT
        oos.extend(trades_in_range(candles, b[k], b[k + 1], best))
    return metrics_of(oos)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--segments", type=int, default=5)
    args = ap.parse_args()
    files = [f for f in sorted(glob.glob("data/*/1h/*_1h.csv")) if len(f.split("/")[1]) == 6]
    print(f"{'pair':>8} | {'FULL default':^30} | {'WALK-FWD OOS':^30}")
    rows = []
    for f in files:
        sym = f.split("/")[1]; candles = load_csv(f)
        if len(candles) < 2000: continue
        full = metrics_of(trades_in_range(candles, 0, len(candles), DEFAULT))
        wf = walk_forward(candles, args.segments); rows.append((sym, full, wf))
        print(f"{sym:>8} | trd={full['closed_trades']:>4} win={full['win_rate']:>4} totR={full['total_R']:>7} PF={full['profit_factor']} "
              f"| trd={wf['closed_trades']:>4} win={wf['win_rate']:>4} totR={wf['total_R']:>7} PF={wf['profit_factor']}", flush=True)
    pos = sum(1 for _, _, w in rows if (w['total_R'] or 0) > 0)
    tw = sum((w['total_R'] or 0) for _, _, w in rows)
    n = sum(w['closed_trades'] for _, _, w in rows)
    e = sum((w['expectancy_R'] or 0) * w['closed_trades'] for _, _, w in rows)
    print(f"\nOOS net-positive: {pos}/{len(rows)} | summed OOS totR {tw:.1f} | pooled OOS expectancy {e/max(1,n):.4f} R over {n} trades")


if __name__ == "__main__":
    main()
