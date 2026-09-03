#!/usr/bin/env python3
"""
Strategy 90: Trend-Filtered Donchian Breakout with ATR Chandelier Trail (NEW)

Rationale (built from the failure analysis of strategies 01-81):
  - Every existing strategy is a counter-trend "sweep + reversal" with a fixed 2R
    target. After realistic costs + conservative fills they cluster at ~30-40%
    win rate, which is below the break-even win rate for 2R, so they bleed.
  - Two structural fixes give a system a fighting chance:
      1. Trade WITH the higher-timeframe trend (positive skew), not against it.
      2. Let winners run with a trailing stop instead of capping at 2R, and trade
         a higher timeframe so spread/commission is a tiny fraction of each move
         (cost drag is what kills the scalpers).

Mechanics (fully causal — decisions only on closed bars):
  - Trend filter: EMA(trend_len) on the SAME timeframe.
  - Entry (long): bar CLOSES above the highest high of the prior `donchian` bars
    AND close > EMA. Symmetric for shorts.
  - Initial stop: entry -/+ atr_mult_init * ATR(atr_len).
  - Trail: chandelier stop = highestHigh_since_entry - atr_mult_trail*ATR (long).
    Stop only ratchets in the trade's favour. Exit when a later bar's low/high
    touches the trailing stop (conservative intrabar).
  - One position at a time. Entry filled at NEXT bar's open (no same-bar fill).

Usage:
  python strategy_90_trend_breakout_atr.py --csv XAUUSD_4h.csv [--long-only] [--output out.json]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import load_csv, to_iso, parse_csv_filename, save_trades


def ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.empty_like(values)
    k = 2.0 / (length + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def atr(high, low, close, length: int) -> np.ndarray:
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    out = np.empty(n)
    out[0] = tr[0]
    k = 1.0 / length
    for i in range(1, n):
        out[i] = tr[i] * k + out[i - 1] * (1 - k)
    return out


def generate_trades(candles, *, donchian=20, trend_len=50, atr_len=14,
                    atr_mult_init=2.0, atr_mult_trail=3.0, long_only=False):
    """Return the list of trades (no file IO). Fully causal."""
    n = len(candles)
    if n < max(donchian, trend_len, atr_len) + 5:
        return []

    o = np.array([c.open for c in candles])
    h = np.array([c.high for c in candles])
    l = np.array([c.low for c in candles])
    cl = np.array([c.close for c in candles])

    ema_arr = ema(cl, trend_len)
    atr_arr = atr(h, l, cl, atr_len)

    trades = []
    i = max(donchian, trend_len, atr_len)
    while i < n - 1:
        # Donchian channel from the prior `donchian` CLOSED bars (exclude bar i).
        prior_hh = h[i - donchian:i].max()
        prior_ll = l[i - donchian:i].min()

        go_long = cl[i] > prior_hh and cl[i] > ema_arr[i]
        go_short = (not long_only) and cl[i] < prior_ll and cl[i] < ema_arr[i]

        if not (go_long or go_short):
            i += 1
            continue

        direction = "long" if go_long else "short"
        entry_idx = i + 1                      # fill at next bar open (causal)
        if entry_idx >= n:
            break
        entry_price = o[entry_idx]
        a = atr_arr[i]
        if a <= 0:
            i += 1
            continue

        init_stop = entry_price - atr_mult_init * a if direction == "long" else entry_price + atr_mult_init * a
        stop = init_stop
        extreme = h[entry_idx] if direction == "long" else l[entry_idx]

        exit_idx = None
        exit_price = None
        for j in range(entry_idx, n):
            # Conservative: check stop against this bar first.
            if direction == "long":
                if l[j] <= stop:
                    exit_idx, exit_price = j, stop
                    break
                extreme = max(extreme, h[j])
                stop = max(stop, extreme - atr_mult_trail * atr_arr[j])
            else:
                if h[j] >= stop:
                    exit_idx, exit_price = j, stop
                    break
                extreme = min(extreme, l[j])
                stop = min(stop, extreme + atr_mult_trail * atr_arr[j])

        if exit_idx is None:
            exit_idx = n - 1
            exit_price = cl[exit_idx]
            outcome = "open"
        else:
            outcome = "win" if (
                (direction == "long" and exit_price > entry_price)
                or (direction == "short" and exit_price < entry_price)
            ) else "loss"

        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(candles[entry_idx].timestamp),
            "direction": direction,
            "entry_price": round(float(entry_price), 5),
            "stop_loss": round(float(init_stop), 5),
            # No fixed target (trailing exit). For chart display the reward zone
            # equals the realized exit; the stop_loss field is the initial risk.
            "take_profit": round(float(exit_price), 5),
            "exit_time": to_iso(candles[exit_idx].timestamp),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "reason": f"Donchian{donchian} breakout w/ EMA{trend_len} trend + ATR{atr_len} chandelier x{atr_mult_trail}",
        })
        i = exit_idx + 1                       # no overlapping positions
    return trades


def run_strategy(candles, output_path, *, donchian=20, trend_len=50, atr_len=14,
                 atr_mult_init=2.0, atr_mult_trail=3.0, long_only=False):
    trades = generate_trades(
        candles, donchian=donchian, trend_len=trend_len, atr_len=atr_len,
        atr_mult_init=atr_mult_init, atr_mult_trail=atr_mult_trail, long_only=long_only,
    )
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 90: Trend Breakout + ATR trail")
    parser.add_argument("--csv", required=True, help="Daily OHLCV CSV (also works on 4h/1h)")
    parser.add_argument("--output", default=None)
    parser.add_argument("--long-only", action="store_true")
    parser.add_argument("--donchian", type=int, default=20)
    parser.add_argument("--trend-len", type=int, default=50)
    parser.add_argument("--atr-trail", type=float, default=3.0)
    args = parser.parse_args()
    candles = load_csv(args.csv)
    meta = parse_csv_filename(args.csv)
    out = args.output or f"strategy_90_results_{meta['symbol']}_{meta['timeframe']}.json"
    run_strategy(candles, out, donchian=args.donchian, trend_len=args.trend_len,
                 atr_mult_trail=args.atr_trail, long_only=args.long_only)


if __name__ == "__main__":
    main()
