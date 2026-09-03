#!/usr/bin/env python3
"""
Faithful replay of Strategy 09 EXACTLY as the live auto_trader executes it.

Differences from the naive forex_backtester (which I do NOT replicate):
  - TP = 1.5R (compute_tp rr=1.5)            [live], not 2.0R
  - SL = compute_smart_sl: OB extreme -> worst 5M extreme between OB and tap
         -> + 15 pip buffer (0.01 for JPY)   [verbatim from auto_trader]
  - Bias TTL = 16h: a setup must produce MSS + OB + 5M tap within 16h of the
    4H sweep, else the bias expires (live --bias-ttl-hours default).
  - Live entry filters: STRICT 4H EMA (close>EMA21>EMA50 / mirror) at tap time,
    and London-Open block (07:00-08:00 UTC).
  - Entry/tap + trade simulation on REAL 5M data (whatever range each pair has).
  - Tap acted on at the tap bar (live acts within the 15-min fresh window).

Execution model (entry/SL/TP/sim) is left UNCHANGED from the strategy. We only
add a per-pair trading-cost column for context; the primary numbers are GROSS,
exactly as the strategy trades.

Step-by-step (not "casual"): biases are processed in time order; each phase
(MSS -> OB -> tap -> filters) uses only data available up to that bar.
"""
import glob
import logging
import os
import sys

logging.disable(logging.CRITICAL)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "strategy_09_mss_ob_entry"))

import numpy as np
import pandas as pd

from core import load_csv, enrich_trades_pnl
from bt_metrics import compute_metrics
from scripts.utils.indicators import detect_liquidity_levels, detect_liquidity_sweep, detect_mss, Direction
from strategy import MSSOrderBlockStrategy, DailyBias, BiasType, MSSConfirmation

STRAT = MSSOrderBlockStrategy()
BIAS_TTL_HOURS = 16.0
FOREX_SL_BUFFER_PIPS = 15


# ---- verbatim copies of the live auto_trader decision functions -------------
def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def _is_jpy_pair(symbol):
    return "JPY" in symbol.upper()


def _check_strict_ema(df_4h, direction):
    if df_4h is None or df_4h.empty or len(df_4h) < 50:
        return False
    close = df_4h["close"]
    ema21 = calculate_ema(close, 21); ema50 = calculate_ema(close, 50)
    c = float(close.iloc[-1]); e21 = float(ema21.iloc[-1]); e50 = float(ema50.iloc[-1])
    if direction.lower() in ("bullish", "long"):
        return c > e21 > e50
    return c < e21 < e50


def _is_london_open_block(now_utc):
    t = now_utc.hour * 60 + now_utc.minute
    return 7 * 60 <= t <= 8 * 60


def compute_smart_sl(ob, bias_direction, df_5m, tap_time, symbol):
    if bias_direction == BiasType.BEARISH:
        base_sl = ob.top
    else:
        base_sl = ob.bottom
    ob_time = ob.datetime
    seg = df_5m.loc[(df_5m.index >= ob_time) & (df_5m.index <= tap_time)]
    if len(seg) > 0:
        if bias_direction == BiasType.BEARISH:
            sh = seg["high"].max()
            if sh > base_sl:
                base_sl = sh
        else:
            sl_ = seg["low"].min()
            if sl_ < base_sl:
                base_sl = sl_
    buffer = (FOREX_SL_BUFFER_PIPS * 0.01) if _is_jpy_pair(symbol) else (FOREX_SL_BUFFER_PIPS * 0.0001)
    return base_sl + buffer if bias_direction == BiasType.BEARISH else base_sl - buffer


def compute_tp(entry_price, sl_price, rr=1.5):
    risk = abs(entry_price - sl_price)
    return entry_price + rr * risk if entry_price > sl_price else entry_price - rr * risk


