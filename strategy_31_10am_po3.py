#!/usr/bin/env python3
"""
Strategy 31: One Trading Setup For Life - ICT 10AM PO3

Source: Faiz SMC - "One Trading Setup For Life - ICT 10AM PO3"
Video: https://www.youtube.com/watch?v=kz2rw7VWpMM

Core concepts:
  - 4H chart: 2AM vs 6AM candle relationship for daily bias
  - 10AM 4H candle open marks PO3 baseline
  - 15m FVG below/above open for manipulation targets
  - Fib std dev -2.0 to -2.5 for manipulation endpoint
  - 1m MSS or iFVG for entry

Usage:
  python strategy_31_10am_po3.py --csv4h 4h_data.csv --csv15m 15m_data.csv --csv1m 1m_data.csv

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Daily bias uses global "last" 2AM/6AM/10AM candles in the file — not the
     pair for each specific trading day.
  2. 15m FVGs are scanned across the entire history without limiting to before
     10AM on the trade day — old gaps can be cherry-picked.
  3. Only one trade per full backtest (break on first match). iFVG uses wick
     touches from core.detect_ifvg before the bar closes.

HOW TO FIX:
  1. For each calendar day, pick that day's 2AM/6AM/10AM 4H candles only.
  2. Only consider 15m FVGs that formed before 10AM on that same day.
  3. Loop per day; record all valid PO3 setups, not just the first in the file.
  4. Close-only iFVG; enter on the bar after MSS/inversion closes.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_fvg, detect_ifvg, detect_mss,
    swing_highs, swing_lows,
    resample, save_trades,
)


# ---------------------------------------------------------------------------
# NY time helpers
# ---------------------------------------------------------------------------

def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def ny_date(ts: int):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=4)
    return dt.date()


from datetime import timedelta


# ---------------------------------------------------------------------------
# Step 1: Establish daily bias from 4H 2AM and 6AM candles
# ---------------------------------------------------------------------------

def get_4h_candles_by_ny_hour(candles_4h: list[Candle], hour: int) -> Optional[Candle]:
    for c in reversed(candles_4h):
        if ny_hour(c.timestamp) == hour:
            return c
    return None


def determine_bias(candles_4h: list[Candle]) -> tuple:
    c2am = get_4h_candles_by_ny_hour(candles_4h, 2)
    c6am = get_4h_candles_by_ny_hour(candles_4h, 6)

    if c2am is None or c6am is None:
        return "neutral", None

    bias = "neutral"
    # Bullish: 6AM closes above 2AM high
    if c6am.close > c2am.high:
        bias = "bullish"
    # Bearish: 6AM closes below 2AM low
    elif c6am.close < c2am.low:
        bias = "bearish"
    # Rejection scenarios
    elif c6am.high > c2am.high and c6am.close < c2am.high:
        bias = "bearish"
    elif c6am.low < c2am.low and c6am.close > c2am.low:
        bias = "bullish"

    # No-trade: wicked both sides
    if c6am.high > c2am.high and c6am.low < c2am.low:
        bias = "notrade"

    return bias, {"c2am": c2am, "c6am": c6am}


# ---------------------------------------------------------------------------
# Step 2 & 3: 10AM open & 15m FVG mapping
# ---------------------------------------------------------------------------

def find_15m_fvg_for_direction(candles_15m: list[Candle], open_price: float, bias: str) -> Optional[dict]:
    """Find 15m FVG below open (for long) or above open (for short)"""
    fvgs = detect_fvg(candles_15m)
    for fvg in fvgs:
        if bias == "bullish" and fvg["direction"] == "bullish" and fvg["upper"] < open_price:
            return fvg
        if bias == "bearish" and fvg["direction"] == "bearish" and fvg["lower"] > open_price:
            return fvg
    return None


# ---------------------------------------------------------------------------
# Step 4: Fibonacci std deviation -2.0 to -2.5
# ---------------------------------------------------------------------------

def compute_fib_std_targets(candles_15m: list[Candle], open_ts: int) -> Optional[dict]:
    """Find last swing high/low before open for fib measurement"""
    before = [c for c in candles_15m if c.timestamp < open_ts]
    if len(before) < 5:
        return None
    sh = swing_highs(before)
    sl = swing_lows(before)
    if not sh or not sl:
        return None
    swing_high = before[sh[-1]].high
    swing_low = before[sl[-1]].low
    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "neg_2_0": swing_low + (swing_high - swing_low) * -2.0,
        "neg_2_5": swing_low + (swing_high - swing_low) * -2.5,
    }


# ---------------------------------------------------------------------------
# Step 5: 1m entry confirmation
# ---------------------------------------------------------------------------

def find_1m_entry(candles_1m: list[Candle], start_idx: int, bias: str) -> Optional[dict]:
    mss_events = detect_mss(candles_1m[start_idx:start_idx + 30], lookback=5)
    for ev in mss_events:
        ev["idx"] += start_idx
        if (bias == "bullish" and ev["direction"] == "bullish") or \
           (bias == "bearish" and ev["direction"] == "bearish"):
            entry_c = candles_1m[ev["idx"]]
            return {
                "type": "mss_entry",
                "entry_idx": ev["idx"],
                "entry_price": entry_c.close,
                "description": f"1M MSS {ev['direction']} at {entry_c.close:.5f}",
            }

    fvgs = detect_fvg(candles_1m[start_idx:start_idx + 30])
    for fvg in fvgs:
        fvg["idx"] += start_idx
        inv = detect_ifvg(candles_1m[start_idx:start_idx + 40], fvg)
        if inv:
            inv["idx"] += start_idx
            entry_c = candles_1m[inv["idx"]]
            return {
                "type": "ifvg_entry",
                "entry_idx": inv["idx"],
                "entry_price": entry_c.close,
                "description": f"1M iFVG entry at {entry_c.close:.5f}",
            }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(
    candles_4h: list[Candle], candles_15m: list[Candle], candles_1m: list[Candle], output_path: str
):
    trades = []

    bias, candles_info = determine_bias(candles_4h)
    if bias == "neutral" or bias == "notrade":
        print(f"No trade bias: {bias}")
        save_trades(trades, output_path)
        return trades

    events_log = []
    c6am = candles_info["c6am"]
    events_log.append({
        "timestamp": to_iso(c6am.timestamp),
        "type": "daily_bias",
        "bias": bias,
        "description": f"Daily bias: {bias} (6AM close vs 2AM candle)",
    })

    # Find 10AM candle
    c10am = get_4h_candles_by_ny_hour(candles_4h, 10)
    if c10am is None:
        save_trades(trades, output_path)
        return trades

    open_price_10am = c10am.open
    open_ts_10am = c10am.timestamp

    events_log.append({
        "timestamp": to_iso(open_ts_10am),
        "type": "po3_baseline",
        "open_price": round(open_price_10am, 5),
        "description": f"10AM PO3 baseline open: {open_price_10am:.5f}",
    })

    # Step 3: 15m FVG mapping
    fvg = find_15m_fvg_for_direction(candles_15m, open_price_10am, bias)
    if fvg is None:
        print("No suitable 15m FVG for bias direction")
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_15m[fvg["idx"]].timestamp),
        "type": "fvg_mapped",
        "direction": fvg["direction"],
        "upper": round(fvg["upper"], 5),
        "lower": round(fvg["lower"], 5),
        "description": f"15m {fvg['direction']} FVG: {fvg['lower']:.5f}-{fvg['upper']:.5f}",
    })

    # Step 4: Fib std dev targets
    fib_targets = compute_fib_std_targets(candles_15m, open_ts_10am)
    if fib_targets:
        events_log.append({
            "timestamp": to_iso(open_ts_10am),
            "type": "fib_std_projection",
            "swing_high": round(fib_targets["swing_high"], 5),
            "swing_low": round(fib_targets["swing_low"], 5),
            "neg_2_0": round(fib_targets["neg_2_0"], 5),
            "neg_2_5": round(fib_targets["neg_2_5"], 5),
            "description": f"Fib std: -2.0={fib_targets['neg_2_0']:.5f}, -2.5={fib_targets['neg_2_5']:.5f}",
        })

    # Wait for manipulation leg to reach into the FVG / fib zone
    start_15m = next((i for i, c in enumerate(candles_15m) if c.timestamp >= open_ts_10am), 0)
    entry_found = False

    for i in range(start_15m, len(candles_15m)):
        c = candles_15m[i]
        # Check if price entered the FVG
        in_fvg = (bias == "bullish" and c.low < fvg["upper"] and c.high > fvg["lower"]) or \
                 (bias == "bearish" and c.high > fvg["lower"] and c.low < fvg["upper"])

        if in_fvg:
            events_log.append({
                "timestamp": to_iso(c.timestamp),
                "type": "price_in_fvg",
                "description": f"Price entered 15m FVG at {c.close:.5f}",
            })

            start_1m = next((j for j, x in enumerate(candles_1m) if x.timestamp >= c.timestamp), 0)
            entry = find_1m_entry(candles_1m, start_1m, bias)
            if entry is None:
                continue

            entry_c = candles_1m[entry["entry_idx"]]
            events_log.append({
                "timestamp": to_iso(entry_c.timestamp),
                "type": "entry_trigger",
                "entry_type": entry["type"],
                "description": entry["description"],
            })

            local_low = min(x.low for x in candles_1m[max(0, start_1m - 5):start_1m + 10])
            local_high = max(x.high for x in candles_1m[max(0, start_1m - 5):start_1m + 10])

            if bias == "bullish":
                sl = local_low - (local_low * 0.0005)
                tp = entry["entry_price"] + 2 * abs(entry["entry_price"] - sl)
            else:
                sl = local_high + (local_high * 0.0005)
                tp = entry["entry_price"] - 2 * abs(entry["entry_price"] - sl)

            trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_c.timestamp),
                "direction": bias,
                "entry_price": round(entry["entry_price"], 5),
                "stop_loss": round(sl, 5),
                "take_profit": round(tp, 5),
                "reason": f"10AM PO3: {bias} bias + 15m FVG + {entry['type']}",
                "events": list(events_log),
            }
            trades.append(trade)
            entry_found = True
            break

    if entry_found:
        trade = trades[-1]
        entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
        for c in candles_1m:
            if c.timestamp > entry_ts:
                if bias == "bullish":
                    if c.high >= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.low <= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                else:
                    if c.low <= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.high >= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
        if "exit_time" not in trade:
            trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
            trade["exit_price"] = candles_1m[-1].close
            trade["outcome"] = "open"

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 31: ICT 10AM PO3")
    parser.add_argument("--csv4h", required=True, help="4-hour CSV")
    parser.add_argument("--csv15m", required=True, help="15-minute CSV")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_4h = load_csv(args.csv4h)
    candles_15m = load_csv(args.csv15m)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv4h)
    output = args.output or f"strategy_31_results_{meta['symbol']}.json"
    run_strategy(candles_4h, candles_15m, candles_1m, output)


if __name__ == "__main__":
    main()
