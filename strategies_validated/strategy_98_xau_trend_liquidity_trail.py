#!/usr/bin/env python3
"""
Strategy 98: XAUUSD Unified Trend + Liquidity + ATR Trail (Exness-calibrated)

Built from batch XAUUSD winners/losers:
  KEEP (winners):
    - 4H EMA bias + 1H liquidity sweep/reclaim + premium/discount (s91)
    - ATR chandelier trail so winners run (s90) instead of capping at fixed 2R
    - Next-bar open entry, one position, fully causal HTF completion
  DISCARD (losers):
    - 1m/5m ICT scalps with tiny stops vs gold spread
    - Fixed 2R targets that cut runners
    - Session Judas/US30 logic transplanted onto gold without HTF filter
    - Ultra-high trade-count mean-rev without trend alignment

TF stack: 4H bias (completed bars) -> 1H trigger -> fill at next 1H open.
Risk: initial stop beyond sweep extreme (ATR floor); trail = chandelier ATR.
Costs: deducted downstream via core.enrich_trades_pnl (Exness ~$0.40 RT).

Usage:
  python strategy_96_xau_trend_liquidity_trail.py --csv1h XAUUSD_1h.csv [--output out.json]
  python strategy_96_xau_trend_liquidity_trail.py --csv1h XAUUSD_1h.csv --also-breakout
"""

from __future__ import annotations

import argparse
import bisect
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from core import (
    EXNESS_XAUUSD_PIP,
    load_csv,
    parse_csv_filename,
    resample,
    round_turn_cost_price,
    save_trades,
    to_iso,
)


