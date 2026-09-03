#!/usr/bin/env python3
"""
Strategy 124: Brando (Elite Options Trader) — HTF S/R Momentum Breakout (mechanical proxy)

Source: Brando / Elite Options Trader — support & resistance momentum playbook
Video: https://www.youtube.com/watch?v=yLuH8YZXORQ

FX/GOLD ADAPTATION (no options / size-for-zero — use hard SL beyond level):
  1. HTF levels from daily swing highs/lows + nearest psychological round.
  2. NY open proxy hours 13–16 UTC; strong momentum candle (body >= 60% range).
  3. Close breaks HTF level with follow-through; fill next 1H open.
  4. SL beyond level + wick; TP 3R riding breakout. Max 1 trade/day.

Usage:
  python strategy_124_brando_sr_breakout.py --csv1h XAUUSD_1h.csv [--output out.json]
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


def _swings(h, l):
    sh, sl = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl.append(i)
    return sh, sl


def _round_step(price: float, symbol: str) -> float:
    s = (symbol or "").upper()
    if "BTC" in s or price >= 10_000:
        return 500.0
    if "XAU" in s or "GOLD" in s or price >= 1000:
        return 50.0
    if "JPY" in s or price >= 50:
        return 1.0
    return 0.0100


def _nearest_round(price: float, step: float) -> float:
    return round(price / step) * step


def _htf_levels(candles_1h, symbol: str):
    """Return per-1H-index list of active resistance/support levels (causal daily)."""
    daily = resample(candles_1h, 1440)
    if len(daily) < 10:
        return [], []
    hd = np.array([c.high for c in daily])
    ld = np.array([c.low for c in daily])
    end_d = [c.timestamp + 86400 for c in daily]
    sh, sl = _swings(hd, ld)
    ts1 = np.array([c.timestamp for c in candles_1h])
    resist = [None] * len(candles_1h)
    support = [None] * len(candles_1h)
    step = _round_step(float(daily[-1].close), symbol)
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end_d, decision_t) - 1
        if k < 3:
            continue
        sh_k = [j for j in sh if j <= k - 1]
        sl_k = [j for j in sl if j <= k - 1]
        if sh_k:
            resist[i] = float(hd[sh_k[-1]])
        if sl_k:
            support[i] = float(ld[sl_k[-1]])
        px = float(candles_1h[i].close)
        nr = _nearest_round(px, step)
        if resist[i] is None or abs(nr - px) < abs(resist[i] - px):
            if nr > px:
                resist[i] = nr
        if support[i] is None or abs(nr - px) < abs(support[i] - px):
            if nr < px:
                support[i] = nr
    return resist, support


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 3.0):
    n = len(candles_1h)
    if n < 80:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    resist, support = _htf_levels(candles_1h, symbol)

    trades = []
    traded_days: set[str] = set()
    i = 30
    while i < n - 1:
        day = days[i]
        if day in traded_days or hours[i] < 13 or hours[i] > 16:
            i += 1
            continue
        rng = h[i] - l[i]
        if rng <= 0:
            i += 1
            continue
        body = abs(c[i] - o[i])
        if body < 0.6 * rng:
            i += 1
            continue

        direction = None
        level = None
        r_lvl = resist[i]
        s_lvl = support[i]
        if r_lvl is not None and c[i] > r_lvl and c[i] > o[i]:
            direction, level = "long", r_lvl
        elif s_lvl is not None and c[i] < s_lvl and c[i] < o[i]:
            direction, level = "short", s_lvl
        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = min(float(l[i]), level) - max(2 * cost, 0.15 * rng)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = max(float(h[i]), level) + max(2 * cost, 0.15 * rng)
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
            "setup": "brando_sr_breakout",
            "symbol": symbol or None,
            "reason": f"Brando S/R breakout level={level:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 124: Brando S/R Breakout")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=3.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_124_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
