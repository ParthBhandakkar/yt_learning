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

FIXED: Causal backtest — per-day PO3 loop on 1H; MSS via mss_events_up_to;
sweep confirmed at bar close, entry bar after MSS; simulate_exits for TP/SL.
"""

import argparse
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    swing_highs, swing_lows,
    save_trades, emit_progress, index_at_or_after,
)
from causal_backtest import group_by_ny_day, past_slice, mss_events_up_to, simulate_exits


def bias_from_2_candles(candles_1h: list[Candle]) -> tuple:
    if len(candles_1h) < 2:
        return "neutral", None, None
    c1 = candles_1h[-2]
    c2 = candles_1h[-1]

    if c2.close > c1.high:
        return "bullish", c1, c2
    if c2.close < c1.low:
        return "bearish", c1, c2
    if c2.close < c1.close:
        return "bearish", c1, c2
    return "neutral", c1, c2


def find_liquidity_left(candles_1m: list[Candle], open_idx: int, lookback: int = 30) -> dict:
    left = candles_1m[max(0, open_idx - lookback):open_idx]
    result = {"equal_highs": [], "equal_lows": []}
    if len(left) < 5:
        return result

    sh = swing_highs(left)
    sl = swing_lows(left)

    high_levels: dict[float, list[float]] = {}
    for idx in sh:
        level = round(left[idx].high, 5)
        rounded = round(level, 4)
        if rounded not in high_levels:
            high_levels[rounded] = []
        high_levels[rounded].append(level)

    for levels in high_levels.values():
        if len(levels) >= 2:
            result["equal_highs"].append(max(levels))

    low_levels: dict[float, list[float]] = {}
    for idx in sl:
        level = round(left[idx].low, 5)
        rounded = round(level, 4)
        if rounded not in low_levels:
            low_levels[rounded] = []
        low_levels[rounded].append(level)

    for levels in low_levels.values():
        if len(levels) >= 2:
            result["equal_lows"].append(min(levels))

    return result


def find_judas_swing_entry_causal(
    candles_1m: list[Candle], open_idx: int, c3_end_ts: int, liquidity: dict, bias: str
) -> Optional[dict]:
    end_idx = index_at_or_after(candles_1m, c3_end_ts)
    end_idx = min(end_idx, open_idx + 60, len(candles_1m))

    for i in range(open_idx, min(end_idx, len(candles_1m) - 1)):
        c = candles_1m[i]

        if bias == "bullish":
            for eq_low in liquidity["equal_lows"]:
                if c.low < eq_low:
                    mss_list = mss_events_up_to(candles_1m, i, lookback=2)
                    for ev in mss_list:
                        if ev["idx"] == i and ev["direction"] == "bullish":
                            entry_idx = i + 1
                            if entry_idx >= len(candles_1m):
                                continue
                            entry_c = candles_1m[entry_idx]
                            past = past_slice(candles_1m, i)
                            sl = min(x.low for x in past[max(0, len(past) - 3) :]) - 0.0005
                            risk = abs(entry_c.close - sl)
                            tp = entry_c.close + (2 * risk)
                            return {
                                "entry_idx": entry_idx,
                                "entry_price": entry_c.close,
                                "direction": "long",
                                "sl": sl,
                                "tp": tp,
                                "swept_level": eq_low,
                                "description": (
                                    f"Bullish Judas: swept equal low {eq_low:.5f} "
                                    f"+ MSS at {entry_c.close:.5f}"
                                ),
                            }

        if bias == "bearish":
            for eq_high in liquidity["equal_highs"]:
                if c.high > eq_high:
                    mss_list = mss_events_up_to(candles_1m, i, lookback=2)
                    for ev in mss_list:
                        if ev["idx"] == i and ev["direction"] == "bearish":
                            entry_idx = i + 1
                            if entry_idx >= len(candles_1m):
                                continue
                            entry_c = candles_1m[entry_idx]
                            past = past_slice(candles_1m, i)
                            sl = max(x.high for x in past[max(0, len(past) - 3) :]) + 0.0005
                            risk = abs(entry_c.close - sl)
                            tp = entry_c.close - (2 * risk)
                            return {
                                "entry_idx": entry_idx,
                                "entry_price": entry_c.close,
                                "direction": "short",
                                "sl": sl,
                                "tp": tp,
                                "swept_level": eq_high,
                                "description": (
                                    f"Bearish Judas: swept equal high {eq_high:.5f} "
                                    f"+ MSS at {entry_c.close:.5f}"
                                ),
                            }
    return None


def run_strategy(candles_1h: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    h1_all: list[Candle] = []
    for day_1h in group_by_ny_day(candles_1h):
        h1_all.extend(day_1h)

    for i in range(2, len(h1_all)):
        if i % 200 == 0:
            emit_progress("strategy", min(99, int(100 * i / len(h1_all))), f"Scanning 1H bars {i}/{len(h1_all)}")
        c1, c2, c3 = h1_all[i - 2], h1_all[i - 1], h1_all[i]
        bias, _, _ = bias_from_2_candles([c1, c2])
        if bias == "neutral":
            continue

        c3_ts = c3.timestamp
        c3_end_ts = c3_ts + 3600

        open_1m = index_at_or_after(candles_1m, c3_ts)
        if open_1m >= len(candles_1m) - 10:
            continue

        liquidity = find_liquidity_left(candles_1m, open_1m)
        if (bias == "bullish" and not liquidity["equal_lows"]) or (
            bias == "bearish" and not liquidity["equal_highs"]
        ):
            continue

        result = find_judas_swing_entry_causal(
            candles_1m, open_1m, c3_end_ts, liquidity, bias
        )
        if result is None:
            continue

        events_log = [{
            "timestamp": to_iso(c2.timestamp),
            "type": "bias_identified",
            "bias": bias,
            "description": (
                f"Bias: {bias} (C2 close {c2.close:.5f} vs C1 "
                f"{'high' if bias == 'bullish' else 'low'} "
                f"{c1.high if bias == 'bullish' else c1.low:.5f})"
            ),
        }]
        events_log.append({
            "timestamp": to_iso(c3_ts),
            "type": "po3_start",
            "description": f"3rd 1H candle (PO3) starts at {to_iso(c3_ts)}",
        })
        events_log.append({
            "timestamp": to_iso(candles_1m[open_1m].timestamp),
            "type": "liquidity_mapped",
            "equal_highs": liquidity["equal_highs"],
            "equal_lows": liquidity["equal_lows"],
            "description": (
                f"Liquidity mapped: highs={liquidity['equal_highs']}, "
                f"lows={liquidity['equal_lows']}"
            ),
        })
        events_log.append({
            "timestamp": to_iso(candles_1m[result["entry_idx"]].timestamp),
            "type": "entry_trigger",
            "direction": result["direction"],
            "description": result["description"],
        })

        entry_c = candles_1m[result["entry_idx"]]
        exit_info = simulate_exits(
            candles_1m,
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
