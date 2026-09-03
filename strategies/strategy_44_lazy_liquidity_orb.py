#!/usr/bin/env python3
"""
Strategy 44: The Lazy Liquidity Strategy (Mechanical ORB Setup)

Source: Faiz SMC - "The Laziest Liquidity Trading Strategy Making $15,000/Month"
Video: https://www.youtube.com/watch?v=YKbkZ4eRd04

Core concepts:
  - 15m chart: London 3:00 AM anchor candle (NY time)
  - Mark candle high/low as range
  - Wait for full body breakout (candle body completely outside range)
  - Strong breakout (close >> boundary) → market entry next bar open
  - Weak breakout (close near boundary) → limit retest at boundary
  - Asian session trend bias filter (only trade with the trend)
  - Stop loss at opposite side of range
  - Target: 1:2 RR
  - Only one trade per day (first valid breakout direction)

Usage:
  python strategy_44_lazy_liquidity_orb.py --csv15m 15m_data.csv [--output results.json]

FIXES APPLIED:
  1. Body breakout: requires full candle body outside anchor range
     (c.open > anchor_high for bullish, c.open < anchor_low for bearish)
  2. close_far logic inverted: strong breakouts enter via market (ride momentum),
     weak breakouts wait for retest via limit at boundary
  3. Market entries execute at next bar's open (no same-bar entry)
  4. Asian session bias filter: only trade in direction of pre-3AM trend
"""

import argparse
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    save_trades,
)
from causal_backtest import find_limit_fill, simulate_exits


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


# ---------------------------------------------------------------------------
# Asian session bias filter (pre-3AM price action)
# ---------------------------------------------------------------------------

def compute_daily_bias(candles_before_anchor: list[Candle]) -> str:
    """Determine direction bias from Asian session before 3AM anchor."""
    if len(candles_before_anchor) < 4:
        return "neutral"
    recent = candles_before_anchor[-4:]
    up_count = sum(1 for c in recent if c.close > c.open)
    if up_count >= 3:
        return "bullish"
    if up_count <= 1:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Step 1: Find 3:00 AM candle (London open anchor)
# ---------------------------------------------------------------------------

def find_3am_candle(candles_15m: list[Candle]) -> Optional[dict]:
    for i, c in enumerate(candles_15m):
        h = ny_hour(c.timestamp)
        m = datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute
        # 3:00 AM NY = first 15m candle of London
        if h == 3 and m == 0:
            return {"idx": i, "candle": c}
    return None


# ---------------------------------------------------------------------------
# Step 2: Body breakout detection
# ---------------------------------------------------------------------------

def detect_body_breakout(candles_15m: list[Candle], anchor: dict) -> Optional[dict]:
    """Detect full body close above high or below low of anchor candle"""
    anchor_c = anchor["candle"]
    anchor_high = anchor_c.high
    anchor_low = anchor_c.low

    for i in range(anchor["idx"] + 1, len(candles_15m)):
        c = candles_15m[i]

        # Bullish body breakout: full candle body above anchor high
        if c.close > anchor_high and c.open > anchor_high:
            distance = abs(c.close - anchor_high)
            return {
                "entry_idx": i,
                "type": "bullish_breakout",
                "entry_price": c.close,
                "breakout_body_close": c.close,
                "anchor_high": anchor_high,
                "anchor_low": anchor_low,
                "close_far": distance > (anchor_high - anchor_low) * 0.5,
                "description": f"Bullish body breakout: 15m candle closed {c.close:.5f} above anchor high {anchor_high:.5f}",
            }

        # Bearish body breakout: full candle body below anchor low
        if c.close < anchor_low and c.open < anchor_low:
            distance = abs(anchor_low - c.close)
            return {
                "entry_idx": i,
                "type": "bearish_breakout",
                "entry_price": c.close,
                "breakout_body_close": c.close,
                "anchor_high": anchor_high,
                "anchor_low": anchor_low,
                "close_far": distance > (anchor_high - anchor_low) * 0.5,
                "description": f"Bearish body breakout: 15m candle closed {c.close:.5f} below anchor low {anchor_low:.5f}",
            }

    return None


# ---------------------------------------------------------------------------
# Step 3: Determine entry method
# ---------------------------------------------------------------------------

