#!/usr/bin/env python3
"""
Strategy 101: Vinbull Trading Academy — Price Action S/R (mechanical proxy)

Source: Vinbull Trading Academy — Price Action / Support & Resistance
Video: https://www.youtube.com/watch?v=P34rJtjc7kw

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. HTF bias from completed 4H close vs 4H EMA(50).
  2. 1H swing highs/lows as S/R (confirmed +1 bar).
  3. Entry: bullish/bearish engulfing that taps S/R within 0.25*ATR of level,
     aligned with HTF bias; fill next 1H open.
  4. Stop beyond the swing extreme; take-profit 2R.

Usage:
  python strategy_101_vinbull_pa_sr.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, resample, round_turn_cost_price, save_trades, to_iso


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


def _swings(h, l):
    sh, sl = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl.append(i)
    return sh, sl


def _htf_bias(candles_1h, ema_len: int = 50):
    ts1 = np.array([c.timestamp for c in candles_1h])
    bias = np.zeros(len(candles_1h), dtype=np.int8)
    bars4 = resample(candles_1h, 240)
    if len(bars4) < ema_len + 2:
        return bias
    c4 = np.array([c.close for c in bars4])
    ema4 = _ema(c4, ema_len)
    end4 = [c.timestamp + 240 * 60 for c in bars4]
    sign4 = np.where(c4 > ema4, 1, np.where(c4 < ema4, -1, 0)).astype(np.int8)
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end4, decision_t) - 1
        if k >= 0:
            bias[i] = sign4[k]
    return bias


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
    n = len(candles_1h)
    if n < 80:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)
    bias = _htf_bias(candles_1h)
    sh, sl = _swings(h, l)

    trades = []
    i = 30
    while i < n - 1:
        b = int(bias[i])
        if b == 0:
            i += 1
            continue
        a = float(atr[i])
        if a <= 0:
            i += 1
            continue

        # Confirmed swings only (index <= i-1)
        last_sl = next((j for j in reversed(sl) if j <= i - 1), None)
        last_sh = next((j for j in reversed(sh) if j <= i - 1), None)

        bull_engulf = c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1]
        bear_engulf = c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1]

        direction = None
        extreme = None
        if b == 1 and bull_engulf and last_sl is not None:
            lvl = float(l[last_sl])
            if abs(l[i] - lvl) <= 0.25 * a or l[i] <= lvl <= h[i]:
                direction, extreme = "long", min(float(l[i]), lvl)
        elif b == -1 and bear_engulf and last_sh is not None:
            lvl = float(h[last_sh])
            if abs(h[i] - lvl) <= 0.25 * a or l[i] <= lvl <= h[i]:
                direction, extreme = "short", max(float(h[i]), lvl)

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = extreme - max(0.1 * a, 2 * cost)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = extreme + max(0.1 * a, 2 * cost)
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
            "setup": "vinbull_engulf_sr",
            "symbol": symbol or None,
            "reason": f"Vinbull PA engulf@S/R HTF={b} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 101: Vinbull PA S/R")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_101_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
