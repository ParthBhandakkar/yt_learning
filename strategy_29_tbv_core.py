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
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_fvg, swing_highs, swing_lows,
    save_trades,
)


# ---------------------------------------------------------------------------
# Step 3: Find 3-candle swing fractal near FVG
# ---------------------------------------------------------------------------

def find_swing_near_fvg(
    candles: list[Candle], fvg: dict, lookback: int = 10
) -> Optional[dict]:
    """Find a 3-candle swing high/low near the FVG zone"""
    fvg_mid = (fvg["upper"] + fvg["lower"]) / 2
    start = max(0, fvg["idx"] - lookback)
    end = min(len(candles), fvg["idx"] + 3)

    for i in range(start, end):
        if i < 1 or i >= len(candles) - 1:
            continue
        c = candles[i]
        # Swing low: center candle lower than neighbors
        if c.low < candles[i - 1].low and c.low < candles[i + 1].low:
            # Check if swing is inside or near FVG
            near_fvg = fvg["lower"] - (fvg["upper"] - fvg["lower"]) <= c.low <= fvg["upper"] + (fvg["upper"] - fvg["lower"])
            if near_fvg:
                return {
                    "idx": i,
                    "type": "swing_low",
                    "level": c.low,
                    "strength": "3-candle fractal",
                }
        # Swing high
        if c.high > candles[i - 1].high and c.high > candles[i + 1].high:
            near_fvg = fvg["lower"] - (fvg["upper"] - fvg["lower"]) <= c.high <= fvg["upper"] + (fvg["upper"] - fvg["lower"])
            if near_fvg:
                return {
                    "idx": i,
                    "type": "swing_high",
                    "level": c.high,
                    "strength": "3-candle fractal",
                }
    return None


# ---------------------------------------------------------------------------
# Step 4-5: Absorption sweep detection
# ---------------------------------------------------------------------------

def detect_absorption_sweep(
    candles: list[Candle], swing: dict, fvg: dict, start_idx: int, lookahead: int = 15
) -> Optional[dict]:
    """
    The first candle that sweeps the swing level must close as:
    - Bullish (close > open) for a swing low sweep (long)
    - Bearish (close < open) for a swing high sweep (short)
    """
    swing_level = swing["level"]
    swing_type = swing["type"]

    for i in range(start_idx, min(start_idx + lookahead, len(candles))):
        c = candles[i]

        # Check sweep
        if swing_type == "swing_low" and c.low < swing_level:
            # Must also touch the FVG
            touches_fvg = (fvg["lower"] <= c.high and c.low <= fvg["upper"])
            if touches_fvg and c.close > c.open:
                return {
                    "entry_idx": i,
                    "entry_price": c.close,
                    "type": "long",
                    "swept_level": swing_level,
                    "description": (
                        f"Bullish absorption: candle swept swing low {swing_level:.5f} "
                        f"inside FVG, closed bullish at {c.close:.5f}"
                    ),
                }
        elif swing_type == "swing_high" and c.high > swing_level:
            touches_fvg = (fvg["lower"] <= c.high and c.low <= fvg["upper"])
            if touches_fvg and c.close < c.open:
                return {
                    "entry_idx": i,
                    "entry_price": c.close,
                    "type": "short",
                    "swept_level": swing_level,
                    "description": (
                        f"Bearish absorption: candle swept swing high {swing_level:.5f} "
                        f"inside FVG, closed bearish at {c.close:.5f}"
                    ),
                }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles: list[Candle], output_path: str):
    trades = []

    # Strategy works on 3m/5m/15m/1h/4h/Daily
    for i in range(20, len(candles) - 5):
        chunk = candles[max(0, i - 15):i + 3]
        events_log = []

        # Step 2: Find same-timeframe FVG
        fvgs = detect_fvg(chunk)
        if not fvgs:
            continue

        fvg = fvgs[-1]
        fvg["idx"] += max(0, i - 15)  # re-base index

        # Step 3: Find swing near FVG
        swing = find_swing_near_fvg(candles, fvg, lookback=10)
        if swing is None:
            continue

        events_log.append({
            "timestamp": to_iso(candles[fvg["idx"]].timestamp),
            "type": "fvg_mapped",
            "upper": round(fvg["upper"], 5),
            "lower": round(fvg["lower"], 5),
            "direction": fvg["direction"],
            "description": f"{fvg['direction']} FVG: {fvg['lower']:.5f}-{fvg['upper']:.5f}",
        })

        events_log.append({
            "timestamp": to_iso(candles[swing["idx"]].timestamp),
            "type": "swing_located",
            "swing_type": swing["type"],
            "level": round(swing["level"], 5),
            "description": f"3-candle {swing['type']} at {swing['level']:.5f}",
        })

        # Step 4-5: Wait for absorption sweep
        result = detect_absorption_sweep(candles, swing, fvg, fvg["idx"] + 1)
        if result is None:
            continue

        entry_c = candles[result["entry_idx"]]
        events_log.append({
            "timestamp": to_iso(entry_c.timestamp),
            "type": "absorption_sweep",
            "direction": result["type"],
            "description": result["description"],
        })

        trade_dir = result["type"]
        if trade_dir == "long":
            sl = entry_c.low - (entry_c.low * 0.0005)
        else:
            sl = entry_c.high + (entry_c.high * 0.0005)

        risk = abs(result["entry_price"] - sl)
        tp = result["entry_price"] + (2 * risk) if trade_dir == "long" else result["entry_price"] - (2 * risk)

        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(entry_c.timestamp),
            "direction": trade_dir,
            "entry_price": round(result["entry_price"], 5),
            "stop_loss": round(sl, 5),
            "take_profit": round(tp, 5),
            "reason": f"TBV: FVG + swing sweep + absorption close",
            "events": list(events_log),
        }
        trades.append(trade)

        # Exit check
        entry_ts = entry_c.timestamp
        for c in candles:
            if c.timestamp > entry_ts:
                if trade_dir == "long":
                    if c.high >= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.low <= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                else:
                    if c.low <= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.high >= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
        if "exit_time" not in trade:
            trade["exit_time"] = to_iso(candles[-1].timestamp)
            trade["exit_price"] = candles[-1].close
            trade["outcome"] = "open"

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
