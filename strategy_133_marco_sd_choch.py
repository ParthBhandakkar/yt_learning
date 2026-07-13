#!/usr/bin/env python3
"""
Strategy 133: Marco Trades — Impulse Zone + CHOCH Retest (mechanical proxy)

Source: Marco Trades — liquidity playbook extended with structure shift + zone retest
Video: https://www.youtube.com/watch?v=DAnXM7C16h0

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. Impulse leg: 3+ consecutive same-direction 1H bodies with total range >= 1.5*ATR.
  2. Origin zone = last opposing candle before impulse (order-block style).
  3. Liquidity sweep of prior swing in impulse direction, then MSS/CHOCH on 1H close.
  4. Retest into origin zone + rejection close → fill next open; SL beyond zone; TP 2R.

See also strategy_132_marco_liquidity_trap.py for pure trap reversal without zone retest.

Usage:
  python strategy_133_marco_sd_choch.py --csv1h EURUSD_1h.csv [--output out.json]
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
    if n < 80:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    atr = _atr(h, l, c)
    sh, sl = _swings(h, l)

    trades = []
    i = 30
    while i < n - 1:
        impulse_dir = None
        zlo = zhi = None
        origin = None
        for k in range(i - 8, i - 2):
            if k < 3:
                continue
            bull_run = all(c[j] > o[j] for j in range(k, k + 3))
            bear_run = all(c[j] < o[j] for j in range(k, k + 3))
            span = float(h[k + 2] - l[k])
            if span < 1.5 * float(atr[k + 2]):
                continue
            if bull_run:
                for j in range(k - 1, max(k - 6, 0), -1):
                    if c[j] < o[j]:
                        origin = j
                        break
                if origin is not None:
                    impulse_dir = "long"
                    zlo, zhi = float(l[origin]), float(max(o[origin], c[origin]))
                    break
            if bear_run:
                for j in range(k - 1, max(k - 6, 0), -1):
                    if c[j] > o[j]:
                        origin = j
                        break
                if origin is not None:
                    impulse_dir = "short"
                    zlo, zhi = float(min(o[origin], c[origin])), float(h[origin])
                    break

        if impulse_dir is None or origin is None:
            i += 1
            continue

        last_sh = next((j for j in reversed(sh) if j < i - 1), None)
        last_sl = next((j for j in reversed(sl) if j < i - 1), None)
        choch = False
        if impulse_dir == "long" and last_sl is not None and l[i] < l[last_sl] and c[i] > l[last_sl]:
            choch = True
        if impulse_dir == "short" and last_sh is not None and h[i] > h[last_sh] and c[i] < h[last_sh]:
            choch = True
        if not choch:
            i += 1
            continue

        direction = impulse_dir
        in_zone = l[i] <= zhi and h[i] >= zlo
        reject = (direction == "long" and c[i] > zlo and c[i] > o[i]) or (
            direction == "short" and c[i] < zhi and c[i] < o[i]
        )
        if not (in_zone and reject):
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(2 * cost, 0.1 * float(atr[i]))
        if direction == "long":
            stop = zlo - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = zhi + pad
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
            "setup": "marco_impulse_choch",
            "symbol": symbol or None,
            "reason": f"Marco impulse zone CHOCH retest RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 133: Marco S/D CHOCH")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_133_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
