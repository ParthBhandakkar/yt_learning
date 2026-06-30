"""
core.py - Shared analysis utilities for all ICT/Orderflow trading strategies.
Provides pure functions operating on OHLCV data loaded from CSV.
"""

import csv
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import numpy as np

from fast_core import (
    arrays_from_candles,
    detect_mss_arrays,
    index_at_or_after_ts,
    resample_arrays,
    simulate_exits_arrays,
    swing_high_indices,
    swing_low_indices,
)


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


def emit_progress(phase: str, pct: int, message: str = "") -> None:
    """Emit a machine-readable progress line for the dashboard backtest runner."""
    if os.environ.get("BT_PROGRESS") != "1":
        return
    safe = message.replace("\n", " ").strip()
    print(f"BT_PROGRESS {phase} {max(0, min(100, pct))} {safe}", flush=True)


def load_csv(path: str) -> list[Candle]:
    candles: list[Candle] = []
    report = os.environ.get("BT_PROGRESS") == "1"
    label = Path(path).name
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return candles
        cols = detect_csv_columns(list(reader.fieldnames))
        for row_num, row in enumerate(reader, 1):
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
            if report and row_num % 100000 == 0:
                emit_progress("load_csv", min(24, row_num // 100000), f"Loading {label}: {row_num:,} rows")
    if report:
        emit_progress("load_csv", 25, f"Loaded {label}: {len(candles):,} candles")
    return candles


def to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class CandleSeries:
    """Cached NumPy OHLCV arrays for fast index lookups and Numba kernels."""

    __slots__ = ("candles", "ts", "open", "high", "low", "close", "volume")

    def __init__(self, candles: list[Candle]):
        self.candles = candles
        if candles:
            self.ts, self.open, self.high, self.low, self.close, self.volume = arrays_from_candles(candles)
        else:
            empty_i = np.empty(0, dtype=np.int64)
            empty_f = np.empty(0, dtype=np.float64)
            self.ts = empty_i
            self.open = empty_f
            self.high = empty_f
            self.low = empty_f
            self.close = empty_f
            self.volume = empty_i

    def at_or_after(self, ts: int) -> int:
        return index_at_or_after_ts(self.ts, ts)

    def at_exact(self, ts: int) -> int:
        idx = self.at_or_after(ts)
        if idx < len(self.candles) and self.candles[idx].timestamp == ts:
            return idx
        return -1


def candle_series(candles: list[Candle]) -> CandleSeries:
    return CandleSeries(candles)


def index_at_or_after(candles: list[Candle], ts: int) -> int:
    """First index where candle timestamp >= ts (O(log n))."""
    lo, hi = 0, len(candles)
    while lo < hi:
        mid = (lo + hi) // 2
        if candles[mid].timestamp < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def index_through_ts(candles: list[Candle], ts: int) -> int:
    """Exclusive end index for candles with timestamp <= ts."""
    lo, hi = 0, len(candles)
    while lo < hi:
        mid = (lo + hi) // 2
        if candles[mid].timestamp <= ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def advance_index_at_or_after(candles: list[Candle], ts: int, start: int = 0) -> int:
    """Monotonic timestamp search — O(1) amortized when ts only increases."""
    i = max(0, start)
    while i < len(candles) and candles[i].timestamp < ts:
        i += 1
    return i


def find_candle_idx(candles: list[Candle], ts: int) -> int:
    idx = index_at_or_after(candles, ts)
    return idx if idx < len(candles) else len(candles) - 1


# ---------------------------------------------------------------------------
# Resample – build higher-timeframe candles from lower-timeframe ones
# ---------------------------------------------------------------------------

def resample(candles: list[Candle], minutes: int) -> list[Candle]:
    if not candles:
        return []
    ts, o, h, l, c, v = arrays_from_candles(candles)
    r_ts, r_o, r_h, r_l, r_c, r_v = resample_arrays(ts, o, h, l, c, v, minutes)
    out: list[Candle] = []
    for i in range(len(r_ts)):
        out.append(Candle(
            time_utc=to_iso(int(r_ts[i])),
            timestamp=int(r_ts[i]),
            open=float(r_o[i]),
            high=float(r_h[i]),
            low=float(r_l[i]),
            close=float(r_c[i]),
            volume=int(r_v[i]),
        ))
    return out


# ---------------------------------------------------------------------------
# Swing High / Swing Low  (3-candle fractal)
# ---------------------------------------------------------------------------

def swing_highs(candles: list[Candle]) -> list[int]:
    if not candles:
        return []
    _, _, h, _, _, _ = arrays_from_candles(candles)
    return [int(i) for i in swing_high_indices(h)]


def swing_lows(candles: list[Candle]) -> list[int]:
    if not candles:
        return []
    _, _, _, l, _, _ = arrays_from_candles(candles)
    return [int(i) for i in swing_low_indices(l)]


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
    if len(candles) <= lookback:
        return []
    _, _, h, l, _, c = arrays_from_candles(candles)
    return detect_mss_arrays(c, h, l, lookback)


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


def compute_volume_profile(
    candles: list[Candle],
    value_area_pct: float = 0.70,
    num_bins: int = 48,
    end_idx: Optional[int] = None,
) -> VolumeProfile:
    """Build VAH/VAL/POC from fixed session bins (works for forex and gold)."""
    if end_idx is not None:
        if end_idx < 0 or not candles:
            return VolumeProfile(0, 0, 0, 0)
        last = min(end_idx, len(candles) - 1)
        candle_list = [candles[i] for i in range(last + 1)]
    else:
        if not candles:
            return VolumeProfile(0, 0, 0, 0)
        candle_list = candles

    if not candle_list:
        return VolumeProfile(0, 0, 0, 0)

    total_vol = sum(c.volume for c in candle_list)
    session_lo = min(c.low for c in candle_list)
    session_hi = max(c.high for c in candle_list)
    session_rng = session_hi - session_lo

    if total_vol == 0:
        mid = candle_list[len(candle_list) // 2].close
        return VolumeProfile(session_hi, session_lo, mid, 0)

    if session_rng == 0:
        flat = round(session_lo, 5)
        return VolumeProfile(session_hi, session_lo, flat, total_vol)

    bin_size = session_rng / num_bins
    bins = [0] * num_bins

    for c in candle_list:
        if c.high <= c.low:
            idx = min(num_bins - 1, max(0, int((c.close - session_lo) / bin_size)))
            bins[idx] += c.volume
            continue

        start_bin = max(0, int((c.low - session_lo) / bin_size))
        end_bin = min(num_bins - 1, int((c.high - session_lo) / bin_size))
        span = end_bin - start_bin + 1
        base = c.volume // span
        extra = c.volume - base * span
        for offset in range(span):
            bins[start_bin + offset] += base + (1 if offset < extra else 0)

    if not any(bins):
        flat = [c.close for c in candle_list]
        return VolumeProfile(max(flat), min(flat), flat[len(flat) // 2], total_vol)

    bin_prices = [round(session_lo + (i + 0.5) * bin_size, 5) for i in range(num_bins)]
    poc_idx = max(range(num_bins), key=lambda i: bins[i])
    poc = bin_prices[poc_idx]

    sorted_by_vol = sorted(enumerate(bins), key=lambda x: x[1], reverse=True)
    target_vol = total_vol * value_area_pct
    cum_vol = 0
    va_prices: list[float] = []
    for idx, vol in sorted_by_vol:
        if vol <= 0:
            continue
        cum_vol += vol
        va_prices.append(bin_prices[idx])
        if cum_vol >= target_vol:
            break

    if not va_prices:
        va_prices = [poc]

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
    if not candles:
        return []
    start = index_at_or_after(candles, start_ts)
    end = index_at_or_after(candles, end_ts)
    return candles[start:end]


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
    """Return trade NET PnL in pips, computing from prices when not stored.

    When a value is already stored (set by enrich_trades_pnl), it is returned
    as-is and is assumed to be net of trading costs. When computed on the fly,
    round-turn trading costs are deducted so callers never see a cost-free PnL.
    """
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
    if direction in ("long", "bullish"):
        raw = float(exit_p) - ref
    elif direction in ("short", "bearish"):
        raw = ref - float(exit_p)
    else:
        return 0.0
    gross = raw / pip_size
    cost = round_turn_cost_pips(ref)
    return round(gross - cost, 1)


# ---------------------------------------------------------------------------
# Trading cost model (spread + commission + slippage)
# ---------------------------------------------------------------------------
#
# Real fills are never free. Every round-turn trade pays the bid/ask spread,
# broker commission, and some slippage. Backtests that ignore this routinely
# show "profitable" systems that bleed money live, especially scalpers whose
# edge per trade is small. We deduct a configurable round-turn cost from every
# trade, expressed in PRICE units (same units as the instrument) and converted
# to the framework's "pip" unit via infer_pip_size.
#
# Defaults are calibrated to a retail Exness-style account and are intentionally
# conservative-but-realistic. Override per run with the env vars below.
#   BT_COST_PRICE       explicit round-turn cost in price units (highest priority)
#   BT_SLIPPAGE_PRICE   extra slippage in price units added on top of class default
# ---------------------------------------------------------------------------

def _default_round_turn_cost_price(ref_price: float) -> float:
    """Round-turn cost (spread + commission + slippage) in PRICE units by class."""
    if ref_price >= 1000:        # metals like XAUUSD (~2000-3000)
        return 0.40              # ~30c spread + ~10c slippage round-turn
    if ref_price >= 100:         # JPY crosses (~150), some indices
        return 0.030
    if ref_price >= 10:          # e.g. silver (~25)
        return 0.020
    return 0.00012               # FX majors (~1.2 pip round-turn)


def round_turn_cost_price(ref_price: float) -> float:
    explicit = os.environ.get("BT_COST_PRICE")
    if explicit:
        try:
            return float(explicit)
        except ValueError:
            pass
    base = _default_round_turn_cost_price(ref_price)
    extra = os.environ.get("BT_SLIPPAGE_PRICE")
    if extra:
        try:
            base += float(extra)
        except ValueError:
            pass
    return base


def round_turn_cost_pips(ref_price: float) -> float:
    """Round-turn cost expressed in the framework's pip unit for this price."""
    if ref_price <= 0:
        return 0.0
    mult = 1.0
    env_mult = os.environ.get("BT_COST_MULT")
    if env_mult:
        try:
            mult = float(env_mult)
        except ValueError:
            mult = 1.0
    return mult * round_turn_cost_price(ref_price) / infer_pip_size(ref_price)


MIN_VALID_TRADE_TS = 86400  # 1970-01-02 UTC — reject epoch-0/garbage only, allow all real history (FX data goes back to 1971/1999)


def _trade_timestamp(value) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts if ts >= MIN_VALID_TRADE_TS else None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = int(dt.timestamp())
        return ts if ts >= MIN_VALID_TRADE_TS else None
    except (TypeError, ValueError):
        return None


def _trade_has_valid_exit_time(trade: dict) -> bool:
    entry_ts = _trade_timestamp(trade.get("entry_time"))
    exit_ts = _trade_timestamp(trade.get("exit_time"))
    if exit_ts is None:
        return False
    if entry_ts is not None and exit_ts <= entry_ts:
        return False
    return True


def _normalize_trade_exit_metadata(trade: dict) -> None:
    """Drop bogus exit_time values that break stats and chart rendering."""
    if _trade_has_valid_exit_time(trade):
        return
    if trade.get("exit_time"):
        trade.pop("exit_time", None)


def _infer_closed_outcome_from_pnl(trade: dict) -> None:
    """Infer win/loss/breakeven only when exit metadata proves the trade closed."""
    outcome = (trade.get("outcome") or "").strip().lower()
    if outcome not in ("", "open"):
        return

    entry = trade.get("entry_price")
    exit_p = trade.get("exit_price")
    if entry is None or exit_p is None:
        return
    if not _trade_has_valid_exit_time(trade):
        return

    pnl = trade.get("pnl_pips")
    if pnl is None:
        return
    pnl = float(pnl)
    if pnl > 0:
        trade["outcome"] = "win"
    elif pnl < 0:
        trade["outcome"] = "loss"
    else:
        trade["outcome"] = "breakeven"


def _gross_pnl_pips(trade: dict) -> Optional[float]:
    entry = trade.get("entry_price")
    exit_p = trade.get("exit_price")
    if entry is None or exit_p is None:
        return None
    ref = float(entry)
    pip_size = infer_pip_size(ref)
    direction = (trade.get("direction") or "").lower()
    if direction in ("long", "bullish"):
        raw = float(exit_p) - ref
    elif direction in ("short", "bearish"):
        raw = ref - float(exit_p)
    else:
        return None
    return raw / pip_size


def enrich_trades_pnl(trades: list[dict]) -> list[dict]:
    """Compute net PnL (after trading costs) and normalize exit metadata.

    For every closed trade we store:
      pnl_gross_pips  - PnL before costs
      cost_pips       - round-turn trading cost applied
      pnl_pips        - net PnL (gross - cost)  <- used by all stats
    Outcome (win/loss/breakeven) is derived from NET PnL so a target that does
    not cover its own trading cost is correctly counted as a loser.
    """
    for trade in trades:
        # Strategies with multi-leg exits (partial profit + breakeven, scaling)
        # cannot be represented by a single entry->exit price. They report their
        # realized result in 'pnl_R'; honor it here (net of round-turn cost, with
        # an extra half round-turn when a partial was taken).
        if trade.get("pnl_R") is not None and trade.get("entry_price") is not None \
                and trade.get("stop_loss") is not None and _trade_has_valid_exit_time(trade):
            ref = float(trade["entry_price"])
            pip = infer_pip_size(ref)
            risk_pips = abs(ref - float(trade["stop_loss"])) / pip
            gross_pips = float(trade["pnl_R"]) * risk_pips
            cost_pips = round_turn_cost_pips(ref) * 1.5  # entry + two exits
            net = round(gross_pips - cost_pips, 1)
            trade["pnl_gross_pips"] = round(gross_pips, 1)
            trade["cost_pips"] = round(cost_pips, 1)
            trade["pnl_pips"] = net
            trade["outcome"] = "win" if net > 0 else "loss" if net < 0 else "breakeven"
            # Scale-out trades have TWO exits, so a single entry->exit row can't
            # represent them (scratches show entry==exit with non-zero pips).
            # Show a BLENDED effective exit so the row reconciles exactly with the
            # net pips; keep the real final fill in final_exit_price (and the real
            # legs in events[]).
            direction = (trade.get("direction") or "").lower()
            if "final_exit_price" not in trade:
                trade["final_exit_price"] = trade.get("exit_price")
            if direction in ("long", "bullish"):
                trade["exit_price"] = round(ref + net * pip, 6)
            else:
                trade["exit_price"] = round(ref - net * pip, 6)
            _normalize_trade_exit_metadata(trade)
            continue
        gross = _gross_pnl_pips(trade)
        if gross is not None and _trade_has_valid_exit_time(trade):
            ref = float(trade["entry_price"])
            cost = round_turn_cost_pips(ref)
            net = round(gross - cost, 1)
            trade["pnl_gross_pips"] = round(gross, 1)
            trade["cost_pips"] = round(cost, 1)
            trade["pnl_pips"] = net
            # Net-based outcome: covers the "won the move, lost to costs" case.
            outcome = (trade.get("outcome") or "").strip().lower()
            if outcome in ("", "open") or trade.get("exit_price") is not None:
                if net > 0:
                    trade["outcome"] = "win"
                elif net < 0:
                    trade["outcome"] = "loss"
                else:
                    trade["outcome"] = "breakeven"
        else:
            # No valid exit -> leave as open, no PnL claimed.
            trade.pop("pnl_pips", None)
            _normalize_trade_exit_metadata(trade)
            continue
        _normalize_trade_exit_metadata(trade)
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
