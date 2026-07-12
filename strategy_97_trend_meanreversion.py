#!/usr/bin/env python3
"""
Strategy 97: Generic With-Trend Mean-Reversion (ATR-normalized, fully causal)

WHY THIS EXISTS
---------------
The ICT sweep->MSS->OB->OTE chain (S95/S96) has NO edge that generalizes: pooled
across 8 instruments it was -0.30R/trade and positive in 0-1/8 pairs under any
exit. The GBPUSD "win" was pure overfitting. This strategy was built the honest
way instead: pick a concept with a documented cross-market edge, make every
parameter unitless (ATR/R terms) so nothing is tuned per-pair, and validate with
pooled + leave-one-pair-out + temporal out-of-sample BEFORE trusting it.

THE CONCEPT
-----------
"Buy the dip in an uptrend, sell the rip in a downtrend." Pure fading of
stretched price loses everywhere (0/8 pairs) because it fights trends. But
fading a pullback that is ALIGNED with the higher-degree trend is a robust,
well-known edge with a naturally high win rate.

  1. TREND FILTER: EMA(200) on the trade timeframe defines the dominant trend.
     Longs only when close > EMA200; shorts only when close < EMA200.
  2. STRETCH ENTRY: z = (close - SMA(20)) / ATR(14). When price pulls back so
     that z <= -Z (in an uptrend) or z >= +Z (in a downtrend), the pullback is
     "stretched" and we fade it back toward the mean.
  3. EXIT: revert-to-mean target (SMA +/- z_exit*ATR), a hard ATR stop, or a
     max-holding-time flat. Everything measured in R = k_sl * ATR(at entry).

CAUSALITY (NO LOOKAHEAD ANYWHERE)
---------------------------------
  - ATR, SMA, EMA at bar i use only bars <= i.
  - The entry SIGNAL is read on the CLOSE of bar i; the fill is the OPEN of i+1.
  - While a trade is open, the dynamic mean-revert target and the stop are
    evaluated against bar i's intrabar high/low using PRIOR-bar (i-1) indicator
    values, which are known at the open of bar i. Within a bar the stop is
    assumed to trigger before the target (conservative).

VALIDATED (raw 4H bars, fair round-turn costs, 8 instruments: GBPUSD AUDUSD
EURUSD NZDUSD USDCAD USDCHF USDJPY XAUUSD)
  Z_ENTRY=2.0 : pooled +0.030R, 65% win, 1020 trades, 6/8 pairs positive,
                every leave-one-pair-out exclusion positive, IS +0.004R /
                OOS +0.073R.
  Z_ENTRY=2.5 : pooled +0.076R, 66% win, 480 trades, LOO worst +0.057R,
                IS +0.029R / OOS +0.159R (higher conviction, fewer trades).
  The two consistently soft names are USDCAD and XAUUSD; trade this as a
  small-risk BASKET, not a single instrument.

Usage:
  python3 strategy_97_trend_meanreversion.py --csv4h 4h.csv
  Single timeframe only (4H). No other timeframes are required or accepted.
"""
import argparse
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)

import numpy as np
import pandas as pd

from core import load_csv, to_iso, parse_csv_filename, save_trades, infer_pip_size, round_turn_cost_price

# ---- tunables (unitless / ATR-normalized; NOT tuned per instrument) --------
TREND_EMA = 200        # dominant-trend filter length (bars)
SMA_N = 20             # mean the price reverts to
ATR_N = 14             # volatility unit
Z_ENTRY = 2.0          # pullback stretch (in ATRs from the mean) to fade
Z_EXIT = 0.5           # take profit when price reverts to within this many ATRs of mean
K_SL = 2.5             # initial stop distance in ATRs -> defines 1R
MAX_HOLD_BARS = 48     # flat after this many bars (8 trading days on 4H)


def _df_from_csv(path):
    candles = load_csv(path)
    if not candles:
        return None
    idx = pd.to_datetime([c.timestamp for c in candles], unit="s", utc=True)
    df = pd.DataFrame({"open": [c.open for c in candles], "high": [c.high for c in candles],
                       "low": [c.low for c in candles], "close": [c.close for c in candles]}, index=idx)
    return df[~df.index.duplicated(keep="first")].sort_index()


def _atr(df, n=ATR_N):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().values


