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
"""

import argparse
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_mss, detect_cisd,
    swing_highs, swing_lows,
    save_trades, resample,
)


# ---------------------------------------------------------------------------
# Group by date
# ---------------------------------------------------------------------------

def group_by_date(candles: list[Candle]) -> list[list[Candle]]:
    groups: list[list[Candle]] = []
    cur: list[Candle] = []
    cur_date = None
    for c in candles:
        d = datetime.fromtimestamp(c.timestamp, tz=timezone.utc).date()
        if cur_date is None:
            cur_date = d
        if d != cur_date:
            if cur:
                groups.append(cur)
            cur = []
            cur_date = d
        cur.append(c)
    if cur:
        groups.append(cur)
    return groups


# ---------------------------------------------------------------------------
# Step 1: Analyze two daily candles for bias
# ---------------------------------------------------------------------------

def determine_2day_bias(daily_candles: list[Candle]) -> tuple:
    """Returns (bias, day3_open, reference_candles)"""
    if len(daily_candles) < 2:
        return "none", None, None

    d1 = daily_candles[-2]  # First candle
    d2 = daily_candles[-1]  # Second candle

    # Bullish: d2 closes above d1 high
    if d2.close > d1.high:
        return "bullish", d2.close, (d1, d2)

    # Bearish: d2 closes below d1 low
    if d2.close < d1.low:
        return "bearish", d2.close, (d1, d2)

    return "none", None, None


# ---------------------------------------------------------------------------
# Step 2: Mark day 3 open and find pre-open liquidity on 15m
# ---------------------------------------------------------------------------

def find_pre_open_liquidity(candles_15m: list[Candle], open_ts: int) -> dict:
    """Find liquidity pools (swing highs/lows) formed before the day's open"""
    before = [c for c in candles_15m if c.timestamp < open_ts]
    pools = {"swing_highs": [], "swing_lows": []}

    sh = swing_highs(before)
    for idx in sh[-5:]:
        pools["swing_highs"].append({"idx": idx, "level": before[idx].high})

    sl = swing_lows(before)
    for idx in sl[-5:]:
        pools["swing_lows"].append({"idx": idx, "level": before[idx].low})

    return pools


# ---------------------------------------------------------------------------
# Step 3: Sweep detection + MSS/CISD on 15m
# ---------------------------------------------------------------------------

