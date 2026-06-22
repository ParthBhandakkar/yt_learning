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
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_fvg, detect_ifvg, detect_mss,
    swing_highs, swing_lows,
    save_trades, resample,
)


# ---------------------------------------------------------------------------
# Step 1: Trend identification (3-second rule)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Step 2: Find the BOS swing level to sweep
# ---------------------------------------------------------------------------

def find_bos_level(candles: list[Candle], trend: str) -> Optional[dict]:
    sh = swing_highs(candles)
    sl = swing_lows(candles)

    if trend == "bullish" and sl:
        # Find the low that caused the most recent BOS
        swing_l = candles[sl[-1]].low
        return {"type": "swing_low", "level": swing_l, "idx": sl[-1]}

    if trend == "bearish" and sh:
        swing_h = candles[sh[-1]].high
        return {"type": "swing_high", "level": swing_h, "idx": sh[-1]}

    return None


# ---------------------------------------------------------------------------
# Step 3: Sweep detection + FVG inversion entry
# ---------------------------------------------------------------------------

def find_purge_entry(
    candles_5m: list[Candle], bos_level: float, bos_type: str, start_idx: int
) -> Optional[dict]:
    for i in range(start_idx, len(candles_5m) - 3):
        c = candles_5m[i]

        # Check sweep
        if bos_type == "swing_low" and c.low < bos_level:
            sweep_idx = i
        elif bos_type == "swing_high" and c.high > bos_level:
            sweep_idx = i
        else:
            continue

        # After sweep, look for FVG inversion
        for j in range(sweep_idx + 1, min(sweep_idx + 15, len(candles_5m))):
            fvgs = detect_fvg(candles_5m[sweep_idx:j + 3])
            for fvg in fvgs:
                inv = detect_ifvg(candles_5m[sweep_idx:j + 5], fvg)
                if inv:
                    entry_c = candles_5m[j]
                    direction = "long" if bos_type == "swing_low" else "short"
                    local_low = min(x.low for x in candles_5m[max(0, sweep_idx - 2):j + 2])
                    local_high = max(x.high for x in candles_5m[max(0, sweep_idx - 2):j + 2])

                    sl = local_low - (local_low * 0.0005) if direction == "long" else local_high + (local_high * 0.0005)
                    risk = abs(entry_c.close - sl)
                    tp = entry_c.close + (2 * risk) if direction == "long" else entry_c.close - (2 * risk)

                    return {
                        "entry_idx": j,
                        "entry_price": entry_c.close,
                        "direction": direction,
                        "sl": sl,
                        "tp": tp,
                        "sweep_idx": sweep_idx,
                        "fvg": fvg,
                        "description": f"Continuation purge: swept BOS {bos_type} {bos_level:.5f} + FVG inversion at {entry_c.close:.5f}",
                    }
        break
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_1h: list[Candle], candles_5m: list[Candle], output_path: str):
    trades = []

    trend = identify_trend(candles_1h)
    if trend is None:
        print("No clear trend identified")
        save_trades(trades, output_path)
        return trades

    events_log = [{
        "timestamp": to_iso(candles_1h[-1].timestamp),
        "type": "trend_identified",
        "trend": trend,
        "description": f"Clear {trend} trend (3-second rule)",
    }]

    bos = find_bos_level(candles_1h, trend)
    if bos is None:
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_1h[bos["idx"]].timestamp),
        "type": "bos_level_located",
        "level": round(bos["level"], 5),
        "description": f"BOS {bos['type']} at {bos['level']:.5f}",
    })

    bos_ts = candles_1h[bos["idx"]].timestamp
    start_5m = next((i for i, c in enumerate(candles_5m) if c.timestamp >= bos_ts), 0)

    result = find_purge_entry(candles_5m, bos["level"], bos["type"], start_5m)
    if result is None:
        print("No purge entry found")
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_5m[result["sweep_idx"]].timestamp),
        "type": "bos_sweep",
        "description": f"BOS {bos['type']} {bos['level']:.5f} swept",
    })
    events_log.append({
        "timestamp": to_iso(candles_5m[result["entry_idx"]].timestamp),
        "type": "fvg_inversion_entry",
        "description": result["description"],
    })

    trade = {
        "trade_number": len(trades) + 1,
        "entry_time": to_iso(candles_5m[result["entry_idx"]].timestamp),
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
    for c in candles_5m:
        if c.timestamp > entry_ts:
            if trade["direction"] == "long":
                if c.high >= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.low <= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
            else:
                if c.low <= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.high >= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
    if "exit_time" not in trade:
        trade["exit_time"] = to_iso(candles_5m[-1].timestamp)
        trade["exit_price"] = candles_5m[-1].close
        trade["outcome"] = "open"

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
