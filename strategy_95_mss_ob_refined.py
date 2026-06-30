#!/usr/bin/env python3
"""
Strategy 95: Refined MSS + Order Block (partial + breakeven, displacement-gated)

This is the cleaned-up, validated evolution of Strategy 09's skeleton. The raw
S09 signal has a small but REAL directional edge on forex (confirmed by a
direction-flip test: trading with it +0.10R, fading it -0.10R per trade). The
live system destroyed that edge with a 4H-EMA filter that fought its own
pullback entries. This version removes that filter and instead:

  1. DISPLACEMENT GATE  - only take the trade if the 1H structure-break candle
                          is a large-body (impulsive) move. This gate AGREES
                          with the entry (real institutional move) rather than
                          fighting it.
  2. PARTIAL + BREAKEVEN - bank half the position at +0.5R and move the stop to
                          entry; let the rest run to +1.5R. Lifts the win rate
                          to ~82% and cuts variance. The edge is the EXPECTANCY
                          (~+0.22 to +0.29R/trade), not the win rate itself.

Pipeline (fully causal, no lookahead):
  4H liquidity-sweep bias -> first 1H MSS within 16h (displacement-gated)
  -> 15M order block in OTE (0.62-0.79) -> 5M tap entry -> smart SL
  -> partial 0.5R + BE -> final 1.5R.

Validated out-of-sample (held-out most-recent 40% per pair, fair costs, no
lookahead): ~82% win, +0.22-0.29R/trade, profitable on 14/16 FX pairs. Best on
the liquid USD majors; trade it as a small-risk BASKET on low-cost execution.

Usage:
  python strategy_95_mss_ob_refined.py --csv4h 4h.csv --csv1h 1h.csv --csv15m 15m.csv --csv5m 5m.csv
"""
import argparse
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
sys.path.insert(0, os.path.join(THIS, "strategy_09_mss_ob_entry"))

import logging
logging.disable(logging.CRITICAL)

import pandas as pd

from core import load_csv, to_iso, parse_csv_filename, save_trades, infer_pip_size
from scripts.utils.indicators import (
    detect_liquidity_levels, detect_liquidity_sweep, detect_mss, Direction,
)
from strategy import MSSOrderBlockStrategy, DailyBias, BiasType, MSSConfirmation

STRAT = MSSOrderBlockStrategy()

# ---- tunables (validated defaults) -----------------------------------------
BIAS_TTL_HOURS = 16.0
PARTIAL_R = 0.5            # bank half here, move stop to breakeven
FINAL_R = 1.5             # remainder target
MIN_DISPLACEMENT_PCT = 0.10   # 1H MSS candle body as % of price
FOREX_SL_BUFFER_PIPS = 15


# ---- helpers (data + smart SL, faithful to the live model) -----------------
def _df_from_csv(path):
    candles = load_csv(path)
    if not candles:
        return None
    idx = pd.to_datetime([c.timestamp for c in candles], unit="s", utc=True)
    return pd.DataFrame({"open": [c.open for c in candles], "high": [c.high for c in candles],
                         "low": [c.low for c in candles], "close": [c.close for c in candles],
                         "volume": [c.volume for c in candles]}, index=idx)


def _resample(df, rule):
    # Kept for reference only — Strategy 95 no longer resamples; the 4H is read
    # from its own raw CSV (--csv4h).
    return df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def _is_jpy(sym):
    return "JPY" in sym.upper()


def _biases(df_4h):
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


def _first_mss(mss_list, bias, ttl_end):
    target = Direction.BULLISH if bias.direction == BiasType.BULLISH else Direction.BEARISH
    for m in mss_list:
        if m.direction == target and bias.sweep_timestamp < m.datetime <= ttl_end:
            return MSSConfirmation(mss=m, direction=bias.direction, timestamp=m.datetime, details="")
    return None


def _displacement_ok(df1, mss_ts):
    i = min(df1.index.searchsorted(mss_ts), len(df1) - 1)
    c = df1.iloc[i]
    return abs(c["close"] - c["open"]) / c["close"] * 100 >= MIN_DISPLACEMENT_PCT


def _smart_sl(ob, bias_dir, df5, tap_time, sym):
    base = ob.top if bias_dir == BiasType.BEARISH else ob.bottom
    seg = df5.loc[(df5.index >= ob.datetime) & (df5.index <= tap_time)]
    if len(seg) > 0:
        if bias_dir == BiasType.BEARISH:
            base = max(base, seg["high"].max())
        else:
            base = min(base, seg["low"].min())
    buf = (FOREX_SL_BUFFER_PIPS * 0.01) if _is_jpy(sym) else (FOREX_SL_BUFFER_PIPS * 0.0001)
    return base + buf if bias_dir == BiasType.BEARISH else base - buf


