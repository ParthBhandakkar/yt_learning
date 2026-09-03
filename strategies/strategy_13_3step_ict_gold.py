#!/usr/bin/env python3
"""
Strategy 13: The 3-Step A+ ICT Gold Strategy

Source: Faiz SMC - "The 3-Step A+ ICT Gold Strategy (that actually works)"
Video: https://www.youtube.com/watch?v=UG9lY_LF_mw

Core concepts:
  - Step 1: 3-second 1H/4H trend check (must be immediately obvious)
  - Step 2: Mark 1H FVGs in the impulse leg
  - Step 3: 5m SMT divergence with Silver (XAGUSD)
  - Step 4: CISD or MSS entry on 5m chart
  - SMT divergence between Gold and Silver is essential confirmation

Usage:
  python strategy_13_3step_ict_gold.py --csv_gold XAUUSD_1h.csv --csv_silver XAGUSD_1h.csv
      --csv_gold_5m XAUUSD_5m.csv [--output results.json]

BACKTEST INTEGRITY NOTICE (severity: MAJOR — results are overstated)
---------------------------------------------------------------------------
HOW THE LEAK HAPPENS (in simple terms):
  1. Takes only the first matching trade on the entire dataset (not a real
     multi-year backtest — stops after one "winning story").
  2. SMT comparison between gold/silver can use misaligned slice indices when
     falling back to swing highs/lows on short windows.
  3. FVG/MSS on a sliding 1H chunk does not wait for swing/FVG confirmation
     lag (+1 bar) required by three-candle patterns.

HOW TO FIX:
  1. Run per-day or per-session loops; record all valid setups, not just first.
  2. Align gold/silver candles by timestamp, not array index in a slice.
  3. Only act on FVG at index i after bar i+1 has closed; same for swings.
  4. Enter on the bar after MSS/CISD confirmation closes.

FIXED: Walk-forward 1H bars with causal chunk; detect_fvg_as_of; timestamp-aligned
SMT; cisd_events_up_to/mss_events_up_to; simulate_exits; all valid trades kept.
"""

import argparse
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso,
    swing_highs, swing_lows,
    save_trades,
)
from causal_backtest import (
    detect_fvg_as_of,
    past_slice,
    mss_events_up_to,
    cisd_events_up_to,
    simulate_exits,
)


# ---------------------------------------------------------------------------
# Step 1: 3-second trend detection
# ---------------------------------------------------------------------------

def detect_immediate_trend(candles_1h: list[Candle]) -> Optional[str]:
    """Return 'bullish', 'bearish', or None if not immediately obvious"""
    if len(candles_1h) < 10:
        return None

    last_10 = candles_1h[-10:]
    higher_highs = 0
    higher_lows = 0
    lower_highs = 0
    lower_lows = 0

    for i in range(1, len(last_10)):
        if last_10[i].high > last_10[i - 1].high:
            higher_highs += 1
        if last_10[i].low > last_10[i - 1].low:
            higher_lows += 1
        if last_10[i].high < last_10[i - 1].high:
            lower_highs += 1
        if last_10[i].low < last_10[i - 1].low:
            lower_lows += 1

    if higher_highs >= 7 and higher_lows >= 7:
        return "bullish"
    if lower_lows >= 7 and lower_highs >= 7:
        return "bearish"
    return None


# ---------------------------------------------------------------------------
# Step 2: Find 1H FVG in the impulse leg
# ---------------------------------------------------------------------------

def find_htf_fvg_in_impulse(candles_1h: list[Candle], trend: str) -> Optional[dict]:
    as_of_idx = len(candles_1h) - 1
    fvgs = detect_fvg_as_of(candles_1h, as_of_idx)
    for fvg in reversed(fvgs):
        if trend == "bullish" and fvg["direction"] == "bullish":
            if not _is_mitigated(candles_1h, fvg["idx"], trend, 5):
                return fvg
        if trend == "bearish" and fvg["direction"] == "bearish":
            if not _is_mitigated(candles_1h, fvg["idx"], trend, 5):
                return fvg
    return None


def _is_mitigated(candles, idx, direction, lookahead):
    end = min(idx + lookahead + 1, len(candles))
    if direction == "bullish":
        for i in range(idx + 1, end):
            if candles[i].low <= candles[idx - 1].high:
                return True
    else:
        for i in range(idx + 1, end):
            if candles[i].high >= candles[idx - 1].low:
                return True
    return False


# ---------------------------------------------------------------------------
# Step 3: SMT Divergence check (Gold vs Silver) — timestamp aligned
# ---------------------------------------------------------------------------

