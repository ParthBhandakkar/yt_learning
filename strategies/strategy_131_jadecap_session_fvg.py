#!/usr/bin/env python3
"""
Strategy 131: JadeCap (Kyle Ng) — Session Liquidity + FVG (mechanical proxy)

Source: JadeCap (Kyle Ng) — ICT intraday liquidity & volatility / Silver Bullet themes
Video: https://www.youtube.com/watch?v=w42kzZb9oYY

MECHANICAL INTERPRETATION (not 1:1 discretionary parity):
  1. Per UTC day: Asian range (hours 0–7) and London range (hours 7–12) highs/lows.
  2. During NY window (UTC 13–16): sweep of session high OR low.
  3. After sweep: first causal bullish/bearish FVG on 1H (3-candle gap) aligned with reversal.
  4. Fill next open after FVG bar closes; stop beyond sweep; TP 2R.
  Max 1 trade per UTC day.

See also strategy_130_jadecap_daily_sweep.py for the separate Daily Sweep / SFP model.

Usage:
  python strategy_131_jadecap_session_fvg.py --csv1h EURUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso


def _session_ranges(days, hours, h, l, day: str):
    asia_hi = asia_lo = lon_hi = lon_lo = None
    for j in range(len(days)):
        if days[j] != day:
            continue
        hr = hours[j]
        if 0 <= hr < 7:
            asia_hi = h[j] if asia_hi is None else max(asia_hi, h[j])
            asia_lo = l[j] if asia_lo is None else min(asia_lo, l[j])
        elif 7 <= hr < 13:
            lon_hi = h[j] if lon_hi is None else max(lon_hi, h[j])
            lon_lo = l[j] if lon_lo is None else min(lon_lo, l[j])
    levels = []
    for val in (asia_hi, asia_lo, lon_hi, lon_lo):
        if val is not None:
            levels.append(float(val))
    return levels


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
    if n < 80:
        return []
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    trades = []
    traded_days: set[str] = set()
    sweep_state: dict[str, dict] = {}

    i = 40
    while i < n - 2:
        day = days[i]
        if hours[i] < 13 or hours[i] > 16:
            i += 1
            continue
        if day in traded_days:
            i += 1
            continue

        levels = _session_ranges(days, hours, h, l, day)
        if not levels:
            i += 1
            continue

        st = sweep_state.get(day)
        if st is None:
            swept_low = any(l[i] < lv and c[i] > lv for lv in levels)
            swept_high = any(h[i] > lv and c[i] < lv for lv in levels)
            if swept_low:
                sweep_state[day] = {"side": "low", "extreme": float(l[i])}
            elif swept_high:
                sweep_state[day] = {"side": "high", "extreme": float(h[i])}
            i += 1
            continue

        fvg = _fvg_at(i, o, h, l, c)
        if fvg is None:
            i += 1
            continue
        if st["side"] == "low" and fvg["dir"] != "bullish":
            i += 1
            continue
        if st["side"] == "high" and fvg["dir"] != "bearish":
            i += 1
            continue

        direction = "long" if fvg["dir"] == "bullish" else "short"
        entry_idx = i + 1
        entry = float(o[entry_idx])
        mid = (fvg["lo"] + fvg["hi"]) / 2.0
        entry = mid if fvg["lo"] <= mid <= fvg["hi"] else entry
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        extreme = st["extreme"]
        if direction == "long":
            stop = min(extreme, fvg["lo"]) - max(2 * cost, (fvg["hi"] - fvg["lo"]) * 0.1)
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = max(extreme, fvg["hi"]) + max(2 * cost, (fvg["hi"] - fvg["lo"]) * 0.1)
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
            "setup": "jadecap_session_fvg",
            "symbol": symbol or None,
            "reason": f"JadeCap session sweep+FVG side={st['side']} RR={rr}",
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 131: JadeCap Session FVG")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_131_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr)


if __name__ == "__main__":
    main()
