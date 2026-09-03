#!/usr/bin/env python3
"""
Strategy 113: Booming Bulls — Sniper Breakout Retest (mechanical proxy)

Source: Booming Bulls (Anish Singh Thakur) — Sniper Setup live example
Video: https://www.youtube.com/watch?v=dJ4QitUYo90

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  Anish's sniper: massive breakout, then price retests broken level as new
  support/resistance and holds — enter on rejection, not on first break.
  Proxy on 1H:
  1. Impulse bar: range >= 2*ATR and close beyond prior 20-bar high/low.
  2. Within next 8 bars: retest broken level (wick into level, close holds).
  3. Rejection candle aligned with breakout direction → fill next open.
  4. Stop beyond retest wick; take-profit 2.5R.

Usage:
  python strategy_113_bb_sniper_retest.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys

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


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.5, retest_bars: int = 8):
    n = len(candles_1h)
    if n < 50:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)

    trades = []
    i = 25
    while i < n - retest_bars - 2:
        a = float(atr[i])
        if a <= 0:
            i += 1
            continue
        prior_hi = float(np.max(h[i - 20 : i]))
        prior_lo = float(np.min(l[i - 20 : i]))
        impulse_rng = h[i] - l[i]

        breakout = None
        level = None
        if impulse_rng >= 2.0 * a and c[i] > prior_hi and c[i] > o[i]:
            breakout, level = "long", prior_hi
        elif impulse_rng >= 2.0 * a and c[i] < prior_lo and c[i] < o[i]:
            breakout, level = "short", prior_lo
        if breakout is None:
            i += 1
            continue

        entry_idx = None
        extreme = None
        for j in range(i + 1, min(i + 1 + retest_bars, n - 1)):
            if breakout == "long":
                if l[j] <= level <= h[j] and c[j] > level and c[j] > o[j]:
                    entry_idx = j + 1
                    extreme = float(l[j])
                    break
            else:
                if l[j] <= level <= h[j] and c[j] < level and c[j] < o[j]:
                    entry_idx = j + 1
                    extreme = float(h[j])
                    break
        if entry_idx is None or entry_idx >= n:
            i += 1
            continue

        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(0.1 * a, 2 * cost)
        if breakout == "long":
            stop = extreme - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = extreme + pad
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, n):
            if breakout == "long":
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

        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(ts[entry_idx])),
            "direction": breakout,
            "entry_price": round(entry, 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(tp), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "setup": "bb_sniper_retest",
            "symbol": symbol or None,
            "reason": f"BB sniper retest@{level:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 113: Booming Bulls Sniper Retest")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.5)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_113_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
