#!/usr/bin/env python3
"""
Strategy 142: MTF Trend Pullback Reclaim

Original strategy built from first principles. It does NOT use supply/demand
zones: validation showed those zones had no pooled directional edge. Instead it
uses a Daily persistence regime, then buys/sells a controlled 4H pullback only
after price reclaims short-term structure.

TIMEFRAMES
  Daily (derived from 4H): permission and direction.
  4H: pullback, reclaim confirmation, entry and exit.

CAUSALITY
  - Daily context is the latest FULLY CLOSED UTC Daily bar.
  - A signal is known only at its 4H close.
  - A stop-entry can fill only on one of the next two bars.
  - Stop is checked before target on ambiguous OHLC bars.
  - All parameters are ATR/R normalized and identical for every instrument.

FROZEN BEFORE SEALED TEST — 2026-08-05
  Parameters selected using only the first 75% of each instrument:
  daily EMA slow=100, target=0.65R, break-even armed at +0.50R for the next
  candle, maximum estimated cost=0.04R; all other values below unchanged.
  Development evidence after costs:
    TRAIN      n=54, win=61.1%, expectancy=+0.060R, PF=1.19
    VALIDATION n=27, win=63.0%, expectancy=+0.046R, PF=1.14

SEALED TEST OPENED ONCE — 2026-08-05; NO POST-TEST RETUNING
  n=20, win=65.0%, expectancy=+0.183R, PF=1.82, total=+3.7R.
  Four of eight instruments were positive; four had zero/negative results.

STATUS: SEALED-PASS, PROVISIONAL. It met the predeclared >=60% net-win and
positive-expectancy targets after costs, but 20 trades and 4/8 positive-symbol
breadth are not enough to promise a durable or live-trading edge. Parameters
must not be retuned against the opened test period. See scripts/validate_s142.py.
"""

from __future__ import annotations

