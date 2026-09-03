#!/usr/bin/env python3
"""
Strategy 100: TopG — Market Structure + Supply/Demand (mechanical proxy)

Source: TopG Traders (Atul Shendge) — structure / BOS / CHOCH / S&D course themes
Video: https://www.youtube.com/playlist?list=PLwdM5wWQGYyD46U5NBDjOuSV7PyEUJQ81

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. On completed 4H bars: mark swing highs/lows (confirmed after +1 bar).
  2. BOS = close beyond prior swing in trend direction; CHOCH = close beyond
     prior swing against prior bias (flip).
  3. Demand/supply zone = last opposing 4H candle before the impulse that
     caused BOS/CHOCH (order-block style).
  4. On 1H: tap into active zone + rejection close → fill next 1H open.
  5. Stop beyond zone; take-profit 2R.

Usage:
  python strategy_100_topg_structure_sd.py --csv1h EURUSD_1h.csv [--output out.json]
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
    if len(bars4) < 30 or len(candles_1h) < 80:
        return []

    h4 = np.array([c.high for c in bars4])
    l4 = np.array([c.low for c in bars4])
    o4 = np.array([c.open for c in bars4])
    c4 = np.array([c.close for c in bars4])
    end4 = [c.timestamp + 240 * 60 for c in bars4]
    sh, sl = _swings(h4, l4)

    # Walk 4H causally: bias + active zone after each completed bar k
    # zone: (lo, hi, direction) active until invalidated or traded
    zones = [None] * len(bars4)
    bias = 0
    last_sh = last_sl = None
    active = None  # {dir, zlo, zhi, born_end}

    for k in range(2, len(bars4)):
        # swings confirmed only when bar k-1 is the swing (needs k closed)
        if (k - 1) in sh:
            last_sh = k - 1
        if (k - 1) in sl:
            last_sl = k - 1

        if last_sh is None or last_sl is None:
            zones[k] = active
            continue

        # Structure events on close of k
        if bias >= 0 and c4[k] > h4[last_sh]:
            # bullish BOS or CHOCH
            # OB = last bearish candle before k in [last_sh, k]
            ob = None
            for j in range(k - 1, max(last_sh - 1, 0), -1):
                if c4[j] < o4[j]:
                    ob = j
                    break
            if ob is not None:
                active = {
                    "dir": "long",
                    "zlo": float(l4[ob]),
                    "zhi": float(max(o4[ob], c4[ob])),
                    "born_end": end4[k],
                }
            bias = 1
            last_sh = k  # update structure
        elif bias <= 0 and c4[k] < l4[last_sl]:
            ob = None
            for j in range(k - 1, max(last_sl - 1, 0), -1):
                if c4[j] > o4[j]:
                    ob = j
                    break
            if ob is not None:
                active = {
                    "dir": "short",
                    "zlo": float(min(o4[ob], c4[ob])),
                    "zhi": float(h4[ob]),
                    "born_end": end4[k],
                }
            bias = -1
            last_sl = k
        zones[k] = active

    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)

    trades = []
    i = 30
    used_zone_birth = set()
    while i < len(candles_1h) - 1:
        # Latest completed 4H at end of bar i
        decision_t = int(ts[i]) + 3600
        k = bisect.bisect_right(end4, decision_t) - 1
        if k < 2 or k >= len(zones) or zones[k] is None:
            i += 1
            continue
        z = zones[k]
        birth = z["born_end"]
        if birth in used_zone_birth:
            i += 1
            continue
        # Zone must already exist before this 1H bar (no peek)
        if birth > int(ts[i]):
            i += 1
            continue

        direction = None
        if z["dir"] == "long" and l[i] <= z["zhi"] and c[i] > z["zlo"] and c[i] > o[i]:
            if c[i] >= z["zlo"]:
                direction = "long"
        elif z["dir"] == "short" and h[i] >= z["zlo"] and c[i] < z["zhi"] and c[i] < o[i]:
            direction = "short"

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = min(z["zlo"], entry) - max(4 * cost, abs(z["zhi"] - z["zlo"]) * 0.1)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = max(z["zhi"], entry) + max(4 * cost, abs(z["zhi"] - z["zlo"]) * 0.1)
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

        used_zone_birth.add(birth)
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
            "setup": "topg_bos_sd",
            "symbol": symbol or None,
            "reason": f"TopG structure zone {z['zlo']:.5f}-{z['zhi']:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 100: TopG Structure S/D")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_100_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
