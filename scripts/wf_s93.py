#!/usr/bin/env python3
"""Anchored walk-forward for strategy_93 (range fade) across all FX pairs.
Optimize a small grid on past data, trade the next unseen segment, stitch OOS.
Set BT_COST_MULT to test cost sensitivity (1.0 = ~1.2pip retail, 0.4 = ~0.5pip raw)."""
import glob, os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import load_csv, enrich_trades_pnl
from bt_metrics import compute_metrics
from strategy_93_mtf_range_fade import generate_trades

GRID = [{"stretch": s, "er_max": e, "min_stop_atr": m, "target_mode": "mean"}
        for s in (1.5, 2.0) for e in (0.30, 0.40) for m in (1.5, 3.0)]
DEFAULT = {"stretch": 1.5, "er_max": 0.35, "min_stop_atr": 3.0, "target_mode": "mean"}
WARMUP = 450
MIN_IS = 25


def _ts(t): return int(datetime.fromisoformat(t["entry_time"].replace("Z", "+00:00")).timestamp())
def metrics(t): enrich_trades_pnl(t); return compute_metrics(t)


def in_range(candles, lo, hi, p):
    start = max(0, lo - WARMUP); seg = candles[start:hi]
    if len(seg) < WARMUP + 20: return []
    a, b = candles[lo].timestamp, candles[hi - 1].timestamp
    return [t for t in generate_trades(seg, **p) if a <= _ts(t) <= b]


def wf(candles, segs=5):
    n = len(candles); bd = [int(n * k / segs) for k in range(segs + 1)]; oos = []
    for k in range(1, segs):
        best, sc = None, -1e18
        for p in GRID:
            m = metrics(in_range(candles, 0, bd[k], p))
            if m["closed_trades"] < MIN_IS: continue
            if m["total_R"] > sc: sc, best = m["total_R"], p
        best = best or DEFAULT
        oos.extend(in_range(candles, bd[k], bd[k + 1], best))
    return metrics(oos)


def main():
    files = [f for f in sorted(glob.glob("data/*/1h/*_1h.csv")) if len(f.split("/")[1]) == 6]
    rows = []
    print(f"cost_mult={os.environ.get('BT_COST_MULT','1.0')}")
    print(f"{'pair':>8} | {'WALK-FWD OOS':^36}")
    for f in files:
        sym = f.split("/")[1]; c = load_csv(f)
        if len(c) < 3000: continue
        m = wf(c); rows.append((sym, m))
        print(f"{sym:>8} | trd={m['closed_trades']:>4} win={m['win_rate']:>5} "
              f"expR={m['expectancy_R']:>7} totR={m['total_R']:>7} PF={m['profit_factor']}", flush=True)
    pos = sum(1 for _, m in rows if (m['total_R'] or 0) > 0)
    tot = sum((m['total_R'] or 0) for _, m in rows)
    n = sum(m['closed_trades'] for _, m in rows)
    e = sum((m['expectancy_R'] or 0) * m['closed_trades'] for _, m in rows)
    print(f"\nOOS net-positive {pos}/{len(rows)} pairs | summed {tot:.1f}R | pooled expectancy {e/max(1,n):.4f} R/trade ({n} trades)")


if __name__ == "__main__":
    main()
