#!/usr/bin/env python3
"""
Strategy 04: Gold Fixed Range Volume Profile Scalping Strategy

Source: Faiz SMC - "The Easiest Gold Volume Profile Trading Strategy That Works!"
Video: https://www.youtube.com/watch?v=LsC2IokcYpc

Core concepts:
  - 5-minute chart only on Gold (XAUUSD)
  - Fixed Range Volume Profile on London session (03:00-07:00 NY time)
  - Profile shapes: D (neutral), P (bullish), B (bearish)
  - Failed Auction Setup (fade edges of value area)
  - Breakout Setup (ride momentum after consolidation outside)
  - Max 2 trades/day (1 per direction)
  - POC invalidation rule

Usage:
  python strategy_04_gold_frvp.py --csv XAUUSD_5m_2021-01-01_2024-01-01.csv [--output results.json]
"""

import argparse
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    compute_volume_profile, detect_fvg,
    candles_for_time_range, save_trades,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_bullish(c: Candle) -> bool:
    return c.close > c.open


def is_bearish(c: Candle) -> bool:
    return c.close < c.open


# ---------------------------------------------------------------------------
# Detect volume profile shape
# ---------------------------------------------------------------------------

def classify_profile_shape(vp) -> str:
    """Classify profile as D (neutral), P (bullish), B (bearish)"""
    if vp.vah == 0 or vp.val == 0 or vp.poc == 0:
        return "unknown"
    mid = (vp.vah + vp.val) / 2
    if abs(vp.poc - mid) < (vp.vah - vp.val) * 0.15:
        return "D"
    if vp.poc > mid:
        return "P"
    return "B"


# ---------------------------------------------------------------------------
# Failed Auction detection
# ---------------------------------------------------------------------------

def detect_failed_auction(
    candles_5m: list[Candle], vp, start_idx: int
) -> Optional[dict]:
    """
    Failed Auction Setup:
      Long: candle closes fully below VAL → subsequent candle closes back above VAL
      Short: candle closes fully above VAH → subsequent candle closes back below VAH
    Returns entry info or None
    """
    for i in range(start_idx, len(candles_5m) - 2):
        c0 = candles_5m[i]
        c1 = candles_5m[i + 1]

        # Long failed auction: close below VAL then reclaim
        if c0.close < vp.val and c1.close > vp.val:
            # POC invalidation check
            if _poc_mitigated_before_entry(candles_5m, start_idx, i, vp.poc):
                continue
            # Find retest entry
            entry_idx = i + 1
            entry_price = c1.close
            sweep_low = min(c0.low, c1.low)
            return {
                "type": "long_failed_auction",
                "entry_idx": entry_idx,
                "entry_price": entry_price,
                "sweep_low": sweep_low,
                "target": vp.poc,
                "stop_loss": sweep_low - (sweep_low * 0.0005),
                "description": (
                    f"Long failed auction: candle closed {c0.close:.5f} below VAL {vp.val:.5f}, "
                    f"reclaim at {c1.close:.5f}"
                ),
            }

        # Short failed auction: close above VAH then reject
        if c0.close > vp.vah and c1.close < vp.vah:
            if _poc_mitigated_before_entry(candles_5m, start_idx, i, vp.poc):
                continue
            entry_idx = i + 1
            entry_price = c1.close
            sweep_high = max(c0.high, c1.high)
            return {
                "type": "short_failed_auction",
                "entry_idx": entry_idx,
                "entry_price": entry_price,
                "sweep_high": sweep_high,
                "target": vp.poc,
                "stop_loss": sweep_high + (sweep_high * 0.0005),
                "description": (
                    f"Short failed auction: candle closed {c0.close:.5f} above VAH {vp.vah:.5f}, "
                    f"rejection at {c1.close:.5f}"
                ),
            }
    return None


def _poc_mitigated_before_entry(candles, start_idx, current_idx, poc_level) -> bool:
    """POC Invalidation Rule: if price touched POC before entry, cancel"""
    for i in range(start_idx, current_idx):
        c = candles[i]
        if c.low <= poc_level <= c.high:
            return True
    return False