import argparse
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(THIS)
for _path in (THIS, ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import numpy as np

from core import emit_progress, load_csv, parse_csv_filename, resample
from core import round_turn_cost_price, save_trades, to_iso

P = {
    "daily_fast": 50,
    "daily_slow": 100,
    "daily_atr": 20,
    "daily_slope_lb": 10,
    "daily_slope_min": 0.20,
    "daily_er_n": 20,
    "daily_er_min": 0.20,
    "base_fast": 20,
    "base_slow": 50,
    "base_atr": 20,
    "base_slope_lb": 5,
    "base_slope_min": 0.05,
    "pullback_bars": 4,
    "pullback_buffer_atr": 0.30,
    "reclaim_bars": 2,
    "close_position": 0.65,
    "range_min_atr": 0.35,
    "range_max_atr": 1.80,
    "max_extension_atr": 1.00,
    "entry_buffer_atr": 0.03,
    "entry_valid_bars": 2,
    "stop_buffer_atr": 0.10,
    "risk_min_atr": 0.45,
    "risk_max_atr": 1.80,
    "max_cost_r": 0.04,
    "target_r": 0.65,
    # Arm after +0.50R MFE and apply only from the following candle so
    # ambiguous same-bar OHLC ordering cannot improve results.
    "breakeven_r": 0.50,
    "max_hold": 12,
    "cooldown": 1,
    # 0 = both directions; no symbol-specific or direction-specific exclusion.
    "side_filter": 0,
}

START_EQUITY = 10_000.0
RISK_PCT = 0.01


def _arrays(bars):
    return (
        np.array([b.timestamp for b in bars], dtype=np.int64),
        np.array([b.open for b in bars], dtype=np.float64),
        np.array([b.high for b in bars], dtype=np.float64),
        np.array([b.low for b in bars], dtype=np.float64),
        np.array([b.close for b in bars], dtype=np.float64),
    )


def _atr(h, l, c, n):
    pc = np.empty_like(c)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.zeros_like(tr)
    if not len(tr):
        return out
    seed = min(n, len(tr))
    out[:seed] = np.mean(tr[:seed])
    alpha = 1.0 / n
    for i in range(seed, len(tr)):
        out[i] = out[i - 1] + alpha * (tr[i] - out[i - 1])
    return out


def _ema(x, n):
    out = np.empty_like(x)
    if not len(x):
        return out
    alpha = 2.0 / (n + 1.0)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + alpha * (x[i] - out[i - 1])
    return out


def _efficiency(c, n):
    out = np.zeros(len(c), dtype=np.float64)
    for i in range(n, len(c)):
        path = float(np.sum(np.abs(np.diff(c[i - n:i + 1]))))
        out[i] = abs(float(c[i] - c[i - n])) / path if path > 0 else 0.0
    return out


def _daily_context(daily, p):
    ts, _, h, l, c = _arrays(daily)
    atr = _atr(h, l, c, p["daily_atr"])
    fast = _ema(c, p["daily_fast"])
    slow = _ema(c, p["daily_slow"])
    er = _efficiency(c, p["daily_er_n"])
    bias = np.zeros(len(c), dtype=np.int8)
    lb = p["daily_slope_lb"]
    warm = max(p["daily_slow"], p["daily_er_n"], lb) + 1
    for i in range(warm, len(c)):
        if atr[i] <= 0:
            continue
        slope = (fast[i] - fast[i - lb]) / atr[i]
        if c[i] > fast[i] > slow[i] and slope >= p["daily_slope_min"] \
                and er[i] >= p["daily_er_min"]:
            bias[i] = 1
        elif c[i] < fast[i] < slow[i] and slope <= -p["daily_slope_min"] \
                and er[i] >= p["daily_er_min"]:
            bias[i] = -1
    return ts, bias, fast, slow, er


def _fill_stop(o, h, l, signal_idx, trigger, direction, valid_bars, n):
    for j in range(signal_idx + 1, min(n, signal_idx + 1 + valid_bars)):
        if direction == "long" and h[j] >= trigger:
            return j, float(max(trigger, o[j]))
        if direction == "short" and l[j] <= trigger:
            return j, float(min(trigger, o[j]))
    return None, None


def _manage(o, h, l, c, entry_idx, entry, stop, direction, p, n):
    is_long = direction == "long"
    risk = (entry - stop) if is_long else (stop - entry)
    target = entry + p["target_r"] * risk if is_long else entry - p["target_r"] * risk
    last = min(n - 1, entry_idx + p["max_hold"])
    active_stop = float(stop)
    be_armed = False
    for j in range(entry_idx, last + 1):
        # Conservative OHLC ordering: the stop active at the candle open is
        # checked before target. Break-even can arm only for the next candle.
        if (l[j] <= active_stop) if is_long else (h[j] >= active_stop):
            pnl = ((active_stop - entry) / risk) if is_long else ((entry - active_stop) / risk)
            why = "breakeven_stop" if be_armed else "structure_stop"
            return j, active_stop, float(pnl), why, target
        if (h[j] >= target) if is_long else (l[j] <= target):
            return j, float(target), float(p["target_r"]), "target", target
        threshold = float(p.get("breakeven_r", 0.0))
        if threshold > 0 and not be_armed:
            favorable = (h[j] - entry) / risk if is_long else (entry - l[j]) / risk
            if favorable >= threshold:
                be_armed = True
                active_stop = float(entry)
    exit_idx = min(last + 1, n - 1)
    px = float(o[exit_idx]) if exit_idx > last else float(c[last])
    pnl_r = ((px - entry) / risk) if is_long else ((entry - px) / risk)
    return exit_idx, px, float(pnl_r), "time_stop", target


def generate_trades(candles, *, symbol="", params=None):
    p = {**P, **(params or {})}
    if len(candles) < 1500:
        return []

    daily = resample(candles, 1440)
    if len(daily) <= p["daily_slow"] + 5:
        return []

    ts, o, h, l, c = _arrays(candles)
    n = len(c)
    atr = _atr(h, l, c, p["base_atr"])
    fast = _ema(c, p["base_fast"])
    slow = _ema(c, p["base_slow"])
    dts, dbias, dfast, dslow, der = _daily_context(daily, p)

    trades = []
    kd = 0
    next_ok = 0
    step = max(1, n // 20)
    warm = max(p["base_slow"], p["base_atr"], p["pullback_bars"] + 2)

    i = warm
    while i < n - p["entry_valid_bars"] - 1:
        if i % step == 0:
            emit_progress("scan", 25 + int(70 * i / n), f"S142 bar {i:,}/{n:,}")

        # UTC Daily bar labelled at its open; only use it after its full close.
        now = int(ts[i]) + 1
        while kd + 1 < len(dts) and int(dts[kd + 1]) + 86400 <= now:
            kd += 1

        if i < next_ok or atr[i] <= 0:
            i += 1
            continue
        b = int(dbias[kd]) if kd < len(dbias) else 0
        if b == 0 or (p["side_filter"] and b != p["side_filter"]):
            i += 1
            continue

        a = float(atr[i])
        lb = p["base_slope_lb"]
        slope = (fast[i] - fast[i - lb]) / a
        long_regime = b > 0 and fast[i] > slow[i] and slope >= p["base_slope_min"]
        short_regime = b < 0 and fast[i] < slow[i] and slope <= -p["base_slope_min"]
        if not (long_regime or short_regime):
            i += 1
            continue

        direction = "long" if long_regime else "short"
        pb = p["pullback_bars"]
        start = max(0, i - pb)
        rng = float(h[i] - l[i])
        if rng <= 0 or not (p["range_min_atr"] <= rng / a <= p["range_max_atr"]):
            i += 1
            continue

        if direction == "long":
            touched = bool(np.any(l[start:i] <= fast[start:i]))
            held = bool(np.all(c[start:i + 1] >= slow[start:i + 1] - p["pullback_buffer_atr"] * atr[start:i + 1]))
            reclaim = c[i] > float(np.max(h[i - p["reclaim_bars"]:i]))
            close_ok = (c[i] - l[i]) / rng >= p["close_position"] and c[i] > o[i]
            extension_ok = (c[i] - fast[i]) / a <= p["max_extension_atr"]
        else:
            touched = bool(np.any(h[start:i] >= fast[start:i]))
            held = bool(np.all(c[start:i + 1] <= slow[start:i + 1] + p["pullback_buffer_atr"] * atr[start:i + 1]))
            reclaim = c[i] < float(np.min(l[i - p["reclaim_bars"]:i]))
            close_ok = (h[i] - c[i]) / rng >= p["close_position"] and c[i] < o[i]
            extension_ok = (fast[i] - c[i]) / a <= p["max_extension_atr"]
        if not (touched and held and reclaim and close_ok and extension_ok):
            i += 1
            continue

        trigger = float(h[i] + p["entry_buffer_atr"] * a) if direction == "long" \
            else float(l[i] - p["entry_buffer_atr"] * a)
        entry_idx, entry = _fill_stop(o, h, l, i, trigger, direction,
                                      p["entry_valid_bars"], n)
        if entry_idx is None:
            i += 1
            continue

        pull_lo = float(np.min(l[start:i + 1]))
        pull_hi = float(np.max(h[start:i + 1]))
        stop = pull_lo - p["stop_buffer_atr"] * a if direction == "long" \
            else pull_hi + p["stop_buffer_atr"] * a
        risk = (entry - stop) if direction == "long" else (stop - entry)
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if risk <= 0 or not (p["risk_min_atr"] <= risk / a <= p["risk_max_atr"]):
            i += 1
            continue
        if cost / risk > p["max_cost_r"]:
            i += 1
            continue

        exit_idx, exit_px, pnl_r, why, target = _manage(
            o, h, l, c, entry_idx, entry, stop, direction, p, n
        )
        events = _events(ts, daily, kd, i, entry_idx, exit_idx, direction,
                         entry, stop, target, exit_px, pnl_r, why, dfast, dslow, der)
        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(ts[entry_idx])),
            "direction": direction,
            "entry_price": round(entry, 6),
            "stop_loss": round(float(stop), 6),
            "take_profit": round(float(target), 6),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_px), 6),
            "outcome": "win" if pnl_r > 0 else "loss" if pnl_r < 0 else "breakeven",
            "gross_R": round(float(pnl_r), 4),
            "symbol": symbol or None,
            "setup": "daily_trend_4h_pullback_reclaim",
            "events": events,
            "reason": (f"Daily persistent {'up' if direction == 'long' else 'down'} trend; "
                       f"4H EMA{p['base_fast']} pullback held EMA{p['base_slow']}, reclaimed "
                       f"{p['reclaim_bars']}-bar structure; stop-entry, {p['target_r']:.2f}R "
                       f"target; exit {why}"),
        })
        next_ok = exit_idx + p["cooldown"]
        i = exit_idx + 1

    emit_progress("scan", 97, f"S142 found {len(trades)} trades")
    return trades


