#!/usr/bin/env python3
"""
Strategy 114: Stock Learners (Gautam Jha) — PDH/PDL Liquidity Sweep (mechanical proxy)

Source: Stock Learners / Gautam Jha — liquidity at previous day high/low
Video: https://www.youtube.com/watch?v=9gln2cf2wMs

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  Gautam marks prior-day high/low as liquidity pools; waits for sweep +
  trigger candle; enters on break of trigger extreme (1m in teaching).
  Proxy on 1H:
  1. Prior UTC-day high/low (PDH/PDL).
  2. Sweep: wick through level, close back inside range.
  3. Trigger: opposing candle after sweep (bearish after PDH sweep, etc.).
  4. Entry when next bar breaks trigger low/high; stop beyond sweep wick;
     target opposite PD level or 2R (whichever closer). Max 1 trade/day.

Usage:
  python strategy_114_sl_pdh_sweep.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
    n = len(candles_1h)
    if n < 50:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)

    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    day_hl: dict[str, tuple[float, float]] = {}
    cur = days[0]
    dhi, dlo = float(h[0]), float(l[0])
    for i in range(n):
        if days[i] != cur:
            day_hl[cur] = (dhi, dlo)
            cur = days[i]
            dhi, dlo = float(h[i]), float(l[i])
        else:
            dhi = max(dhi, float(h[i]))
            dlo = min(dlo, float(l[i]))
    day_hl[cur] = (dhi, dlo)

    ordered = []
    seen = set()
    for d in days:
        if d not in seen:
            ordered.append(d)
            seen.add(d)
    prev_of = {ordered[i]: ordered[i - 1] for i in range(1, len(ordered))}

    trades = []
    traded_days: set[str] = set()
    i = 2
    while i < n - 3:
        day = days[i]
        if day in traded_days or day not in prev_of:
            i += 1
            continue
        pdh, pdl = day_hl[prev_of[day]]

        direction = None
        sweep_ext = None
        trigger_i = None
        # PDH sweep → short
        if h[i] > pdh and c[i] < pdh:
            if i + 1 < n and c[i + 1] < o[i + 1]:
                direction, sweep_ext, trigger_i = "short", float(h[i]), i + 1
        # PDL sweep → long
        elif l[i] < pdl and c[i] > pdl:
            if i + 1 < n and c[i + 1] > o[i + 1]:
                direction, sweep_ext, trigger_i = "long", float(l[i]), i + 1

        if direction is None or trigger_i is None:
            i += 1
            continue

        break_lvl = float(l[trigger_i]) if direction == "short" else float(h[trigger_i])
        entry_idx = None
        for j in range(trigger_i + 1, min(trigger_i + 6, n)):
            if direction == "short" and l[j] < break_lvl:
                entry_idx = j
                break
            if direction == "long" and h[j] > break_lvl:
                entry_idx = j
                break
        if entry_idx is None or entry_idx >= n - 1:
            i += 1
            continue

        entry = float(max(break_lvl, o[entry_idx]) if direction == "long" else min(break_lvl, o[entry_idx]))
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(2 * cost, abs(pdh - pdl) * 0.05)
        if direction == "long":
            stop = sweep_ext - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp_rr = entry + rr * risk
            tp_lvl = pdh
            tp = tp_lvl if tp_lvl - entry >= risk else tp_rr
        else:
            stop = sweep_ext + pad
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp_rr = entry - rr * risk
            tp_lvl = pdl
            tp = tp_lvl if entry - tp_lvl >= risk else tp_rr

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
            "setup": "sl_pdh_sweep",
            "symbol": symbol or None,
            "reason": f"StockLearners PDH={pdh:.5f} PDL={pdl:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 114: Stock Learners PDH Sweep")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_114_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
