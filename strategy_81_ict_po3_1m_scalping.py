#!/usr/bin/env python3
"""
Strategy 81: ICT Power of 3 (1-Minute Scalping)

Source: Faiz SMC - "How I Make $500/Day Trading ICT Power Of 3! (Insane Winrate)"
Video: https://www.youtube.com/watch?v=yPA1XioVF18

Core concepts:
  - 1H chart: last 2 candles determine bias for 3rd candle
    - Bullish: 2nd candle closes above 1st candle high
    - Bearish: 2nd candle fails OR closes below 1st candle low
  - 1M chart: during 3rd 1H candle
  - Find liquidity (equal highs/lows) to the left of 3rd 1M candle open
  - Judas swing sweeps liquidity → 1M MSS (wicks valid) → entry at order block

Usage:
  python strategy_81_ict_po3_1m_scalping.py --csv1h 1h.csv --csv1m 1m.csv

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Entry can occur on the same bar as the liquidity sweep while MSS is
     detected using candles[i-2:i+5] — future bars help "confirm" the sweep.
  2. 1H bias from the last two 1H candles in the whole file, not per day.
  3. One trade on entire CSV (break on first match).

HOW TO FIX:
  1. Confirm sweep on bar i only after bar i closes; MSS only on candles[0:i+1]
     then act earliest on bar i+1.
  2. For each day, use that day's relevant 1H candles for PO3 bias only.
  3. Per-day PO3 loop on 1m; record all valid setups.
  4. Close-only MSS/iFVG; no wick-based triggers from core.detect_ifvg.
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_mss,
    swing_highs, swing_lows,
    save_trades,
)


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


# ---------------------------------------------------------------------------
# Step 1: Bias from last 2 1H candles
# ---------------------------------------------------------------------------

def bias_from_2_candles(candles_1h: list[Candle]) -> tuple:
    if len(candles_1h) < 2:
        return "neutral", None, None
    c1 = candles_1h[-2]
    c2 = candles_1h[-1]

    if c2.close > c1.high:
        return "bullish", c1, c2
    elif c2.close < c1.low:
        return "bearish", c1, c2
    elif c2.close < c1.close:
        return "bearish", c1, c2
    else:
        return "neutral", c1, c2


# ---------------------------------------------------------------------------
# Step 2: Find liquidity to the left of 3rd candle open on 1M
# ---------------------------------------------------------------------------

def find_liquidity_left(candles_1m: list[Candle], open_idx: int, lookback: int = 30) -> dict:
    left = candles_1m[max(0, open_idx - lookback):open_idx]
    result = {"equal_highs": [], "equal_lows": []}
    if len(left) < 5:
        return result

    sh = swing_highs(left)
    sl = swing_lows(left)

    # Group similar highs as equal highs
    high_levels = {}
    for idx in sh:
        level = round(left[idx].high, 5)
        rounded = round(level, 4)
        if rounded not in high_levels:
            high_levels[rounded] = []
        high_levels[rounded].append(level)

    for rl, levels in high_levels.items():
        if len(levels) >= 2:
            result["equal_highs"].append(max(levels))

    low_levels = {}
    for idx in sl:
        level = round(left[idx].low, 5)
        rounded = round(level, 4)
        if rounded not in low_levels:
            low_levels[rounded] = []
        low_levels[rounded].append(level)

    for rl, levels in low_levels.items():
        if len(levels) >= 2:
            result["equal_lows"].append(min(levels))

    return result


# ---------------------------------------------------------------------------
# Step 3: Judas swing sweep + MSS on 1M
# ---------------------------------------------------------------------------

def find_judas_swing_entry(
    candles_1m: list[Candle], open_idx: int, liquidity: dict, bias: str
) -> Optional[dict]:
    for i in range(open_idx, min(open_idx + 60, len(candles_1m) - 3)):
        c = candles_1m[i]

        # Bullish: look for sweep of equal lows (Judas swing down)
        if bias == "bullish":
            for eq_low in liquidity["equal_lows"]:
                if c.low < eq_low:
                    # MSS check: wicks valid, body closure not required
                    mss = detect_mss(candles_1m[max(0, i - 2):i + 5], lookback=2)
                    for ev in mss:
                        if ev["direction"] == "bullish":
                            entry_c = candles_1m[i]
                            sl = min(x.low for x in candles_1m[max(0, i - 2):i + 3]) - 0.0005
                            risk = abs(c.close - sl)
                            tp = c.close + (2 * risk)

                            return {
                                "entry_idx": i,
                                "entry_price": c.close,
                                "direction": "long",
                                "sl": sl,
                                "tp": tp,
                                "swept_level": eq_low,
                                "description": f"Bullish Judas: swept equal low {eq_low:.5f} + MSS at {c.close:.5f}",
                            }

        # Bearish: look for sweep of equal highs
        if bias == "bearish":
            for eq_high in liquidity["equal_highs"]:
                if c.high > eq_high:
                    mss = detect_mss(candles_1m[max(0, i - 2):i + 5], lookback=2)
                    for ev in mss:
                        if ev["direction"] == "bearish":
                            entry_c = candles_1m[i]
                            sl = max(x.high for x in candles_1m[max(0, i - 2):i + 3]) + 0.0005
                            risk = abs(c.close - sl)
                            tp = c.close - (2 * risk)

                            return {
                                "entry_idx": i,
                                "entry_price": c.close,
                                "direction": "short",
                                "sl": sl,
                                "tp": tp,
                                "swept_level": eq_high,
                                "description": f"Bearish Judas: swept equal high {eq_high:.5f} + MSS at {c.close:.5f}",
                            }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_1h: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []

    bias, c1, c2 = bias_from_2_candles(candles_1h)
    if bias == "neutral":
        print("No clear bias from 2 1H candles")
        save_trades(trades, output_path)
        return trades

    events_log = [{
        "timestamp": to_iso(c2.timestamp),
        "type": "bias_identified",
        "bias": bias,
        "description": f"Bias: {bias} (C2 close {c2.close:.5f} vs C1 {'high' if bias == 'bullish' else 'low'} {c1.high if bias == 'bullish' else c1.low:.5f})",
    }]

    # Third 1H candle open
    c3_ts = c2.timestamp + 3600
    events_log.append({
        "timestamp": to_iso(c3_ts),
        "type": "po3_start",
        "description": f"3rd 1H candle (PO3) starts at {to_iso(c3_ts)}",
    })

    # Find 1M open index
    open_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= c3_ts), 0)
    if open_1m >= len(candles_1m) - 10:
        save_trades(trades, output_path)
        return trades

    # Find liquidity left of open
    liquidity = find_liquidity_left(candles_1m, open_1m)
    if (bias == "bullish" and not liquidity["equal_lows"]) or \
       (bias == "bearish" and not liquidity["equal_highs"]):
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_1m[open_1m].timestamp),
        "type": "liquidity_mapped",
        "equal_highs": liquidity["equal_highs"],
        "equal_lows": liquidity["equal_lows"],
        "description": f"Liquidity mapped: highs={liquidity['equal_highs']}, lows={liquidity['equal_lows']}",
    })

    result = find_judas_swing_entry(candles_1m, open_1m, liquidity, bias)
    if result is None:
        print("No Judas swing entry found")
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_1m[result["entry_idx"]].timestamp),
        "type": "entry_trigger",
        "direction": result["direction"],
        "description": result["description"],
    })

    trade = {
        "trade_number": len(trades) + 1,
        "entry_time": to_iso(candles_1m[result["entry_idx"]].timestamp),
        "direction": result["direction"],
        "entry_price": round(result["entry_price"], 5),
        "stop_loss": round(result["sl"], 5),
        "take_profit": round(result["tp"], 5),
        "reason": result["description"],
        "events": list(events_log),
    }
    trades.append(trade)

    # Exit
    entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
    for c in candles_1m:
        if c.timestamp > entry_ts:
            if result["direction"] == "long":
                if c.high >= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.low <= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
            else:
                if c.low <= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.high >= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
    if "exit_time" not in trade:
        trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
        trade["exit_price"] = candles_1m[-1].close
        trade["outcome"] = "open"

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 81: ICT PO3 1-Minute Scalping")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_1h = load_csv(args.csv1h)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv1h)
    output = args.output or f"strategy_81_results_{meta['symbol']}.json"
    run_strategy(candles_1h, candles_1m, output)


if __name__ == "__main__":
    main()
