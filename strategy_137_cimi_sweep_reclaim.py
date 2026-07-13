#!/usr/bin/env python3
"""
Strategy 137: Andrea Cimi — Liquidity Sweep + Reclaim (mechanical proxy)

Source: Andrea Cimi — stop-run / absorption / reclaim auction logic
Video: https://www.youtube.com/watch?v=vwSxOFM8GWI

MECHANICAL INTERPRETATION (orderflow proxied by price rejection only):
  1. Prior UTC-day high/low as external liquidity pools.
  2. Sweep: wick through PDH or PDL during NY hours (UTC 12–17).
  3. Reclaim: same bar or next bar closes back inside (absorption proxy).
  4. Fill next open; stop beyond sweep; TP 2R toward opposite PD level.

See also strategy_136_cimi_orb.py for opening-range breakout model.

Usage:
  python strategy_137_cimi_sweep_reclaim.py --csv1h EURUSD_1h.csv [--output out.json]
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
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

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
    i = 3
    while i < n - 2:
        day = days[i]
        if day in traded_days or day not in prev_of or hours[i] < 12 or hours[i] > 17:
            i += 1
            continue
        pdh, pdl = day_hl[prev_of[day]]

        direction = None
        extreme = None
        if l[i] < pdl and c[i] > pdl:
            direction, extreme = "long", float(l[i])
        elif h[i] > pdh and c[i] < pdh:
            direction, extreme = "short", float(h[i])
        elif i + 1 < n and l[i] < pdl and c[i + 1] > pdl:
            direction, extreme = "long", float(l[i])
        elif i + 1 < n and h[i] > pdh and c[i + 1] < pdh:
            direction, extreme = "short", float(h[i])

        if direction is None:
            i += 1
            continue

        signal_idx = i if (direction == "long" and c[i] > pdl) or (direction == "short" and c[i] < pdh) else i + 1
        entry_idx = signal_idx + 1
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(2 * cost, abs(pdh - pdl) * 0.03)
        if direction == "long":
            stop = extreme - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = min(entry + rr * risk, pdh)
            if tp <= entry:
                tp = entry + rr * risk
        else:
            stop = extreme + pad
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = max(entry - rr * risk, pdl)
            if tp >= entry:
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
            "setup": "cimi_sweep_reclaim",
            "symbol": symbol or None,
            "reason": f"Cimi PD sweep+reclaim RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 137: Cimi Sweep Reclaim")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_137_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
