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

BACKTEST INTEGRITY NOTICE (severity: CRITICAL — results are likely inflated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  At the start of each day, this script builds the volume profile, VWAP, daily
  high, and daily low from the ENTIRE day — including hours that have not
  happened yet when a trade would be taken. It is like trading at 9:30 AM while
  already knowing where the day will close.
  Absorption detection also peeks at future candles (follow_through uses bars
  after index i) to decide if price "failed" to break a level — you cannot know
  that until those future bars have closed.

HOW TO FIX:
  1. Walk bar-by-bar through the day. Recompute VP/VWAP/high/low only from
     candles seen so far (or use yesterday's completed profile for levels).
  2. Only call detect_absorption_at_level when bar i has fully closed and use
     only candles[0:i+1] — never candles[i:i+window] for the decision at i.
  3. Set stop/target from levels known at entry time, not end-of-day extremes.
  4. Enter on the bar AFTER the inversion signal closes (e.g. i+1 open/close).
  FIXED: Bar-by-bar VP/VWAP/high/low, absorption_at_level_causal, simulate_exits.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Optional
from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    compute_volume_profile,
    save_trades,
)
from causal_backtest import (
    group_by_ny_day,
    past_slice,
    compute_vwap,
    absorption_at_level_causal,
    simulate_exits,
)


def detect_order_inversion(candles: list[Candle], level: float, direction: str, at_idx: int) -> bool:
    """Order inversion at bar at_idx only (causal single-bar check)."""
    if at_idx >= len(candles):
        return False
    c = candles[at_idx]
    if direction == "bearish" and c.close < level:
        return True
    if direction == "bullish" and c.close > level:
        return True
    return False


def run_strategy(candles_1m: list[Candle], output_path: str):
    trades = []
    days = group_by_ny_day(candles_1m)

    for day_idx, day_candles in enumerate(days):
        if len(day_candles) < 30:
            continue

        events_log = []
        pending_short_abs: Optional[dict] = None
        pending_long_abs: Optional[dict] = None
        day_traded = False

        for i in range(30, len(day_candles)):
            known = past_slice(day_candles, i)
            vp = compute_volume_profile(known)
            vwap = compute_vwap(known)
            running_high = max(c.high for c in known)
            running_low = min(c.low for c in known)

            if i == 30:
                events_log.append({
                    "timestamp": to_iso(day_candles[0].timestamp),
                    "type": "day_start",
                    "description": (
                        f"Day {day_idx + 1} walk-forward: "
                        f"High={running_high:.5f}, Low={running_low:.5f}, "
                        f"VAH={vp.vah:.5f}, VAL={vp.val:.5f}, POC={vp.poc:.5f}, VWAP={vwap:.5f}"
                    ),
                })

            if vp.poc > 0:
                for ab in absorption_at_level_causal(known, vp.poc, "bullish", window=3):
                    if ab["idx"] == i:
                        pending_short_abs = ab
                        events_log.append({
                            "timestamp": to_iso(day_candles[i].timestamp),
                            "type": ab["type"],
                            "description": ab["description"],
                            "level": ab["level"],
                        })

            if vp.val > 0:
                for ab in absorption_at_level_causal(known, vp.val, "bearish", window=3):
                    if ab["idx"] == i:
                        pending_long_abs = ab
                        events_log.append({
                            "timestamp": to_iso(day_candles[i].timestamp),
                            "type": ab["type"],
                            "description": ab["description"],
                            "level": ab["level"],
                        })

            if pending_short_abs and i > pending_short_abs["idx"] + 1:
                if detect_order_inversion(day_candles, pending_short_abs["level"], "bearish", i):
                    entry_idx = i + 1
                    if entry_idx < len(day_candles):
                        entry_candle = day_candles[entry_idx]
                        entry_price = entry_candle.close
                        sl = running_high + (running_high * 0.0005)
                        tp = vp.val if vp.val > 0 else vwap

                        events_log.append({
                            "timestamp": to_iso(day_candles[i].timestamp),
                            "type": "order_inversion",
                            "direction": "short",
                            "description": (
                                f"Bearish order inversion – close {day_candles[i].close:.5f} "
                                f"below level {pending_short_abs['level']:.5f}"
                            ),
                        })

                        exit_info = simulate_exits(
                            day_candles, entry_idx, entry_candle.timestamp, "short", sl, tp
                        )
                        trade = {
                            "trade_number": len(trades) + 1,
                            "entry_time": to_iso(entry_candle.timestamp),
                            "direction": "short",
                            "entry_price": round(entry_price, 5),
                            "stop_loss": round(sl, 5),
                            "take_profit": round(tp, 5),
                            "reason": "Buyer absorption at POC/VWAP + bearish order inversion",
                            "events": list(events_log),
                            **exit_info,
                        }
                        if exit_info["outcome"] == "win":
                            trade["pnl_pips"] = round((entry_price - tp) * 100000, 1)
                        elif exit_info["outcome"] == "loss":
                            trade["pnl_pips"] = round((entry_price - sl) * 100000, 1)
                        else:
                            trade["pnl_pips"] = 0
                        trades.append(trade)
                        day_traded = True
                    pending_short_abs = None

            if not day_traded and pending_long_abs and i > pending_long_abs["idx"] + 1:
                if detect_order_inversion(day_candles, pending_long_abs["level"], "bullish", i):
                    entry_idx = i + 1
                    if entry_idx < len(day_candles):
                        entry_candle = day_candles[entry_idx]
                        entry_price = entry_candle.close
                        sl = running_low - (running_low * 0.0005)
                        tp = vwap if vwap > 0 else vp.poc

                        events_log.append({
                            "timestamp": to_iso(day_candles[i].timestamp),
                            "type": "order_inversion",
                            "direction": "long",
                            "description": (
                                f"Bullish order inversion – close {day_candles[i].close:.5f} "
                                f"above level {pending_long_abs['level']:.5f}"
                            ),
                        })

                        exit_info = simulate_exits(
                            day_candles, entry_idx, entry_candle.timestamp, "long", sl, tp
                        )
                        trade = {
                            "trade_number": len(trades) + 1,
                            "entry_time": to_iso(entry_candle.timestamp),
                            "direction": "long",
                            "entry_price": round(entry_price, 5),
                            "stop_loss": round(sl, 5),
                            "take_profit": round(tp, 5),
                            "reason": "Seller absorption at VAL + bullish order inversion",
                            "events": list(events_log),
                            **exit_info,
                        }
                        if exit_info["outcome"] == "win":
                            trade["pnl_pips"] = round((tp - entry_price) * 100000, 1)
                        elif exit_info["outcome"] == "loss":
                            trade["pnl_pips"] = round((sl - entry_price) * 100000, 1)
                        else:
                            trade["pnl_pips"] = 0
                        trades.append(trade)
                        day_traded = True
                    pending_long_abs = None

            if day_traded:
                break

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


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
