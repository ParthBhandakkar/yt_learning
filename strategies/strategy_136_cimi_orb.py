#!/usr/bin/env python3
"""
Strategy 136: Andrea Cimi — Opening Range Breakout (mechanical proxy)

Source: Andrea Cimi — 15-minute cash-session opening range breakout (ORB) + initiative
Video: https://www.youtube.com/watch?v=KObUowoFiK0

MECHANICAL INTERPRETATION (no footprint data — volume proxy on OHLCV):
  1. NY cash open proxy: first 1H bar at UTC hour 13 defines opening range.
  2. Breakout: later bar (hours 14–17) closes beyond range with body >= 0.3*ATR
     and volume >= 1.2x 20-bar average (initiative proxy).
  3. Fill next open; stop at opposite side of range; TP 2R.
  Max 1 ORB trade per UTC day.

See also strategy_137_cimi_sweep_reclaim.py for liquidity sweep + reclaim model.

Usage:
  python strategy_136_cimi_orb.py --csv1h XAUUSD_1h.csv [--output out.json]
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


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
    n = len(candles_1h)
    if n < 50:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    vol = np.array([c.volume for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    orb: dict[str, tuple[float, float, int]] = {}
    for idx in range(n):
        if hours[idx] == 13 and days[idx] not in orb:
            orb[days[idx]] = (float(h[idx]), float(l[idx]), idx)

    trades = []
    traded_days: set[str] = set()
    i = 25
    while i < n - 1:
        day = days[i]
        if day in traded_days or day not in orb or hours[i] < 14 or hours[i] > 17:
            i += 1
            continue
        or_hi, or_lo, or_idx = orb[day]
        if i <= or_idx:
            i += 1
            continue

        vol_avg = float(np.mean(vol[max(0, i - 20):i])) if i >= 5 else float(vol[i])
        body = abs(c[i] - o[i])
        a = float(atr[i])
        vol_ok = vol[i] >= 1.2 * max(vol_avg, 1.0)
        if not vol_ok or body < 0.3 * a:
            i += 1
            continue

        direction = None
        if c[i] > or_hi and c[i] > o[i]:
            direction = "long"
        elif c[i] < or_lo and c[i] < o[i]:
            direction = "short"
        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = or_lo - max(2 * cost, 0.1 * a)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = or_hi + max(2 * cost, 0.1 * a)
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
            "setup": "cimi_orb",
            "symbol": symbol or None,
            "reason": f"Cimi ORB break range={or_lo:.5f}-{or_hi:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 136: Cimi ORB")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_136_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
