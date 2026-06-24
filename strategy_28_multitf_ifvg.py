#!/usr/bin/env python3
"""
Strategy 28: Multi-Timeframe Highest Inversion FVG Model

Source: Faiz SMC - "This 'iFVG' Strategy is The Easiest Way to Become Profitable FAST"
Video: https://www.youtube.com/watch?v=KBGaDKKtUMo

Core concepts:
  - 1H/4H macro bias mapping (EQH, EQL, PDH, PDL)
  - NY Open filter (9:30 AM) – never trade before
  - 5m/15m displacement after open → FVG toward macro draw
  - Drop to 1m when price re-enters the HTF FVG
  - Check highest-timeframe inversion FVG (1m→2m→3m→4m→5m)
  - Enter when candle body closes past the inversion boundary

Usage:
  python strategy_28_multitf_ifvg.py --csv1m 1m_data.csv [--output results.json]

BACKTEST INTEGRITY NOTICE (severity: CRITICAL — results are likely inflated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. resample() builds higher-timeframe candles from a forward window — the
     "5m/15m" bar includes 1m candles that have not happened yet at entry time.
  2. Entry 1m index is guessed as start_idx + j*tf_mult — often points to the
     wrong minute bar (early entry or wrong price).
  3. Only one trade is taken on the full file (first match), not a walk-forward
     backtest. Stop loss can use bars after entry that were used to find the setup.

HOW TO FIX:
  1. Build HTF candles only from 1m data with timestamp <= current bar.
  2. Map HTF events to exact 1m timestamps, not approximate index math.
  3. Loop per trading day; take all valid setups with proper exit simulation.
  4. Use close-only iFVG; confirm FVG/swing with +1 bar lag from core.py rules.
  FIXED: Per-day loop, resample_as_of, map_tf_bar_to_1m_idx, ifvg_up_to, simulate_exits.
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
    save_trades, index_at_or_after, candle_series,
)
from causal_backtest import (
    group_by_ny_day,
    ny_hour,
    past_slice,
    resample_as_of,
    detect_fvg_as_of,
    ifvg_up_to,
    map_tf_bar_to_1m_idx,
    simulate_exits,
)


TIMEFRAMES_TO_CHECK = [5, 4, 3, 2, 1]


def get_macro_landmarks(candles_1h: list[Candle], before_ts: int) -> dict:
    recent = [c for c in candles_1h if c.timestamp < before_ts][-24:]
    if len(recent) < 6:
        return {}

    swings_h = swing_highs(recent)
    swings_l = swing_lows(recent)
    landmarks = {
        "pdh": max(c.high for c in recent[-6:]),
        "pdl": min(c.low for c in recent[-6:]),
    }

    if len(swings_h) >= 2:
        eq_highs = [recent[i].high for i in swings_h[-3:]]
        if max(eq_highs) - min(eq_highs) < (max(eq_highs) * 0.001):
            landmarks["eqh"] = max(eq_highs)

    if len(swings_l) >= 2:
        eq_lows = [recent[i].low for i in swings_l[-3:]]
        if max(eq_lows) - min(eq_lows) < (max(eq_lows) * 0.001):
            landmarks["eql"] = min(eq_lows)

    return landmarks


def find_ny_open_idx(candles: list[Candle]) -> int:
    for i, c in enumerate(candles):
        h = ny_hour(c.timestamp)
        dt = datetime.fromtimestamp(c.timestamp, tz=timezone.utc)
        if h > 9 or (h == 9 and dt.minute >= 30):
            return i
    return 0


def find_post_open_displacement_fvg(
    candles_1m: list[Candle], ny_idx: int, as_of_idx: int, direction: str
) -> Optional[dict]:
    as_of_ts = candles_1m[as_of_idx].timestamp
    candles_5m = resample_as_of(candles_1m, 5, as_of_ts)
    if len(candles_5m) < 5:
        return None

    ny_ts = candles_1m[ny_idx].timestamp
    start_5m = index_at_or_after(candles_5m, ny_ts)
    tf_as_of = len(candles_5m) - 1

    displaced = False
    for i in range(start_5m + 2, min(start_5m + 15, tf_as_of + 1)):
        c = candles_5m[i]
        body = abs(c.close - c.open)
        prior = candles_5m[max(0, i - 5):i]
        avg_body = sum(abs(x.close - x.open) for x in prior) / max(1, len(prior))
        if body > avg_body * 1.5:
            displaced = True
            break

    if not displaced:
        return None

    fvgs = detect_fvg_as_of(candles_5m, tf_as_of)
    for fvg in fvgs:
        if fvg["idx"] < start_5m:
            continue
        if direction == "bullish" and fvg["direction"] == "bullish":
            fvg["tf_bar"] = candles_5m[fvg["idx"]]
            return fvg
        if direction == "bearish" and fvg["direction"] == "bearish":
            fvg["tf_bar"] = candles_5m[fvg["idx"]]
            return fvg
    return None


def find_highest_tf_ifvg(
    candles_1m: list[Candle], start_idx: int, as_of_idx: int
) -> Optional[dict]:
    as_of_ts = candles_1m[as_of_idx].timestamp
    for tf_mult in TIMEFRAMES_TO_CHECK:
        tf_candles = resample_as_of(candles_1m, tf_mult, as_of_ts)
        if len(tf_candles) < 3:
            continue
        tf_as_of = len(tf_candles) - 1
        fvgs = detect_fvg_as_of(tf_candles, tf_as_of)
        for fvg in fvgs:
            inv = ifvg_up_to(tf_candles, fvg, tf_as_of)
            if inv is None or inv["idx"] != tf_as_of:
                continue
            entry_1m_idx = map_tf_bar_to_1m_idx(tf_candles[inv["idx"]], candles_1m)
            if entry_1m_idx < start_idx or entry_1m_idx > as_of_idx:
                continue
            trade_dir = "long" if "bullish" in inv["direction"] else "short"
            return {
                "timeframe": f"{tf_mult}m",
                "fvg": fvg,
                "entry_1m_idx": entry_1m_idx,
                "entry_price": candles_1m[entry_1m_idx].close,
                "direction": trade_dir,
                "description": (
                    f"Highest TF iFVG: {tf_mult}m FVG inverted at "
                    f"{candles_1m[entry_1m_idx].close:.5f} "
                    f"(FVG: {fvg['lower']:.5f}-{fvg['upper']:.5f})"
                ),
            }
    return None


def run_strategy(candles_1h: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []
    cs_1m = candle_series(candles_1m)
    days = group_by_ny_day(candles_1m)

    for day_candles in days:
        if len(day_candles) < 60:
            continue

        day_start_ts = day_candles[0].timestamp
        macro = get_macro_landmarks(candles_1h, day_start_ts)
        if not macro:
            continue

        last_close = day_candles[min(30, len(day_candles) - 1)].close
        direction = "bullish" if last_close < macro.get("pdh", float("inf")) else "bearish"

        ny_idx = find_ny_open_idx(day_candles)
        if ny_idx >= len(day_candles) - 10:
            continue

        events_log = [{
            "timestamp": to_iso(day_candles[ny_idx].timestamp),
            "type": "ny_open",
            "description": "NY open (9:30 AM) – trading window starts",
        }, {
            "timestamp": to_iso(day_start_ts),
            "type": "macro_landmarks",
            "landmarks": {k: round(v, 5) for k, v in macro.items()},
            "description": (
                f"Macro landmarks mapped: PDH={macro.get('pdh', 'N/A')}, "
                f"PDL={macro.get('pdl', 'N/A')}"
            ),
        }]

        htf_fvg: Optional[dict] = None
        reentry_start_idx: Optional[int] = None
        traded_indices: set[int] = set()

        for i in range(ny_idx + 10, len(day_candles)):
            as_of_ts = day_candles[i].timestamp
            global_i = cs_1m.at_exact(as_of_ts)
            if global_i < 0:
                global_i = i

            if htf_fvg is None:
                htf_fvg = find_post_open_displacement_fvg(day_candles, ny_idx, i, direction)
                if htf_fvg is None:
                    alt_dir = "bearish" if direction == "bullish" else "bullish"
                    htf_fvg = find_post_open_displacement_fvg(day_candles, ny_idx, i, alt_dir)
                    if htf_fvg:
                        direction = alt_dir

                if htf_fvg:
                    events_log.append({
                        "timestamp": to_iso(as_of_ts),
                        "type": "displacement_fvg",
                        "direction": htf_fvg["direction"],
                        "upper": round(htf_fvg["upper"], 5),
                        "lower": round(htf_fvg["lower"], 5),
                        "description": (
                            f"5m displacement {htf_fvg['direction']} FVG: "
                            f"{htf_fvg['lower']:.5f}-{htf_fvg['upper']:.5f}"
                        ),
                    })
                continue

            c = day_candles[i]
            inside = (
                (htf_fvg["direction"] == "bullish" and c.low < htf_fvg["upper"] and c.high > htf_fvg["lower"])
                or (htf_fvg["direction"] == "bearish" and c.high > htf_fvg["lower"] and c.low < htf_fvg["upper"])
            )

            if not inside:
                continue

            if reentry_start_idx is None:
                reentry_start_idx = i
                events_log.append({
                    "timestamp": to_iso(c.timestamp),
                    "type": "price_reentered_htf_fvg",
                    "description": f"Price re-entered 5m FVG at {c.close:.5f}",
                })

            ifvg_result = find_highest_tf_ifvg(candles_1m, reentry_start_idx, global_i)
            if ifvg_result is None:
                continue

            entry_idx = ifvg_result["entry_1m_idx"]
            if entry_idx in traded_indices:
                continue

            entry_candle = candles_1m[entry_idx]
            entry_price = ifvg_result["entry_price"]
            known = past_slice(candles_1m, entry_idx)
            local_low = min(x.low for x in known)
            local_high = max(x.high for x in known)

            trade_dir = ifvg_result["direction"]
            sl = (
                local_low - (local_low * 0.0005)
                if trade_dir == "long"
                else local_high + (local_high * 0.0005)
            )
            risk = abs(entry_price - sl)
            tp = entry_price + (1.5 * risk) if trade_dir == "long" else entry_price - (1.5 * risk)

            events_log.append({
                "timestamp": to_iso(entry_candle.timestamp),
                "type": "ifvg_entry",
                "timeframe": ifvg_result["timeframe"],
                "direction": trade_dir,
                "description": ifvg_result["description"],
            })

            exit_info = simulate_exits(candles_1m, entry_idx, entry_candle.timestamp, trade_dir, sl, tp)
            trade = {
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(entry_candle.timestamp),
                "direction": trade_dir,
                "entry_price": round(entry_price, 5),
                "stop_loss": round(sl, 5),
                "take_profit": round(tp, 5),
                "ifvg_timeframe": ifvg_result["timeframe"],
                "reason": f"5m displacement FVG + {ifvg_result['timeframe']} iFVG entry",
                "events": list(events_log),
                **exit_info,
            }
            trades.append(trade)
            traded_indices.add(entry_idx)
            htf_fvg = None
            reentry_start_idx = None

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 28: Multi-TF Highest Inversion FVG")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV for macro")
    parser.add_argument("--csv1m", required=True, help="1-minute CSV for execution")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_1h = load_csv(args.csv1h)
    candles_1m = load_csv(args.csv1m)

    meta = parse_csv_filename(args.csv1h)
    output = args.output or f"strategy_28_results_{meta['symbol']}.json"
    run_strategy(candles_1h, candles_1m, output)


if __name__ == "__main__":
    main()
