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
    resample, save_trades,
)


# ---------------------------------------------------------------------------
# NY time helpers
# ---------------------------------------------------------------------------

def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def ny_minute(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return ((dt.hour - 4) % 24) * 60 + dt.minute


# ---------------------------------------------------------------------------
# Step 1: Macro bias (PDH, PDL, EQH, EQL)
# ---------------------------------------------------------------------------

def get_macro_landmarks(candles_1h: list[Candle]) -> dict:
    if len(candles_1h) < 24:
        return {}
    recent = candles_1h[-24:]
    swings_h = swing_highs(recent)
    swings_l = swing_lows(recent)

    landmarks = {}

    # Previous day high/low
    landmarks["pdh"] = max(c.high for c in recent[-6:])
    landmarks["pdl"] = min(c.low for c in recent[-6:])

    # Equal highs/lows
    if len(swings_h) >= 2:
        eq_highs = [recent[i].high for i in swings_h[-3:]]
        if max(eq_highs) - min(eq_highs) < (max(eq_highs) * 0.001):
            landmarks["eqh"] = max(eq_highs)

    if len(swings_l) >= 2:
        eq_lows = [recent[i].low for i in swings_l[-3:]]
        if max(eq_lows) - min(eq_lows) < (max(eq_lows) * 0.001):
            landmarks["eql"] = min(eq_lows)

    return landmarks


# ---------------------------------------------------------------------------
# Step 2: Find NY open index
# ---------------------------------------------------------------------------

def find_ny_open_idx(candles: list[Candle]) -> int:
    for i, c in enumerate(candles):
        h = ny_hour(c.timestamp)
        m = (datetime.fromtimestamp(c.timestamp, tz=timezone.utc).hour * 60 +
             datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute)
        ny_min = ((h + 4) % 24) * 60 + datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute
        if h > 9 or (h == 9 and datetime.fromtimestamp(c.timestamp, tz=timezone.utc).minute >= 30):
            return i
    return 0


# ---------------------------------------------------------------------------
# Step 3: Find displacement FVG after NY open on 5m/15m
# ---------------------------------------------------------------------------

def find_post_open_displacement_fvg(candles: list[Candle], start_idx: int, direction: str) -> Optional[dict]:
    displaced = False
    for i in range(start_idx + 2, min(start_idx + 15, len(candles))):
        c = candles[i]
        prev = candles[i - 1]
        # Displacement check: large body
        body = abs(c.close - c.open)
        avg_body = sum(abs(x.close - x.open) for x in candles[max(0, i - 5):i]) / 5 if i >= 5 else body
        if body > avg_body * 1.5:
            displaced = True
            break

    if not displaced:
        return None

    fvgs = detect_fvg(candles[start_idx:start_idx + 20])
    for fvg in fvgs:
        fvg["idx"] += start_idx
        if direction == "bullish" and fvg["direction"] == "bullish":
            return fvg
        if direction == "bearish" and fvg["direction"] == "bearish":
            return fvg
    return None


# ---------------------------------------------------------------------------
# Step 5: Highest timeframe inversion FVG selection
# ---------------------------------------------------------------------------

TIMEFRAMES_TO_CHECK = [5, 4, 3, 2, 1]


def find_highest_tf_ifvg(candles_1m: list[Candle], start_idx: int, lookahead: int = 30) -> Optional[dict]:
    """Check 1m→2m→3m→4m→5m for an unmitigated FVG that gets inverted"""
    for tf_mult in TIMEFRAMES_TO_CHECK:
        tf_candles_raw = resample(candles_1m[start_idx:start_idx + lookahead], tf_mult)
        if len(tf_candles_raw) < 3:
            continue

        fvgs = detect_fvg(tf_candles_raw)
        for fvg in fvgs:
            fvg["idx"] += 0  # idx relative to tf_candles_raw
            # Check if this FVG gets inverted
            for j in range(fvg["idx"] + 1, len(tf_candles_raw)):
                tc = tf_candles_raw[j]
                if fvg["direction"] == "bullish" and (tc.close < fvg["lower"] or tc.low < fvg["lower"]):
                    # Map back to 1m index
                    entry_1m_idx = start_idx + (j * tf_mult)
                    if entry_1m_idx < len(candles_1m):
                        return {
                            "timeframe": f"{tf_mult}m",
                            "fvg": fvg,
                            "entry_1m_idx": entry_1m_idx,
                            "entry_price": tc.close,
                            "direction": "long",
                            "description": (
                                f"Highest TF iFVG: {tf_mult}m FVG inverted at {tc.close:.5f} "
                                f"(FVG: {fvg['lower']:.5f}-{fvg['upper']:.5f})"
                            ),
                        }
                if fvg["direction"] == "bearish" and (tc.close > fvg["upper"] or tc.high > fvg["upper"]):
                    entry_1m_idx = start_idx + (j * tf_mult)
                    if entry_1m_idx < len(candles_1m):
                        return {
                            "timeframe": f"{tf_mult}m",
                            "fvg": fvg,
                            "entry_1m_idx": entry_1m_idx,
                            "entry_price": tc.close,
                            "direction": "short",
                            "description": (
                                f"Highest TF iFVG: {tf_mult}m FVG inverted at {tc.close:.5f} "
                                f"(FVG: {fvg['lower']:.5f}-{fvg['upper']:.5f})"
                            ),
                        }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_1h: list[Candle], candles_1m: list[Candle], output_path: str):
    trades = []

    # Macro bias
    macro = get_macro_landmarks(candles_1h)
    if not macro:
        print("Could not determine macro landmarks")
        save_trades(trades, output_path)
        return trades

    # Determine direction bias toward macro draw
    last_close = candles_1h[-1].close
    direction = "bullish" if last_close < macro.get("pdh", float("inf")) else "bearish"

    # Find NY open
    ny_idx = find_ny_open_idx(candles_1m)
    if ny_idx >= len(candles_1m) - 10:
        save_trades(trades, output_path)
        return trades

    events_log = []
    events_log.append({
        "timestamp": to_iso(candles_1m[ny_idx].timestamp),
        "type": "ny_open",
        "description": "NY open (9:30 AM) – trading window starts",
    })

    events_log.append({
        "timestamp": to_iso(candles_1h[-1].timestamp),
        "type": "macro_landmarks",
        "landmarks": {k: round(v, 5) for k, v in macro.items()},
        "description": f"Macro landmarks mapped: PDH={macro.get('pdh', 'N/A')}, PDL={macro.get('pdl', 'N/A')}",
    })

    # Resample 1m → 5m for displacement check
    candles_5m = resample(candles_1m, 5)
    ny_idx_5m = next((i for i, c in enumerate(candles_5m) if c.timestamp >= candles_1m[ny_idx].timestamp), 0)

    htf_fvg = find_post_open_displacement_fvg(candles_5m, ny_idx_5m, direction)
    if htf_fvg is None:
        # Try opposite direction
        direction = "bearish" if direction == "bullish" else "bullish"
        htf_fvg = find_post_open_displacement_fvg(candles_5m, ny_idx_5m, direction)

    if htf_fvg is None:
        print("No post-open displacement FVG found")
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_5m[htf_fvg["idx"]].timestamp),
        "type": "displacement_fvg",
        "direction": htf_fvg["direction"],
        "upper": round(htf_fvg["upper"], 5),
        "lower": round(htf_fvg["lower"], 5),
        "description": f"5m displacement {htf_fvg['direction']} FVG: {htf_fvg['lower']:.5f}-{htf_fvg['upper']:.5f}",
    })

    # Step 4: Wait for price to trade back into the FVG
    htf_fvg_ts = candles_5m[htf_fvg["idx"]].timestamp
    for i in range(htf_fvg["idx"] + 1, len(candles_5m)):
        c = candles_5m[i]
        inside = (htf_fvg["direction"] == "bullish" and c.low < htf_fvg["upper"] and c.high > htf_fvg["lower"]) or \
                 (htf_fvg["direction"] == "bearish" and c.high > htf_fvg["lower"] and c.low < htf_fvg["upper"])
        if inside:
            reentry_ts = c.timestamp
            events_log.append({
                "timestamp": to_iso(reentry_ts),
                "type": "price_reentered_htf_fvg",
                "description": f"Price re-entered 5m FVG at {c.close:.5f}",
            })

            start_1m = next((j for j, x in enumerate(candles_1m) if x.timestamp >= reentry_ts), 0)

            # Step 5: Find highest TF iFVG
            ifvg_result = find_highest_tf_ifvg(candles_1m, start_1m)
            if ifvg_result is None:
                continue

            entry_idx = ifvg_result["entry_1m_idx"]
            entry_candle = candles_1m[entry_idx]
            entry_price = ifvg_result["entry_price"]

            events_log.append({
                "timestamp": to_iso(entry_candle.timestamp),
                "type": "ifvg_entry",
                "timeframe": ifvg_result["timeframe"],
                "direction": ifvg_result["direction"],
                "description": ifvg_result["description"],
            })

            # SL below manipulation leg bodies
            local_low = min(x.low for x in candles_1m[max(0, start_1m - 5):entry_idx + 3])
            local_high = max(x.high for x in candles_1m[max(0, start_1m - 5):entry_idx + 3])

            trade_dir = "long" if ifvg_result["direction"] == "long" else "short"
            if trade_dir == "long":
                sl = local_low - (local_low * 0.0005)
            else:
                sl = local_high + (local_high * 0.0005)

            risk = abs(entry_price - sl)
            tp = entry_price + (1.5 * risk) if trade_dir == "long" else entry_price - (1.5 * risk)

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
            }
            trades.append(trade)

            # Exit check
            for c in candles_1m:
                if c.timestamp > entry_candle.timestamp:
                    if trade_dir == "long":
                        if c.high >= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                        elif c.low <= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                    else:
                        if c.low <= tp: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                        elif c.high >= sl: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
            if "exit_time" not in trade:
                trade["exit_time"] = to_iso(candles_1m[-1].timestamp)
                trade["exit_price"] = candles_1m[-1].close
                trade["outcome"] = "open"
            break

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
