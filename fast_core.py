"""
Numba-accelerated OHLCV primitives used by core.py and causal_backtest.py.
Falls back to NumPy when Numba is unavailable.
"""

from __future__ import annotations

import numpy as np

try:
    from numba import njit

    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

    def njit(*args, **kwargs):
        def wrapper(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return wrapper


@njit(cache=True)
def _swing_high_indices_nb(high: np.ndarray) -> np.ndarray:
    n = len(high)
    out = np.empty(n, dtype=np.int64)
    count = 0
    for i in range(1, n - 1):
        if high[i] > high[i - 1] and high[i] > high[i + 1]:
            out[count] = i
            count += 1
    return out[:count]


@njit(cache=True)
def _swing_low_indices_nb(low: np.ndarray) -> np.ndarray:
    n = len(low)
    out = np.empty(n, dtype=np.int64)
    count = 0
    for i in range(1, n - 1):
        if low[i] < low[i - 1] and low[i] < low[i + 1]:
            out[count] = i
            count += 1
    return out[:count]


@njit(cache=True)
def _nearest_swing_high_before_nb(swing_highs: np.ndarray, i: int) -> int:
    lo = 0
    hi = len(swing_highs)
    while lo < hi:
        mid = (lo + hi) // 2
        if swing_highs[mid] < i:
            lo = mid + 1
        else:
            hi = mid
    pos = lo - 1
    while pos >= 0:
        sh = swing_highs[pos]
        if sh + 1 <= i:
            return sh
        pos -= 1
    return -1


@njit(cache=True)
def _nearest_swing_low_before_nb(swing_lows: np.ndarray, i: int) -> int:
    lo = 0
    hi = len(swing_lows)
    while lo < hi:
        mid = (lo + hi) // 2
        if swing_lows[mid] < i:
            lo = mid + 1
        else:
            hi = mid
    pos = lo - 1
    while pos >= 0:
        sl = swing_lows[pos]
        if sl + 1 <= i:
            return sl
        pos -= 1
    return -1


@njit(cache=True)
def _detect_mss_nb(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    lookback: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    swing_h = _swing_high_indices_nb(high)
    swing_l = _swing_low_indices_nb(low)
    n = len(close)
    idx_out = np.empty(n, dtype=np.int64)
    dir_out = np.empty(n, dtype=np.int8)
    swing_out = np.empty(n, dtype=np.int64)
    type_out = np.empty(n, dtype=np.int8)
    count = 0
    for i in range(lookback, n):
        sh = _nearest_swing_high_before_nb(swing_h, i)
        if sh >= 0 and close[i] > high[sh]:
            idx_out[count] = i
            dir_out[count] = 0
            swing_out[count] = sh
            type_out[count] = 0
            count += 1
        sl = _nearest_swing_low_before_nb(swing_l, i)
        if sl >= 0 and close[i] < low[sl]:
            idx_out[count] = i
            dir_out[count] = 1
            swing_out[count] = sl
            type_out[count] = 1
            count += 1
    return idx_out[:count], dir_out[:count], swing_out[:count], type_out[:count]


@njit(cache=True)
def _simulate_exits_nb(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    ts: np.ndarray,
    entry_idx: int,
    entry_ts: int,
    is_long: bool,
    sl: float,
    tp: float,
) -> tuple[int, float, int]:
    """Returns (exit_idx, exit_price, outcome_code) outcome: 1 win, -1 loss, 0 open."""
    n = len(ts)
    if entry_idx + 1 >= n:
        last = n - 1
        return last, close[last], 0
    for j in range(entry_idx + 1, n):
        if is_long:
            if high[j] >= tp:
                return j, tp, 1
            if low[j] <= sl:
                return j, sl, -1
        else:
            if low[j] <= tp:
                return j, tp, 1
            if high[j] >= sl:
                return j, sl, -1
    last = n - 1
    return last, close[last], 0


@njit(cache=True)
def _resample_nb(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    secs: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(ts)
    if n == 0:
        empty = np.empty(0, dtype=np.int64)
        empty_f = np.empty(0, dtype=np.float64)
        return empty, empty_f, empty_f, empty_f, empty_f, empty

    out_ts = np.empty(n, dtype=np.int64)
    out_o = np.empty(n, dtype=np.float64)
    out_h = np.empty(n, dtype=np.float64)
    out_l = np.empty(n, dtype=np.float64)
    out_c = np.empty(n, dtype=np.float64)
    out_v = np.empty(n, dtype=np.int64)
    count = 0

    start_ts = ts[0]
    g_o = open_[0]
    g_h = high[0]
    g_l = low[0]
    g_c = close[0]
    g_v = volume[0]
    in_group = True

    for i in range(1, n):
        t = ts[i]
        if t < start_ts + secs:
            g_h = max(g_h, high[i])
            g_l = min(g_l, low[i])
            g_c = close[i]
            g_v += volume[i]
        else:
            out_ts[count] = start_ts
            out_o[count] = g_o
            out_h[count] = g_h
            out_l[count] = g_l
            out_c[count] = g_c
            out_v[count] = g_v
            count += 1
            start_ts = start_ts + secs
            while t >= start_ts + secs:
                start_ts += secs
            g_o = open_[i]
            g_h = high[i]
            g_l = low[i]
            g_c = close[i]
            g_v = volume[i]

    if in_group:
        out_ts[count] = start_ts
        out_o[count] = g_o
        out_h[count] = g_h
        out_l[count] = g_l
        out_c[count] = g_c
        out_v[count] = g_v
        count += 1

    return (
        out_ts[:count],
        out_o[:count],
        out_h[:count],
        out_l[:count],
        out_c[:count],
        out_v[:count],
    )


@njit(cache=True)
def _index_at_or_after_nb(ts: np.ndarray, target: int) -> int:
    lo = 0
    hi = len(ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def arrays_from_candles(candles) -> tuple[np.ndarray, ...]:
    n = len(candles)
    ts = np.empty(n, dtype=np.int64)
    o = np.empty(n, dtype=np.float64)
    h = np.empty(n, dtype=np.float64)
    l = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)
    v = np.empty(n, dtype=np.int64)
    for i, candle in enumerate(candles):
        ts[i] = candle.timestamp
        o[i] = candle.open
        h[i] = candle.high
        l[i] = candle.low
        c[i] = candle.close
        v[i] = candle.volume
    return ts, o, h, l, c, v


def swing_high_indices(high: np.ndarray) -> np.ndarray:
    return _swing_high_indices_nb(high)


def swing_low_indices(low: np.ndarray) -> np.ndarray:
    return _swing_low_indices_nb(low)


def detect_mss_arrays(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    lookback: int = 5,
) -> list[dict]:
    idx, dirs, swings, types = _detect_mss_nb(close, high, low, lookback)
    events = []
    for i in range(len(idx)):
        direction = "bullish" if dirs[i] == 0 else "bearish"
        swing_type = "high" if types[i] == 0 else "low"
        events.append({
            "idx": int(idx[i]),
            "direction": direction,
            "swing_idx": int(swings[i]),
            "swing_type": swing_type,
        })
    return events


def simulate_exits_arrays(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    ts: np.ndarray,
    entry_idx: int,
    entry_ts: int,
    direction: str,
    sl: float,
    tp: float,
) -> tuple[int, float, int]:
    is_long = direction == "long"
    return _simulate_exits_nb(high, low, close, ts, entry_idx, entry_ts, is_long, sl, tp)


def resample_arrays(
    ts: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    minutes: int,
) -> tuple[np.ndarray, ...]:
    return _resample_nb(ts, open_, high, low, close, volume, minutes * 60)


def index_at_or_after_ts(ts: np.ndarray, target: int) -> int:
    return int(_index_at_or_after_nb(ts, target))
