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

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Sliding window (start += 20) re-scans overlapping sections and can fire
     multiple correlated trades on the same price move.
  2. Range and MSS are found on slices like scan[s:s+10] and [j-3:j+3] that
     include future bars relative to the decision bar.
  3. Swings at the edge of a scan window are not confirmed with the required
     +1 bar lag.

HOW TO FIX:
  1. Walk bar-by-bar once; at index i only use candles[0:i+1].
  2. Confirm MSS only after all bars in the pattern have closed.
  3. One trade per range sweep; skip overlapping signals on the same liquidity.
  4. Enter on the bar after MSS close back inside the range.
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
    swing_highs, swing_lows, nearest_swing_high_left, nearest_swing_low_left,
    save_trades,
)


# ---------------------------------------------------------------------------
# Step 1: Identify range on 5m
# ---------------------------------------------------------------------------

def identify_range(candles_5m: list[Candle], start_idx: int) -> Optional[dict]:
    """Find a clear push (range high) → pullback → MSS (range low)"""
    scan = candles_5m[start_idx:start_idx + 40]
    if len(scan) < 15:
        return None

    sh = swing_highs(scan)
    sl = swing_lows(scan)

    if len(sh) < 2 or len(sl) < 2:
        return None

    # Range high = recent swing high
    range_high_idx = sh[-1]
    range_high = scan[range_high_idx].high

    # After range high, look for pullback and MSS to define range low
    for s in sl:
        if s > range_high_idx:
            range_low = scan[s].low
            # Check MSS after range low
            mss = detect_mss(scan[s:s + 10], lookback=3)
            for ev in mss:
                if ev["direction"] == "bullish":
                    return {
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_high_idx": start_idx + range_high_idx,
                        "range_low_idx": start_idx + s,
                        "type": "range_identified",
                    }
            break

    return None


# ---------------------------------------------------------------------------
# Step 2: Sweep + MSS + entry
# ---------------------------------------------------------------------------

def find_sweep_entry(
    candles_5m: list[Candle], range_info: dict, start_idx: int
) -> Optional[dict]:
    rh = range_info["range_high"]
    rl = range_info["range_low"]
    found_sweep = False
    sweep_dir = None

    for i in range(start_idx, len(candles_5m) - 3):
        c = candles_5m[i]

        if c.high > rh and not found_sweep:
            found_sweep = True
            sweep_dir = "high_swept"
        elif c.low < rl and not found_sweep:
            found_sweep = True
            sweep_dir = "low_swept"

        if found_sweep:
            # Look for MSS + close back inside range
            for j in range(i + 1, min(i + 15, len(candles_5m))):
                cj = candles_5m[j]

                if sweep_dir == "high_swept" and cj.close < rh:
                    inside = True
                elif sweep_dir == "low_swept" and cj.close > rl:
                    inside = True
                else:
                    continue

                mss = detect_mss(candles_5m[max(0, j - 5):j + 3], lookback=3)
                for ev in mss:
                    if sweep_dir == "high_swept" and ev["direction"] == "bearish":
                        return {
                            "entry_idx": j,
                            "direction": "short",
                            "entry_price": cj.close,
                            "sweep_dir": sweep_dir,
                            "sweep_idx": i,
                            "description": f"High sweep + bearish MSS + range re-entry at {cj.close:.5f}",
                        }
                    if sweep_dir == "low_swept" and ev["direction"] == "bullish":
                        return {
                            "entry_idx": j,
                            "direction": "long",
                            "entry_price": cj.close,
                            "sweep_dir": sweep_dir,
                            "sweep_idx": i,
                            "description": f"Low sweep + bullish MSS + range re-entry at {cj.close:.5f}",
                        }
            found_sweep = False
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_5m: list[Candle], output_path: str):
    trades = []

    for start in range(0, len(candles_5m) - 40, 20):
        range_info = identify_range(candles_5m, start)
        if range_info is None:
            continue

        events_log = [{
            "timestamp": to_iso(candles_5m[range_info["range_high_idx"]].timestamp),
            "type": "range_defined",
            "range_high": round(range_info["range_high"], 5),
            "range_low": round(range_info["range_low"], 5),
            "description": f"Range: high={range_info['range_high']:.5f}, low={range_info['range_low']:.5f}",
        }]

        result = find_sweep_entry(candles_5m, range_info, range_info["range_low_idx"] + 1)
        if result is None:
            continue

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
        local_low = min(c.low for c in candles_5m[max(0, result["entry_idx"] - 3):result["entry_idx"] + 3])
        local_high = max(c.high for c in candles_5m[max(0, result["entry_idx"] - 3):result["entry_idx"] + 3])

        sl = local_low - (local_low * 0.0005) if trade_dir == "long" else local_high + (local_high * 0.0005)
        risk = abs(entry_price - sl)
        tp = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)

        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(entry_c.timestamp),
            "direction": trade_dir,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(sl, 5),
            "take_profit": round(tp, 5),
            "range_target": round(range_info["range_high"] if trade_dir == "long" else range_info["range_low"], 5),
            "reason": result["description"],
            "events": list(events_log),
        }
        trades.append(trade)

        # Exit check
        entry_ts = entry_c.timestamp
        for c in candles_5m:
            if c.timestamp > entry_ts:
                if trade_dir == "long":
                    if c.high >= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.low <= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                else:
                    if c.low <= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.high >= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
        if "exit_time" not in trade:
            trade["exit_time"] = to_iso(candles_5m[-1].timestamp)
            trade["exit_price"] = candles_5m[-1].close
            trade["outcome"] = "open"

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