# ---- data helpers ----------------------------------------------------------
def df_from_csv(path):
    candles = load_csv(path)
    if not candles:
        return None
    idx = pd.to_datetime([c.timestamp for c in candles], unit="s", utc=True)
    return pd.DataFrame({"open": [c.open for c in candles], "high": [c.high for c in candles],
                         "low": [c.low for c in candles], "close": [c.close for c in candles],
                         "volume": [c.volume for c in candles]}, index=idx)


def resample_df(df, rule):
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def biases_all(df_4h):
    out = []
    for level in detect_liquidity_levels(df_4h, lookback=3, lookforward=1):
        sweep = detect_liquidity_sweep(df_4h, level, start_index=level.index + 1)
        if sweep is None:
            continue
        sweep_idx, opp = sweep
        if sweep_idx >= len(df_4h):
            continue
        direction = BiasType.BEARISH if level.level_type == "high" else BiasType.BULLISH
        out.append(DailyBias(direction=direction, sweep_level=level, sweep_index=sweep_idx,
                             sweep_timestamp=df_4h.index[sweep_idx], confidence=70 if opp else 50,
                             reason="", source_timestamp=level.datetime,
                             event_timestamp=df_4h.index[sweep_idx]))
    return out


def first_mss_within(mss_list, bias, ttl_end):
    target = Direction.BULLISH if bias.direction == BiasType.BULLISH else Direction.BEARISH
    for m in mss_list:
        if m.direction == target and bias.sweep_timestamp < m.datetime <= ttl_end:
            return MSSConfirmation(mss=m, direction=bias.direction, timestamp=m.datetime, details="")
    return None


def simulate(df5, entry_time, direction, entry, sl, tp, max_bars=8640):  # ~30 days of 5m
    lo = df5["low"].values; hi = df5["high"].values; cl = df5["close"].values
    start = df5.index.searchsorted(entry_time)
    is_long = direction == "long"
    for j in range(start, min(start + max_bars, len(df5))):
        if is_long:
            if lo[j] <= sl: return df5.index[j], sl, "loss"
            if hi[j] >= tp: return df5.index[j], tp, "win"
        else:
            if hi[j] >= sl: return df5.index[j], sl, "loss"
            if lo[j] <= tp: return df5.index[j], tp, "win"
    j = min(start + max_bars, len(df5)) - 1
    return (df5.index[j], cl[j], "open") if j >= start else (None, None, None)


def backtest(sym, df_1h, df_15m, df_5m, use_filters):
    df_4h = resample_df(df_1h, "4h")
    if len(df_4h) < 55 or len(df_5m) < 50:
        return []
    mss_list = detect_mss(df_1h, lookback=5, require_body_close=True)
    trades = []
    open_until = None
    for bias in biases_all(df_4h):
        ttl_end = bias.sweep_timestamp + pd.Timedelta(hours=BIAS_TTL_HOURS)
        mss = first_mss_within(mss_list, bias, ttl_end)
        if mss is None:
            continue
        # tap must occur after MSS and within TTL
        ms = df_5m.index.searchsorted(mss.timestamp)
        me = df_5m.index.searchsorted(ttl_end)
        df5_win = df_5m.iloc[ms:me]
        if len(df5_win) < 3:
            continue
        ob = STRAT.find_ob_entry(df_15m, df5_win, bias, mss)
        if ob is None:
            continue
        tap_time = ob.timestamp
        if open_until is not None and tap_time <= open_until:
            continue  # one position per pair at a time
        direction = "long" if bias.direction == BiasType.BULLISH else "short"

        if use_filters:
            # Only 4H bars that have CLOSED by tap time (open + 4h <= tap) — no peeking
            # at the in-progress 4H candle (live drops incomplete bars).
            closed_4h_cutoff = tap_time - pd.Timedelta(hours=4)
            i4 = df_4h.index.searchsorted(closed_4h_cutoff, side="right")
            if not _check_strict_ema(df_4h.iloc[:i4], direction):
                continue
            if _is_london_open_block(tap_time.to_pydatetime()):
                continue

        sl = compute_smart_sl(ob.order_block, bias.direction, df_5m, tap_time, sym)
        entry = ob.entry_price
        if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
            continue
        tp = compute_tp(entry, sl, 1.5)
        et, ep, oc = simulate(df_5m, tap_time, direction, entry, sl, tp)
        if et is None:
            continue
        open_until = et
        trades.append({"trade_number": len(trades) + 1, "entry_time": tap_time.isoformat(),
                       "direction": direction, "entry_price": round(float(entry), 6),
                       "stop_loss": round(float(sl), 6), "take_profit": round(float(tp), 6),
                       "exit_time": et.isoformat(), "exit_price": round(float(ep), 6), "outcome": oc})
    return trades