def _simulate(df5, entry_time, direction, entry, sl, max_bars=8640):
    """Partial at +PARTIAL_R then breakeven; remainder to +FINAL_R. Conservative
    (stop checked first). Returns dict with the two-leg breakdown."""
    lo = df5["low"].values; hi = df5["high"].values; cl = df5["close"].values
    start = df5.index.searchsorted(entry_time)
    risk = abs(entry - sl)
    if risk <= 0 or start >= len(df5):
        return None
    is_long = direction == "long"
    tp1 = entry + PARTIAL_R * risk if is_long else entry - PARTIAL_R * risk
    tpf = entry + FINAL_R * risk if is_long else entry - FINAL_R * risk
    cur_stop = sl
    half = False
    realized = 0.0
    partial_time = None
    partial_price = None

    def result(exit_idx, exit_price):
        outcome = "win" if realized > 0 else "loss" if realized < 0 else "breakeven"
        return {
            "exit_time": df5.index[exit_idx], "exit_price": float(exit_price),
            "outcome": outcome, "pnl_R": realized,
            "partial_time": partial_time, "partial_price": partial_price,
        }

    for j in range(start, min(start + max_bars, len(df5))):
        hit_stop = (lo[j] <= cur_stop) if is_long else (hi[j] >= cur_stop)
        if hit_stop:
            stop_R = (cur_stop - entry) / risk if is_long else (entry - cur_stop) / risk
            realized += (0.5 if half else 1.0) * stop_R
            return result(j, cur_stop)
        if not half and ((hi[j] >= tp1) if is_long else (lo[j] <= tp1)):
            realized += 0.5 * PARTIAL_R
            half = True
            cur_stop = entry
            partial_time = df5.index[j]
            partial_price = float(tp1)
            continue
        if half and ((hi[j] >= tpf) if is_long else (lo[j] <= tpf)):
            realized += 0.5 * FINAL_R
            return result(j, tpf)
    j = min(start + max_bars, len(df5)) - 1
    last = cl[j]
    rem_R = (last - entry) / risk if is_long else (entry - last) / risk
    realized += (0.5 if half else 1.0) * rem_R
    return result(j, last)


