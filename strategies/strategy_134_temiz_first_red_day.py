#!/usr/bin/env python3
"""
Strategy 134: Alex Temiz (AT09) — First Red Day Fade (mechanical proxy)

Source: Alex Temiz — First Red Day / extended-rally exhaustion (former SMB Capital guest)
Video: https://www.youtube.com/watch?v=vP6GK_HsnDM

MECHANICAL INTERPRETATION (FX/gold/BTC proxy — not small-cap tape):
  1. Count consecutive bullish 1H closes (extension >= 4 bars).
  2. First bearish close after extension = profit-taking trigger (fade).
  3. Fill next 1H open short; stop above extension high; TP 2R.
  Max 1 fade per extension leg.

See also strategy_135_temiz_level_lower_high.py for key-level + lower-high add model.

Usage:
  python strategy_134_temiz_first_red_day.py --csv1h BTCUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0, min_streak: int = 4):
    n = len(candles_1h)
    if n < 40:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)

    trades = []
    i = min_streak + 2
    while i < n - 1:
        streak = 0
        leg_hi = -np.inf
        j = i - 1
        while j >= 1 and c[j] > o[j]:
            streak += 1
            leg_hi = max(leg_hi, h[j])
            j -= 1
        if streak < min_streak or c[i] >= o[i]:
            i += 1
            continue

        direction = "short"
        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        stop = leg_hi + max(2 * cost, (leg_hi - float(l[j + 1])) * 0.05)
        risk = stop - entry
        if risk <= 0:
            i += 1
            continue
        tp = entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for k in range(entry_idx, n):
            if h[k] >= stop:
                exit_idx, exit_price, outcome = k, stop, "loss"
                break
            if l[k] <= tp:
                exit_idx, exit_price, outcome = k, tp, "win"
                break
        if exit_idx is None:
            exit_idx = n - 1
            exit_price = float(c[exit_idx])

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
            "setup": "temiz_first_red_day",
            "symbol": symbol or None,
            "reason": f"Temiz first-red-day fade streak={streak} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 134: Temiz First Red Day")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_134_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
