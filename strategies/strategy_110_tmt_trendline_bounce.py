#!/usr/bin/env python3
"""
Strategy 110: Thoughts Magic Trading — HTF Trendline Bounce (mechanical proxy)

Source: TMT (Thought's Magic Trading) — Global FCC / session trendline teaching
Video: https://www.youtube.com/@thoughtsmagictrading

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  TMT content repeatedly frames BTC/gold moves as trendline support holds in
  session context (Asian → London → NY). Proxy:
  1. On completed 4H bars: last two confirmed swing lows define rising trendline.
  2. HTF bias = long only when second low > first low (uptrend line).
  3. On 1H: bar low taps trendline (within 0.15*ATR) + bullish rejection close.
  4. Fill next 1H open; stop below tap wick; take-profit 2R.

Usage:
  python strategy_110_tmt_trendline_bounce.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, resample, round_turn_cost_price, save_trades, to_iso


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


def _swing_lows(low):
    idx = []
    for i in range(1, len(low) - 1):
        if low[i] < low[i - 1] and low[i] < low[i + 1]:
            idx.append(i)
    return idx


def _line_at(t1: int, p1: float, t2: int, p2: float, t: int) -> float:
    if t2 == t1:
        return p1
    return p1 + (p2 - p1) * (t - t1) / (t2 - t1)


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
    bars4 = resample(candles_1h, 240)
    if len(bars4) < 30 or len(candles_1h) < 80:
        return []

    ts4 = np.array([c.timestamp for c in bars4], dtype=np.int64)
    l4 = np.array([c.low for c in bars4], dtype=np.float64)
    end4 = [c.timestamp + 240 * 60 for c in bars4]

    # Causal trendline state after each completed 4H bar k
    tl_state = [None] * len(bars4)
    sl_idx = _swing_lows(l4)
    for k in range(3, len(bars4)):
        confirmed = [j for j in sl_idx if j <= k - 1]
        if len(confirmed) < 2:
            tl_state[k] = None
            continue
        j1, j2 = confirmed[-2], confirmed[-1]
        if l4[j2] <= l4[j1]:
            tl_state[k] = None
            continue
        tl_state[k] = (int(ts4[j1]), float(l4[j1]), int(ts4[j2]), float(l4[j2]))

    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)

    trades = []
    i = 30
    while i < len(candles_1h) - 1:
        decision_t = int(ts[i]) + 3600
        k = bisect.bisect_right(end4, decision_t) - 1
        if k < 3 or tl_state[k] is None:
            i += 1
            continue
        t1, p1, t2, p2 = tl_state[k]
        line = _line_at(t1, p1, t2, p2, int(ts[i]))
        a = float(atr[i])
        if a <= 0:
            i += 1
            continue
        tol = 0.15 * a
        tapped = l[i] <= line + tol and l[i] >= line - 2 * tol
        rejection = c[i] > o[i] and c[i] > c[i - 1]
        if not (tapped and rejection):
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        stop = min(float(l[i]), line) - max(0.1 * a, 2 * cost)
        risk = entry - stop
        if risk <= 0:
            i += 1
            continue
        tp = entry + rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, len(candles_1h)):
            if l[j] <= stop:
                exit_idx, exit_price, outcome = j, stop, "loss"
                break
            if h[j] >= tp:
                exit_idx, exit_price, outcome = j, tp, "win"
                break
        if exit_idx is None:
            exit_idx = len(candles_1h) - 1
            exit_price = float(c[exit_idx])

        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(ts[entry_idx])),
            "direction": "long",
            "entry_price": round(entry, 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(tp), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "setup": "tmt_trendline_bounce",
            "symbol": symbol or None,
            "reason": f"TMT 4H trendline bounce line={line:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 110: TMT Trendline Bounce")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_110_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
