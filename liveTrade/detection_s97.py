"""
Live detection adapter for Strategy 97 (generic with-trend mean-reversion, 4H).

Reuses the EXACT indicator math and entry rule from
strategy_97_trend_meanreversion.py, evaluated on the last CLOSED 4H bar only.

Backtest entry: signal read on close of bar i, fill at open of bar i+1.
Live entry:     signal read on close of bar i, fill at market immediately after
                the bar closes (~= open of i+1). Same risk distance (K_SL * ATR[i]).

Exit is handled in trade_manager_s97.py (fixed ATR stop on the broker, dynamic
mean-revert TP recomputed each closed bar, and a max-hold time stop) — identical
to the backtest's stop / mean_revert / time_stop logic.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

_THIS = os.path.dirname(os.path.abspath(__file__))
_STRAT_DIR = os.path.dirname(_THIS)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

# Reuse the backtest's own ATR so live == backtest to the last decimal.
from strategy_97_trend_meanreversion import _atr as _bt_atr  # noqa: E402

from config import CONFIG  # noqa: E402


def _indicators(df: pd.DataFrame):
    """ATR, SMA, EMA computed exactly like the backtest (all causal, <= i)."""
    a = _bt_atr(df, CONFIG.s97_atr_n)
    c = df["close"].values
    sma = pd.Series(c).rolling(CONFIG.s97_sma_n).mean().values
    ema = pd.Series(c).ewm(span=CONFIG.s97_trend_ema, adjust=False).mean().values
    return a, sma, ema


def detect_signal(df4h: pd.DataFrame) -> Optional[dict]:
    """Return an entry signal on the last CLOSED 4H bar, or None.

    df4h must contain only CLOSED candles (the mt5 client already drops the
    forming bar), indexed by UTC timestamp, columns open/high/low/close.
    """
    if df4h is None:
        return None
    n = len(df4h)
    if n < CONFIG.s97_trend_ema + 5:
        return None

    a, sma, ema = _indicators(df4h)

    i = n - 1  # last closed bar
    if i < CONFIG.s97_trend_ema + 1:
        return None
    if a[i] <= 0 or np.isnan(sma[i]) or np.isnan(ema[i]):
        return None

    c_i = float(df4h["close"].iloc[i])
    z = (c_i - sma[i]) / a[i]
    z_entry = CONFIG.s97_z_entry

    long_sig = (z <= -z_entry) and (c_i > ema[i])
    short_sig = (z >= z_entry) and (c_i < ema[i])
    if not (long_sig or short_sig):
        return None

    direction = "long" if long_sig else "short"
    risk = CONFIG.s97_k_sl * float(a[i])
    if risk <= 0:
        return None

    # entry_ref = signal-bar close (backtest fills next-bar open; live fills at
    # market right after close). Stop is anchored to the actual fill in the engine.
    entry_ref = c_i
    stop_ref = entry_ref - risk if direction == "long" else entry_ref + risk

    # Initial mean-revert target for the first in-trade bar uses the signal bar's
    # SMA/ATR (backtest: sma[entry_idx-1], a[entry_idx-1] with entry_idx = i+1).
    z_exit = CONFIG.s97_z_exit
    if direction == "long":
        tp0 = float(sma[i]) - z_exit * float(a[i])
    else:
        tp0 = float(sma[i]) + z_exit * float(a[i])

    return {
        "strategy": "s97",
        "direction": direction,
        "setup": "with_trend_mean_reversion",
        "signal_time": pd.Timestamp(df4h.index[i]).isoformat(),
        "entry_ref": entry_ref,
        "sl": float(stop_ref),
        "risk": float(risk),
        "tp0": tp0,
        "atr": float(a[i]),
        "sma": float(sma[i]),
        "entry_z": float(z),
    }


def mean_revert_target(df4h: pd.DataFrame, direction: str) -> Optional[float]:
    """Mean-revert TP to use for the NEXT bar, from the LAST closed bar's
    SMA/ATR — matches the backtest's use of prior-bar indicators intrabar."""
    if df4h is None or len(df4h) < CONFIG.s97_sma_n + 2:
        return None
    a, sma, _ = _indicators(df4h)
    last = len(df4h) - 1
    if np.isnan(sma[last]) or a[last] <= 0:
        return None
    z_exit = CONFIG.s97_z_exit
    if direction == "long":
        return float(sma[last]) - z_exit * float(a[last])
    return float(sma[last]) + z_exit * float(a[last])
