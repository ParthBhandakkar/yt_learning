#!/usr/bin/env python3
"""
Strategy 106: Shreya FRX — London Session FVG Pullback (mechanical proxy)

Source: Shreya FRX (@shreya.frx) — ICT/SMC fair-value-gap day trades
Video:
  https://www.instagram.com/reel/DVtL9R5j7k7/  (USDCAD London +1.92R, #fvg #icttrading)
  https://www.instagram.com/reel/DVwY0WXkjYp/  (NZDUSD London FVG, CPI context)
  https://www.instagram.com/reel/DV0tLCSjwID/  (EURUSD London, #fairvaluegaps)
  https://www.instagram.com/reel/DWO-mtRD-Rt/  (USDCAD London +1.8R, #smc #fvg)

MECHANICAL INTERPRETATION (Instagram captions + ICT FVG teaching proxy):
  Public reels consistently tag London session, FVG, ICT/SMC on majors (EURUSD,
  USDCAD, NZDUSD). Comments reference EMA context; profile cites liquidity +
  market structure. Proxy rules:
  1. HTF bias: last completed 4H close vs EMA(50) — longs above, shorts below.
  2. Trade window: UTC 07:00–10:00 (London kill-zone proxy on 1H).
  3. Causal bullish FVG (3-candle gap); bearish mirror.
  4. Tap into unmitigated FVG in bias direction + rejection close → next open.
  5. Stop beyond FVG boundary; TP default 1.8R (reel RR ~1.5–2R). Max 1/day.

Usage:
  python strategy_106_shreya_frx_london_fvg.py --csv1h EURUSD_1h.csv [--output out.json]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import (
    detect_fvg,
    is_fvg_mitigated,
    load_csv,
    parse_csv_filename,
    resample,
    round_turn_cost_price,
    save_trades,
    to_iso,
)


def _ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.empty_like(values)
    k = 2.0 / (length + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _htf_bias_array(candles_1h, htf_ema: int) -> np.ndarray:
    ts1 = np.array([c.timestamp for c in candles_1h])
    bias = np.zeros(len(candles_1h), dtype=np.int8)
    bars4 = resample(candles_1h, 240)
    if len(bars4) < htf_ema + 2:
        return bias
    c4 = np.array([c.close for c in bars4])
    ema4 = _ema(c4, htf_ema)
    end4 = [c.timestamp + 240 * 60 for c in bars4]
    sign4 = np.where(c4 > ema4, 1, np.where(c4 < ema4, -1, 0)).astype(np.int8)
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end4, decision_t) - 1
        if k >= 0:
            bias[i] = sign4[k]
    return bias


def _best_fvg(fvgs, candles, i: int, fvg_dir: str, *, max_age: int = 30):
    best = None
    for fvg in fvgs:
        idx = fvg["idx"]
        if idx > i - 2 or idx < i - max_age:
            continue
        if fvg["direction"] != fvg_dir:
            continue
        if is_fvg_mitigated(candles, idx, fvg["direction"], i - 1):
            continue
        if best is None or idx > best["idx"]:
            best = fvg
    return best


def generate_trades(
    candles_1h,
    *,
    symbol: str = "",
    rr: float = 1.8,
    htf_ema: int = 50,
    london_start: int = 7,
    london_end: int = 10,
    max_per_day: int = 1,
):
    n = len(candles_1h)
    if n < 80:
        return []

    fvgs = detect_fvg(candles_1h)
    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)
    bias = _htf_bias_array(candles_1h, htf_ema)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    trades = []
    day_count: dict[str, int] = {}
    i = 40
    while i < n - 1:
        day = days[i]
        if day_count.get(day, 0) >= max_per_day:
            i += 1
            continue
        if hours[i] < london_start or hours[i] >= london_end:
            i += 1
            continue
        b = int(bias[i])
        if b == 0:
            i += 1
            continue

        direction = None
        fvg = None
        if b == 1:
            fvg = _best_fvg(fvgs, candles_1h, i, "bullish")
            if fvg is not None:
                if l[i] <= fvg["upper"] and c[i] >= fvg["lower"] and c[i] > o[i]:
                    direction = "long"
        elif b == -1:
            fvg = _best_fvg(fvgs, candles_1h, i, "bearish")
            if fvg is not None:
                if h[i] >= fvg["lower"] and c[i] <= fvg["upper"] and c[i] < o[i]:
                    direction = "short"

        if direction is None or fvg is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        gap = float(fvg["upper"] - fvg["lower"])
        pad = max(4.0 * cost, gap * 0.15)

        if direction == "long":
            stop = float(fvg["lower"]) - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = float(fvg["upper"]) + pad
            risk = stop - entry
            if risk <= 0:
                i += 1
                continue
            tp = entry - rr * risk

        exit_idx, exit_price, outcome = None, None, "open"
        for j in range(entry_idx, n):
            if direction == "long":
                if l[j] <= stop:
                    exit_idx, exit_price, outcome = j, stop, "loss"
                    break
                if h[j] >= tp:
                    exit_idx, exit_price, outcome = j, tp, "win"
                    break
            else:
                if h[j] >= stop:
                    exit_idx, exit_price, outcome = j, stop, "loss"
                    break
                if l[j] <= tp:
                    exit_idx, exit_price, outcome = j, tp, "win"
                    break
        if exit_idx is None:
            exit_idx, exit_price = n - 1, float(c[n - 1])

        day_count[day] = day_count.get(day, 0) + 1
        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(ts[entry_idx])),
            "direction": direction,
            "entry_price": round(entry, 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(tp), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "setup": "shreya_london_fvg",
            "symbol": symbol or None,
            "reason": (
                f"Shreya FRX London FVG {fvg['lower']:.5f}-{fvg['upper']:.5f} "
                f"bias={'bull' if b == 1 else 'bear'} RR={rr}"
            ),
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 106: Shreya FRX London FVG")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=1.8)
    p.add_argument("--htf-ema", type=int, default=50)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_106_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr, htf_ema=args.htf_ema)


if __name__ == "__main__":
    main()
