#!/usr/bin/env python3
"""
Honest, causal, cost-aware backtest of Strategy 09 (MSS + Order Block) across
all forex pairs in the data library.

Why not use their forex_backtester.py?
  - It fetches only a few hundred recent bars live from TradingView (tiny sample).
  - It applies NO trading costs.
  - It runs the RAW strategy WITHOUT the entry_filters that the live auto_trader uses.

This harness:
  - Uses the Drive CSVs (years of history) per pair.
  - Rebuilds biases over full history CAUSALLY: for each 4H liquidity sweep, take
    the FIRST 1H MSS after it (not the "most recent overall", which would peek
    into the future on historical data), then their real find_ob_entry()/OB/tap.
  - Execution/entry-tap + trade simulation on 15M (Drive 5M is only ~8 months for
    most pairs). Conservative intrabar fills (stop checked before target).
  - Trading costs + R-metrics applied via core.enrich_trades_pnl / bt_metrics.
  - Runs TWO variants: RAW (no filters) and FILTERED (live entry_filters, applied
    causally on frames sliced to the decision bar).
"""
import glob
import os
import sys
import logging
from datetime import datetime

logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_09_mss_ob_entry"))

import numpy as np
import pandas as pd
import pytz

from core import load_csv, enrich_trades_pnl
from bt_metrics import compute_metrics
from scripts.utils.indicators import (
    detect_liquidity_levels, detect_liquidity_sweep, detect_mss, Direction,
)
from strategy import MSSOrderBlockStrategy, DailyBias, BiasType, MSSConfirmation
from entry_filters import apply_entry_filters

IST = pytz.timezone("Asia/Kolkata")
STRAT = MSSOrderBlockStrategy()


def df_from_csv(path):
    candles = load_csv(path)
    if not candles:
        return None
    idx = pd.to_datetime([c.timestamp for c in candles], unit="s", utc=True)
    return pd.DataFrame({
        "open": [c.open for c in candles], "high": [c.high for c in candles],
        "low": [c.low for c in candles], "close": [c.close for c in candles],
        "volume": [c.volume for c in candles],
    }, index=idx)


def resample_df(df, rule):
    o = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return o.dropna()


def biases_full_history(df_4h):
    levels = detect_liquidity_levels(df_4h, lookback=3, lookforward=1)
    out = []
    for level in levels:
        sweep = detect_liquidity_sweep(df_4h, level, start_index=level.index + 1)
        if sweep is None:
            continue
        sweep_idx, opp = sweep
        if sweep_idx >= len(df_4h):
            continue
        direction = BiasType.BEARISH if level.level_type == "high" else BiasType.BULLISH
        out.append(DailyBias(
            direction=direction, sweep_level=level, sweep_index=sweep_idx,
            sweep_timestamp=df_4h.index[sweep_idx], confidence=70 if opp else 50,
            reason="", source_timestamp=level.datetime, event_timestamp=df_4h.index[sweep_idx]))
    return out


def first_mss_after(mss_list, bias):
    target = Direction.BULLISH if bias.direction == BiasType.BULLISH else Direction.BEARISH
    for mss in mss_list:                      # ascending time
        if mss.direction == target and mss.datetime > bias.sweep_timestamp:
            return MSSConfirmation(mss=mss, direction=bias.direction, timestamp=mss.datetime, details="")
    return None


def simulate(df_exec, entry_time, direction, entry, sl, tp, max_bars=1000):
    start = df_exec.index.searchsorted(entry_time)
    if start >= len(df_exec):
        return None
    lo = df_exec["low"].values; hi = df_exec["high"].values; cl = df_exec["close"].values
    is_long = direction == "long"
    for j in range(start, min(start + max_bars, len(df_exec))):
        if is_long:
            if lo[j] <= sl: return df_exec.index[j], sl, "loss"
            if hi[j] >= tp: return df_exec.index[j], tp, "win"
        else:
            if hi[j] >= sl: return df_exec.index[j], sl, "loss"
            if lo[j] <= tp: return df_exec.index[j], tp, "win"
    j = min(start + max_bars, len(df_exec)) - 1
    return df_exec.index[j], cl[j], "open"


