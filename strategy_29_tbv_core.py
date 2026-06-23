#!/usr/bin/env python3
"""
Strategy 29: Time-Based Volume (TBV) Core Strategy

Source: Faiz SMC - "Give Me 9 Minutes & I'll Teach You My 80% Winrate Trading Strategy"
Video: https://www.youtube.com/watch?v=6HMG-NO2h_A

Core concepts:
  - Works on 3m timeframe and above (NO 1m/2m)
  - Same-timeframe FVG mapping
  - 3-candle swing high/low fractal near FVG
  - Absorption sweep: first candle that sweeps the swing point
    must close as bullish (for long) or bearish (for short)
  - Entry at close of the sweeping candle

Usage:
  python strategy_29_tbv_core.py --csv 3m_data.csv [--output results.json]

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  At loop index i, the code builds chunk = candles[i-15:i+3] and runs FVG/swing
  detection on it. Bars i+1 and i+2 are in the future when you are deciding at
  bar i — so swings and gaps are confirmed using data you should not have yet.
  The sliding window also creates many overlapping signals on the same move.

HOW TO FIX:
  1. At bar i, only use candles[0:i+1] (or [i-15:i+1] at most).
  2. Confirm 3-candle swings only after bar i+1 has closed (act at i+2 earliest).
  3. Enter on the bar after the sweep candle closes, not the same bar if the
     sweep is only known after close.
  4. Deduplicate trades so one sweep does not fire many overlapping entries.

FIXED: past_slice/detect_fvg_as_of at bar i; swings require i+1 closed; absorption
sweep on causal data; simulate_exits; skip overlapping entries within 10 bars.
"""

import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import Candle, load_csv, to_iso, parse_csv_filename, save_trades
from causal_backtest import past_slice, detect_fvg_as_of, simulate_exits


def find_swing_near_fvg(
    candles: list[Candle], fvg: dict, as_of_idx: int, lookback: int = 10
) -> Optional[dict]:
    """3-candle swing fractal near FVG, confirmed only when bar i+1 has closed."""
    start = max(0, fvg["idx"] - lookback)
    for i in range(start, min(as_of_idx, len(candles) - 2)):
        if i < 1 or i + 1 > as_of_idx:
            continue
        c = candles[i]
        if c.low < candles[i - 1].low and c.low < candles[i + 1].low:
            near_fvg = (
                fvg["lower"] - (fvg["upper"] - fvg["lower"])
                <= c.low
                <= fvg["upper"] + (fvg["upper"] - fvg["lower"])
            )
            if near_fvg:
                return {"idx": i, "type": "swing_low", "level": c.low, "strength": "3-candle fractal"}
        if c.high > candles[i - 1].high and c.high > candles[i + 1].high:
            near_fvg = (
                fvg["lower"] - (fvg["upper"] - fvg["lower"])
                <= c.high
                <= fvg["upper"] + (fvg["upper"] - fvg["lower"])
            )
            if near_fvg:
                return {"idx": i, "type": "swing_high", "level": c.high, "strength": "3-candle fractal"}
    return None


def detect_absorption_sweep(
    candles: list[Candle], swing: dict, fvg: dict, start_idx: int, lookahead: int = 15
) -> Optional[dict]:
    swing_level = swing["level"]
    swing_type = swing["type"]

    for i in range(start_idx, min(start_idx + lookahead, len(candles))):
        c = candles[i]
        if swing_type == "swing_low" and c.low < swing_level:
            touches_fvg = fvg["lower"] <= c.high and c.low <= fvg["upper"]
            if touches_fvg and c.close > c.open:
                return {
                    "entry_idx": i,
                    "entry_price": c.close,
                    "type": "long",
                    "swept_level": swing_level,
                    "description": (
                        f"Bullish absorption: swept swing low {swing_level:.5f} "
                        f"inside FVG, closed bullish at {c.close:.5f}"
                    ),
                }
        elif swing_type == "swing_high" and c.high > swing_level:
            touches_fvg = fvg["lower"] <= c.high and c.low <= fvg["upper"]
            if touches_fvg and c.close < c.open:
                return {
                    "entry_idx": i,
                    "entry_price": c.close,
                    "type": "short",
                    "swept_level": swing_level,
                    "description": (
                        f"Bearish absorption: swept swing high {swing_level:.5f} "
                        f"inside FVG, closed bearish at {c.close:.5f}"
                    ),
                }
    return None


def run_strategy(candles: list[Candle], output_path: str):
    trades = []
    last_entry_idx = -100

    for i in range(20, len(candles) - 5):
        if i - last_entry_idx < 10:
            continue

        sub = past_slice(candles, i)
        fvgs = detect_fvg_as_of(sub, i)
        if not fvgs:
            continue

        fvg = fvgs[-1]
        swing = find_swing_near_fvg(candles, fvg, i, lookback=10)
        if swing is None:
            continue

        result = detect_absorption_sweep(candles, swing, fvg, max(fvg["idx"] + 1, swing["idx"] + 1))
        if result is None or result["entry_idx"] > i:
            continue

        entry_idx = result["entry_idx"]
        if entry_idx - last_entry_idx < 10:
            continue

        events_log = [
            {
                "timestamp": to_iso(candles[fvg["idx"]].timestamp),
                "type": "fvg_mapped",
                "upper": round(fvg["upper"], 5),
                "lower": round(fvg["lower"], 5),
                "direction": fvg["direction"],
                "description": f"{fvg['direction']} FVG: {fvg['lower']:.5f}-{fvg['upper']:.5f}",
            },
            {
                "timestamp": to_iso(candles[swing["idx"]].timestamp),
                "type": "swing_located",
                "swing_type": swing["type"],
                "level": round(swing["level"], 5),
                "description": f"3-candle {swing['type']} at {swing['level']:.5f}",
            },
            {
                "timestamp": to_iso(candles[entry_idx].timestamp),
                "type": "absorption_sweep",
                "direction": result["type"],
                "description": result["description"],
            },
        ]

        entry_c = candles[entry_idx]
        trade_dir = result["type"]
        if trade_dir == "long":
            sl = entry_c.low - (entry_c.low * 0.0005)
        else:
            sl = entry_c.high + (entry_c.high * 0.0005)

        risk = abs(result["entry_price"] - sl)
        tp = (
            result["entry_price"] + (2 * risk)
            if trade_dir == "long"
            else result["entry_price"] - (2 * risk)
        )

        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(entry_c.timestamp),
            "direction": trade_dir,
            "entry_price": round(result["entry_price"], 5),
            "stop_loss": round(sl, 5),
            "take_profit": round(tp, 5),
            "reason": "TBV: FVG + swing sweep + absorption close",
            "events": events_log,
        }
        exit_info = simulate_exits(
            candles, entry_idx, entry_c.timestamp, trade_dir, sl, tp,
        )
        trade.update(exit_info)
        trades.append(trade)
        last_entry_idx = entry_idx

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 29: Time-Based Volume (TBV) Core")
    parser.add_argument("--csv", required=True, help="OHLCV CSV (3m/5m/15m+)")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)
    output = args.output or f"strategy_29_results_{meta['symbol']}_{meta['timeframe']}.json"
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