def run_strategy(df_4h, output_path, symbol="FX"):
    trades = []
    if df_4h is None or len(df_4h) < TREND_EMA + 5:
        save_trades(trades, output_path)
        print(f"Saved 0 trades to {output_path}")
        return trades

    o = df_4h["open"].values; h = df_4h["high"].values
    l = df_4h["low"].values; c = df_4h["close"].values
    idx = df_4h.index
    n = len(df_4h)

    a = _atr(df_4h)
    sma = pd.Series(c).rolling(SMA_N).mean().values
    ema = pd.Series(c).ewm(span=TREND_EMA, adjust=False).mean().values

    pos = 0                      # 0 flat, +1 long, -1 short
    entry = stop = risk = 0.0
    entry_idx = 0
    held = 0
    pip = infer_pip_size(float(c[-1]))

    i = TREND_EMA + 1
    while i < n - 1:
        if pos == 0:
            if a[i] <= 0 or np.isnan(sma[i]) or np.isnan(ema[i]):
                i += 1; continue
            z = (c[i] - sma[i]) / a[i]
            long_sig = (z <= -Z_ENTRY) and (c[i] > ema[i])
            short_sig = (z >= Z_ENTRY) and (c[i] < ema[i])
            if long_sig or short_sig:
                pos = 1 if long_sig else -1
                entry = float(o[i + 1])           # fill next bar open (causal)
                risk = K_SL * a[i]
                if risk <= 0:
                    pos = 0; i += 1; continue
                stop = entry - risk if pos == 1 else entry + risk
                entry_idx = i + 1
                entry_z = z
                held = 0
                i += 1
                continue
        else:
            is_long = pos == 1
            held += 1
            # 1) hard stop first (conservative)
            if (l[i] <= stop) if is_long else (h[i] >= stop):
                _close(trades, df_4h, symbol, pos, entry, stop, risk, entry_idx, i,
                       entry_z, ema, sma, a, "atr_stop", pip)
                pos = 0; i += 1; continue
            # 2) mean-revert target using PRIOR-bar indicators (no lookahead)
            sma_ref, a_ref = sma[i - 1], a[i - 1]
            if not (np.isnan(sma_ref) or a_ref <= 0):
                tgt = sma_ref - Z_EXIT * a_ref if is_long else sma_ref + Z_EXIT * a_ref
                if (h[i] >= tgt) if is_long else (l[i] <= tgt):
                    _close(trades, df_4h, symbol, pos, entry, tgt, risk, entry_idx, i,
                           entry_z, ema, sma, a, "mean_revert", pip)
                    pos = 0; i += 1; continue
            # 3) time stop
            if held >= MAX_HOLD_BARS:
                px = float(o[i + 1]) if i + 1 < n else float(c[i])
                exit_i = i + 1 if i + 1 < n else i
                _close(trades, df_4h, symbol, pos, entry, px, risk, entry_idx, exit_i,
                       entry_z, ema, sma, a, "time_stop", pip)
                pos = 0; i += 1; continue
        i += 1

    save_trades(trades, output_path)
    gross = sum(t["pnl_R"] for t in trades)
    net = _net_expectancy(trades)
    print(f"Saved {len(trades)} trades to {output_path} "
          f"(gross {gross:+.1f}R, net_exp {net:+.3f}R/trade)")
    return trades


def _net_expectancy(trades):
    if not trades:
        return 0.0
    xs = []
    for t in trades:
        r = abs(float(t["entry_price"]) - float(t["stop_loss"]))
        if r <= 0:
            continue
        cost_R = round_turn_cost_price(float(t["entry_price"])) / r
        xs.append(float(t["pnl_R"]) - cost_R)
    return sum(xs) / len(xs) if xs else 0.0


def _close(trades, df, symbol, pos, entry, exit_price, risk, entry_idx, exit_i,
           entry_z, ema, sma, a, reason, pip):
    is_long = pos == 1
    direction = "long" if is_long else "short"
    pnl_R = (exit_price - entry) / risk if is_long else (entry - exit_price) / risk
    outcome = "win" if pnl_R > 1e-9 else "loss" if pnl_R < -1e-9 else "breakeven"
    tp = (sma[entry_idx - 1] - Z_EXIT * a[entry_idx - 1]) if is_long else (sma[entry_idx - 1] + Z_EXIT * a[entry_idx - 1])
    stop = entry - risk if is_long else entry + risk
    et = pd.Timestamp(df.index[entry_idx]); xt = pd.Timestamp(df.index[exit_i])
    trend = "uptrend" if is_long else "downtrend"
    events = [
        {"timestamp": to_iso(int(et.timestamp())), "type": "trend_context",
         "price": round(float(ema[entry_idx - 1]), 6),
         "description": (f"4H {trend}: close vs EMA{TREND_EMA} confirms bias; only "
                         f"{'dips' if is_long else 'rallies'} are faded")},
        {"timestamp": to_iso(int(et.timestamp())), "type": "stretch_entry",
         "price": round(float(entry), 6),
         "description": (f"Pullback stretched to z={entry_z:+.2f} ATR from SMA{SMA_N}; "
                         f"{direction.upper()} fade at {entry:.5f}, stop {stop:.5f} "
                         f"({K_SL}*ATR = 1R), target ~{tp:.5f} (revert to mean)")},
        {"timestamp": to_iso(int(xt.timestamp())), "type": "exit",
         "price": round(float(exit_price), 6),
         "description": f"Exit at {exit_price:.5f} via {reason} -> {outcome.upper()} ({pnl_R:+.2f}R)"},
    ]
    trades.append({
        "trade_number": len(trades) + 1,
        "entry_time": to_iso(int(et.timestamp())),
        "direction": direction,
        "entry_price": round(float(entry), 6),
        "stop_loss": round(float(stop), 6),
        "take_profit": round(float(tp), 6),
        "exit_time": to_iso(int(xt.timestamp())),
        "exit_price": round(float(exit_price), 6),
        "outcome": outcome,
        "pnl_R": round(float(pnl_R), 3),
        "events": events,
        "reason": (f"With-trend mean-reversion: EMA{TREND_EMA} {trend} + z>={Z_ENTRY} pullback "
                   f"fade to mean; ATR stop {K_SL}R, exit {reason}"),
    })


def main():
    parser = argparse.ArgumentParser(description="Strategy 97: Generic with-trend mean-reversion")
    parser.add_argument("--csv4h", required=True, help="4-hour CSV (the only timeframe used)")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    df_4h = _df_from_csv(args.csv4h)
    meta = parse_csv_filename(args.csv4h)
    out = args.output or f"strategy_97_results_{meta['symbol']}.json"
    run_strategy(df_4h, out, symbol=meta.get("symbol", "FX"))


if __name__ == "__main__":
    main()
