#!/usr/bin/env python3
"""
Strategy 44: The Lazy Liquidity Strategy (Mechanical ORB Setup)

Source: Faiz SMC - "The Laziest Liquidity Trading Strategy Making $15,000/Month"
Video: https://www.youtube.com/watch?v=YKbkZ4eRd04

Core concepts:
  - 15m chart: London 3:00 AM anchor candle (NY time)
  - Mark candle high/low as range
  - Wait for body close breakout (full candle body outside range)
  - If close is close → aggressive market entry
  - If close is far → limit order at the boundary
  - Stop loss at opposite side of range
  - Target: 1:2 RR, move to BE at 1:1
  - Reverse if opposite breakout occurs (OCO reversal)

Usage:
  python strategy_44_lazy_liquidity_orb.py --csv15m 15m_data.csv [--output results.json]
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


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


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

        # Bullish breakout: body close above anchor high
        if c.close > anchor_high and c.open > anchor_low:
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

        # Bearish breakout: body close below anchor low
        if c.close < anchor_low and c.open < anchor_high:
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

def determine_entry(result: dict, candles_15m: list[Candle]) -> dict:
    """Aggressive (market at close) or retest (limit at boundary)"""
    if result["close_far"]:
        # Retest entry: place limit at boundary
        if "bullish" in result["type"]:
            entry_price = result["anchor_high"]
            entry_type = "limit_retest"
        else:
            entry_price = result["anchor_low"]
            entry_type = "limit_retest"
    else:
        entry_price = result["entry_price"]
        entry_type = "market"

    return {"entry_type": entry_type, "entry_price": entry_price}


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

        events_log = []
        events_log.append({
            "timestamp": to_iso(anchor["candle"].timestamp),
            "type": "anchor_candle",
            "high": round(anchor["candle"].high, 5),
            "low": round(anchor["candle"].low, 5),
            "description": f"3AM anchor: high={anchor['candle'].high:.5f}, low={anchor['candle'].low:.5f}",
        })

        # Step 2: Detect breakout
        result = detect_body_breakout(day_candles, anchor)
        if result is None:
            continue

        entry_info = determine_entry(result, day_candles)

        events_log.append({
            "timestamp": to_iso(day_candles[result["entry_idx"]].timestamp),
            "type": "breakout_detected",
            "breakout_type": result["type"],
            "description": result["description"],
        })

        trade_dir = "long" if "bullish" in result["type"] else "short"

        # Stop loss at opposite side of anchor
        sl = result["anchor_low"] if trade_dir == "long" else result["anchor_high"]

        # Adjust SL for spread
        if trade_dir == "long":
            sl_adjusted = sl - (sl * 0.0005)
        else:
            sl_adjusted = sl + (sl * 0.0005)

        entry_price = entry_info["entry_price"]
        risk = abs(entry_price - sl_adjusted)
        tp = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)

        events_log.append({
            "timestamp": to_iso(day_candles[result["entry_idx"]].timestamp),
            "type": "entry_executed",
            "entry_type": entry_info["entry_type"],
            "entry_price": round(entry_price, 5),
            "description": f"Entry at {entry_price:.5f} via {entry_info['entry_type']}",
        })

        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(day_candles[result["entry_idx"]].timestamp),
            "direction": trade_dir,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(sl_adjusted, 5),
            "take_profit": round(tp, 5),
            "reason": f"ORB: 3AM anchor {result['type']} via {entry_info['entry_type']}",
            "events": list(events_log),
        }
        trades.append(trade)

        # Exit check
        entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
        for c in day_candles:
            if c.timestamp > entry_ts:
                if trade_dir == "long":
                    if c.high >= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.low <= sl_adjusted: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl_adjusted; trade["outcome"] = "loss"; break
                else:
                    if c.low <= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.high >= sl_adjusted: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl_adjusted; trade["outcome"] = "loss"; break
        if "exit_time" not in trade:
            trade["exit_time"] = to_iso(day_candles[-1].timestamp)
            trade["exit_price"] = day_candles[-1].close
            trade["outcome"] = "open"

    for trade in trades:
        if "exit_price" in trade and "entry_price" in trade:
            diff = trade["exit_price"] - trade["entry_price"]
            if trade["direction"] == "short":
                diff = -diff
            trade["pnl_pips"] = round(diff * 10000, 1)
        else:
            trade["pnl_pips"] = 0

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
