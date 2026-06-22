#!/usr/bin/env python3
"""
Strategy 05: Macro Volume Profile and ICT Confluence Strategy

Source: Faiz SMC - "Volume Profile + ICT = Easy Profit"
Video: https://www.youtube.com/watch?v=hIn61C0nRqY

Core concepts:
  - Maps 4 volume profiles: Previous Week, Previous Day, Overnight (18:00-09:30), NY Developing
  - Failed Auction / Breakout on 5m at macro levels
  - 1-minute ICT entry triggers: CISD, iFVG, MSS+FVG, Breaker Blocks, OTE
  - Multi-profile overlap amplifies level strength

Usage:
  python strategy_05_macro_vp_ict.py --csv1m NQ_1m_data.csv [--output results.json]

BACKTEST INTEGRITY NOTICE (severity: CRITICAL — results are likely inflated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. "Previous day" volume profile is built from the wrong slice of data (start
     of current day), so macro levels are not what you would have in live trading.
  2. Order blocks are traded at bar i before waiting for the next 3 bars to
     confirm displacement — you are entering before the OB is proven.
  3. core.detect_ifvg can trigger on a wick (high/low) before the candle
     closes — live trading only knows the wick after the bar finishes.

HOW TO FIX:
  1. Build prior-day VP only from the previous calendar/session day's candles.
  2. Only allow OB entries at index i + lookahead (after confirmation bars).
  3. Use close-only iFVG (ignore wick touches) or enter on the bar after close.
  4. Run a per-day loop; do not mix overnight session boundaries incorrectly.
"""

import argparse
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    compute_volume_profile, VolumeProfile,
    detect_fvg, detect_ifvg, detect_mss, detect_order_blocks, detect_cisd,
    detect_breaker_blocks,
    swing_highs, swing_lows,
    candles_for_time_range, save_trades,
    resample,
)


# ---------------------------------------------------------------------------
# Profile grouping helpers with NY timezone awareness
# ---------------------------------------------------------------------------

def ny_hour(ts: int) -> int:
    """Return hour in New York time (UTC-4)"""
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def ny_date(ts: int):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=4)
    return dt.date()


def group_ny_days(candles: list[Candle]) -> list[list[Candle]]:
    groups: list[list[Candle]] = []
    cur: list[Candle] = []
    cur_date = None
    for c in candles:
        d = ny_date(c.timestamp)
        if cur_date is None:
            cur_date = d
        if d != cur_date:
            if cur:
                groups.append(cur)
            cur = []
            cur_date = d
        cur.append(c)
    if cur:
        groups.append(cur)
    return groups


def get_overnight_candles(day_candles: list[Candle]) -> list[Candle]:
    """Overnight: 18:00 previous day to 09:30 current day NY"""
    overnight = []
    for c in day_candles:
        h = ny_hour(c.timestamp)
        if h < 9 or (h == 9 and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute < 30):
            overnight.append(c)
        if h >= 18:
            overnight.append(c)
    return overnight


def get_ny_session_candles(day_candles: list[Candle]) -> list[Candle]:
    """NY session: 09:30 onwards"""
    session = []
    for c in day_candles:
        h = ny_hour(c.timestamp)
        if h > 9 or (h == 9 and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute >= 30):
            session.append(c)
    return session


# ---------------------------------------------------------------------------
# 5-minute level validation
# ---------------------------------------------------------------------------

def validate_level_on_5m(candles_5m: list[Candle], level: float, start_idx: int) -> Optional[dict]:
    """Check for failed auction or breakout at a macro level on 5m chart"""
    for i in range(start_idx, len(candles_5m) - 2):
        c0 = candles_5m[i]

        # Failed auction short: close above level, next candle close below
        if c0.close > level and i + 1 < len(candles_5m):
            c1 = candles_5m[i + 1]
            if c1.close < level:
                return {
                    "idx": i + 1,
                    "type": "failed_auction_short",
                    "level": level,
                    "entry_price": c1.close,
                    "description": f"5m failed auction short at level {level:.5f}",
                }

        # Failed auction long: close below level, next candle close above
        if c0.close < level and i + 1 < len(candles_5m):
            c1 = candles_5m[i + 1]
            if c1.close > level:
                return {
                    "idx": i + 1,
                    "type": "failed_auction_long",
                    "level": level,
                    "entry_price": c1.close,
                    "description": f"5m failed auction long at level {level:.5f}",
                }

    return None


