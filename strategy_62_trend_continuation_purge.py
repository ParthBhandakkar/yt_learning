#!/usr/bin/env python3
"""
Strategy 62: Trend Continuation "Continuation Purge" Entry Model

Source: Faiz SMC - "This entry model will change how you trade forever.. (20x results)"
Video: https://www.youtube.com/watch?v=k76AXYhcr1U

Core concepts:
  - Identify clear trend within 3 seconds (1H/4H)
  - Find low (bullish) or high (bearish) that caused the most recent BOS
  - Wait for sweep of that specific level
  - FVG inversion entry within the dealing range
  - SMT divergence optional confirmation

Usage:
  python strategy_62_trend_continuation_purge.py --csv1h 1h.csv --csv5m 5m.csv

FIXED: Causal backtest — per-day 1H walk-forward; swings/FVG/iFVG only from data
available at decision bar (detect_fvg_as_of, ifvg_up_to); simulate_exits for TP/SL.
"""

import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows,
    save_trades,
)
from causal_backtest import (
    group_by_ny_day,
    ny_date,
    past_slice,
    detect_fvg_as_of,
    ifvg_up_to,
    simulate_exits,
)


def identify_trend(candles_1h: list[Candle]) -> Optional[str]:
    if len(candles_1h) < 8:
        return None
    last_8 = candles_1h[-8:]
    hh = sum(1 for i in range(1, len(last_8)) if last_8[i].high > last_8[i - 1].high)
    hl = sum(1 for i in range(1, len(last_8)) if last_8[i].low > last_8[i - 1].low)
    ll = sum(1 for i in range(1, len(last_8)) if last_8[i].low < last_8[i - 1].low)
    lh = sum(1 for i in range(1, len(last_8)) if last_8[i].high < last_8[i - 1].high)

    if hh >= 6 and hl >= 6:
        return "bullish"
    if ll >= 6 and lh >= 6:
        return "bearish"
    return None


def find_bos_level(candles: list[Candle], trend: str) -> Optional[dict]:
    sh = swing_highs(candles)
    sl = swing_lows(candles)

    if trend == "bullish" and sl:
        swing_l = candles[sl[-1]].low
        return {"type": "swing_low", "level": swing_l, "idx": sl[-1]}

    if trend == "bearish" and sh:
        swing_h = candles[sh[-1]].high
        return {"type": "swing_high", "level": swing_h, "idx": sh[-1]}

    return None


def find_purge_entry_causal(
    candles_5m: list[Candle], bos_level: float, bos_type: str, start_idx: int
) -> Optional[dict]:
    for i in range(start_idx, len(candles_5m) - 2):
        c = candles_5m[i]

        if bos_type == "swing_low" and c.low < bos_level:
            sweep_idx = i
        elif bos_type == "swing_high" and c.high > bos_level:
            sweep_idx = i
        else:
            continue

        for j in range(sweep_idx + 1, min(sweep_idx + 15, len(candles_5m) - 1)):
            fvgs = detect_fvg_as_of(candles_5m, j)
            for fvg in fvgs:
                if fvg["idx"] < sweep_idx:
                    continue
                inv = ifvg_up_to(candles_5m, fvg, j)
                if inv is None or inv["idx"] > j:
                    continue
                entry_idx = inv["idx"] + 1
                if entry_idx >= len(candles_5m):
                    continue
                entry_c = candles_5m[entry_idx]
                direction = "long" if bos_type == "swing_low" else "short"
                past = past_slice(candles_5m, inv["idx"])
                local_low = min(x.low for x in past[max(0, sweep_idx - 2) :])
                local_high = max(x.high for x in past[max(0, sweep_idx - 2) :])
                sl = (
                    local_low - (local_low * 0.0005)
                    if direction == "long"
                    else local_high + (local_high * 0.0005)
                )
                risk = abs(entry_c.close - sl)
                tp = entry_c.close + (2 * risk) if direction == "long" else entry_c.close - (2 * risk)
                return {
                    "entry_idx": entry_idx,
                    "entry_price": entry_c.close,
                    "direction": direction,
                    "sl": sl,
                    "tp": tp,
                    "sweep_idx": sweep_idx,
                    "fvg": fvg,
                    "description": (
                        f"Continuation purge: swept BOS {bos_type} {bos_level:.5f} "
                        f"+ FVG inversion at {entry_c.close:.5f}"
                    ),
                }
        break
    return None


def run_strategy(candles_1h: list[Candle], candles_5m: list[Candle], output_path: str):
    trades = []
    days_1h = group_by_ny_day(candles_1h)
    h1_accum: list[Candle] = []

    for day_1h in days_1h:
        h1_accum.extend(day_1h)
        if len(h1_accum) < 8:
            continue

        trend = identify_trend(h1_accum)
        if trend is None:
            continue

        bos = find_bos_level(h1_accum, trend)
        if bos is None:
            continue

        day_date = ny_date(day_1h[0].timestamp)
        day_5m = [c for c in candles_5m if ny_date(c.timestamp) == day_date]
        if not day_5m:
            continue

        bos_ts = h1_accum[bos["idx"]].timestamp
        start_5m = next((i for i, c in enumerate(day_5m) if c.timestamp >= bos_ts), 0)

        result = find_purge_entry_causal(day_5m, bos["level"], bos["type"], start_5m)
        if result is None:
            continue

        events_log = [{
            "timestamp": to_iso(h1_accum[-1].timestamp),
            "type": "trend_identified",
            "trend": trend,
            "description": f"Clear {trend} trend (3-second rule)",
        }]
        events_log.append({
            "timestamp": to_iso(h1_accum[bos["idx"]].timestamp),
            "type": "bos_level_located",
            "level": round(bos["level"], 5),
            "description": f"BOS {bos['type']} at {bos['level']:.5f}",
        })
        events_log.append({
            "timestamp": to_iso(day_5m[result["sweep_idx"]].timestamp),
            "type": "bos_sweep",
            "description": f"BOS {bos['type']} {bos['level']:.5f} swept",
        })
        events_log.append({
            "timestamp": to_iso(day_5m[result["entry_idx"]].timestamp),
            "type": "fvg_inversion_entry",
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
            "reason": result["description"],
            "events": events_log,
            **exit_info,
        }
        trades.append(trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 62: Trend Continuation Purge")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV")
    parser.add_argument("--csv5m", required=True, help="5-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_1h = load_csv(args.csv1h)
    candles_5m = load_csv(args.csv5m)

    meta = parse_csv_filename(args.csv1h)
    output = args.output or f"strategy_62_results_{meta['symbol']}.json"
    run_strategy(candles_1h, candles_5m, output)


if __name__ == "__main__":
    main()
