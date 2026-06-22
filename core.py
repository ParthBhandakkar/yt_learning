"""
core.py - Shared analysis utilities for all ICT/Orderflow trading strategies.
Provides pure functions operating on OHLCV data loaded from CSV.
"""

import csv
import json
import math
from datetime import datetime, timezone
from typing import NamedTuple, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Candle(NamedTuple):
    time_utc: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: int


def load_csv(path: str) -> list[Candle]:
    candles = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(Candle(
                time_utc=row["time_utc"],
                timestamp=int(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["tick_volume"]),
            ))
    return candles


def to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def find_candle_idx(candles: list[Candle], ts: int) -> int:
    for i, c in enumerate(candles):
        if c.timestamp >= ts:
            return i
    return len(candles) - 1


# ---------------------------------------------------------------------------
# Resample – build higher-timeframe candles from lower-timeframe ones
# ---------------------------------------------------------------------------

def resample(candles: list[Candle], minutes: int) -> list[Candle]:
    if not candles:
        return []
    grouped: list[Candle] = []
    secs = minutes * 60
    start_ts = candles[0].timestamp
    group: list[Candle] = []
    for c in candles:
        if c.timestamp < start_ts + secs:
            group.append(c)
        else:
            if group:
                grouped.append(_merge_group(group))
            start_ts = start_ts + secs
            while c.timestamp >= start_ts + secs:
                start_ts += secs
            group = [c]
    if group:
        grouped.append(_merge_group(group))
    return grouped


def _merge_group(group: list[Candle]) -> Candle:
    ot = group[0].open
    cl = group[-1].close
    h = max(c.high for c in group)
    lw = min(c.low for c in group)
    v = sum(c.volume for c in group)
    return Candle(
        time_utc=group[0].time_utc,
        timestamp=group[0].timestamp,
        open=ot, high=h, low=lw, close=cl, volume=v,
    )


# ---------------------------------------------------------------------------
# Swing High / Swing Low  (3-candle fractal)
# ---------------------------------------------------------------------------

def swing_highs(candles: list[Candle]) -> list[int]:
    idxs = []
    for i in range(1, len(candles) - 1):
        if candles[i].high > candles[i - 1].high and candles[i].high > candles[i + 1].high:
            idxs.append(i)
    return idxs


def swing_lows(candles: list[Candle]) -> list[int]:
    idxs = []
    for i in range(1, len(candles) - 1):
        if candles[i].low < candles[i - 1].low and candles[i].low < candles[i + 1].low:
            idxs.append(i)
    return idxs


def nearest_swing_high_left(candles: list[Candle], idx: int) -> Optional[int]:
    for i in range(idx - 1, 0, -1):
        if candles[i].high > candles[i - 1].high and candles[i].high > candles[i + 1].high:
            return i
    return None


def nearest_swing_low_left(candles: list[Candle], idx: int) -> Optional[int]:
    for i in range(idx - 1, 0, -1):
        if candles[i].low < candles[i - 1].low and candles[i].low < candles[i + 1].low:
            return i
    return None


# ---------------------------------------------------------------------------
# Market Structure Shift (MSS)
# ---------------------------------------------------------------------------

def detect_mss(candles: list[Candle], lookback: int = 5) -> list[dict]:
    """Return list of MSS events: {idx, direction, swing_idx, swing_type}"""
    events = []
    for i in range(lookback, len(candles)):
        swing = nearest_swing_high_left(candles, i)
        if swing is not None:
            if candles[i].close > candles[swing].high:
                events.append({"idx": i, "direction": "bullish", "swing_idx": swing, "swing_type": "high"})
        swing = nearest_swing_low_left(candles, i)
        if swing is not None:
            if candles[i].close < candles[swing].low:
                events.append({"idx": i, "direction": "bearish", "swing_idx": swing, "swing_type": "low"})
    return events


# ---------------------------------------------------------------------------
# Fair Value Gap (FVG)
# ---------------------------------------------------------------------------

def detect_fvg(candles: list[Candle]) -> list[dict]:
    """Return list of FVGs: {idx, type, upper, lower, direction}"""
    fvgs = []
    for i in range(1, len(candles) - 1):
        prev, cur, nxt = candles[i - 1], candles[i], candles[i + 1]
        # Bullish FVG: nxt.low > prev.high
        gap_low = nxt.low - prev.high
        if gap_low > 0:
            fvgs.append({
                "idx": i,
                "direction": "bullish",
                "upper": nxt.low,
                "lower": prev.high,
                "gap": gap_low,
            })
        # Bearish FVG: nxt.high < prev.low
        gap_high = prev.low - nxt.high
        if gap_high > 0:
            fvgs.append({
                "idx": i,
                "direction": "bearish",
                "upper": prev.low,
                "lower": nxt.high,
                "gap": gap_high,
            })
    return fvgs


