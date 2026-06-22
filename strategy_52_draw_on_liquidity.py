#!/usr/bin/env python3
"""
Strategy 52: Finding The Correct Draw On Liquidity

Source: Faiz SMC - "Finding The Correct Draw On Liquidity With 96% Accuracy"
Video: http://www.youtube.com/watch?v=vT_xPTnsZ5s

Core concepts:
  - 15m session range (London session high/low)
  - Wait for sweep of session high or low
  - Apply Fibonacci 0.79 retracement level
  - 1m candle close past 0.79 level confirms direction
  - Entry at extreme FVG within dealing range
  - Target opposite session liquidity point

Usage:
  python strategy_52_draw_on_liquidity.py --csv15m 15m_data.csv --csv1m 1m_data.csv
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
    resample, save_trades,
)


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


# ---------------------------------------------------------------------------
# Fib 0.79
# ---------------------------------------------------------------------------

FIB_079 = 0.79


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_15m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []

    # Process each day
    days_15m: list[list[Candle]] = []
    cur: list[Candle] = []
    cur_date = None
    for c in candles_15m:
        d = (datetime.fromtimestamp(c.timestamp, tz=timezone.utc) - timedelta(hours=4)).date()
        if cur_date is None: cur_date = d
        if d != cur_date:
            if cur: days_15m.append(cur)
            cur = []; cur_date = d
        cur.append(c)
    if cur: days_15m.append(cur)

    from datetime import timedelta

    for day_candles_15m in days_15m:
        if len(day_candles_15m) < 12:
            continue

        # Step 1: Define London session (03:00-07:00 NY) range
        london = [c for c in day_candles_15m if 3 <= ny_hour(c.timestamp) < 7]
        if len(london) < 4:
            continue

        session_high = max(c.high for c in london)
        session_low = min(c.low for c in london)

        events_log = [{
            "timestamp": to_iso(london[0].timestamp),
            "type": "session_range",
            "high": round(session_high, 5),
            "low": round(session_low, 5),
            "description": f"London session range: high={session_high:.5f}, low={session_low:.5f}",
        }]

        # Step 2: Wait for sweep
        sweep_idx = None
        sweep_dir = None
        london_end_idx = next((i for i, c in enumerate(day_candles_15m) if c.timestamp >= london[-1].timestamp), len(day_candles_15m) - 1)

        for i in range(london_end_idx, len(day_candles_15m)):
            c = day_candles_15m[i]
            if c.high > session_high:
                sweep_idx = i
                sweep_dir = "high_swept"
                break
            if c.low < session_low:
                sweep_idx = i
                sweep_dir = "low_swept"
                break

        if sweep_idx is None:
            continue

        events_log.append({
            "timestamp": to_iso(day_candles_15m[sweep_idx].timestamp),
            "type": "session_sweep",
            "direction": sweep_dir,
            "description": f"Session {'high' if 'high' in sweep_dir else 'low'} swept",
        })

        # Step 2: Apply Fib 0.79
        if sweep_dir == "high_swept":
            fib_level = session_high - (session_high - session_low) * FIB_079
            expected_direction = "short"
            target = session_low
        else:
            fib_level = session_low + (session_high - session_low) * FIB_079
            expected_direction = "long"
            target = session_high

        events_log.append({
            "timestamp": to_iso(day_candles_15m[sweep_idx].timestamp),
            "type": "fib_079_level",
            "level": round(fib_level, 5),
            "direction": expected_direction,
            "description": f"0.79 Fib level: {fib_level:.5f}, expecting {expected_direction} to {target:.5f}",
        })

        # Step 3: Check 1m for close past 0.79 + FVG entry
        sweep_ts = day_candles_15m[sweep_idx].timestamp
        start_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= sweep_ts), 0)

        for i in range(start_1m, min(start_1m + 60, len(candles_1m))):
            c = candles_1m[i]

            if expected_direction == "long" and c.close > fib_level:
                events_log.append({
                    "timestamp": to_iso(c.timestamp),
                    "type": "fib_confirmation",
                    "description": f"1m candle closed {c.close:.5f} above 0.79 Fib {fib_level:.5f}",
                })
                # Find FVG entry
                fvgs = detect_fvg(candles_1m[max(0, i - 5):i + 5])
                for fvg in fvgs:
                    entry_c = candles_1m[i]
                    sl = min(x.low for x in candles_1m[max(0, i - 3):i + 3]) - 0.0005
                    risk = abs(c.close - sl)
                    tp = c.close + (1.5 * risk)

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(entry_c.timestamp),
                        "direction": "long",
                        "entry_price": round(c.close, 5),
                        "stop_loss": round(sl, 5),
                        "take_profit": round(tp, 5),
                        "target_liquidity": round(target, 5),
                        "reason": f"Draw on liquidity: low swept + 0.79 fib + FVG entry",
                        "events": list(events_log),
                    }
                    trades.append(trade)

                    # Exit check
                    for cx in range(i, len(candles_1m)):
                        cx_c = candles_1m[cx]
                        if cx_c.high >= tp: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                        elif cx_c.low <= sl: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                    if "exit_time" not in trade:
                        trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
                        trade["exit_price"] = candles_1m[-1].close
                        trade["outcome"] = "open"
                    break
                break

            elif expected_direction == "short" and c.close < fib_level:
                events_log.append({
                    "timestamp": to_iso(c.timestamp),
                    "type": "fib_confirmation",
                    "description": f"1m candle closed {c.close:.5f} below 0.79 Fib {fib_level:.5f}",
                })
                fvgs = detect_fvg(candles_1m[max(0, i - 5):i + 5])
                for fvg in fvgs:
                    entry_c = candles_1m[i]
                    sl = max(x.high for x in candles_1m[max(0, i - 3):i + 3]) + 0.0005
                    risk = abs(c.close - sl)
                    tp = c.close - (1.5 * risk)

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(entry_c.timestamp),
                        "direction": "short",
                        "entry_price": round(c.close, 5),
                        "stop_loss": round(sl, 5),
                        "take_profit": round(tp, 5),
                        "target_liquidity": round(target, 5),
                        "reason": f"Draw on liquidity: high swept + 0.79 fib + FVG entry",
                        "events": list(events_log),
                    }
                    trades.append(trade)

                    for cx in range(i, len(candles_1m)):
                        cx_c = candles_1m[cx]
                        if cx_c.low <= tp: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                        elif cx_c.high >= sl: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                    if "exit_time" not in trade:
                        trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
                        trade["exit_price"] = candles_1m[-1].close
                        trade["outcome"] = "open"
                    break
                break

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 52: Draw On Liquidity")
    parser.add_argument("--csv15m", required=True, help="15m CSV")
    parser.add_argument("--csv1m", required=True, help="1m CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_15m = load_csv(args.csv15m)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv15m)
    output = args.output or f"strategy_52_results_{meta['symbol']}.json"
    run_strategy(candles_15m, candles_1m, output)


if __name__ == "__main__":
    main()
