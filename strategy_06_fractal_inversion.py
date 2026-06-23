#!/usr/bin/env python3
"""
Strategy 06: Fractal-Based Inversion & Order Flow Strategy

Source: Faiz SMC - "The Only Trading Strategy I'd Use If I Had To Start Over"
Video: https://www.youtube.com/watch?v=YGKTvqJIx1w

Core concepts:
  - 1H chart for macro order flow (bullish/bearish)
  - 4H/Daily for draw on liquidity (equal highs/lows)
  - 5m/15m FVG formation after 9:30 AM NY time
  - 1m inversion confirmation (all micro-FVGs must invert)
  - SMT divergence for extra confluence

Usage:
  python strategy_06_fractal_inversion.py --csv1h 1h_data.csv --csv5m 5m_data.csv --csv1m 1m_data.csv

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Scans the full history and takes the first matching setup — not one trade
     per day across years (cherry-picking the first lucky match).
  2. 1H trend bias uses the last 20 hours of the entire file, which at early
     dates still includes "future" relative to that trade day.
  3. Inversion/FVG logic uses wick-based iFVG from core.py and windows that
     include bars after the signal bar before the signal is "confirmed."

HOW TO FIX:
  1. Loop day-by-day (or bar-by-bar) and only use 1H candles that closed before
     the current session.
  2. Confirm swings/FVG only after the required extra bar(s) have closed.
  3. Use close-only inversion; enter on the next bar after confirmation.
  4. Allow multiple days of trades instead of one break on first match.

FIXED: Per-day NY loops; 1H bias from candles before session only; detect_fvg_as_of,
ifvg_up_to (close-only), past_slice for SL; simulate_exits; one trade per day max.
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
    past_slice,
    detect_fvg_as_of,
    ifvg_up_to,
    simulate_exits,
)


# ---------------------------------------------------------------------------
# Step 1: Identify 1H order flow
# ---------------------------------------------------------------------------

def classify_1h_orderflow(candles_1h: list[Candle]) -> str:
    """Classify 1H order flow as 'bullish', 'bearish', or 'neutral'"""
    if len(candles_1h) < 10:
        return "neutral"

    window = candles_1h[-20:]
    as_of_idx = len(window) - 1
    fvgs = detect_fvg_as_of(window, as_of_idx)
    bullish_fvgs = [f for f in fvgs if f["direction"] == "bullish"]
    bearish_fvgs = [f for f in fvgs if f["direction"] == "bearish"]

    recent = candles_1h[-10:]
    higher_highs = sum(1 for i in range(1, len(recent)) if recent[i].high > recent[i - 1].high)
    higher_lows = sum(1 for i in range(1, len(recent)) if recent[i].low > recent[i - 1].low)
    lower_lows = sum(1 for i in range(1, len(recent)) if recent[i].low < recent[i - 1].low)
    lower_highs = sum(1 for i in range(1, len(recent)) if recent[i].high < recent[i - 1].high)

    if higher_highs >= 6 and higher_lows >= 6 and len(bullish_fvgs) >= len(bearish_fvgs):
        return "bullish"
    if lower_lows >= 6 and lower_highs >= 6 and len(bearish_fvgs) >= len(bullish_fvgs):
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Step 2: Macro draw on liquidity (4H/Daily)
# ---------------------------------------------------------------------------

def find_macro_draw(candles_1h: list[Candle], orderflow: str) -> dict:
    """Find equal highs (for bullish) or equal lows (for bearish) as draw targets"""
    swings_h = swing_highs(candles_1h)
    swings_l = swing_lows(candles_1h)

    if orderflow == "bullish" and len(swings_h) >= 2:
        last_two = [candles_1h[i].high for i in swings_h[-2:]]
        return {"type": "equal_highs", "level": max(last_two), "targets": last_two}

    if orderflow == "bearish" and len(swings_l) >= 2:
        last_two = [candles_1h[i].low for i in swings_l[-2:]]
        return {"type": "equal_lows", "level": min(last_two), "targets": last_two}

    return {"type": "none", "level": None, "targets": []}


# ---------------------------------------------------------------------------
# Step 3: Find first 5m/15m FVG after 9:30 AM NY
# ---------------------------------------------------------------------------

def find_post_open_fvg(candles: list[Candle], orderflow: str) -> Optional[dict]:
    """Find first fresh FVG after 9:30 NY time aligned with orderflow (causal scan)."""
    start = None
    for i, c in enumerate(candles):
        h = ny_hour(c.timestamp)
        if h > 9 or (h == 9 and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute >= 30):
            start = i
            break
    if start is None:
        return None

    for j in range(start, min(start + 20, len(candles))):
        sub = past_slice(candles, j)
        for fvg in detect_fvg_as_of(sub, j):
            if fvg["idx"] < start:
                continue
            if orderflow == "bullish" and fvg["direction"] == "bullish":
                return fvg
            if orderflow == "bearish" and fvg["direction"] == "bearish":
                return fvg
    return None


# ---------------------------------------------------------------------------
# Step 4: 1m FVG inversion check
# ---------------------------------------------------------------------------

def check_1m_inversion(candles_1m: list[Candle], start_idx: int, max_bars: int = 30) -> Optional[dict]:
    """Check if ALL micro FVGs in the pullback leg get inverted (close-only)."""
    window_end = min(start_idx + max_bars, len(candles_1m))
    micro_fvgs: list[dict] = []
    seen: set[int] = set()

    for j in range(start_idx, window_end):
        sub = past_slice(candles_1m, j)
        for fvg in detect_fvg_as_of(sub, j):
            if fvg["idx"] >= start_idx and fvg["idx"] not in seen:
                seen.add(fvg["idx"])
                micro_fvgs.append(fvg)

    if not micro_fvgs:
        return None

    last_inv_idx = start_idx
    for mfvg in micro_fvgs:
        inv = None
        for k in range(mfvg["idx"] + 1, window_end):
            inv = ifvg_up_to(candles_1m, mfvg, k)
            if inv:
                last_inv_idx = max(last_inv_idx, inv["idx"])
                break
        if inv is None:
            return None

    entry_candle = candles_1m[last_inv_idx]
    return {
        "entry_idx": last_inv_idx,
        "entry_price": entry_candle.close,
        "micro_fvgs_inverted": len(micro_fvgs),
        "description": f"All {len(micro_fvgs)} micro-FVGs inverted at {entry_candle.close:.5f}",
    }


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_1h: list[Candle], candles_5m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    days_5m = group_by_ny_day(candles_5m)

    for day_5m in days_5m:
        if not day_5m:
            continue
        session_start_ts = day_5m[0].timestamp
        candles_1h_before = [c for c in candles_1h if c.timestamp < session_start_ts]
        if len(candles_1h_before) < 10:
            continue

        orderflow = classify_1h_orderflow(candles_1h_before)
        if orderflow == "neutral":
            continue

        macro_draw = find_macro_draw(candles_1h_before, orderflow)
        htf_fvg = find_post_open_fvg(day_5m, orderflow)
        if htf_fvg is None:
            continue

        events_log = [
            {
                "timestamp": to_iso(candles_1h_before[-1].timestamp),
                "type": "orderflow_identified",
                "orderflow": orderflow,
                "description": f"1H order flow: {orderflow}",
            },
            {
                "timestamp": to_iso(day_5m[htf_fvg["idx"]].timestamp),
                "type": "htf_fvg_formed",
                "direction": htf_fvg["direction"],
                "upper": round(htf_fvg["upper"], 5),
                "lower": round(htf_fvg["lower"], 5),
                "description": (
                    f"Post-open 5m {htf_fvg['direction']} FVG formed: "
                    f"{htf_fvg['lower']:.5f}-{htf_fvg['upper']:.5f}"
                ),
            },
        ]

        day_trade = None
        for i in range(htf_fvg["idx"] + 1, len(day_5m)):
            c = day_5m[i]
            if htf_fvg["direction"] == "bullish" and c.low < htf_fvg["upper"] and c.high > htf_fvg["lower"]:
                price_in_fvg = True
            elif htf_fvg["direction"] == "bearish" and c.high > htf_fvg["lower"] and c.low < htf_fvg["upper"]:
                price_in_fvg = True
            else:
                continue

            events_log.append({
                "timestamp": to_iso(c.timestamp),
                "type": "price_entered_htf_fvg",
                "description": f"Price entered 5m FVG zone at {c.close:.5f}",
            })

            start_1m = next((j for j, x in enumerate(candles_1m) if x.timestamp >= c.timestamp), 0)
            inv_result = check_1m_inversion(candles_1m, start_1m)
            if inv_result is None:
                continue

            events_log.append({
                "timestamp": to_iso(candles_1m[inv_result["entry_idx"]].timestamp),
                "type": "micro_fvg_inversion",
                "description": inv_result["description"],
            })

            entry_idx = inv_result["entry_idx"]
            entry_candle = candles_1m[entry_idx]
            direction = orderflow
            past = past_slice(candles_1m, entry_idx)
            local_low = min(x.low for x in past[max(0, len(past) - 15):])
            local_high = max(x.high for x in past[max(0, len(past) - 15):])

            if direction == "bullish":
                sl = local_low - (local_low * 0.0005)
            else:
                sl = local_high + (local_high * 0.0005)

            risk = abs(inv_result["entry_price"] - sl)
            tp = (
                inv_result["entry_price"] + (2 * risk)
                if direction == "bullish"
                else inv_result["entry_price"] - (2 * risk)
            )

            day_trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_candle.timestamp),
                "direction": direction,
                "entry_price": round(inv_result["entry_price"], 5),
                "stop_loss": round(sl, 5),
                "take_profit": round(tp, 5),
                "reason": f"1H {orderflow} flow + 5m FVG + 1m inversion confirmation",
                "macro_draw": macro_draw,
                "events": list(events_log),
            }
            break

        if day_trade is None:
            continue

        exit_info = simulate_exits(
            candles_1m,
            entry_idx,
            entry_candle.timestamp,
            "long" if day_trade["direction"] == "bullish" else "short",
            day_trade["stop_loss"],
            day_trade["take_profit"],
        )
        day_trade.update(exit_info)
        trades.append(day_trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 06: Fractal-Based Inversion Order Flow")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV")
    parser.add_argument("--csv5m", required=True, help="5-minute CSV")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_1h = load_csv(args.csv1h)
    candles_5m = load_csv(args.csv5m)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv1h)
    output = args.output or f"strategy_06_results_{meta['symbol']}.json"
    run_strategy(candles_1h, candles_5m, candles_1m, output)


if __name__ == "__main__":
    main()
