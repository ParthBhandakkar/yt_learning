#!/usr/bin/env python3
"""
Strategy 99: Power of Stocks — Golden Setup (mechanical proxy)

Source: Power of Stocks (Subhasish Pani) — Golden Setup Live Trading
Video: https://www.youtube.com/watch?v=8cbKitkmxFc
Playlist: https://youtube.com/playlist?list=PLEK2KlOx2h_FUiF2ZQ4oB2tIFf82_NtJl

MECHANICAL INTERPRETATION of Token IQ / live Golden Setup teaching:
  1. Each UTC day: first 1H open = session reference.
  2. Nearest round levels (BTC 500, XAU 10, JPY 0.5, FX 0.005).
  3. Bias from day-open vs mid of round band.
  4. 1H close breaks bias-side round → fill next open.
  5. SL ~0.4*step (ATR floor); TP 3R. Max 2 attempts/day.

See also strategy_103_pos_5ema.py for his separate documented 5EMA system.

Usage:
  python strategy_99_pos_golden_setup.py --csv1h XAUUSD_1h.csv [--output out.json]
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


def _round_step(price: float, symbol: str) -> float:
    s = (symbol or "").upper()
    if "BTC" in s or price >= 10_000:
        return 500.0
    if "XAU" in s or "GOLD" in s or price >= 1000:
        return 10.0
    if "JPY" in s or price >= 50:
        return 0.50
    return 0.0050


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 3.0, max_per_day: int = 2):
    n = len(candles_1h)
    if n < 40:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)
    day_keys = [
        datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts
    ]
    trades = []
    day_attempts: dict[str, int] = {}
    i = 20
    while i < n - 1:
        day = day_keys[i]
        day_start = i
        while day_start > 0 and day_keys[day_start - 1] == day:
            day_start -= 1
        day_open = float(o[day_start])
        step = _round_step(day_open, symbol)
        round_lo = np.floor(day_open / step) * step
        round_hi = round_lo + step
        mid = (round_lo + round_hi) / 2.0
        bias = "long" if day_open >= mid else "short"
        if day_attempts.get(day, 0) >= max_per_day or i < day_start + 1:
            i += 1
            continue
        direction = None
        level = None
        if bias == "long" and c[i] > round_hi and c[i] > o[i] and c[i] > day_open:
            direction, level = "long", float(round_hi)
        elif bias == "short" and c[i] < round_lo and c[i] < o[i] and c[i] < day_open:
            direction, level = "short", float(round_lo)
        if direction is None:
            i += 1
            continue
        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        a = float(atr[i])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        sl_dist = max(0.4 * step, 0.8 * a, 4.0 * cost)
        stop = entry - sl_dist if direction == "long" else entry + sl_dist
        tp = entry + rr * sl_dist if direction == "long" else entry - rr * sl_dist
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
        day_attempts[day] = day_attempts.get(day, 0) + 1
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
            "setup": "pos_golden_round",
            "symbol": symbol or None,
            "reason": f"POS Golden Setup bias={bias} level={level} step={step} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 99: Power of Stocks Golden Setup")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=3.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_99_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
