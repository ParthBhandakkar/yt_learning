#!/usr/bin/env python3
"""
Strategy 65: US30 Trading Strategy (Judas Swing)

Source: Faiz SMC - "Best ICT Judas Swing Trading Strategy With 79% Winrate!"
Video: https://www.youtube.com/watch?v=s0jk8ENNkjw

Core concepts:
  - 15m chart: mark most recent high/low before 9:30 AM NY open
  - Drop to 1m after 9:30 AM open
  - Wait for sweep of 15m high or low
  - 1m MSS with displacement (FVG) → entry
  - Target opposite 15m level

Usage:
  python strategy_65_us30_judas_swing.py --csv15m 15m.csv --csv1m 1m.csv
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


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_15m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []

    # Find pre-open levels (most recent high/low before 9:30 AM)
    pre_open_15m = [c for c in candles_15m if datetime.fromtimestamp(c.timestamp, tz=timezone.utc).hour < 9 or
                    (datetime.fromtimestamp(c.timestamp, tz=timezone.utc).hour == 9 and
                     datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute < 30)]

    if not pre_open_15m:
        save_trades(trades, output_path)
        return trades

    sh = swing_highs(pre_open_15m)
    sl = swing_lows(pre_open_15m)

    if not sh or not sl:
        save_trades(trades, output_path)
        return trades

    pre_high = pre_open_15m[sh[-1]].high
    pre_low = pre_open_15m[sl[-1]].low

    events_log = [{
        "timestamp": to_iso(pre_open_15m[-1].timestamp),
        "type": "pre_open_levels",
        "high": round(pre_high, 5),
        "low": round(pre_low, 5),
        "description": f"Pre-9:30 levels: high={pre_high:.5f}, low={pre_low:.5f}",
    }]

    # After 9:30 AM, watch 1m for sweep + MSS
    ny_open_ts = None
    for c in candles_1m:
        h = ny_hour(c.timestamp)
        if h > 9 or (h == 9 and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute >= 30):
            ny_open_ts = c.timestamp
            break
    if ny_open_ts is None:
        save_trades(trades, output_path)
        return trades

    start_1m = next((i for i, c in enumerate(candles_1m) if c.timestamp >= ny_open_ts), 0)
    sweep_found = False

    for i in range(start_1m, min(start_1m + 120, len(candles_1m))):
        c = candles_1m[i]

        if c.high > pre_high and not sweep_found:
            sweep_dir = "high_swept"
            sweep_found = True
        elif c.low < pre_low and not sweep_found:
            sweep_dir = "low_swept"
            sweep_found = True
        else:
            continue

        events_log.append({
            "timestamp": to_iso(c.timestamp),
            "type": "liquidity_sweep",
            "direction": sweep_dir,
            "description": f"15m {'high' if 'high' in sweep_dir else 'low'} swept",
        })

        # Look for MSS with displacement
        for j in range(i + 1, min(i + 30, len(candles_1m))):
            cj = candles_1m[j]
            body = abs(cj.close - cj.open)
            avg_body = sum(abs(x.close - x.open) for x in candles_1m[max(0, j - 5):j]) / max(1, min(5, j))
            displaced = body > avg_body * 1.5

            if not displaced:
                continue

            mss = detect_mss(candles_1m[max(0, j - 3):j + 5], lookback=3)
            for ev in mss:
                if sweep_dir == "high_swept" and ev["direction"] == "bearish":
                    direction = "short"
                elif sweep_dir == "low_swept" and ev["direction"] == "bullish":
                    direction = "long"
                else:
                    continue

                entry_price = cj.close
                local_low = min(x.low for x in candles_1m[max(0, j - 3):j + 3])
                local_high = max(x.high for x in candles_1m[max(0, j - 3):j + 3])

                sl = local_low - (local_low * 0.0005) if direction == "long" else local_high + (local_high * 0.0005)
                risk = abs(entry_price - sl)
                tp = entry_price + (2 * risk) if direction == "long" else entry_price - (2 * risk)

                events_log.append({
                    "timestamp": to_iso(cj.timestamp),
                    "type": "entry_trigger",
                    "direction": direction,
                    "description": f"1M MSS + displacement at {entry_price:.5f}",
                })

                trade = {
                    "trade_number": len(trades) + 1,
                    "entry_time": to_iso(cj.timestamp),
                    "direction": direction,
                    "entry_price": round(entry_price, 5),
                    "stop_loss": round(sl, 5),
                    "take_profit": round(tp, 5),
                    "target_level": round(pre_high if direction == "long" else pre_low, 5),
                    "reason": f"US30 Judas Swing: {sweep_dir} + MSS at {entry_price:.5f}",
                    "events": list(events_log),
                }
                trades.append(trade)

                # Exit
                for cx in range(j, len(candles_1m)):
                    cx_c = candles_1m[cx]
                    if direction == "long":
                        if cx_c.high >= tp: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                        elif cx_c.low <= sl: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                    else:
                        if cx_c.low <= tp: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                        elif cx_c.high >= sl: trade["exit_time"] = to_iso(cx_c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                if "exit_time" not in trade:
                    trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
                    trade["exit_price"] = candles_1m[-1].close
                    trade["outcome"] = "open"
                break
        break

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 65: US30 Judas Swing")
    parser.add_argument("--csv15m", required=True, help="15-minute CSV")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_15m = load_csv(args.csv15m)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv15m)
    output = args.output or f"strategy_65_results_{meta['symbol']}.json"
    run_strategy(candles_15m, candles_1m, output)


if __name__ == "__main__":
    main()
