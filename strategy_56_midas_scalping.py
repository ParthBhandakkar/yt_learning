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

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Picks one session from the entire dataset and one trade (first match) —
     not a daily walk-forward backtest.
  2. MSS is detected on candles[i-3:i+5], which includes up to 5 future 1m bars
     when evaluating bar i.
  3. Entry is on the same displacement bar used to confirm structure.

HOW TO FIX:
  1. Loop each trading day / session separately; take all valid Midas setups.
  2. At bar i, only use candles[0:i+1] for MSS/displacement checks.
  3. Enter on the bar after displacement + MSS confirmation closes.
  4. Verify pre-session "unswept" levels using only candles before the open.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_mss, detect_fvg,
    swing_highs, swing_lows,
    save_trades,
)


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


# ---------------------------------------------------------------------------
# Find nearest unswept swing high/low before a given time
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Session check: 8:00 PM or 9:00 PM model
# ---------------------------------------------------------------------------

def get_session_windows() -> list[dict]:
    return [
        {"name": "8PM", "hour": 20, "window_end_hour": 21},
        {"name": "9PM", "hour": 21, "window_end_hour": 0},
    ]


# ---------------------------------------------------------------------------
# Monitor sweep + 1m entry
# ---------------------------------------------------------------------------

def monitor_session(
    candles_15m: list[Candle], candles_1m: list[Candle],
    levels: dict, session_open_ts: int, session_name: str
) -> Optional[dict]:
    events_log = [{
        "timestamp": to_iso(session_open_ts),
        "type": "session_start",
        "session": session_name,
        "unswept_high": round(levels["high"], 5) if levels["high"] else None,
        "unswept_low": round(levels["low"], 5) if levels["low"] else None,
        "description": f"{session_name}: unswept high={levels['high']:.5f}, low={levels['low']:.5f}",
    }]

    # Find sweep on 15m
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

    # Drop to 1m after sweep
    sweep_ts = candles_15m[sweep_idx].timestamp
    start_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= sweep_ts), 0)

    for i in range(start_1m, min(start_1m + 60, len(candles_1m))):
        c = candles_1m[i]
        # Check for displacement: large body candle
        body = abs(c.close - c.open)
        avg_body = sum(abs(x.close - x.open) for x in candles_1m[max(0, i - 5):i]) / max(1, min(5, i))
        displaced = body > avg_body * 1.5

        if not displaced:
            continue

        # Check MSS
        mss = detect_mss(candles_1m[max(0, i - 3):i + 5], lookback=3)
        has_mss = False
        mss_dir = None
        for ev in mss:
            if sweep_dir == "high_swept" and ev["direction"] == "bearish":
                has_mss = True
                mss_dir = "short"
            elif sweep_dir == "low_swept" and ev["direction"] == "bullish":
                has_mss = True
                mss_dir = "long"

        if has_mss:
            entry_c = candles_1m[i]
            local_low = min(x.low for x in candles_1m[max(0, i - 3):i + 3])
            local_high = max(x.high for x in candles_1m[max(0, i - 3):i + 3])

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
                "entry_idx": i,
                "entry_price": entry_c.close,
                "direction": mss_dir,
                "sl": sl,
                "tp": tp,
                "events": events_log,
            }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_15m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    sessions = get_session_windows()
    active_session = None

    for c in candles_15m:
        h = ny_hour(c.timestamp)
        for sess in sessions:
            if sess["hour"] == h:
                active_session = sess
                break
    if active_session is None:
        print("No session window found")
        save_trades(trades, output_path)
        return trades

    # Find session times
    open_hour = active_session["hour"]
    for c in candles_15m:
        if ny_hour(c.timestamp) == open_hour and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute == 0:
            session_open_ts = c.timestamp
            break
    else:
        save_trades(trades, output_path)
        return trades

    levels = nearest_unswept_levels(candles_15m, session_open_ts)
    if levels["high"] is None and levels["low"] is None:
        save_trades(trades, output_path)
        return trades

    result = monitor_session(candles_15m, candles_1m, levels, session_open_ts, active_session["name"])
    if result is None:
        print("No trade setup found")
        save_trades(trades, output_path)
        return trades

    trade = {
        "trade_number": len(trades) + 1,
        "entry_time": to_iso(candles_1m[result["entry_idx"]].timestamp),
        "direction": result["direction"],
        "entry_price": round(result["entry_price"], 5),
        "stop_loss": round(result["sl"], 5),
        "take_profit": round(result["tp"], 5),
        "session": active_session["name"],
        "reason": f"Midas {active_session['name']}: sweep + MSS + displacement",
        "events": result["events"],
    }
    trades.append(trade)

    # Exit
    entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
    for c in candles_1m:
        if c.timestamp > entry_ts:
            if trade["direction"] == "long":
                if c.high >= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.low <= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
            else:
                if c.low <= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.high >= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
    if "exit_time" not in trade:
        trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
        trade["exit_price"] = candles_1m[-1].close
        trade["outcome"] = "open"

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
