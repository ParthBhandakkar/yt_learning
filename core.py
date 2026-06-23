"""
core.py - Shared analysis utilities for all ICT/Orderflow trading strategies.
Provides pure functions operating on OHLCV data loaded from CSV.
"""

import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
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


def _norm_header(name: str) -> str:
    return re.sub(r"[\s\-\.]+", "_", name.strip().lower())


_OHLC_ALIASES = {
    "open": ("open", "o", "op", "bid_open", "ask_open"),
    "high": ("high", "h", "hi"),
    "low": ("low", "l", "lo"),
    "close": ("close", "c", "cl", "last", "price"),
    "volume": ("tick_volume", "tickvolume", "volume", "vol", "v", "tick_vol", "tickvol"),
    "time_utc": ("time_utc", "datetime", "date_time", "timestamp_utc", "gmt_time", "utc"),
    "time": ("time", "timestamp", "unix", "epoch", "ts", "time_unix"),
    "date": ("date", "dt", "day"),
}


def _resolve_column(headers: list[str], aliases: tuple[str, ...]) -> Optional[str]:
    normalized = {_norm_header(h): h for h in headers}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def detect_csv_columns(headers: list[str]) -> dict[str, Optional[str]]:
    """Map logical OHLCV fields to actual CSV header names."""
    if not headers:
        raise ValueError("CSV has no header row")

    mapping: dict[str, Optional[str]] = {"time_part": None}
    for field, aliases in _OHLC_ALIASES.items():
        mapping[field] = _resolve_column(headers, aliases)

    # MT4/MT5 style: separate Date + Time columns (Time is HH:MM:SS, not unix)
    if mapping["date"] and mapping["time"] and mapping["time_utc"] is None:
        if _norm_header(mapping["time"]) == "time":
            mapping["time_part"] = mapping["time"]
            mapping["time"] = None

    missing = [f for f in ("open", "high", "low", "close") if not mapping[f]]
    if missing:
        raise ValueError(
            f"Could not detect required OHLC columns ({', '.join(missing)}). "
            f"Found headers: {headers}"
        )

    has_time = mapping["time"] or mapping["time_utc"] or mapping["date"]
    if not has_time:
        raise ValueError(
            "Could not detect a time column. Expected unix timestamp, datetime, "
            f"or separate date/time columns. Found headers: {headers}"
        )

    return mapping


def _parse_timestamp(row: dict[str, str], cols: dict[str, Optional[str]]) -> tuple[int, str]:
    if cols.get("time_utc"):
        raw = row[cols["time_utc"]].strip()
        if re.match(r"^-?\d+(\.\d+)?$", raw):
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000
            ts_int = int(ts)
            return ts_int, to_iso(ts_int)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts_int = int(dt.timestamp())
        return ts_int, dt.astimezone(timezone.utc).isoformat()

    if cols.get("time"):
        raw = row[cols["time"]].strip()
        if not raw:
            raise ValueError("Empty timestamp value")
        if re.match(r"^-?\d+(\.\d+)?$", raw):
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000
            ts_int = int(ts)
            return ts_int, to_iso(ts_int)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts_int = int(dt.timestamp())
        return ts_int, dt.astimezone(timezone.utc).isoformat()

    date_raw = row[cols["date"]].strip()  # type: ignore[index]
    date_raw = date_raw.replace(".", "-")
    time_raw = row[cols["time_part"]].strip() if cols.get("time_part") else "00:00:00"  # type: ignore[index]
    combined = f"{date_raw} {time_raw}"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            dt = datetime.strptime(combined, fmt).replace(tzinfo=timezone.utc)
            ts_int = int(dt.timestamp())
            return ts_int, dt.isoformat()
        except ValueError:
            continue
    raise ValueError(f"Could not parse date/time: {combined!r}")


def _parse_volume(row: dict[str, str], cols: dict[str, Optional[str]]) -> int:
    vol_col = cols.get("volume")
    if not vol_col:
        return 0
    raw = row.get(vol_col, "").strip()
    if not raw:
        return 0
    return int(float(raw))


