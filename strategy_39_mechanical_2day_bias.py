#!/usr/bin/env python3
"""
Strategy 39: Mechanical 2-Day Daily Bias Strategy

Source: Faiz SMC - "Best ICT Gold Trading Strategy That Works Everyday!"
Video: https://www.youtube.com/watch?v=bUKt8df141U

Core concepts:
  - Daily chart: two consecutive candles determine bias for day 3
  - Bullish: 2nd daily candle closes above 1st daily candle's high
  - Bearish: 2nd daily candle closes below 1st daily candle's low
  - Mark day 3 open, find pre-open liquidity pools on 15m
  - Wait for sweep of pre-open liquidity + 15m MSS or CISD

Usage:
  python strategy_39_mechanical_2day_bias.py --csv_daily daily.csv --csv_15m 15m_data.csv

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Uses only the last two daily candles in the whole file for bias — not
     rolling day-1/day-2 pairs for each trade day.
  2. MSS/CISD is detected on slices like candles[j-5:j+3], which include up to
     3 future 15m bars when judging bar j.
  3. Stops after the first matching trade on the entire dataset.

HOW TO FIX:
  1. Walk forward: for each day 3, use that day's actual prior two daily candles.
  2. At bar j, only pass candles[0:j+1] into detect_mss / detect_cisd.
  3. Enter on the bar after structure confirmation closes.
  4. Run per-day and collect all valid trades across the sample.

FIXED: Rolling 2-day bias per day-3; mss_events_up_to/cisd_events_up_to at bar j;
past_slice for SL; simulate_exits; all valid day-3 trades collected.
"""

import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows,
    save_trades, index_at_or_after,
)
from causal_backtest import (
    past_slice,
    mss_events_up_to,
    cisd_events_up_to,
    simulate_exits,
)


def determine_2day_bias(d1: Candle, d2: Candle) -> str:
    if d2.close > d1.high:
        return "bullish"
    if d2.close < d1.low:
        return "bearish"
    return "none"


def find_pre_open_liquidity(candles_15m: list[Candle], open_ts: int) -> dict:
    before = [c for c in candles_15m if c.timestamp < open_ts]
    pools = {"swing_highs": [], "swing_lows": []}

    sh = swing_highs(before)
    for idx in sh[-5:]:
        pools["swing_highs"].append({"idx": idx, "level": before[idx].high})

    sl = swing_lows(before)
    for idx in sl[-5:]:
        pools["swing_lows"].append({"idx": idx, "level": before[idx].low})

    return pools


def find_sweep_and_entry(
    candles_15m: list[Candle], pools: dict, bias: str, start_idx: int
) -> Optional[dict]:
    for i in range(start_idx, len(candles_15m) - 2):
        c = candles_15m[i]

        if bias == "bullish":
            for pool in pools["swing_lows"]:
                if c.low < pool["level"]:
                    sweep_event = {
                        "idx": i,
                        "type": "liquidity_sweep",
                        "swept_level": pool["level"],
                        "direction": "sell_side_swept",
                    }
                    for j in range(i + 1, min(i + 10, len(candles_15m))):
                        sub = past_slice(candles_15m, j)
                        for ev in mss_events_up_to(sub, j, lookback=3):
                            if ev["direction"] == "bullish" and ev["idx"] >= i:
                                entry_c = candles_15m[ev["idx"]]
                                return {
                                    "entry_idx": ev["idx"],
                                    "entry_price": entry_c.close,
                                    "direction": "long",
                                    "sweep": sweep_event,
                                    "trigger": "mss",
                                    "description": (
                                        f"Swept low {pool['level']:.5f} + bullish MSS "
                                        f"at {entry_c.close:.5f}"
                                    ),
                                }
                        for ev in cisd_events_up_to(sub, j, lookback=3):
                            if ev["direction"] == "bullish" and ev["idx"] >= i:
                                entry_c = candles_15m[ev["idx"]]
                                return {
                                    "entry_idx": ev["idx"],
                                    "entry_price": entry_c.close,
                                    "direction": "long",
                                    "sweep": sweep_event,
                                    "trigger": "cisd",
                                    "description": (
                                        f"Swept low {pool['level']:.5f} + bullish CISD "
                                        f"at {entry_c.close:.5f}"
                                    ),
                                }
                    break

        elif bias == "bearish":
            for pool in pools["swing_highs"]:
                if c.high > pool["level"]:
                    sweep_event = {
                        "idx": i,
                        "type": "liquidity_sweep",
                        "swept_level": pool["level"],
                        "direction": "buy_side_swept",
                    }
                    for j in range(i + 1, min(i + 10, len(candles_15m))):
                        sub = past_slice(candles_15m, j)
                        for ev in mss_events_up_to(sub, j, lookback=3):
                            if ev["direction"] == "bearish" and ev["idx"] >= i:
                                entry_c = candles_15m[ev["idx"]]
                                return {
                                    "entry_idx": ev["idx"],
                                    "entry_price": entry_c.close,
                                    "direction": "short",
                                    "sweep": sweep_event,
                                    "trigger": "mss",
                                    "description": (
                                        f"Swept high {pool['level']:.5f} + bearish MSS "
                                        f"at {entry_c.close:.5f}"
                                    ),
                                }
                        for ev in cisd_events_up_to(sub, j, lookback=3):
                            if ev["direction"] == "bearish" and ev["idx"] >= i:
                                entry_c = candles_15m[ev["idx"]]
                                return {
                                    "entry_idx": ev["idx"],
                                    "entry_price": entry_c.close,
                                    "direction": "short",
                                    "sweep": sweep_event,
                                    "trigger": "cisd",
                                    "description": (
                                        f"Swept high {pool['level']:.5f} + bearish CISD "
                                        f"at {entry_c.close:.5f}"
                                    ),
                                }
                    break
    return None


