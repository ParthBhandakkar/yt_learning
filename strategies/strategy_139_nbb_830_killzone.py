#!/usr/bin/env python3
"""
Strategy 139: Omor NBB Trader — 8:30 NY Kill Zone FVG (mechanical proxy)

Source: Omor (NBB Trader) — 8:30 AM EST liquidity injection + ICT FVG entry
Video: https://www.youtube.com/watch?v=LF1VTFohH0U

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. Weekly/daily bias: prior-day close vs PD open (bullish/bearish day).
  2. Kill-zone window: UTC hours 12–14 (proxy for 8:30 AM EST/EDT on 1H bars).
  3. Sweep PDH or PDL, then displacement FVG aligned with bias.
  4. Entry at FVG midpoint on next bar; stop beyond sweep; TP 2R.
  Max 1 trade per UTC day.

Usage:
  python strategy_139_nbb_830_killzone.py --csv1h GBPUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def _fvg_at(i, o, h, l, c):
    if i < 1 or i >= len(c) - 1:
        return None
    if c[i - 1] > o[i - 1] and l[i + 1] > h[i - 1]:
        return {"dir": "bullish", "lo": float(h[i - 1]), "hi": float(l[i + 1])}
    if c[i - 1] < o[i - 1] and h[i + 1] < l[i - 1]:
        return {"dir": "bearish", "lo": float(h[i + 1]), "hi": float(l[i - 1])}
    return None


def generate_trades(candles_1h, *, symbol: str = "", rr: float = 2.0):
    n = len(candles_1h)
    if n < 50:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    day_hl: dict[str, tuple[float, float, float, float]] = {}
    cur = days[0]
    dhi, dlo, dopen = float(h[0]), float(l[0]), float(o[0])
    dclose = float(c[0])
    for i in range(n):
        if days[i] != cur:
            day_hl[cur] = (dhi, dlo, dopen, dclose)
            cur = days[i]
            dhi, dlo, dopen = float(h[i]), float(l[i]), float(o[i])
            dclose = float(c[i])
        else:
            dhi = max(dhi, float(h[i]))
            dlo = min(dlo, float(l[i]))
            dclose = float(c[i])
    day_hl[cur] = (dhi, dlo, dopen, dclose)

    ordered = []
    seen = set()
    for d in days:
        if d not in seen:
            ordered.append(d)
            seen.add(d)
    prev_of = {ordered[i]: ordered[i - 1] for i in range(1, len(ordered))}

    trades = []
    traded_days: set[str] = set()
    i = 5
    while i < n - 2:
        day = days[i]
        if day in traded_days or day not in prev_of:
            i += 1
            continue
        if hours[i] < 12 or hours[i] > 14:
            i += 1
            continue

        pdh, pdl, pdo, pdc = day_hl[prev_of[day]]
        bias = 1 if pdc > pdo else -1 if pdc < pdo else 0
        if bias == 0:
            i += 1
            continue

        swept = (bias == 1 and l[i] < pdl) or (bias == -1 and h[i] > pdh)
        if not swept:
            i += 1
            continue

        fvg = _fvg_at(i, o, h, l, c)
        if fvg is None:
            i += 1
            continue
        if bias == 1 and fvg["dir"] != "bullish":
            i += 1
            continue
        if bias == -1 and fvg["dir"] != "bearish":
            i += 1
            continue

        direction = "long" if bias == 1 else "short"
        entry_idx = i + 1
        entry = (fvg["lo"] + fvg["hi"]) / 2.0
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        extreme = float(l[i]) if direction == "long" else float(h[i])
        pad = max(2 * cost, (fvg["hi"] - fvg["lo"]) * 0.15)
        if direction == "long":
            stop = min(extreme, fvg["lo"]) - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = max(extreme, fvg["hi"]) + pad
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
            "setup": "nbb_830_fvg",
            "symbol": symbol or None,
            "reason": f"NBB 8:30 killzone bias={bias} FVG RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 139: NBB 8:30 Kill Zone")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_139_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
