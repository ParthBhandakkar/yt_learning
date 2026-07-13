"""
Live detection adapter for Strategy 98 (XAUUSD trend + liquidity + ATR trail).

Reuses the exact signal logic from strategy_98_xau_trend_liquidity_trail.py on the
last CLOSED 1H bar only. Entry in live is at market when the signal bar has closed
(backtest equivalent: next 1H open).
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

_THIS = os.path.dirname(os.path.abspath(__file__))
_STRAT_DIR = os.path.dirname(_THIS)
sys.path.insert(0, _STRAT_DIR)

from core import Candle, round_turn_cost_price  # noqa: E402
from strategy_98_xau_trend_liquidity_trail import (  # noqa: E402
    _atr,
    _ema,
    _htf_bias_array,
    _last_before,
    _ny_hour,
    _swing_levels,
)
from config import CONFIG  # noqa: E402


def df_to_candles(df: pd.DataFrame) -> list[Candle]:
    candles: list[Candle] = []
    for idx, row in df.iterrows():
        ts = int(pd.Timestamp(idx).timestamp())
        candles.append(
            Candle(
                time_utc=pd.Timestamp(idx).isoformat(),
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0)),
            )
        )
    return candles


def detect_signal(df1h: pd.DataFrame) -> Optional[dict]:
    """Return an entry signal on the last closed 1H bar, or None."""
    if df1h is None or len(df1h) < 80:
        return None

    candles = df_to_candles(df1h)
    n = len(candles)
    htf_ema = CONFIG.s98_htf_ema
    range_lookback = CONFIG.s98_range_lookback
    donchian = CONFIG.s98_donchian
    atr_len = CONFIG.s98_atr_len
    atr_mult_init = CONFIG.s98_atr_mult_init
    sweep_buffer_frac = 0.0003
    use_pd_filter = CONFIG.s98_use_pd_filter
    also_breakout = CONFIG.s98_also_breakout
    session_filter = CONFIG.s98_session_filter
    min_cost_mult = 4.0

    need = max(htf_ema * 4, range_lookback, donchian, atr_len) + 10
    if n < need + 1:
        return None

    ts = np.array([c.timestamp for c in candles], dtype=np.int64)
    o = np.array([c.open for c in candles], dtype=np.float64)
    h = np.array([c.high for c in candles], dtype=np.float64)
    l = np.array([c.low for c in candles], dtype=np.float64)
    c = np.array([c.close for c in candles], dtype=np.float64)

    bias = _htf_bias_array(candles, htf_ema)
    atr_arr = _atr(h, l, c, atr_len)
    ema1 = _ema(c, htf_ema)
    sh_idx, sl_idx = _swing_levels(h, l)

    i = n - 1
    b = int(bias[i])
    if b == 0:
        return None

    if session_filter:
        hr = _ny_hour(int(ts[i]))
        if hr < 3 or hr >= 16:
            return None

    rng_hi = h[i - range_lookback:i].max()
    rng_lo = l[i - range_lookback:i].min()
    mid = (rng_hi + rng_lo) / 2.0

    direction = None
    sweep_extreme = None
    setup = None

    if b == 1:
        sl_i = _last_before(sl_idx, 1, i)
        if sl_i is not None:
            level = l[sl_i]
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
        return None

    a = float(atr_arr[i])
    if a <= 0:
        return None

    entry_price = float(c[i])
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
        return None

    return {
        "direction": direction,
        "setup": setup,
        "signal_time": pd.Timestamp(df1h.index[i]).isoformat(),
        "entry_ref": entry_price,
        "sl": float(stop),
        "risk": float(risk),
        "atr": a,
        "atr_mult_trail": CONFIG.s98_atr_mult_trail,
        "bias": b,
    }
