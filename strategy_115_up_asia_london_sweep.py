#!/usr/bin/env python3
"""
Strategy 115: Umar Punjabi — Asia Range London Sweep + BOS Retest (mechanical proxy)

Source: Umar Punjabi (The Alpha Trader) — London gold playbook
Video: https://www.youtube.com/watch?v=Fk2CBMxl9_E

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  Umar maps Asia 00:00–06:00 London, waits for London sweep of one side,
  then 5m BOS opposite direction and retest entry (1m–3m in teaching).
  Proxy on 1H:
  1. Asia box: UTC hours 0–5 high/low.
  2. London window UTC 7–10: sweep Asia high/low (wick through, close inside).
  3. BOS: close beyond prior 1H swing in sweep-opposite direction.
  4. Retest BOS level with rejection → next open; stop beyond sweep wick;
     TP Asia midline then stretch to opposite edge (use 2R min).
  Max 1 trade per UTC day.

Usage:
  python strategy_115_up_asia_london_sweep.py --csv1h XAUUSD_1h.csv [--output out.json]
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


def _swings(h, l):
    sh, sl = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl.append(i)
    return sh, sl


def generate_trades(
    candles_1h,
    *,
    symbol: str = "",
    rr: float = 2.0,
    asia_end: int = 6,
    london_start: int = 7,
    london_end: int = 10,
):
    n = len(candles_1h)
    if n < 80:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)
    sh, sl = _swings(h, l)

    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    trades = []
    traded_days: set[str] = set()
    i = 30
    while i < n - 2:
        day = days[i]
        hour = hours[i]
        if day in traded_days or hour < london_start or hour >= london_end:
            i += 1
            continue

        ahi, alo = None, None
        for j in range(i):
            if days[j] != day:
                continue
            if hours[j] >= asia_end:
                continue
            if ahi is None:
                ahi, alo = float(h[j]), float(l[j])
            else:
                ahi = max(ahi, float(h[j]))
                alo = min(alo, float(l[j]))
        if ahi is None or alo is None:
            i += 1
            continue
        amid = (ahi + alo) / 2.0
        a = float(atr[i])
        min_sweep = 0.5 * a if a > 0 else 0.0

        sweep_dir = None
        sweep_ext = None
        if h[i] >= ahi + min_sweep and c[i] < ahi:
            sweep_dir, sweep_ext = "high_swept", float(h[i])
        elif l[i] <= alo - min_sweep and c[i] > alo:
            sweep_dir, sweep_ext = "low_swept", float(l[i])
        if sweep_dir is None:
            i += 1
            continue

        last_sh = next((j for j in reversed(sh) if j <= i - 1), None)
        last_sl = next((j for j in reversed(sl) if j <= i - 1), None)
        if last_sh is None or last_sl is None:
            i += 1
            continue

        bos_level = None
        trade_dir = None
        if sweep_dir == "high_swept" and c[i] < l[last_sl]:
            trade_dir, bos_level = "short", float(l[last_sl])
        elif sweep_dir == "low_swept" and c[i] > h[last_sh]:
            trade_dir, bos_level = "long", float(h[last_sh])
        if trade_dir is None or bos_level is None:
            i += 1
            continue

        # Retest within next few bars
        entry_idx = None
        extreme = sweep_ext
        for j in range(i + 1, min(i + 6, n - 1)):
            if trade_dir == "long":
                if l[j] <= bos_level <= h[j] and c[j] > bos_level and c[j] > o[j]:
                    entry_idx = j + 1
                    extreme = min(extreme, float(l[j]))
                    break
            else:
                if l[j] <= bos_level <= h[j] and c[j] < bos_level and c[j] < o[j]:
                    entry_idx = j + 1
                    extreme = max(extreme, float(h[j]))
                    break
        if entry_idx is None or entry_idx >= n:
            i += 1
            continue

        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(0.1 * a, 2 * cost) if a > 0 else 2 * cost
        if trade_dir == "long":
            stop = extreme - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp_mid = amid
            tp = tp_mid if tp_mid - entry >= risk else entry + rr * risk
        else:
            stop = extreme + pad
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp_mid = amid
            tp = tp_mid if entry - tp_mid >= risk else entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, n):
            if trade_dir == "long":
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
            "direction": trade_dir,
            "entry_price": round(entry, 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(tp), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "setup": "up_asia_london_sweep",
            "symbol": symbol or None,
            "reason": f"Umar Asia {alo:.5f}-{ahi:.5f} sweep={sweep_dir} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 115: Umar Asia-London Sweep")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_115_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
