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

BACKTEST INTEGRITY NOTICE (severity: CRITICAL — results are likely inflated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Exit loop starts at the ENTRY bar: for cx in range(i, ...). On the same
     candle you "enter," the code checks if high/low already hit TP or SL.
     In real life you are not in the trade until after that bar closes — so
     same-bar TP/SL is peeking at the future path of the entry candle.
  2. Stop loss uses candles[i-3:i+3] — includes 3 future bars when sizing risk
     at bar i.

HOW TO FIX:
  1. Start exit checks on the NEXT bar only: if cx_c.timestamp > entry_ts.
  2. Compute SL from candles[max(0,i-3):i+1] only (no future bars).
  3. Confirm FVG on past data only; enter on bar after fib confirmation close.
  4. Verify limit/FVG entry price was reachable on that bar or the next.
  FIXED: detect_fvg_as_of, past_slice SL, simulate_exits after entry bar.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    save_trades,
)
from causal_backtest import (
    group_by_ny_day,
    ny_hour,
    past_slice,
    detect_fvg_as_of,
    simulate_exits,
)


FIB_079 = 0.79


def run_strategy(candles_15m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    days_15m = group_by_ny_day(candles_15m)

    for day_candles_15m in days_15m:
        if len(day_candles_15m) < 12:
            continue

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

        sweep_idx = None
        sweep_dir = None
        london_end_idx = next(
            (i for i, c in enumerate(day_candles_15m) if c.timestamp >= london[-1].timestamp),
            len(day_candles_15m) - 1,
        )

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

        sweep_ts = day_candles_15m[sweep_idx].timestamp
        start_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= sweep_ts), 0)

        for i in range(start_1m, min(start_1m + 60, len(candles_1m))):
            c = candles_1m[i]
            known = past_slice(candles_1m, i)
            fvgs = detect_fvg_as_of(candles_1m, i)

            if expected_direction == "long" and c.close > fib_level and fvgs:
                events_log.append({
                    "timestamp": to_iso(c.timestamp),
                    "type": "fib_confirmation",
                    "description": f"1m candle closed {c.close:.5f} above 0.79 Fib {fib_level:.5f}",
                })
                sl_slice = known[max(0, len(known) - 4):]
                sl = min(x.low for x in sl_slice) - 0.0005
                entry_price = c.close
                risk = abs(entry_price - sl)
                tp = entry_price + (1.5 * risk)

                exit_info = simulate_exits(candles_1m, i, c.timestamp, "long", sl, tp)
                trade = {
                    "trade_number": len(trades) + 1,
                    "entry_time": to_iso(c.timestamp),
                    "direction": "long",
                    "entry_price": round(entry_price, 5),
                    "stop_loss": round(sl, 5),
                    "take_profit": round(tp, 5),
                    "target_liquidity": round(target, 5),
                    "reason": "Draw on liquidity: low swept + 0.79 fib + FVG entry",
                    "events": list(events_log),
                    **exit_info,
                }
                trades.append(trade)
                break

            elif expected_direction == "short" and c.close < fib_level and fvgs:
                events_log.append({
                    "timestamp": to_iso(c.timestamp),
                    "type": "fib_confirmation",
                    "description": f"1m candle closed {c.close:.5f} below 0.79 Fib {fib_level:.5f}",
                })
                sl_slice = known[max(0, len(known) - 4):]
                sl = max(x.high for x in sl_slice) + 0.0005
                entry_price = c.close
                risk = abs(entry_price - sl)
                tp = entry_price - (1.5 * risk)

                exit_info = simulate_exits(candles_1m, i, c.timestamp, "short", sl, tp)
                trade = {
                    "trade_number": len(trades) + 1,
                    "entry_time": to_iso(c.timestamp),
                    "direction": "short",
                    "entry_price": round(entry_price, 5),
                    "stop_loss": round(sl, 5),
                    "take_profit": round(tp, 5),
                    "target_liquidity": round(target, 5),
                    "reason": "Draw on liquidity: high swept + 0.79 fib + FVG entry",
                    "events": list(events_log),
                    **exit_info,
                }
                trades.append(trade)
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
