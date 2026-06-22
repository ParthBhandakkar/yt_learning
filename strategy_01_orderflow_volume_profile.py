#!/usr/bin/env python3
"""
Strategy 01: Orderflow and Volume Profile Day Trading Strategy

Source: Faiz SMC - "How I Made $14,068 Day Trading With Orderflow"
Video: https://www.youtube.com/watch?v=yvcqeXnghDc

Core concepts:
  - 1-minute chart with Daily Volume Profile (VAH, VAL, POC, VWAP)
  - Detect buyer/seller absorption at volume extremes
  - Wait for order inversion (close past key level)
  - Entry on structural close confirming absorption exhaustion

Input:   CSV file with OHLCV data (preferably 1-minute or lower timeframe)
Output:  JSON file with chronological trade events

Usage:
  python strategy_01_orderflow_volume_profile.py --csv data.csv [--output results.json]
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Optional
from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows, nearest_swing_high_left, nearest_swing_low_left,
    detect_fvg, detect_ifvg, detect_mss, detect_order_blocks,
    compute_volume_profile, VolumeProfile,
    candles_for_time_range, save_trades,
)


# ---------------------------------------------------------------------------
# VWAP calculation
# ---------------------------------------------------------------------------

def compute_vwap(candles: list[Candle]) -> float:
    pv = sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in candles)
    tv = sum(c.volume for c in candles)
    return pv / tv if tv else 0.0


# ---------------------------------------------------------------------------
# Daily grouping – group 1m candles into trading days (New York session)
# ---------------------------------------------------------------------------

def group_by_day(candles: list[Candle]) -> list[list[Candle]]:
    if not candles:
        return []
    days: list[list[Candle]] = []
    current_day: list[Candle] = []
    current_date = None
    for c in candles:
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
    return days


# ---------------------------------------------------------------------------
# Absorption detection – heavy volume at resistance/support without follow-through
# ---------------------------------------------------------------------------

def detect_absorption_at_level(
    candles: list[Candle], level: float, direction: str, window: int = 5
) -> list[dict]:
    """
    Detect absorption: heavy volume cluster at a level where price fails to follow through.
    direction='bullish' → buyer absorption at resistance (price stalls near high)
    direction='bearish' → seller absorption at support (price stalls near low)
    """
    events = []
    for i in range(window, len(candles) - window):
        cluster = candles[i - window:i + window]
        total_vol = sum(c.volume for c in cluster)
        avg_vol = total_vol / len(cluster) if cluster else 0

        if direction == "bullish":
            near_level = sum(1 for c in cluster if abs(c.high - level) / level < 0.0005)
            follow_through = sum(1 for c in candles[i:i + window] if c.close > level * 1.0005)
            if near_level >= 3 and avg_vol > 100 and follow_through <= 1:
                events.append({
                    "idx": i,
                    "type": "buyer_absorption",
                    "level": level,
                    "avg_volume": avg_vol,
                    "description": f"Buyer absorption at resistance {level:.5f} – heavy volume without follow-through",
                })
        else:
            near_level = sum(1 for c in cluster if abs(c.low - level) / level < 0.0005)
            follow_through = sum(1 for c in candles[i:i + window] if c.close < level * 0.9995)
            if near_level >= 3 and avg_vol > 100 and follow_through <= 1:
                events.append({
                    "idx": i,
                    "type": "seller_absorption",
                    "level": level,
                    "avg_volume": avg_vol,
                    "description": f"Seller absorption at support {level:.5f} – heavy volume without follow-through",
                })
    return events


# ---------------------------------------------------------------------------
# Order inversion – candle closes past a cluster/level
# ---------------------------------------------------------------------------

def detect_order_inversion(candles: list[Candle], level: float, direction: str, start_idx: int) -> Optional[dict]:
    """
    Order inversion: a candle closes decisively past a level.
    direction='bearish' → close below level (inversion from support to resistance)
    direction='bullish' → close above level (inversion from resistance to support)
    """
    for i in range(start_idx, len(candles)):
        c = candles[i]
        if direction == "bearish" and c.close < level:
            return {
                "idx": i,
                "type": "order_inversion",
                "direction": "bearish",
                "level": level,
                "price": c.close,
                "description": f"Bearish order inversion – close {c.close:.5f} below level {level:.5f}",
            }
        if direction == "bullish" and c.close > level:
            return {
                "idx": i,
                "type": "order_inversion",
                "direction": "bullish",
                "level": level,
                "price": c.close,
                "description": f"Bullish order inversion – close {c.close:.5f} above level {level:.5f}",
            }
    return None


# ---------------------------------------------------------------------------
# Main strategy logic
# ---------------------------------------------------------------------------

def run_strategy(candles_1m: list[Candle], output_path: str):
    meta = {"strategy": "Orderflow and Volume Profile Day Trading Strategy"}
    trades = []
    events_log = []

    # Group candles into days
    days = group_by_day(candles_1m)

    for day_idx, day_candles in enumerate(days):
        if len(day_candles) < 30:
            continue

        # Compute daily volume profile from all day candles
        vp = compute_volume_profile(day_candles)
        vwap = compute_vwap(day_candles)

        daily_high = max(c.high for c in day_candles)
        daily_low = min(c.low for c in day_candles)

        events_log.append({
            "timestamp": to_iso(day_candles[0].timestamp),
            "type": "day_start",
            "description": f"Day {day_idx + 1}: High={daily_high:.5f}, Low={daily_low:.5f}, VAH={vp.vah:.5f}, VAL={vp.val:.5f}, POC={vp.poc:.5f}, VWAP={vwap:.5f}",
        })

        # Step 2: Track buyer absorption at VWAP / POC (resistance levels for shorts)
        if vp.poc > 0:
            buy_absorptions = detect_absorption_at_level(day_candles, vp.poc, "bullish", window=3)
            for ab in buy_absorptions:
                events_log.append({
                    "timestamp": to_iso(day_candles[ab["idx"]].timestamp),
                    "type": ab["type"],
                    "description": ab["description"],
                    "level": ab["level"],
                })
                # Step 3: Look for order inversion for short
                inv = detect_order_inversion(day_candles, ab["level"], "bearish", ab["idx"] + 2)
                if inv:
                    c = day_candles[inv["idx"]]
                    entry_price = c.close
                    sl = daily_high + (daily_high * 0.0005)
                    tp = vp.val if vp.val > 0 else daily_low

                    events_log.append({
                        "timestamp": to_iso(c.timestamp),
                        "type": inv["type"],
                        "direction": "short",
                        "description": inv["description"],
                    })

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(c.timestamp),
                        "direction": "short",
                        "entry_price": round(entry_price, 5),
                        "stop_loss": round(sl, 5),
                        "take_profit": round(tp, 5),
                        "reason": "Buyer absorption at POC/VWAP + bearish order inversion",
                        "events": list(events_log),
                    }
                    trades.append(trade)
                    events_log = []
                    break

        # Step 4: Monitor VAL for reversal longs
        if vp.val > 0:
            sell_absorptions = detect_absorption_at_level(day_candles, vp.val, "bearish", window=3)
            for ab in sell_absorptions:
                events_log.append({
                    "timestamp": to_iso(day_candles[ab["idx"]].timestamp),
                    "type": ab["type"],
                    "description": ab["description"],
                    "level": ab["level"],
                })
                inv = detect_order_inversion(day_candles, ab["level"], "bullish", ab["idx"] + 2)
                if inv:
                    c = day_candles[inv["idx"]]
                    entry_price = c.close
                    sl = daily_low - (daily_low * 0.0005)
                    tp = vwap if vwap > 0 else vp.poc

                    events_log.append({
                        "timestamp": to_iso(c.timestamp),
                        "type": inv["type"],
                        "direction": "long",
                        "description": inv["description"],
                    })

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(c.timestamp),
                        "direction": "long",
                        "entry_price": round(entry_price, 5),
                        "stop_loss": round(sl, 5),
                        "take_profit": round(tp, 5),
                        "reason": "Seller absorption at VAL + bullish order inversion",
                        "events": list(events_log),
                    }
                    trades.append(trade)
                    events_log = []
                    break

        # If no trade on this day, reset
        if events_log and events_log[-1]["type"] == "day_start":
            events_log = []
        elif events_log and events_log[0]["type"] == "day_start":
            pass
        else:
            events_log = []

    # Check exit conditions for each trade
    for trade in trades:
        entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
        exit_candle = None
        for c in candles_1m:
            if c.timestamp > entry_ts:
                if trade["direction"] == "short":
                    if c.low <= trade["take_profit"]:
                        exit_candle = c
                        trade["exit_time"] = to_iso(c.timestamp)
                        trade["exit_price"] = trade["take_profit"]
                        trade["outcome"] = "win"
                        trade["pnl_pips"] = round((trade["entry_price"] - trade["take_profit"]) * 100000, 1)
                        break
                    elif c.high >= trade["stop_loss"]:
                        exit_candle = c
                        trade["exit_time"] = to_iso(c.timestamp)
                        trade["exit_price"] = trade["stop_loss"]
                        trade["outcome"] = "loss"
                        trade["pnl_pips"] = round((trade["entry_price"] - trade["stop_loss"]) * 100000, 1)
                        break
                else:
                    if c.high >= trade["take_profit"]:
                        exit_candle = c
                        trade["exit_time"] = to_iso(c.timestamp)
                        trade["exit_price"] = trade["take_profit"]
                        trade["outcome"] = "win"
                        trade["pnl_pips"] = round((trade["take_profit"] - trade["entry_price"]) * 100000, 1)
                        break
                    elif c.low <= trade["stop_loss"]:
                        exit_candle = c
                        trade["exit_time"] = to_iso(c.timestamp)
                        trade["exit_price"] = trade["stop_loss"]
                        trade["outcome"] = "loss"
                        trade["pnl_pips"] = round((trade["stop_loss"] - trade["entry_price"]) * 100000, 1)
                        break
        if exit_candle is None:
            trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
            trade["exit_price"] = candles_1m[-1].close
            trade["outcome"] = "open"
            trade["pnl_pips"] = 0

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Strategy 01: Orderflow & Volume Profile Day Trading")
    parser.add_argument("--csv", required=True, help="Path to OHLCV CSV file")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)

    output = args.output or f"strategy_01_results_{meta['symbol']}_{meta['timeframe']}.json"
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
