"""
Causal (no-lookahead) helpers for strategy backtests.
Only use data available at or before the decision bar's close.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from core import (
    Candle,
    compute_volume_profile,
    detect_cisd,
    detect_fvg,
    detect_ifvg,
    detect_mss,
    resample,
    to_iso,
)


def ny_hour(ts: int) -> int:
    return (datetime.fromtimestamp(ts, tz=timezone.utc).hour - 4) % 24


def ny_minute_of_day(ts: int) -> int:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    h = (dt.hour - 4) % 24
    return h * 60 + dt.minute


def ny_date(ts: int):
    return (datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=4)).date()


def group_by_ny_day(candles: list[Candle]) -> list[list[Candle]]:
    days: list[list[Candle]] = []
    cur: list[Candle] = []
    cur_date = None
    for c in candles:
        d = ny_date(c.timestamp)
        if cur_date is None:
            cur_date = d
        if d != cur_date:
            if cur:
                days.append(cur)
            cur = []
            cur_date = d
        cur.append(c)
    if cur:
        days.append(cur)
    return days


def past_slice(candles: list[Candle], end_idx: int) -> list[Candle]:
    """Candles known after end_idx closes (inclusive)."""
    return candles[: max(0, end_idx) + 1]


def detect_fvg_as_of(candles: list[Candle], as_of_idx: int) -> list[dict]:
    """FVG at middle bar i only when bar i+1 has closed (as_of_idx >= i+1)."""
    fvgs = []
    for i in range(1, min(as_of_idx, len(candles) - 2)):
        if as_of_idx < i + 1:
            continue
        prev, nxt = candles[i - 1], candles[i + 1]
        gap_low = nxt.low - prev.high
        if gap_low > 0:
            fvgs.append({
                "idx": i,
                "direction": "bullish",
                "upper": nxt.low,
                "lower": prev.high,
                "gap": gap_low,
            })
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


def resample_as_of(candles: list[Candle], minutes: int, as_of_ts: int) -> list[Candle]:
    """Completed HTF bars only, using 1m data up to as_of_ts."""
    subset = [c for c in candles if c.timestamp <= as_of_ts]
    if not subset:
        return []
    bars = resample(subset, minutes)
    secs = minutes * 60
    while bars and bars[-1].timestamp + secs > as_of_ts:
        bars.pop()
    return bars


def mss_events_up_to(candles: list[Candle], as_of_idx: int, lookback: int = 5) -> list[dict]:
    return detect_mss(candles[: as_of_idx + 1], lookback)


def cisd_events_up_to(candles: list[Candle], as_of_idx: int, lookback: int = 5) -> list[dict]:
    return detect_cisd(candles[: as_of_idx + 1], lookback)


def ifvg_up_to(candles: list[Candle], fvg: dict, as_of_idx: int) -> Optional[dict]:
    sub = candles[: as_of_idx + 1]
    if fvg["idx"] >= len(sub):
        return None
    return detect_ifvg(sub, fvg)


def compute_vwap(candles: list[Candle]) -> float:
    vol_sum = sum(c.volume for c in candles)
    if vol_sum == 0:
        return candles[-1].close if candles else 0.0
    return sum((c.high + c.low + c.close) / 3 * c.volume for c in candles) / vol_sum


def simulate_exits(
    candles: list[Candle],
    entry_idx: int,
    entry_ts: int,
    direction: str,
    sl: float,
    tp: float,
) -> dict:
    """TP/SL on bars strictly after the entry bar closes."""
    for j in range(entry_idx + 1, len(candles)):
        c = candles[j]
        if c.timestamp <= entry_ts:
            continue
        if direction == "long":
            if c.high >= tp:
                return {"exit_time": to_iso(c.timestamp), "exit_price": tp, "outcome": "win"}
            if c.low <= sl:
                return {"exit_time": to_iso(c.timestamp), "exit_price": sl, "outcome": "loss"}
        else:
            if c.low <= tp:
                return {"exit_time": to_iso(c.timestamp), "exit_price": tp, "outcome": "win"}
            if c.high >= sl:
                return {"exit_time": to_iso(c.timestamp), "exit_price": sl, "outcome": "loss"}
    last = candles[-1]
    return {
        "exit_time": to_iso(last.timestamp),
        "exit_price": last.close,
        "outcome": "open",
    }


def find_limit_fill(
    candles: list[Candle],
    after_idx: int,
    limit_price: float,
    direction: str,
    max_bars: int = 48,
) -> Optional[tuple[int, float]]:
    for j in range(after_idx + 1, min(after_idx + 1 + max_bars, len(candles))):
        c = candles[j]
        if direction == "long" and c.low <= limit_price:
            return j, limit_price
        if direction == "short" and c.high >= limit_price:
            return j, limit_price
    return None


def absorption_at_level_causal(
    candles: list[Candle],
    level: float,
    direction: str,
    window: int = 3,
) -> list[dict]:
    """Absorption using only past bars — no forward follow-through peek."""
    events = []
    for i in range(window, len(candles)):
        cluster = candles[i - window : i + 1]
        avg_vol = sum(c.volume for c in cluster) / len(cluster) if cluster else 0
        if direction == "bullish":
            near = sum(1 for c in cluster if abs(c.high - level) / max(level, 1e-9) < 0.0005)
            stalled = candles[i].close < level * 1.0005
            if near >= 2 and avg_vol > 100 and stalled:
                events.append({
                    "idx": i,
                    "type": "buyer_absorption",
                    "level": level,
                    "avg_volume": avg_vol,
                    "description": f"Buyer absorption at {level:.5f} (causal)",
                })
        else:
            near = sum(1 for c in cluster if abs(c.low - level) / max(level, 1e-9) < 0.0005)
            stalled = candles[i].close > level * 0.9995
            if near >= 2 and avg_vol > 100 and stalled:
                events.append({
                    "idx": i,
                    "type": "seller_absorption",
                    "level": level,
                    "avg_volume": avg_vol,
                    "description": f"Seller absorption at {level:.5f} (causal)",
                })
    return events


def order_block_entry_idx(ob_idx: int, lookahead: int = 3) -> int:
    """Earliest bar you may act on a detected order block."""
    return ob_idx + lookahead


def map_tf_bar_to_1m_idx(tf_bar: Candle, candles_1m: list[Candle]) -> int:
    """Find 1m index at or after HTF bar open timestamp."""
    for i, c in enumerate(candles_1m):
        if c.timestamp >= tf_bar.timestamp:
            return i
    return len(candles_1m) - 1