def _silver_at(silver_5m: list[Candle], ts: int) -> Optional[Candle]:
    for c in silver_5m:
        if c.timestamp == ts:
            return c
    return None


def check_smt_divergence(
    gold_5m: list[Candle], silver_5m: list[Candle], trend: str, start_ts: int, end_ts: int
) -> Optional[dict]:
    """SMT divergence using swings aligned by timestamp, not array index."""
    g_slice = [c for c in gold_5m if start_ts <= c.timestamp <= end_ts]
    s_slice = [c for c in silver_5m if start_ts <= c.timestamp <= end_ts]
    if len(g_slice) < 5 or len(s_slice) < 5:
        return None

    g_swing_l = swing_lows(g_slice)
    s_swing_l = swing_lows(s_slice)
    g_swing_h = swing_highs(g_slice)
    s_swing_h = swing_highs(s_slice)

    if trend == "bullish" and len(g_swing_l) >= 2:
        g_recent = g_slice[g_swing_l[-1]]
        g_prev = g_slice[g_swing_l[-2]]
        s_recent = _silver_at(silver_5m, g_recent.timestamp)
        s_prev = _silver_at(silver_5m, g_prev.timestamp)
        if s_recent and s_prev and g_recent.low < g_prev.low and s_recent.low > s_prev.low:
            return {
                "type": "bullish_smt",
                "gold_low": g_recent.low,
                "silver_low": s_recent.low,
                "description": (
                    f"Bullish SMT: Gold lower low {g_recent.low:.5f} vs "
                    f"Silver higher low {s_recent.low:.5f}"
                ),
            }

    if trend == "bearish" and len(g_swing_h) >= 2:
        g_recent = g_slice[g_swing_h[-1]]
        g_prev = g_slice[g_swing_h[-2]]
        s_recent = _silver_at(silver_5m, g_recent.timestamp)
        s_prev = _silver_at(silver_5m, g_prev.timestamp)
        if s_recent and s_prev and g_recent.high > g_prev.high and s_recent.high < s_prev.high:
            return {
                "type": "bearish_smt",
                "gold_high": g_recent.high,
                "silver_high": s_recent.high,
                "description": (
                    f"Bearish SMT: Gold higher high {g_recent.high:.5f} vs "
                    f"Silver lower high {s_recent.high:.5f}"
                ),
            }

    return None


# ---------------------------------------------------------------------------
# Step 4: CISD or MSS entry trigger on 5m
# ---------------------------------------------------------------------------

