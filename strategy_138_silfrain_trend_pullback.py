#!/usr/bin/env python3
"""
Strategy 138: Marci Silfrain — Top-Down Trend Pullback (mechanical proxy)

Source: Marci Silfrain — Robbins Cup competitor; top-down index trend + specialization
Video: https://www.youtube.com/watch?v=Dt9vzMmf__o

MECHANICAL INTERPRETATION (FX/gold/BTC proxy — not ES/NQ tape):
  1. Weekly bias: completed-week close vs weekly EMA(10) on resampled 1W bars.
  2. Daily alignment: current day opens and trades with weekly bias.
  3. Pullback: 1H tag of EMA(20) with rejection candle in bias direction.
  4. Fill next open; stop beyond pullback extreme; TP 2R.
  Note: spelling variants Marci Silfrain / Silfrain in public sources.

Usage:
  python strategy_138_silfrain_trend_pullback.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys
from datetime import datetime, timezone

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


def _weekly_bias(candles_1h, ema_len: int = 10):
    ts1 = np.array([c.timestamp for c in candles_1h])
    bias = np.zeros(len(candles_1h), dtype=np.int8)
    bars_w = resample(candles_1h, 10080)
    if len(bars_w) < ema_len + 2:
        return bias
    c_w = np.array([c.close for c in bars_w])
    ema_w = _ema(c_w, ema_len)
    end_w = [c.timestamp + 10080 * 60 for c in bars_w]
    sign = np.where(c_w > ema_w, 1, np.where(c_w < ema_w, -1, 0)).astype(np.int8)
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end_w, decision_t) - 1
        if k >= 0:
            bias[i] = sign[k]
    return bias


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0, ema_len: int = 20):
    n = len(candles_1h)
    if n < 120:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    ema = _ema(c, ema_len)
    wbias = _weekly_bias(candles_1h)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    day_open: dict[str, float] = {}
    for idx in range(n):
        if days[idx] not in day_open:
            day_open[days[idx]] = float(o[idx])

    trades = []
    i = ema_len + 5
    while i < n - 1:
        b = int(wbias[i])
        if b == 0:
            i += 1
            continue
        day = days[i]
        dopen = day_open.get(day, float(o[i]))
        if b == 1 and dopen < ema[i]:
            i += 1
            continue
        if b == -1 and dopen > ema[i]:
            i += 1
            continue

        direction = None
        extreme = None
        tol = 0.15 * abs(c[i] - ema[i]) + 1e-8
        if b == 1 and l[i] <= ema[i] + tol and c[i] > ema[i] and c[i] > o[i]:
            direction, extreme = "long", float(l[i])
        elif b == -1 and h[i] >= ema[i] - tol and c[i] < ema[i] and c[i] < o[i]:
            direction, extreme = "short", float(h[i])

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(2 * cost, abs(c[i] - ema[i]) * 0.2)
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
            "setup": "silfrain_trend_pullback",
            "symbol": symbol or None,
            "reason": f"Silfrain weekly={b} EMA pullback RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 138: Silfrain Trend Pullback")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_138_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
