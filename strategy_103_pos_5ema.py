#!/usr/bin/env python3
"""
Strategy 103: Power of Stocks — 5EMA Alert Candle (canonical mechanical)

Source: Power of Stocks (Subhasish Pani) — famous 5EMA setup
Video: https://www.youtube.com/watch?v=8cbKitkmxFc
Rule sheets: community ports of his 5EMA teaching (Rattibha / Streak)

SEPARATE from Golden Setup (see strategy_99_pos_golden_setup.py).

  SELL (canonical 5m):
    - Alert: entire candle above 5EMA (no wick touch)
    - Entry: next candle breaks alert low → fill next open after break bar
    - SL: alert high; TP: 1:3 RR
  BUY (mirrored; he often uses 15m for buys — same rules on provided TF):
    - Alert: entire candle below 5EMA (no wick touch)
    - Entry: next candle breaks alert high
    - SL: alert low; TP: 1:3 RR

Usage:
  python strategy_103_pos_5ema.py --csv5m XAUUSD_5m.csv [--output out.json]
  python strategy_103_pos_5ema.py --csv1h XAUUSD_1h.csv
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def _ema(values: np.ndarray, length: int = 5) -> np.ndarray:
    out = np.empty_like(values, dtype=np.float64)
    k = 2.0 / (length + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def generate_trades(candles, *, symbol: str = "", rr: float = 3.0, allow_long: bool = True):
    n = len(candles)
    if n < 40:
        return []
    ts = np.array([c.timestamp for c in candles], dtype=np.int64)
    o = np.array([c.open for c in candles], dtype=np.float64)
    h = np.array([c.high for c in candles], dtype=np.float64)
    l = np.array([c.low for c in candles], dtype=np.float64)
    c = np.array([c.close for c in candles], dtype=np.float64)
    ema5 = _ema(c, 5)

    trades = []
    alert_sell = None
    alert_buy = None
    i = 10
    while i < n - 1:
        if l[i] > ema5[i]:
            alert_sell = i
        elif h[i] < ema5[i] and allow_long:
            alert_buy = i

        direction = None
        alert_i = None
        if alert_sell is not None and alert_sell < i:
            a = alert_sell
            if l[a] > ema5[a] and l[i] < l[a]:
                direction, alert_i = "short", a
                alert_sell = None
        if direction is None and allow_long and alert_buy is not None and alert_buy < i:
            a = alert_buy
            if h[a] < ema5[a] and h[i] > h[a]:
                direction, alert_i = "long", a
                alert_buy = None

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        if direction == "short":
            stop = float(h[alert_i])
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = entry - rr * risk
        else:
            stop = float(l[alert_i])
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk

        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if risk < 2 * cost:
            i += 1
            continue

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
            "setup": "pos_5ema_alert",
            "symbol": symbol or None,
            "reason": f"POS 5EMA alert@{alert_i} break RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles, output_path, **kw):
    trades = generate_trades(candles, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 103: Power of Stocks 5EMA")
    p.add_argument("--csv5m", default=None, help="5-minute OHLCV CSV (preferred)")
    p.add_argument("--csv15m", default=None, help="15-minute OHLCV CSV")
    p.add_argument("--csv1h", default=None, help="1-hour fallback")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=3.0)
    args = p.parse_args()
    path = args.csv5m or args.csv15m or args.csv1h
    if not path:
        p.error("Provide --csv5m (preferred), --csv15m, or --csv1h")
    candles = load_csv(path)
    meta = parse_csv_filename(path)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_103_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
