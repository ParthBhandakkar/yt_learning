#!/usr/bin/env python3
"""
Strategy 126: Trader Kane — Lab Model Continuation (mechanical proxy)

Source: Trader Kane — The Lab Model continuation branch
Video: https://www.youtube.com/watch?v=symX8WhG_dk

SEPARATE from strategy_125 (10am 4H sweep reversal).
MECHANICAL INTERPRETATION:
  1. HTF imbalance: 4H close vs 20-bar EMA defines trend.
  2. LTF balance: last 8 1H bars range width < 1.2 * ATR(14).
  3. Break of LTF range in HTF direction + bullish/bearish FVG on break bar.
  4. Entry on retest of broken range edge; SL other side; TP 2.5R toward HTF swing.

Usage:
  python strategy_126_kane_lab_continuation.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import detect_fvg, load_csv, parse_csv_filename, resample, round_turn_cost_price, save_trades, to_iso


def _ema(values, length: int):
    out = np.empty_like(values)
    k = 2.0 / (length + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


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


def _htf_trend(candles_1h, ema_len: int = 20):
    bias = np.zeros(len(candles_1h), dtype=np.int8)
    bars4 = resample(candles_1h, 240)
    if len(bars4) < ema_len + 3:
        return bias
    c4 = np.array([c.close for c in bars4])
    ema4 = _ema(c4, ema_len)
    end4 = [c.timestamp + 240 * 60 for c in bars4]
    sign = np.where(c4 > ema4, 1, np.where(c4 < ema4, -1, 0)).astype(np.int8)
    ts1 = np.array([c.timestamp for c in candles_1h])
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end4, decision_t) - 1
        if k >= 0:
            bias[i] = sign[k]
    return bias


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.5, balance_bars: int = 8):
    n = len(candles_1h)
    if n < 60:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)
    bias = _htf_trend(candles_1h)
    fvg_set = {f["idx"]: f for f in detect_fvg(candles_1h)}

    trades = []
    i = balance_bars + 5
    while i < n - 2:
        b = int(bias[i])
        if b == 0:
            i += 1
            continue
        window = slice(i - balance_bars, i)
        r_hi = float(np.max(h[window]))
        r_lo = float(np.min(l[window]))
        r_width = r_hi - r_lo
        a = float(atr[i])
        if a <= 0 or r_width > 1.2 * a:
            i += 1
            continue

        break_idx = None
        direction = None
        edge = None
        if b == 1 and c[i] > r_hi and c[i] > o[i]:
            fvg = fvg_set.get(i)
            if fvg and fvg["direction"] == "bullish":
                break_idx, direction, edge = i, "long", r_hi
        elif b == -1 and c[i] < r_lo and c[i] < o[i]:
            fvg = fvg_set.get(i)
            if fvg and fvg["direction"] == "bearish":
                break_idx, direction, edge = i, "short", r_lo
        if break_idx is None:
            i += 1
            continue

        entry_idx = None
        for j in range(break_idx + 1, min(break_idx + 6, n)):
            if direction == "long" and l[j] <= edge <= h[j] and c[j] > edge:
                entry_idx = j + 1
                break
            if direction == "short" and l[j] <= edge <= h[j] and c[j] < edge:
                entry_idx = j + 1
                break
        if entry_idx is None or entry_idx >= n:
            i += 1
            continue

        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = r_lo - max(2 * cost, 0.1 * a)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = r_hi + max(2 * cost, 0.1 * a)
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
            "setup": "kane_lab_continuation",
            "symbol": symbol or None,
            "reason": f"Kane Lab continuation HTF={b} range={r_lo:.5f}-{r_hi:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 126: Kane Lab Continuation")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.5)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_126_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
