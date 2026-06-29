#!/usr/bin/env python3
"""
Strategy 91: Multi-Timeframe Liquidity-Sweep Reversal (NEW, forex-general)

A confluence model that combines the requested concepts into one causal system:

  - TREND / MTF bias (ICT "draw on liquidity" direction):
      Resample the 1H input to 4H and take EMA(htf_ema). Only look for longs when
      the last COMPLETED 4H bar closed above its EMA (uptrend), shorts when below.
  - ICT liquidity sweep + reclaim (the actual trigger):
      In an uptrend, wait for a 1H bar to trade BELOW a recent swing low (grab
      sell-side liquidity / run stops) and then CLOSE back ABOVE it (reclaim).
      That failed breakdown is the institutional footprint. Mirror for shorts.
  - SMC market structure:
      Levels are 3-bar swing highs/lows confirmed one bar later (no peeking).
  - Premium / discount (OTE):
      Longs only when the sweep happens in the DISCOUNT half of the recent range,
      shorts only in PREMIUM. Keeps us buying low / selling high.
  - Price action confirmation:
      The reclaim bar must close in its direction (bullish close for longs).

Risk model: stop just beyond the sweep extreme; target = rr * risk. One position
at a time; entry at the NEXT bar's open; conservative intrabar exits; per-pair
trading costs applied downstream in core.enrich_trades_pnl.

Fully causal: every decision at bar i uses only data available at/through bar i.

Usage:
  python strategy_91_mtf_liquidity_reversion.py --csv1h EURUSD_1h.csv [--output out.json]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, to_iso, parse_csv_filename, save_trades, resample
from fast_core import arrays_from_candles, simulate_exits_arrays


def _ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.empty_like(values)
    k = 2.0 / (length + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _swing_levels(h: np.ndarray, l: np.ndarray):
    """3-bar fractal swing highs/lows; index s is confirmed only after bar s+1."""
    n = len(h)
    sh_idx, sl_idx = [], []
    for i in range(1, n - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh_idx.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl_idx.append(i)
    return sh_idx, sl_idx


def _last_before(sorted_idx: list, confirm_offset: int, i: int):
    """Most recent swing index whose confirmation bar (idx+1) is <= i."""
    import bisect
    # confirmed at idx+confirm_offset; we need idx+confirm_offset <= i  => idx <= i-confirm_offset
    pos = bisect.bisect_right(sorted_idx, i - confirm_offset) - 1
    return sorted_idx[pos] if pos >= 0 else None


def _htf_bias_array(candles_1h, htf_ema: int) -> np.ndarray:
    """+1 bullish / -1 bearish / 0 none per 1H bar, from completed 4H bars only."""
    ts1 = np.array([c.timestamp for c in candles_1h])
    bias = np.zeros(len(candles_1h), dtype=np.int8)
    bars4 = resample(candles_1h, 240)  # 4H
    if len(bars4) < htf_ema + 2:
        return bias
    c4 = np.array([c.close for c in bars4])
    ema4 = _ema(c4, htf_ema)
    end4 = np.array([c.timestamp + 240 * 60 for c in bars4])  # completion time of each 4H bar
    sign4 = np.where(c4 > ema4, 1, np.where(c4 < ema4, -1, 0)).astype(np.int8)
    # for each 1H bar, last 4H bar completed by this bar's close (ts + 3600)
    import bisect
    end_list = end4.tolist()
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end_list, decision_t) - 1
        if k >= 0:
            bias[i] = sign4[k]
    return bias


def generate_trades(candles_1h, *, htf_ema=50, rr=2.0, range_lookback=20,
                    sweep_buffer_frac=0.0005, use_pd_filter=True, stop_lookback=3):
    n = len(candles_1h)
    if n < max(htf_ema * 4, range_lookback) + 10:
        return []

    ts, o, h, l, c, _ = arrays_from_candles(candles_1h)
    bias = _htf_bias_array(candles_1h, htf_ema)
    sh_idx, sl_idx = _swing_levels(h, l)

    trades = []
    i = range_lookback + 2
    while i < n - 1:
        b = bias[i]
        if b == 0:
            i += 1
            continue

        rng_hi = h[i - range_lookback:i].max()
        rng_lo = l[i - range_lookback:i].min()
        mid = (rng_hi + rng_lo) / 2.0

        direction = None
        sweep_extreme = None

        if b == 1:  # uptrend -> long on sell-side sweep + reclaim in discount
            sl = _last_before(sl_idx, 1, i)
            if sl is not None:
                level = l[sl]
                if l[i] < level and c[i] > level and c[i] > o[i]:
                    if (not use_pd_filter) or (l[i] <= mid):
                        direction = "long"
                        sweep_extreme = l[i]
        elif b == -1:  # downtrend -> short on buy-side sweep + reclaim in premium
            shx = _last_before(sh_idx, 1, i)
            if shx is not None:
                level = h[shx]
                if h[i] > level and c[i] < level and c[i] < o[i]:
                    if (not use_pd_filter) or (h[i] >= mid):
                        direction = "short"
                        sweep_extreme = h[i]

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_price = o[entry_idx]

        if direction == "long":
            recent_low = min(l[max(0, i - stop_lookback + 1):i + 1].min(), sweep_extreme)
            stop = recent_low * (1 - sweep_buffer_frac)
            risk = entry_price - stop
            if risk <= 0:
                i += 1
                continue
            tp = entry_price + rr * risk
        else:
            recent_high = max(h[max(0, i - stop_lookback + 1):i + 1].max(), sweep_extreme)
            stop = recent_high * (1 + sweep_buffer_frac)
            risk = stop - entry_price
            if risk <= 0:
                i += 1
                continue
            tp = entry_price - rr * risk

        exit_idx, exit_price, code = simulate_exits_arrays(
            h, l, c, ts, entry_idx, int(ts[entry_idx]), direction, float(stop), float(tp)
        )
        exit_idx = int(exit_idx)
        outcome = "win" if code == 1 else "loss" if code == -1 else "open"

        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(ts[entry_idx])),
            "direction": direction,
            "entry_price": round(float(entry_price), 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(tp), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "reason": (f"4H {'up' if direction=='long' else 'down'}trend + 1H liquidity "
                       f"sweep/reclaim + {'discount' if direction=='long' else 'premium'} (rr {rr})"),
        })
        i = exit_idx + 1 if exit_idx > i else i + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 91: MTF Liquidity-Sweep Reversal")
    parser.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--rr", type=float, default=2.0)
    parser.add_argument("--htf-ema", type=int, default=50)
    args = parser.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    out = args.output or f"strategy_91_results_{meta['symbol']}.json"
    run_strategy(candles, out, rr=args.rr, htf_ema=args.htf_ema)


if __name__ == "__main__":
    main()
