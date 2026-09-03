#!/usr/bin/env python3
"""
Strategy 140: Small-Account Reversal at Key S/R (flush + trend break + stop entry)

Source: docs/"Small Account Futures Trading Strategy & Trade Execution Guide.pdf"
        (micro-futures reversal playbook, $50 risk -> scaled contracts)

WHAT THE SOURCE SAYS (6 steps) AND HOW IT IS MECHANISED HERE
------------------------------------------------------------
  Step 1  Pre-map key S/R on the 15-MINUTE chart from major daily + overnight
          swing highs/lows.
          -> 15m bars are derived from the execution CSV. Levels = confirmed 15m
             fractal pivots (both sides) + previous-day high/low. A level only
             becomes tradeable AFTER the bar that confirms it has closed, and it
             expires after LEVEL_MAX_AGE_DAYS. Overlapping levels cluster into a
             zone; the cluster size is the level's "strength" and at least
             MIN_LEVEL_TOUCHES confirmations are required ("key" level).

  Step 2  Forex-Factory red/orange news filter.
          -> DROPPED. No economic-calendar feed in the backtest data. This is the
             one rule that cannot be reproduced; live, it only removes trades.

  Step 3  Trade only inside the post-open timing windows (9:45 / 10:00 EST).
          -> Equity-index-open specific, so it is OFF by default (SESSION_START_UTC
             = 0, SESSION_END_UTC = 24) because gold/FX trade around the clock.
             Set --session-start/--session-end to re-impose a window.

  Step 4  Macro trend line broken + "unhealthy" parabolic flush into the level.
          -> Two measurable tests on the approach leg:
               a) displacement >= FLUSH_ATR_MULT * ATR  (big move)
               b) efficiency  = leg / sum(true ranges) >= FLUSH_EFFICIENCY
                  and >= FLUSH_BAR_RATIO of the leg's bars point the same way
                  (vertical, no pullbacks = exhaustion)
             Micro trend break = the signal bar CLOSES beyond the extreme of the
             previous MICRO_BREAK_BARS bars (the flush's micro trend is done).

  Step 5  Structural reversal pattern + strong confirmation candle.
          -> One of: failed_breakout (wick through the level, close back inside),
             double_bottom/double_top, inverse_head_and_shoulders/head_and_shoulders.
             Plus a confirmation candle with body >= CONF_BODY_MIN of range, close
             in the last CONF_CLOSE_POS of the range, and range >= CONF_RANGE_ATR*ATR.

  Step 6  Buy/Sell STOP entry above/below the confirmation candle, stop below/above
          structure, break-even at 1.0-1.5x risk, then trail.
          -> Resting stop order at the candle extreme + ENTRY_BUFFER_ATR*ATR, valid
             ORDER_VALID_BARS bars then cancelled (never a limit order, never a
             falling knife). SL below the pattern structure. PARTIAL_FRACTION of the
             position is banked at PARTIAL_R, the stop goes to break-even at
             BE_TRIGGER_R, and from then on it trails under the preceding bar's
             low/high. Remainder targets RR_TARGET (source demands 2:1-3:1 minimum).

  Scaling  "2 contracts -> 6 -> 12 as equity builds" is position sizing, not signal
           logic. Trades are recorded in R so the geometry is size-independent; the
           run summary prints the compounded equity curve at RISK_PCT per trade,
           which is exactly the source's scaling protocol.

CAUSALITY
---------
  - Levels, ATR, patterns and the flush test read only bars <= the signal bar.
  - The signal is read on the CLOSE of bar i; the stop order can only fill on
    bar i+1 or later, at max(stop_price, open) for longs (gap-aware).
  - Stop/target/trail checks use the CURRENT bar's high/low with a stop level that
    was fixed by the PREVIOUS bar's close. Stop is always checked before target
    inside a bar (conservative when a bar straddles both).

Usage:
  python strategy_140_small_account_reversal.py --csv5m XAUUSD_5m.csv
  python strategy_140_small_account_reversal.py --csv5m GBPUSD_5m.csv --session-start 12 --session-end 20
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(THIS)          # repo root holds the `core` package
for _p in (THIS, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

from core import (
    emit_progress,
    load_csv,
    parse_csv_filename,
    resample,
    round_turn_cost_price,
    save_trades,
    to_iso,
)

# --- Step 1: level mapping (15m) --------------------------------------------
LEVEL_TF_MIN = 15          # source maps S/R on the 15-minute chart
PIVOT_WIDTH = 2            # 15m fractal pivot: N bars lower/higher on both sides
LEVEL_MAX_AGE_DAYS = 5.0   # "daily and overnight" swings, not ancient history
MIN_LEVEL_TOUCHES = 2      # cluster size that makes a level "key"
ZONE_CLUSTER_ATR = 0.75    # levels within this many 15m ATRs are the same zone
LEVEL_TOUCH_ATR = 0.60     # flush extreme must land this close to the zone

# --- Step 3: optional timing window (UTC hours, [start, end)) ---------------
SESSION_START_UTC = 0      # 0..24 = always on (index-open windows are US-specific)
SESSION_END_UTC = 24

# --- Step 4: unhealthy parabolic flush + micro trend break ------------------
FLUSH_MAX_BARS = 10        # bars allowed in the approach leg
FLUSH_ATR_MULT = 2.0       # leg displacement in ATRs
FLUSH_EFFICIENCY = 0.55    # leg / sum(true range) -> vertical, few pullbacks
FLUSH_BAR_RATIO = 0.60     # share of leg bars closing in the flush direction
MAX_BARS_SINCE_EXTREME = 6 # reversal must be prompt, not a slow base
MICRO_BREAK_BARS = 2       # close beyond the extreme of the last N bars

# --- Step 5: pattern + confirmation candle ---------------------------------
PATTERN_LOOKBACK = 24      # bars scanned for the reversal structure
DOUBLE_TOL_ATR = 0.55      # how equal the two feet of a double top/bottom must be
DOUBLE_MIN_SEP = 3         # bars between the two feet
DOUBLE_MIN_BOUNCE_ATR = 0.80   # intervening pullback that makes it a real "W"/"M"
CONF_BODY_MIN = 0.50       # confirmation candle body / range
CONF_CLOSE_POS = 0.62      # close must sit in the top/bottom (1 - x) of the range
CONF_RANGE_ATR = 0.55      # confirmation candle range in ATRs

# --- Step 6: entry, stop, targets, management ------------------------------
ENTRY_BUFFER_ATR = 0.05    # stop order sits this far past the candle extreme
ORDER_VALID_BARS = 3       # unfilled stop order is cancelled after N bars
STOP_BUFFER_ATR = 0.25     # SL sits this far beyond the pattern structure
MAX_RISK_ATR = 3.5         # reject setups whose structural stop is too wide
RR_TARGET = 3.0            # source: minimum 2:1-3:1
PARTIAL_R = 1.5            # bank part of the position here
PARTIAL_FRACTION = 2.0 / 3.0   # source: "scaled out 2/3 contracts early"
BE_TRIGGER_R = 1.0         # source: break-even once 1.0x-1.5x risk is reached
TRAIL_ENABLED = True       # then trail under the preceding candle low/high
TRAIL_BUFFER_ATR = 0.10
MAX_HOLD_BARS = 96         # intraday reversal, not a swing position
COOLDOWN_BARS = 6          # no re-entry immediately after a trade closes

# --- position sizing report only (does not affect signals) -----------------
START_EQUITY = 1_000.0
RISK_PCT = 0.05            # source risks ~$50 on a small account, then scales


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def _true_range(h, l, c):
    pc = np.empty_like(c)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def _atr(h, l, c, n):
    """Wilder ATR. atr[i] uses bars <= i only."""
    tr = _true_range(h, l, c)
    out = np.zeros_like(tr)
    if len(tr) == 0:
        return out
    seed = min(n, len(tr))
    out[:seed] = np.mean(tr[:seed])
    alpha = 1.0 / n
    for i in range(seed, len(tr)):
        out[i] = out[i - 1] + alpha * (tr[i] - out[i - 1])
    return out


def _arrays(candles):
    ts = np.array([c.timestamp for c in candles], dtype=np.int64)
    o = np.array([c.open for c in candles], dtype=np.float64)
    h = np.array([c.high for c in candles], dtype=np.float64)
    l = np.array([c.low for c in candles], dtype=np.float64)
    c_ = np.array([c.close for c in candles], dtype=np.float64)
    return ts, o, h, l, c_


# ---------------------------------------------------------------------------
# Step 1 - key support / resistance from the 15m chart
# ---------------------------------------------------------------------------

def build_levels(bars15):
    """Confirmed 15m swing pivots + previous-day extremes, each with the
    timestamp at which it first becomes usable (no lookahead)."""
    levels: list[dict] = []
    n = len(bars15)
    tf_sec = LEVEL_TF_MIN * 60
    w = PIVOT_WIDTH

    for i in range(w, n - w):
        b = bars15[i]
        confirm_ts = bars15[i + w].timestamp + tf_sec
        if all(b.high > bars15[i - k].high for k in range(1, w + 1)) and \
           all(b.high > bars15[i + k].high for k in range(1, w + 1)):
            levels.append({"price": float(b.high), "kind": "high",
                           "confirm_ts": int(confirm_ts), "source": "15m_swing_high"})
        if all(b.low < bars15[i - k].low for k in range(1, w + 1)) and \
           all(b.low < bars15[i + k].low for k in range(1, w + 1)):
            levels.append({"price": float(b.low), "kind": "low",
                           "confirm_ts": int(confirm_ts), "source": "15m_swing_low"})

    # previous-day high / low (the "major daily" reference in the guide)
    by_day: dict[str, list] = {}
    for b in bars15:
        day = datetime.fromtimestamp(b.timestamp, tz=timezone.utc).date().isoformat()
        rec = by_day.get(day)
        if rec is None:
            by_day[day] = [float(b.high), float(b.low), int(b.timestamp)]
        else:
            rec[0] = max(rec[0], float(b.high))
            rec[1] = min(rec[1], float(b.low))
            rec[2] = max(rec[2], int(b.timestamp))
    for hi, lo, last_ts in by_day.values():
        confirm_ts = last_ts + tf_sec  # only usable once that day has fully closed
        levels.append({"price": hi, "kind": "high", "confirm_ts": confirm_ts, "source": "prev_day_high"})
        levels.append({"price": lo, "kind": "low", "confirm_ts": confirm_ts, "source": "prev_day_low"})

    levels.sort(key=lambda lv: lv["confirm_ts"])
    return levels


def match_zone(active, price, kind, atr15):
    """Cluster the active levels of one side around `price`. Returns the zone
    when it is 'key' enough, else None."""
    tol = LEVEL_TOUCH_ATR * atr15
    near = [lv for lv in active if lv["kind"] == kind and abs(lv["price"] - price) <= tol]
    if not near:
        return None
    anchor = min(near, key=lambda lv: abs(lv["price"] - price))
    cluster_tol = ZONE_CLUSTER_ATR * atr15
    cluster = [lv for lv in active
               if lv["kind"] == kind and abs(lv["price"] - anchor["price"]) <= cluster_tol]
    if len(cluster) < MIN_LEVEL_TOUCHES:
        return None
    prices = [lv["price"] for lv in cluster]
    return {
        "price": float(anchor["price"]),
        "high": float(max(prices)),
        "low": float(min(prices)),
        "strength": len(cluster),
        "sources": sorted({lv["source"] for lv in cluster}),
    }


# ---------------------------------------------------------------------------
# Step 4 - "unhealthy" parabolic flush into the level
# ---------------------------------------------------------------------------

def detect_flush(h, l, c, o, tr, extreme_idx, direction, atr):
    """Measure the approach leg that ends at `extreme_idx`. Returns leg stats when
    the move is big AND vertical (exhaustion), else None."""
    lo_bound = max(0, extreme_idx - FLUSH_MAX_BARS)
    if extreme_idx - lo_bound < 3:
        return None

    if direction == "long":          # down-flush into support
        start = lo_bound + int(np.argmax(h[lo_bound:extreme_idx + 1]))
        if extreme_idx - start < 3:
            return None
        leg = float(h[start] - l[extreme_idx])
        with_dir = int(np.sum(c[start:extreme_idx + 1] < o[start:extreme_idx + 1]))
    else:                            # up-flush into resistance
        start = lo_bound + int(np.argmin(l[lo_bound:extreme_idx + 1]))
        if extreme_idx - start < 3:
            return None
        leg = float(h[extreme_idx] - l[start])
        with_dir = int(np.sum(c[start:extreme_idx + 1] > o[start:extreme_idx + 1]))

    if leg < FLUSH_ATR_MULT * atr:
        return None
    span = extreme_idx - start + 1
    tr_sum = float(np.sum(tr[start:extreme_idx + 1]))
    if tr_sum <= 0:
        return None
    efficiency = leg / tr_sum
    if efficiency < FLUSH_EFFICIENCY:
        return None
    if with_dir / span < FLUSH_BAR_RATIO:
        return None
    return {"start_idx": start, "leg": leg, "atr_mult": leg / atr,
            "efficiency": efficiency, "bars": span}


# ---------------------------------------------------------------------------
# Step 5 - reversal structure + confirmation candle
# ---------------------------------------------------------------------------

def detect_pattern(h, l, c, extreme_idx, signal_idx, direction, level_price, atr):
    """Failed breakout / double bottom-top / (inverse) head & shoulders."""
    lo_bound = max(0, extreme_idx - PATTERN_LOOKBACK)
    tol = DOUBLE_TOL_ATR * atr

    if direction == "long":
        # a) failed breakout: wick spears the level, candle closes back inside
        if l[extreme_idx] < level_price and c[extreme_idx] > level_price:
            return "failed_breakout"
        # b) double bottom: an earlier low at the same price with a real bounce between
        for k in range(extreme_idx - DOUBLE_MIN_SEP, lo_bound - 1, -1):
            if abs(l[k] - l[extreme_idx]) > tol:
                continue
            bounce = float(np.max(h[k:extreme_idx + 1])) - max(l[k], l[extreme_idx])
            if bounce >= DOUBLE_MIN_BOUNCE_ATR * atr:
                # c) inverse H&S: head lower than both shoulders, shoulders level
                for j in range(extreme_idx + 1, signal_idx + 1):
                    if abs(l[j] - l[k]) <= tol and l[extreme_idx] < min(l[k], l[j]) - 0.2 * atr:
                        return "inverse_head_and_shoulders"
                return "double_bottom"
    else:
        if h[extreme_idx] > level_price and c[extreme_idx] < level_price:
            return "failed_breakout"
        for k in range(extreme_idx - DOUBLE_MIN_SEP, lo_bound - 1, -1):
            if abs(h[k] - h[extreme_idx]) > tol:
                continue
            bounce = min(h[k], h[extreme_idx]) - float(np.min(l[k:extreme_idx + 1]))
            if bounce >= DOUBLE_MIN_BOUNCE_ATR * atr:
                for j in range(extreme_idx + 1, signal_idx + 1):
                    if abs(h[j] - h[k]) <= tol and h[extreme_idx] > max(h[k], h[j]) + 0.2 * atr:
                        return "head_and_shoulders"
                return "double_top"
    return None


def is_confirmation_candle(o, h, l, c, i, direction, atr):
    rng = float(h[i] - l[i])
    if rng <= 0 or rng < CONF_RANGE_ATR * atr:
        return False
    body = abs(float(c[i] - o[i]))
    if body / rng < CONF_BODY_MIN:
        return False
    if direction == "long":
        return c[i] > o[i] and (c[i] - l[i]) / rng >= CONF_CLOSE_POS
    return c[i] < o[i] and (h[i] - c[i]) / rng >= CONF_CLOSE_POS


# ---------------------------------------------------------------------------
# Step 6 - stop-order fill + active management
# ---------------------------------------------------------------------------

def fill_stop_order(o, h, l, signal_idx, trigger, direction, n):
    """Resting buy/sell STOP. Gap-aware fill, cancelled after ORDER_VALID_BARS."""
    for j in range(signal_idx + 1, min(signal_idx + 1 + ORDER_VALID_BARS, n)):
        if direction == "long" and h[j] >= trigger:
            return j, float(max(trigger, o[j]))
        if direction == "short" and l[j] <= trigger:
            return j, float(min(trigger, o[j]))
    return None, None


def manage_trade(o, h, l, c, entry_idx, entry, sl, direction, atr, n):
    """Partial -> break-even -> trail under preceding candle. Stop is checked
    before target inside every bar (conservative)."""
    is_long = direction == "long"
    risk = (entry - sl) if is_long else (sl - entry)
    if risk <= 0:
        return None

    tp_partial = entry + PARTIAL_R * risk if is_long else entry - PARTIAL_R * risk
    tp_final = entry + RR_TARGET * risk if is_long else entry - RR_TARGET * risk
    be_level = entry + BE_TRIGGER_R * risk if is_long else entry - BE_TRIGGER_R * risk
    trail_buf = TRAIL_BUFFER_ATR * atr

    stop = sl
    realized = 0.0
    remaining = 1.0
    partial_idx = None
    partial_price = None
    be_active = False

    last = min(n - 1, entry_idx + MAX_HOLD_BARS)
    for j in range(entry_idx, last + 1):
        # 1) stop first
        hit_stop = (l[j] <= stop) if is_long else (h[j] >= stop)
        if hit_stop:
            leg_r = ((stop - entry) / risk) if is_long else ((entry - stop) / risk)
            realized += remaining * leg_r
            return {"exit_idx": j, "exit_price": float(stop), "pnl_R": realized,
                    "partial_idx": partial_idx, "partial_price": partial_price,
                    "reason": "breakeven_stop" if be_active else "structure_stop"}

        # 2) scale out
        if partial_idx is None and ((h[j] >= tp_partial) if is_long else (l[j] <= tp_partial)):
            realized += PARTIAL_FRACTION * PARTIAL_R
            remaining -= PARTIAL_FRACTION
            partial_idx = j
            partial_price = float(tp_partial)
            # bank the scale-out and protect the remainder at break-even
            stop = max(stop, entry) if is_long else min(stop, entry)
            be_active = True

        # 3) final target
        if remaining > 1e-9 and ((h[j] >= tp_final) if is_long else (l[j] <= tp_final)):
            realized += remaining * RR_TARGET
            return {"exit_idx": j, "exit_price": float(tp_final), "pnl_R": realized,
                    "partial_idx": partial_idx, "partial_price": partial_price,
                    "reason": "target"}

        # 4) break-even rule, then trail — decided on this bar's CLOSE, used from j+1
        if not be_active and ((h[j] >= be_level) if is_long else (l[j] <= be_level)):
            stop = max(stop, entry) if is_long else min(stop, entry)
            be_active = True
        if be_active and TRAIL_ENABLED:
            if is_long:
                stop = max(stop, float(l[j]) - trail_buf)
            else:
                stop = min(stop, float(h[j]) + trail_buf)

    # 5) time stop
    exit_idx = min(last + 1, n - 1)
    px = float(o[exit_idx]) if exit_idx > last else float(c[last])
    leg_r = ((px - entry) / risk) if is_long else ((entry - px) / risk)
    realized += remaining * leg_r
    return {"exit_idx": exit_idx, "exit_price": px, "pnl_R": realized,
            "partial_idx": partial_idx, "partial_price": partial_price,
            "reason": "time_stop"}


# ---------------------------------------------------------------------------
# Main signal loop
# ---------------------------------------------------------------------------

def generate_trades(candles, *, symbol: str = "", session=(SESSION_START_UTC, SESSION_END_UTC)):
    if len(candles) < 400:
        return []

    bars15 = resample(candles, LEVEL_TF_MIN)
    if len(bars15) < 60:
        return []

    _, o15, h15, l15, c15 = _arrays(bars15)
    ts15 = np.array([b.timestamp for b in bars15], dtype=np.int64)
    atr15 = _atr(h15, l15, c15, 14)

    ts, o, h, l, c = _arrays(candles)
    n = len(candles)
    atr = _atr(h, l, c, 14)
    tr = _true_range(h, l, c)
    hours = np.array([datetime.fromtimestamp(int(t), tz=timezone.utc).hour for t in ts], dtype=np.int64)

    levels = build_levels(bars15)
    max_age = int(LEVEL_MAX_AGE_DAYS * 86400)
    tf_sec15 = LEVEL_TF_MIN * 60
    s_start, s_end = session
    session_always = (s_start <= 0 and s_end >= 24)

    trades: list[dict] = []
    lp = 0            # pointer into `levels` (sorted by confirm_ts)
    active: list[dict] = []
    k15 = 0           # last fully-closed 15m bar
    i = 60
    next_ok = i
    progress_step = max(1, n // 20)

    while i < n - 1:
        if i % progress_step == 0:
            emit_progress("scan", 25 + int(70 * i / n), f"S140 bar {i:,}/{n:,}")

        now = int(ts[i]) + 1   # signal read on the close of bar i

        # roll the level book forward (causal)
        while lp < len(levels) and levels[lp]["confirm_ts"] <= now:
            active.append(levels[lp])
            lp += 1
        while active and active[0]["confirm_ts"] < now - max_age:
            active.pop(0)
        while k15 + 1 < len(ts15) and int(ts15[k15 + 1]) + tf_sec15 <= now:
            k15 += 1

        a = float(atr[i])
        a15 = float(atr15[k15]) if k15 < len(atr15) else a
        if i < next_ok or a <= 0 or a15 <= 0 or not active:
            i += 1
            continue
        if not session_always and not (s_start <= int(hours[i]) < s_end):
            i += 1
            continue

        placed = False
        for direction in ("long", "short"):
            lo_scan = max(0, i - PATTERN_LOOKBACK)
            if direction == "long":
                extreme_idx = lo_scan + int(np.argmin(l[lo_scan:i + 1]))
                extreme_px = float(l[extreme_idx])
                level_kind = "low"
                micro_ref = float(np.max(h[max(0, i - MICRO_BREAK_BARS):i]))
                micro_ok = c[i] > micro_ref
            else:
                extreme_idx = lo_scan + int(np.argmax(h[lo_scan:i + 1]))
                extreme_px = float(h[extreme_idx])
                level_kind = "high"
                micro_ref = float(np.min(l[max(0, i - MICRO_BREAK_BARS):i]))
                micro_ok = c[i] < micro_ref

            if i - extreme_idx > MAX_BARS_SINCE_EXTREME or not micro_ok:
                continue

            # Step 1: is the flush extreme sitting on a key 15m level?
            zone = match_zone(active, extreme_px, level_kind, a15)
            if zone is None:
                continue

            # Step 4: unhealthy parabolic approach
            flush = detect_flush(h, l, c, o, tr, extreme_idx, direction, a)
            if flush is None:
                continue

            # Step 5: structure + confirmation candle
            pattern = detect_pattern(h, l, c, extreme_idx, i, direction, zone["price"], a)
            if pattern is None:
                continue
            if not is_confirmation_candle(o, h, l, c, i, direction, a):
                continue

            # Step 6: resting stop order past the confirmation candle
            buf = ENTRY_BUFFER_ATR * a
            trigger = float(h[i] + buf) if direction == "long" else float(l[i] - buf)
            entry_idx, entry = fill_stop_order(o, h, l, i, trigger, direction, n)
            if entry_idx is None:
                continue

            struct_lo = float(np.min(l[extreme_idx:i + 1]))
            struct_hi = float(np.max(h[extreme_idx:i + 1]))
            sl = struct_lo - STOP_BUFFER_ATR * a if direction == "long" else struct_hi + STOP_BUFFER_ATR * a
            risk = (entry - sl) if direction == "long" else (sl - entry)
            cost = round_turn_cost_price(entry, symbol=symbol or None)
            if risk <= 0 or risk > MAX_RISK_ATR * a or risk < 2.0 * cost:
                continue

            res = manage_trade(o, h, l, c, entry_idx, entry, sl, direction, a, n)
            if res is None:
                continue

            tp_final = entry + RR_TARGET * risk if direction == "long" else entry - RR_TARGET * risk
            pnl_R = float(res["pnl_R"])
            outcome = "win" if pnl_R > 1e-9 else "loss" if pnl_R < -1e-9 else "breakeven"

            events = _build_events(
                ts, direction, zone, extreme_idx, extreme_px, flush, pattern, micro_ref,
                i, trigger, entry_idx, entry, sl, tp_final, res, risk, pnl_R,
            )

            trades.append({
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(int(ts[entry_idx])),
                "direction": direction,
                "entry_price": round(float(entry), 6),
                "stop_loss": round(float(sl), 6),
                "take_profit": round(float(tp_final), 6),
                "exit_time": to_iso(int(ts[res["exit_idx"]])),
                "exit_price": round(float(res["exit_price"]), 6),
                "outcome": outcome,
                "pnl_R": round(pnl_R, 3),
                "setup": f"reversal_{pattern}",
                "symbol": symbol or None,
                "level_price": round(zone["price"], 6),
                "level_strength": zone["strength"],
                "level_sources": ",".join(zone["sources"]),
                "flush_atr": round(float(flush["atr_mult"]), 2),
                "flush_efficiency": round(float(flush["efficiency"]), 2),
                "events": events,
                "reason": (
                    f"Pure reversal at key {level_kind.upper()} zone {zone['low']:.5f}-{zone['high']:.5f} "
                    f"(x{zone['strength']} {'/'.join(zone['sources'])}); unhealthy flush "
                    f"{flush['atr_mult']:.1f}xATR eff {flush['efficiency']:.2f} over {flush['bars']} bars; "
                    f"micro trend break + {pattern}; {direction} STOP entry, partial {PARTIAL_R}R "
                    f"({PARTIAL_FRACTION:.0%}) -> BE -> trail, final {RR_TARGET}R; exit {res['reason']}"
                ),
            })

            next_ok = res["exit_idx"] + COOLDOWN_BARS
            i = res["exit_idx"] + 1
            placed = True
            break

        if not placed:
            i += 1

    emit_progress("scan", 97, f"S140 found {len(trades)} trades")
    return trades


def _build_events(ts, direction, zone, extreme_idx, extreme_px, flush, pattern, micro_ref,
                  signal_idx, trigger, entry_idx, entry, sl, tp_final, res, risk, pnl_R):
    side = "support" if direction == "long" else "resistance"
    events = [
        {"timestamp": to_iso(int(ts[max(0, flush["start_idx"])])),
         "type": "liquidity_level_formed",
         "price": round(zone["price"], 6),
         "level": round(zone["price"], 6),
         "high": round(zone["high"], 6),
         "low": round(zone["low"], 6),
         "description": (f"Step 1: key 15m {side} zone {zone['low']:.5f}-{zone['high']:.5f} "
                         f"mapped from {', '.join(zone['sources'])} (x{zone['strength']})")},
        {"timestamp": to_iso(int(ts[extreme_idx])),
         "type": "swing_located",
         "price": round(extreme_px, 6),
         "level": round(extreme_px, 6),
         "description": (f"Step 4: unhealthy {'flush' if direction == 'long' else 'push'} "
                         f"{flush['atr_mult']:.1f}xATR over {flush['bars']} bars, efficiency "
                         f"{flush['efficiency']:.2f} (vertical, no pullbacks) -> exhaustion at "
                         f"{extreme_px:.5f}")},
        {"timestamp": to_iso(int(ts[signal_idx])),
         "type": "mss",
         "price": round(float(micro_ref), 6),
         "level": round(float(micro_ref), 6),
         "description": (f"Step 4: micro trend break — close beyond {micro_ref:.5f} ends the "
                         f"flush's micro trend")},
        {"timestamp": to_iso(int(ts[signal_idx])),
         "type": "entry_trigger",
         "price": round(float(trigger), 6),
         "level": round(float(trigger), 6),
         "description": (f"Step 5+6: {pattern} + confirmation candle; "
                         f"{'BUY' if direction == 'long' else 'SELL'} STOP at {trigger:.5f}")},
        {"timestamp": to_iso(int(ts[entry_idx])),
         "type": "entry_tap",
         "price": round(float(entry), 6),
         "description": (f"Stop order filled at {entry:.5f}; SL {sl:.5f} (1R = {risk:.5f}), "
                         f"final target {tp_final:.5f} ({RR_TARGET}R)")},
    ]
    if res["partial_idx"] is not None:
        events.append({
            "timestamp": to_iso(int(ts[res["partial_idx"]])),
            "type": "partial_take_profit",
            "price": round(float(res["partial_price"]), 6),
            "description": (f"Scaled out {PARTIAL_FRACTION:.0%} at +{PARTIAL_R}R "
                            f"({res['partial_price']:.5f}); stop to break-even, then trailing"),
        })
    events.append({
        "timestamp": to_iso(int(ts[res["exit_idx"]])),
        "type": "final_exit",
        "price": round(float(res["exit_price"]), 6),
        "description": (f"Remainder exited at {res['exit_price']:.5f} via {res['reason']} "
                        f"-> trade {pnl_R:+.2f}R"),
    })
    events.sort(key=lambda e: e["timestamp"])
    return events


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _equity_curve(trades):
    eq = START_EQUITY
    peak = eq
    max_dd = 0.0
    for t in trades:
        eq += eq * RISK_PCT * float(t.get("pnl_R") or 0.0)
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    return eq, max_dd


def run_strategy(candles, output_path, *, symbol: str = "", session=(SESSION_START_UTC, SESSION_END_UTC)):
    trades = generate_trades(candles, symbol=symbol, session=session)
    save_trades(trades, output_path)
    total_r = sum(float(t["pnl_R"]) for t in trades)
    wins = sum(1 for t in trades if t["outcome"] == "win")
    eq, dd = _equity_curve(trades)
    exp = total_r / len(trades) if trades else 0.0
    print(f"Saved {len(trades)} trades to {output_path}")
    if trades:
        print(f"  gross {total_r:+.1f}R | expectancy {exp:+.3f}R/trade | win {wins / len(trades):.1%}")
        print(f"  scaling model: ${START_EQUITY:,.0f} -> ${eq:,.0f} at {RISK_PCT:.0%} risk/trade "
              f"(max DD {dd:.1%})")
    return trades


def main():
    p = argparse.ArgumentParser(description="Strategy 140: Small-Account Reversal at Key S/R")
    p.add_argument("--csv5m", required=True, help="5-minute OHLCV CSV (execution TF; 15m levels are derived)")
    p.add_argument("--output", default=None, help="Output JSON path")
    p.add_argument("--session-start", type=int, default=SESSION_START_UTC, help="UTC hour window start (0 = off)")
    p.add_argument("--session-end", type=int, default=SESSION_END_UTC, help="UTC hour window end (24 = off)")
    args = p.parse_args()

    candles = load_csv(args.csv5m)
    meta = parse_csv_filename(args.csv5m)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_140_results_{sym or 'data'}.json"
    run_strategy(candles, out, symbol=sym, session=(args.session_start, args.session_end))


if __name__ == "__main__":
    main()
