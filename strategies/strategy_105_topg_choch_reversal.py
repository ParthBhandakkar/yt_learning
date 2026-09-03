#!/usr/bin/env python3
"""
Strategy 105: TopG — CHOCH Reversal at HTF Zone (mechanical proxy)

Source: TopG Traders (Atul Shendge) — structure / CHOCH themes
Video: https://www.youtube.com/playlist?list=PLwdM5wWQGYyD46U5NBDjOuSV7PyEUJQ81

SEPARATE from strategy_100 (BOS continuation + S/D tap).
This file focuses on *reversal* CHOCH: prior bias flips when price closes
beyond the last opposing swing on 4H, then 1H rejection at the break level.

Usage:
  python strategy_105_topg_choch_reversal.py --csv1h EURUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys

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


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
    bars4 = resample(candles_1h, 240)
    if len(bars4) < 40 or len(candles_1h) < 80:
        return []
    h4 = np.array([c.high for c in bars4])
    l4 = np.array([c.low for c in bars4])
    c4 = np.array([c.close for c in bars4])
    end4 = [c.timestamp + 240 * 60 for c in bars4]
    sh, sl = _swings(h4, l4)

    # Event list: (end_ts, direction, level, extreme)
    events = []
    bias = 0
    last_sh = last_sl = None
    for k in range(2, len(bars4)):
        if (k - 1) in sh:
            last_sh = k - 1
        if (k - 1) in sl:
            last_sl = k - 1
        if last_sh is None or last_sl is None:
            continue
        # Bullish CHOCH: was bearish/flat, close breaks last swing high
        if bias <= 0 and c4[k] > h4[last_sh]:
            events.append((end4[k], "long", float(h4[last_sh]), float(l4[k])))
            bias = 1
            last_sh = k
        elif bias >= 0 and c4[k] < l4[last_sl]:
            events.append((end4[k], "short", float(l4[last_sl]), float(h4[k])))
            bias = -1
            last_sl = k

    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)

    trades = []
    used = set()
    i = 30
    while i < len(candles_1h) - 1:
        decision_t = int(ts[i]) + 3600
        # Latest CHOCH whose end is <= decision_t
        ev = None
        for e in reversed(events):
            if e[0] <= decision_t:
                ev = e
                break
        if ev is None or ev[0] in used:
            i += 1
            continue
        direction, level, extreme = ev[1], ev[2], ev[3]
        # 1H rejection at CHOCH level
        ok = False
        if direction == "long" and l[i] <= level and c[i] > level and c[i] > o[i]:
            ok = True
        elif direction == "short" and h[i] >= level and c[i] < level and c[i] < o[i]:
            ok = True
        if not ok:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = min(extreme, entry) - 2 * cost
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = max(extreme, entry) + 2 * cost
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, len(candles_1h)):
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
            exit_idx = len(candles_1h) - 1
            exit_price = float(c[exit_idx])
        used.add(ev[0])
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
            "setup": "topg_choch_rev",
            "symbol": symbol or None,
            "reason": f"TopG CHOCH reversal @ {level:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 105: TopG CHOCH Reversal")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_105_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