# ---------------------------------------------------------------------------
# Breakout Setup detection
# ---------------------------------------------------------------------------

def detect_breakout_setup(
    candles_5m: list[Candle], vp, start_idx: int
) -> Optional[dict]:
    """
    Breakout Setup:
      Bullish: price closes above VAH, consolidates above VAH, breaks consolidation high
      Bearish: price closes below VAL, consolidates below VAL, breaks consolidation low
    """
    consolidation_lookback = 3

    for i in range(start_idx + consolidation_lookback, len(candles_5m) - 1):
        # Check if recent candles closed outside value area
        recent = candles_5m[i - consolidation_lookback:i + 1]
        all_above_vah = all(c.close > vp.vah for c in recent)
        all_below_val = all(c.close < vp.val for c in recent)

        if all_above_vah:
            consol_high = max(c.high for c in recent)
            if candles_5m[i + 1].close > consol_high:
                return {
                    "type": "bullish_breakout",
                    "entry_idx": i + 1,
                    "entry_price": candles_5m[i + 1].close,
                    "stop_loss": consol_high - (consol_high * 0.0005),
                    "target_type": "1:2 RR",
                    "description": (
                        f"Bullish breakout: price held above VAH {vp.vah:.5f}, "
                        f"consolidation high {consol_high:.5f} broken"
                    ),
                }

        if all_below_val:
            consol_low = min(c.low for c in recent)
            if candles_5m[i + 1].close < consol_low:
                return {
                    "type": "bearish_breakout",
                    "entry_idx": i + 1,
                    "entry_price": candles_5m[i + 1].close,
                    "stop_loss": consol_low + (consol_low * 0.0005),
                    "target_type": "1:2 RR",
                    "description": (
                        f"Bearish breakout: price held below VAL {vp.val:.5f}, "
                        f"consolidation low {consol_low:.5f} broken"
                    ),
                }
    return None


# ---------------------------------------------------------------------------
# Profile shape determines strategy focus
# ---------------------------------------------------------------------------

