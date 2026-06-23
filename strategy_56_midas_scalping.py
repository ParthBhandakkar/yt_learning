#!/usr/bin/env python3
"""
Strategy 56: Midas Model Scalping Strategy

Source: Faiz SMC - "This Gold Scalping Strategy Works Everyday! (Easy & Profitable)"
Video: https://www.youtube.com/watch?v=Ei0MtKztZtA

Core concepts:
  - Gold only, 15m + 1m timeframes
  - 8:00 PM model: mark nearest unswept high/low before 8PM NY
  - 9:00 PM model: same but for 9PM
  - Wait for sweep of the 15m high/low after session open
  - 1m MSS with displacement + FVG for entry
  - Target 1:2 RR, BE at 1:1

Usage:
  python strategy_56_midas_scalping.py --csv15m 15m_data.csv --csv1m 1m_data.csv

FIXED: Causal backtest — per-day session loop (8PM/9PM each NY day); MSS via
mss_events_up_to; entry bar after displacement+MSS close; simulate_exits for TP/SL.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows,
    save_trades,
)
from causal_backtest import group_by_ny_day, ny_hour, past_slice, mss_events_up_to, simulate_exits


def nearest_unswept_levels(candles_15m: list[Candle], before_ts: int) -> dict:
    before = [c for c in candles_15m if c.timestamp < before_ts]
    result = {"high": None, "low": None, "high_idx": None, "low_idx": None}
    if len(before) < 5:
        return result
    sh = swing_highs(before)
    sl = swing_lows(before)
    if sh:
        result["high"] = before[sh[-1]].high
        result["high_idx"] = sh[-1]
    if sl:
        result["low"] = before[sl[-1]].low
        result["low_idx"] = sl[-1]
    return result


def get_session_windows() -> list[dict]:
    return [
        {"name": "8PM", "hour": 20, "window_end_hour": 21},
        {"name": "9PM", "hour": 21, "window_end_hour": 0},
    ]


def monitor_session_causal(
    candles_15m: list[Candle],
    candles_1m: list[Candle],
    levels: dict,
    session_open_ts: int,
    session_name: str,
) -> Optional[dict]:
    events_log = [{
        "timestamp": to_iso(session_open_ts),
        "type": "session_start",
        "session": session_name,
        "unswept_high": round(levels["high"], 5) if levels["high"] else None,
        "unswept_low": round(levels["low"], 5) if levels["low"] else None,
        "description": (
            f"{session_name}: unswept high={levels['high']:.5f}, low={levels['low']:.5f}"
            if levels["high"] and levels["low"]
            else f"{session_name}: levels marked"
        ),
    }]

    start_15m = next((i for i, c in enumerate(candles_15m) if c.timestamp >= session_open_ts), 0)
    sweep_idx = None
    sweep_dir = None

    for i in range(start_15m, len(candles_15m)):
        c = candles_15m[i]
        if levels["high"] and c.high > levels["high"]:
            sweep_idx = i
            sweep_dir = "high_swept"
            break
        if levels["low"] and c.low < levels["low"]:
            sweep_idx = i
            sweep_dir = "low_swept"
            break

    if sweep_idx is None:
        return None

    events_log.append({
        "timestamp": to_iso(candles_15m[sweep_idx].timestamp),
        "type": "liquidity_sweep",
        "direction": sweep_dir,
        "description": f"15m {'high' if 'high' in sweep_dir else 'low'} swept",
    })

    sweep_ts = candles_15m[sweep_idx].timestamp
    start_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= sweep_ts), 0)

    for i in range(start_1m, min(start_1m + 60, len(candles_1m) - 1)):
        c = candles_1m[i]
        past = past_slice(candles_1m, i)
        body = abs(c.close - c.open)
        avg_body = sum(abs(x.close - x.open) for x in past[max(0, len(past) - 6) : -1]) / max(
            1, min(5, len(past) - 1)
        )
        if body <= avg_body * 1.5:
            continue

        mss_list = mss_events_up_to(candles_1m, i, lookback=3)
        mss_dir = None
        for ev in mss_list:
            if ev["idx"] != i:
                continue
            if sweep_dir == "high_swept" and ev["direction"] == "bearish":
                mss_dir = "short"
            elif sweep_dir == "low_swept" and ev["direction"] == "bullish":
                mss_dir = "long"
        if mss_dir is None:
            continue

        entry_idx = i + 1
        entry_c = candles_1m[entry_idx]
        past_entry = past_slice(candles_1m, i)
        local_low = min(x.low for x in past_entry[max(0, len(past_entry) - 4) :])
        local_high = max(x.high for x in past_entry[max(0, len(past_entry) - 4) :])
        sl = local_low - (local_low * 0.0005) if mss_dir == "long" else local_high + (local_high * 0.0005)
        risk = abs(entry_c.close - sl)
        tp = entry_c.close + (2 * risk) if mss_dir == "long" else entry_c.close - (2 * risk)

        events_log.append({
            "timestamp": to_iso(entry_c.timestamp),
            "type": "entry_trigger",
            "direction": mss_dir,
            "description": f"1M MSS + displacement at {entry_c.close:.5f}",
        })
        return {
            "entry_idx": entry_idx,
            "entry_price": entry_c.close,
            "direction": mss_dir,
            "sl": sl,
            "tp": tp,
            "events": events_log,
        }
    return None


def run_strategy(candles_15m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    sessions = get_session_windows()

    for day_15m in group_by_ny_day(candles_15m):
        for sess in sessions:
            session_open_ts = None
            for c in day_15m:
                dt = datetime.fromtimestamp(c.timestamp, tz=timezone.utc)
                if ny_hour(c.timestamp) == sess["hour"] and dt.minute == 0:
                    session_open_ts = c.timestamp
                    break
            if session_open_ts is None:
                continue

            levels = nearest_unswept_levels(candles_15m, session_open_ts)
            if levels["high"] is None and levels["low"] is None:
                continue

            result = monitor_session_causal(
                candles_15m, candles_1m, levels, session_open_ts, sess["name"]
            )
            if result is None:
                continue

            entry_c = candles_1m[result["entry_idx"]]
            exit_info = simulate_exits(
                candles_1m,
                result["entry_idx"],
                entry_c.timestamp,
                result["direction"],
                result["sl"],
                result["tp"],
            )
            trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_c.timestamp),
                "direction": result["direction"],
                "entry_price": round(result["entry_price"], 5),
                "stop_loss": round(result["sl"], 5),
                "take_profit": round(result["tp"], 5),
                "session": sess["name"],
                "reason": f"Midas {sess['name']}: sweep + MSS + displacement",
                "events": result["events"],
                **exit_info,
            }
            trades.append(trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 56: Midas Model Scalping")
    parser.add_argument("--csv15m", required=True, help="15m CSV")
    parser.add_argument("--csv1m", required=True, help="1m CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_15m = load_csv(args.csv15m)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv15m)
    output = args.output or f"strategy_56_results_{meta['symbol']}.json"
    run_strategy(candles_15m, candles_1m, output)


if __name__ == "__main__":
    main()
