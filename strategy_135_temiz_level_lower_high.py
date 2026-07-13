#!/usr/bin/env python3
"""
Strategy 135: Alex Temiz (AT09) — Key Level + Lower High (mechanical proxy)

Source: Alex Temiz — staged short at resistance with lower-high confirmation (SMB / AT09)
Video: https://www.youtube.com/watch?v=rOxJsfFch2Y

MECHANICAL INTERPRETATION (FX/gold/BTC proxy — not equity tape/VWAP):
  1. Daily swing high as resistance (confirmed +1 bar).
  2. Price tags resistance within 0.25*ATR; first rejection bar (bearish close).
  3. Lower high forms within next 6 bars → fill next open short.
  4. Stop above resistance + sweep; TP 2R. Proxy for VWAP reclaim failure on 1H.

See also strategy_134_temiz_first_red_day.py for extension fade model.

Usage:
  python strategy_135_temiz_level_lower_high.py --csv1h XAUUSD_1h.csv [--output out.json]
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


def _swings(h, l):
    sh, sl = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl.append(i)
    return sh, sl


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
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    sh, _ = _swings(h, l)

    trades = []
    i = 30
    while i < n - 8:
        last_sh = next((j for j in reversed(sh) if j <= i - 2 and days[j] < days[i]), None)
        if last_sh is None:
            i += 1
            continue
        res = float(h[last_sh])
        a = float(atr[i])
        if not (abs(h[i] - res) <= 0.25 * a or (l[i] <= res <= h[i])):
            i += 1
            continue
        if c[i] >= o[i]:
            i += 1
            continue

        peak = float(h[i])
        lh_idx = None
        for j in range(i + 1, min(i + 7, n - 1)):
            if h[j] > peak:
                peak = float(h[j])
            if h[j] < peak and c[j] < o[j]:
                lh_idx = j
                break
        if lh_idx is None:
            i += 1
            continue

        entry_idx = lh_idx + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        stop = max(res, peak) + max(2 * cost, 0.1 * a)
        risk = stop - entry
        if risk <= 0:
            i += 1
            continue
        tp = entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, n):
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
            "direction": "short",
            "entry_price": round(entry, 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(tp), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "setup": "temiz_resistance_lh",
            "symbol": symbol or None,
            "reason": f"Temiz resistance lower-high RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 135: Temiz Level Lower High")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_135_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