def _events(ts, daily, kd, signal_idx, entry_idx, exit_idx, direction,
            entry, stop, target, exit_px, pnl_r, why, dfast, dslow, der):
    signal_known_at = int(ts[signal_idx + 1]) if signal_idx + 1 < len(ts) else int(ts[signal_idx])
    return [
        {"timestamp": to_iso(int(daily[kd].timestamp) + 86400), "type": "trend_context",
         "price": round(float(dfast[kd]), 6),
         "description": (f"Latest closed Daily context permits {direction.upper()}: "
                         f"EMA50={dfast[kd]:.5f}, EMA200={dslow[kd]:.5f}, "
                         f"efficiency={der[kd]:.2f}")},
        {"timestamp": to_iso(signal_known_at), "type": "mss",
         "price": round(float(entry), 6), "level": round(float(entry), 6),
         "description": "Closed 4H bar held trend support and reclaimed short-term structure"},
        {"timestamp": to_iso(int(ts[entry_idx])), "type": "entry_trigger",
         "price": round(float(entry), 6), "level": round(float(entry), 6),
         "description": (f"Momentum stop-entry {entry:.5f}; stop {stop:.5f}; "
                         f"target {target:.5f}")},
        {"timestamp": to_iso(int(ts[exit_idx])), "type": "final_exit",
         "price": round(float(exit_px), 6),
         "description": f"Exit via {why}: {pnl_r:+.2f}R gross"},
    ]