def backtest_pair(sym, df_1h, df_15m, df_exec, use_filters, start="2021-06-01", tap_bars=480):
    df_1h = df_1h[df_1h.index >= start]
    df_15m = df_15m[df_15m.index >= start]
    df_exec = df_exec[df_exec.index >= start]
    df_4h = resample_df(df_1h, "4h")
    if len(df_4h) < 30 or len(df_1h) < 60 or len(df_15m) < 30:
        return []
    mss_list = detect_mss(df_1h, lookback=5, require_body_close=True)
    biases = biases_full_history(df_4h)
    trades = []
    used_keys = set()
    last_entry_time = None

    for bias in biases:
        key = (bias.source_timestamp, bias.direction.value)
        if key in used_keys:
            continue
        mss_conf = first_mss_after(mss_list, bias)
        if mss_conf is None:
            continue
        # Bound the 5M/exec tap search to a few days after the MSS (realistic and fast).
        ex_start = df_exec.index.searchsorted(mss_conf.timestamp)
        df_exec_slice = df_exec.iloc[ex_start:ex_start + tap_bars]
        if len(df_exec_slice) < 3:
            continue
        ob = STRAT.find_ob_entry(df_15m, df_exec_slice, bias, mss_conf)
        if ob is None:
            continue
        used_keys.add(key)
        entry_time = ob.timestamp
        if last_entry_time is not None and abs((entry_time - last_entry_time).total_seconds()) < 4 * 3600:
            continue

        direction = "long" if bias.direction == BiasType.BULLISH else "short"

        if use_filters:
            i4 = df_4h.index.searchsorted(mss_conf.timestamp) + 1
            i1 = df_1h.index.searchsorted(mss_conf.timestamp) + 1
            i15 = df_15m.index.searchsorted(entry_time) + 1
            verdict = apply_entry_filters(
                signal_direction=direction,
                df_4h=df_4h.iloc[:i4], df_1h=df_1h.iloc[:i1],
                df_15m=df_15m.iloc[:i15], df_5m=df_exec_slice,
                entry_price=ob.entry_price, mss_timestamp=mss_conf.timestamp,
                now_ist=entry_time.astimezone(IST),
                require_structure=False, check_fvg=False, debug=False,
            )
            if not verdict.passed:
                continue

        res = simulate(df_exec, entry_time, direction, ob.entry_price, ob.stop_loss, ob.tp_price)
        if res is None:
            continue
        exit_time, exit_price, outcome = res
        last_entry_time = entry_time
        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": entry_time.isoformat(),
            "direction": direction,
            "entry_price": round(float(ob.entry_price), 6),
            "stop_loss": round(float(ob.stop_loss), 6),
            "take_profit": round(float(ob.tp_price), 6),
            "exit_time": exit_time.isoformat(),
            "exit_price": round(float(exit_price), 6),
            "outcome": outcome,
        })
    return trades


def main():
    pairs = sorted(glob.glob("data/*/1h/*_1h.csv"))
    rows = []
    print(f"{'pair':>8} | {'RAW (no filters)':^32} | {'FILTERED (live)':^32}")
    print(f"{'':>8} | {'trd':>4} {'win%':>5} {'expR':>7} {'totR':>7} {'PF':>5} | "
          f"{'trd':>4} {'win%':>5} {'expR':>7} {'totR':>7} {'PF':>5}")
    for p in pairs:
        sym = p.split("/")[1]
        f15 = f"data/{sym}/15m/{sym}_15m.csv"
        if not os.path.exists(f15):
            continue
        df_1h = df_from_csv(p)
        df_15m = df_from_csv(f15)
        if df_1h is None or df_15m is None:
            continue
        df_exec = df_15m  # execution modeled on 15m
        raw = backtest_pair(sym, df_1h, df_15m, df_exec, use_filters=False)
        flt = backtest_pair(sym, df_1h, df_15m, df_exec, use_filters=True)
        enrich_trades_pnl(raw); enrich_trades_pnl(flt)
        mr = compute_metrics(raw); mf = compute_metrics(flt)
        rows.append((sym, mr, mf))
        print(f"{sym:>8} | {mr['closed_trades']:>4} {mr['win_rate']:>5} {mr['expectancy_R']:>7} "
              f"{mr['total_R']:>7} {str(mr['profit_factor']):>5} | "
              f"{mf['closed_trades']:>4} {mf['win_rate']:>5} {mf['expectancy_R']:>7} "
              f"{mf['total_R']:>7} {str(mf['profit_factor']):>5}", flush=True)

    def agg(idx):
        tot = sum((r[idx]['total_R'] or 0) for r in rows)
        pos = sum(1 for r in rows if (r[idx]['total_R'] or 0) > 0)
        n = sum(r[idx]['closed_trades'] for r in rows)
        e = sum((r[idx]['expectancy_R'] or 0) * r[idx]['closed_trades'] for r in rows)
        return tot, pos, n, (e / max(1, n))
    tr, pr, nr, er = agg(1); tf, pf, nf, ef = agg(2)
    print(f"\nRAW      : net-positive {pr}/{len(rows)} pairs | summed {tr:.1f}R | pooled expectancy {er:.4f} R/trade ({nr} trades)")
    print(f"FILTERED : net-positive {pf}/{len(rows)} pairs | summed {tf:.1f}R | pooled expectancy {ef:.4f} R/trade ({nf} trades)")


if __name__ == "__main__":
    main()
