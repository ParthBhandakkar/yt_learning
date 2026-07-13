#!/usr/bin/env python3
"""
Strategy 112: Booming Bulls — Morning Range Breakout (mechanical proxy)

Source: Booming Bulls (Anish Singh Thakur) — 15m range / post-open breakout
Video: https://www.youtube.com/watch?v=4iPEKSo_vwY

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  Anish teaches: skip first ~45m chop; mark 15m high/low; range < ~0.8%;
  trade breakout after 10:45 IST (05:15 UTC) in breakout direction.
  Proxy on 1H:
  1. UTC 00:00–05:00 range high/low (Asian / pre-London box).
  2. Range width / mid <= max_pct (default 0.8%).
  3. From UTC hour >= 5: 1H close breaks range with momentum candle.
  4. Fill next open; stop opposite side; take-profit 2R. Max 1 trade/day.

Usage:
  python strategy_112_bb_morning_range.py --csv1h EURUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def generate_trades(
    candles_1h,
    *,
    symbol: str = "",
    rr: float = 2.0,
    max_pct: float = 0.008,
    range_end_hour: int = 5,
    trade_start_hour: int = 5,
):
    n = len(candles_1h)
    if n < 60:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)

    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    trades = []
    traded_days: set[str] = set()
    i = 10
    while i < n - 1:
        day = days[i]
        if day in traded_days:
            i += 1
            continue
        if hours[i] < trade_start_hour:
            i += 1
            continue

        # Build pre-range box for this UTC day (hours 0 .. range_end_hour-1)
        rhi, rlo = None, None
        for j in range(i):
            if days[j] != day:
                continue
            if hours[j] >= range_end_hour:
                continue
            if rhi is None:
                rhi, rlo = float(h[j]), float(l[j])
            else:
                rhi = max(rhi, float(h[j]))
                rlo = min(rlo, float(l[j]))
        if rhi is None or rlo is None or rhi <= rlo:
            i += 1
            continue
        mid = (rhi + rlo) / 2.0
        if mid <= 0 or (rhi - rlo) / mid > max_pct:
            i += 1
            continue

        direction = None
        if c[i] > rhi and c[i] > o[i]:
            direction = "long"
        elif c[i] < rlo and c[i] < o[i]:
            direction = "short"
        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(2 * cost, 0.05 * (rhi - rlo))
        if direction == "long":
            stop = rlo - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = rhi + pad
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, n):
            if days[j] != day and j > entry_idx:
                exit_idx, exit_price = j - 1, float(c[j - 1])
                outcome = "open"
                break
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
            exit_idx = n - 1
            exit_price = float(c[exit_idx])

        traded_days.add(day)
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
            "setup": "bb_morning_range",
            "symbol": symbol or None,
            "reason": f"BB morning range {rlo:.5f}-{rhi:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 112: Booming Bulls Morning Range")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_112_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
