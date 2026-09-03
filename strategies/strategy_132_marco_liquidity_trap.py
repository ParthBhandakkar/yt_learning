#!/usr/bin/env python3
"""
Strategy 132: Marco Trades — Liquidity Trap Reversal (mechanical proxy)

Source: Marco Trades — liquidity trap / stop-run reversal playbook
Video: https://www.youtube.com/watch?v=DAnXM7C16h0

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. 1H swing high/low that previously rejected price (moved away >= 0.5*ATR).
  2. Price returns, wicks through level, closes back inside (trap confirmed).
  3. Fill next 1H open; stop beyond sweep wick; TP 2R.
  Max 1 trade per swing level.

See also strategy_133_marco_sd_choch.py for sweep + CHOCH + zone retest variant.

Usage:
  python strategy_132_marco_liquidity_trap.py --csv1h XAUUSD_1h.csv [--output out.json]
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


def _swings(h, l):
    sh, sl = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl.append(i)
    return sh, sl


def _respected_swing(idx, h, l, atr, is_high: bool, min_bars: int = 3):
    a = float(atr[idx])
    if a <= 0:
        return False
    end = min(idx + min_bars + 5, len(h))
    if is_high:
        return any(l[j] < h[idx] - 0.5 * a for j in range(idx + 1, end))
    return any(h[j] > l[idx] + 0.5 * a for j in range(idx + 1, end))


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
    n = len(candles_1h)
    if n < 60:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)
    sh, sl = _swings(h, l)

    trades = []
    used_levels: set[tuple[str, float]] = set()
    i = 25
    while i < n - 1:
        direction = None
        extreme = None
        level_key = None

        last_sl = next((j for j in reversed(sl) if j <= i - 2), None)
        if last_sl is not None and _respected_swing(last_sl, h, l, atr, False):
            lvl = float(l[last_sl])
            key = ("low", round(lvl, 5))
            if key not in used_levels and l[i] < lvl and c[i] > lvl:
                direction, extreme, level_key = "long", float(l[i]), key

        if direction is None:
            last_sh = next((j for j in reversed(sh) if j <= i - 2), None)
            if last_sh is not None and _respected_swing(last_sh, h, l, atr, True):
                lvl = float(h[last_sh])
                key = ("high", round(lvl, 5))
                if key not in used_levels and h[i] > lvl and c[i] < lvl:
                    direction, extreme, level_key = "short", float(h[i]), key

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(2 * cost, 0.1 * float(atr[i]))
        if direction == "long":
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

        used_levels.add(level_key)
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
            "setup": "marco_liquidity_trap",
            "symbol": symbol or None,
            "reason": f"Marco liquidity trap RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 132: Marco Liquidity Trap")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_132_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
