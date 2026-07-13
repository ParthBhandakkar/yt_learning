#!/usr/bin/env python3
"""
Strategy 107: Shreya FRX — Liquidity Sweep + FVG Confluence (mechanical proxy)

Source: Shreya FRX (@shreya.frx) — ICT/SMC institutional footprint trades
Video:
  https://www.instagram.com/reel/DVtL9R5j7k7/
  https://www.instagram.com/reel/DV8aW-cD40u/
  https://www.instagram.com/reel/DWO-mtRD-Rt/  (#ict #smc #fvg London +1.8R)

MECHANICAL INTERPRETATION (reel tags + ICT liquidity-sweep model proxy):
  Profile and hashtags emphasize liquidity sweeps, order blocks, and FVG entries
  during London/NY sessions. ICT sweep model encoded causally on 1H:
  1. 4H EMA(50) daily bias (draw-on-liquidity direction).
  2. Sweep window UTC 07:00–10:00; entry window extended to 12:00 (London→NY overlap).
  3. Counter-bias liquidity sweep: wick through recent swing, close reclaims.
  4. Reversal leg leaves causal FVG in bias direction within 12 bars after sweep.
  5. Entry on FVG tap + rejection; SL beyond sweep extreme; TP 2R. Max 1/day.

Usage:
  python strategy_107_shreya_frx_sweep_fvg.py --csv1h USDCAD_1h.csv [--output out.json]
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


def _swings(h, l):
    sh, sl = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl.append(i)
    return sh, sl


def _last_before(sorted_idx: list[int], i: int, confirm: int = 1) -> int | None:
    pos = bisect.bisect_right(sorted_idx, i - confirm) - 1
    return sorted_idx[pos] if pos >= 0 else None


def _fvg_after_sweep(fvgs, candles, sweep_idx: int, i: int, fvg_dir: str, *, window: int = 12):
    lo = max(sweep_idx, i - window)
    best = None
    for fvg in fvgs:
        idx = fvg["idx"]
        if idx < lo or idx > i - 2:
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
    rr: float = 2.0,
    htf_ema: int = 50,
    sweep_start: int = 7,
    sweep_end: int = 10,
    entry_end: int = 12,
    sweep_lookback: int = 20,
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
    sh, sl = _swings(h, l)
    days = [datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat() for t in ts]
    hours = [datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts]

    trades = []
    traded_days: set[str] = set()
    sweep_state: dict[str, tuple[int, str, float]] = {}

    i = 40
    while i < n - 1:
        day = days[i]
        if hours[i] < sweep_start or hours[i] >= entry_end:
            i += 1
            continue

        b = int(bias[i])
        if b == 0:
            i += 1
            continue

        # Phase 1: detect counter-bias sweep during London open (store per day)
        if (
            day not in traded_days
            and day not in sweep_state
            and sweep_start <= hours[i] < sweep_end
        ):
            if b == 1:
                slx = _last_before(sl, i)
                if slx is not None and i - slx <= sweep_lookback:
                    level = float(l[slx])
                    if l[i] < level and c[i] > level and c[i] > o[i]:
                        sweep_state[day] = (i, "long", float(l[i]))
            elif b == -1:
                shx = _last_before(sh, i)
                if shx is not None and i - shx <= sweep_lookback:
                    level = float(h[shx])
                    if h[i] > level and c[i] < level and c[i] < o[i]:
                        sweep_state[day] = (i, "short", float(h[i]))

        # Phase 2: after sweep, wait for FVG tap in bias direction
        if day in traded_days or day not in sweep_state:
            i += 1
            continue

        sweep_idx, direction, sweep_ext = sweep_state[day]
        if i <= sweep_idx + 1:
            i += 1
            continue

        fvg_dir = "bullish" if direction == "long" else "bearish"
        fvg = _fvg_after_sweep(fvgs, candles_1h, sweep_idx, i, fvg_dir)
        if fvg is None:
            i += 1
            continue

        tapped = False
        if direction == "long":
            tapped = l[i] <= fvg["upper"] and c[i] >= fvg["lower"] and c[i] > o[i]
        else:
            tapped = h[i] >= fvg["lower"] and c[i] <= fvg["upper"] and c[i] < o[i]
        if not tapped:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry = float(o[entry_idx])
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        pad = max(4.0 * cost, abs(entry - sweep_ext) * 0.05)

        if direction == "long":
            stop = sweep_ext - pad
            risk = entry - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry + rr * risk
        else:
            stop = sweep_ext + pad
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

        traded_days.add(day)
        sweep_state.pop(day, None)
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
            "setup": "shreya_sweep_fvg",
            "symbol": symbol or None,
            "reason": (
                f"Shreya FRX sweep@{sweep_idx} + FVG {fvg['lower']:.5f}-{fvg['upper']:.5f} "
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
    p = argparse.ArgumentParser(description="Strategy 107: Shreya FRX Sweep + FVG")
    p.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    p.add_argument("--output", default=None)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--htf-ema", type=int, default=50)
    args = p.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_107_results_{sym}.json"
    run_strategy(candles, out, symbol=sym, rr=args.rr, htf_ema=args.htf_ema)


if __name__ == "__main__":
    main()
