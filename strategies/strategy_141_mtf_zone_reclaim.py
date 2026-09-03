#!/usr/bin/env python3
"""
Strategy 141: MTF Demand/Supply Origin Reclaim (HTF bias -> zone -> LTF confirmation)

An original design (not derived from any other strategy in this repo). Three
timeframe degrees, a strict definition of a supply/demand zone, a confirmation
requirement, and a hard minimum-R:R gate so the payoff asymmetry is structural.

WHY THIS SHAPE
--------------
Two things repeatedly survive out-of-sample testing: (a) trading WITH the higher
degree trend, and (b) only accepting trades whose objective is far away relative
to the invalidation point. Everything else here exists to locate a precise,
low-risk place to express those two facts.

Fading a level against the dominant trend is the losing version of "reversal".
The winning version is a LOCAL reversal that is a CONTINUATION of the dominant
trend: price pulls back into the origin of the last impulsive leg and turns.

THE THREE DEGREES (all derived from one CSV, so nothing can desync)
------------------------------------------------------------------
  HTF  (base x 24, e.g. Daily from 1H)   -> directional bias, nothing else
  MID  (base x 4,  e.g. 4H   from 1H)    -> where zones are built
  BASE (the CSV itself, e.g. 1H)         -> confirmation + execution + management

WHAT COUNTS AS A ZONE (this is the part most "supply/demand" code gets wrong)
----------------------------------------------------------------------------
A zone is NOT a swing high/low and NOT a round number. It is the ORIGIN of an
impulsive leg that changed structure. All four conditions must hold on MID:

  1. DISPLACEMENT   a bar whose body >= DISP_ATR * ATR(MID) — real intent.
  2. BREAK OF STRUCTURE  that bar CLOSES beyond the most recent CONFIRMED MID
     swing in the bias direction. A leg that does not break structure is noise.
  3. IMBALANCE      the leg left an unfilled gap (low[j] > high[j-2] for a
     bullish leg). Inefficiency is what makes price come back at all.
  4. ORIGIN CANDLE  the zone is the last OPPOSING candle before the leg — the
     base institutions departed from. Demand = (base.low, max(base.open,
     base.close)). Supply = (min(base.open, base.close), base.high).

  Zones are FRESH ONLY. First touch is the trade; after that the zone is spent.
  A zone dies when price CLOSES through its far side, or when it ages out.

CHRONOLOGY OF ONE TRADE
-----------------------
  HTF bias up -> a fresh MID demand zone exists below -> price taps it ->
  a BASE bar reclaims the zone with a micro break of structure and a strong
  body -> fill at the NEXT bar's open -> stop under the pullback low ->
  target = the leg high the zone launched -> scale out at PARTIAL_R, stop to
  break even, ATR-trail the rest.

THE R:R GATE
------------
The target is not invented: it is the leg high/low that the zone produced, i.e.
a real level price is drawn back toward. If (target - entry) / (entry - stop) is
below MIN_RR, the trade is SKIPPED. This is why the average winner is structurally
larger than the average loser instead of accidentally so.

=============================================================================
VERDICT — READ THIS BEFORE TRADING IT.  NOT VALIDATED AS PROFITABLE.
=============================================================================
The R:R half of the design works exactly as intended and is stable everywhere:

    average winner  1.35R - 1.90R      average loser  0.85R - 0.96R

The DIRECTIONAL half does not. Across 8 instruments (EURUSD back to 1999),
three timeframe stacks (Daily/4H/1H, 1W/1D/4H, 4H/1H/15m) and fair round-turn
costs, the hit rate lands at 28-36% when >~36% is needed to pay for that payoff.
Best pooled result found: -0.068R/trade (4H base, n=157).

Every structural variant was tested and every pooled result was negative:
  target        leg extreme / 1.5R / 2R / 3R      -> -0.068R .. -0.194R
  bias          with-trend / ignore / against     -> -0.125R / -0.038R / -0.060R
  regime        HTF EMA slope / MID structure / both -> -0.072R / -0.145R / -0.027R
  entry         next open / 50% retest / zone edge -> -0.125R / -0.072R / -0.144R
  imbalance     required / not required            -> small n / -0.072R
  invalidation exit  off / on                      -> -0.072R / -0.068R
Leave-one-symbol-out was negative in EVERY cell, and out-of-sample was negative
in almost every cell. Notably the HTF trend filter changes nothing measurable,
which is the tell: the ZONE itself carries no directional information here.

XAUUSD is positive in 8 of 9 variants (best +0.64R, n=17) and USDCHF/USDCAD are
often positive, but n<20 per symbol is not evidence — it is noise, and treating
it as a result is exactly how the overfitting in this repo's history happened.

Reproduce any row above with:
    python scripts/validate_s141.py --tf 4h,1h,15m --sweep "entry_mode=0|1|2"

So: use this as a correct, causal, well-instrumented research chassis for
zone-based ideas. Do not trade it as-is.

CAUSALITY
---------
  - MID pivots need PIVOT_W bars on both sides, so a zone is only visible after
    its confirming bar has closed; a zone activates at its MID bar's close time.
  - HTF bias reads the last FULLY CLOSED HTF bar.
  - Confirmation is read on the CLOSE of a BASE bar; the fill is the NEXT bar's
    open. No same-bar entries.
  - While managing, the stop level in force during bar j was fixed by bar j-1's
    close, and the stop is always checked before the target inside a bar.

Usage:
  python strategy_141_mtf_zone_reclaim.py --csv1h XAUUSD_1h.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timezone

THIS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(THIS)
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

# ---------------------------------------------------------------------------
# Parameters. Deliberately unitless (ATR / R multiples) so nothing is tuned
# per instrument. Every value below is shared by every symbol and timeframe.
# ---------------------------------------------------------------------------
P = {
    # timeframe stack
    "mid_mult": 4,          # MID  = base x 4   (1H -> 4H)
    "htf_mult": 24,         # HTF  = base x 24  (1H -> Daily)

    # HTF bias
    "htf_ema": 20,
    "htf_slope_lb": 5,      # bars back for the slope measurement
    "htf_slope_min": 0.25,  # slope in ATR(HTF) units — skips flat/choppy regimes

    # MID zone construction
    "pivot_w": 1,           # confirmed MID pivot half-width
    "disp_atr": 0.6,        # displacement body in ATR(MID)
    # Requiring the imbalance cuts the sample to ~1/4 with no measurable gain in
    # per-trade quality, so it is off by default. Set to 1 for the concept-pure
    # version (fewer, "cleaner" zones).
    "require_imbalance": 0,
    "base_lookback": 4,     # bars searched backwards for the origin candle
    "zone_max_age_mid": 60, # a zone older than this many MID bars is stale
    "zone_min_height_atr": 0.10,

    # BASE confirmation
    "conf_max_bars": 18,    # confirmation window after a touch of the zone
    "choch_bars": 3,        # micro BOS: close beyond the last N highs/lows
    "conf_body": 0.45,      # confirmation candle body / range
    "max_touches": 2,       # a zone is spent after this many separate touches

    # risk + targets
    #
    # NOTE ON THE PAYOFF: banking half the position at +1R and tightly trailing
    # the rest caps the average winner near 1R while every loser is a full -1R,
    # which throws away the asymmetry the min_rr gate was built to capture. The
    # partial is therefore small and late, and the trail is deliberately loose.
    "sl_buf_atr": 0.25,
    "max_risk_atr": 6.0,    # sanity cap only; min_rr does the real filtering
    "min_rr": 2.0,          # the gate that makes the payoff asymmetric
    "target_r": 0.0,        # >0 = fixed R target instead of the leg extreme
    "bias_mode": 1,         # 1 = with HTF trend, -1 = against it, 0 = ignore bias
    "regime_mode": 1,       # 0 = HTF EMA slope, 1 = MID market structure, 2 = both

    # Entry placement. Chasing the confirmation close buys an extension with a
    # wide stop; a limit back into value fills cheaper and tightens 1R.
    "entry_mode": 1,        # 0 = next open, 1 = 50% of confirmation candle, 2 = zone edge
    "retest_bars": 6,       # limit order lifetime in base bars
    "partial_r": 2.0,       # 0 disables the scale-out entirely
    "partial_frac": 0.34,
    "be_at_r": 1.5,         # move to break-even here (0 disables)
    "trail_atr": 3.5,       # loose chandelier so runners are not clipped
    "max_hold": 40,
    "cooldown": 2,
    "atr_n": 14,

    # The thesis is dead the moment price CLOSES back through the zone. Waiting
    # for the full stop in that case just pays for information already received.
    "inval_exit": 1,
}

START_EQUITY = 10_000.0
RISK_PCT = 0.01

# Funnel counters from the most recent generate_trades() call (diagnostics only).
LAST_DIAG: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

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
    out[:seed] = tr[:seed].mean()
    a = 1.0 / n
    for i in range(seed, len(tr)):
        out[i] = out[i - 1] + a * (tr[i] - out[i - 1])
    return out


def _ema(x, n):
    out = np.empty_like(x)
    if not len(x):
        return out
    k = 2.0 / (n + 1.0)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + k * (x[i] - out[i - 1])
    return out


def _base_minutes(ts):
    if len(ts) < 10:
        return 60
    d = np.diff(ts[:2000])
    d = d[d > 0]
    if not len(d):
        return 60
    return max(1, int(round(float(np.median(d)) / 60.0)))


def _confirmed_pivots(h, l, w):
    """Index -> price of the most recent pivot CONFIRMED strictly before it.
    piv_high[i] is the last swing high usable at bar i (needs w bars after it)."""
    n = len(h)
    piv_high = np.full(n, np.nan)
    piv_low = np.full(n, np.nan)
    last_h = np.nan
    last_l = np.nan
    for i in range(n):
        # a pivot at p is confirmed once bar p+w has closed, i.e. usable at i > p+w
        p = i - w - 1
        if p >= w:
            if all(h[p] > h[p - k] for k in range(1, w + 1)) and \
               all(h[p] > h[p + k] for k in range(1, w + 1)):
                last_h = h[p]
            if all(l[p] < l[p - k] for k in range(1, w + 1)) and \
               all(l[p] < l[p + k] for k in range(1, w + 1)):
                last_l = l[p]
        piv_high[i] = last_h
        piv_low[i] = last_l
    return piv_high, piv_low


# ---------------------------------------------------------------------------
# HTF bias
# ---------------------------------------------------------------------------

def structure_regime(bars_mid, w):
    """Regime from real market structure on MID: +1 when the last two CONFIRMED
    swing highs AND the last two confirmed swing lows are both rising, -1 when
    both are falling, 0 otherwise. Uses only pivots already confirmed at bar i."""
    _, _, h, l, _ = _arrays(bars_mid)
    n = len(h)
    out = np.zeros(n, dtype=np.int8)
    hs: list[float] = []
    ls: list[float] = []
    for i in range(n):
        p = i - w - 1
        if p >= w:
            if all(h[p] > h[p - k] for k in range(1, w + 1)) and \
               all(h[p] > h[p + k] for k in range(1, w + 1)):
                hs.append(float(h[p]))
            if all(l[p] < l[p - k] for k in range(1, w + 1)) and \
               all(l[p] < l[p + k] for k in range(1, w + 1)):
                ls.append(float(l[p]))
        if len(hs) >= 2 and len(ls) >= 2:
            up = hs[-1] > hs[-2] and ls[-1] > ls[-2]
            dn = hs[-1] < hs[-2] and ls[-1] < ls[-2]
            out[i] = 1 if up else (-1 if dn else 0)
    return out


def htf_bias(bars_htf, p):
    ts, _, h, l, c = _arrays(bars_htf)
    atr = _atr(h, l, c, p["atr_n"])
    ema = _ema(c, p["htf_ema"])
    lb = p["htf_slope_lb"]
    bias = np.zeros(len(c), dtype=np.int8)
    for i in range(lb, len(c)):
        if atr[i] <= 0:
            continue
        slope = (ema[i] - ema[i - lb]) / atr[i]
        if c[i] > ema[i] and slope >= p["htf_slope_min"]:
            bias[i] = 1
        elif c[i] < ema[i] and slope <= -p["htf_slope_min"]:
            bias[i] = -1
    return ts, bias


# ---------------------------------------------------------------------------
# MID zones: displacement + BOS + imbalance + origin candle
# ---------------------------------------------------------------------------

def build_zones(bars_mid, p, mid_sec):
    ts, o, h, l, c = _arrays(bars_mid)
    n = len(c)
    atr = _atr(h, l, c, p["atr_n"])
    piv_h, piv_l = _confirmed_pivots(h, l, p["pivot_w"])
    zones = []

    d = {"disp": 0, "disp_bos": 0, "disp_bos_imb": 0}
    for j in range(p["pivot_w"] + 4, n):
        a = atr[j]
        if a <= 0:
            continue
        body = c[j] - o[j]
        if abs(body) >= p["disp_atr"] * a:
            d["disp"] += 1
            up = body > 0
            piv = piv_h[j] if up else piv_l[j]
            if piv == piv and ((c[j] > piv) if up else (c[j] < piv)):
                d["disp_bos"] += 1
                if (l[j] > h[j - 2]) if up else (h[j] < l[j - 2]):
                    d["disp_bos_imb"] += 1

        need_imb = bool(p["require_imbalance"])

        # --- bullish leg -> demand zone -------------------------------------
        if body >= p["disp_atr"] * a and not np.isnan(piv_h[j]) and c[j] > piv_h[j] \
                and (l[j] > h[j - 2] or not need_imb):
            b = None
            for k in range(j - 1, max(-1, j - 1 - p["base_lookback"]), -1):
                if c[k] < o[k]:
                    b = k
                    break
            if b is None:
                b = j - 1
            z_lo, z_hi = float(l[b]), float(max(o[b], c[b]))
            if z_hi - z_lo >= p["zone_min_height_atr"] * a and z_hi < c[j]:
                zones.append({
                    "kind": "demand", "low": z_lo, "high": z_hi,
                    "imb": bool(l[j] > h[j - 2]),
                    "created_ts": int(ts[j]) + mid_sec,
                    "expire_ts": int(ts[j]) + mid_sec * p["zone_max_age_mid"],
                    "mid_idx": j, "bos_level": float(piv_h[j]),
                    "traded": False, "last_tap": -1, "touches": 0,
                })

        # --- bearish leg -> supply zone --------------------------------------
        body = o[j] - c[j]
        if body >= p["disp_atr"] * a and not np.isnan(piv_l[j]) and c[j] < piv_l[j] \
                and (h[j] < l[j - 2] or not need_imb):
            b = None
            for k in range(j - 1, max(-1, j - 1 - p["base_lookback"]), -1):
                if c[k] > o[k]:
                    b = k
                    break
            if b is None:
                b = j - 1
            z_lo, z_hi = float(min(o[b], c[b])), float(h[b])
            if z_hi - z_lo >= p["zone_min_height_atr"] * a and z_lo > c[j]:
                zones.append({
                    "kind": "supply", "low": z_lo, "high": z_hi,
                    "imb": bool(h[j] < l[j - 2]),
                    "created_ts": int(ts[j]) + mid_sec,
                    "expire_ts": int(ts[j]) + mid_sec * p["zone_max_age_mid"],
                    "mid_idx": j, "bos_level": float(piv_l[j]),
                    "traded": False, "last_tap": -1, "touches": 0,
                })

    zones.sort(key=lambda z: z["created_ts"])
    LAST_DIAG.update(d)
    LAST_DIAG["zones"] = len(zones)
    return zones, ts, h, l


# ---------------------------------------------------------------------------
# Trade management
# ---------------------------------------------------------------------------

def _fill_limit(o, h, l, signal_idx, limit, direction, max_bars, n):
    """Resting limit order back into value. Fills at the limit price (or at the
    open when price gapped past it), cancelled after max_bars."""
    for j in range(signal_idx + 1, min(signal_idx + 1 + max_bars, n)):
        if direction == "long" and l[j] <= limit:
            return j, float(min(limit, o[j]))
        if direction == "short" and h[j] >= limit:
            return j, float(max(limit, o[j]))
    return None, None


def manage(o, h, l, c, entry_idx, entry, sl, target, direction, atr_at_entry, p, n,
           zone_lo=None, zone_hi=None):
    is_long = direction == "long"
    risk = (entry - sl) if is_long else (sl - entry)
    if risk <= 0:
        return None

    use_part = p["partial_r"] > 0 and p["partial_frac"] > 0
    tp_part = (entry + p["partial_r"] * risk) if is_long else (entry - p["partial_r"] * risk)
    be_lvl = (entry + p["be_at_r"] * risk) if is_long else (entry - p["be_at_r"] * risk)
    stop = sl
    realized = 0.0
    remaining = 1.0
    part_idx = None
    part_px = None
    be = False
    run_ext = entry
    last = min(n - 1, entry_idx + p["max_hold"])

    for j in range(entry_idx, last + 1):
        if (l[j] <= stop) if is_long else (h[j] >= stop):
            leg = ((stop - entry) / risk) if is_long else ((entry - stop) / risk)
            realized += remaining * leg
            return {"exit_idx": j, "exit_price": float(stop), "pnl_R": realized,
                    "part_idx": part_idx, "part_px": part_px,
                    "reason": "trail_stop" if be else "structure_stop"}

        if use_part and part_idx is None and ((h[j] >= tp_part) if is_long else (l[j] <= tp_part)):
            realized += p["partial_frac"] * p["partial_r"]
            remaining -= p["partial_frac"]
            part_idx, part_px = j, float(tp_part)

        if remaining > 1e-9 and ((h[j] >= target) if is_long else (l[j] <= target)):
            leg = ((target - entry) / risk) if is_long else ((entry - target) / risk)
            realized += remaining * leg
            return {"exit_idx": j, "exit_price": float(target), "pnl_R": realized,
                    "part_idx": part_idx, "part_px": part_px, "reason": "target"}

        if p["be_at_r"] > 0 and not be and ((h[j] >= be_lvl) if is_long else (l[j] <= be_lvl)):
            stop = max(stop, entry) if is_long else min(stop, entry)
            be = True

        # thesis invalidated: price closed back through the zone
        if p["inval_exit"] and not be and zone_lo is not None:
            dead = (c[j] < zone_lo) if is_long else (c[j] > zone_hi)
            if dead:
                px = float(c[j])
                leg = ((px - entry) / risk) if is_long else ((entry - px) / risk)
                realized += remaining * leg
                return {"exit_idx": j, "exit_price": px, "pnl_R": realized,
                        "part_idx": part_idx, "part_px": part_px,
                        "reason": "zone_invalidated"}

        run_ext = max(run_ext, float(h[j])) if is_long else min(run_ext, float(l[j]))
        if be:
            trail = (run_ext - p["trail_atr"] * atr_at_entry) if is_long \
                else (run_ext + p["trail_atr"] * atr_at_entry)
            stop = max(stop, trail) if is_long else min(stop, trail)

    exit_idx = min(last + 1, n - 1)
    px = float(o[exit_idx]) if exit_idx > last else float(c[last])
    leg = ((px - entry) / risk) if is_long else ((entry - px) / risk)
    realized += remaining * leg
    return {"exit_idx": exit_idx, "exit_price": px, "pnl_R": realized,
            "part_idx": part_idx, "part_px": part_px, "reason": "time_stop"}


# ---------------------------------------------------------------------------
# Signal engine
# ---------------------------------------------------------------------------

def generate_trades(candles, *, symbol: str = "", params: dict | None = None):
    p = {**P, **(params or {})}
    if len(candles) < 300:
        return []

    ts_b, o, h, l, c = _arrays(candles)
    n = len(candles)
    base_min = _base_minutes(ts_b)
    mid_min = base_min * p["mid_mult"]
    htf_min = base_min * p["htf_mult"]

    bars_mid = resample(candles, mid_min)
    bars_htf = resample(candles, htf_min)
    if len(bars_mid) < 60 or len(bars_htf) < 40:
        return []

    atr_b = _atr(h, l, c, p["atr_n"])
    zones, ts_m, h_m, l_m = build_zones(bars_mid, p, mid_min * 60)
    ts_h, bias = htf_bias(bars_htf, p)
    struct = structure_regime(bars_mid, p["pivot_w"])
    if not zones:
        return []

    htf_sec = htf_min * 60
    mid_sec = mid_min * 60

    trades: list[dict] = []
    zp = 0                 # pointer into zones (sorted by created_ts)
    live: list[dict] = []  # zones currently in play
    kh = 0                 # last closed HTF bar
    km = 0                 # last closed MID bar
    next_ok = 0
    step = max(1, n // 20)
    dg = {"bars_bias_ok": 0, "taps": 0, "conf_timeout": 0, "confirmed": 0,
          "rej_risk": 0, "rej_rr": 0, "rej_nofill": 0, "zone_broken": 0,
          "zone_expired": 0}

    for i in range(p["atr_n"] + 2, n - 1):
        if i % step == 0:
            emit_progress("scan", 25 + int(70 * i / n), f"S141 bar {i:,}/{n:,}")
        now = int(ts_b[i]) + 1

        while zp < len(zones) and zones[zp]["created_ts"] <= now:
            live.append(zones[zp])
            zp += 1
        while kh + 1 < len(ts_h) and int(ts_h[kh + 1]) + htf_sec <= now:
            kh += 1
        while km + 1 < len(ts_m) and int(ts_m[km + 1]) + mid_sec <= now:
            km += 1

        # retire dead zones
        # Retire dead zones and record touches. Touching a zone is a FACT about
        # price, so it is recorded whatever the bias is doing; the bias only gates
        # whether we are allowed to act on it.
        if live:
            keep = []
            for z in live:
                if z["traded"]:
                    continue
                if now > z["expire_ts"]:
                    dg["zone_expired"] += 1
                    continue
                if z["kind"] == "demand" and c[i - 1] < z["low"]:
                    dg["zone_broken"] += 1
                    continue          # closed through the far side -> zone broken
                if z["kind"] == "supply" and c[i - 1] > z["high"]:
                    dg["zone_broken"] += 1
                    continue
                touched = (l[i] <= z["high"]) if z["kind"] == "demand" else (h[i] >= z["low"])
                if touched:
                    if z["last_tap"] < 0 or i - z["last_tap"] > 1:
                        z["touches"] += 1
                        dg["taps"] += 1
                    z["last_tap"] = i
                keep.append(z)
            live = keep

        a = float(atr_b[i])
        if not live or a <= 0 or i < next_ok:
            continue

        rm = p["regime_mode"]
        b_htf = int(bias[kh]) if kh < len(bias) else 0
        b_str = int(struct[km]) if km < len(struct) else 0
        if rm == 1:
            b = b_str
        elif rm == 2:
            b = b_htf if b_htf == b_str else 0
        else:
            b = b_htf
        mode = p["bias_mode"]
        if mode != 0 and b == 0:
            continue
        dg["bars_bias_ok"] += 1
        if mode == 0:
            wanted = ("demand", "supply")
        elif mode > 0:
            wanted = ("demand",) if b > 0 else ("supply",)
        else:
            wanted = ("supply",) if b > 0 else ("demand",)

        for z in live:
            if z["kind"] not in wanted:
                continue
            want = z["kind"]

            t = z["last_tap"]
            if t < 0 or i - t > p["conf_max_bars"]:
                continue              # not in a reaction window
            if z["touches"] > p["max_touches"]:
                continue              # zone no longer fresh enough

            # --- confirmation on the BASE timeframe ------------------------
            rng = float(h[i] - l[i])
            if rng <= 0:
                continue
            body_ok = abs(float(c[i] - o[i])) / rng >= p["conf_body"]
            cb = p["choch_bars"]
            if want == "demand":
                ok = (c[i] > o[i] and body_ok and c[i] > z["high"]
                      and c[i] > float(np.max(h[max(0, i - cb):i])))
            else:
                ok = (c[i] < o[i] and body_ok and c[i] < z["low"]
                      and c[i] < float(np.min(l[max(0, i - cb):i])))
            if not ok:
                continue
            dg["confirmed"] += 1

            direction = "long" if want == "demand" else "short"
            em = p["entry_mode"]
            if em == 0:
                entry_idx = i + 1
                entry = float(o[entry_idx])
            else:
                if em == 2:
                    limit = z["high"] if direction == "long" else z["low"]
                else:
                    limit = float(l[i] + (h[i] - l[i]) * 0.5)
                entry_idx, entry = _fill_limit(o, h, l, i, limit, direction,
                                              p["retest_bars"], n)
                if entry_idx is None:
                    dg["rej_nofill"] += 1
                    continue

            # The stop belongs under the REACTION low (the low printed while price
            # was working the zone), not under the whole pre-tap swing — measuring
            # it over a wide window inflates risk and rejects most valid setups.
            swing = float(np.min(l[t:i + 1])) if direction == "long" \
                else float(np.max(h[t:i + 1]))
            if direction == "long":
                sl = min(swing, z["low"]) - p["sl_buf_atr"] * a
                target = float(np.max(h_m[z["mid_idx"]:km + 1])) if km >= z["mid_idx"] else np.nan
                risk = entry - sl
                reward = (target - entry) if target == target else -1.0
            else:
                sl = max(swing, z["high"]) + p["sl_buf_atr"] * a
                target = float(np.min(l_m[z["mid_idx"]:km + 1])) if km >= z["mid_idx"] else np.nan
                risk = sl - entry
                reward = (entry - target) if target == target else -1.0

            cost = round_turn_cost_price(entry, symbol=symbol or None)
            if risk <= 0 or risk > p["max_risk_atr"] * a or risk < 2.0 * cost:
                dg["rej_risk"] += 1
                continue
            if reward / risk < p["min_rr"]:      # the R:R gate
                dg["rej_rr"] += 1
                continue
            if p["target_r"] > 0:                # clip the objective to a fixed R
                target = (entry + p["target_r"] * risk) if direction == "long" \
                    else (entry - p["target_r"] * risk)
                reward = p["target_r"] * risk

            res = manage(o, h, l, c, entry_idx, entry, sl, target, direction, a, p, n,
                         zone_lo=z["low"], zone_hi=z["high"])
            z["traded"] = True
            if res is None:
                continue

            pnl_R = float(res["pnl_R"])
            outcome = "win" if pnl_R > 1e-9 else "loss" if pnl_R < -1e-9 else "breakeven"
            trades.append({
                "trade_number": len(trades) + 1,
                "entry_time": to_iso(int(ts_b[entry_idx])),
                "direction": direction,
                "entry_price": round(entry, 6),
                "stop_loss": round(float(sl), 6),
                "take_profit": round(float(target), 6),
                "exit_time": to_iso(int(ts_b[res["exit_idx"]])),
                "exit_price": round(float(res["exit_price"]), 6),
                "outcome": outcome,
                "pnl_R": round(pnl_R, 3),
                "setup": f"{z['kind']}_origin_reclaim",
                "symbol": symbol or None,
                "planned_rr": round(reward / risk, 2),
                "zone_low": round(z["low"], 6),
                "zone_high": round(z["high"], 6),
                "events": _events(ts_b, ts_m, z, t, i, entry_idx, entry, sl, target,
                                  direction, res, risk, pnl_R, reward / risk, mid_min, htf_min),
                "reason": (
                    f"{htf_min // 60 if htf_min >= 60 else htf_min}"
                    f"{'h' if htf_min >= 60 else 'm'} bias {'up' if b > 0 else 'down'} + fresh "
                    f"{mid_min // 60 if mid_min >= 60 else mid_min}"
                    f"{'h' if mid_min >= 60 else 'm'} {z['kind']} origin "
                    f"{z['low']:.5f}-{z['high']:.5f} (BOS {z['bos_level']:.5f}) + base reclaim "
                    f"CHoCH; planned {reward / risk:.1f}R to leg extreme {target:.5f}; "
                    f"partial {p['partial_r']}R -> BE -> {p['trail_atr']}xATR trail; exit {res['reason']}"
                ),
            })
            next_ok = res["exit_idx"] + p["cooldown"]
            break

    dg["trades"] = len(trades)
    dg["bars"] = n
    LAST_DIAG.update(dg)
    emit_progress("scan", 97, f"S141 found {len(trades)} trades")
    return trades


def _events(ts_b, ts_m, z, tap_idx, conf_idx, entry_idx, entry, sl, target,
            direction, res, risk, pnl_R, rr, mid_min, htf_min):
    mid_lbl = f"{mid_min // 60}h" if mid_min >= 60 else f"{mid_min}m"
    ev = [
        {"timestamp": to_iso(int(ts_m[z["mid_idx"]])), "type": "order_block",
         "upper": round(z["high"], 6), "lower": round(z["low"], 6),
         "direction": "bullish" if direction == "long" else "bearish",
         "description": (f"{mid_lbl} {z['kind']} origin {z['low']:.5f}-{z['high']:.5f}: "
                         f"displacement closed through structure {z['bos_level']:.5f}"
                         + (" and left an imbalance behind it" if z.get("imb")
                            else " (no unfilled gap left behind)"))},
        {"timestamp": to_iso(int(ts_m[z["mid_idx"]])), "type": "bos_level_located",
         "price": round(z["bos_level"], 6), "level": round(z["bos_level"], 6),
         "description": f"{mid_lbl} break of structure at {z['bos_level']:.5f} validates the zone"},
        {"timestamp": to_iso(int(ts_b[tap_idx])), "type": "entry_tap",
         "price": round(z["high"] if direction == "long" else z["low"], 6),
         "description": "First touch of the fresh zone — pullback into the origin"},
        {"timestamp": to_iso(int(ts_b[conf_idx])), "type": "mss",
         "price": round(float(entry), 6), "level": round(float(entry), 6),
         "description": ("Base-timeframe confirmation: zone reclaimed with a micro break of "
                         "structure and a strong body")},
        {"timestamp": to_iso(int(ts_b[entry_idx])), "type": "entry_trigger",
         "price": round(float(entry), 6), "level": round(float(entry), 6),
         "description": (f"{direction.upper()} filled at next open {entry:.5f}; stop {sl:.5f} "
                         f"(1R={risk:.5f}); target {target:.5f} = {rr:.1f}R")},
    ]
    if res["part_idx"] is not None:
        ev.append({"timestamp": to_iso(int(ts_b[res["part_idx"]])),
                   "type": "partial_take_profit", "price": round(float(res["part_px"]), 6),
                   "description": "Scaled out at +1R, stop to break-even, ATR trail armed"})
    ev.append({"timestamp": to_iso(int(ts_b[res["exit_idx"]])), "type": "final_exit",
               "price": round(float(res["exit_price"]), 6),
               "description": f"Exit via {res['reason']} -> trade {pnl_R:+.2f}R"})
    ev.sort(key=lambda e: e["timestamp"])
    return ev


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def summarize(trades):
    if not trades:
        return {}
    rs = [float(t["pnl_R"]) for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    eq, peak, dd = START_EQUITY, START_EQUITY, 0.0
    for r in rs:
        eq += eq * RISK_PCT * r
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    gp = sum(wins)
    gl = -sum(losses)
    return {
        "trades": len(rs),
        "total_R": sum(rs),
        "expectancy_R": sum(rs) / len(rs),
        "win_rate": len(wins) / len(rs),
        "avg_win_R": (gp / len(wins)) if wins else 0.0,
        "avg_loss_R": (gl / len(losses)) if losses else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else float("inf"),
        "equity": eq,
        "max_dd": dd,
    }


def run_strategy(candles, output_path, *, symbol: str = "", params: dict | None = None):
    trades = generate_trades(candles, symbol=symbol, params=params)
    save_trades(trades, output_path)
    s = summarize(trades)
    print(f"Saved {len(trades)} trades to {output_path}")
    if s:
        print(f"  {s['total_R']:+.1f}R total | {s['expectancy_R']:+.3f}R/trade | "
              f"win {s['win_rate']:.1%} | PF {s['profit_factor']:.2f} | "
              f"avg win {s['avg_win_R']:.2f}R vs avg loss {s['avg_loss_R']:.2f}R")
        print(f"  ${START_EQUITY:,.0f} -> ${s['equity']:,.0f} at {RISK_PCT:.0%}/1R "
              f"(max DD {s['max_dd']:.1%})")
    return trades


def main():
    ap = argparse.ArgumentParser(description="Strategy 141: MTF Demand/Supply Origin Reclaim")
    ap.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV (4H zones and Daily bias are derived)")
    ap.add_argument("--output", default=None, help="Output JSON path")
    args = ap.parse_args()

    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_141_results_{sym or 'data'}.json"
    run_strategy(candles, out, symbol=sym)


if __name__ == "__main__":
    main()