def summarize(trades):
    if not trades:
        return {}
    rs = [float(t["gross_R"]) for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    gp, gl = sum(wins), sum(losses)
    eq = peak = START_EQUITY
    dd = 0.0
    for r in rs:
        eq += eq * RISK_PCT * r
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return {
        "n": len(rs), "total_r": sum(rs), "exp_r": sum(rs) / len(rs),
        "win": len(wins) / len(rs), "pf": gp / gl if gl else float("inf"),
        "avg_win": gp / len(wins) if wins else 0.0,
        "avg_loss": gl / len(losses) if losses else 0.0,
        "equity": eq, "dd": dd,
    }


def run_strategy(candles, output_path, *, symbol="", params=None):
    trades = generate_trades(candles, symbol=symbol, params=params)
    save_trades(trades, output_path)
    s = summarize(trades)
    print(f"Saved {len(trades)} trades to {output_path}")
    if s:
        print(f"  gross before dashboard cost enrichment: {s['total_r']:+.1f}R | "
              f"{s['exp_r']:+.3f}R/trade | win {s['win']:.1%} | PF {s['pf']:.2f}")
        print(f"  avg win {s['avg_win']:.2f}R vs loss {s['avg_loss']:.2f}R | "
              f"${START_EQUITY:,.0f} -> ${s['equity']:,.0f} | DD {s['dd']:.1%}")
    return trades


def main():
    ap = argparse.ArgumentParser(description="Strategy 142: MTF Trend Pullback Reclaim")
    ap.add_argument("--csv4h", required=True, help="4-hour OHLCV CSV (Daily context is derived)")
    ap.add_argument("--output", default=None, help="Output JSON path")
    args = ap.parse_args()
    candles = load_csv(args.csv4h)
    meta = parse_csv_filename(args.csv4h)
    symbol = meta.get("symbol") or ""
    out = args.output or f"strategy_142_results_{symbol or 'data'}.json"
    run_strategy(candles, out, symbol=symbol)


if __name__ == "__main__":
    main()
