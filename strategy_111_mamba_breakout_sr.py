#!/usr/bin/env python3
"""
Strategy 111: MambaFX — 5m S/R Breakout Scalp (mechanical proxy on 1H)

Source: MambaFX (Anthony) — 5m direction + 1m breakout entries
Video: https://www.youtube.com/watch?v=h-Z7CEqBO3s

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. Rolling 48-bar window on 1H ≈ 5m multi-touch S/R (3+ taps within 0.12*ATR).
  2. Bias from last bounce: support holds → long-only breaks; resistance → short.
  3. Breakout bar: close beyond level + range >= 1.15*ATR (volume expansion proxy).
  4. Fill next 1H open; stop beyond level; take-profit 2R.

Usage:
  python strategy_111_mamba_breakout_sr.py --csv1h EURUSD_1h.csv [--output out.json]
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


def _level_from_window(h, l, c, start: int, end: int, a: float):
    """Return (level, 'support'|'resistance') if 3+ taps found, else None."""
    band = max(a * 0.12, 1e-9)
    lows = l[start:end]
    highs = h[start:end]
    closes = c[start:end]

    # Support cluster near window low
    wlo = float(np.min(lows))
    taps_lo = sum(1 for x in lows if abs(x - wlo) <= band)
    if taps_lo >= 3 and closes[-1] > wlo:
        return wlo, "support"

    whi = float(np.max(highs))
    taps_hi = sum(1 for x in highs if abs(x - whi) <= band)
    if taps_hi >= 3 and closes[-1] < whi:
        return whi, "resistance"
    return None


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0, lookback: int = 48):
    n = len(candles_1h)
    if n < lookback + 20:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)

    trades = []
    i = lookback
    while i < n - 1:
        a = float(atr[i])
        if a <= 0:
            i += 1
            continue
        lvl_info = _level_from_window(h, l, c, i - lookback, i, a)
        if lvl_info is None:
            i += 1
            continue
        level, kind = lvl_info
        bar_rng = h[i] - l[i]
        if bar_rng < 1.15 * a:
            i += 1
            continue

        direction = None
        if kind == "support" and c[i] > level and c[i] > o[i]:
            direction = "long"
        elif kind == "resistance" and c[i] < level and c[i] < o[i]:
            direction = "short"
        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(0.08 * a, 2 * cost)
        if direction == "long":
            stop = level - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = level + pad
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
            "setup": "mamba_sr_breakout",
            "symbol": symbol or None,
            "reason": f"MambaFX {kind} break@{level:.5f} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 111: MambaFX S/R Breakout")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_111_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
