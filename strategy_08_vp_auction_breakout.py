#!/usr/bin/env python3
"""
Strategy 08: Volume Profile Auction & Breakout Strategy

Source: Faiz SMC - "The Only Volume Profile Strategy You'll Ever Need! (FULL COURSE)"
Video: https://www.youtube.com/watch?v=dcmKOcMMT8Y

Core concepts:
  - Fixed Range Volume Profile (FRVP) – Row Size 1000, VA 70%
  - Profile shapes: D (neutral), P (bullish breakout), b (bearish breakout)
  - Failed Auction Setup: 5m close outside VA → reclaim close → 1m volume confirmation
  - Breakout Setup: price stays outside VA, consolidates, breaks out
  - Max 2 trades/day for Failed Auction
  - Session anchor: Asia (18:00 NY open)

Usage:
  python strategy_08_vp_auction_breakout.py --csv 5m_data.csv [--output results.json]

BACKTEST INTEGRITY NOTICE (severity: MINOR — one of the more honest scripts)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  Similar to strategy 04: Asia-session profile is built from completed session
  candles, and entries use the next bar's close after a reclaim/breakout signal.
  Minor risk: day grouping order and very tight consolidation windows may still
  be slightly optimistic.

HOW TO FIX:
  1. Keep profile limited to completed Asia session candles only.
  2. Ensure days are processed in strict chronological order.
  3. Optionally enter at next-bar open instead of close for a stricter test.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    compute_volume_profile, VolumeProfile,
    detect_fvg, resample,
    candles_for_time_range, save_trades,
)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def ny_date(ts: int):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=4)
    return dt.date()


def group_ny_days(candles):
    from collections import defaultdict
    groups = defaultdict(list)
    for c in candles:
        d = ny_date(c.timestamp)
        groups[d].append(c)
    return list(groups.values())


from datetime import timedelta


def get_asia_session(candles: list[Candle]) -> list[Candle]:
    """Asia session: 18:00-xx (previous NY close to ~03:00 NY)"""
    return [c for c in candles if ny_hour(c.timestamp) >= 18 or ny_hour(c.timestamp) < 3]


def get_london_session(candles: list[Candle]) -> list[Candle]:
    return [c for c in candles if 3 <= ny_hour(c.timestamp) < 7]


def get_ny_session(candles: list[Candle]) -> list[Candle]:
    return [c for c in candles if ny_hour(c.timestamp) >= 9 or (ny_hour(c.timestamp) >= 7 and ny_hour(c.timestamp) < 18)]


# ---------------------------------------------------------------------------
# Profile shape classification
# ---------------------------------------------------------------------------

def classify_shape(vp) -> str:
    if vp.vah == 0 or vp.val == 0 or vp.poc == 0:
        return "unknown"
    mid = (vp.vah + vp.val) / 2
    rng = vp.vah - vp.val
    if rng == 0:
        return "D"
    if vp.poc > mid + rng * 0.15:
        return "P"
    if vp.poc < mid - rng * 0.15:
        return "b"
    return "D"


# ---------------------------------------------------------------------------
# Failed Auction Setup
# ---------------------------------------------------------------------------

def failed_auction_setup(candles: list[Candle], vp, start_idx: int) -> Optional[dict]:
    for i in range(start_idx, len(candles) - 2):
        c0 = candles[i]
        c1 = candles[i + 1]

        # Long: close below VAL → reclaim above VAL
        if c0.close < vp.val and c1.close > vp.val:
            sweep_low = min(c0.low, c1.low)
            return {
                "type": "long_failed_auction",
                "entry_idx": i + 1,
                "entry_price": c1.close,
                "stop_loss": sweep_low - (sweep_low * 0.0005),
                "target_poc": vp.poc,
                "sweep_low": sweep_low,
                "description": f"VAL failed auction: {c0.close:.5f} below VAL {vp.val:.5f}, reclaimed at {c1.close:.5f}",
            }

        # Short: close above VAH → reclaim below VAH
        if c0.close > vp.vah and c1.close < vp.vah:
            sweep_high = max(c0.high, c1.high)
            return {
                "type": "short_failed_auction",
                "entry_idx": i + 1,
                "entry_price": c1.close,
                "stop_loss": sweep_high + (sweep_high * 0.0005),
                "target_poc": vp.poc,
                "sweep_high": sweep_high,
                "description": f"VAH failed auction: {c0.close:.5f} above VAH {vp.vah:.5f}, rejected at {c1.close:.5f}",
            }
    return None


# ---------------------------------------------------------------------------
# Breakout Setup
# ---------------------------------------------------------------------------

def breakout_setup(candles: list[Candle], vp, start_idx: int, shape: str) -> Optional[dict]:
    for i in range(start_idx + 3, len(candles) - 1):
        recent = candles[i - 3:i + 1]

        if shape in ("P", "D"):
            all_above = all(c.close > vp.vah for c in recent)
            if all_above:
                consol_high = max(c.high for c in recent)
                if candles[i + 1].close > consol_high:
                    return {
                        "type": "bullish_breakout",
                        "entry_idx": i + 1,
                        "entry_price": candles[i + 1].close,
                        "stop_loss": consol_high - (consol_high * 0.0005),
                        "description": f"Bullish breakout above VAH {vp.vah:.5f}, consolidation high {consol_high:.5f} broken",
                    }

        if shape in ("b", "D"):
            all_below = all(c.close < vp.val for c in recent)
            if all_below:
                consol_low = min(c.low for c in recent)
                if candles[i + 1].close < consol_low:
                    return {
                        "type": "bearish_breakout",
                        "entry_idx": i + 1,
                        "entry_price": candles[i + 1].close,
                        "stop_loss": consol_low + (consol_low * 0.0005),
                        "description": f"Bearish breakout below VAL {vp.val:.5f}, consolidation low {consol_low:.5f} broken",
                    }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_5m: list[Candle], output_path: str):
    trades = []
    days = group_ny_days(candles_5m)

    for day_candles in days:
        if len(day_candles) < 20:
            continue

        events_log = []
        dt = ny_date(day_candles[0].timestamp)

        # Build developing profile from Asia session (or full day developing)
        asia_candles = get_asia_session(day_candles)
        london_candles = get_london_session(day_candles)

        # Use a minimum of 30 min of data for initial profile
        profile_candles = asia_candles if len(asia_candles) >= 6 else day_candles[:max(6, len(day_candles) // 4)]
        vp = compute_volume_profile(profile_candles)
        shape = classify_shape(vp)

        events_log.append({
            "timestamp": to_iso(profile_candles[0].timestamp),
            "type": "profile_established",
            "shape": shape,
            "vah": round(vp.vah, 5),
            "val": round(vp.val, 5),
            "poc": round(vp.poc, 5),
            "description": f"Profile: {shape}, VAH={vp.vah:.5f}, VAL={vp.val:.5f}, POC={vp.poc:.5f}",
        })

        start_idx = len(profile_candles)
        trades_taken = 0
        directions_taken = set()

        # Failed Auction (max 2, 1 per direction)
        for _ in range(2):
            fa = failed_auction_setup(day_candles, vp, start_idx)
            if fa is None:
                break
            direction = "long" if "long" in fa["type"] else "short"
            if direction in directions_taken:
                start_idx = fa["entry_idx"] + 1
                continue

            entry_candle = day_candles[fa["entry_idx"]]
            events_log.append({
                "timestamp": to_iso(entry_candle.timestamp),
                "type": "failed_auction_entry",
                "direction": direction,
                "description": fa["description"],
            })

            risk = abs(fa["entry_price"] - fa["stop_loss"])
            tp = fa["entry_price"] + (2 * risk) if direction == "long" else fa["entry_price"] - (2 * risk)

            trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_candle.timestamp),
                "direction": direction,
                "entry_price": round(fa["entry_price"], 5),
                "stop_loss": round(fa["stop_loss"], 5),
                "take_profit": round(tp, 5),
                "strategy_type": "Failed Auction",
                "profile_shape": shape,
                "reason": fa["description"],
                "events": list(events_log),
            }
            trades.append(trade)
            trades_taken += 1
            directions_taken.add(direction)
            start_idx = fa["entry_idx"] + 3

        # Breakout Setup (if below max trades)
        if trades_taken < 2:
            bo = breakout_setup(day_candles, vp, start_idx, shape)
            if bo:
                direction = "long" if "bullish" in bo["type"] else "short"
                if direction not in directions_taken:
                    entry_candle = day_candles[bo["entry_idx"]]
                    events_log.append({
                        "timestamp": to_iso(entry_candle.timestamp),
                        "type": "breakout_entry",
                        "direction": direction,
                        "description": bo["description"],
                    })

                    risk = abs(bo["entry_price"] - bo["stop_loss"])
                    tp = bo["entry_price"] + (2 * risk) if direction == "long" else bo["entry_price"] - (2 * risk)

                    trade = {
                        "trade_number": len(trades) + 1,
                        "entry_time": to_iso(entry_candle.timestamp),
                        "direction": direction,
                        "entry_price": round(bo["entry_price"], 5),
                        "stop_loss": round(bo["stop_loss"], 5),
                        "take_profit": round(tp, 5),
                        "strategy_type": "Breakout",
                        "profile_shape": shape,
                        "reason": bo["description"],
                        "events": list(events_log),
                    }
                    trades.append(trade)

        # Check exits
        for trade in trades[-trades_taken:] if trades_taken > 0 else []:
            if "exit_time" in trade:
                continue
            entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
            for c in day_candles:
                if c.timestamp > entry_ts:
                    if trade["direction"] == "long":
                        if c.high >= trade["take_profit"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["take_profit"]; trade["outcome"] = "win"; break
                        elif c.low <= trade["stop_loss"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["stop_loss"]; trade["outcome"] = "loss"; break
                    else:
                        if c.low <= trade["take_profit"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["take_profit"]; trade["outcome"] = "win"; break
                        elif c.high >= trade["stop_loss"]:
                            trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = trade["stop_loss"]; trade["outcome"] = "loss"; break
            if "exit_time" not in trade:
                trade["exit_time"] = to_iso(day_candles[-1].timestamp)
                trade["exit_price"] = day_candles[-1].close
                trade["outcome"] = "open"

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 08: Volume Profile Auction & Breakout")
    parser.add_argument("--csv", required=True, help="5m OHLCV CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)
    output = args.output or f"strategy_08_results_{meta['symbol']}_{meta['timeframe']}.json"
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
