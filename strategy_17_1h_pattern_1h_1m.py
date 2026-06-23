#!/usr/bin/env python3
"""
Strategy 17: The 1H Pattern Nobody Talks About (1H, 1M Strategy)

Source: Faiz SMC - "The 1H Pattern Nobody Talks About.. (1H, 1M Strategy)"
Video: https://www.youtube.com/watch?v=f7UXeZ1AZtA

Core concepts:
  - 1H candle open price anchoring
  - Session candles: 8PM (Asia), 4AM (London), 8AM (New York)
  - 5M structural boundaries (closest unswept swing high/low before 1H open)
  - 1M manipulation sweeps the 5M level
  - Fibonacci -2.0 to -2.5 standard deviation to find reversal zone
  - Entry on 1M MSS or FVG inversion at the fib zone

Usage:
  python strategy_17_1h_pattern_1h_1m.py --csv1h 1h_data.csv --csv5m 5m_data.csv --csv1m 1m_data.csv

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. MSS/iFVG is detected on a slice like candles[i-5:i+10] — that includes up
     to 10 future 1m bars when deciding at bar i. You cannot see those bars yet.
  2. "Unswept" swing levels are labeled but not verified as still unswept at
     decision time — the code assumes they are valid without checking sweeps.
  3. core.detect_ifvg can fire on wicks before the candle closes.

HOW TO FIX:
  1. Only pass candles[0:i+1] into structure detectors at bar i.
  2. Confirm a level is unswept by scanning only past candles up to i.
  3. Wait for swing/FVG +1 bar confirmation; enter on the next bar after MSS.
  4. Use close-only inversion signals.

FIXED: Per-day session loops; past_slice/mss_events_up_to/detect_fvg_as_of/ifvg_up_to;
unswept levels verified causally; simulate_exits; entry on signal bar close.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows,
    save_trades,
)
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


SESSION_ANCHORS = {
    "asia": 20,
    "london": 4,
    "newyork": 8,
}


def find_session_candle_idx(candles_1h: list[Candle], target_hour: int) -> Optional[int]:
    for i, c in enumerate(candles_1h):
        if ny_hour(c.timestamp) == target_hour and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute == 0:
            return i
    return None


def find_unswept_5m_levels(candles_5m: list[Candle], open_ts: int, as_of_ts: int) -> dict:
    """Closest unswept 5m swing high/low before open, verified up to as_of_ts."""
    before_open = [c for c in candles_5m if c.timestamp < open_ts]
    if len(before_open) < 10:
        return {"high": None, "low": None, "high_idx": None, "low_idx": None}

    sh = swing_highs(before_open)
    sl = swing_lows(before_open)
    result = {"high": None, "low": None, "high_idx": None, "low_idx": None}

    if sh:
        level = before_open[sh[-1]].high
        swept = any(c.high > level for c in candles_5m if open_ts <= c.timestamp <= as_of_ts)
        if not swept:
            result["high"] = level
            result["high_idx"] = sh[-1]

    if sl:
        level = before_open[sl[-1]].low
        swept = any(c.low < level for c in candles_5m if open_ts <= c.timestamp <= as_of_ts)
        if not swept:
            result["low"] = level
            result["low_idx"] = sl[-1]

    return result


def fib_std_projection(high: float, low: float, level: float) -> float:
    return low + (high - low) * level


def find_swing_for_fib(candles_5m: list[Candle], open_idx_5m: int) -> Optional[dict]:
    scan = past_slice(candles_5m, open_idx_5m)[-10:]
    if len(scan) < 3:
        return None
    sh = swing_highs(scan)
    sl = swing_lows(scan)
    if sh and sl:
        return {"swing_high": scan[sh[-1]].high, "swing_low": scan[sl[-1]].low}
    if sh:
        return {"swing_high": scan[sh[-1]].high, "swing_low": min(c.low for c in scan)}
    if sl:
        return {"swing_high": max(c.high for c in scan), "swing_low": scan[sl[-1]].low}
    return None


def _structure_at_bar(candles_1m: list[Candle], i: int, direction: str) -> tuple[bool, bool]:
    """Return (mss_found, ifvg_found) using only data through bar i."""
    sub = past_slice(candles_1m, i)
    mss_found = any(ev["direction"] == direction for ev in mss_events_up_to(sub, i, lookback=3))
    ifvg_found = False
    for fvg in detect_fvg_as_of(sub, i):
        inv = ifvg_up_to(sub, fvg, i)
        if inv and (
            (direction == "bullish" and inv["direction"] == "bullish_ifvg")
            or (direction == "bearish" and inv["direction"] == "bearish_ifvg")
        ):
            ifvg_found = True
            break
    return mss_found, ifvg_found


def run_strategy(candles_1h: list[Candle], candles_5m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    days_1h = group_by_ny_day(candles_1h)

    for day_1h in days_1h:
        if not day_1h:
            continue

        for anchor_name, anchor_hour in SESSION_ANCHORS.items():
            session_candle_idx = find_session_candle_idx(day_1h, anchor_hour)
            if session_candle_idx is None:
                continue

            session_candle = day_1h[session_candle_idx]
            open_ts = session_candle.timestamp

            events_log = [{
                "timestamp": to_iso(open_ts),
                "type": "session_candle_open",
                "session": anchor_name,
                "open_price": round(session_candle.open, 5),
                "description": f"{anchor_name.title()} session 1H candle opened at {session_candle.open:.5f}",
            }]

            open_idx_5m = next((i for i, c in enumerate(candles_5m) if c.timestamp >= open_ts), 0)
            fib_source = find_swing_for_fib(candles_5m, open_idx_5m)
            if fib_source is None:
                continue

            fib_high = fib_source["swing_high"]
            fib_low = fib_source["swing_low"]
            fib_target_2_0 = fib_std_projection(fib_high, fib_low, -2.0)
            fib_target_2_5 = fib_std_projection(fib_high, fib_low, -2.5)

            events_log.append({
                "timestamp": to_iso(candles_5m[max(0, open_idx_5m - 1)].timestamp),
                "type": "fib_projection",
                "swing_high": round(fib_high, 5),
                "swing_low": round(fib_low, 5),
                "fib_2_0": round(fib_target_2_0, 5),
                "fib_2_5": round(fib_target_2_5, 5),
                "description": f"Fib std projection: -2.0={fib_target_2_0:.5f}, -2.5={fib_target_2_5:.5f}",
            })

            start_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= open_ts), 0)
            day_trade = None

            for i in range(start_1m, min(start_1m + 60, len(candles_1m))):
                c = candles_1m[i]
                if ny_date(c.timestamp) != ny_date(open_ts):
                    break

                levels = find_unswept_5m_levels(candles_5m, open_ts, c.timestamp)
                if levels["high"] is None or levels["low"] is None:
                    continue

                if i == start_1m:
                    events_log.append({
                        "timestamp": to_iso(open_ts),
                        "type": "structural_boundaries",
                        "swing_high": round(levels["high"], 5),
                        "swing_low": round(levels["low"], 5),
                        "description": (
                            f"Unswept 5M swing high: {levels['high']:.5f}, "
                            f"swing low: {levels['low']:.5f}"
                        ),
                    })

                low_swept = c.low < levels["low"]
                high_swept = c.high > levels["high"]

                if low_swept and fib_target_2_5 <= c.low <= fib_target_2_0:
                    mss_found, ifvg_found = _structure_at_bar(candles_1m, i, "bullish")
                    if mss_found or ifvg_found:
                        entry_candle = c
                        entry_price = entry_candle.close
                        past = past_slice(candles_1m, i)
                        sl = min(x.low for x in past[max(0, len(past) - 6):]) - 0.0005
                        risk = abs(entry_price - sl)
                        tp = entry_price + (2 * risk)

                        events_log.append({
                            "timestamp": to_iso(entry_candle.timestamp),
                            "type": "entry_trigger",
                            "direction": "long",
                            "trigger": "mss" if mss_found else "ifvg",
                            "description": (
                                f"1M MSS/iFVG at fib zone after low sweep - entry at {entry_price:.5f}"
                            ),
                        })

                        day_trade = {
                            "trade_number": len(trades) + 1,
                            "entry_time": to_iso(entry_candle.timestamp),
                            "direction": "long",
                            "entry_price": round(entry_price, 5),
                            "stop_loss": round(sl, 5),
                            "take_profit": round(tp, 5),
                            "session": anchor_name,
                            "reason": (
                                f"{anchor_name} session: 5M swing low swept into "
                                f"-2.0/-2.5 fib zone + 1M MSS/iFVG"
                            ),
                            "events": list(events_log),
                            "_entry_idx": i,
                        }
                        break

                elif high_swept and fib_target_2_0 <= c.high <= fib_target_2_5:
                    mss_found, ifvg_found = _structure_at_bar(candles_1m, i, "bearish")
                    if mss_found or ifvg_found:
                        entry_candle = c
                        entry_price = entry_candle.close
                        past = past_slice(candles_1m, i)
                        sl = max(x.high for x in past[max(0, len(past) - 6):]) + 0.0005
                        risk = abs(entry_price - sl)
                        tp = entry_price - (2 * risk)

                        events_log.append({
                            "timestamp": to_iso(entry_candle.timestamp),
                            "type": "entry_trigger",
                            "direction": "short",
                            "description": (
                                f"1M MSS/iFVG at fib zone after high sweep - entry at {entry_price:.5f}"
                            ),
                        })

                        day_trade = {
                            "trade_number": len(trades) + 1,
                            "entry_time": to_iso(entry_candle.timestamp),
                            "direction": "short",
                            "entry_price": round(entry_price, 5),
                            "stop_loss": round(sl, 5),
                            "take_profit": round(tp, 5),
                            "session": anchor_name,
                            "reason": f"{anchor_name} session: 5M swing high swept into fib zone + 1M MSS/iFVG",
                            "events": list(events_log),
                            "_entry_idx": i,
                        }
                        break

            if day_trade is None:
                continue

            entry_idx = day_trade.pop("_entry_idx")
            exit_info = simulate_exits(
                candles_1m, entry_idx, candles_1m[entry_idx].timestamp,
                "long" if day_trade["direction"] == "long" else "short",
                day_trade["stop_loss"], day_trade["take_profit"],
            )
            day_trade.update(exit_info)
            trades.append(day_trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 17: 1H Pattern (1H, 1M Strategy)")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV")
    parser.add_argument("--csv5m", required=True, help="5-minute CSV")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_1h = load_csv(args.csv1h)
    candles_5m = load_csv(args.csv5m)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv1h)
    output = args.output or f"strategy_17_results_{meta['symbol']}.json"
    run_strategy(candles_1h, candles_5m, candles_1m, output)


if __name__ == "__main__":
    main()