def find_entry_trigger_5m(candles_5m: list[Candle], start_idx: int, trend: str) -> Optional[dict]:
    end_idx = min(start_idx + 20, len(candles_5m) - 1)
    for j in range(start_idx, end_idx + 1):
        for ev in cisd_events_up_to(candles_5m, j, lookback=5):
            if ev["idx"] < start_idx:
                continue
            entry_c = candles_5m[ev["idx"]]
            if trend == "bullish" and ev["direction"] == "bullish":
                return {
                    "type": "cisd_entry",
                    "entry_idx": ev["idx"],
                    "entry_price": entry_c.close,
                    "description": f"Bullish CISD entry at {entry_c.close:.5f}",
                }
            if trend == "bearish" and ev["direction"] == "bearish":
                return {
                    "type": "cisd_entry",
                    "entry_idx": ev["idx"],
                    "entry_price": entry_c.close,
                    "description": f"Bearish CISD entry at {entry_c.close:.5f}",
                }

        for ev in mss_events_up_to(candles_5m, j, lookback=5):
            if ev["idx"] < start_idx:
                continue
            entry_c = candles_5m[ev["idx"]]
            if trend == "bullish" and ev["direction"] == "bullish":
                return {
                    "type": "mss_entry",
                    "entry_idx": ev["idx"],
                    "entry_price": entry_c.close,
                    "description": f"Bullish MSS entry at {entry_c.close:.5f}",
                }
            if trend == "bearish" and ev["direction"] == "bearish":
                return {
                    "type": "mss_entry",
                    "entry_idx": ev["idx"],
                    "entry_price": entry_c.close,
                    "description": f"Bearish MSS entry at {entry_c.close:.5f}",
                }

    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(
    gold_1h: list[Candle], silver_1h: list[Candle],
    gold_5m: list[Candle], silver_5m: list[Candle],
    output_path: str,
):
    trades = []

    for slide_idx in range(20, len(gold_1h)):
        chunk = gold_1h[: slide_idx + 1]
        trend = detect_immediate_trend(chunk[-15:])
        if trend is None:
            continue

        events_log = [{
            "timestamp": to_iso(chunk[-1].timestamp),
            "type": "trend_identified",
            "trend": trend,
            "description": f"Clear {trend} trend identified on 1H (3-second rule)",
        }]

        htf_fvg = find_htf_fvg_in_impulse(chunk, trend)
        if htf_fvg is None:
            continue

        events_log.append({
            "timestamp": to_iso(gold_1h[htf_fvg["idx"]].timestamp),
            "type": "htf_fvg_located",
            "direction": htf_fvg["direction"],
            "upper": round(htf_fvg["upper"], 5),
            "lower": round(htf_fvg["lower"], 5),
            "description": f"1H {trend} FVG located: {htf_fvg['lower']:.5f}-{htf_fvg['upper']:.5f}",
        })

        htf_end_idx = htf_fvg["idx"]
        price_in_fvg = False
        fvg_entry_ts = None
        for i in range(htf_end_idx, min(slide_idx + 1, len(gold_1h))):
            c = gold_1h[i]
            if trend == "bullish" and c.low < htf_fvg["upper"] and c.high > htf_fvg["lower"]:
                price_in_fvg = True
                fvg_entry_ts = c.timestamp
                break
            if trend == "bearish" and c.high > htf_fvg["lower"] and c.low < htf_fvg["upper"]:
                price_in_fvg = True
                fvg_entry_ts = c.timestamp
                break

        if not price_in_fvg or fvg_entry_ts is None:
            continue

        events_log.append({
            "timestamp": to_iso(fvg_entry_ts),
            "type": "price_in_htf_fvg",
            "description": "Price pulled back into 1H FVG zone",
        })

        start_5m = next((i for i, c in enumerate(gold_5m) if c.timestamp >= fvg_entry_ts), 0)
        end_ts = gold_5m[min(start_5m + 29, len(gold_5m) - 1)].timestamp

        smt = check_smt_divergence(gold_5m, silver_5m, trend, fvg_entry_ts, end_ts)
        if smt is None:
            continue

        events_log.append({
            "timestamp": to_iso(fvg_entry_ts),
            "type": "smt_divergence",
            "description": smt["description"],
        })

        entry_trigger = find_entry_trigger_5m(gold_5m, start_5m, trend)
        if entry_trigger is None:
            continue

        entry_idx = entry_trigger["entry_idx"]
        entry_c = gold_5m[entry_idx]
        events_log.append({
            "timestamp": to_iso(entry_c.timestamp),
            "type": "entry_trigger",
            "entry_type": entry_trigger["type"],
            "description": entry_trigger["description"],
        })

        past = past_slice(gold_5m, entry_idx)
        local_low = min(c.low for c in past[max(0, len(past) - 13):])
        local_high = max(c.high for c in past[max(0, len(past) - 13):])

        if trend == "bullish":
            sl = local_low - (local_low * 0.0005)
        else:
            sl = local_high + (local_high * 0.0005)

        risk = abs(entry_trigger["entry_price"] - sl)
        tp = (
            entry_trigger["entry_price"] + (2 * risk)
            if trend == "bullish"
            else entry_trigger["entry_price"] - (2 * risk)
        )

        trade = {
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(entry_c.timestamp),
            "direction": trend,
            "entry_price": round(entry_trigger["entry_price"], 5),
            "stop_loss": round(sl, 5),
            "take_profit": round(tp, 5),
            "reason": f"3-Step ICT Gold: {trend} trend + 1H FVG + SMT divergence + {entry_trigger['type']}",
            "events": list(events_log),
        }
        exit_info = simulate_exits(
            gold_5m, entry_idx, entry_c.timestamp,
            "long" if trend == "bullish" else "short", sl, tp,
        )
        trade.update(exit_info)
        trades.append(trade)

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 13: 3-Step ICT Gold Strategy")
    parser.add_argument("--csv_gold", required=True, help="Gold 1h CSV")
    parser.add_argument("--csv_silver", required=True, help="Silver 1h CSV")
    parser.add_argument("--csv_gold_5m", required=True, help="Gold 5m CSV")
    parser.add_argument("--csv_silver_5m", required=True, help="Silver 5m CSV")
    parser.add_argument("--output", default=None, help="Output JSON")
    args = parser.parse_args()

    gold_1h = load_csv(args.csv_gold)
    silver_1h = load_csv(args.csv_silver)
    gold_5m = load_csv(args.csv_gold_5m)
    silver_5m = load_csv(args.csv_silver_5m)

    output = args.output or "strategy_13_results.json"
    run_strategy(gold_1h, silver_1h, gold_5m, silver_5m, output)


if __name__ == "__main__":
    main()