def fvg_contains_price(fvg: dict, price: float) -> bool:
    return fvg["lower"] < price < fvg["upper"]


def is_fvg_mitigated(candles: list[Candle], fvg_idx: int, direction: str, up_to: int) -> bool:
    if direction == "bullish":
        for i in range(fvg_idx + 1, min(up_to + 1, len(candles))):
            if candles[i].low <= candles[fvg_idx - 1].high:
                return True
    else:
        for i in range(fvg_idx + 1, min(up_to + 1, len(candles))):
            if candles[i].high >= candles[fvg_idx - 1].low:
                return True
    return False


# ---------------------------------------------------------------------------
# Inversion FVG (iFVG) – price closes past the gap boundary
# ---------------------------------------------------------------------------

def detect_ifvg(candles: list[Candle], fvg: dict) -> Optional[dict]:
    """Return inversion event if price inverts an FVG"""
    idx = fvg["idx"]
    direction = fvg["direction"]
    for i in range(idx + 1, len(candles)):
        c = candles[i]
        if direction == "bearish":
            if c.close > fvg["upper"]:
                return {"idx": i, "fvg_idx": idx, "direction": "bullish_ifvg", "price": c.close, "boundary": fvg["upper"]}
            if c.high > fvg["upper"]:
                return {"idx": i, "fvg_idx": idx, "direction": "bullish_ifvg", "price": c.high, "boundary": fvg["upper"]}
        else:
            if c.close < fvg["lower"]:
                return {"idx": i, "fvg_idx": idx, "direction": "bearish_ifvg", "price": c.close, "boundary": fvg["lower"]}
            if c.low < fvg["lower"]:
                return {"idx": i, "fvg_idx": idx, "direction": "bearish_ifvg", "price": c.low, "boundary": fvg["lower"]}
    return None


# ---------------------------------------------------------------------------
# Order Block (OB)
# ---------------------------------------------------------------------------

def detect_order_blocks(candles: list[Candle], lookahead: int = 3) -> list[dict]:
    """Return order blocks: {idx, direction, strength}"""
    obs = []
    for i in range(len(candles) - lookahead):
        move = sum(1 for j in range(1, lookahead + 1) if candles[i + j].close > candles[i + j].open)
        if move >= lookahead - 1:
            obs.append({"idx": i, "direction": "bullish", "open": candles[i].open, "close": candles[i].close})
        move = sum(1 for j in range(1, lookahead + 1) if candles[i + j].close < candles[i + j].open)
        if move >= lookahead - 1:
            obs.append({"idx": i, "direction": "bearish", "open": candles[i].open, "close": candles[i].close})
    return obs


# ---------------------------------------------------------------------------
# Breaker Block (failed OB)
# ---------------------------------------------------------------------------

def detect_breaker_blocks(candles: list[Candle], lookahead: int = 3) -> list[dict]:
    """A breaker block is an OB that later gets fully mitigated."""
    obs = detect_order_blocks(candles, lookahead)
    breakers = []
    for ob in obs:
        ob_high = max(ob["open"], ob["close"])
        ob_low = min(ob["open"], ob["close"])
        for j in range(ob["idx"] + lookahead, len(candles)):
            if ob["direction"] == "bullish" and candles[j].close < ob_low:
                breakers.append({**ob, "breaker_idx": j, "type": "breaker"})
                break
            elif ob["direction"] == "bearish" and candles[j].close > ob_high:
                breakers.append({**ob, "breaker_idx": j, "type": "breaker"})
                break
    return breakers


# ---------------------------------------------------------------------------
# CISD – Change in State of Delivery
# ---------------------------------------------------------------------------

def detect_cisd(candles: list[Candle], lookback: int = 5) -> list[dict]:
    """A candle closes past the body of a prior candle in a directional run."""
    events = []
    for i in range(lookback, len(candles)):
        for j in range(max(0, i - lookback), i):
            if candles[j].close > candles[j].open:
                if candles[i].close < candles[j].open:
                    events.append({"idx": i, "direction": "bearish", "reference_idx": j})
                    break
            else:
                if candles[i].close > candles[j].close:
                    events.append({"idx": i, "direction": "bullish", "reference_idx": j})
                    break
    return events


# ---------------------------------------------------------------------------
# Liquidity sweep detection
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(candles: list[Candle], swing_idx: int, sweep_idx: int) -> Optional[dict]:
    """Check if price swept a swing high/low level"""
    sw = candles[swing_idx]
    sc = candles[sweep_idx]
    if sc.high > sw.high and sweep_idx > swing_idx:
        return {"swing_idx": swing_idx, "sweep_idx": sweep_idx, "level": sw.high, "direction": "buy_side_swept"}
    if sc.low < sw.low and sweep_idx > swing_idx:
        return {"swing_idx": swing_idx, "sweep_idx": sweep_idx, "level": sw.low, "direction": "sell_side_swept"}
    return None