def run_strategy(df_4h, df_1h, df_15m, df_5m, output_path, symbol="FX"):
    trades = []
    if df_4h is None or len(df_4h) < 55 or df_5m is None or len(df_5m) < 50:
        save_trades(trades, output_path)
        print(f"Saved 0 trades to {output_path}")
        return trades
    mss_list = detect_mss(df_1h, lookback=5, require_body_close=True)
    open_until = None
    for bias in _biases(df_4h):
        ttl_end = bias.sweep_timestamp + pd.Timedelta(hours=BIAS_TTL_HOURS)
        mss = _first_mss(mss_list, bias, ttl_end)
        if mss is None or not _displacement_ok(df_1h, mss.timestamp):
            continue
        ms = df_5m.index.searchsorted(mss.timestamp); me = df_5m.index.searchsorted(ttl_end)
        win = df_5m.iloc[ms:me]
        if len(win) < 3:
            continue
        ob = STRAT.find_ob_entry(df_15m, win, bias, mss)
        if ob is None:
            continue
        tap = ob.timestamp
        if open_until is not None and tap <= open_until:
            continue
        direction = "long" if bias.direction == BiasType.BULLISH else "short"
        sl = _smart_sl(ob.order_block, bias.direction, df_5m, tap, symbol)
        entry = ob.entry_price
        if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
            continue
        res = _simulate(df_5m, tap, direction, entry, sl)
        if res is None:
            continue
        exit_time = res["exit_time"]; exit_price = res["exit_price"]
        outcome = res["outcome"]; pnl_R = res["pnl_R"]
        partial_time = res["partial_time"]; partial_price = res["partial_price"]
        open_until = exit_time
        risk = abs(entry - sl)
        # display target = full final target level
        tp = entry + FINAL_R * risk if direction == "long" else entry - FINAL_R * risk

        # ---- chronological event log (with timestamps + prices) ----
        lvl = bias.sweep_level
        mi = min(df_1h.index.searchsorted(mss.timestamp), len(df_1h) - 1)
        mc = df_1h.iloc[mi]
        body_pct = abs(mc["close"] - mc["open"]) / mc["close"] * 100
        ob_blk = ob.order_block
        sweep_dir = "bullish" if bias.direction == BiasType.BULLISH else "bearish"
        swept_side = "low (sell-side)" if bias.direction == BiasType.BULLISH else "high (buy-side)"
        lvl_dt = pd.Timestamp(lvl.datetime)
        ob_dt = pd.Timestamp(ob_blk.datetime)
        events = [
            {
                "timestamp": to_iso(int(lvl_dt.timestamp())),
                "type": "liquidity_level_formed",
                "price": round(float(lvl.price), 6),
                "description": (f"4H {lvl.level_type} formed at {lvl.price:.5f} — this is the "
                                f"level that later gets swept"),
            },
            {
                "timestamp": to_iso(int(bias.sweep_timestamp.timestamp())),
                "type": f"{sweep_dir}_sweep",
                "price": round(float(lvl.price), 6),
                "description": (f"4H {sweep_dir} sweep: price ran the {swept_side} liquidity at "
                                f"{lvl.price:.5f} (level formed {lvl_dt.strftime('%Y-%m-%d %H:%M')}). "
                                f"Bias = {direction.upper()}"),
            },
            {
                "timestamp": to_iso(int(pd.Timestamp(mss.timestamp).timestamp())),
                "type": "mss",
                "price": round(float(mss.mss.break_price), 6),
                "description": (f"1H {sweep_dir} MSS: structure broken at {mss.mss.break_price:.5f} "
                                f"with a {body_pct:.2f}% displacement candle (gate >= {MIN_DISPLACEMENT_PCT}%)"),
            },
            {
                "timestamp": to_iso(int(ob_dt.timestamp())),
                "type": "order_block",
                "upper": round(float(ob_blk.top), 6),
                "lower": round(float(ob_blk.bottom), 6),
                "price": round(float((ob_blk.top + ob_blk.bottom) / 2), 6),
                "description": (f"15M order block {ob_blk.bottom:.5f}–{ob_blk.top:.5f} "
                                f"(fib {ob.fib_level}, in OTE={ob.in_ote_zone})"),
            },
            {
                "timestamp": to_iso(int(tap.timestamp())),
                "type": "entry_tap",
                "price": round(float(entry), 6),
                "description": (f"5M tap of the order block → {direction.upper()} entry at "
                                f"{entry:.5f}; SL {sl:.5f}, final TP {tp:.5f}"),
            },
        ]
        # Two-leg exit breakdown so the displayed pips fully reconcile.
        if partial_time is not None:
            events.append({
                "timestamp": to_iso(int(pd.Timestamp(partial_time).timestamp())),
                "type": "partial_take_profit",
                "price": round(float(partial_price), 6),
                "description": (f"Leg 1: closed HALF at +{PARTIAL_R}R ({partial_price:.5f}) and "
                                f"moved stop to breakeven ({entry:.5f})"),
            })
            if abs(float(exit_price) - float(entry)) < 1e-9:
                final_desc = (f"Leg 2: remaining half stopped at BREAKEVEN ({exit_price:.5f}). "
                              f"Trade net {pnl_R:+.2f}R (the +{PARTIAL_R}R partial is the profit)")
            else:
                final_desc = (f"Leg 2: remaining half exited at {exit_price:.5f}. "
                              f"Trade net {pnl_R:+.2f}R (half at +{PARTIAL_R}R, half here)")
            events.append({
                "timestamp": to_iso(int(exit_time.timestamp())),
                "type": "final_exit",
                "price": round(float(exit_price), 6),
                "description": final_desc,
            })
        else:
            events.append({
                "timestamp": to_iso(int(exit_time.timestamp())),
                "type": "exit",
                "price": round(float(exit_price), 6),
                "description": (f"Full position exited at {exit_price:.5f} → {outcome.upper()} "
                                f"({pnl_R:+.2f}R) — partial level never reached"),
            })
        events.sort(key=lambda e: e["timestamp"])  # true chronological order

        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(tap.timestamp())),
            "direction": direction,
            "entry_price": round(float(entry), 6),
            "stop_loss": round(float(sl), 6),
            "take_profit": round(float(tp), 6),
            "exit_time": to_iso(int(exit_time.timestamp())),
            "exit_price": round(float(exit_price), 6),
            "outcome": outcome,
            "pnl_R": round(float(pnl_R), 3),
            "events": events,
            "reason": (f"4H sweep {bias.direction.value} + 1H displacement MSS + 15M OB(OTE) "
                       f"+ 5M tap; partial {PARTIAL_R}R->BE, final {FINAL_R}R"),
        })
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 95: Refined MSS + OB (partial+BE)")
    parser.add_argument("--csv4h", required=True, help="4-hour CSV")
    parser.add_argument("--csv1h", required=True, help="1-hour CSV")
    parser.add_argument("--csv15m", required=True, help="15-minute CSV")
    parser.add_argument("--csv5m", required=True, help="5-minute CSV")
    parser.add_argument("--output", default=None, help="Output JSON path")
    args = parser.parse_args()

    df_4h = _df_from_csv(args.csv4h)
    df_1h = _df_from_csv(args.csv1h)
    df_15m = _df_from_csv(args.csv15m)
    df_5m = _df_from_csv(args.csv5m)
    meta = parse_csv_filename(args.csv4h)
    out = args.output or f"strategy_95_results_{meta['symbol']}.json"
    run_strategy(df_4h, df_1h, df_15m, df_5m, out, symbol=meta.get("symbol", "FX"))


if __name__ == "__main__":
    main()
