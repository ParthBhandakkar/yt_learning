#!/usr/bin/env python3
"""
Strategy 123: Umar Ashraf — Break-and-Hold at Key Level (mechanical proxy)

Source: Umar Ashraf — level-based execution / opening-drive mechanics
Video: https://www.youtube.com/watch?v=K2bHUgUMgx8

MECHANICAL INTERPRETATION (FX/gold price proxy; no options/L2):
  1. Key levels = prior UTC-day high/low.
  2. Break: 1H close beyond level with volume >= 1.5x 10-bar avg.
  3. Retest: within 6 bars price taps level (0.2*ATR) and rejects.
  4. Fill next 1H open after rejection; SL beyond retest extreme; TP 2.5R.
  Max 1 trade per UTC day.

Usage:
  python strategy_123_umar_break_hold.py --csv1h XAUUSD_1h.csv [--output out.json]
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


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.5):
    n = len(candles_1h)
    if n < 50:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    v = np.array([c.volume for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)

    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
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

    vol_avg = np.full(n, np.nan)
    for i in range(10, n):
        vol_avg[i] = float(np.mean(v[i - 10:i]))

    trades = []
    traded_days: set[str] = set()
    i = 15
    while i < n - 2:
        day = days[i]
        if day in traded_days or day not in prev_of:
            i += 1
            continue
        pdh, pdl = day_hl[prev_of[day]]
        va = vol_avg[i]
        vol_ok = np.isnan(va) or va <= 0 or v[i] >= 1.5 * va
        a = float(atr[i])
        tol = 0.2 * a if a > 0 else abs(pdh - pdl) * 0.01

        break_dir = None
        level = None
        if vol_ok and c[i] > pdh and c[i] > o[i]:
            break_dir, level = "long", pdh
        elif vol_ok and c[i] < pdl and c[i] < o[i]:
            break_dir, level = "short", pdl
        if break_dir is None:
            i += 1
            continue

        signal_idx = None
        extreme = None
        for j in range(i + 1, min(i + 7, n)):
            if break_dir == "long":
                if l[j] <= level + tol and c[j] > level and c[j] > o[j]:
                    signal_idx, extreme = j, float(l[j])
                    break
            else:
                if h[j] >= level - tol and c[j] < level and c[j] < o[j]:
                    signal_idx, extreme = j, float(h[j])
                    break
        if signal_idx is None:
            i += 1
            continue

        entry_idx = signal_idx + 1
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if break_dir == "long":
            stop = extreme - max(2 * cost, tol)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = extreme + max(2 * cost, tol)
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, n):
            if break_dir == "long":
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
            "direction": break_dir,
            "entry_price": round(entry, 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(tp), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "setup": "umar_break_hold",
            "symbol": symbol or None,
            "reason": f"Umar break-hold level={level:.5f} vol_ok RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 123: Umar Break-and-Hold")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.5)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_123_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
