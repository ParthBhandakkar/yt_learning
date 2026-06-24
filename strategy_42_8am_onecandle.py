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

FIXED: Per-NY-day 8AM range; mss_events_up_to/detect_fvg_as_of/ifvg_up_to at bar j;
past_slice for SL; simulate_exits (1:2 partial target); one trade per day max.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import Candle, load_csv, to_iso, parse_csv_filename, save_trades, index_at_or_after
from causal_backtest import (
    group_by_ny_day,
    ny_hour,
    ny_date,
    past_slice,
    detect_fvg_as_of,
    ifvg_up_to,
    mss_events_up_to,
    simulate_exits,
)


def find_8am_candle(day_1h: list[Candle]) -> Optional[Candle]:
    for c in day_1h:
        if ny_hour(c.timestamp) == 8 and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute == 0:
            return c
    return None


def monitor_sweep_and_entry(
    candles_1m: list[Candle], range_high: float, range_low: float, range_ts: int, day
) -> Optional[dict]:
    """Wait for sweep outside range, then MSS + close back inside (causal)."""
    start_idx = index_at_or_after(candles_1m, range_ts)

    real_start = start_idx
    for i in range(start_idx, len(candles_1m)):
        if ny_date(candles_1m[i].timestamp) != day:
            break
        h = ny_hour(candles_1m[i].timestamp)
        if h > 9 or (h == 9 and datetime.fromtimestamp(candles_1m[i].timestamp, tz=timezone.utc).minute >= 30):
            real_start = i
            break

    sweep_idx = None
    sweep_direction = None

    for i in range(real_start, min(real_start + 120, len(candles_1m))):
        if ny_date(candles_1m[i].timestamp) != day:
            break
        c = candles_1m[i]

        if c.high > range_high and sweep_idx is None:
            sweep_idx = i
            sweep_direction = "high_swept"
        elif c.low < range_low and sweep_idx is None:
            sweep_idx = i
            sweep_direction = "low_swept"

        if sweep_idx is not None:
            for j in range(i + 1, min(i + 30, len(candles_1m))):
                if ny_date(candles_1m[j].timestamp) != day:
                    break
                cj = candles_1m[j]

                if sweep_direction == "high_swept" and cj.close < range_high:
                    inside = True
                elif sweep_direction == "low_swept" and cj.close > range_low:
                    inside = True
                else:
                    continue

                sub = past_slice(candles_1m, j)
                has_mss = False
                mss_dir = None
                for ev in mss_events_up_to(sub, j, lookback=3):
                    if sweep_direction == "high_swept" and ev["direction"] == "bearish":
                        has_mss = True
                        mss_dir = "short"
                    elif sweep_direction == "low_swept" and ev["direction"] == "bullish":
                        has_mss = True
                        mss_dir = "long"

                if not has_mss:
                    continue

                for fvg in detect_fvg_as_of(sub, j):
                    inv = ifvg_up_to(sub, fvg, j)
                    if inv:
                        entry_c = cj
                        return {
                            "entry_idx": j,
                            "entry_price": entry_c.close,
                            "direction": mss_dir,
                            "sweep_direction": sweep_direction,
                            "sweep_idx": sweep_idx,
                            "description": (
                                f"{mss_dir.title()} entry at {entry_c.close:.5f} after "
                                f"{sweep_direction} + MSS + range re-entry (iFVG)"
                            ),
                        }

                entry_c = cj
                return {
                    "entry_idx": j,
                    "entry_price": entry_c.close,
                    "direction": mss_dir,
                    "sweep_direction": sweep_direction,
                    "sweep_idx": sweep_idx,
                    "description": (
                        f"{mss_dir.title()} entry at {entry_c.close:.5f} after "
                        f"sweep + MSS (OB)"
                    ),
                }
    return None


def run_strategy(candles_1h: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    days_1h = group_by_ny_day(candles_1h)

    for day_1h in days_1h:
        if not day_1h:
            continue
        day = ny_date(day_1h[0].timestamp)

        c8am = find_8am_candle(day_1h)
        if c8am is None:
            continue

        range_high = c8am.high
        range_low = c8am.low
        range_ts = c8am.timestamp

        events_log = [{
            "timestamp": to_iso(range_ts),
            "type": "range_defined",
            "range_high": round(range_high, 5),
            "range_low": round(range_low, 5),
            "description": f"8AM range: high={range_high:.5f}, low={range_low:.5f}",
        }]

        result = monitor_sweep_and_entry(candles_1m, range_high, range_low, range_ts, day)
        if result is None:
            continue

        events_log.append({
            "timestamp": to_iso(candles_1m[result["sweep_idx"]].timestamp),
            "type": "liquidity_sweep",
            "direction": result["sweep_direction"],
            "description": (
                f"Price swept 8AM range {'high' if 'high' in result['sweep_direction'] else 'low'}"
            ),
        })

        entry_idx = result["entry_idx"]
        entry_c = candles_1m[entry_idx]
        events_log.append({
            "timestamp": to_iso(entry_c.timestamp),
            "type": "entry_trigger",
            "direction": result["direction"],
            "description": result["description"],
        })

        trade_dir = result["direction"]
        entry_price = result["entry_price"]
        past = past_slice(candles_1m, entry_idx)
        local_low = min(c.low for c in past[max(0, len(past) - 8):])
        local_high = max(c.high for c in past[max(0, len(past) - 8):])

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
            "reason": "8AM range sweep + 1M MSS + range re-entry",
            "events": list(events_log),
        }

        exit_info = simulate_exits(
            candles_1m, entry_idx, entry_c.timestamp, trade_dir, sl, tp1,
        )
        trade.update(exit_info)
        if trade.get("outcome") == "win":
            trade["outcome"] = "partial_win"
        trades.append(trade)

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
