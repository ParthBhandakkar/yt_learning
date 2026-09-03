#!/usr/bin/env python3
"""
Strategy 54: Liquidity Range Trading Strategy

Source: Faiz SMC - "The Only Liquidity Strategy You'll Ever Need"
Video: http://www.youtube.com/watch?v=LivWGyobZcA

Core concepts:
  - 5m timeframe
  - Define range: big push (range high) → pullback → MSS defines range low
  - Wait for sweep of range high/low
  - 5m MSS + close back inside range → entry
  - Entry via auto block / breaker block

Usage:
  python strategy_54_liquidity_range.py --csv5m 5m_data.csv [--output results.json]

FIXED: Causal backtest — single bar-by-bar walk (no overlapping start+=20 windows);
MSS via past_slice/mss_events_up_to only; simulate_exits for TP/SL after entry bar.
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
from causal_backtest import past_slice, mss_events_up_to, simulate_exits


def identify_range_causal(candles_5m: list[Candle], as_of_idx: int) -> Optional[dict]:
    """Find range using only candles known at as_of_idx."""
    if as_of_idx < 14:
        return None
    window_start = max(0, as_of_idx - 39)
    scan = candles_5m[window_start : as_of_idx + 1]
    if len(scan) < 15:
        return None

    sh = swing_highs(scan)
    sl = swing_lows(scan)
    if len(sh) < 1 or len(sl) < 1:
        return None

    range_high_idx = sh[-1]
    range_high = scan[range_high_idx].high

    for s in sl:
        if s <= range_high_idx:
            continue
        range_low = scan[s].low
        global_low_idx = window_start + s
        mss_list = mss_events_up_to(candles_5m, as_of_idx, lookback=3)
        for ev in mss_list:
            if ev["direction"] == "bullish" and ev["idx"] >= global_low_idx:
                return {
                    "range_high": range_high,
                    "range_low": range_low,
                    "range_high_idx": window_start + range_high_idx,
                    "range_low_idx": global_low_idx,
                    "identified_at": as_of_idx,
                }
        break
    return None


def find_sweep_entry_causal(
    candles_5m: list[Candle], range_info: dict, start_idx: int
) -> Optional[dict]:
    rh = range_info["range_high"]
    rl = range_info["range_low"]
    found_sweep = False
    sweep_dir = None
    sweep_idx = None

    for i in range(start_idx, len(candles_5m) - 1):
        c = candles_5m[i]

        if not found_sweep:
            if c.high > rh:
                found_sweep = True
                sweep_dir = "high_swept"
                sweep_idx = i
            elif c.low < rl:
                found_sweep = True
                sweep_dir = "low_swept"
                sweep_idx = i
            continue

        cj = c
        j = i
        inside = False
        if sweep_dir == "high_swept" and cj.close < rh:
            inside = True
        elif sweep_dir == "low_swept" and cj.close > rl:
            inside = True
        if not inside:
            if j - sweep_idx > 15:
                found_sweep = False
                sweep_dir = None
            continue

        mss_list = mss_events_up_to(candles_5m, j, lookback=3)
        for ev in mss_list:
            if ev["idx"] != j:
                continue
            if sweep_dir == "high_swept" and ev["direction"] == "bearish":
                past = past_slice(candles_5m, j)
                local_low = min(x.low for x in past[max(0, len(past) - 4) :])
                local_high = max(x.high for x in past[max(0, len(past) - 4) :])
                return {
                    "entry_idx": j,
                    "direction": "short",
                    "entry_price": cj.close,
                    "sweep_dir": sweep_dir,
                    "sweep_idx": sweep_idx,
                    "local_low": local_low,
                    "local_high": local_high,
                    "description": f"High sweep + bearish MSS + range re-entry at {cj.close:.5f}",
                }
            if sweep_dir == "low_swept" and ev["direction"] == "bullish":
                past = past_slice(candles_5m, j)
                local_low = min(x.low for x in past[max(0, len(past) - 4) :])
                local_high = max(x.high for x in past[max(0, len(past) - 4) :])
                return {
                    "entry_idx": j,
                    "direction": "long",
                    "entry_price": cj.close,
                    "sweep_dir": sweep_dir,
                    "sweep_idx": sweep_idx,
                    "local_low": local_low,
                    "local_high": local_high,
                    "description": f"Low sweep + bullish MSS + range re-entry at {cj.close:.5f}",
                }

        if j - sweep_idx > 15:
            found_sweep = False
            sweep_dir = None
    return None


def run_strategy(candles_5m: list[Candle], output_path: str):
    trades = []
    i = 15
    while i < len(candles_5m) - 3:
        range_info = identify_range_causal(candles_5m, i)
        if range_info is None:
            i += 1
            continue

        search_from = max(range_info["range_low_idx"] + 1, range_info["identified_at"] + 1)
        result = find_sweep_entry_causal(candles_5m, range_info, search_from)
        if result is None:
            i += 1
            continue

        events_log = [{
            "timestamp": to_iso(candles_5m[range_info["range_high_idx"]].timestamp),
            "type": "range_defined",
            "range_high": round(range_info["range_high"], 5),
            "range_low": round(range_info["range_low"], 5),
            "description": f"Range: high={range_info['range_high']:.5f}, low={range_info['range_low']:.5f}",
        }]
        events_log.append({
            "timestamp": to_iso(candles_5m[result["sweep_idx"]].timestamp),
            "type": "liquidity_sweep",
            "direction": result["sweep_dir"],
            "description": f"Range {'high' if 'high' in result['sweep_dir'] else 'low'} swept",
        })

        entry_c = candles_5m[result["entry_idx"]]
        events_log.append({
            "timestamp": to_iso(entry_c.timestamp),
            "type": "entry_trigger",
            "direction": result["direction"],
            "description": result["description"],
        })

        trade_dir = result["direction"]
        entry_price = result["entry_price"]
        local_low = result["local_low"]
        local_high = result["local_high"]
        sl = local_low - (local_low * 0.0005) if trade_dir == "long" else local_high + (local_high * 0.0005)
        risk = abs(entry_price - sl)
        tp = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)

        exit_info = simulate_exits(
            candles_5m, result["entry_idx"], entry_c.timestamp, trade_dir, sl, tp
        )
        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(entry_c.timestamp),
            "direction": trade_dir,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(sl, 5),
            "take_profit": round(tp, 5),
            "range_target": round(
                range_info["range_high"] if trade_dir == "long" else range_info["range_low"], 5
            ),
            "reason": result["description"],
            "events": events_log,
            **exit_info,
        }
        trades.append(trade)
        i = result["entry_idx"] + 1

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 54: Liquidity Range Trading")
    parser.add_argument("--csv", required=True, help="5m OHLCV CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)
    output = args.output or f"strategy_54_results_{meta['symbol']}_{meta['timeframe']}.json"
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