def shape_priority(shape: str) -> str:
    if shape == "D":
        return "failed_auction"
    if shape == "P":
        return "breakout"
    if shape == "B":
        return "breakout"
    return "failed_auction"


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_5m: list[Candle], output_path: str):
    trades = []

    # Group by day
    days: list[list[Candle]] = []
    current_day: list[Candle] = []
    current_date = None
    for c in candles_5m:
        dt = datetime.fromtimestamp(c.timestamp, tz=timezone.utc)
        d = dt.date()
        if current_date is None:
            current_date = d
        if d != current_date:
            if current_day:
                days.append(current_day)
            current_day = []
            current_date = d
        current_day.append(c)
    if current_day:
        days.append(current_day)

    for day_candles in days:
        if len(day_candles) < 24:
            continue

        dt = datetime.fromtimestamp(day_candles[0].timestamp, tz=timezone.utc)

        # Step 1: London session FRVP (03:00-07:00 NY time)
        # Find London session candles by checking hour in NY time (UTC-4 or UTC-5)
        london_candles = []
        for c in day_candles:
            h = (datetime.fromtimestamp(c.timestamp, tz=timezone.utc).hour - 4) % 24
            if 3 <= h < 7:
                london_candles.append(c)

        if len(london_candles) < 4:
            continue

        vp = compute_volume_profile(london_candles)
        shape = classify_profile_shape(vp)

        events_log = []
        events_log.append({
            "timestamp": to_iso(london_candles[0].timestamp),
            "type": "london_profile",
            "shape": shape,
            "vah": round(vp.vah, 5),
            "val": round(vp.val, 5),
            "poc": round(vp.poc, 5),
            "description": f"London profile shape: {shape}, VAH={vp.vah:.5f}, VAL={vp.val:.5f}, POC={vp.poc:.5f}",
        })

        # Determine strategy focus
        focus = shape_priority(shape)

        trades_taken = 0
        directions_taken = set()
        start_idx = len(london_candles)  # look at candles after London session

        # Try Failed Auction first (for D-shape) or primary for others
        if focus == "failed_auction" or shape in ("D", "unknown"):
            result = detect_failed_auction(day_candles, vp, start_idx)
            if result:
                entry_candle = day_candles[result["entry_idx"]]
                events_log.append({
                    "timestamp": to_iso(entry_candle.timestamp),
                    "type": "setup_detected",
                    "setup_type": result["type"],
                    "description": result["description"],
                })

                direction = "long" if "long" in result["type"] else "short"
                risk = abs(result["entry_price"] - result["stop_loss"])
                tp = result["entry_price"] + (2 * risk) if direction == "long" else result["entry_price"] - (2 * risk)

                trade = {
                    "trade_number": len(trades) + 1,
                    "entry_time": to_iso(entry_candle.timestamp),
                    "direction": direction,
                    "entry_price": round(result["entry_price"], 5),
                    "stop_loss": round(result["stop_loss"], 5),
                    "take_profit": round(tp, 5),
                    "reason": result["description"],
                    "strategy_type": "Failed Auction",
                    "profile_shape": shape,
                    "events": list(events_log),
                }
                trades.append(trade)
                trades_taken += 1
                directions_taken.add(direction)

        # Try Breakout setup
        if trades_taken < 2:
            result = detect_breakout_setup(day_candles, vp, start_idx)
            if result:
                entry_candle = day_candles[result["entry_idx"]]
                direction = "long" if "bullish" in result["type"] else "short"

                if direction not in directions_taken:
                    events_log.append({
                        "timestamp": to_iso(entry_candle.timestamp),
                        "type": "setup_detected",
                        "setup_type": result["type"],
                        "description": result["description"],
                    })

                    risk = abs(result["entry_price"] - result["stop_loss"])
                    tp = result["entry_price"] + (2 * risk) if direction == "long" else result["entry_price"] - (2 * risk)

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(entry_candle.timestamp),
                        "direction": direction,
                        "entry_price": round(result["entry_price"], 5),
                        "stop_loss": round(result["stop_loss"], 5),
                        "take_profit": round(tp, 5),
                        "reason": result["description"],
                        "strategy_type": "Breakout",
                        "profile_shape": shape,
                        "events": list(events_log),
                    }
                    trades.append(trade)
                    trades_taken += 1
                    directions_taken.add(direction)

        # Check exits
        for trade in trades[-trades_taken:] if trades_taken > 0 else []:
            entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
            for c in day_candles:
                if c.timestamp > entry_ts:
                    if trade["direction"] == "long":
                        if c.high >= trade["take_profit"]:
                            trade["exit_time"] = to_iso(c.timestamp)
                            trade["exit_price"] = trade["take_profit"]
                            trade["outcome"] = "win"
                            break
                        elif c.low <= trade["stop_loss"]:
                            trade["exit_time"] = to_iso(c.timestamp)
                            trade["exit_price"] = trade["stop_loss"]
                            trade["outcome"] = "loss"
                            break
                    else:
                        if c.low <= trade["take_profit"]:
                            trade["exit_time"] = to_iso(c.timestamp)
                            trade["exit_price"] = trade["take_profit"]
                            trade["outcome"] = "win"
                            break
                        elif c.high >= trade["stop_loss"]:
                            trade["exit_time"] = to_iso(c.timestamp)
                            trade["exit_price"] = trade["stop_loss"]
                            trade["outcome"] = "loss"
                            break
            if "exit_time" not in trade:
                trade["exit_time"] = to_iso(day_candles[-1].timestamp)
                trade["exit_price"] = day_candles[-1].close
                trade["outcome"] = "open"

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 04: Gold Fixed Range Volume Profile")
    parser.add_argument("--csv", required=True, help="Path to 5m OHLCV CSV file")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)
    output = args.output or f"strategy_04_results_{meta['symbol']}_{meta['timeframe']}.json"
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
