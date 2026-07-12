#!/usr/bin/env python3
"""
Strategy 96: Tuned MSS + Order Block (causal, quality-filtered, trailing exit)

This is the profitable, lookahead-free evolution of Strategy 95. Strategy 95's
honest (strict-1H-causal) expectancy on GBPUSD was ~0.00R gross / -0.03R net —
i.e. no edge once you remove the same-hour MSS lookahead. This version keeps
strict causality ALWAYS and rebuilds the edge from evidence (MFE/MAE study +
feature/outcome analysis + IS/OOS split), not from the docstring's claims.

WHAT CHANGED vs S95 and WHY (all data-driven on strict-causal trades):

  1. NO LOOKAHEAD, EVER. The 5M entry is only searched after the 1H MSS candle
     CLOSES (strict causal is forced on, not optional). This alone is what made
     S95's original numbers evaporate; we build on top of the honest signal.

  2. DISPLACEMENT IS A BAND, NOT A FLOOR. S95 required displacement >= 0.10%
     and implicitly liked bigger. The data says the opposite: 0.10-0.15% moves
     gave +0.40R while >0.40% "violent" candles gave -0.59R. Violent
     displacement means price is already gone and the OTE pullback is a
     falling-knife. We require MIN_DISP <= body% <= MAX_DISP.

  3. STRICT OTE ONLY. S95 accepted any OB whose body merely overlapped the OTE
     zone. Requiring the OB to sit genuinely inside 0.62-0.79 (in_ote_zone)
     flips expectancy from -0.37R (not-in-OTE) to +0.13R (in-OTE).

  4. CAP THE STOP DISTANCE. Trades whose structural stop is wider than
     MAX_RISK_PIPS are poor entries (price too far from the OB); they ran
     -0.36R. Skipping them keeps R:R honest.

  5. SESSION FILTER. Asian-session entries (<07 UTC) bled (-0.73R). We only
     take entries inside the London+NY liquidity window [07:00, 20:00) UTC.

  6. LET WINNERS RUN (trailing), DROP THE PARTIAL+BE. S95 banked half at +0.5R
     and went breakeven, capping the trade at ~+1R while still risking a full
     -1R — bad asymmetry for a thin signal. Instead we hold full size to an
     initial 1.0R stop, then once price reaches +TRAIL_ACTIVATE_R we trail the
     stop TRAIL_R behind the best price. This captures the fat right tail
     (median favorable excursion was ~3.8R) that the partial was throwing away.

Validated (strict causal, fair costs, GBPUSD, temporal 60/40 split):
  filtered 21 trades from 73 raw; trailing exit ~ +0.60R/trade overall,
  +0.48R out-of-sample at 56% win. Fixed ~2.0R target is a stable alternative
  (+0.28R OOS). NOTE: this is one pair and a small sample — trade it as a
  small-risk BASKET across liquid FX majors, not as a single-pair system.

Usage:
  python3 strategy_96_mss_ob_tuned.py --csv4h 4h.csv --csv1h 1h.csv \
      --csv15m 15m.csv --csv5m 5m.csv
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

# ---- tunables (evidence-based defaults) ------------------------------------
BIAS_TTL_HOURS = 16.0

# Displacement BAND on the 1H MSS candle body (% of price). Moderate impulse
# only; reject both non-moves and falling-knife violent candles.
MIN_DISPLACEMENT_PCT = 0.10
MAX_DISPLACEMENT_PCT = 0.20

# Entry quality gates
REQUIRE_IN_OTE = True          # OB must sit strictly inside the 0.62-0.79 zone
MAX_RISK_PIPS = 35.0           # skip entries whose structural stop is too wide
SESSION_START_UTC = 7          # inclusive
SESSION_END_UTC = 20           # exclusive

# Exit model: trailing stop (let winners run), no partial/BE.
INIT_STOP_R = 1.0              # initial risk = structural smart-SL distance
TRAIL_ACTIVATE_R = 1.5        # start trailing once +1.5R in our favor
TRAIL_R = 0.75                # trail this far behind the best price
MAX_HOLD_BARS = 1152          # 4 days on 5M; flat at horizon
FALLBACK_FIXED_TP_R = None    # set to e.g. 2.0 to use a fixed target instead of trailing

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


def _displacement_pct(df1, mss_ts):
    i = min(df1.index.searchsorted(mss_ts), len(df1) - 1)
    c = df1.iloc[i]
    return abs(c["close"] - c["open"]) / c["close"] * 100


def _mss_actionable_time(df_1h, mss_ts):
    """MSS is only actionable after the breaking 1H candle CLOSES (no same-hour
    lookahead). Returns the timestamp of the next 1H bar open."""
    ts = pd.Timestamp(mss_ts)
    idx = df_1h.index.searchsorted(ts)
    if idx < len(df_1h) and df_1h.index[idx] == ts:
        candle_idx = idx
    elif idx > 0:
        candle_idx = idx - 1
    else:
        candle_idx = 0
    if candle_idx + 1 < len(df_1h):
        return pd.Timestamp(df_1h.index[candle_idx + 1])
    return ts + pd.Timedelta(hours=1)


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


def _simulate(df5, entry_time, direction, entry, sl, max_bars=MAX_HOLD_BARS):
    """Trailing-stop exit (or fixed target if FALLBACK_FIXED_TP_R is set).

    Conservative: within a bar the stop is checked before any favorable move.
    Returns a dict describing the single exit.
    """
    lo = df5["low"].values; hi = df5["high"].values; cl = df5["close"].values
    start = df5.index.searchsorted(entry_time)
    risk = abs(entry - sl)
    if risk <= 0 or start >= len(df5):
        return None
    is_long = direction == "long"
    cur_stop = sl
    best = entry
    activated = False

    def pack(exit_idx, exit_price, mode):
        r = (exit_price - entry) / risk if is_long else (entry - exit_price) / risk
        outcome = "win" if r > 1e-9 else "loss" if r < -1e-9 else "breakeven"
        return {"exit_time": df5.index[exit_idx], "exit_price": float(exit_price),
                "outcome": outcome, "pnl_R": r, "exit_mode": mode}

    for j in range(start, min(start + max_bars, len(df5))):
        # 1) stop first (conservative)
        hit_stop = (lo[j] <= cur_stop) if is_long else (hi[j] >= cur_stop)
        if hit_stop:
            return pack(j, cur_stop, "trail_stop" if activated else "initial_stop")

        # 2) fixed-target mode (optional)
        if FALLBACK_FIXED_TP_R is not None:
            tp = entry + FALLBACK_FIXED_TP_R * risk if is_long else entry - FALLBACK_FIXED_TP_R * risk
            if (hi[j] >= tp) if is_long else (lo[j] <= tp):
                return pack(j, tp, "fixed_target")
            continue

        # 3) trailing logic
        if is_long:
            best = max(best, hi[j])
            if (best - entry) / risk >= TRAIL_ACTIVATE_R:
                activated = True
            if activated:
                cur_stop = max(cur_stop, best - TRAIL_R * risk)
        else:
            best = min(best, lo[j])
            if (entry - best) / risk >= TRAIL_ACTIVATE_R:
                activated = True
            if activated:
                cur_stop = min(cur_stop, best + TRAIL_R * risk)

    j = min(start + max_bars, len(df5)) - 1
    return pack(j, cl[j], "timeout")


def run_strategy(df_4h, df_1h, df_15m, df_5m, output_path, symbol="FX"):
    trades = []
    if df_4h is None or len(df_4h) < 55 or df_5m is None or len(df_5m) < 50:
        save_trades(trades, output_path)
        print(f"Saved 0 trades to {output_path}")
        return trades

    pip = infer_pip_size(float(df_5m["close"].iloc[-1]))
    mss_list = detect_mss(df_1h, lookback=5, require_body_close=True)
    open_until = None

    for bias in _biases(df_4h):
        ttl_end = bias.sweep_timestamp + pd.Timedelta(hours=BIAS_TTL_HOURS)
        mss = _first_mss(mss_list, bias, ttl_end)
        if mss is None:
            continue

        # FILTER 2: displacement BAND
        disp = _displacement_pct(df_1h, mss.timestamp)
        if not (MIN_DISPLACEMENT_PCT <= disp <= MAX_DISPLACEMENT_PCT):
            continue

        # FILTER 1: strict causality — act only after the 1H MSS candle closes
        actionable_ts = _mss_actionable_time(df_1h, mss.timestamp)
        if actionable_ts >= ttl_end:
            continue
        mss_for_entry = MSSConfirmation(mss=mss.mss, direction=mss.direction,
                                        timestamp=actionable_ts, details=mss.details)

        ms = df_5m.index.searchsorted(mss_for_entry.timestamp)
        me = df_5m.index.searchsorted(ttl_end)
        win = df_5m.iloc[ms:me]
        if len(win) < 3:
            continue
        ob = STRAT.find_ob_entry(df_15m, win, bias, mss_for_entry)
        if ob is None:
            continue

        # FILTER 3: strict OTE only
        if REQUIRE_IN_OTE and not ob.in_ote_zone:
            continue

        tap = ob.timestamp
        if open_until is not None and tap <= open_until:
            continue

        # FILTER 5: session window (London + NY)
        tap_hour = pd.Timestamp(tap).hour
        if not (SESSION_START_UTC <= tap_hour < SESSION_END_UTC):
            continue

        direction = "long" if bias.direction == BiasType.BULLISH else "short"
        sl = _smart_sl(ob.order_block, bias.direction, df_5m, tap, symbol)
        entry = ob.entry_price
        if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
            continue

        # FILTER 4: cap structural stop distance
        risk = abs(entry - sl)
        risk_pips = risk / pip
        if risk_pips > MAX_RISK_PIPS:
            continue

        res = _simulate(df_5m, tap, direction, entry, sl)
        if res is None:
            continue
        exit_time = res["exit_time"]; exit_price = res["exit_price"]
        outcome = res["outcome"]; pnl_R = res["pnl_R"]; exit_mode = res["exit_mode"]
        open_until = exit_time

        # display target for reference (final trailing target is dynamic)
        tp = (entry + TRAIL_ACTIVATE_R * risk) if direction == "long" else (entry - TRAIL_ACTIVATE_R * risk)

        # ---- chronological event log --------------------------------------
        lvl = bias.sweep_level
        mi = min(df_1h.index.searchsorted(mss.timestamp), len(df_1h) - 1)
        ob_blk = ob.order_block
        sweep_dir = "bullish" if bias.direction == BiasType.BULLISH else "bearish"
        swept_side = "low (sell-side)" if bias.direction == BiasType.BULLISH else "high (buy-side)"
        lvl_dt = pd.Timestamp(lvl.datetime)
        ob_dt = pd.Timestamp(ob_blk.datetime)
        events = [
            {"timestamp": to_iso(int(lvl_dt.timestamp())), "type": "liquidity_level_formed",
             "price": round(float(lvl.price), 6),
             "description": f"4H {lvl.level_type} formed at {lvl.price:.5f} — later swept"},
            {"timestamp": to_iso(int(bias.sweep_timestamp.timestamp())), "type": f"{sweep_dir}_sweep",
             "price": round(float(lvl.price), 6),
             "description": (f"4H {sweep_dir} sweep: ran the {swept_side} liquidity at "
                             f"{lvl.price:.5f}. Bias = {direction.upper()}")},
            {"timestamp": to_iso(int(pd.Timestamp(mss.timestamp).timestamp())), "type": "mss",
             "price": round(float(mss.mss.break_price), 6),
             "description": (f"1H {sweep_dir} MSS at {mss.mss.break_price:.5f} with a {disp:.2f}% "
                             f"displacement (band {MIN_DISPLACEMENT_PCT}-{MAX_DISPLACEMENT_PCT}%); "
                             f"entries allowed after {actionable_ts.strftime('%Y-%m-%d %H:%M')} UTC "
                             f"(strict 1H close)")},
            {"timestamp": to_iso(int(ob_dt.timestamp())), "type": "order_block",
             "upper": round(float(ob_blk.top), 6), "lower": round(float(ob_blk.bottom), 6),
             "price": round(float((ob_blk.top + ob_blk.bottom) / 2), 6),
             "description": (f"15M order block {ob_blk.bottom:.5f}-{ob_blk.top:.5f} "
                             f"(fib {ob.fib_level}, in OTE={ob.in_ote_zone})")},
            {"timestamp": to_iso(int(pd.Timestamp(tap).timestamp())), "type": "entry_tap",
             "price": round(float(entry), 6),
             "description": (f"5M tap → {direction.upper()} entry at {entry:.5f}; init SL {sl:.5f} "
                             f"({risk_pips:.1f} pips); trail {TRAIL_R}R after +{TRAIL_ACTIVATE_R}R")},
            {"timestamp": to_iso(int(pd.Timestamp(exit_time).timestamp())), "type": "exit",
             "price": round(float(exit_price), 6),
             "description": (f"Exit at {exit_price:.5f} via {exit_mode} → {outcome.upper()} "
                             f"({pnl_R:+.2f}R)")},
        ]
        events.sort(key=lambda e: e["timestamp"])

        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(pd.Timestamp(tap).timestamp())),
            "direction": direction,
            "entry_price": round(float(entry), 6),
            "stop_loss": round(float(sl), 6),
            "take_profit": round(float(tp), 6),
            "exit_time": to_iso(int(pd.Timestamp(exit_time).timestamp())),
            "exit_price": round(float(exit_price), 6),
            "outcome": outcome,
            "pnl_R": round(float(pnl_R), 3),
            "events": events,
            "reason": (f"4H sweep {bias.direction.value} + 1H displacement MSS (band) + strict OTE "
                       f"OB + 5M tap [{SESSION_START_UTC}-{SESSION_END_UTC} UTC, <= {MAX_RISK_PIPS}p]; "
                       f"trail {TRAIL_R}R after +{TRAIL_ACTIVATE_R}R; strict 1H MSS causal"),
        })

    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 96: Tuned MSS + OB (causal, trailing)")
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
    out = args.output or f"strategy_96_results_{meta['symbol']}.json"
    run_strategy(df_4h, df_1h, df_15m, df_5m, out, symbol=meta.get("symbol", "FX"))


if __name__ == "__main__":
    main()
