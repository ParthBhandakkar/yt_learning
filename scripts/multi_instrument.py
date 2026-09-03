#!/usr/bin/env python3
"""
Run strategy_90 across every instrument's DAILY data.
Reports full-sample (default params) and anchored walk-forward OOS side by side.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import load_csv
from wf_validate import trades_in_range, metrics_of, GRID


def walk_forward(candles, segments=5, long_only=False):
    n = len(candles)
    bounds = [int(n * k / segments) for k in range(segments + 1)]
    oos = []
    for k in range(1, segments):
        best, best_score = None, -1e18
        for params in GRID:
            m = metrics_of(trades_in_range(candles, 0, bounds[k], params, long_only))
            if m["closed_trades"] < 20:
                continue
            if m["total_R"] > best_score:
                best_score, best = m["total_R"], params
        if best is None:
            best = {"donchian": 20, "trend_len": 50, "atr_mult_trail": 3.0}
        oos.extend(trades_in_range(candles, bounds[k], bounds[k + 1], best, long_only))
    return metrics_of(oos)


def main():
    long_only = "--long-only" in sys.argv
    files = sorted(glob.glob("data/*/1d/*_1d*.csv"))
    print(f"{'symbol':>8} | {'FULL-SAMPLE (default params)':^46} | {'WALK-FWD OOS':^40}")
    print(f"{'':>8} | {'trades':>6} {'win%':>5} {'expR':>7} {'totR':>7} {'PF':>5} {'ddR':>5} | "
          f"{'trades':>6} {'win%':>5} {'expR':>7} {'totR':>7} {'PF':>5}")
    rows = []
    for f in files:
        sym = f.split("/")[1]
        candles = load_csv(f)
        if len(candles) < 200:
            print(f"{sym:>8} | (only {len(candles)} bars, skipped)")
            continue
        full = metrics_of(trades_in_range(candles, 0, len(candles), {}, long_only))
        wf = walk_forward(candles, long_only=long_only)
        rows.append((sym, full, wf))
        print(f"{sym:>8} | {full['closed_trades']:>6} {full['win_rate']:>5} {full['expectancy_R']:>7} "
              f"{full['total_R']:>7} {str(full['profit_factor']):>5} {full['max_drawdown_R']:>5} | "
              f"{wf['closed_trades']:>6} {wf['win_rate']:>5} {wf['expectancy_R']:>7} "
              f"{wf['total_R']:>7} {str(wf['profit_factor']):>5}")

    pos_full = sum(1 for _, f, _ in rows if (f['total_R'] or 0) > 0)
    pos_wf = sum(1 for _, _, w in rows if (w['total_R'] or 0) > 0)
    tot_full = sum((f['total_R'] or 0) for _, f, _ in rows)
    tot_wf = sum((w['total_R'] or 0) for _, _, w in rows)
    print(f"\nInstruments net-positive: full-sample {pos_full}/{len(rows)}, walk-forward OOS {pos_wf}/{len(rows)}")
    print(f"Summed total_R: full-sample {tot_full:.1f}R, walk-forward OOS {tot_wf:.1f}R "
          f"(long_only={long_only})")


if __name__ == "__main__":
    main()