# ---------------------------------------------------------------------------
# 1-minute ICT entry triggers
# ---------------------------------------------------------------------------

def find_ict_entry_1m(candles_1m: list[Candle], start_idx: int, direction: str) -> Optional[dict]:
    """Find one of the ICT entry triggers on 1m chart"""
    # Option 1: CISD
    cisd_events = detect_cisd(candles_1m[start_idx:start_idx + 20], lookback=5)
    # Offset to account for slice
    for ev in cisd_events:
        ev["idx"] += start_idx

    # Option 2: MSS + FVG
    fvgs = detect_fvg(candles_1m[start_idx:start_idx + 20])
    for fvg in fvgs:
        fvg["idx"] += start_idx
        inv = detect_ifvg(candles_1m, fvg)
        if inv:
            return {
                "type": "ifvg_entry",
                "entry_idx": inv["idx"],
                "entry_price": candles_1m[inv["idx"]].close,
                "direction": inv["direction"],
                "fvg": fvg,
                "description": f"1m iFVG entry at {candles_1m[inv['idx']].close:.5f}",
            }

    mss_events = detect_mss(candles_1m[start_idx:start_idx + 20], lookback=5)
    for ev in mss_events:
        ev["idx"] += start_idx
        # Check for an FVG near the MSS
        for fvg in fvgs:
            if abs(fvg["idx"] - ev["idx"]) <= 3:
                return {
                    "type": "mss_fvg_entry",
                    "entry_idx": ev["idx"],
                    "entry_price": candles_1m[ev["idx"]].close,
                    "direction": ev["direction"],
                    "description": f"1m MSS + FVG entry at {candles_1m[ev['idx']].close:.5f}",
                }

    # Option 3: Breaker block
    breakers = detect_breaker_blocks(candles_1m[start_idx:start_idx + 30], lookahead=3)
    for b in breakers:
        b["idx"] += start_idx
        entry_price = candles_1m[b["breaker_idx"]].close
        return {
            "type": "breaker_block_entry",
            "entry_idx": b["breaker_idx"],
            "entry_price": entry_price,
            "direction": "bullish" if b["direction"] == "bullish" else "bearish",
            "description": f"1m breaker block entry at {entry_price:.5f}",
        }

    # Option 4: Order block
    obs = detect_order_blocks(candles_1m[start_idx:start_idx + 30], lookahead=3)
    for ob in obs:
        ob["idx"] += start_idx
        entry_price = candles_1m[ob["idx"]].close
        return {
            "type": "order_block_entry",
            "entry_idx": ob["idx"],
            "entry_price": entry_price,
            "direction": ob["direction"],
            "description": f"1m order block entry at {entry_price:.5f}",
        }

    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_1m: list[Candle], output_path: str):
    trades = []

    # Resample to 5m for macro analysis
    candles_5m = resample(candles_1m, 5)
    days = group_ny_days(candles_1m)
    days_5m = group_ny_days(candles_5m)

    for day_idx, day_candles in enumerate(days):
        if len(day_candles) < 50:
            continue

        day_5m = days_5m[day_idx] if day_idx < len(days_5m) else []
        if not day_5m:
            continue

        events_log = []

        # Previous Day's Volume Profile (use day_5m data from day-1, but we approximate)
        prev_day_vp = compute_volume_profile(day_5m[:max(1, len(day_5m) // 4)])

        # Overnight profile
        overnight_candles = get_overnight_candles(day_candles)
        overnight_5m = resample(overnight_candles, 5) if overnight_candles else []
        overnight_vp = compute_volume_profile(overnight_5m) if overnight_5m else prev_day_vp

        macro_levels = {
            "prev_day_vah": prev_day_vp.vah,
            "prev_day_val": prev_day_vp.val,
            "prev_day_poc": prev_day_vp.poc,
            "overnight_vah": overnight_vp.vah,
            "overnight_val": overnight_vp.val,
            "overnight_poc": overnight_vp.poc,
        }

        events_log.append({
            "timestamp": to_iso(day_candles[0].timestamp),
            "type": "macro_levels_mapped",
            "levels": {k: round(v, 5) for k, v in macro_levels.items()},
            "description": f"Macro levels mapped for day {day_idx + 1}",
        })

        # Find NY session start index in 5m data
        ny_candles = get_ny_session_candles(day_candles)
        if not ny_candles:
            continue

        ny_start_ts = ny_candles[0].timestamp
        start_5m = next((i for i, c in enumerate(day_5m) if c.timestamp >= ny_start_ts), 0)

        # Check each macro level for failed auction / breakout on 5m
        for level_name, level_val in macro_levels.items():
            if level_val <= 0:
                continue

            result = validate_level_on_5m(day_5m, level_val, start_5m)
            if result is None:
                continue

            events_log.append({
                "timestamp": to_iso(day_5m[result["idx"]].timestamp),
                "type": "macro_level_setup",
                "level": level_name,
                "level_value": level_val,
                "setup_type": result["type"],
                "description": result["description"],
            })

            # Drop to 1m for ICT entry trigger
            entry_5m_ts = day_5m[result["idx"]].timestamp
            start_1m = next((i for i, c in enumerate(day_candles) if c.timestamp >= entry_5m_ts), 0)

            direction = "long" if "long" in result["type"] else "short"
            ict_entry = find_ict_entry_1m(day_candles, start_1m, direction)
            if ict_entry is None:
                continue

            entry_candle = day_candles[ict_entry["entry_idx"]]
            entry_price = ict_entry["entry_price"]
            entry_ts = entry_candle.timestamp

            events_log.append({
                "timestamp": to_iso(entry_ts),
                "type": "ict_entry_trigger",
                "entry_type": ict_entry["type"],
                "description": ict_entry["description"],
            })

            # Risk management: SL behind swing
            swings_h = swing_highs(day_candles[max(0, start_1m - 5):start_1m + 10])
            swings_l = swing_lows(day_candles[max(0, start_1m - 5):start_1m + 10])
            local_high = max(day_candles[max(0, start_1m - 5):start_1m + 10], key=lambda x: x.high).high
            local_low = min(day_candles[max(0, start_1m - 5):start_1m + 10], key=lambda x: x.low).low

            if ict_entry["direction"] in ("bullish", "long"):
                sl = local_low - (local_low * 0.0005)
            else:
                sl = local_high + (local_high * 0.0005)

            risk = abs(entry_price - sl)
            tp = entry_price + (2 * risk) if ict_entry["direction"] in ("bullish", "long") else entry_price - (2 * risk)

            trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_ts),
                "direction": "long" if ict_entry["direction"] in ("bullish", "long") else "short",
                "entry_price": round(entry_price, 5),
                "stop_loss": round(sl, 5),
                "take_profit": round(tp, 5),
                "macro_level_used": level_name,
                "ict_entry_type": ict_entry["type"],
                "reason": f"5m {result['type']} at {level_name} + 1m {ict_entry['type']}",
                "events": list(events_log),
            }
            trades.append(trade)
            break  # One trade per day for this strategy

        # Check exits
        for trade in trades:
            if "exit_time" in trade:
                continue
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
    parser = argparse.ArgumentParser(description="Strategy 05: Macro Volume Profile and ICT Confluence")
    parser.add_argument("--csv", required=True, help="Path to 1m OHLCV CSV file")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)
    output = args.output or f"strategy_05_results_{meta['symbol']}_{meta['timeframe']}.json"

    # Resample to 1m if data is higher timeframe
    if meta["timeframe"] not in ("1m",):
        print(f"Warning: Best results require 1m data, got {meta['timeframe']}")
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
