#!/usr/bin/env python3
"""
Strategy 120: Fabio Valentini — IVB Opening Range Breakout (mechanical proxy)

Source: Fabio Valentini (Fabervaale) — IVB / orderflow ORB model
Video: https://www.youtube.com/watch?v=cUTsoU-15Tc

MECHANICAL INTERPRETATION (no footprint/CVD — volume spike proxy only):
  1. NY cash proxy: 13:00 UTC 1H bar = opening range (OR) high/low.
  2. Session window hours 13–17 UTC only.
  3. Close breaks OR with body > 50% range and volume >= 1.2x 10-bar avg.
  4. Fill next 1H open; SL opposite OR side; TP 2.5R. Max 1 trade/day.

See strategy_121_fabio_amt_meanrev.py for his separate AMT mean-reversion model.

Usage:
  python strategy_120_fabio_orb_ivb.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.5):
    n = len(candles_1h)
    if n < 60:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    v = np.array([c.volume for c in candles_1h], dtype=np.float64)

    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    or_by_day: dict[str, tuple[float, float, int]] = {}
    for i in range(n):
        if hours[i] == 13:
            or_by_day[days[i]] = (float(h[i]), float(l[i]), i)

    vol_avg = np.full(n, np.nan)
    for i in range(10, n):
        vol_avg[i] = float(np.mean(v[i - 10:i]))

    trades = []
    traded_days: set[str] = set()
    i = 15
    while i < n - 1:
        day = days[i]
        if day in traded_days or day not in or_by_day:
            i += 1
            continue
        or_hi, or_lo, or_idx = or_by_day[day]
        if i <= or_idx or hours[i] < 14 or hours[i] > 17:
            i += 1
            continue
        rng = h[i] - l[i]
        if rng <= 0:
            i += 1
            continue
        body = abs(c[i] - o[i])
        va = vol_avg[i]
        vol_ok = np.isnan(va) or va <= 0 or v[i] >= 1.2 * va

        direction = None
        if vol_ok and body >= 0.5 * rng and c[i] > or_hi and c[i] > o[i]:
            direction = "long"
        elif vol_ok and body >= 0.5 * rng and c[i] < or_lo and c[i] < o[i]:
            direction = "short"
        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = or_lo - max(2 * cost, 0.05 * (or_hi - or_lo))
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = or_hi + max(2 * cost, 0.05 * (or_hi - or_lo))
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
            "setup": "fabio_orb_ivb",
            "symbol": symbol or None,
            "reason": f"Fabio IVB ORB break OR={or_lo:.5f}-{or_hi:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 120: Fabio Valentini IVB ORB")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.5)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_120_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