def load_csv(path: str) -> list[Candle]:
    candles: list[Candle] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return candles
        cols = detect_csv_columns(list(reader.fieldnames))
        for row in reader:
            ts, time_utc = _parse_timestamp(row, cols)
            candles.append(Candle(
                time_utc=time_utc,
                timestamp=ts,
                open=float(row[cols["open"]]),  # type: ignore[index]
                high=float(row[cols["high"]]),  # type: ignore[index]
                low=float(row[cols["low"]]),  # type: ignore[index]
                close=float(row[cols["close"]]),  # type: ignore[index]
                volume=_parse_volume(row, cols),
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
    """Swing at i is valid only after bar i+1 has closed (i+1 <= idx)."""
    upper = min(idx - 1, len(candles) - 2)
    for i in range(upper, 0, -1):
        if candles[i].high > candles[i - 1].high and candles[i].high > candles[i + 1].high:
            return i
    return None


def nearest_swing_low_left(candles: list[Candle], idx: int) -> Optional[int]:
    upper = min(idx - 1, len(candles) - 2)
    for i in range(upper, 0, -1):
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
    """Return inversion event when price CLOSES past the gap boundary (no wick peeking)."""
    idx = fvg["idx"]
    direction = fvg["direction"]
    for i in range(idx + 1, len(candles)):
        c = candles[i]
        if direction == "bearish" and c.close > fvg["upper"]:
            return {
                "idx": i,
                "fvg_idx": idx,
                "direction": "bullish_ifvg",
                "price": c.close,
                "boundary": fvg["upper"],
            }
        if direction == "bullish" and c.close < fvg["lower"]:
            return {
                "idx": i,
                "fvg_idx": idx,
                "direction": "bearish_ifvg",
                "price": c.close,
                "boundary": fvg["lower"],
            }
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
# PnL helpers
# ---------------------------------------------------------------------------

def infer_pip_size(price: float) -> float:
    """Guess pip size from price level (indices, metals, JPY, standard forex)."""
    if price >= 1000:
        return 1.0
    if price >= 100:
        return 0.1
    if price >= 10:
        return 0.01
    return 0.0001


def trade_pnl_pips(trade: dict) -> float:
    """Return trade PnL in pips, computing from prices when not stored."""
    stored = trade.get("pnl_pips")
    if stored is not None:
        return float(stored)

    entry = trade.get("entry_price")
    exit_p = trade.get("exit_price")
    if entry is None or exit_p is None:
        return 0.0

    ref = float(entry)
    pip_size = infer_pip_size(ref)
    direction = (trade.get("direction") or "").lower()
    if direction == "long":
        raw = float(exit_p) - ref
    elif direction == "short":
        raw = ref - float(exit_p)
    else:
        return 0.0
    return round(raw / pip_size, 1)


def enrich_trades_pnl(trades: list[dict]) -> list[dict]:
    """Fill missing pnl_pips on each trade from entry/exit prices."""
    for trade in trades:
        if trade.get("pnl_pips") is None:
            trade["pnl_pips"] = trade_pnl_pips(trade)
    return trades


# ---------------------------------------------------------------------------
# Save output JSON
# ---------------------------------------------------------------------------

def save_trades(trades: list[dict], output_path: str):
    with open(output_path, "w") as f:
        json.dump(trades, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Parse CSV filename for metadata
# ---------------------------------------------------------------------------

def parse_csv_filename(path: str) -> dict:
    base = Path(path).stem
    parts = base.split("_")
    # pattern: SYMBOL_TIMEFRAME_START_END (e.g. XAUUSD_5m_2021-01-01_2024-01-01)
    symbol = parts[0] if parts else "UNKNOWN"
    tf = parts[1] if len(parts) > 1 else "unknown"
    start_date = parts[2] if len(parts) > 2 else ""
    end_date = parts[3] if len(parts) > 3 else ""
    return {"symbol": symbol, "timeframe": tf, "start_date": start_date, "end_date": end_date}
