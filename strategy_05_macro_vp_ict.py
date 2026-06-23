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
  FIXED: Prior-day VP, order_block_entry_idx, ifvg_up_to/mss_events_up_to, simulate_exits.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    compute_volume_profile,
    detect_order_blocks, detect_breaker_blocks, detect_cisd,
    save_trades,
    resample,
)
from causal_backtest import (
    group_by_ny_day,
    ny_hour,
    past_slice,
    detect_fvg_as_of,
    ifvg_up_to,
    mss_events_up_to,
    order_block_entry_idx,
    simulate_exits,
)


def get_overnight_candles(day_candles: list[Candle]) -> list[Candle]:
    """Overnight: 18:00 previous day to 09:30 current day NY"""
    overnight = []
    for c in day_candles:
        h = ny_hour(c.timestamp)
        dt = datetime.fromtimestamp(c.timestamp, tz=timezone.utc)
        if h < 9 or (h == 9 and dt.minute < 30):
            overnight.append(c)
        if h >= 18:
            overnight.append(c)
    return overnight


def get_ny_session_candles(day_candles: list[Candle]) -> list[Candle]:
    """NY session: 09:30 onwards"""
    session = []
    for c in day_candles:
        h = ny_hour(c.timestamp)
        dt = datetime.fromtimestamp(c.timestamp, tz=timezone.utc)
        if h > 9 or (h == 9 and dt.minute >= 30):
            session.append(c)
    return session


def validate_level_on_5m(candles_5m: list[Candle], level: float, start_idx: int, as_of_idx: int) -> Optional[dict]:
    """Check for failed auction at a macro level on 5m chart (causal up to as_of_idx)."""
    end = min(as_of_idx, len(candles_5m) - 2)
    for i in range(start_idx, end):
        c0 = candles_5m[i]
        if c0.close > level and i + 1 <= as_of_idx:
            c1 = candles_5m[i + 1]
            if c1.close < level:
                return {
                    "idx": i + 1,
                    "type": "failed_auction_short",
                    "level": level,
                    "entry_price": c1.close,
                    "description": f"5m failed auction short at level {level:.5f}",
                }
        if c0.close < level and i + 1 <= as_of_idx:
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


def find_ict_entry_1m(
    candles_1m: list[Candle], start_idx: int, as_of_idx: int, direction: str
) -> Optional[dict]:
    """Find ICT entry trigger using only data up to as_of_idx."""
    for j in range(start_idx, min(as_of_idx + 1, len(candles_1m))):
        known = past_slice(candles_1m, j)

        cisd_events = detect_cisd(known, lookback=5)
        for ev in cisd_events:
            if ev["idx"] != j or ev["idx"] < start_idx:
                continue
            if direction == "long" and ev["direction"] == "bullish":
                return {
                    "type": "cisd_entry",
                    "entry_idx": j,
                    "entry_price": candles_1m[j].close,
                    "direction": "bullish",
                    "description": f"1m CISD entry at {candles_1m[j].close:.5f}",
                }
            if direction == "short" and ev["direction"] == "bearish":
                return {
                    "type": "cisd_entry",
                    "entry_idx": j,
                    "entry_price": candles_1m[j].close,
                    "direction": "bearish",
                    "description": f"1m CISD entry at {candles_1m[j].close:.5f}",
                }

        fvgs = detect_fvg_as_of(candles_1m, j)
        for fvg in fvgs:
            if fvg["idx"] < start_idx:
                continue
            inv = ifvg_up_to(candles_1m, fvg, j)
            if inv and inv["idx"] == j:
                trade_dir = "bullish" if "bullish" in inv["direction"] else "bearish"
                if (direction == "long" and trade_dir == "bullish") or (
                    direction == "short" and trade_dir == "bearish"
                ):
                    return {
                        "type": "ifvg_entry",
                        "entry_idx": j,
                        "entry_price": candles_1m[j].close,
                        "direction": trade_dir,
                        "fvg": fvg,
                        "description": f"1m iFVG entry at {candles_1m[j].close:.5f}",
                    }

        mss_events = mss_events_up_to(candles_1m, j, lookback=5)
        for ev in mss_events:
            if ev["idx"] != j or ev["idx"] < start_idx:
                continue
            for fvg in fvgs:
                if abs(fvg["idx"] - ev["idx"]) <= 3:
                    if (direction == "long" and ev["direction"] == "bullish") or (
                        direction == "short" and ev["direction"] == "bearish"
                    ):
                        return {
                            "type": "mss_fvg_entry",
                            "entry_idx": j,
                            "entry_price": candles_1m[j].close,
                            "direction": ev["direction"],
                            "description": f"1m MSS + FVG entry at {candles_1m[j].close:.5f}",
                        }

        breakers = detect_breaker_blocks(known, lookahead=3)
        for b in breakers:
            if b["breaker_idx"] == j and b["breaker_idx"] >= start_idx:
                return {
                    "type": "breaker_block_entry",
                    "entry_idx": b["breaker_idx"],
                    "entry_price": candles_1m[b["breaker_idx"]].close,
                    "direction": b["direction"],
                    "description": f"1m breaker block entry at {candles_1m[b['breaker_idx']].close:.5f}",
                }

        obs = detect_order_blocks(known, lookahead=3)
        for ob in obs:
            entry_idx = order_block_entry_idx(ob["idx"], 3)
            if entry_idx == j and entry_idx >= start_idx:
                return {
                    "type": "order_block_entry",
                    "entry_idx": entry_idx,
                    "entry_price": candles_1m[entry_idx].close,
                    "direction": ob["direction"],
                    "description": f"1m order block entry at {candles_1m[entry_idx].close:.5f}",
                }

    return None


