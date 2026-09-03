#!/usr/bin/env python3
"""
Strategy 121: Fabio Valentini — AMT Failed-Breakout Mean Reversion (mechanical proxy)

Source: Fabio Valentini (Fabervaale) — Auction Market Theory / balance vs imbalance
Video: https://www.youtube.com/watch?v=tvERE-Beu2U

SEPARATE from strategy_120 (IVB ORB breakout).
MECHANICAL INTERPRETATION:
  1. 4H balance range = high/low of last 12 completed 4H bars (value area proxy).
  2. Imbalance: close beyond range; failure = within 3 bars close back inside.
  3. Fade toward range midpoint (POC proxy); fill next 1H open.
  4. SL beyond failure extreme; TP at midpoint or 2R (whichever closer in R).

Usage:
  python strategy_121_fabio_amt_meanrev.py --csv1h XAUUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, resample, round_turn_cost_price, save_trades, to_iso


def generate_trades(candles_1h, *, symbol: str = "", rr_cap: float = 2.0, lookback: int = 12):
    bars4 = resample(candles_1h, 240)
    if len(bars4) < lookback + 5 or len(candles_1h) < 80:
        return []

    h4 = np.array([c.high for c in bars4], dtype=np.float64)
    l4 = np.array([c.low for c in bars4], dtype=np.float64)
    c4 = np.array([c.close for c in bars4], dtype=np.float64)
    end4 = [c.timestamp + 240 * 60 for c in bars4]

    # Causal events on 4H close: (end_ts, direction, mid, extreme, zhi, zlo)
    events: list[tuple[int, str, float, float, float, float]] = []
    for k in range(lookback, len(bars4) - 1):
        window = slice(k - lookback, k)
        zhi = float(np.max(h4[window]))
        zlo = float(np.min(l4[window]))
        mid = (zhi + zlo) / 2.0
        if zhi <= zlo:
            continue
        broke_up = c4[k] > zhi
        broke_dn = c4[k] < zlo
        if not broke_up and not broke_dn:
            continue
        failed = False
        extreme = float(h4[k]) if broke_up else float(l4[k])
        for j in range(k + 1, min(k + 4, len(bars4))):
            if broke_up and c4[j] < zhi:
                events.append((end4[j], "short", mid, extreme, zhi, zlo))
                failed = True
                break
            if broke_dn and c4[j] > zlo:
                events.append((end4[j], "long", mid, extreme, zhi, zlo))
                failed = True
                break
        if not failed:
            continue

    if not events:
        return []

    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    event_ends = [e[0] for e in events]

    trades = []
    used: set[int] = set()
    i = 30
    while i < len(candles_1h) - 1:
        decision_t = int(ts[i]) + 3600
        ei = bisect.bisect_right(event_ends, decision_t) - 1
        if ei < 0:
            i += 1
            continue
        end_ts, direction, mid, extreme, zhi, zlo = events[ei]
        if end_ts in used or end_ts > int(ts[i]):
            i += 1
            continue

        entry_idx = i + 1
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if direction == "long":
            stop = extreme - max(2 * cost, 0.1 * (zhi - zlo))
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp_mid = mid
            tp_rr = entry + rr_cap * risk
            tp = min(tp_mid, tp_rr) if entry < mid else tp_rr
        else:
            stop = extreme + max(2 * cost, 0.1 * (zhi - zlo))
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp_mid = mid
            tp_rr = entry - rr_cap * risk
            tp = max(tp_mid, tp_rr) if entry > mid else tp_rr

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

        used.add(end_ts)
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
            "setup": "fabio_amt_meanrev",
            "symbol": symbol or None,
            "reason": f"Fabio AMT fail-revert POC={mid:.5f} zone={zlo:.5f}-{zhi:.5f}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 121: Fabio AMT Mean Reversion")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0, dest="rr_cap")
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_121_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr_cap=args.rr_cap)


if __name__ == "__main__":
    main()
