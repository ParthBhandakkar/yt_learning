#!/usr/bin/env python3
"""
Strategy 69: ICT Market Maker Model (MMXM)

Source: Faiz SMC - "Make 'F*ck You' Money With This Simple ICT MMXM Strategy"
Video: https://www.youtube.com/watch?v=06DxvYmOXP4

Core concepts:
  - 4H timeframe for trend direction
  - Mark lowest (bullish) or highest (bearish) point before BOS
  - Wait for sweep of that liquidity level
  - 15m MSS with strong displacement after sweep
  - Entry at retest of FVG or breaker block formed during shift

Usage:
  python strategy_69_ict_market_maker.py --csv4h 4h.csv --csv15m 15m.csv [--csv1m 1m.csv]
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
    save_trades, resample,
)


# ---------------------------------------------------------------------------
# Step 1: 4H trend
# ---------------------------------------------------------------------------

def trend_4h(candles_4h: list[Candle]) -> str:
    if len(candles_4h) < 6:
        return "neutral"
    last = candles_4h[-6:]
    hh = sum(1 for i in range(1, len(last)) if last[i].high > last[i - 1].high)
    ll = sum(1 for i in range(1, len(last)) if last[i].low < last[i - 1].low)
    if hh >= 4: return "bullish"
    if ll >= 4: return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Step 2: Liquidity level before BOS
# ---------------------------------------------------------------------------

def find_liquidity_level(candles_4h: list[Candle], trend: str) -> Optional[dict]:
    sh = swing_highs(candles_4h)
    sl = swing_lows(candles_4h)

    if trend == "bullish" and sl:
        level = candles_4h[sl[-1]].low
        return {"type": "swing_low", "level": level, "idx": sl[-1]}
    if trend == "bearish" and sh:
        level = candles_4h[sh[-1]].high
        return {"type": "swing_high", "level": level, "idx": sh[-1]}
    return None


# ---------------------------------------------------------------------------
# Step 3-4: Sweep + 15m MSS + FVG entry
# ---------------------------------------------------------------------------

def find_entry_15m(candles_15m: list[Candle], liq_level: float, liq_type: str, start_idx: int) -> Optional[dict]:
    for i in range(start_idx, len(candles_15m) - 3):
        c = candles_15m[i]

        swept = False
        if liq_type == "swing_low" and c.low < liq_level:
            swept = True
        elif liq_type == "swing_high" and c.high > liq_level:
            swept = True
        if not swept:
            continue

        for j in range(i + 1, min(i + 15, len(candles_15m))):
            cj = candles_15m[j]
            body = abs(cj.close - cj.open)
            avg_body = sum(abs(x.close - x.open) for x in candles_15m[max(0, j - 5):j]) / max(1, min(5, j))
            displaced = body > avg_body * 1.5

            if not displaced:
                continue

            mss = detect_mss(candles_15m[max(0, j - 3):j + 3], lookback=3)
            for ev in mss:
                direction = None
                if liq_type == "swing_low" and ev["direction"] == "bullish":
                    direction = "long"
                elif liq_type == "swing_high" and ev["direction"] == "bearish":
                    direction = "short"

                if direction:
                    # Entry at FVG retest
                    fvgs = detect_fvg(candles_15m[max(0, j - 3):j + 5])
                    for fvg in fvgs:
                        entry_price = cj.close
                        local_low = min(x.low for x in candles_15m[max(0, j - 3):j + 3])
                        local_high = max(x.high for x in candles_15m[max(0, j - 3):j + 3])

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
                            "description": f"MMXM: {liq_type} {liq_level:.5f} swept + 15m MSS + FVG at {entry_price:.5f}",
                        }
    return None


# ---------------------------------------------------------------------------
# Main strategy
# ---------------------------------------------------------------------------

def run_strategy(candles_4h: list[Candle], candles_15m: list[Candle], output_path: str):
    trades = []

    trend = trend_4h(candles_4h)
    if trend == "neutral":
        print("No clear 4H trend")
        save_trades(trades, output_path)
        return trades

    events_log = [{
        "timestamp": to_iso(candles_4h[-1].timestamp),
        "type": "htf_trend",
        "trend": trend,
        "description": f"4H trend: {trend}",
    }]

    liq = find_liquidity_level(candles_4h, trend)
    if liq is None:
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_4h[liq["idx"]].timestamp),
        "type": "liquidity_level",
        "level": round(liq["level"], 5),
        "description": f"Liquidity {liq['type']} at {liq['level']:.5f}",
    })

    liq_ts = candles_4h[liq["idx"]].timestamp
    start_15m = next((i for i, c in enumerate(candles_15m) if c.timestamp >= liq_ts), 0)

    result = find_entry_15m(candles_15m, liq["level"], liq["type"], start_15m)
    if result is None:
        print("No entry found")
        save_trades(trades, output_path)
        return trades

    events_log.append({
        "timestamp": to_iso(candles_15m[result["sweep_idx"]].timestamp),
        "type": "liquidity_sweep",
        "description": f"Liquidity {liq['type']} swept",
    })
    events_log.append({
        "timestamp": to_iso(candles_15m[result["entry_idx"]].timestamp),
        "type": "entry_trigger",
        "direction": result["direction"],
        "description": result["description"],
    })

    trade = {
        "trade_number": len(trades) + 1,
        "entry_time": to_iso(candles_15m[result["entry_idx"]].timestamp),
        "direction": result["direction"],
        "entry_price": round(result["entry_price"], 5),
        "stop_loss": round(result["sl"], 5),
        "take_profit": round(result["tp"], 5),
        "reason": result["description"],
        "events": list(events_log),
    }
    trades.append(trade)

    entry_ts = datetime.fromisoformat(trade["entry_time"]).timestamp()
    for c in candles_15m:
        if c.timestamp > entry_ts:
            if result["direction"] == "long":
                if c.high >= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.low <= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
            else:
                if c.low <= result["tp"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["tp"]; trade["outcome"] = "win"; break
                elif c.high >= result["sl"]: trade["exit_time"] = to_iso(c.timestamp); trade["exit_price"] = result["sl"]; trade["outcome"] = "loss"; break
    if "exit_time" not in trade:
        trade["exit_time"] = to_iso(candles_15m[-1].timestamp)
        trade["exit_price"] = candles_15m[-1].close
        trade["outcome"] = "open"

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 69: ICT Market Maker Model")
    parser.add_argument("--csv4h", required=True, help="4-hour CSV")
    parser.add_argument("--csv15m", required=True, help="15-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    candles_4h = load_csv(args.csv4h)
    candles_15m = load_csv(args.csv15m)

    meta = parse_csv_filename(args.csv4h)
    output = args.output or f"strategy_69_results_{meta['symbol']}.json"
    run_strategy(candles_4h, candles_15m, output)


if __name__ == "__main__":
    main()
