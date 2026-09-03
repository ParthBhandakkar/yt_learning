#!/usr/bin/env python3
"""
Strategy 127: Trader Kane — PO3 50% Reversal (mechanical proxy)

Source: Trader Kane — PO3 + 50% retracement / manipulation model
Video: https://www.youtube.com/watch?v=CY-AakQIECI

MECHANICAL INTERPRETATION (single instrument; no ES/NQ SMT):
  1. Accumulation: 6+ 1H bars with range < 1.5 * ATR(14).
  2. Manipulation: hour 14 UTC bar sweeps prior-day H/L (liquidity grab).
  3. Distribution target: 50% of impulse leg from sweep extreme to range mid.
  4. Entry after rejection close; SL beyond sweep wick; TP at 50% level.

Usage:
  python strategy_127_kane_po3_fifty.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def _atr(high, low, close, length: int = 14):
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    out = np.empty(n)
    out[0] = tr[0]
    k = 1.0 / length
    for i in range(1, n):
        out[i] = tr[i] * k + out[i - 1] * (1 - k)
    return out


def generate_trades(candles_1h, *, symbol: str = ""):
    n = len(candles_1h)
    if n < 50:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)

    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    day_hl: dict[str, tuple[float, float]] = {}
    cur, dhi, dlo = days[0], float(h[0]), float(l[0])
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
    i = 12
    while i < n - 1:
        day = days[i]
        if day in traded_days or day not in prev_of or hours[i] not in (14, 15, 16):
            i += 1
            continue
        pdh, pdl = day_hl[prev_of[day]]
        acc = slice(i - 6, i)
        acc_hi = float(np.max(h[acc]))
        acc_lo = float(np.min(l[acc]))
        a = float(atr[i])
        if a <= 0 or (acc_hi - acc_lo) > 1.5 * a:
            i += 1
            continue
        acc_mid = (acc_hi + acc_lo) / 2.0

        direction = None
        extreme = None
        if h[i] > pdh and c[i] < pdh:
            direction, extreme = "short", float(h[i])
        elif l[i] < pdl and c[i] > pdl:
            direction, extreme = "long", float(l[i])
        if direction is None:
            i += 1
            continue

        if direction == "long":
            tp = extreme + 0.5 * (acc_mid - extreme)
        else:
            tp = extreme - 0.5 * (extreme - acc_mid)

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = extreme - max(2 * cost, 0.1 * a)
            if entry <= stop or entry >= acc_mid:
                i += 1
                continue
            if tp <= entry:
                risk = entry - stop
                tp = entry + max(risk * 0.5, 2 * cost)
        else:
            stop = extreme + max(2 * cost, 0.1 * a)
            if entry >= stop or entry <= acc_mid:
                i += 1
                continue
            if tp >= entry:
                risk = stop - entry
                tp = entry - max(risk * 0.5, 2 * cost)

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
            "setup": "kane_po3_fifty",
            "symbol": symbol or None,
            "reason": f"Kane PO3 50% manip@{extreme:.5f} target={tp:.5f}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 127: Kane PO3 50% Reversal")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_127_results_{sym}.json"
    run_strategy(candles, out, symbol=sym)


if __name__ == "__main__":
    main()
