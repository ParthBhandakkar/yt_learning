#!/usr/bin/env python3
"""
Strategy 130: JadeCap (Kyle Ng) — Daily Sweep / SFP (mechanical proxy)

Source: JadeCap (Kyle Ng) — "Daily Sweep" / swing-failure liquidity hunt
Video: https://www.youtube.com/watch?v=wZ4ea0VJnrw

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. Mark prior UTC-day swing high/low on 1H (confirmed +1 bar).
  2. During NY equity-open window (UTC hours 13–16): wick sweeps swing,
     then bar closes back inside (swing-failure pattern).
  3. Fill next 1H open; stop beyond sweep wick; TP 2R toward opposite swing.
  Max 1 attempt per UTC day.

See also strategy_131_jadecap_session_fvg.py for session liquidity + FVG model.

Usage:
  python strategy_130_jadecap_daily_sweep.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def _swings(h, l):
    sh, sl = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl.append(i)
    return sh, sl


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
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
    sh, sl = _swings(h, l)

    trades = []
    traded_days: set[str] = set()
    i = 30
    while i < n - 1:
        day = days[i]
        if day in traded_days or hours[i] < 13 or hours[i] > 16:
            i += 1
            continue
        last_sh = next((j for j in reversed(sh) if j <= i - 2 and days[j] < day), None)
        last_sl = next((j for j in reversed(sl) if j <= i - 2 and days[j] < day), None)
        if last_sh is None and last_sl is None:
            i += 1
            continue

        direction = None
        extreme = None
        if last_sl is not None:
            lvl = float(l[last_sl])
            if l[i] < lvl and c[i] > lvl and c[i] > o[i]:
                direction, extreme = "long", float(l[i])
        if direction is None and last_sh is not None:
            lvl = float(h[last_sh])
            if h[i] > lvl and c[i] < lvl and c[i] < o[i]:
                direction, extreme = "short", float(h[i])

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = extreme - max(2 * cost, abs(entry - extreme) * 0.05)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = extreme + max(2 * cost, abs(entry - extreme) * 0.05)
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
            "setup": "jadecap_daily_sweep",
            "symbol": symbol or None,
            "reason": f"JadeCap daily SFP NY window RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 130: JadeCap Daily Sweep")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_130_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
