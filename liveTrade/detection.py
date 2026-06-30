"""
Live detection — thin adapter over the VALIDATED Strategy 95 code so live
signals are identical to the backtest. We import the strategy's own functions
(no re-implementation of the edge) and only stage them across timeframes.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

import pandas as pd

# Make the strategies package importable
_THIS = os.path.dirname(os.path.abspath(__file__))
_STRAT_DIR = os.path.dirname(_THIS)
sys.path.insert(0, _STRAT_DIR)
sys.path.insert(0, os.path.join(_STRAT_DIR, "strategy_09_mss_ob_entry"))

import strategy_95_mss_ob_refined as S95  # noqa: E402
from strategy import BiasType  # noqa: E402
from scripts.utils.indicators import detect_mss, Direction  # noqa: E402

# strategy_95 disables logging at import time; re-enable for the live app.
logging.disable(logging.NOTSET)

from config import CONFIG  # noqa: E402

# Apply .env overrides to the strategy module so detection uses the configured values.
S95.PARTIAL_R = CONFIG.partial_r
S95.FINAL_R = CONFIG.final_r
S95.BIAS_TTL_HOURS = CONFIG.bias_ttl_hours
S95.MIN_DISPLACEMENT_PCT = CONFIG.min_displacement_pct
S95.FOREX_SL_BUFFER_PIPS = CONFIG.sl_buffer_pips

STRAT = S95.STRAT
BIAS_TTL_HOURS = CONFIG.bias_ttl_hours
FINAL_R = CONFIG.final_r
PARTIAL_R = CONFIG.partial_r


# ---- Phase 1: 4H bias (liquidity sweep) ------------------------------------
def detect_biases(df4: pd.DataFrame) -> list:
    if df4 is None or len(df4) < 30:
        return []
    return S95._biases(df4)


# ---- Phase 2: 1H MSS (displacement-gated) ----------------------------------
def find_mss(df1: pd.DataFrame, bias, ttl_end):
    if df1 is None or len(df1) < 30:
        return None
    mss_list = detect_mss(df1, lookback=5, require_body_close=True)
    mss = S95._first_mss(mss_list, bias, ttl_end)
    if mss is None:
        return None
    if not S95._displacement_ok(df1, mss.timestamp):
        return None
    return mss


# ---- Phase 3: 15M order block in OTE (informational; tap checked on 5M) -----
def find_ob_zone(df15: pd.DataFrame, bias, mss):
    """Replicates the OB+OTE part of MSSOrderBlockStrategy.find_ob_entry WITHOUT
    requiring a tap, so the 15M cycle can confirm/log that an OB zone exists."""
    if df15 is None or len(df15) < 10:
        return None
    mss_idx = min(df15.index.searchsorted(mss.timestamp), len(df15) - 1)
    lo = max(0, mss_idx - 60)
    seg = df15.iloc[lo:mss_idx + 1]
    if len(seg) < 3:
        return None
    if bias.direction == BiasType.BULLISH:
        swing_low = seg["low"].min(); swing_high = mss.mss.break_price
        if swing_high <= swing_low:
            return None
        rng = swing_high - swing_low
        ote_top = swing_high - STRAT.ote_fib_low * rng
        ote_bottom = swing_high - STRAT.ote_fib_high * rng
        return STRAT._find_ob_in_zone(df15, Direction.BULLISH, mss_idx, ote_top, ote_bottom)
    else:
        swing_high = seg["high"].max(); swing_low = mss.mss.break_price
        if swing_high <= swing_low:
            return None
        rng = swing_high - swing_low
        ote_bottom = swing_low + STRAT.ote_fib_low * rng
        ote_top = swing_low + STRAT.ote_fib_high * rng
        return STRAT._find_ob_in_zone(df15, Direction.BEARISH, mss_idx, ote_top, ote_bottom)


# ---- Phase 4: 5M tap entry (the real trigger, exact backtest function) ------
def find_entry(df15: pd.DataFrame, df5_window: pd.DataFrame, bias, mss):
    """Returns an OBEntrySetup (order_block, entry_price, timestamp=tap time) or None.
    Uses the strategy's own find_ob_entry so the entry exactly matches the backtest."""
    if df15 is None or df5_window is None or len(df5_window) < 3:
        return None
    return STRAT.find_ob_entry(df15, df5_window, bias, mss)


def build_trade(symbol: str, bias, ob, df5: pd.DataFrame):
    """Compute direction / entry / smart-SL / final TP exactly as run_strategy does."""
    direction = "long" if bias.direction == BiasType.BULLISH else "short"
    entry = float(ob.entry_price)
    sl = float(S95._smart_sl(ob.order_block, bias.direction, df5, ob.timestamp, symbol))
    if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
        return None
    risk = abs(entry - sl)
    tp = entry + FINAL_R * risk if direction == "long" else entry - FINAL_R * risk
    return {"direction": direction, "entry": entry, "sl": sl, "tp": tp, "risk": risk}