def find_sweep_and_entry(
    candles_15m: list[Candle], pools: dict, bias: str, start_idx: int
) -> Optional[dict]:
    for i in range(start_idx, len(candles_15m) - 2):
        c = candles_15m[i]

        if bias == "bullish":
            # Wait for sweep of a pre-open low
            for pool in pools["swing_lows"]:
                if c.low < pool["level"]:
                    sweep_event = {
                        "idx": i,
                        "type": "liquidity_sweep",
                        "swept_level": pool["level"],
                        "direction": "sell_side_swept",
                    }
                    # Look for MSS or CISD after sweep
                    for j in range(i + 1, min(i + 10, len(candles_15m))):
                        mss = detect_mss(candles_15m[max(0, j - 5):j + 3], lookback=3)
                        for ev in mss:
                            if ev["direction"] == "bullish":
                                entry_c = candles_15m[j]
                                return {
                                    "entry_idx": j,
                                    "entry_price": entry_c.close,
                                    "direction": "long",
                                    "sweep": sweep_event,
                                    "trigger": "mss",
                                    "description": f"Swept low {pool['level']:.5f} + bullish MSS at {entry_c.close:.5f}",
                                }
                        cisd = detect_cisd(candles_15m[max(0, j - 5):j + 3], lookback=3)
                        for ev in cisd:
                            if ev["direction"] == "bullish":
                                entry_c = candles_15m[j]
                                return {
                                    "entry_idx": j,
                                    "entry_price": entry_c.close,
                                    "direction": "long",
                                    "sweep": sweep_event,
                                    "trigger": "cisd",
                                    "description": f"Swept low {pool['level']:.5f} + bullish CISD at {entry_c.close:.5f}",
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
                        mss = detect_mss(candles_15m[max(0, j - 5):j + 3], lookback=3)
                        for ev in mss:
                            if ev["direction"] == "bearish":
                                entry_c = candles_15m[j]
                                return {
                                    "entry_idx": j,
                                    "entry_price": entry_c.close,
                                    "direction": "short",
                                    "sweep": sweep_event,
                                    "trigger": "mss",
                                    "description": f"Swept high {pool['level']:.5f} + bearish MSS at {entry_c.close:.5f}",
                                }
                        cisd = detect_cisd(candles_15m[max(0, j - 5):j + 3], lookback=3)
                        for ev in cisd:
                            if ev["direction"] == "bearish":
                                entry_c = candles_15m[j]
                                return {
                                    "entry_idx": j,
                                    "entry_price": entry_c.close,
                                    "direction": "short",
                                    "sweep": sweep_event,
                                    "trigger": "cisd",
                                    "description": f"Swept high {pool['level']:.5f} + bearish CISD at {entry_c.close:.5f}",
                                }
                    break
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_daily: list[Candle], candles_15m: list[Candle], output_path: str):
    trades = []

    bias, day3_open, ref = determine_2day_bias(candles_daily)
    if bias == "none" or day3_open is None:
        print("No clear 2-day bias")
        save_trades(trades, output_path)
        return trades

    d1, d2 = ref
    day3_ts = d2.timestamp + 86400  # approximate next day

    events_log = [
        {
            "timestamp": to_iso(d1.timestamp),
            "type": "day1_candle",
            "open": round(d1.open, 5),
            "high": round(d1.high, 5),
            "low": round(d1.low, 5),
            "close": round(d1.close, 5),
            "description": f"Day 1: {d1.open:.5f} / {d1.high:.5f} / {d1.low:.5f} / {d1.close:.5f}",
        },
        {
            "timestamp": to_iso(d2.timestamp),
            "type": "day2_candle",
            "open": round(d2.open, 5),
            "high": round(d2.high, 5),
            "low": round(d2.low, 5),
            "close": round(d2.close, 5),
            "description": f"Day 2: {d2.open:.5f} / {d2.high:.5f} / {d2.low:.5f} / {d2.close:.5f}",
        },
        {
            "timestamp": to_iso(day3_ts),
            "type": "bias_determined",
            "bias": bias,
            "description": f"{bias.title()} bias for day 3 (d2 close {d2.close:.5f} vs d1 {'high' if bias == 'bullish' else 'low'} {d1.high if bias == 'bullish' else d1.low:.5f})",
        },
    ]

    # Find pre-open liquidity on 15m
    pools = find_pre_open_liquidity(candles_15m, day3_ts)
    events_log.append({
        "timestamp": to_iso(candles_15m[-1].timestamp),
        "type": "pre_open_liquidity",
        "pools": {
            "highs": [round(p["level"], 5) for p in pools["swing_highs"]],
            "lows": [round(p["level"], 5) for p in pools["swing_lows"]],
        },
        "description": f"Pre-open liquidity pools mapped",
    })

    start_idx = next((i for i, c in enumerate(candles_15m) if c.timestamp >= day3_ts), 0)
    result = find_sweep_and_entry(candles_15m, pools, bias, start_idx)
    if result is None:
        print("No sweep + entry found")
        save_trades(trades, output_path)
        return trades

    entry_c = candles_15m[result["entry_idx"]]
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
    local_low = min(c.low for c in candles_15m[max(0, result["entry_idx"] - 3):result["entry_idx"] + 3])
    local_high = max(c.high for c in candles_15m[max(0, result["entry_idx"] - 3):result["entry_idx"] + 3])

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
    trades.append(trade)

    # Exit check
    entry_ts = entry_c.timestamp
    for c in candles_15m:
        if c.timestamp > entry_ts:
            if trade_dir == "long":
                if c.high >= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                elif c.low <= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
            else:
                if c.low <= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                elif c.high >= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
    if "exit_time" not in trade:
        trade["exit_time"] = to_iso(candles_15m[-1].timestamp)
        trade["exit_price"] = candles_15m[-1].close
        trade["outcome"] = "open"

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