def run_strategy(candles_daily: list[Candle], candles_15m: list[Candle], output_path: str):
    trades = []

    for day_idx in range(2, len(candles_daily)):
        d1 = candles_daily[day_idx - 2]
        d2 = candles_daily[day_idx - 1]
        d3 = candles_daily[day_idx]

        bias = determine_2day_bias(d1, d2)
        if bias == "none":
            continue

        day3_ts = d3.timestamp

        events_log = [
            {
                "timestamp": to_iso(d1.timestamp),
                "type": "day1_candle",
                "open": round(d1.open, 5),
                "high": round(d1.high, 5),
                "low": round(d1.low, 5),
                "close": round(d1.close, 5),
                "description": (
                    f"Day 1: {d1.open:.5f} / {d1.high:.5f} / {d1.low:.5f} / {d1.close:.5f}"
                ),
            },
            {
                "timestamp": to_iso(d2.timestamp),
                "type": "day2_candle",
                "open": round(d2.open, 5),
                "high": round(d2.high, 5),
                "low": round(d2.low, 5),
                "close": round(d2.close, 5),
                "description": (
                    f"Day 2: {d2.open:.5f} / {d2.high:.5f} / {d2.low:.5f} / {d2.close:.5f}"
                ),
            },
            {
                "timestamp": to_iso(day3_ts),
                "type": "bias_determined",
                "bias": bias,
                "description": (
                    f"{bias.title()} bias for day 3 (d2 close {d2.close:.5f} vs d1 "
                    f"{'high' if bias == 'bullish' else 'low'} "
                    f"{d1.high if bias == 'bullish' else d1.low:.5f})"
                ),
            },
        ]

        pools = find_pre_open_liquidity(candles_15m, day3_ts)
        events_log.append({
            "timestamp": to_iso(day3_ts),
            "type": "pre_open_liquidity",
            "pools": {
                "highs": [round(p["level"], 5) for p in pools["swing_highs"]],
                "lows": [round(p["level"], 5) for p in pools["swing_lows"]],
            },
            "description": "Pre-open liquidity pools mapped",
        })

        start_idx = index_at_or_after(candles_15m, day3_ts)
        result = find_sweep_and_entry(candles_15m, pools, bias, start_idx)
        if result is None:
            continue

        entry_idx = result["entry_idx"]
        entry_c = candles_15m[entry_idx]
        events_log.append({
            "timestamp": to_iso(candles_15m[result["sweep"]["idx"]].timestamp),
            "type": "liquidity_sweep",
            "direction": result["sweep"]["direction"],
            "swept_level": round(result["sweep"]["swept_level"], 5),
            "description": f"Liquidity sweep at {result['sweep']['swept_level']:.5f}",
        })
        events_log.append({
            "timestamp": to_iso(entry_c.timestamp),
            "type": "entry_trigger",
            "trigger": result["trigger"],
            "description": result["description"],
        })

        trade_dir = result["direction"]
        entry_price = result["entry_price"]
        past = past_slice(candles_15m, entry_idx)
        local_low = min(c.low for c in past[max(0, len(past) - 6):])
        local_high = max(c.high for c in past[max(0, len(past) - 6):])

        if trade_dir == "long":
            sl = local_low - (local_low * 0.0005)
        else:
            sl = local_high + (local_high * 0.0005)

        risk = abs(entry_price - sl)
        tp = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)

        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(entry_c.timestamp),
            "direction": trade_dir,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(sl, 5),
            "take_profit": round(tp, 5),
            "reason": f"2-Day Bias: {bias} + pre-open sweep + {result['trigger']}",
            "events": list(events_log),
        }
        exit_info = simulate_exits(
            candles_15m, entry_idx, entry_c.timestamp, trade_dir, sl, tp,
        )
        trade.update(exit_info)
        trades.append(trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 39: Mechanical 2-Day Daily Bias")
    parser.add_argument("--csv_daily", required=True, help="Daily CSV")
    parser.add_argument("--csv_15m", required=True, help="15-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_daily = load_csv(args.csv_daily)
    candles_15m = load_csv(args.csv_15m)

    meta = parse_csv_filename(args.csv_daily)
    output = args.output or f"strategy_39_results_{meta['symbol']}.json"
    run_strategy(candles_daily, candles_15m, output)


if __name__ == "__main__":
    main()