def determine_entry(result: dict) -> dict:
    """Strong breakout → market entry next bar; weak breakout → limit retest at boundary."""
    if result["close_far"]:
        # Strong breakout: ride momentum with market entry
        return {"entry_type": "market", "entry_price": 0.0}
    # Weak breakout: wait for retest confirmation at the anchor boundary
    if "bullish" in result["type"]:
        entry_price = result["anchor_high"]
    else:
        entry_price = result["anchor_low"]
    return {"entry_type": "limit_retest", "entry_price": entry_price}


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_15m: list[Candle], output_path: str):
    trades = []

    # Process each day
    days: list[list[Candle]] = []
    cur: list[Candle] = []
    cur_date = None
    for c in candles_15m:
        d = (datetime.fromtimestamp(c.timestamp, tz=timezone.utc) - timedelta(hours=4)).date()
        if cur_date is None: cur_date = d
        if d != cur_date:
            if cur: days.append(cur)
            cur = []; cur_date = d
        cur.append(c)
    if cur: days.append(cur)

    for day_candles in days:
        if len(day_candles) < 10:
            continue

        anchor = find_3am_candle(day_candles)
        if anchor is None:
            continue

        # Asian session trend bias filter
        pre_anchor_candles = day_candles[:anchor["idx"]]
        bias = compute_daily_bias(pre_anchor_candles)

        events_log = []
        events_log.append({
            "timestamp": to_iso(anchor["candle"].timestamp),
            "type": "anchor_candle",
            "high": round(anchor["candle"].high, 5),
            "low": round(anchor["candle"].low, 5),
            "bias": bias,
            "description": f"3AM anchor: high={anchor['candle'].high:.5f}, low={anchor['candle'].low:.5f}, session bias={bias}",
        })

        # Step 2: Detect breakout
        result = detect_body_breakout(day_candles, anchor)
        if result is None:
            continue

        trade_dir = "long" if "bullish" in result["type"] else "short"

        # Skip trades against the Asian session bias
        if bias != "neutral":
            if (bias == "bearish" and trade_dir == "long") or (bias == "bullish" and trade_dir == "short"):
                continue

        entry_info = determine_entry(result)
        breakout_idx = result["entry_idx"]

        events_log.append({
            "timestamp": to_iso(day_candles[breakout_idx].timestamp),
            "type": "breakout_detected",
            "breakout_type": result["type"],
            "description": result["description"],
        })

        # Stop loss at opposite side of anchor
        sl = result["anchor_low"] if trade_dir == "long" else result["anchor_high"]

        # Adjust SL for spread
        if trade_dir == "long":
            sl_adjusted = sl - (sl * 0.0005)
        else:
            sl_adjusted = sl + (sl * 0.0005)

        if entry_info["entry_type"] == "limit_retest":
            fill = find_limit_fill(
                day_candles,
                breakout_idx,
                entry_info["entry_price"],
                trade_dir,
            )
            if fill is None:
                continue
            entry_idx, entry_price = fill
        else:
            # Market entry at the next bar's open (signal confirmed at breakout bar close)
            entry_idx = breakout_idx + 1
            if entry_idx >= len(day_candles):
                continue
            entry_price = day_candles[entry_idx].open

        entry_candle = day_candles[entry_idx]
        risk = abs(entry_price - sl_adjusted)
        tp = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)

        events_log.append({
            "timestamp": to_iso(entry_candle.timestamp),
            "type": "entry_executed",
            "entry_type": entry_info["entry_type"],
            "entry_price": round(entry_price, 5),
            "description": f"Entry at {entry_price:.5f} via {entry_info['entry_type']}",
        })

        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(entry_candle.timestamp),
            "direction": trade_dir,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(sl_adjusted, 5),
            "take_profit": round(tp, 5),
            "reason": f"ORB: 3AM anchor {result['type']} via {entry_info['entry_type']}",
            "events": list(events_log),
        }
        exit_info = simulate_exits(
            day_candles,
            entry_idx,
            entry_candle.timestamp,
            trade_dir,
            trade["stop_loss"],
            trade["take_profit"],
        )
        trade.update(exit_info)
        trades.append(trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 44: Lazy Liquidity ORB")
    parser.add_argument("--csv", required=True, help="15-minute OHLCV CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)
    output = args.output or f"strategy_44_results_{meta['symbol']}_{meta['timeframe']}.json"
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
