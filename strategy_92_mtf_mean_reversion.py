#!/usr/bin/env python3
"""
Strategy 92: MTF Mean-Reversion to HTF Fair Value (NEW, forex-general)

FX majors mean-revert more than they trend, so this fades stretched moves back
to higher-timeframe fair value, using an ICT liquidity-sweep trigger:

  - HTF fair value: EMA(htf_ema) on the 4H series (resampled from 1H).
  - Extension filter: only act when 1H price is stretched > `stretch` * ATR away
    from the 4H EMA (premium if above, discount if below).
  - Trigger (counter-extension): in PREMIUM, wait for a buy-side liquidity sweep
    of a recent swing high + bearish reclaim -> SHORT back toward fair value.
    Mirror in DISCOUNT for LONG.
  - Target: the 4H EMA (dynamic fair value). Stop just beyond the sweep extreme.
  - Filters: require min reward:risk `min_rr` so we skip trades already at value.

Fully causal; entry next bar open; conservative exits; per-pair costs downstream.

Usage: python strategy_92_mtf_mean_reversion.py --csv1h EURUSD_1h.csv
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


def _atr(h, l, c, length):
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = np.empty(n)
    out[0] = tr[0]
    k = 1.0 / length
    for i in range(1, n):
        out[i] = tr[i] * k + out[i - 1] * (1 - k)
    return out


def _htf_ema_at_1h(candles_1h, ts1, htf_ema):
    """Map each 1H bar to the EMA value of the last COMPLETED 4H bar (causal)."""
    bars4 = resample(candles_1h, 240)
    out = np.full(len(candles_1h), np.nan)
    if len(bars4) < htf_ema + 2:
        return out
    c4 = np.array([c.close for c in bars4])
    ema4 = _ema(c4, htf_ema)
    end4 = [c.timestamp + 240 * 60 for c in bars4]
    for i in range(len(candles_1h)):
        k = bisect.bisect_right(end4, int(ts1[i]) + 3600) - 1
        if k >= 0:
            out[i] = ema4[k]
    return out


def generate_trades(candles_1h, *, htf_ema=50, atr_len=14, stretch=1.5,
                    min_rr=1.0, sweep_buffer_frac=0.0005, stop_lookback=3):
    n = len(candles_1h)
    if n < max(htf_ema * 4, atr_len) + 20:
        return []
    ts, o, h, l, c, _ = arrays_from_candles(candles_1h)
    ema_at = _htf_ema_at_1h(candles_1h, ts, htf_ema)
    atr = _atr(h, l, c, atr_len)
    sh_idx, sl_idx = _swing_levels(h, l)

    trades = []
    i = 5
    while i < n - 1:
        fv = ema_at[i]
        if np.isnan(fv) or atr[i] <= 0:
            i += 1
            continue
        stretched = (c[i] - fv) / atr[i]

        direction = None
        sweep_extreme = None
        if stretched >= stretch:          # premium -> fade short
            shx = _last_before(sh_idx, 1, i)
            if shx is not None:
                lvl = h[shx]
                if h[i] > lvl and c[i] < lvl and c[i] < o[i]:
                    direction = "short"; sweep_extreme = h[i]
        elif stretched <= -stretch:        # discount -> fade long
            slx = _last_before(sl_idx, 1, i)
            if slx is not None:
                lvl = l[slx]
                if l[i] < lvl and c[i] > lvl and c[i] > o[i]:
                    direction = "long"; sweep_extreme = l[i]

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry = o[entry_idx]
        target = fv  # revert to HTF fair value
        if direction == "long":
            stop = min(l[max(0, i - stop_lookback + 1):i + 1].min(), sweep_extreme) * (1 - sweep_buffer_frac)
            risk = entry - stop
            reward = target - entry
        else:
            stop = max(h[max(0, i - stop_lookback + 1):i + 1].max(), sweep_extreme) * (1 + sweep_buffer_frac)
            risk = stop - entry
            reward = entry - target
        if risk <= 0 or reward <= 0 or reward / risk < min_rr:
            i += 1
            continue

        exit_idx, exit_price, code = simulate_exits_arrays(
            h, l, c, ts, entry_idx, int(ts[entry_idx]), direction, float(stop), float(target))
        exit_idx = int(exit_idx)
        outcome = "win" if code == 1 else "loss" if code == -1 else "open"
        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(ts[entry_idx])),
            "direction": direction,
            "entry_price": round(float(entry), 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(target), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "reason": f"Fade {'premium' if direction=='short' else 'discount'} "
                      f"({stretched:+.1f} ATR) + sweep/reclaim -> revert to 4H EMA",
        })
        i = exit_idx + 1 if exit_idx > i else i + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 92: MTF Mean-Reversion")
    parser.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--stretch", type=float, default=1.5)
    parser.add_argument("--htf-ema", type=int, default=50)
    args = parser.parse_args()
    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    out = args.output or f"strategy_92_results_{meta['symbol']}.json"
    run_strategy(candles, out, stretch=args.stretch, htf_ema=args.htf_ema)


if __name__ == "__main__":
    main()