def metrics_gross(trades):
    # gross: pnl in R from entry/sl/tp, no cost
    closed = [t for t in trades if t["outcome"] in ("win", "loss")]
    if not closed:
        return dict(trd=0, win=0.0, totR=0.0, PF=0.0, expR=0.0)
    R = []
    for t in closed:
        risk = abs(t["entry_price"] - t["stop_loss"])
        if risk <= 0: continue
        if t["direction"] == "long":
            r = (t["exit_price"] - t["entry_price"]) / risk
        else:
            r = (t["entry_price"] - t["exit_price"]) / risk
        R.append(r)
    wins = [r for r in R if r > 0]
    gp = sum(wins); gl = abs(sum(r for r in R if r < 0))
    return dict(trd=len(R), win=round(len(wins) / len(R) * 100, 1), totR=round(sum(R), 1),
                PF=round(gp / gl, 2) if gl else None, expR=round(sum(R) / len(R), 3))


def main():
    pairs = sorted(glob.glob("data/*/5m/*_5m.csv"))
    print(f"{'pair':>8} {'5m bars':>8} | {'AS-STRATEGY (filters on)':^40} | {'NET(cost)':>8} | {'NO-FILTER raw':^26}")
    print(f"{'':>8} {'':>8} | {'trd':>4} {'win%':>5} {'expR':>7} {'totR':>7} {'PF':>5} | {'totR':>8} | {'trd':>5} {'win%':>5} {'totR':>7}")
    agg = {"f_trd": 0, "f_pos": 0, "f_tot": 0.0, "r_tot": 0.0, "n": 0}
    for p in pairs:
        sym = p.split("/")[1]
        f1 = f"data/{sym}/1h/{sym}_1h.csv"; f15 = f"data/{sym}/15m/{sym}_15m.csv"
        if not (os.path.exists(f1) and os.path.exists(f15)):
            continue
        df1 = df_from_csv(f1); df15 = df_from_csv(f15); df5 = df_from_csv(p)
        if df1 is None or df15 is None or df5 is None:
            continue
        flt = backtest(sym, df1, df15, df5, use_filters=True)
        raw = backtest(sym, df1, df15, df5, use_filters=False)
        mf = metrics_gross(flt); mr = metrics_gross(raw)
        # net (cost) on filtered
        fcopy = [dict(t) for t in flt]; enrich_trades_pnl(fcopy)
        net = compute_metrics(fcopy)["total_R"]
        agg["f_trd"] += mf["trd"]; agg["f_tot"] += mf["totR"]; agg["r_tot"] += mr["totR"]; agg["n"] += 1
        if mf["totR"] > 0: agg["f_pos"] += 1
        print(f"{sym:>8} {len(df5):>8} | {mf['trd']:>4} {mf['win']:>5} {mf['expR']:>7} {mf['totR']:>7} "
              f"{str(mf['PF']):>5} | {net:>8} | {mr['trd']:>5} {mr['win']:>5} {mr['totR']:>7}", flush=True)
    print(f"\nAS-STRATEGY (filters on): net-positive {agg['f_pos']}/{agg['n']} pairs | "
          f"summed {agg['f_tot']:.1f}R over {agg['f_trd']} trades")
    print(f"NO-FILTER raw summed: {agg['r_tot']:.1f}R")


if __name__ == "__main__":
    main()
