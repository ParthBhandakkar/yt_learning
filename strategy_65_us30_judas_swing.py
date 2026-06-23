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

BACKTEST INTEGRITY NOTICE (severity: CRITICAL — results are likely inflated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Same-bar exit leak: for cx in range(j, ...) checks TP/SL on the entry bar j
     before you could realistically be filled — inflates wins.
  2. Pre-9:30 filter uses UTC hour, not New York hour — wrong session window.
  3. MSS window includes future bars (j+5 slice). Only one trade on full file.

HOW TO FIX:
  1. Exit loop: only bars with timestamp strictly after entry_time.
  2. Convert all session filters to NY time (UTC-4/5), same as other strategies.
  3. At bar j, MSS only on candles[0:j+1]; enter next bar after confirmation.
  4. Per-day loop; record all valid Judas setups across years.
  FIXED: Per-day NY loop, ny_hour pre-open, mss_events_up_to, past_slice SL, simulate_exits.
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
    mss_events_up_to,
    simulate_exits,
)


def _ny_minute(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    h = (dt.hour - 4) % 24
    return h * 60 + dt.minute


def _is_pre_open(ts: int) -> bool:
    return _ny_minute(ts) < 9 * 60 + 30


def run_strategy(candles_15m: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    days_15m = group_by_ny_day(candles_15m)
    days_1m = group_by_ny_day(candles_1m)

    for day_idx, day_15m in enumerate(days_15m):
        if len(day_15m) < 4:
            continue

        day_1m = days_1m[day_idx] if day_idx < len(days_1m) else []
        if len(day_1m) < 30:
            continue

        pre_open_15m = [c for c in day_15m if _is_pre_open(c.timestamp)]
        if not pre_open_15m:
            continue

        sh = swing_highs(pre_open_15m)
        sl_swings = swing_lows(pre_open_15m)
        if not sh or not sl_swings:
            continue

        pre_high = pre_open_15m[sh[-1]].high
        pre_low = pre_open_15m[sl_swings[-1]].low

        events_log = [{
            "timestamp": to_iso(pre_open_15m[-1].timestamp),
            "type": "pre_open_levels",
            "high": round(pre_high, 5),
            "low": round(pre_low, 5),
            "description": f"Pre-9:30 levels: high={pre_high:.5f}, low={pre_low:.5f}",
        }]

        ny_open_idx = None
        for i, c in enumerate(day_1m):
            h = ny_hour(c.timestamp)
            dt = datetime.fromtimestamp(c.timestamp, tz=timezone.utc)
            if h > 9 or (h == 9 and dt.minute >= 30):
                ny_open_idx = i
                break
        if ny_open_idx is None:
            continue

        sweep_found = False
        sweep_dir: Optional[str] = None

        for i in range(ny_open_idx, min(ny_open_idx + 120, len(day_1m))):
            c = day_1m[i]

            if not sweep_found:
                if c.high > pre_high:
                    sweep_dir = "high_swept"
                    sweep_found = True
                elif c.low < pre_low:
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

            for j in range(i + 1, min(i + 30, len(day_1m))):
                cj = day_1m[j]
                body = abs(cj.close - cj.open)
                prior = day_1m[max(0, j - 5):j]
                avg_body = sum(abs(x.close - x.open) for x in prior) / max(1, len(prior))
                if body <= avg_body * 1.5:
                    continue

                mss = mss_events_up_to(day_1m, j, lookback=3)
                for ev in mss:
                    if ev["idx"] != j:
                        continue
                    if sweep_dir == "high_swept" and ev["direction"] == "bearish":
                        direction = "short"
                    elif sweep_dir == "low_swept" and ev["direction"] == "bullish":
                        direction = "long"
                    else:
                        continue

                    known = past_slice(day_1m, j)
                    local_low = min(x.low for x in known)
                    local_high = max(x.high for x in known)
                    entry_price = cj.close
                    sl = (
                        local_low - (local_low * 0.0005)
                        if direction == "long"
                        else local_high + (local_high * 0.0005)
                    )
                    risk = abs(entry_price - sl)
                    tp = entry_price + (2 * risk) if direction == "long" else entry_price - (2 * risk)

                    events_log.append({
                        "timestamp": to_iso(cj.timestamp),
                        "type": "entry_trigger",
                        "direction": direction,
                        "description": f"1M MSS + displacement at {entry_price:.5f}",
                    })

                    exit_info = simulate_exits(day_1m, j, cj.timestamp, direction, sl, tp)
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
                        **exit_info,
                    }
                    trades.append(trade)
                    sweep_found = False
                    sweep_dir = None
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
