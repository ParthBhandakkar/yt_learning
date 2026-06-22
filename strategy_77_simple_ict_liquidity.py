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
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_mss, detect_fvg,
    swing_highs, swing_lows,
    save_trades,
)


# ---------------------------------------------------------------------------
# Step 1: Find swing high/low on 4H (3-candle fractal)
# ---------------------------------------------------------------------------

def find_4h_swing_liquidity(candles_4h: list[Candle]) -> Optional[dict]:
    if len(candles_4h) < 3:
        return None
    sh = swing_highs(candles_4h)
    sl = swing_lows(candles_4h)

    if sh:
        idx = sh[-1]
        liquid_high = candles_4h[idx].high
        liquid_low = candles_4h[idx].low  # Third candle
        return {"type": "swing_high", "idx": idx, "liquidity_levels": (liquid_low, liquid_high)}
    if sl:
        idx = sl[-1]
        liquid_high = candles_4h[idx].high
        liquid_low = candles_4h[idx].low
        return {"type": "swing_low", "idx": idx, "liquidity_levels": (liquid_low, liquid_high)}
    return None


# ---------------------------------------------------------------------------
# Step 3: Sweep + 5m MSS + entry
# ---------------------------------------------------------------------------

def find_entry_5m(
    candles_5m: list[Candle], liquidity: dict, start_ts: int
) -> Optional[dict]:
    start_idx = next((i for i, c in enumerate(candles_5m) if c.timestamp >= start_ts), 0)
    liq_low, liq_high = liquidity["liquidity_levels"]

    for i in range(start_idx, len(candles_5m) - 3):
        c = candles_5m[i]

        swept_high = c.high > liq_high
        swept_low = c.low < liq_low

        if not swept_high and not swept_low:
            continue

        for j in range(i + 1, min(i + 15, len(candles_5m))):
            cj = candles_5m[j]
            mss = detect_mss(candles_5m[max(0, j - 3):j + 3], lookback=3)
            for ev in mss:
                direction = None
                if swept_low and ev["direction"] == "bullish":
                    direction = "long"
                elif swept_high and ev["direction"] == "bearish":
                    direction = "short"

                if direction:
                    fvgs = detect_fvg(candles_5m[max(0, j - 3):j + 3])
                    entry_price = cj.close
                    local_low = min(x.low for x in candles_5m[max(0, j - 3):j + 3])
                    local_high = max(x.high for x in candles_5m[max(0, j - 3):j + 3])

                    sl = local_low - (local_low * 0.0005) if direction == "long" else local_high + (local_high * 0.0005)
                    risk = abs(entry_price - sl)
                    tp = entry_price + (2 * risk) if direction == "long" else entry_price - (2 * risk)

                    return {
                        "entry_idx": j,
                        "entry_price": entry_price,
                        "direction": direction,
                        "sl": sl,
                        "tp": tp,
                        "sweep_idx": i,
                        "swept_level": liq_low if swept_low else liq_high,
                        "description": f"4H liquidity {liq_low if swept_low else liq_high:.5f} swept + 5m MSS at {entry_price:.5f}",
                    }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_4h: list[Candle], candles_5m: list[Candle], output_path: str):
    trades = []

    liquidity = find_4h_swing_liquidity(candles_4h)
    if liquidity is None:
        print("No 4H swing found")
        save_trades(trades, output_path)
        return trades

    liq_ts = candles_4h[liquidity["idx"]].timestamp
    liq_low, liq_high = liquidity["liquidity_levels"]

    events_log = [{
        "timestamp": to_iso(liq_ts),
        "type": "liquidity_levels_marked",
        "high": round(liq_high, 5),
        "low": round(liq_low, 5),
        "description": f"4H {liquidity['type']}: liquidity high={liq_high:.5f}, low={liq_low:.5f}",
    }]

    # Wait for next 4H candle
    next_4h_ts = liq_ts + (4 * 3600)
    result = find_entry_5m(candles_5m, liquidity, next_4h_ts)
    if result is None:
        print("No sweep + entry found in next 4H candle")
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_5m[result["sweep_idx"]].timestamp),
        "type": "liquidity_sweep",
        "swept_level": round(result["swept_level"], 5),
        "description": f"Liquidity level {result['swept_level']:.5f} swept",
    })
    events_log.append({
        "timestamp": to_iso(candles_5m[result["entry_idx"]].timestamp),
        "type": "entry_trigger",
        "direction": result["direction"],
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

    entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
    for c in candles_5m:
        if c.timestamp > entry_ts:
            if result["direction"] == "long":
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
