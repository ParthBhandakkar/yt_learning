#!/usr/bin/env python3
"""
Strategy 122: TG Capital (Tyler) — London Trident Pattern (mechanical proxy)

Source: TG Capital (Tyler Goedtel) — Trident / Unique High RR London model
Video: https://www.youtube.com/watch?v=ADnslyKOwFE

MECHANICAL INTERPRETATION (30M approximated on 1H; no 200-pip targets):
  1. Daily bias: close vs EMA(200) on resampled daily bars.
  2. London window 07–11 UTC; EMAs 5/9/13/21 stacked on 1H.
  3. Bullish FVG on signal bar; doji wicks FVG 50% (consequent encroachment).
  4. Confirmation: next bar closes below doji high (long) / above doji low (short).
  5. SL beyond doji wick; TP 3R. Max 1 trade per UTC day.

Usage:
  python strategy_122_tg_trident.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys
from datetime import datetime, timezone

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


def _daily_bias(candles_1h, ema_len: int = 200):
    bias = np.zeros(len(candles_1h), dtype=np.int8)
    daily = resample(candles_1h, 1440)
    if len(daily) < ema_len + 2:
        return bias
    ts1 = np.array([c.timestamp for c in candles_1h])
    c1d = np.array([c.close for c in daily])
    ema1d = _ema(c1d, ema_len)
    end1d = [c.timestamp + 86400 for c in daily]
    sign = np.where(c1d > ema1d, 1, np.where(c1d < ema1d, -1, 0)).astype(np.int8)
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end1d, decision_t) - 1
        if k >= 0:
            bias[i] = sign[k]
    return bias


def _stacked(o, h, l, c, i: int, direction: str) -> bool:
    e5, e9, e13, e21 = (_ema(c, n)[i] for n in (5, 9, 13, 21))
    if direction == "long":
        return e5 > e9 > e13 > e21 and c[i] > e21
    return e5 < e9 < e13 < e21 and c[i] < e21


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 3.0):
    n = len(candles_1h)
    if n < 250:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    bias = _daily_bias(candles_1h)

    fvgs = detect_fvg(candles_1h)
    fvg_by_idx = {f["idx"]: f for f in fvgs}

    trades = []
    traded_days: set[str] = set()
    i = 220
    while i < n - 2:
        day = days[i]
        if day in traded_days or hours[i] < 7 or hours[i] > 11:
            i += 1
            continue
        b = int(bias[i])
        if b == 0:
            i += 1
            continue

        fvg = fvg_by_idx.get(i - 1) or fvg_by_idx.get(i - 2)
        if fvg is None:
            i += 1
            continue

        rng = h[i] - l[i]
        body = abs(c[i] - o[i])
        if rng <= 0 or body > 0.25 * rng:
            i += 1
            continue

        fvg_mid = (fvg["upper"] + fvg["lower"]) / 2.0
        direction = None
        if b == 1 and fvg["direction"] == "bullish" and _stacked(o, h, l, c, i, "long"):
            if l[i] <= fvg_mid <= h[i]:
                if c[i + 1] < h[i]:
                    direction = "long"
        elif b == -1 and fvg["direction"] == "bearish" and _stacked(o, h, l, c, i, "short"):
            if l[i] <= fvg_mid <= h[i]:
                if c[i + 1] > l[i]:
                    direction = "short"
        if direction is None:
            i += 1
            continue

        entry_idx = i + 2
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = float(l[i]) - max(2 * cost, 0.1 * rng)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = float(h[i]) + max(2 * cost, 0.1 * rng)
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
            "setup": "tg_trident",
            "symbol": symbol or None,
            "reason": f"TG Trident FVG@{fvg_mid:.5f} bias={b} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 122: TG Capital Trident")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=3.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_122_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