def run_strategy(candles_1m: list[Candle], output_path: str):
    trades = []
    candles_5m = resample(candles_1m, 5)
    days = group_by_ny_day(candles_1m)
    days_5m = group_by_ny_day(candles_5m)

    for day_idx, day_candles in enumerate(days):
        if len(day_candles) < 50:
            continue

        day_5m = days_5m[day_idx] if day_idx < len(days_5m) else []
        if not day_5m:
            continue

        events_log = []

        if day_idx > 0:
            prev_day_5m = days_5m[day_idx - 1]
            prev_day_vp = compute_volume_profile(prev_day_5m)
        else:
            prev_day_vp = compute_volume_profile(day_5m[:max(1, len(day_5m) // 4)])

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

        ny_candles = get_ny_session_candles(day_candles)
        if not ny_candles:
            continue

        ny_start_ts = ny_candles[0].timestamp
        start_5m = next((i for i, c in enumerate(day_5m) if c.timestamp >= ny_start_ts), 0)
        day_traded = False

        for as_of_5m in range(start_5m, len(day_5m)):
            if day_traded:
                break

            for level_name, level_val in macro_levels.items():
                if level_val <= 0:
                    continue

                result = validate_level_on_5m(day_5m, level_val, start_5m, as_of_5m)
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

                entry_5m_ts = day_5m[result["idx"]].timestamp
                start_1m = next((i for i, c in enumerate(day_candles) if c.timestamp >= entry_5m_ts), 0)
                direction = "long" if "long" in result["type"] else "short"

                ict_entry = None
                for as_of_1m in range(start_1m, min(start_1m + 30, len(day_candles))):
                    ict_entry = find_ict_entry_1m(day_candles, start_1m, as_of_1m, direction)
                    if ict_entry is not None:
                        break

                if ict_entry is None:
                    continue

                entry_idx = ict_entry["entry_idx"]
                entry_candle = day_candles[entry_idx]
                entry_price = ict_entry["entry_price"]
                entry_ts = entry_candle.timestamp

                events_log.append({
                    "timestamp": to_iso(entry_ts),
                    "type": "ict_entry_trigger",
                    "entry_type": ict_entry["type"],
                    "description": ict_entry["description"],
                })

                known_at_entry = past_slice(day_candles, entry_idx)
                local_high = max(c.high for c in known_at_entry)
                local_low = min(c.low for c in known_at_entry)

                if ict_entry["direction"] in ("bullish", "long"):
                    sl = local_low - (local_low * 0.0005)
                    trade_dir = "long"
                else:
                    sl = local_high + (local_high * 0.0005)
                    trade_dir = "short"

                risk = abs(entry_price - sl)
                tp = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)

                exit_info = simulate_exits(day_candles, entry_idx, entry_ts, trade_dir, sl, tp)

                trade = {
                    "trade_number": len(trades) + 1,
                    "entry_time": to_iso(entry_ts),
                    "direction": trade_dir,
                    "entry_price": round(entry_price, 5),
                    "stop_loss": round(sl, 5),
                    "take_profit": round(tp, 5),
                    "macro_level_used": level_name,
                    "ict_entry_type": ict_entry["type"],
                    "reason": f"5m {result['type']} at {level_name} + 1m {ict_entry['type']}",
                    "events": list(events_log),
                    **exit_info,
                }
                trades.append(trade)
                day_traded = True
                break

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

    if meta["timeframe"] not in ("1m",):
        print(f"Warning: Best results require 1m data, got {meta['timeframe']}")
    run_strategy(candles, output)


if __name__ == "__main__":
    main()
