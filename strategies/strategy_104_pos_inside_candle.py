#!/usr/bin/env python3
"""
Strategy 104: Power of Stocks — Inside Candle Breakout (mechanical proxy)

Source: Power of Stocks (Subhasish Pani) — inside candle teaching summaries
Video: https://www.youtube.com/watch?v=8cbKitkmxFc

SEPARATE from Golden Setup (s99) and 5EMA (s103).

  - Mother candle, then baby (inside) candle fully contained in mother range.
  - Entry: break of baby high (long) / baby low (short) on a later bar.
  - SL: opposite baby extreme; optional pyramid note omitted (single entry).
  - TP: 2R (conservative vs his discretionary holds).

Usage:
  python strategy_104_pos_inside_candle.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def generate_trades(candles, *, symbol: str = "", rr: float = 2.0):
    n = len(candles)
    if n < 30:
        return []
    ts = np.array([c.timestamp for c in candles], dtype=np.int64)
    o = np.array([c.open for c in candles], dtype=np.float64)
    h = np.array([c.high for c in candles], dtype=np.float64)
    l = np.array([c.low for c in candles], dtype=np.float64)
    c = np.array([c.close for c in candles], dtype=np.float64)

    trades = []
    i = 2
    while i < n - 2:
        # Baby at i, mother at i-1
        if h[i] < h[i - 1] and l[i] > l[i - 1]:
            baby_hi, baby_lo = float(h[i]), float(l[i])
            # Scan for break within next few bars
            direction = None
            entry_idx = None
            entry = None
            for j in range(i + 1, min(i + 8, n)):
                if h[j] > baby_hi:
                    direction = "long"
                    entry_idx = j
                    entry = float(max(baby_hi, float(o[j])))
                    break
                if l[j] < baby_lo:
                    direction = "short"
                    entry_idx = j
                    entry = float(min(baby_lo, float(o[j])))
                    break
            if direction is None:
                i += 1
                continue
            cost = round_turn_cost_price(entry, symbol=symbol or None)
            if direction == "long":
                stop = baby_lo - 2 * cost
                risk = entry - stop
                if risk <= 0:
                    i += 1
                    continue
                tp = entry + rr * risk
            else:
                stop = baby_hi + 2 * cost
                risk = stop - entry
                if risk <= 0:
                    i += 1
                    continue
                tp = entry - rr * risk

            exit_idx, exit_price, outcome = None, None, "open"
            for j in range(entry_idx, n):
                if direction == "long":
                    if l[j] <= stop:
                        exit_idx, exit_price, outcome = j, stop, "loss"
                        break
                    if h[j] >= tp:
                        exit_idx, exit_price, outcome = j, tp, "win"
                        break
                else:
                    if h[j] >= stop:
                        exit_idx, exit_price, outcome = j, stop, "loss"
                        break
                    if l[j] <= tp:
                        exit_idx, exit_price, outcome = j, tp, "win"
                        break
            if exit_idx is None:
                exit_idx, exit_price = n - 1, float(c[n - 1])
            trades.append({
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(int(ts[entry_idx])),
                "direction": direction,
                "entry_price": round(entry, 5),
                "stop_loss": round(float(stop), 5),
                "take_profit": round(float(tp), 5),
                "exit_time": to_iso(int(ts[exit_idx])),
                "exit_price": round(float(exit_price), 5),
                "outcome": outcome,
                "setup": "pos_inside_break",
                "symbol": symbol or None,
                "reason": f"POS inside-candle break RR={rr}",
            })
            i = exit_idx + 1
            continue
        i += 1
    return trades


def run_strategy(candles, output_path, **kw):
    trades = generate_trades(candles, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 104: Power of Stocks Inside Candle")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_104_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
