#!/usr/bin/env python3
"""
Strategy 125: Trader Kane — Lab Model Reversal (mechanical proxy)

Source: Trader Kane — The Lab Model (NQ futures; ES SMT approximated via range logic)
Video: https://www.youtube.com/watch?v=3rdUZEbKSRA

MECHANICAL INTERPRETATION (no ES/NQ pair — single-instrument sweep + iFVG proxy):
  1. 10am ET 4H bar proxy: 4H bar opening 14:00 UTC; trade window 14–17 UTC.
  2. Sweep 4H H/L (wick through, close back inside) = liquidity grab.
  3. iFVG proxy: prior bearish FVG violated upward (long) or bullish FVG violated down.
  4. Entry next 1H open; SL at sweep extreme; TP = premium/discount midpoint (LLT proxy).

See strategy_126_kane_lab_continuation.py and strategy_127_kane_po3_fifty.py.

Usage:
  python strategy_125_kane_lab_reversal.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import detect_fvg, load_csv, parse_csv_filename, resample, round_turn_cost_price, save_trades, to_iso


def _ref_4h_by_day(bars4):
    """Map UTC date -> (hi, lo, end_ts) for 10am-ET proxy 4H bar (open hour 11 or 15 UTC)."""
    out: dict[str, tuple[float, float, int]] = {}
    for bar in bars4:
        dt = datetime.fromtimestamp(bar.timestamp, tz=timezone.utc)
        if dt.hour in (11, 15):
            day = dt.date().isoformat()
            end_ts = bar.timestamp + 240 * 60
            prev = out.get(day)
            if prev is None or end_ts > prev[2]:
                out[day] = (float(bar.high), float(bar.low), end_ts)
    return out


def _leg_mid(h, l, i: int, lookback: int = 20) -> float:
    start = max(0, i - lookback)
    return (float(np.max(h[start:i + 1])) + float(np.min(l[start:i + 1]))) / 2.0


def generate_trades(candles_1h, *, symbol: str = ""):
    bars4 = resample(candles_1h, 240)
    if len(bars4) < 30 or len(candles_1h) < 80:
        return []

    ref_map = _ref_4h_by_day(bars4)
    fvgs = detect_fvg(candles_1h)

    n = len(candles_1h)
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]

    trades = []
    traded_days: set[str] = set()
    i = 30
    while i < n - 1:
        day = days[i]
        if day in traded_days or day not in ref_map or hours[i] < 15 or hours[i] > 20:
            i += 1
            continue
        ref_hi, ref_lo, ref_end = ref_map[day]
        if int(ts[i]) < ref_end:
            i += 1
            continue

        direction = None
        extreme = None
        if h[i] > ref_hi and c[i] < ref_hi:
            direction, extreme = "short", float(h[i])
        elif l[i] < ref_lo and c[i] > ref_lo:
            direction, extreme = "long", float(l[i])
        if direction is None:
            i += 1
            continue

        ifvg_ok = False
        for fvg in fvgs:
            if fvg["idx"] > i - 8 or fvg["idx"] < i - 30:
                continue
            if direction == "long" and fvg["direction"] == "bearish" and c[i] > fvg["upper"]:
                ifvg_ok = True
                break
            if direction == "short" and fvg["direction"] == "bullish" and c[i] < fvg["lower"]:
                ifvg_ok = True
                break
        if not ifvg_ok:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        llt = _leg_mid(h, l, i)
        if direction == "long":
            stop = extreme - max(2 * cost, abs(entry - llt) * 0.05)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = max(llt, entry + risk)
        else:
            stop = extreme + max(2 * cost, abs(entry - llt) * 0.05)
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = min(llt, entry - risk)

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
            "setup": "kane_lab_reversal",
            "symbol": symbol or None,
            "reason": f"Kane Lab reversal sweep 4H={ref_lo:.5f}-{ref_hi:.5f} LLT={llt:.5f}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 125: Kane Lab Reversal")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_125_results_{sym}.json"
    run_strategy(candles, out, symbol=sym)


if __name__ == "__main__":
    main()
