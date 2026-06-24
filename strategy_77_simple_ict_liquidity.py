#!/usr/bin/env python3
"""
Strategy 77: Simple ICT Liquidity Trading Strategy

Source: Faiz SMC - "Simple ICT Liquidity Trading Strategy That Makes $500/Day"
Video: https://www.youtube.com/watch?v=Zqw2tDMGqqA

Core concepts:
  - 4H chart: identify swing high/low (3-candle fractal)
  - Mark high/low of the third candle → liquidity levels
  - Next 4H candle: wait for sweep of those levels
  - 5m MSS + entry via OB/FVG

Usage:
  python strategy_77_simple_ict_liquidity.py --csv4h 4h.csv --csv5m 5m.csv

FIXED: Causal backtest — per-day loop; 4H swings from bars closed before session;
5m MSS via mss_events_up_to and FVG via detect_fvg_as_of; simulate_exits for TP/SL.
"""

import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows,
    save_trades, index_at_or_after,
)
from causal_backtest import (
    group_by_ny_day,
    ny_date,
    past_slice,
    mss_events_up_to,
    detect_fvg_as_of,
    simulate_exits,
)


def find_4h_swing_liquidity(candles_4h: list[Candle]) -> Optional[dict]:
    if len(candles_4h) < 3:
        return None
    sh = swing_highs(candles_4h)
    sl = swing_lows(candles_4h)

    if sh:
        idx = sh[-1]
        if idx + 1 >= len(candles_4h):
            return None
        liquid_high = candles_4h[idx].high
        liquid_low = candles_4h[idx].low
        return {"type": "swing_high", "idx": idx, "liquidity_levels": (liquid_low, liquid_high)}
    if sl:
        idx = sl[-1]
        if idx + 1 >= len(candles_4h):
            return None
        liquid_high = candles_4h[idx].high
        liquid_low = candles_4h[idx].low
        return {"type": "swing_low", "idx": idx, "liquidity_levels": (liquid_low, liquid_high)}
    return None


def find_entry_5m_causal(
    candles_5m: list[Candle], liquidity: dict, start_ts: int
) -> Optional[dict]:
    start_idx = index_at_or_after(candles_5m, start_ts)
    liq_low, liq_high = liquidity["liquidity_levels"]

    for i in range(start_idx, len(candles_5m) - 2):
        c = candles_5m[i]
        swept_high = c.high > liq_high
        swept_low = c.low < liq_low

        if not swept_high and not swept_low:
            continue

        for j in range(i + 1, min(i + 15, len(candles_5m) - 1)):
            mss_list = mss_events_up_to(candles_5m, j, lookback=3)
            direction = None
            for ev in mss_list:
                if ev["idx"] != j:
                    continue
                if swept_low and ev["direction"] == "bullish":
                    direction = "long"
                elif swept_high and ev["direction"] == "bearish":
                    direction = "short"
            if direction is None:
                continue

            fvgs = detect_fvg_as_of(candles_5m, j)
            if not fvgs:
                continue

            entry_idx = j + 1
            if entry_idx >= len(candles_5m):
                continue
            entry_c = candles_5m[entry_idx]
            entry_price = entry_c.close
            past = past_slice(candles_5m, j)
            local_low = min(x.low for x in past[max(0, len(past) - 4) :])
            local_high = max(x.high for x in past[max(0, len(past) - 4) :])
            sl = (
                local_low - (local_low * 0.0005)
                if direction == "long"
                else local_high + (local_high * 0.0005)
            )
            risk = abs(entry_price - sl)
            tp = entry_price + (2 * risk) if direction == "long" else entry_price - (2 * risk)

            return {
                "entry_idx": entry_idx,
                "entry_price": entry_price,
                "direction": direction,
                "sl": sl,
                "tp": tp,
                "sweep_idx": i,
                "swept_level": liq_low if swept_low else liq_high,
                "description": (
                    f"4H liquidity {liq_low if swept_low else liq_high:.5f} swept "
                    f"+ 5m MSS at {entry_price:.5f}"
                ),
            }
    return None


def run_strategy(candles_4h: list[Candle], candles_5m: list[Candle], output_path: str):
    trades = []
    days_5m = group_by_ny_day(candles_5m)
    h4_accum: list[Candle] = []
    h4_by_day = group_by_ny_day(candles_4h)
    day_idx = 0

    for day_5m in days_5m:
        day_date = ny_date(day_5m[0].timestamp)
        while day_idx < len(h4_by_day) and ny_date(h4_by_day[day_idx][0].timestamp) <= day_date:
            h4_accum.extend(h4_by_day[day_idx])
            day_idx += 1

        liquidity = find_4h_swing_liquidity(h4_accum)
        if liquidity is None:
            continue

        liq_ts = h4_accum[liquidity["idx"]].timestamp
        liq_low, liq_high = liquidity["liquidity_levels"]
        next_4h_ts = liq_ts + (4 * 3600)

        events_log = [{
            "timestamp": to_iso(liq_ts),
            "type": "liquidity_levels_marked",
            "high": round(liq_high, 5),
            "low": round(liq_low, 5),
            "description": (
                f"4H {liquidity['type']}: liquidity high={liq_high:.5f}, low={liq_low:.5f}"
            ),
        }]

        result = find_entry_5m_causal(day_5m, liquidity, next_4h_ts)
        if result is None:
            continue

        events_log.append({
            "timestamp": to_iso(day_5m[result["sweep_idx"]].timestamp),
            "type": "liquidity_sweep",
            "swept_level": round(result["swept_level"], 5),
            "description": f"Liquidity level {result['swept_level']:.5f} swept",
        })
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
            "reason": result["description"],
            "events": events_log,
            **exit_info,
        }
        trades.append(trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 77: Simple ICT Liquidity")
    parser.add_argument("--csv4h", required=True, help="4-hour CSV")
    parser.add_argument("--csv5m", required=True, help="5-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_4h = load_csv(args.csv4h)
    candles_5m = load_csv(args.csv5m)

    meta = parse_csv_filename(args.csv4h)
    output = args.output or f"strategy_77_results_{meta['symbol']}.json"
    run_strategy(candles_4h, candles_5m, output)


if __name__ == "__main__":
    main()
