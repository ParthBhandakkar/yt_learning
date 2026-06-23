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

FIXED: Per-NY-day loop with that day's 2AM/6AM/10AM candles; 15m FVGs before 10AM
only; mss_events_up_to/ifvg_up_to/detect_fvg_as_of; past_slice for SL; simulate_exits.
"""

import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows,
    save_trades,
)
from causal_backtest import (
    ny_hour,
    ny_date,
    past_slice,
    detect_fvg_as_of,
    ifvg_up_to,
    mss_events_up_to,
    simulate_exits,
)


def get_4h_candle_on_day(candles_4h: list[Candle], day, hour: int) -> Optional[Candle]:
    for c in reversed(candles_4h):
        if ny_date(c.timestamp) == day and ny_hour(c.timestamp) == hour:
            return c
    return None


def determine_bias(c2am: Candle, c6am: Candle) -> str:
    if c6am.close > c2am.high:
        bias = "bullish"
    elif c6am.close < c2am.low:
        bias = "bearish"
    elif c6am.high > c2am.high and c6am.close < c2am.high:
        bias = "bearish"
    elif c6am.low < c2am.low and c6am.close > c2am.low:
        bias = "bullish"
    else:
        bias = "neutral"

    if c6am.high > c2am.high and c6am.low < c2am.low:
        bias = "notrade"
    return bias


def find_15m_fvg_for_direction(
    candles_15m: list[Candle], open_price: float, bias: str, before_ts: int
) -> Optional[dict]:
    subset = [c for c in candles_15m if c.timestamp < before_ts]
    if len(subset) < 3:
        return None
    as_of_idx = len(subset) - 1
    fvgs = detect_fvg_as_of(subset, as_of_idx)
    for fvg in reversed(fvgs):
        if bias == "bullish" and fvg["direction"] == "bullish" and fvg["upper"] < open_price:
            return fvg
        if bias == "bearish" and fvg["direction"] == "bearish" and fvg["lower"] > open_price:
            return fvg
    return None


def compute_fib_std_targets(candles_15m: list[Candle], open_ts: int) -> Optional[dict]:
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


def find_1m_entry(candles_1m: list[Candle], start_idx: int, bias: str) -> Optional[dict]:
    end_idx = min(start_idx + 30, len(candles_1m) - 1)
    for j in range(start_idx, end_idx + 1):
        sub = past_slice(candles_1m, j)
        for ev in mss_events_up_to(sub, j, lookback=5):
            if ev["idx"] < start_idx:
                continue
            if (bias == "bullish" and ev["direction"] == "bullish") or (
                bias == "bearish" and ev["direction"] == "bearish"
            ):
                entry_c = candles_1m[ev["idx"]]
                return {
                    "type": "mss_entry",
                    "entry_idx": ev["idx"],
                    "entry_price": entry_c.close,
                    "description": f"1M MSS {ev['direction']} at {entry_c.close:.5f}",
                }

        for fvg in detect_fvg_as_of(sub, j):
            if fvg["idx"] < start_idx:
                continue
            inv = ifvg_up_to(sub, fvg, j)
            if inv:
                entry_c = candles_1m[inv["idx"]]
                return {
                    "type": "ifvg_entry",
                    "entry_idx": inv["idx"],
                    "entry_price": entry_c.close,
                    "description": f"1M iFVG entry at {entry_c.close:.5f}",
                }
    return None


def run_strategy(
    candles_4h: list[Candle], candles_15m: list[Candle], candles_1m: list[Candle], output_path: str
):
    trades = []
    unique_days = sorted({ny_date(c.timestamp) for c in candles_4h})

    for day in unique_days:
        c2am = get_4h_candle_on_day(candles_4h, day, 2)
        c6am = get_4h_candle_on_day(candles_4h, day, 6)
        c10am = get_4h_candle_on_day(candles_4h, day, 10)
        if c2am is None or c6am is None or c10am is None:
            continue

        bias = determine_bias(c2am, c6am)
        if bias in ("neutral", "notrade"):
            continue

        events_log = [
            {
                "timestamp": to_iso(c6am.timestamp),
                "type": "daily_bias",
                "bias": bias,
                "description": f"Daily bias: {bias} (6AM close vs 2AM candle)",
            },
            {
                "timestamp": to_iso(c10am.timestamp),
                "type": "po3_baseline",
                "open_price": round(c10am.open, 5),
                "description": f"10AM PO3 baseline open: {c10am.open:.5f}",
            },
        ]

        open_price_10am = c10am.open
        open_ts_10am = c10am.timestamp

        fvg = find_15m_fvg_for_direction(candles_15m, open_price_10am, bias, open_ts_10am)
        if fvg is None:
            continue

        events_log.append({
            "timestamp": to_iso(candles_15m[fvg["idx"]].timestamp),
            "type": "fvg_mapped",
            "direction": fvg["direction"],
            "upper": round(fvg["upper"], 5),
            "lower": round(fvg["lower"], 5),
            "description": f"15m {fvg['direction']} FVG: {fvg['lower']:.5f}-{fvg['upper']:.5f}",
        })

        fib_targets = compute_fib_std_targets(candles_15m, open_ts_10am)
        if fib_targets:
            events_log.append({
                "timestamp": to_iso(open_ts_10am),
                "type": "fib_std_projection",
                "swing_high": round(fib_targets["swing_high"], 5),
                "swing_low": round(fib_targets["swing_low"], 5),
                "neg_2_0": round(fib_targets["neg_2_0"], 5),
                "neg_2_5": round(fib_targets["neg_2_5"], 5),
                "description": (
                    f"Fib std: -2.0={fib_targets['neg_2_0']:.5f}, "
                    f"-2.5={fib_targets['neg_2_5']:.5f}"
                ),
            })

        start_15m = next((i for i, c in enumerate(candles_15m) if c.timestamp >= open_ts_10am), 0)
        day_trade = None

        for i in range(start_15m, len(candles_15m)):
            c = candles_15m[i]
            if ny_date(c.timestamp) != day:
                break

            in_fvg = (
                (bias == "bullish" and c.low < fvg["upper"] and c.high > fvg["lower"])
                or (bias == "bearish" and c.high > fvg["lower"] and c.low < fvg["upper"])
            )
            if not in_fvg:
                continue

            events_log.append({
                "timestamp": to_iso(c.timestamp),
                "type": "price_in_fvg",
                "description": f"Price entered 15m FVG at {c.close:.5f}",
            })

            start_1m = next((j for j, x in enumerate(candles_1m) if x.timestamp >= c.timestamp), 0)
            entry = find_1m_entry(candles_1m, start_1m, bias)
            if entry is None:
                continue

            entry_idx = entry["entry_idx"]
            entry_c = candles_1m[entry_idx]
            events_log.append({
                "timestamp": to_iso(entry_c.timestamp),
                "type": "entry_trigger",
                "entry_type": entry["type"],
                "description": entry["description"],
            })

            past = past_slice(candles_1m, entry_idx)
            local_low = min(x.low for x in past[max(0, len(past) - 15):])
            local_high = max(x.high for x in past[max(0, len(past) - 15):])

            if bias == "bullish":
                sl = local_low - (local_low * 0.0005)
                tp = entry["entry_price"] + 2 * abs(entry["entry_price"] - sl)
            else:
                sl = local_high + (local_high * 0.0005)
                tp = entry["entry_price"] - 2 * abs(entry["entry_price"] - sl)

            day_trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_c.timestamp),
                "direction": bias,
                "entry_price": round(entry["entry_price"], 5),
                "stop_loss": round(sl, 5),
                "take_profit": round(tp, 5),
                "reason": f"10AM PO3: {bias} bias + 15m FVG + {entry['type']}",
                "events": list(events_log),
                "_entry_idx": entry_idx,
            }
            break

        if day_trade is None:
            continue

        entry_idx = day_trade.pop("_entry_idx")
        exit_info = simulate_exits(
            candles_1m, entry_idx, candles_1m[entry_idx].timestamp,
            "long" if bias == "bullish" else "short",
            day_trade["stop_loss"], day_trade["take_profit"],
        )
        day_trade.update(exit_info)
        trades.append(day_trade)

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
