"""
Strategy 143: causal multi-timeframe supply/demand reversal.

ARCHITECTURE
  4H: confirmed pivots -> displacement/BOS -> origin-candle supply/demand.
  1H: fresh first return, liquidity penetration, and rejection close.
  15m: closed directional market-structure shift.
  5m: independent micro-BOS, origin block, and later limit retest.
  Exit: structural 1H stop, fixed 1.5R target, next-bar break-even after +1R.

DEVELOPMENT VERDICT — 2026-08-05
  Eight symbols were evaluated with 5m histories downloaded from the repository's
  Google Drive manifest. The latest 20% remains SEALED and was not evaluated.
  After modeled round-turn costs:
    TRAIN      n=36, win=36.1%, expectancy=-0.090R, PF=0.82
    VALIDATION n=14, win=50.0%, expectancy=+0.227R, PF=1.48
  Small target (1.10/1.25/1.50R) and break-even (0.50/0.75/1.00R)
  neighborhoods produced no setting positive in both train and validation.

STATUS: IMPLEMENTED, DASHBOARD-COMPATIBLE, NOT VALIDATED AS PROFITABLE.
The same ATR/R-normalized parameters apply to every symbol; there are no symbol
exclusions. Results are emitted as gross_R so the dashboard deducts one actual
round-turn cost. Do not open the sealed period unless a future hypothesis is
frozen from train/validation without tuning against sealed data.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Candle, emit_progress, load_csv, parse_csv_filename, resample
from core import round_turn_cost_price, save_trades, to_iso


H4_SECONDS = 4 * 60 * 60
H1_SECONDS = 60 * 60
M15_SECONDS = 15 * 60
M5_SECONDS = 5 * 60


@dataclass(frozen=True)
class Params:
    atr_period: int = 14
    pivot_left: int = 2
    pivot_right: int = 2
    displacement_body_atr: float = 0.80
    displacement_range_atr: float = 1.00
    displacement_close_location: float = 0.72
    origin_lookback: int = 12
    zone_max_age_hours: int = 24 * 45
    rejection_body_atr: float = 0.30
    rejection_range_atr: float = 0.65
    rejection_close_location: float = 0.68
    rejection_wick_atr: float = 0.20
    m15_window_bars: int = 48
    m15_structure_lookback: int = 6
    m15_body_atr: float = 0.45
    m15_range_atr: float = 0.70
    m15_close_location: float = 0.72
    m5_window_bars: int = 72
    m5_structure_lookback: int = 3
    m5_bos_body_atr: float = 0.35
    m5_bos_close_location: float = 0.65
    m5_retest_bars: int = 24
    stop_buffer_atr1: float = 0.10
    min_risk_atr1: float = 0.15
    max_risk_atr1: float = 1.50
    max_cost_risk: float = 0.15
    target_r: float = 1.50
    breakeven_arm_r: float = 1.00
    max_hold_5m_bars: int = 144


@dataclass
class Zone:
    zone_id: int
    direction: int  # +1 demand/long, -1 supply/short
    lower: float
    upper: float
    distal: float
    proximal: float
    active_time: int
    bos_time: int
    origin_time: int
    swing_price: float
    end_time: int
    end_reason: str = "expiry"


@dataclass
class Setup:
    zone: Zone
    reject_idx: int
    reject_time: int
    sweep_extreme: float
    atr1: float
    mss_idx: int
    mss_time: int
    mss_level: float
    exec_lower: float
    exec_upper: float
    confirm_idx: int
    confirm_time: int


@dataclass
class Candidate:
    setup: Setup
    fill_idx: int
    fill_time: int
    entry: float
    stop: float
    risk: float


def _clean_candles(candles: list[Candle], label: str) -> list[Candle]:
    """Sort and deduplicate defensively, then enforce valid strict chronology."""
    if not candles:
        raise ValueError(f"{label} input is empty")
    by_timestamp: dict[int, Candle] = {}
    for candle in candles:
        values = (candle.open, candle.high, candle.low, candle.close)
        if candle.timestamp <= 0 or not all(math.isfinite(v) for v in values):
            raise ValueError(f"{label} contains an invalid timestamp or non-finite OHLC value")
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close, candle.high):
            raise ValueError(f"{label} contains an invalid OHLC envelope at {candle.timestamp}")
        by_timestamp[candle.timestamp] = candle
    ordered = [by_timestamp[ts] for ts in sorted(by_timestamp)]
    timestamps = np.fromiter((c.timestamp for c in ordered), dtype=np.int64)
    if len(timestamps) < 2 or np.any(np.diff(timestamps) <= 0):
        raise ValueError(f"{label} is nonchronological after canonicalization")
    return ordered


def _common_overlap(h4: list[Candle], m5: list[Candle]) -> tuple[list[Candle], list[Candle]]:
    start = max(h4[0].timestamp, m5[0].timestamp)
    end = min(h4[-1].timestamp + H4_SECONDS, m5[-1].timestamp + M5_SECONDS)
    if end <= start:
        raise ValueError("4H and 5m inputs do not overlap")
    h4_out = [c for c in h4 if c.timestamp >= start and c.timestamp + H4_SECONDS <= end]
    m5_out = [c for c in m5 if c.timestamp >= start and c.timestamp + M5_SECONDS <= end]
    if len(h4_out) < 80 or len(m5_out) < 1000 or end - start < 10 * 24 * 60 * 60:
        raise ValueError("insufficient common 4H/5m overlap (need at least 10 days, 80 4H bars, and 1000 5m bars)")
    return h4_out, m5_out


def _complete_resample(m5: list[Candle], minutes: int) -> list[Candle]:
    """Use core.resample, retaining only buckets backed by every expected 5m bar."""
    result = resample(m5, minutes)
    source_ts = np.fromiter((c.timestamp for c in m5), dtype=np.int64)
    required = minutes // 5
    duration = minutes * 60
    complete: list[Candle] = []
    for candle in result:
        left = int(np.searchsorted(source_ts, candle.timestamp, side="left"))
        right = int(np.searchsorted(source_ts, candle.timestamp + duration, side="left"))
        if right - left != required:
            continue
        expected = candle.timestamp + np.arange(required, dtype=np.int64) * M5_SECONDS
        if np.array_equal(source_ts[left:right], expected):
            complete.append(candle)
    return complete


def _arrays(candles: list[Candle]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.fromiter((c.timestamp for c in candles), dtype=np.int64),
        np.fromiter((c.open for c in candles), dtype=float),
        np.fromiter((c.high for c in candles), dtype=float),
        np.fromiter((c.low for c in candles), dtype=float),
        np.fromiter((c.close for c in candles), dtype=float),
    )


def _atr(candles: list[Candle], period: int) -> np.ndarray:
    _, _, high, low, close = _arrays(candles)
    out = np.full(len(candles), np.nan, dtype=float)
    if len(candles) < period:
        return out
    previous = np.r_[close[0], close[:-1]]
    true_range = np.maximum(high - low, np.maximum(np.abs(high - previous), np.abs(low - previous)))
    out[period - 1] = float(np.mean(true_range[:period]))
    for idx in range(period, len(candles)):
        out[idx] = (out[idx - 1] * (period - 1) + true_range[idx]) / period
    return out


def _close_location(candle: Candle, direction: int) -> float:
    span = candle.high - candle.low
    if span <= 0:
        return 0.5
    return (candle.close - candle.low) / span if direction > 0 else (candle.high - candle.close) / span


def _last_opposite(candles: list[Candle], before: int, direction: int, lookback: int) -> Optional[int]:
    floor = max(0, before - lookback)
    for idx in range(before - 1, floor - 1, -1):
        body_direction = 1 if candles[idx].close > candles[idx].open else -1 if candles[idx].close < candles[idx].open else 0
        if body_direction == -direction:
            return idx
    return None


def _build_zones(h4: list[Candle], params: Params) -> list[Zone]:
    _, open_, high, low, close = _arrays(h4)
    atr4 = _atr(h4, params.atr_period)
    latest_high: Optional[int] = None
    latest_low: Optional[int] = None
    broken_high: set[int] = set()
    broken_low: set[int] = set()
    zones: list[Zone] = []
    left, right = params.pivot_left, params.pivot_right

    for idx in range(left + right, len(h4)):
        pivot = idx - right
        if high[pivot] > np.max(high[pivot - left:pivot]) and high[pivot] > np.max(high[pivot + 1:pivot + right + 1]):
            latest_high = pivot
        if low[pivot] < np.min(low[pivot - left:pivot]) and low[pivot] < np.min(low[pivot + 1:pivot + right + 1]):
            latest_low = pivot
        atr_value = atr4[idx]
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        candle = h4[idx]
        body = abs(candle.close - candle.open)
        span = candle.high - candle.low
        strong_common = body >= params.displacement_body_atr * atr_value and span >= params.displacement_range_atr * atr_value
        direction = 0
        swing_idx: Optional[int] = None
        if latest_high is not None and latest_high not in broken_high and candle.close > high[latest_high] and candle.close > candle.open:
            direction, swing_idx = 1, latest_high
        elif latest_low is not None and latest_low not in broken_low and candle.close < low[latest_low] and candle.close < candle.open:
            direction, swing_idx = -1, latest_low
        if not direction or swing_idx is None:
            continue
        if direction > 0:
            broken_high.add(swing_idx)
        else:
            broken_low.add(swing_idx)
        if not strong_common or _close_location(candle, direction) < params.displacement_close_location:
            continue
        origin_idx = _last_opposite(h4, idx, direction, params.origin_lookback)
        if origin_idx is None:
            continue
        origin = h4[origin_idx]
        if direction > 0:
            distal = origin.low
            proximal = max(origin.open, origin.close)
            lower, upper = distal, proximal
            swing_price = float(high[swing_idx])
        else:
            distal = origin.high
            proximal = min(origin.open, origin.close)
            lower, upper = proximal, distal
            swing_price = float(low[swing_idx])
        if not (lower < upper):
            continue
        active_time = candle.timestamp + H4_SECONDS
        zones.append(Zone(
            zone_id=len(zones) + 1,
            direction=direction,
            lower=float(lower),
            upper=float(upper),
            distal=float(distal),
            proximal=float(proximal),
            active_time=active_time,
            bos_time=active_time,
            origin_time=origin.timestamp,
            swing_price=swing_price,
            end_time=active_time + params.zone_max_age_hours * H1_SECONDS,
        ))
    return zones


def _h1_rejection(zone: Zone, candle: Candle, atr_value: float, params: Params) -> bool:
    if not math.isfinite(atr_value) or atr_value <= 0:
        return False
    body = abs(candle.close - candle.open)
    span = candle.high - candle.low
    if span < params.rejection_range_atr * atr_value:
        return False
    if zone.direction > 0:
        wick = min(candle.open, candle.close) - candle.low
        return (
            candle.close > zone.proximal
            and candle.close > candle.open
            and _close_location(candle, 1) >= params.rejection_close_location
            and (body >= params.rejection_body_atr * atr_value or wick >= params.rejection_wick_atr * atr_value)
        )
    wick = candle.high - max(candle.open, candle.close)
    return (
        candle.close < zone.proximal
        and candle.close < candle.open
        and _close_location(candle, -1) >= params.rejection_close_location
        and (body >= params.rejection_body_atr * atr_value or wick >= params.rejection_wick_atr * atr_value)
    )


def _resolve_first_returns(zones: list[Zone], h1: list[Candle], params: Params) -> list[tuple[Zone, int, int, float, float]]:
    close_times = np.fromiter((c.timestamp + H1_SECONDS for c in h1), dtype=np.int64)
    atr1 = _atr(h1, params.atr_period)
    rejected: list[tuple[Zone, int, int, float, float]] = []
    for zone in zones:
        start = int(np.searchsorted(close_times, zone.active_time, side="right"))
        stop = int(np.searchsorted(close_times, zone.end_time, side="right"))
        for idx in range(start, min(stop, len(h1))):
            candle = h1[idx]
            event_time = int(close_times[idx])
            invalid = candle.close < zone.distal if zone.direction > 0 else candle.close > zone.distal
            penetrated = candle.low <= zone.proximal if zone.direction > 0 else candle.high >= zone.proximal
            if invalid:
                zone.end_time, zone.end_reason = event_time, "distal_close"
                break
            if not penetrated:
                continue
            zone.end_time = event_time
            if _h1_rejection(zone, candle, atr1[idx], params):
                zone.end_reason = "qualified_first_return"
                extreme = candle.low if zone.direction > 0 else candle.high
                rejected.append((zone, idx, event_time, float(extreme), float(atr1[idx])))
            else:
                zone.end_reason = "consumed_first_return"
            break
    return rejected


def _find_m15_signal(
    item: tuple[Zone, int, int, float, float],
    m15: list[Candle],
    close_times: np.ndarray,
    atr15: np.ndarray,
    params: Params,
) -> Optional[tuple[int, int, float, float, float]]:
    zone, _, reject_time, _, _ = item
    start = int(np.searchsorted(close_times, reject_time, side="right"))
    end = min(len(m15), start + params.m15_window_bars)
    lookback = params.m15_structure_lookback
    for idx in range(max(start, lookback), end):
        candle = m15[idx]
        atr_value = atr15[idx]
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        body = abs(candle.close - candle.open)
        span = candle.high - candle.low
        if body < params.m15_body_atr * atr_value or span < params.m15_range_atr * atr_value:
            continue
        if _close_location(candle, zone.direction) < params.m15_close_location:
            continue
        if zone.direction > 0:
            level = max(c.high for c in m15[idx - lookback:idx])
            breaks = candle.close > level and candle.close > candle.open
        else:
            level = min(c.low for c in m15[idx - lookback:idx])
            breaks = candle.close < level and candle.close < candle.open
        if not breaks:
            continue
        origin_idx = _last_opposite(m15, idx, zone.direction, params.origin_lookback)
        if origin_idx is None or close_times[origin_idx] <= reject_time:
            continue
        origin = m15[origin_idx]
        if zone.direction > 0:
            lower, upper = origin.low, max(origin.open, origin.close)
        else:
            lower, upper = min(origin.open, origin.close), origin.high
        if lower < upper:
            return idx, int(close_times[idx]), float(level), float(lower), float(upper)
    return None


def _find_candidate(
    partial: tuple[tuple[Zone, int, int, float, float], tuple[int, int, float, float, float]],
    m5: list[Candle],
    close_times: np.ndarray,
    atr5: np.ndarray,
    zones: list[Zone],
    symbol: str,
    params: Params,
) -> Optional[Candidate]:
    rejection, signal = partial
    zone, reject_idx, reject_time, sweep_extreme, atr1_value = rejection
    mss_idx, mss_time, mss_level, _, _ = signal
    start = int(np.searchsorted(close_times, mss_time, side="right"))
    end = min(len(m5), start + params.m5_window_bars)
    lookback = params.m5_structure_lookback

    # First require an independent, closed 5m micro-BOS after the 15m MSS.
    for idx in range(max(start, lookback), end):
        candle = m5[idx]
        atr_value = atr5[idx]
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        body = abs(candle.close - candle.open)
        if body < params.m5_bos_body_atr * atr_value:
            continue
        if zone.direction > 0:
            micro_level = max(c.high for c in m5[idx - lookback:idx])
            confirmed = (candle.close > micro_level and candle.close > candle.open
                         and _close_location(candle, 1) >= params.m5_bos_close_location)
        else:
            micro_level = min(c.low for c in m5[idx - lookback:idx])
            confirmed = (candle.close < micro_level and candle.close < candle.open
                         and _close_location(candle, -1) >= params.m5_bos_close_location)
        if not confirmed:
            continue

        # The last opposite 5m candle is the execution block created by that
        # micro displacement. Entry is a later limit retest, never the BOS bar.
        origin_idx = _last_opposite(m5, idx, zone.direction, params.origin_lookback)
        if origin_idx is None or close_times[origin_idx] <= mss_time:
            continue
        origin = m5[origin_idx]
        if zone.direction > 0:
            exec_lower, exec_upper = float(origin.low), float(max(origin.open, origin.close))
        else:
            exec_lower, exec_upper = float(min(origin.open, origin.close)), float(origin.high)
        if exec_lower >= exec_upper:
            continue

        retest_end = min(len(m5), idx + 1 + params.m5_retest_bars)
        for fill_idx in range(idx + 1, retest_end):
            fill_bar = m5[fill_idx]
            if fill_bar.low > exec_upper or fill_bar.high < exec_lower:
                continue
            if zone.direction > 0:
                entry = min(exec_upper, fill_bar.open) if fill_bar.open <= exec_upper else exec_upper
                stop = sweep_extreme - params.stop_buffer_atr1 * atr1_value
                risk = entry - stop
            else:
                entry = max(exec_lower, fill_bar.open) if fill_bar.open >= exec_lower else exec_lower
                stop = sweep_extreme + params.stop_buffer_atr1 * atr1_value
                risk = stop - entry
            if risk <= 0:
                return None
            risk_atr = risk / atr1_value
            if not (params.min_risk_atr1 <= risk_atr <= params.max_risk_atr1):
                return None
            if round_turn_cost_price(entry, symbol=symbol) / risk > params.max_cost_risk:
                return None
            fill_time = fill_bar.timestamp + M5_SECONDS
            if zone.direction > 0:
                edges = [z.lower - entry for z in zones if z.direction < 0 and z.active_time <= fill_time < z.end_time and z.lower > entry]
            else:
                edges = [entry - z.upper for z in zones if z.direction > 0 and z.active_time <= fill_time < z.end_time and z.upper < entry]
            if edges and min(edges) < params.target_r * risk:
                return None
            setup = Setup(
                zone=zone, reject_idx=reject_idx, reject_time=reject_time,
                sweep_extreme=sweep_extreme, atr1=atr1_value,
                mss_idx=mss_idx, mss_time=mss_time, mss_level=mss_level,
                exec_lower=exec_lower, exec_upper=exec_upper,
                confirm_idx=idx, confirm_time=int(close_times[idx]),
            )
            return Candidate(setup, fill_idx, fill_time, float(entry), float(stop), float(risk))
        return None  # first valid micro-BOS owns the setup; an unfilled block expires
    return None


def _event(event_type: str, timestamp: int, **fields: object) -> dict:
    return {"type": event_type, "timestamp": to_iso(timestamp), **fields}


def _simulate_trade(candidate: Candidate, m5: list[Candle], symbol: str, params: Params) -> dict:
    setup = candidate.setup
    direction = setup.zone.direction
    target = candidate.entry + direction * params.target_r * candidate.risk
    original_stop = candidate.stop
    active_stop = original_stop
    be_from_idx: Optional[int] = None
    last_idx = min(len(m5) - 1, candidate.fill_idx + params.max_hold_5m_bars - 1)
    exit_idx, exit_price, exit_reason = last_idx, m5[last_idx].close, "time_exit"

    for idx in range(candidate.fill_idx, last_idx + 1):
        candle = m5[idx]
        if be_from_idx is not None and idx >= be_from_idx:
            active_stop = candidate.entry
        stop_hit = candle.low <= active_stop if direction > 0 else candle.high >= active_stop
        target_hit = candle.high >= target if direction > 0 else candle.low <= target
        if stop_hit:  # deliberately stop-first when both levels occur in one OHLC bar
            gap_stop = candle.open <= active_stop if direction > 0 else candle.open >= active_stop
            exit_price = min(active_stop, candle.open) if direction > 0 and gap_stop else max(active_stop, candle.open) if direction < 0 and gap_stop else active_stop
            exit_idx, exit_reason = idx, "breakeven" if active_stop == candidate.entry else "stop_loss"
            break
        if target_hit:
            gap_target = candle.open >= target if direction > 0 else candle.open <= target
            exit_price = max(target, candle.open) if direction > 0 and gap_target else min(target, candle.open) if direction < 0 and gap_target else target
            exit_idx, exit_reason = idx, "take_profit"
            break
        favorable_hit = candle.high >= candidate.entry + params.breakeven_arm_r * candidate.risk if direction > 0 else candle.low <= candidate.entry - params.breakeven_arm_r * candidate.risk
        if favorable_hit and be_from_idx is None:
            be_from_idx = idx + 1

    exit_time = m5[exit_idx].timestamp + M5_SECONDS
    gross_r = direction * (float(exit_price) - candidate.entry) / candidate.risk
    zone = setup.zone
    events = [
        _event(
            "order_block",
            zone.active_time,
            timeframe="4h",
            direction="demand" if direction > 0 else "supply",
            upper=zone.upper,
            lower=zone.lower,
            distal=zone.distal,
            proximal=zone.proximal,
            bos_level=zone.swing_price,
            origin_time=to_iso(zone.origin_time),
        ),
        _event(
            "liquidity_sweep",
            setup.reject_time,
            direction="sell_side_swept" if direction > 0 else "buy_side_swept",
            price=setup.sweep_extreme,
            level=zone.proximal,
        ),
        _event(
            "mss",
            setup.mss_time,
            direction="bullish" if direction > 0 else "bearish",
            level=setup.mss_level,
            timeframe="15m",
        ),
        _event(
            "order_block",
            setup.confirm_time,
            direction="demand" if direction > 0 else "supply",
            upper=setup.exec_upper,
            lower=setup.exec_lower,
            timeframe="5m",
        ),
        _event(
            "entry_tap",
            candidate.fill_time,
            direction="long" if direction > 0 else "short",
            price=candidate.entry,
            confirmation_time=to_iso(setup.confirm_time),
            upper=setup.exec_upper,
            lower=setup.exec_lower,
        ),
        _event("final_exit", exit_time, price=float(exit_price), reason=exit_reason, gross_R=float(gross_r)),
    ]
    return {
        "strategy": "strategy_143_mtf_supply_demand_reversal",
        "symbol": symbol,
        "zone_id": zone.zone_id,
        "direction": "long" if direction > 0 else "short",
        "entry_time": to_iso(candidate.fill_time),
        "entry_timestamp": candidate.fill_time,
        "exit_time": to_iso(exit_time),
        "exit_timestamp": exit_time,
        "entry_price": candidate.entry,
        "exit_price": float(exit_price),
        "stop_loss": original_stop,
        "take_profit": float(target),
        "risk_price": candidate.risk,
        "gross_R": float(gross_r),
        "exit_reason": exit_reason,
        "events": events,
    }


def generate_trades(
    candles4h: list[Candle],
    candles5m: list[Candle],
    symbol: str = "UNKNOWN",
    params: Optional[Params] = None,
) -> list[dict]:
    """Generate causal trades from required 4H and 5m candle inputs."""
    settings = params or Params()
    h4 = _clean_candles(candles4h, "4H")
    m5 = _clean_candles(candles5m, "5m")
    h4, m5 = _common_overlap(h4, m5)
    h1 = _complete_resample(m5, 60)
    m15 = _complete_resample(m5, 15)
    if len(h1) < 80 or len(m15) < 320:
        raise ValueError("insufficient complete 1H/15m candles in the common overlap")

    zones = _build_zones(h4, settings)
    if not zones:
        return []
    rejected = _resolve_first_returns(zones, h1, settings)
    m15_close_times = np.fromiter((c.timestamp + M15_SECONDS for c in m15), dtype=np.int64)
    m5_close_times = np.fromiter((c.timestamp + M5_SECONDS for c in m5), dtype=np.int64)
    atr15 = _atr(m15, settings.atr_period)
    atr5 = _atr(m5, settings.atr_period)

    partials: list[tuple[tuple[Zone, int, int, float, float], tuple[int, int, float, float, float]]] = []
    for rejection in rejected:
        signal = _find_m15_signal(rejection, m15, m15_close_times, atr15, settings)
        if signal is not None:
            partials.append((rejection, signal))

    candidates: list[Candidate] = []
    for partial in partials:
        candidate = _find_candidate(partial, m5, m5_close_times, atr5, zones, symbol, settings)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda candidate: (candidate.fill_time, candidate.setup.zone.zone_id))

    trades: list[dict] = []
    last_exit_time = -1
    traded_zones: set[int] = set()
    for candidate in candidates:
        zone_id = candidate.setup.zone.zone_id
        if zone_id in traded_zones or candidate.fill_time <= last_exit_time:
            continue
        trade = _simulate_trade(candidate, m5, symbol, settings)
        trades.append(trade)
        traded_zones.add(zone_id)
        last_exit_time = int(trade["exit_timestamp"])
    emit_progress("strategy", 100, f"S143 generated {len(trades)} trades from {len(zones)} causal zones")
    return trades


def run_strategy(csv4h: str, csv5m: str, output: str) -> list[dict]:
    symbol = os.environ.get("BT_SYMBOL") or parse_csv_filename(csv5m).get("symbol") or "UNKNOWN"
    emit_progress("strategy", 5, "Loading and aligning 4H/5m inputs")
    trades = generate_trades(load_csv(csv4h), load_csv(csv5m), symbol=str(symbol).upper())
    save_trades(trades, output)
    print(f"Saved {len(trades)} trades to {output}")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(description="UNVALIDATED causal 4H/1H/15m/5m supply-demand reversal strategy")
    parser.add_argument("--csv4h", required=True, help="Path to the required 4-hour OHLCV CSV")
    parser.add_argument("--csv5m", required=True, help="Path to the required 5-minute OHLCV CSV")
    parser.add_argument("--output", required=True, help="Path for dashboard-compatible trade JSON output")
    args = parser.parse_args()
    run_strategy(args.csv4h, args.csv5m, args.output)


if __name__ == "__main__":
    main()
