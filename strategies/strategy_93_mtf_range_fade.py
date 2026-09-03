#!/usr/bin/env python3
"""
Strategy 93: MTF Range Fade (NEW, forex-general)

Built from the lessons of every prior test in this project:
  - FX majors MEAN-REVERT, so this FADES extensions back to value (not breakout).
  - REGIME FILTER agrees with the entry: trade only when the market is RANGING
    (low efficiency ratio). In trends, mean-reversion bleeds, so we switch OFF.
    (This is the opposite of Strategy 09's mistake, whose trend filter fought
    its own pullback entries.)
  - ACHIEVABLE target: tunable (revert to the 4H mean, or a modest fixed R) so
    the win rate can clear the spread/commission hurdle.
  - Keeps Strategy 09's good skeleton: MTF value (4H EMA), structure-based stop
    beyond the sweep extreme + buffer, one position at a time, defined risk.

Trigger (ICT/SMC flavour, causal):
  - 4H EMA = fair value. Extension = (close - ema)/ATR on 1H.
  - PREMIUM (ext >= +stretch) AND ranging: a 1H bar sweeps a recent swing HIGH
    and closes back below it (failed breakout) -> SHORT toward value.
  - DISCOUNT (ext <= -stretch) AND ranging: sweep recent swing LOW + close back
    above -> LONG toward value.

Entry next bar open; stop beyond the sweep extreme + buffer; conservative fills;
per-pair costs applied downstream.

Usage: python strategy_93_mtf_range_fade.py --csv1h EURUSD_1h.csv
"""
import argparse
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, to_iso, parse_csv_filename, save_trades, resample
from fast_core import arrays_from_candles, simulate_exits_arrays
from strategy_91_mtf_liquidity_reversion import _ema, _swing_levels, _last_before
from strategy_92_mtf_mean_reversion import _atr, _htf_ema_at_1h


def _efficiency_ratio(close: np.ndarray, n: int) -> np.ndarray:
    """Kaufman efficiency ratio: |net change| / sum|changes| over n bars.
    ~1 = clean trend, ~0 = choppy/ranging. Causal."""
    er = np.zeros(len(close))
    diff = np.abs(np.diff(close, prepend=close[0]))
    for i in range(n, len(close)):
        net = abs(close[i] - close[i - n])
        path = diff[i - n + 1:i + 1].sum()
        er[i] = net / path if path > 0 else 1.0
    return er


def generate_trades(candles_1h, *, htf_ema=50, atr_len=14, stretch=1.5,
                    er_len=20, er_max=0.35, target_mode="mean", rr=1.0,
                    min_rr=0.8, sweep_buffer_frac=0.0005, stop_lookback=3,
                    min_stop_atr=0.0):
    n = len(candles_1h)
    if n < max(htf_ema * 4, atr_len, er_len) + 20:
        return []
    ts, o, h, l, c, _ = arrays_from_candles(candles_1h)
    ema_at = _htf_ema_at_1h(candles_1h, ts, htf_ema)
    atr = _atr(h, l, c, atr_len)
    er = _efficiency_ratio(c, er_len)
    sh_idx, sl_idx = _swing_levels(h, l)

    trades = []
    i = er_len + 2
    while i < n - 1:
        fv = ema_at[i]
        if np.isnan(fv) or atr[i] <= 0 or er[i] > er_max:   # only RANGING regimes
            i += 1
            continue
        ext = (c[i] - fv) / atr[i]
        direction = sweep_extreme = None
        if ext >= stretch:                 # premium -> fade short
            shx = _last_before(sh_idx, 1, i)
            if shx is not None and h[i] > h[shx] and c[i] < h[shx] and c[i] < o[i]:
                direction, sweep_extreme = "short", h[i]
        elif ext <= -stretch:              # discount -> fade long
            slx = _last_before(sl_idx, 1, i)
            if slx is not None and l[i] < l[slx] and c[i] > l[slx] and c[i] > o[i]:
                direction, sweep_extreme = "long", l[i]
        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry = o[entry_idx]
        floor = min_stop_atr * atr[i]   # widen stop so fixed costs are a small fraction of risk
        if direction == "long":
            stop = min(l[max(0, i - stop_lookback + 1):i + 1].min(), sweep_extreme) * (1 - sweep_buffer_frac)
            if floor > 0:
                stop = min(stop, entry - floor)
            risk = entry - stop
            target = fv if target_mode == "mean" else entry + rr * risk
            reward = target - entry
        else:
            stop = max(h[max(0, i - stop_lookback + 1):i + 1].max(), sweep_extreme) * (1 + sweep_buffer_frac)
            if floor > 0:
                stop = max(stop, entry + floor)
            risk = stop - entry
            target = fv if target_mode == "mean" else entry - rr * risk
            reward = entry - target
        if risk <= 0 or reward <= 0 or reward / risk < min_rr:
            i += 1
            continue

        exit_idx, exit_price, code = simulate_exits_arrays(
            h, l, c, ts, entry_idx, int(ts[entry_idx]), direction, float(stop), float(target))
        exit_idx = int(exit_idx)
        outcome = "win" if code == 1 else "loss" if code == -1 else "open"
        trades.append({
            "trade_number": len(trades) + 1, "entry_time": to_iso(int(ts[entry_idx])),
            "direction": direction, "entry_price": round(float(entry), 6),
            "stop_loss": round(float(stop), 6), "take_profit": round(float(target), 6),
            "exit_time": to_iso(int(ts[exit_idx])), "exit_price": round(float(exit_price), 6),
            "outcome": outcome,
            "reason": f"range-fade {'premium' if direction=='short' else 'discount'} "
                      f"ext={ext:+.1f}ATR ER={er[i]:.2f} -> {target_mode}"})
        i = exit_idx + 1 if exit_idx > i else i + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 93: MTF Range Fade")
    parser.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    parser.add_argument("--output", default=None)
    parser.add_argument("--stretch", type=float, default=1.5)
    parser.add_argument("--er-max", type=float, default=0.35)
    parser.add_argument("--target-mode", default="mean")
    args = parser.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    out = args.output or f"strategy_93_results_{meta['symbol']}.json"
    run_strategy(candles, out, stretch=args.stretch, er_max=args.er_max, target_mode=args.target_mode)


if __name__ == "__main__":
    main()
