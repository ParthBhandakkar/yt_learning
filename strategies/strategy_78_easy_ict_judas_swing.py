#!/usr/bin/env python3
"""
Strategy 78: Easy ICT Judas Swing Trading Strategy

Source: Faiz SMC - "Easy ICT Judas Swing Trading Strategy That Works! (High Winrate)"
Video: https://www.youtube.com/watch?v=-bEF1vca1Xc

Core concepts:
  - Asian range (8PM-2AM NY) or London range (3AM-7AM NY)
  - Sweep occurs in specific windows (2AM-3AM for Asia, 7AM-8AM for London)
  - After sweep, 5m MSS with body closure
  - Entry at OB/FVG
  - Target opposite range high/low

Usage:
  python strategy_78_easy_ict_judas_swing.py --csv15m 15m.csv --csv5m 5m.csv

FIXED: Causal backtest — group_by_ny_day session ranges (Asia per calendar day);
5m MSS via mss_events_up_to; entry bar after MSS; simulate_exits for TP/SL.
"""

import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    save_trades, index_at_or_after,
)
from causal_backtest import group_by_ny_day, ny_hour, past_slice, mss_events_up_to, simulate_exits


ASIAN = {"name": "asia", "start": 20, "end": 2, "sweep_start": 2, "sweep_end": 3}
LONDON = {"name": "london", "start": 3, "end": 7, "sweep_start": 7, "sweep_end": 8}


def get_session_range(day_candles: list[Candle], session: dict) -> Optional[dict]:
    candles_in = []
    for c in day_candles:
        h = ny_hour(c.timestamp)
        if session["start"] <= session["end"]:
            if session["start"] <= h < session["end"]:
                candles_in.append(c)
        else:
            if h >= session["start"] or h < session["end"]:
                candles_in.append(c)

    if len(candles_in) < 2:
        return None
    high = max(c.high for c in candles_in)
    low = min(c.low for c in candles_in)
    return {"high": high, "low": low, "range_end_ts": candles_in[-1].timestamp}


def find_sweep_in_window(
    day_candles: list[Candle], session: dict, range_high: float, range_low: float
) -> Optional[dict]:
    for c in day_candles:
        h = ny_hour(c.timestamp)
        if session["sweep_start"] <= h < session["sweep_end"]:
            if c.high > range_high:
                return {"direction": "high_swept", "level": range_high, "timestamp": c.timestamp}
            if c.low < range_low:
                return {"direction": "low_swept", "level": range_low, "timestamp": c.timestamp}
    return None


def entry_after_sweep_causal(
    candles_5m: list[Candle], sweep_ts: int, direction: str
) -> Optional[dict]:
    start_idx = index_at_or_after(candles_5m, sweep_ts)
    for i in range(start_idx, min(start_idx + 20, len(candles_5m) - 1)):
        mss_list = mss_events_up_to(candles_5m, i, lookback=3)
        trade_dir = None
        for ev in mss_list:
            if ev["idx"] != i:
                continue
            if direction == "low_swept" and ev["direction"] == "bullish":
                trade_dir = "long"
            elif direction == "high_swept" and ev["direction"] == "bearish":
                trade_dir = "short"
        if trade_dir is None:
            continue

        entry_idx = i + 1
        if entry_idx >= len(candles_5m):
            continue
        entry_c = candles_5m[entry_idx]
        entry_price = entry_c.close
        past = past_slice(candles_5m, i)
        local_low = min(x.low for x in past[max(0, len(past) - 4) :])
        local_high = max(x.high for x in past[max(0, len(past) - 4) :])
        sl = (
            local_low - (local_low * 0.0005)
            if trade_dir == "long"
            else local_high + (local_high * 0.0005)
        )
        risk = abs(entry_price - sl)
        tp = entry_price + (2 * risk) if trade_dir == "long" else entry_price - (2 * risk)

        return {
            "entry_idx": entry_idx,
            "entry_price": entry_price,
            "direction": trade_dir,
            "sl": sl,
            "tp": tp,
            "description": f"5m MSS {trade_dir} at {entry_price:.5f} after {direction}",
        }
    return None


def run_strategy(candles_15m: list[Candle], candles_5m: list[Candle], output_path: str):
    trades = []

    for day_15m in group_by_ny_day(candles_15m):
        day_start_ts = day_15m[0].timestamp
        day_end_ts = day_15m[-1].timestamp
        day_5m = [c for c in candles_5m if day_start_ts <= c.timestamp <= day_end_ts + 86400]

        for session in [ASIAN, LONDON]:
            range_info = get_session_range(day_15m, session)
            if range_info is None:
                continue

            events_log = [{
                "timestamp": to_iso(day_15m[-1].timestamp),
                "type": f"{session['name']}_range",
                "high": round(range_info["high"], 5),
                "low": round(range_info["low"], 5),
                "description": (
                    f"{session['name'].title()} range: high={range_info['high']:.5f}, "
                    f"low={range_info['low']:.5f}"
                ),
            }]

            sweep = find_sweep_in_window(
                day_15m, session, range_info["high"], range_info["low"]
            )
            if sweep is None:
                continue

            events_log.append({
                "timestamp": to_iso(sweep["timestamp"]),
                "type": "liquidity_sweep",
                "direction": sweep["direction"],
                "description": f"{session['name'].title()} {sweep['direction']} during sweep window",
            })

            result = entry_after_sweep_causal(day_5m, sweep["timestamp"], sweep["direction"])
            if result is None:
                continue

            events_log.append({
                "timestamp": to_iso(day_5m[result["entry_idx"]].timestamp),
                "type": "entry_trigger",
                "direction": result["direction"],
                "description": result["description"],
            })

            entry_c = day_5m[result["entry_idx"]]
            exit_info = simulate_exits(
                day_5m,
                result["entry_idx"],
                entry_c.timestamp,
                result["direction"],
                result["sl"],
                result["tp"],
            )
            trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_c.timestamp),
                "direction": result["direction"],
                "entry_price": round(result["entry_price"], 5),
                "stop_loss": round(result["sl"], 5),
                "take_profit": round(result["tp"], 5),
                "session": session["name"],
                "reason": result["description"],
                "events": events_log,
                **exit_info,
            }
            trades.append(trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 78: Easy ICT Judas Swing")
    parser.add_argument("--csv15m", required=True, help="15-minute CSV (session ranges)")
    parser.add_argument("--csv5m", required=True, help="5-minute CSV (entry)")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_15m = load_csv(args.csv15m)
    candles_5m = load_csv(args.csv5m)

    meta = parse_csv_filename(args.csv15m)
    output = args.output or f"strategy_78_results_{meta['symbol']}.json"
    run_strategy(candles_15m, candles_5m, output)


if __name__ == "__main__":
    main()
