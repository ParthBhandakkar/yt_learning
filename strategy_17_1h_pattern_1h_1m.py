#!/usr/bin/env python3
"""
Strategy 17: The 1H Pattern Nobody Talks About (1H, 1M Strategy)

Source: Faiz SMC - "The 1H Pattern Nobody Talks About.. (1H, 1M Strategy)"
Video: https://www.youtube.com/watch?v=f7UXeZ1AZtA

Core concepts:
  - 1H candle open price anchoring
  - Session candles: 8PM (Asia), 4AM (London), 8AM (New York)
  - 5M structural boundaries (closest unswept swing high/low before 1H open)
  - 1M manipulation sweeps the 5M level
  - Fibonacci -2.0 to -2.5 standard deviation to find reversal zone
  - Entry on 1M MSS or FVG inversion at the fib zone

Usage:
  python strategy_17_1h_pattern_1h_1m.py --csv1h 1h_data.csv --csv5m 5m_data.csv --csv1m 1m_data.csv
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows, nearest_swing_high_left, nearest_swing_low_left,
    detect_fvg, detect_ifvg, detect_mss,
    resample, save_trades,
)


# ---------------------------------------------------------------------------
# Session anchors
# ---------------------------------------------------------------------------

SESSION_ANCHORS = {
    "asia": 20,     # 8:00 PM NY
    "london": 4,    # 4:00 AM NY
    "newyork": 8,   # 8:00 AM NY
}


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def find_session_candle_idx(candles_1h: list[Candle], target_hour: int) -> Optional[int]:
    for i, c in enumerate(candles_1h):
        if ny_hour(c.timestamp) == target_hour and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute == 0:
            return i
    return None


# ---------------------------------------------------------------------------
# Step 2: Find closest unswept 5M swing high/low to the left of 1H open
# ---------------------------------------------------------------------------

def find_unswept_5m_levels(
    candles_5m: list[Candle], open_ts: int
) -> dict:
    """Find the closest 5m swing high and low that are unswept before the 1H open"""
    # Get 5m candles before the 1H open
    before_open = [c for c in candles_5m if c.timestamp < open_ts]
    if len(before_open) < 10:
        return {"high": None, "low": None, "high_idx": None, "low_idx": None}

    sh = swing_highs(before_open)
    sl = swing_lows(before_open)

    result = {"high": None, "low": None, "high_idx": None, "low_idx": None}
    if sh:
        # Closest swing high to the open
        result["high"] = before_open[sh[-1]].high
        result["high_idx"] = sh[-1]
    if sl:
        result["low"] = before_open[sl[-1]].low
        result["low_idx"] = sl[-1]

    return result


# ---------------------------------------------------------------------------
# Step 3: Fibonacci standard deviation projections
# ---------------------------------------------------------------------------

def fib_std_projection(high: float, low: float, level: float) -> float:
    """Standard deviation projection: -2.0, -2.5, -4.0"""
    return low + (high - low) * level


def find_swing_for_fib(candles_5m: list[Candle], open_idx_5m: int) -> Optional[dict]:
    """Find the structural swing that broke right before open for fib measurement"""
    scan = candles_5m[max(0, open_idx_5m - 10):open_idx_5m]
    if len(scan) < 3:
        return None
    sh = swing_highs(scan)
    sl = swing_lows(scan)
    if sh and sl:
        return {"swing_high": scan[sh[-1]].high, "swing_low": scan[sl[-1]].low}
    if sh:
        return {"swing_high": scan[sh[-1]].high, "swing_low": min(c.low for c in scan)}
    if sl:
        return {"swing_high": max(c.high for c in scan), "swing_low": scan[sl[-1]].low}
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_1h: list[Candle], candles_5m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []

    for anchor_name, anchor_hour in SESSION_ANCHORS.items():
        session_candle_idx = find_session_candle_idx(candles_1h, anchor_hour)
        if session_candle_idx is None or session_candle_idx >= len(candles_1h) - 1:
            continue

        session_candle = candles_1h[session_candle_idx]
        open_price = session_candle.open
        open_ts = session_candle.timestamp

        events_log = []
        events_log.append({
            "timestamp": to_iso(open_ts),
            "type": "session_candle_open",
            "session": anchor_name,
            "open_price": round(open_price, 5),
            "description": f"{anchor_name.title()} session 1H candle opened at {open_price:.5f}",
        })

        # Step 2: Find 5M structural boundaries
        levels = find_unswept_5m_levels(candles_5m, open_ts)
        if levels["high"] is None or levels["low"] is None:
            continue

        events_log.append({
            "timestamp": to_iso(open_ts),
            "type": "structural_boundaries",
            "swing_high": round(levels["high"], 5),
            "swing_low": round(levels["low"], 5),
            "description": f"Unswept 5M swing high: {levels['high']:.5f}, swing low: {levels['low']:.5f}",
        })

        # Step 3: Fib std projection
        open_idx_5m = next((i for i, c in enumerate(candles_5m) if c.timestamp >= open_ts), 0)
        fib_source = find_swing_for_fib(candles_5m, open_idx_5m)
        if fib_source is None:
            continue

        fib_high = fib_source["swing_high"]
        fib_low = fib_source["swing_low"]

        fib_target_2_0 = fib_std_projection(fib_high, fib_low, -2.0)
        fib_target_2_5 = fib_std_projection(fib_high, fib_low, -2.5)

        events_log.append({
            "timestamp": to_iso(candles_5m[max(0, open_idx_5m - 1)].timestamp),
            "type": "fib_projection",
            "swing_high": round(fib_high, 5),
            "swing_low": round(fib_low, 5),
            "fib_2_0": round(fib_target_2_0, 5),
            "fib_2_5": round(fib_target_2_5, 5),
            "description": f"Fib std projection: -2.0={fib_target_2_0:.5f}, -2.5={fib_target_2_5:.5f}",
        })

        # Watch 1M chart for sweep of 5M level + price into fib zone
        start_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= open_ts), 0)
        entry_found = False

        for i in range(start_1m, min(start_1m + 60, len(candles_1m) - 5)):
            c = candles_1m[i]

            # Check if 5M swing high was swept (for bearish setup)
            high_swept = c.high > levels["high"]
            low_swept = c.low < levels["low"]

            # Check if price is in fib target zone
            if low_swept and fib_target_2_5 <= c.low <= fib_target_2_0:
                # Bearish sweep → looking for long (Power of 3 accumulation → distribution up)
                # Check for 1M MSS or FVG inversion
                mss_events = detect_mss(candles_1m[max(0, i - 5):i + 10], lookback=3)
                mss_found = any(ev["direction"] == "bullish" for ev in mss_events)

                fvgs = detect_fvg(candles_1m[max(0, i - 5):i + 5])
                ifvg_found = None
                for fvg in fvgs:
                    inv = detect_ifvg(candles_1m[max(0, i - 5):i + 10], fvg)
                    if inv:
                        ifvg_found = inv
                        break

                if mss_found or ifvg_found:
                    entry_candle = candles_1m[i + 1] if i + 1 < len(candles_1m) else c
                    entry_price = entry_candle.close
                    sl = min(c.low for c in candles_1m[max(0, i - 3):i + 3]) - 0.0005
                    risk = abs(entry_price - sl)
                    tp = entry_price + (2 * risk)

                    events_log.append({
                        "timestamp": to_iso(entry_candle.timestamp),
                        "type": "entry_trigger",
                        "direction": "long",
                        "trigger": "mss" if mss_found else "ifvg",
                        "description": f"1M MSS/iFVG at fib zone after low sweep - entry at {entry_price:.5f}",
                    })

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(entry_candle.timestamp),
                        "direction": "long",
                        "entry_price": round(entry_price, 5),
                        "stop_loss": round(sl, 5),
                        "take_profit": round(tp, 5),
                        "session": anchor_name,
                        "reason": f"{anchor_name} session: 5M swing low swept into -2.0/-2.5 fib zone + 1M MSS/iFVG",
                        "events": list(events_log),
                    }
                    trades.append(trade)
                    entry_found = True
                    break

            elif high_swept and fib_target_2_0 <= c.high <= fib_target_2_5:
                mss_events = detect_mss(candles_1m[max(0, i - 5):i + 10], lookback=3)
                mss_found = any(ev["direction"] == "bearish" for ev in mss_events)

                fvgs = detect_fvg(candles_1m[max(0, i - 5):i + 5])
                ifvg_found = None
                for fvg in fvgs:
                    inv = detect_ifvg(candles_1m[max(0, i - 5):i + 10], fvg)
                    if inv:
                        ifvg_found = inv
                        break

                if mss_found or ifvg_found:
                    entry_candle = candles_1m[i + 1] if i + 1 < len(candles_1m) else c
                    entry_price = entry_candle.close
                    sl = max(c.high for c in candles_1m[max(0, i - 3):i + 3]) + 0.0005
                    risk = abs(entry_price - sl)
                    tp = entry_price - (2 * risk)

                    events_log.append({
                        "timestamp": to_iso(entry_candle.timestamp),
                        "type": "entry_trigger",
                        "direction": "short",
                        "description": f"1M MSS/iFVG at fib zone after high sweep - entry at {entry_price:.5f}",
                    })

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(entry_candle.timestamp),
                        "direction": "short",
                        "entry_price": round(entry_price, 5),
                        "stop_loss": round(sl, 5),
                        "take_profit": round(tp, 5),
                        "session": anchor_name,
                        "reason": f"{anchor_name} session: 5M swing high swept into fib zone + 1M MSS/iFVG",
                        "events": list(events_log),
                    }
                    trades.append(trade)
                    entry_found = True
                    break

        if entry_found:
            # Check exit
            trade = trades[-1]
            entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
            for c in candles_1m:
                if c.timestamp > entry_ts:
                    if trade["direction"] == "long":
                        if c.high >= trade["take_profit"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["take_profit"]; trade["outcome"] = "win"; break
                        elif c.low <= trade["stop_loss"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["stop_loss"]; trade["outcome"] = "loss"; break
                    else:
                        if c.low <= trade["take_profit"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["take_profit"]; trade["outcome"] = "win"; break
                        elif c.high >= trade["stop_loss"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["stop_loss"]; trade["outcome"] = "loss"; break
            if "exit_time" not in trade:
                trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
                trade["exit_price"] = candles_1m[-1].close
                trade["outcome"] = "open"

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 17: 1H Pattern (1H, 1M Strategy)")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV")
    parser.add_argument("--csv5m", required=True, help="5-minute CSV")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_1h = load_csv(args.csv1h)
    candles_5m = load_csv(args.csv5m)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv1h)
    output = args.output or f"strategy_17_results_{meta['symbol']}.json"
    run_strategy(candles_1h, candles_5m, candles_1m, output)


if __name__ == "__main__":
    main()
