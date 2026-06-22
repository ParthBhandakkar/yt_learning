#!/usr/bin/env python3
"""
Strategy 42: The 8 AM One-Candle Trading Strategy

Source: Faiz SMC - "This One Candle Can Change Your Life.. (Stupid Simple Strategy)"
Video: https://www.youtube.com/watch?v=YKbkZ4eRd04

Core concepts:
  - 1H chart: mark 8AM candle high/low as trading range
  - Wait for sweep of either boundary
  - No trades before 9:30 AM NY
  - 1M MSS + close back inside the range
  - Entry at 1M breaker/FVG/OB inside range
  - Partial at 1:2, runner to opposite range boundary

Usage:
  python strategy_42_8am_onecandle.py --csv1h 1h_data.csv --csv1m 1m_data.csv

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Scans full history and takes only the first valid setup (one trade demo,
     not a multi-year backtest).
  2. MSS is run on candles[j-5:j+3] — includes future 1m bars when deciding at j.
  3. FVG/iFVG uses core helpers that can trigger on wicks before bar close.

HOW TO FIX:
  1. Loop each trading day: build 8AM range only after that 1H candle closes.
  2. At minute j, use only past 1m candles for MSS/FVG detection.
  3. Close-only inversion; enter on next bar after MSS inside the range.
  4. Allow multiple days of trades instead of break on first match.
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
    save_trades,
)


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def ny_minutes(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return ((dt.hour - 4) % 24) * 60 + dt.minute


# ---------------------------------------------------------------------------
# Step 1: Find 8AM candle
# ---------------------------------------------------------------------------

def find_8am_candle(candles_1h: list[Candle]) -> Optional[Candle]:
    for c in reversed(candles_1h):
        if ny_hour(c.timestamp) == 8 and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute == 0:
            return c
    return None


# ---------------------------------------------------------------------------
# Step 2-4: Monitor sweep + 1M structure shift + range re-entry
# ---------------------------------------------------------------------------

def monitor_sweep_and_entry(
    candles_1m: list[Candle], range_high: float, range_low: float, open_ts: int, range_ts: int
) -> Optional[dict]:
    """Wait for sweep outside range, then MSS + close back inside"""
    start_idx = next((i for i, c in enumerate(candles_1m) if c.timestamp >= range_ts), 0)

    # Wait for 9:30 AM
    real_start = start_idx
    for i in range(start_idx, len(candles_1m)):
        if ny_hour(candles_1m[i].timestamp) > 9 or \
           (ny_hour(candles_1m[i].timestamp) == 9 and
            datetime.fromtimestamp(candles_1m[i].timestamp, tz=timezone.utc).minute >= 30):
            real_start = i
            break

    sweep_idx = None
    sweep_direction = None

    for i in range(real_start, min(real_start + 120, len(candles_1m))):
        c = candles_1m[i]

        # Detect sweep
        if c.high > range_high and sweep_idx is None:
            sweep_idx = i
            sweep_direction = "high_swept"
        elif c.low < range_low and sweep_idx is None:
            sweep_idx = i
            sweep_direction = "low_swept"

        if sweep_idx is not None:
            # After sweep, look for MSS + close back inside range
            for j in range(i + 1, min(i + 30, len(candles_1m))):
                cj = candles_1m[j]

                # Check close back inside range
                if sweep_direction == "high_swept" and cj.close < range_high:
                    inside = True
                elif sweep_direction == "low_swept" and cj.close > range_low:
                    inside = True
                else:
                    continue

                # Check MSS
                mss = detect_mss(candles_1m[max(0, j - 5):j + 3], lookback=3)
                has_mss = False
                mss_dir = None
                for ev in mss:
                    if sweep_direction == "high_swept" and ev["direction"] == "bearish":
                        has_mss = True
                        mss_dir = "short"
                    elif sweep_direction == "low_swept" and ev["direction"] == "bullish":
                        has_mss = True
                        mss_dir = "long"

                if has_mss:
                    # Find entry via FVG/breaker/OB
                    fvgs = detect_fvg(candles_1m[max(0, j - 3):j + 5])
                    for fvg in fvgs:
                        inv = detect_ifvg(candles_1m[max(0, j - 3):j + 10], fvg)
                        if inv:
                            entry_c = candles_1m[j]
                            return {
                                "entry_idx": j,
                                "entry_price": entry_c.close,
                                "direction": mss_dir,
                                "sweep_direction": sweep_direction,
                                "sweep_idx": sweep_idx,
                                "description": f"{mss_dir.title()} entry at {entry_c.close:.5f} after {sweep_direction} + MSS + range re-entry",
                            }

                    # Alternative: order block entry
                    entry_c = candles_1m[j]
                    return {
                        "entry_idx": j,
                        "entry_price": entry_c.close,
                        "direction": mss_dir,
                        "sweep_direction": sweep_direction,
                        "sweep_idx": sweep_idx,
                        "description": f"{mss_dir.title()} entry at {entry_c.close:.5f} after sweep + MSS (OB)",
                    }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_1h: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []

    c8am = find_8am_candle(candles_1h)
    if c8am is None:
        print("No 8AM NY candle found")
        save_trades(trades, output_path)
        return trades

    range_high = c8am.high
    range_low = c8am.low
    range_ts = c8am.timestamp

    events_log = [
        {
            "timestamp": to_iso(range_ts),
            "type": "range_defined",
            "range_high": round(range_high, 5),
            "range_low": round(range_low, 5),
            "description": f"8AM range: high={range_high:.5f}, low={range_low:.5f}",
        },
    ]

    result = monitor_sweep_and_entry(candles_1m, range_high, range_low, c8am.open, range_ts)
    if result is None:
        print("No sweep + entry found before range expiry")
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_1m[result["sweep_idx"]].timestamp),
        "type": "liquidity_sweep",
        "direction": result["sweep_direction"],
        "description": f"Price swept 8AM range {'high' if 'high' in result['sweep_direction'] else 'low'}",
    })

    entry_c = candles_1m[result["entry_idx"]]
    events_log.append({
        "timestamp": to_iso(entry_c.timestamp),
        "type": "entry_trigger",
        "direction": result["direction"],
        "description": result["description"],
    })

    trade_dir = result["direction"]
    entry_price = result["entry_price"]
    local_low = min(c.low for c in candles_1m[max(0, result["entry_idx"] - 5):result["entry_idx"] + 3])
    local_high = max(c.high for c in candles_1m[max(0, result["entry_idx"] - 5):result["entry_idx"] + 3])

    sl = local_low - (local_low * 0.0005) if trade_dir == "long" else local_high + (local_high * 0.0005)
    risk = abs(entry_price - sl)
    tp1 = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)
    tp2 = range_high if trade_dir == "long" else range_low

    trade = {
        "trade_number": len(trades) + 1,
        "entry_time": to_iso(entry_c.timestamp),
        "direction": trade_dir,
        "entry_price": round(entry_price, 5),
        "stop_loss": round(sl, 5),
        "take_profit_1_2": round(tp1, 5),
        "take_profit_range_extreme": round(tp2, 5),
        "reason": f"8AM range sweep + 1M MSS + range re-entry",
        "events": list(events_log),
    }
    trades.append(trade)

    # Exit check
    entry_ts = entry_c.timestamp
    for c in candles_1m:
        if c.timestamp > entry_ts:
            if trade_dir == "long":
                if c.high >= tp1:
                    trade["exit_time"] = to_iso(c.timestamp)
                    trade["exit_price"] = tp1
                    trade["outcome"] = "partial_win"
                    break
                elif c.low <= sl:
                    trade["exit_time"] = to_iso(c.timestamp)
                    trade["exit_price"] = sl
                    trade["outcome"] = "loss"
                    break
            else:
                if c.low <= tp1:
                    trade["exit_time"] = to_iso(c.timestamp)
                    trade["exit_price"] = tp1
                    trade["outcome"] = "partial_win"
                    break
                elif c.high >= sl:
                    trade["exit_time"] = to_iso(c.timestamp)
                    trade["exit_price"] = sl
                    trade["outcome"] = "loss"
                    break
    if "exit_time" not in trade:
        trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
        trade["exit_price"] = candles_1m[-1].close
        trade["outcome"] = "open"

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 42: 8 AM One-Candle Strategy")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_1h = load_csv(args.csv1h)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv1h)
    output = args.output or f"strategy_42_results_{meta['symbol']}.json"
    run_strategy(candles_1h, candles_1m, output)


if __name__ == "__main__":
    main()