def _ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.empty_like(values)
    k = 2.0 / (length + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    out = np.empty(n)
    out[0] = tr[0]
    k = 1.0 / length
    for i in range(1, n):
        out[i] = tr[i] * k + out[i - 1] * (1 - k)
    return out


def _swing_levels(h: np.ndarray, l: np.ndarray):
    sh_idx, sl_idx = [], []
    for i in range(1, len(h) - 1):
        if h[i] > h[i - 1] and h[i] > h[i + 1]:
            sh_idx.append(i)
        if l[i] < l[i - 1] and l[i] < l[i + 1]:
            sl_idx.append(i)
    return sh_idx, sl_idx


def _last_before(sorted_idx: list, confirm_offset: int, i: int):
    pos = bisect.bisect_right(sorted_idx, i - confirm_offset) - 1
    return sorted_idx[pos] if pos >= 0 else None


def _htf_bias_array(candles_1h, htf_ema: int) -> np.ndarray:
    """+1 / -1 / 0 from completed 4H EMA only (no same-bar HTF peek)."""
    ts1 = np.array([c.timestamp for c in candles_1h])
    bias = np.zeros(len(candles_1h), dtype=np.int8)
    bars4 = resample(candles_1h, 240)
    if len(bars4) < htf_ema + 2:
        return bias
    c4 = np.array([c.close for c in bars4])
    ema4 = _ema(c4, htf_ema)
    end4 = [c.timestamp + 240 * 60 for c in bars4]
    sign4 = np.where(c4 > ema4, 1, np.where(c4 < ema4, -1, 0)).astype(np.int8)
    for i in range(len(candles_1h)):
        decision_t = int(ts1[i]) + 3600
        k = bisect.bisect_right(end4, decision_t) - 1
        if k >= 0:
            bias[i] = sign4[k]
    return bias


def _ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def generate_trades(
    candles_1h,
    *,
    htf_ema: int = 50,
    range_lookback: int = 20,
    donchian: int = 20,
    atr_len: int = 14,
    atr_mult_init: float = 1.5,
    atr_mult_trail: float = 3.0,
    sweep_buffer_frac: float = 0.0003,
    use_pd_filter: bool = True,
    also_breakout: bool = True,
    session_filter: bool = False,
    min_cost_mult: float = 4.0,
):
    """Causal trade generator. Returns trade dicts (no file IO)."""
    n = len(candles_1h)
    need = max(htf_ema * 4, range_lookback, donchian, atr_len) + 10
    if n < need:
        return []

    ts = np.array([c.timestamp for c in candles_1h], dtype=np.int64)
    o = np.array([c.open for c in candles_1h], dtype=np.float64)
    h = np.array([c.high for c in candles_1h], dtype=np.float64)
    l = np.array([c.low for c in candles_1h], dtype=np.float64)
    c = np.array([c.close for c in candles_1h], dtype=np.float64)

    bias = _htf_bias_array(candles_1h, htf_ema)
    atr_arr = _atr(h, l, c, atr_len)
    ema1 = _ema(c, htf_ema)
    sh_idx, sl_idx = _swing_levels(h, l)

    trades = []
    i = max(range_lookback, donchian, atr_len) + 2
    while i < n - 1:
        b = int(bias[i])
        if b == 0:
            i += 1
            continue

        if session_filter:
            # London open through NY afternoon (gold's most liquid hours, NY time).
            hr = _ny_hour(int(ts[i]))
            if hr < 3 or hr >= 16:
                i += 1
                continue

        rng_hi = h[i - range_lookback:i].max()
        rng_lo = l[i - range_lookback:i].min()
        mid = (rng_hi + rng_lo) / 2.0

        direction = None
        sweep_extreme = None
        setup = None

        # Primary: liquidity sweep + reclaim with candle close confirmation.
        if b == 1:
            sl = _last_before(sl_idx, 1, i)
            if sl is not None:
                level = l[sl]
                if l[i] < level and c[i] > level and c[i] > o[i]:
                    if (not use_pd_filter) or (l[i] <= mid):
                        direction = "long"
                        sweep_extreme = float(l[i])
                        setup = "liq_reclaim"
        elif b == -1:
            shx = _last_before(sh_idx, 1, i)
            if shx is not None:
                level = h[shx]
                if h[i] > level and c[i] < level and c[i] < o[i]:
                    if (not use_pd_filter) or (h[i] >= mid):
                        direction = "short"
                        sweep_extreme = float(h[i])
                        setup = "liq_reclaim"

        # Secondary: Donchian breakout with same-TF EMA + 4H bias (s90 edge).
        if direction is None and also_breakout:
            prior_hh = h[i - donchian:i].max()
            prior_ll = l[i - donchian:i].min()
            if b == 1 and c[i] > prior_hh and c[i] > ema1[i]:
                direction = "long"
                sweep_extreme = float(prior_ll)
                setup = "donchian_breakout"
            elif b == -1 and c[i] < prior_ll and c[i] < ema1[i]:
                direction = "short"
                sweep_extreme = float(prior_hh)
                setup = "donchian_breakout"

        if direction is None:
            i += 1
            continue

        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_price = float(o[entry_idx])
        a = float(atr_arr[i])
        if a <= 0:
            i += 1
            continue

        cost = round_turn_cost_price(entry_price)
        min_risk = max(min_cost_mult * cost, 0.5 * a)

        if direction == "long":
            structural = sweep_extreme * (1.0 - sweep_buffer_frac)
            atr_stop = entry_price - atr_mult_init * a
            stop = min(structural, atr_stop)
            risk = entry_price - stop
            if risk < min_risk:
                stop = entry_price - min_risk
                risk = min_risk
        else:
            structural = sweep_extreme * (1.0 + sweep_buffer_frac)
            atr_stop = entry_price + atr_mult_init * a
            stop = max(structural, atr_stop)
            risk = stop - entry_price
            if risk < min_risk:
                stop = entry_price + min_risk
                risk = min_risk

        if risk <= 0:
            i += 1
            continue

        init_stop = stop
        trail = stop
        extreme = float(h[entry_idx]) if direction == "long" else float(l[entry_idx])

        exit_idx = None
        exit_price = None
        for j in range(entry_idx, n):
            if direction == "long":
                if l[j] <= trail:
                    exit_idx, exit_price = j, trail
                    break
                extreme = max(extreme, float(h[j]))
                trail = max(trail, extreme - atr_mult_trail * float(atr_arr[j]))
            else:
                if h[j] >= trail:
                    exit_idx, exit_price = j, trail
                    break
                extreme = min(extreme, float(l[j]))
                trail = min(trail, extreme + atr_mult_trail * float(atr_arr[j]))

        if exit_idx is None:
            exit_idx = n - 1
            exit_price = float(c[exit_idx])
            outcome = "open"
        else:
            outcome = (
                "win"
                if (
                    (direction == "long" and exit_price > entry_price)
                    or (direction == "short" and exit_price < entry_price)
                )
                else "loss"
            )

        risk_exness = risk / EXNESS_XAUUSD_PIP
        trades.append({
            "trade_number": len(trades) + 1,
            "entry_time": to_iso(int(ts[entry_idx])),
            "direction": direction,
            "entry_price": round(entry_price, 5),
            "stop_loss": round(float(init_stop), 5),
            "take_profit": round(float(exit_price), 5),
            "exit_time": to_iso(int(ts[exit_idx])),
            "exit_price": round(float(exit_price), 5),
            "outcome": outcome,
            "setup": setup,
            "risk_price": round(float(risk), 5),
            "risk_exness_pips": round(float(risk_exness), 1),
            "reason": (
                f"4H bias + 1H {setup} + ATR trail x{atr_mult_trail} "
                f"(Exness pip={EXNESS_XAUUSD_PIP})"
            ),
        })
        i = exit_idx + 1
    return trades


def run_strategy(candles_1h, output_path, **kw):
    trades = generate_trades(candles_1h, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(
        description="Strategy 98: XAUUSD Trend + Liquidity + ATR Trail"
    )
    parser.add_argument("--csv1h", required=True, help="1-hour OHLCV CSV")
    parser.add_argument("--output", default=None)
    parser.add_argument("--htf-ema", type=int, default=50)
    parser.add_argument("--atr-trail", type=float, default=3.0)
    parser.add_argument("--atr-init", type=float, default=1.5)
    parser.add_argument(
        "--also-breakout",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include Donchian breakout entries (default: on)",
    )
    parser.add_argument(
        "--session-filter",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Restrict signals to London–NY hours (default: off for max returns)",
    )
    parser.add_argument("--no-pd-filter", action="store_true")
    args = parser.parse_args()

    candles = load_csv(args.csv1h)
    meta = parse_csv_filename(args.csv1h)
    out = args.output or f"strategy_98_results_{meta['symbol']}.json"
    run_strategy(
        candles,
        out,
        htf_ema=args.htf_ema,
        atr_mult_trail=args.atr_trail,
        atr_mult_init=args.atr_init,
        also_breakout=args.also_breakout,
        session_filter=args.session_filter,
        use_pd_filter=not args.no_pd_filter,
    )


if __name__ == "__main__":
    main()
