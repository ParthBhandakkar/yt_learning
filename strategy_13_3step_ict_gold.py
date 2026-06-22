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
"""

import argparse
import sys
import os
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (
    Candle, load_csv, to_iso, parse_csv_filename,
    detect_fvg, detect_ifvg, detect_mss, detect_cisd,
    swing_highs, swing_lows,
    resample, save_trades,
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
    fvgs = detect_fvg(candles_1h)
    for fvg in fvgs:
        if trend == "bullish" and fvg["direction"] == "bullish":
            # Check it's in the impulse (not mitigated within 5 candles)
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
# Step 3: SMT Divergence check (Gold vs Silver)
# ---------------------------------------------------------------------------

def check_smt_divergence(
    gold_5m: list[Candle], silver_5m: list[Candle], trend: str
) -> Optional[dict]:
    """Check SMT divergence: Gold makes a lower low while Silver makes a higher low, etc."""
    g_swing_l = swing_lows(gold_5m)
    s_swing_l = swing_lows(silver_5m)
    g_swing_h = swing_highs(gold_5m)
    s_swing_h = swing_highs(silver_5m)

    if trend == "bullish" and len(g_swing_l) >= 2 and len(s_swing_l) >= 2:
        g_recent_low = gold_5m[g_swing_l[-1]].low
        g_prev_low = gold_5m[g_swing_l[-2]].low
        s_recent_low = silver_5m[s_swing_l[-1]].low
        s_prev_low = silver_5m[s_swing_l[-2]].low

        if g_recent_low < g_prev_low and s_recent_low > s_prev_low:
            return {
                "type": "bullish_smt",
                "gold_low": g_recent_low,
                "silver_low": s_recent_low,
                "description": f"Bullish SMT: Gold made lower low {g_recent_low:.5f} but Silver made higher low {s_recent_low:.5f}",
            }

    if trend == "bearish" and len(g_swing_h) >= 2 and len(s_swing_h) >= 2:
        g_recent_high = gold_5m[g_swing_h[-1]].high
        g_prev_high = gold_5m[g_swing_h[-2]].high
        s_recent_high = silver_5m[s_swing_h[-1]].high
        s_prev_high = silver_5m[s_swing_h[-2]].high

        if g_recent_high > g_prev_high and s_recent_high < s_prev_high:
            return {
                "type": "bearish_smt",
                "gold_high": g_recent_high,
                "silver_high": s_recent_high,
                "description": f"Bearish SMT: Gold made higher high {g_recent_high:.5f} but Silver made lower high {s_recent_high:.5f}",
            }

    return None


# ---------------------------------------------------------------------------
# Step 4: CISD or MSS entry trigger on 5m
# ---------------------------------------------------------------------------

def find_entry_trigger_5m(candles_5m: list[Candle], start_idx: int, trend: str) -> Optional[dict]:
    # CISD entry
    cisd_events = detect_cisd(candles_5m[start_idx:start_idx + 20], lookback=5)
    for ev in cisd_events:
        ev["idx"] += start_idx
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

    # MSS entry
    mss_events = detect_mss(candles_5m[start_idx:start_idx + 20], lookback=5)
    for ev in mss_events:
        ev["idx"] += start_idx
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

    for slide_idx in range(len(gold_1h) - 20):
        chunk = gold_1h[slide_idx:slide_idx + 15]
        trend = detect_immediate_trend(chunk)
        if trend is None:
            continue

        events_log = []
        events_log.append({
            "timestamp": to_iso(chunk[-1].timestamp),
            "type": "trend_identified",
            "trend": trend,
            "description": f"Clear {trend} trend identified on 1H (3-second rule)",
        })

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

        # Wait for price to pull back into FVG on higher timeframe
        htf_end_idx = htf_fvg["idx"]
        price_in_fvg = False
        for i in range(htf_end_idx, len(gold_1h)):
            c = gold_1h[i]
            if trend == "bullish" and c.low < htf_fvg["upper"] and c.high > htf_fvg["lower"]:
                price_in_fvg = True
                fvg_entry_ts = c.timestamp
                break
            if trend == "bearish" and c.high > htf_fvg["lower"] and c.low < htf_fvg["upper"]:
                price_in_fvg = True
                fvg_entry_ts = c.timestamp
                break

        if not price_in_fvg:
            continue

        events_log.append({
            "timestamp": to_iso(fvg_entry_ts),
            "type": "price_in_htf_fvg",
            "description": f"Price pulled back into 1H FVG zone",
        })

        # Step 3: SMT Divergence
        # Find corresponding 5m data
        start_5m = next((i for i, c in enumerate(gold_5m) if c.timestamp >= fvg_entry_ts), 0)
        gold_slice = gold_5m[start_5m:start_5m + 30]
        silver_slice = silver_5m[start_5m:start_5m + 30]

        smt = check_smt_divergence(gold_slice, silver_slice if silver_slice else silver_5m, trend)
        if smt is None:
            continue

        events_log.append({
            "timestamp": to_iso(fvg_entry_ts),
            "type": "smt_divergence",
            "description": smt["description"],
        })

        # Step 4: Entry trigger
        entry_trigger = find_entry_trigger_5m(gold_5m, start_5m, trend)
        if entry_trigger is None:
            continue

        entry_c = gold_5m[entry_trigger["entry_idx"]]
        events_log.append({
            "timestamp": to_iso(entry_c.timestamp),
            "type": "entry_trigger",
            "entry_type": entry_trigger["type"],
            "description": entry_trigger["description"],
        })

        # SL behind SMT leg or structural change block
        local_low = min(c.low for c in gold_5m[max(0, start_5m - 3):start_5m + 10])
        local_high = max(c.high for c in gold_5m[max(0, start_5m - 3):start_5m + 10])

        if trend == "bullish":
            sl = local_low - (local_low * 0.0005)
        else:
            sl = local_high + (local_high * 0.0005)

        risk = abs(entry_trigger["entry_price"] - sl)
        tp = entry_trigger["entry_price"] + (2 * risk) if trend == "bullish" else entry_trigger["entry_price"] - (2 * risk)

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
        trades.append(trade)

        # Check exit
        entry_ts = entry_c.timestamp
        for c in gold_5m:
            if c.timestamp > entry_ts:
                if trade["direction"] == "bullish":
                    if c.high >= tp:
                        trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.low <= sl:
                        trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
                else:
                    if c.low <= tp:
                        trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = tp; trade["outcome"] = "win"; break
                    elif c.high >= sl:
                        trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = sl; trade["outcome"] = "loss"; break
        if "exit_time" not in trade:
            trade["exit_time"] = to_iso(gold_5m[-1].timestamp)
            trade["exit_price"] = gold_5m[-1].close
            trade["outcome"] = "open"

        break  # One trade per run

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