# ---------------------------------------------------------------------------
# Fibonacci utilities
# ---------------------------------------------------------------------------

def fib_retracement(high: float, low: float, level: float) -> float:
    return high - (high - low) * level


def fib_extension(high: float, low: float, level: float) -> float:
    return low + (high - low) * level


# ---------------------------------------------------------------------------
# Volume Profile (VAH, VAL, POC)
# ---------------------------------------------------------------------------

class VolumeProfile(NamedTuple):
    vah: float
    val: float
    poc: float
    total_volume: int


def compute_volume_profile(candles: list[Candle], value_area_pct: float = 0.70) -> VolumeProfile:
    if not candles:
        return VolumeProfile(0, 0, 0, 0)
    total_vol = sum(c.volume for c in candles)
    if total_vol == 0:
        return VolumeProfile(
            vah=max(c.high for c in candles),
            val=min(c.low for c in candles),
            poc=0,
            total_volume=0,
        )
    # Build price bins (rounded to 5th decimal for forex)
    price_bins: dict[float, int] = {}
    for c in candles:
        # Approximate volume distribution across the range
        rng = c.high - c.low
        if rng == 0:
            key = round(c.close, 5)
            price_bins[key] = price_bins.get(key, 0) + c.volume
        else:
            # Distribute volume across price levels
            steps = max(1, int(rng / 0.0001))
            for s in range(steps + 1):
                price = c.low + (rng * s / steps)
                key = round(price, 5)
                price_bins[key] = price_bins.get(key, 0) + c.volume // (steps + 1)
    if not price_bins:
        flat = [c.close for c in candles]
        return VolumeProfile(max(flat), min(flat), flat[len(flat) // 2], total_vol)
    prices = sorted(price_bins.keys())
    poc = max(price_bins, key=price_bins.get)
    # Find value area (70% of volume around POC)
    sorted_by_vol = sorted(price_bins.items(), key=lambda x: x[1], reverse=True)
    target_vol = total_vol * value_area_pct
    cum_vol = 0
    va_prices = []
    for price, vol in sorted_by_vol:
        cum_vol += vol
        va_prices.append(price)
        if cum_vol >= target_vol:
            break
    return VolumeProfile(
        vah=max(va_prices),
        val=min(va_prices),
        poc=poc,
        total_volume=total_vol,
    )


# ---------------------------------------------------------------------------
# Resample intra-minute data from 1m candles
# ---------------------------------------------------------------------------

def candles_for_time_range(candles: list[Candle], start_ts: int, end_ts: int) -> list[Candle]:
    return [c for c in candles if start_ts <= c.timestamp < end_ts]


# ---------------------------------------------------------------------------
# Find highest-timeframe FVG inside a candle-list
# (checks multiple timeframe multiples)
# ---------------------------------------------------------------------------

def find_highest_tf_ifvg(
    candles_1m: list[Candle], target_indices: list[int]
) -> Optional[dict]:
    """
    From the 1m manipulation leg, check 1m→2m→3m→4m→5m for an unmitigated FVG.
    Return the highest confirmed FVG and its inversion event.
    """
    for multiple in [5, 4, 3, 2, 1]:
        tf_candles = resample(candles_1m, multiple)
        fvgs = detect_fvg(tf_candles)
        # map target indices to tf
        if not fvgs:
            continue
        for fvg in fvgs:
            inv = detect_ifvg(tf_candles, fvg)
            if inv:
                return {
                    "timeframe": f"{multiple}m",
                    "fvg": fvg,
                    "inversion": inv,
                }
    return None


# ---------------------------------------------------------------------------
# Save output JSON
# ---------------------------------------------------------------------------

def save_trades(trades: list[dict], output_path: str):
    with open(output_path, "w") as f:
        json.dump(trades, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Parse CSV filename for metadata
# ---------------------------------------------------------------------------

import re

def parse_csv_filename(path: str) -> dict:
    base = path.rsplit("/", 1)[-1].replace(".csv", "")
    parts = base.split("_")
    # pattern: SYMBOL_TIMEFRAME_START_END
    # Timeframe is like 1h, 15m, 5m, 4h, 1d
    symbol = parts[0]
    tf = parts[1]
    start_date = parts[2]
    end_date = parts[3] if len(parts) > 3 else ""
    return {"symbol": symbol, "timeframe": tf, "start_date": start_date, "end_date": end_date}
