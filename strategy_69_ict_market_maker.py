#!/usr/bin/env python3
"""
Strategy 69: ICT Market Maker Model (MMXM)

Source: Faiz SMC - "Make 'F*ck You' Money With This Simple ICT MMXM Strategy"
Video: https://www.youtube.com/watch?v=06DxvYmOXP4

Core concepts:
  - 4H timeframe for trend direction
  - Mark lowest (bullish) or highest (bearish) point before BOS
  - Wait for sweep of that liquidity level
  - 15m MSS with strong displacement after sweep
  - Entry at retest of FVG or breaker block formed during shift

Usage:
  python strategy_69_ict_market_maker.py --csv4h 4h.csv --csv15m 15m.csv [--csv1m 1m.csv]

FIXED: Causal backtest — per-day 4H walk-forward; MSS/FVG from data available at
decision bar (mss_events_up_to, detect_fvg_as_of); simulate_exits for TP/SL.
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
    mss_events_up_to,
    detect_fvg_as_of,
    simulate_exits,
)


def trend_4h(candles_4h: list[Candle]) -> str:
    if len(candles_4h) < 6:
        return "neutral"
    last = candles_4h[-6:]
    hh = sum(1 for i in range(1, len(last)) if last[i].high > last[i - 1].high)
    ll = sum(1 for i in range(1, len(last)) if last[i].low < last[i - 1].low)
    if hh >= 4:
        return "bullish"
    if ll >= 4:
        return "bearish"
    return "neutral"


def find_liquidity_level(candles_4h: list[Candle], trend: str) -> Optional[dict]:
    sh = swing_highs(candles_4h)
    sl = swing_lows(candles_4h)

    if trend == "bullish" and sl:
        level = candles_4h[sl[-1]].low
        return {"type": "swing_low", "level": level, "idx": sl[-1]}
    if trend == "bearish" and sh:
        level = candles_4h[sh[-1]].high
        return {"type": "swing_high", "level": level, "idx": sh[-1]}
    return None


def find_entry_15m_causal(
    candles_15m: list[Candle], liq_level: float, liq_type: str, start_idx: int
) -> Optional[dict]:
    for i in range(start_idx, len(candles_15m) - 2):
        c = candles_15m[i]

        swept = False
        if liq_type == "swing_low" and c.low < liq_level:
            swept = True
        elif liq_type == "swing_high" and c.high > liq_level:
            swept = True
        if not swept:
            continue

        for j in range(i + 1, min(i + 15, len(candles_15m) - 1)):
            cj = candles_15m[j]
            past = past_slice(candles_15m, j)
            body = abs(cj.close - cj.open)
            avg_body = sum(abs(x.close - x.open) for x in past[max(0, len(past) - 6) : -1]) / max(
                1, min(5, len(past) - 1)
            )
            if body <= avg_body * 1.5:
                continue

            mss_list = mss_events_up_to(candles_15m, j, lookback=3)
            direction = None
            for ev in mss_list:
                if ev["idx"] != j:
                    continue
                if liq_type == "swing_low" and ev["direction"] == "bullish":
                    direction = "long"
                elif liq_type == "swing_high" and ev["direction"] == "bearish":
                    direction = "short"
            if direction is None:
                continue

            fvgs = detect_fvg_as_of(candles_15m, j)
            if not fvgs:
                continue

            entry_idx = j + 1
            if entry_idx >= len(candles_15m):
                continue
            entry_c = candles_15m[entry_idx]
            entry_price = entry_c.close
            past_j = past_slice(candles_15m, j)
            local_low = min(x.low for x in past_j[max(0, len(past_j) - 4) :])
            local_high = max(x.high for x in past_j[max(0, len(past_j) - 4) :])
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
                "description": (
                    f"MMXM: {liq_type} {liq_level:.5f} swept + 15m MSS + FVG at {entry_price:.5f}"
                ),
            }
    return None


def run_strategy(candles_4h: list[Candle], candles_15m: list[Candle], output_path: str):
    trades = []
    days_4h = group_by_ny_day(candles_4h)
    h4_accum: list[Candle] = []

    for day_4h in days_4h:
        h4_accum.extend(day_4h)
        if len(h4_accum) < 6:
            continue

        trend = trend_4h(h4_accum)
        if trend == "neutral":
            continue

        liq = find_liquidity_level(h4_accum, trend)
        if liq is None:
            continue

        day_date = ny_date(day_4h[0].timestamp)
        day_15m = [c for c in candles_15m if ny_date(c.timestamp) == day_date]
        if not day_15m:
            continue

        liq_ts = h4_accum[liq["idx"]].timestamp
        start_15m = next((i for i, c in enumerate(day_15m) if c.timestamp >= liq_ts), 0)

        result = find_entry_15m_causal(day_15m, liq["level"], liq["type"], start_15m)
        if result is None:
            continue

        events_log = [{
            "timestamp": to_iso(h4_accum[-1].timestamp),
            "type": "htf_trend",
            "trend": trend,
            "description": f"4H trend: {trend}",
        }]
        events_log.append({
            "timestamp": to_iso(h4_accum[liq["idx"]].timestamp),
            "type": "liquidity_level",
            "level": round(liq["level"], 5),
            "description": f"Liquidity {liq['type']} at {liq['level']:.5f}",
        })
        events_log.append({
            "timestamp": to_iso(day_15m[result["sweep_idx"]].timestamp),
            "type": "liquidity_sweep",
            "description": f"Liquidity {liq['type']} swept",
        })
        events_log.append({
            "timestamp": to_iso(day_15m[result["entry_idx"]].timestamp),
            "type": "entry_trigger",
            "direction": result["direction"],
            "description": result["description"],
        })

        entry_c = day_15m[result["entry_idx"]]
        exit_info = simulate_exits(
            day_15m,
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
    parser = argparse.ArgumentParser(description="Strategy 69: ICT Market Maker Model")
    parser.add_argument("--csv4h", required=True, help="4-hour CSV")
    parser.add_argument("--csv15m", required=True, help="15-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_4h = load_csv(args.csv4h)
    candles_15m = load_csv(args.csv15m)

    meta = parse_csv_filename(args.csv4h)
    output = args.output or f"strategy_69_results_{meta['symbol']}.json"
    run_strategy(candles_4h, candles_15m, output)


if __name__ == "__main__":
    main()
