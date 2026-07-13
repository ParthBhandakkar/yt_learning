#!/usr/bin/env python3
"""
Strategy 102: Trading Techstreet — Level Candlestick Scalp (mechanical proxy)

Source: Trading Techstreet (Akhand Pratap Singh) — morning scalping / candlesticks
Video: https://www.youtube.com/watch?v=TUurudYuDtg

MECHANICAL INTERPRETATION (tightened toward common PA scalp teaching):
  1. Prior UTC-day high/low as liquidity levels.
  2. Within first 10 hours: pin bar (wick >= 2x body) or engulfing that rejects PDH/PDL.
  3. Entry on *break* of signal-candle high (long) / low (short) — not naked next open.
  4. Stop beyond signal wick; take-profit 1.5R.
  Max 1 trade per UTC day.

Usage:
  python strategy_102_techstreet_level_scalp.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 1.5, max_hour: int = 10):
    n = len(candles_1h)
    if n < 50:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)

    days = [
        datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat()
        for t in ts
    ]
    hours = [
        datetime.fromtimestamp(int(t), tz=timezone.utc).hour
        for t in ts
    ]

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

    ordered_days = []
    seen = set()
    for d in days:
        if d not in seen:
            ordered_days.append(d)
            seen.add(d)
    prev_of = {ordered_days[i]: ordered_days[i - 1] for i in range(1, len(ordered_days))}

    trades = []
    traded_days = set()
    i = 2
    while i < n - 2:
        day = days[i]
        if day in traded_days or day not in prev_of:
            i += 1
            continue
        if hours[i] >= max_hour:
            i += 1
            continue
        pdh, pdl = day_hl[prev_of[day]]

        body = abs(c[i] - o[i])
        upper = h[i] - max(c[i], o[i])
        lower = min(c[i], o[i]) - l[i]
        rng = h[i] - l[i]
        if rng <= 0:
            i += 1
            continue

        # Pin after opposing move (hammer after red / shooting star after green)
        prior_bear = c[i - 1] < o[i - 1]
        prior_bull = c[i - 1] > o[i - 1]
        bull_pin = prior_bear and lower >= 2 * max(body, 1e-12) and lower >= upper
        bear_pin = prior_bull and upper >= 2 * max(body, 1e-12) and upper >= lower
        bull_eng = c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]
        bear_eng = c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1]

        signal = None  # (direction, stop_extreme, break_level)
        if (bull_pin or bull_eng) and l[i] <= pdl * 1.001 and c[i] >= pdl:
            signal = ("long", float(l[i]), float(h[i]))
        elif (bear_pin or bear_eng) and h[i] >= pdh * 0.999 and c[i] <= pdh:
            signal = ("short", float(h[i]), float(l[i]))

        if signal is None:
            i += 1
            continue

        direction, extreme, break_lvl = signal
        # Wait for break of signal candle (causal: scan forward bars after signal close)
        entry_idx = None
        entry = None
        for j in range(i + 1, min(i + 6, n)):
            if direction == "long" and h[j] > break_lvl:
                entry_idx = j
                entry = float(max(break_lvl, float(o[j])))  # approx stop-entry fill
                break
            if direction == "short" and l[j] < break_lvl:
                entry_idx = j
                entry = float(min(break_lvl, float(o[j])))
                break
        if entry_idx is None:
            i += 1
            continue

        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = extreme - max(2 * cost, 0.05 * abs(pdh - pdl))
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = extreme + max(2 * cost, 0.05 * abs(pdh - pdl))
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
            "setup": "techstreet_pdhl_break",
            "symbol": symbol or None,
            "reason": f"Techstreet PDH/L break-of-signal RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 102: Techstreet Level Scalp")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=1.5)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_102_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
