#!/usr/bin/env python3
"""
Strategy 146: Naked 4H POI Draw with 15m Counter-Zone Entries.

The inverse execution of Strategy 145. Instead of trading the reaction AT a 4H
point of interest, this model treats an unmitigated 4H demand/supply zone as the
destination and trades the leg that travels toward it.

Sequence, all from three independently loaded native feeds:

  1. 4H builds structural zones. A still-unmitigated 4H zone becomes a target.
  2. 15m zones that oppose that 4H zone become the entry points, because a zone
     opposing the destination is aligned with the direction of travel toward it.
  3. 5m confirms the entry with either a liquidity-sweep liquidation candle or a
     conservative structure shift.
  4. Take profit is the PROXIMAL edge of the 4H destination: the first price at
     which the zone is actually reached.

The destination is known before the trade exists, so entry, stop and target are
all defined at order placement. No timeframe is resampled. No indicators, no
moving averages, no oscillators, no volume filters.

Give-up rules matter more than the entry model here: an unmitigated zone acts as
a probabilistic draw with no guaranteed deadline, so every trade carries a hard
holding limit.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Optional

import numpy as np

from core.core import Candle, emit_progress, load_csv, parse_csv_filename, save_trades, to_iso

H4_SECONDS = 4 * 60 * 60
M15_SECONDS = 15 * 60
M5_SECONDS = 5 * 60

# Chunk size for the vectorised "first bar that crosses a level" scans.
_SCAN_CHUNK = 8192


@dataclass(frozen=True)
class Params:
    h4_swing_left: int = 2
    h4_swing_right: int = 2
    m15_swing_left: int = 2
    m15_swing_right: int = 2
    m5_swing_left: int = 2
    m5_swing_right: int = 2
    equal_level_tolerance_bps: float = 2.0
    stop_buffer_bps: float = 0.5
    # Target distance is judged against the structural risk, never in absolute
    # price, so the same settings behave identically across instruments.
    min_target_reward_risk: float = 1.5
    max_target_reward_risk: float = 20.0
    # Price must return to the 15m entry zone within this many 5m bars, else the
    # point of interest is treated as stale (also bounds the setup scan).
    max_wait_bars_5m: int = 864
    # Hard holding limit. The draw toward a 4H zone has no deadline, so a trade
    # that has not resolved within this window is closed at market.
    max_hold_bars_5m: int = 864
    max_liquidity_lookback_5m: int = 72


@dataclass(frozen=True)
class Swing:
    index: int
    direction: int  # +1 high, -1 low
    price: float
    time: int
    confirmed_time: int


@dataclass(frozen=True)
class Zone:
    zone_id: str
    timeframe: str
    direction: int  # +1 demand/bullish, -1 supply/bearish
    lower: float
    upper: float
    distal: float
    proximal: float
    origin_index: int
    origin_time: int
    bos_index: int
    active_time: int
    broken_level: float
    strong_level: float
    weak_level: float


@dataclass(frozen=True)
class LiquidityLevel:
    direction: int  # +1 buy-side/high, -1 sell-side/low
    price: float
    formation_time: int
    confirmation_time: int
    equal: bool


@dataclass(frozen=True)
class Destination:
    """An unmitigated 4H zone used as a target.

    `first_touch_time` is the 5m bar-open time at which price first reaches the
    zone. Before that moment the zone is unmitigated; from that moment it is
    consumed and can no longer be used as a fresh destination.
    """
    zone: Zone
    first_touch_time: int


@dataclass(frozen=True)
class Alert:
    zone: Zone
    index: int
    time: int


@dataclass(frozen=True)
class Setup:
    entry_zone: Zone
    destination: Destination
    direction: int
    alert_index: int
    alert_time: int
    expiry_time: int


@dataclass(frozen=True)
class Candidate:
    setup: Setup
    direction: int
    model: str
    signal_index: int
    signal_time: int
    trigger: float
    stop: float
    fill_index: int
    fill_time: int
    entry: float
    confirmation_level: float
    swept_level: Optional[float]


# ---------------------------------------------------------------------------
# Native feed validation
# ---------------------------------------------------------------------------

def _clean_native(candles: list[Candle], label: str, seconds: int) -> list[Candle]:
    """Validate one native feed without synthesizing or resampling candles."""
    if not candles:
        raise ValueError(f"{label} input is empty")
    canonical: dict[int, Candle] = {}
    for candle in candles:
        prices = (candle.open, candle.high, candle.low, candle.close)
        if candle.timestamp <= 0 or not all(math.isfinite(value) for value in prices):
            raise ValueError(f"{label} contains an invalid timestamp or non-finite OHLC value")
        if (candle.high < max(candle.open, candle.close, candle.low)
                or candle.low > min(candle.open, candle.close, candle.high)):
            raise ValueError(f"{label} contains an invalid OHLC envelope at {candle.timestamp}")
        canonical[candle.timestamp] = candle
    ordered = [canonical[stamp] for stamp in sorted(canonical)]
    if len(ordered) < 2:
        raise ValueError(f"{label} requires at least two candles")

    differences = [current.timestamp - previous.timestamp
                   for previous, current in zip(ordered, ordered[1:])]

    # Weekend, holiday and DST gaps are larger than the native interval and must
    # not be mistaken for the wrong timeframe, so judge the dominant cadence.
    ordinary = sorted(difference for difference in differences if difference <= seconds * 4)
    typical = ordinary[len(ordinary) // 2] if ordinary else min(differences)
    if typical != seconds:
        raise ValueError(
            f"{label} input is not native {label}: observed cadence is "
            f"{typical} seconds ({typical // 3600}h{(typical % 3600) // 60:02d}m), "
            f"expected {seconds} seconds ({seconds // 3600}h{(seconds % 3600) // 60:02d}m). "
            "Provide the actual native timeframe CSV; this strategy never resamples."
        )
    if any(difference < seconds for difference in differences):
        bad_index = next(index for index, difference in enumerate(differences)
                         if difference < seconds)
        previous = ordered[bad_index].timestamp
        current = ordered[bad_index + 1].timestamp
        raise ValueError(
            f"{label} contains a sub-{label} interval: {previous} -> {current} "
            f"({differences[bad_index]} seconds; expected at least {seconds})"
        )
    return ordered


def _common_overlap(
    h4: list[Candle], m15: list[Candle], m5: list[Candle]
) -> tuple[list[Candle], list[Candle], list[Candle]]:
    start = max(h4[0].timestamp, m15[0].timestamp, m5[0].timestamp)
    end = min(h4[-1].timestamp + H4_SECONDS,
              m15[-1].timestamp + M15_SECONDS,
              m5[-1].timestamp + M5_SECONDS)
    if end <= start:
        raise ValueError("native 4H, 15m, and 5m inputs do not overlap")
    feeds = (
        [c for c in h4 if c.timestamp >= start and c.timestamp + H4_SECONDS <= end],
        [c for c in m15 if c.timestamp >= start and c.timestamp + M15_SECONDS <= end],
        [c for c in m5 if c.timestamp >= start and c.timestamp + M5_SECONDS <= end],
    )
    if len(feeds[0]) < 20 or len(feeds[1]) < 100 or len(feeds[2]) < 300:
        raise ValueError("insufficient common native data (need 20 4H, 100 15m, and 300 5m bars)")
    return feeds


# ---------------------------------------------------------------------------
# Structure: swings and zones
# ---------------------------------------------------------------------------

def _swings(candles: list[Candle], left: int, right: int, seconds: int) -> list[Swing]:
    swings: list[Swing] = []
    for index in range(left, len(candles) - right):
        candle = candles[index]
        high_left = all(candle.high > candles[j].high for j in range(index - left, index))
        high_right = all(candle.high >= candles[j].high for j in range(index + 1, index + right + 1))
        low_left = all(candle.low < candles[j].low for j in range(index - left, index))
        low_right = all(candle.low <= candles[j].low for j in range(index + 1, index + right + 1))
        confirmation = candles[index + right].timestamp + seconds
        if high_left and high_right:
            swings.append(Swing(index, 1, candle.high, candle.timestamp, confirmation))
        if low_left and low_right:
            swings.append(Swing(index, -1, candle.low, candle.timestamp, confirmation))
    return sorted(swings, key=lambda item: (item.confirmed_time, item.index, -item.direction))


def _last_opposite_candle(candles: list[Candle], start: int, end: int, direction: int) -> Optional[int]:
    for index in range(end - 1, start - 1, -1):
        candle = candles[index]
        if (direction > 0 and candle.close < candle.open) or (direction < 0 and candle.close > candle.open):
            return index
    return None


def _zone_identity(
    timeframe: str, direction: int, origin_time: int, lower: float, upper: float
) -> str:
    """Stable id derived from WHAT the zone is, never from where it sits in an array.

    An index-based id (the old `bos_index`) is only unique inside one fixed
    candle array. Live, the feed is a rolling window, so the same physical zone
    is renumbered on every new bar: dedupe keys stop matching, journal events get
    suppressed as duplicates, and cross-timeframe joins break. Origin timestamp
    plus a digest of the boundaries is invariant under windowing, so the same
    zone keeps one id across every cycle and across restarts.
    """
    digest = hashlib.blake2s(
        f"{lower:.10f}:{upper:.10f}".encode("utf-8"), digest_size=3
    ).hexdigest()
    return f"{timeframe}-{origin_time}-{direction}-{digest}"


def _build_zones(
    candles: list[Candle], timeframe: str, seconds: int, left: int, right: int
) -> list[Zone]:
    """Order blocks confirmed by a close beyond a confirmed swing."""
    swings = _swings(candles, left, right, seconds)
    pending: dict[int, list[Swing]] = {1: [], -1: []}
    history: dict[int, list[Swing]] = {1: [], -1: []}
    pointer = 0
    zones: list[Zone] = []
    for bos_index, candle in enumerate(candles):
        decision_time = candle.timestamp + seconds
        while pointer < len(swings) and swings[pointer].confirmed_time <= decision_time:
            swing = swings[pointer]
            if swing.index < bos_index:
                pending[swing.direction].append(swing)
                history[swing.direction].append(swing)
            pointer += 1
        for direction in (1, -1):
            if not pending[direction]:
                continue
            swing = pending[direction][-1]
            if not (candle.close > swing.price if direction > 0 else candle.close < swing.price):
                continue
            strong = next(
                (item for item in reversed(history[-direction]) if item.index < swing.index),
                None,
            )
            pending[direction].pop()
            if strong is None:
                continue
            origin_index = _last_opposite_candle(candles, strong.index, bos_index, direction)
            if origin_index is None:
                continue
            origin = candles[origin_index]
            if direction > 0:
                lower, upper = origin.low, max(origin.open, origin.close)
            else:
                lower, upper = min(origin.open, origin.close), origin.high
            if upper <= lower:
                continue
            zones.append(Zone(
                zone_id=_zone_identity(timeframe, direction, origin.timestamp, lower, upper),
                timeframe=timeframe,
                direction=direction, lower=lower, upper=upper,
                distal=lower if direction > 0 else upper,
                proximal=upper if direction > 0 else lower,
                origin_index=origin_index, origin_time=origin.timestamp,
                bos_index=bos_index, active_time=decision_time, broken_level=swing.price,
                strong_level=strong.price, weak_level=swing.price,
            ))
    return sorted(zones, key=lambda zone: (zone.active_time, zone.bos_index))


def _dedupe_zones(zones: list[Zone]) -> list[Zone]:
    """Collapse repeated confirmations of one physical zone.

    Successive breaks of structure can re-confirm the same origin candle with
    identical boundaries. Those are one zone, not several, so only the earliest
    confirmation is kept. Without this the same price area would be traded more
    than once and zone counts would be inflated.
    """
    earliest: dict[tuple, Zone] = {}
    for zone in zones:
        key = (zone.timeframe, zone.direction, zone.origin_index,
               round(zone.lower, 10), round(zone.upper, 10))
        current = earliest.get(key)
        if current is None or zone.active_time < current.active_time:
            earliest[key] = zone
    return sorted(earliest.values(), key=lambda zone: (zone.active_time, zone.bos_index))


def _touches(candle: Candle, zone: Zone) -> bool:
    return candle.low <= zone.upper and candle.high >= zone.lower


def _invalidated(candle: Candle, zone: Zone) -> bool:
    return candle.close < zone.distal if zone.direction > 0 else candle.close > zone.distal


def _zone_distal_stop(zone: Zone, params: Params) -> float:
    """Protect the structural thesis beyond the entry zone's distal boundary."""
    buffer = abs(zone.distal) * params.stop_buffer_bps / 10000.0
    return zone.distal - buffer if zone.direction > 0 else zone.distal + buffer


# ---------------------------------------------------------------------------
# Vectorised 5m level scans
# ---------------------------------------------------------------------------

class Series:
    """Native 5m OHLC as arrays, for level-crossing searches."""

    __slots__ = ("candles", "ts", "high", "low", "close")

    def __init__(self, candles: list[Candle]):
        count = len(candles)
        self.candles = candles
        self.ts = np.fromiter((c.timestamp for c in candles), dtype=np.int64, count=count)
        self.high = np.fromiter((c.high for c in candles), dtype=np.float64, count=count)
        self.low = np.fromiter((c.low for c in candles), dtype=np.float64, count=count)
        self.close = np.fromiter((c.close for c in candles), dtype=np.float64, count=count)

    def index_at_or_after(self, timestamp: int) -> int:
        return int(np.searchsorted(self.ts, timestamp, side="left"))


def _first_at_or_below(values: np.ndarray, start: int, limit: int, threshold: float) -> int:
    """First index in [start, limit) whose value is <= threshold, else -1."""
    end_all = min(len(values), limit)
    index = max(0, start)
    while index < end_all:
        stop = min(end_all, index + _SCAN_CHUNK)
        hits = np.flatnonzero(values[index:stop] <= threshold)
        if hits.size:
            return index + int(hits[0])
        index = stop
    return -1


def _first_at_or_above(values: np.ndarray, start: int, limit: int, threshold: float) -> int:
    """First index in [start, limit) whose value is >= threshold, else -1."""
    end_all = min(len(values), limit)
    index = max(0, start)
    while index < end_all:
        stop = min(end_all, index + _SCAN_CHUNK)
        hits = np.flatnonzero(values[index:stop] >= threshold)
        if hits.size:
            return index + int(hits[0])
        index = stop
    return -1


def _first_zone_touch(zone: Zone, five: Series, start: int, limit: int) -> int:
    """First 5m index that reaches the zone.

    A zone is created by a break of structure that moves price away from it, so
    at `active_time` price sits on the proximal side. Reaching the zone is
    therefore a low crossing down to a demand's upper edge, or a high crossing
    up to a supply's lower edge.
    """
    if zone.direction > 0:
        return _first_at_or_below(five.low, start, limit, zone.upper)
    return _first_at_or_above(five.high, start, limit, zone.lower)


def _destinations(zones4: list[Zone], five: Series) -> list[Destination]:
    """Pair every 4H zone with the moment it stops being unmitigated.

    Note there is deliberately no separate "destination invalidated" state. The
    proximal edge is always nearer than the distal edge, so price cannot close
    beyond a zone without first reaching it. Reaching it is the take profit, so
    invalidation can never occur while a trade toward the zone is still waiting.
    """
    never = 2**63 - 1
    total = len(five.candles)
    destinations: list[Destination] = []
    for zone in zones4:
        start = five.index_at_or_after(zone.active_time)
        if start >= total:
            continue
        touch_index = _first_zone_touch(zone, five, start, total)
        first_touch = five.candles[touch_index].timestamp if touch_index >= 0 else never
        destinations.append(Destination(zone, first_touch))
    return destinations


def _alerts(zones15: list[Zone], five: Series, params: Params) -> list[Alert]:
    """First 5m return to each 15m entry zone, within the staleness window."""
    total = len(five.candles)
    alerts: list[Alert] = []
    for zone in zones15:
        start = five.index_at_or_after(zone.active_time)
        if start >= total:
            continue
        limit = min(total, start + params.max_wait_bars_5m)
        touch_index = _first_zone_touch(zone, five, start, limit)
        if touch_index < 0:
            continue
        alerts.append(Alert(zone, touch_index, five.candles[touch_index].timestamp + M5_SECONDS))
    # Tie-break on position, not on the id string: the id is now a content digest
    # and its lexical order carries no chronological meaning.
    alerts.sort(key=lambda alert: (alert.time, alert.zone.bos_index, alert.zone.direction))
    return alerts


class DestinationBook:
    """Unmitigated destinations of one direction, kept sorted by proximal price.

    Decisions are processed in chronological order, so a zone that has been
    reached is removed permanently. Queries then only ever see zones that were
    genuinely still unmitigated at that moment.
    """

    __slots__ = ("queue", "pending", "live", "by_seq", "touch_heap")

    def __init__(self, destinations: list[Destination], direction: int):
        self.queue = sorted(
            (item for item in destinations if item.zone.direction == direction),
            key=lambda item: item.zone.active_time,
        )
        self.pending = 0
        self.live: list[tuple[float, int]] = []
        self.by_seq: dict[int, Destination] = {}
        self.touch_heap: list[tuple[int, float, int]] = []

    def advance(self, now: int) -> None:
        while self.pending < len(self.queue) and self.queue[self.pending].zone.active_time <= now:
            destination = self.queue[self.pending]
            sequence = self.pending
            self.pending += 1
            if destination.first_touch_time <= now:
                continue
            proximal = destination.zone.proximal
            insort(self.live, (proximal, sequence))
            self.by_seq[sequence] = destination
            heappush(self.touch_heap, (destination.first_touch_time, proximal, sequence))
        while self.touch_heap and self.touch_heap[0][0] <= now:
            _, proximal, sequence = heappop(self.touch_heap)
            if self.by_seq.pop(sequence, None) is None:
                continue
            position = bisect_left(self.live, (proximal, sequence))
            if position < len(self.live) and self.live[position] == (proximal, sequence):
                self.live.pop(position)

    def nearest_below(self, threshold: float, before_time: int) -> Optional[Destination]:
        position = bisect_left(self.live, (threshold, -1))
        for index in range(position - 1, -1, -1):
            destination = self.by_seq[self.live[index][1]]
            if destination.zone.active_time < before_time:
                return destination
        return None

    def nearest_above(self, threshold: float, before_time: int) -> Optional[Destination]:
        position = bisect_right(self.live, (threshold, 2**62))
        for index in range(position, len(self.live)):
            destination = self.by_seq[self.live[index][1]]
            if destination.zone.active_time < before_time:
                return destination
        return None


def _find_setups(
    alerts: list[Alert], destinations: list[Destination], params: Params
) -> list[Setup]:
    """Pair each 15m entry zone with the nearest unmitigated opposing 4H zone.

    The 4H destination must already exist when the 15m entry zone is confirmed,
    which encodes the intended order of operations: the higher timeframe zone
    forms first, then the lower timeframe offers a way to trade toward it.
    """
    demand_book = DestinationBook(destinations, 1)
    supply_book = DestinationBook(destinations, -1)
    setups: list[Setup] = []
    for alert in alerts:
        demand_book.advance(alert.time)
        supply_book.advance(alert.time)
        zone = alert.zone
        direction = zone.direction
        if direction < 0:
            # Short from 15m supply toward an unmitigated 4H demand below it.
            destination = demand_book.nearest_below(zone.lower, zone.active_time)
        else:
            # Long from 15m demand toward an unmitigated 4H supply above it.
            destination = supply_book.nearest_above(zone.upper, zone.active_time)
        if destination is None:
            continue
        setups.append(Setup(
            entry_zone=zone, destination=destination, direction=direction,
            alert_index=alert.index, alert_time=alert.time,
            expiry_time=destination.first_touch_time,
        ))
    return setups


# ---------------------------------------------------------------------------
# 5m execution
# ---------------------------------------------------------------------------

class SwingIndex:
    """Confirmed 5m swings with binary-search access by confirmation time."""

    __slots__ = ("swings", "times")

    def __init__(self, swings: list[Swing]):
        self.swings = swings
        self.times = [swing.confirmed_time for swing in swings]

    def window(self, minimum_time: int, available_time: int) -> list[Swing]:
        left = bisect_left(self.times, minimum_time)
        right = bisect_right(self.times, available_time)
        return self.swings[left:right]


def _liquidity_levels(
    swings: list[Swing], available_time: int, tolerance_bps: float, minimum_time: int = 0,
) -> list[LiquidityLevel]:
    """Confirmed swing stops, retaining both formation and confirmation times."""
    eligible = [s for s in swings if minimum_time <= s.confirmed_time <= available_time]
    levels: list[LiquidityLevel] = []
    last_same: dict[int, Swing] = {}
    for swing in eligible:
        equal = False
        price = swing.price
        previous = last_same.get(swing.direction)
        if previous is not None:
            tolerance = max(abs(previous.price), abs(swing.price)) * tolerance_bps / 10000.0
            if abs(previous.price - swing.price) <= tolerance:
                equal = True
                price = (previous.price + swing.price) / 2.0
        last_same[swing.direction] = swing
        levels.append(LiquidityLevel(
            swing.direction, price, swing.time, swing.confirmed_time, equal
        ))
    return levels


def _recent_confirmed_swing(
    swings5: SwingIndex, direction: int, time: int, minimum_time: int = 0
) -> Optional[Swing]:
    eligible = [s for s in swings5.window(minimum_time, time) if s.direction == direction]
    return max(eligible, key=lambda swing: swing.index) if eligible else None


def _swept_liquidity(
    candle: Candle, direction: int, swings5: SwingIndex, params: Params
) -> Optional[LiquidityLevel]:
    lookback_start = max(0, candle.timestamp - params.max_liquidity_lookback_5m * M5_SECONDS)
    levels = _liquidity_levels(
        swings5.window(lookback_start, candle.timestamp),
        candle.timestamp, params.equal_level_tolerance_bps, minimum_time=lookback_start,
    )
    side = -1 if direction > 0 else 1
    swept: list[LiquidityLevel] = []
    for level in levels:
        if level.direction != side:
            continue
        if direction > 0 and candle.low < level.price and candle.close > level.price:
            swept.append(level)
        elif direction < 0 and candle.high > level.price and candle.close < level.price:
            swept.append(level)
    if not swept:
        return None
    return max(swept, key=lambda level: (level.equal, level.confirmation_time))


def _order_fill(
    m5: list[Candle], signal_index: int, trigger: float, direction: int,
    stop: float, zone: Zone, expiry_time: int,
) -> Optional[tuple[int, int, float]]:
    for index in range(signal_index + 1, len(m5)):
        candle = m5[index]
        if candle.timestamp >= expiry_time or _invalidated(candle, zone):
            return None
        triggered = candle.high >= trigger if direction > 0 else candle.low <= trigger
        if not triggered:
            continue
        entry = max(trigger, candle.open) if direction > 0 else min(trigger, candle.open)
        if (direction > 0 and entry <= stop) or (direction < 0 and entry >= stop):
            return None
        return index, candle.timestamp, entry
    return None


def _aggressive_candidate(
    setup: Setup, m5: list[Candle], swings5: SwingIndex, params: Params
) -> Optional[Candidate]:
    direction = setup.direction
    zone = setup.entry_zone
    for index in range(setup.alert_index, len(m5)):
        candle = m5[index]
        close_time = candle.timestamp + M5_SECONDS
        if close_time >= setup.expiry_time or _invalidated(candle, zone):
            return None
        swept = _swept_liquidity(candle, direction, swings5, params)
        directional_close = candle.close > candle.open if direction > 0 else candle.close < candle.open
        rejected = candle.close > zone.lower if direction > 0 else candle.close < zone.upper
        if swept is None or not directional_close or not rejected:
            continue
        buffer = candle.close * params.stop_buffer_bps / 10000.0
        trigger = candle.high + buffer if direction > 0 else candle.low - buffer
        stop = _zone_distal_stop(zone, params)
        fill = _order_fill(m5, index, trigger, direction, stop, zone, setup.expiry_time)
        if fill is None:
            return None
        fill_index, fill_time, entry = fill
        return Candidate(setup, direction, "aggressive_liquidation", index, close_time,
                         trigger, stop, fill_index, fill_time, entry,
                         swept.price, swept.price)
    return None


def _conservative_candidate(
    setup: Setup, m5: list[Candle], swings5: SwingIndex, params: Params
) -> Optional[Candidate]:
    direction = setup.direction
    zone = setup.entry_zone
    reference = _recent_confirmed_swing(
        swings5, 1 if direction > 0 else -1, setup.alert_time,
        max(0, setup.alert_time - params.max_liquidity_lookback_5m * M5_SECONDS),
    )
    if reference is None:
        return None
    for index in range(setup.alert_index + 1, len(m5)):
        candle = m5[index]
        close_time = candle.timestamp + M5_SECONDS
        if close_time >= setup.expiry_time or _invalidated(candle, zone):
            return None
        shifted = candle.close > reference.price if direction > 0 else candle.close < reference.price
        if not shifted:
            continue
        buffer = candle.close * params.stop_buffer_bps / 10000.0
        trigger = candle.high + buffer if direction > 0 else candle.low - buffer
        stop = _zone_distal_stop(zone, params)
        fill = _order_fill(m5, index, trigger, direction, stop, zone, setup.expiry_time)
        if fill is None:
            return None
        fill_index, fill_time, entry = fill
        return Candidate(setup, direction, "conservative_mss", index, close_time,
                         trigger, stop, fill_index, fill_time, entry,
                         reference.price, None)
    return None


def _reward_risk(candidate: Candidate, target: float) -> Optional[float]:
    direction = candidate.direction
    risk = direction * (candidate.entry - candidate.stop)
    reward = direction * (target - candidate.entry)
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


# ---------------------------------------------------------------------------
# Simulation and reporting
# ---------------------------------------------------------------------------

def _event(event_type: str, timestamp: int, **fields: object) -> dict:
    event = {"timestamp": to_iso(timestamp), "type": event_type}
    for key, value in fields.items():
        if isinstance(value, float):
            event[key] = round(value, 8)
        elif value is not None:
            event[key] = value
    return event


def _simulate(
    candidate: Candidate, target: float, m5: list[Candle], symbol: str, params: Params
) -> dict:
    direction = candidate.direction
    setup = candidate.setup
    zone = setup.entry_zone
    destination = setup.destination.zone
    stop = candidate.stop

    budget_end = candidate.fill_index + params.max_hold_bars_5m
    scan_end = min(len(m5) - 1, budget_end)
    exit_index: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason = "open"
    # Fill is intrabar; stop-first is deliberately conservative when a single
    # bar spans both the stop and the target.
    for index in range(candidate.fill_index, scan_end + 1):
        candle = m5[index]
        stop_hit = candle.low <= stop if direction > 0 else candle.high >= stop
        target_hit = candle.high >= target if direction > 0 else candle.low <= target
        if stop_hit:
            gap = candle.open <= stop if direction > 0 else candle.open >= stop
            exit_price = (min(stop, candle.open) if direction > 0 and gap
                          else max(stop, candle.open) if direction < 0 and gap
                          else stop)
            exit_index, exit_reason = index, "stop_loss"
            break
        if target_hit:
            gap = candle.open >= target if direction > 0 else candle.open <= target
            exit_price = (max(target, candle.open) if direction > 0 and gap
                          else min(target, candle.open) if direction < 0 and gap
                          else target)
            exit_index, exit_reason = index, "take_profit"
            break

    if exit_index is None and budget_end <= len(m5) - 1:
        # The draw toward an unmitigated zone has no deadline of its own, so the
        # holding limit closes the trade at market rather than holding forever.
        exit_index = budget_end
        exit_price = m5[budget_end].close
        exit_reason = "time_expiry"

    direction_name = "long" if direction > 0 else "short"
    destination_kind = "demand" if destination.direction > 0 else "supply"
    entry_kind = "demand" if zone.direction > 0 else "supply"
    signal_candle = m5[candidate.signal_index]
    fill_candle = m5[candidate.fill_index]

    events = [
        _event("order_block", destination.origin_time, timeframe="4h",
               direction=destination_kind, role="destination",
               lower=destination.lower, upper=destination.upper,
               origin_time=to_iso(destination.origin_time),
               confirmed_at=to_iso(destination.active_time), confirmation="4h_bos_close",
               bos_level=destination.broken_level, strong_level=destination.strong_level,
               weak_level=destination.weak_level,
               narrative="bullish" if destination.direction > 0 else "bearish"),
        _event("mss", zone.active_time, timeframe="15m",
               direction="bullish" if zone.direction > 0 else "bearish",
               level=zone.broken_level, strong_level=zone.strong_level,
               weak_level=zone.weak_level, confirmation="15m_bos_close"),
        _event("order_block", zone.origin_time, timeframe="15m",
               direction=entry_kind, role="entry",
               lower=zone.lower, upper=zone.upper,
               origin_time=to_iso(zone.origin_time),
               confirmed_at=to_iso(zone.active_time), confirmation="15m_bos_close"),
        _event("zone_alert", setup.alert_time, timeframe="5m", zone_timeframe="15m",
               direction=direction_name, level=zone.proximal,
               lower=zone.lower, upper=zone.upper,
               confirmation="5m_touch_bar_close"),
    ]
    signal_type = "liquidation_candle" if candidate.model.startswith("aggressive") else "mss"
    events.append(_event(signal_type, candidate.signal_time, timeframe="5m",
                         direction=direction_name, trigger=candidate.trigger,
                         stop=candidate.stop, stop_basis="15m_zone_distal_plus_buffer",
                         stop_reference=zone.distal,
                         confirmation_level=candidate.confirmation_level,
                         swept_level=candidate.swept_level,
                         bar_open_time=to_iso(signal_candle.timestamp),
                         confirmation="5m_signal_bar_close"))
    events.append(_event("entry_stop_filled", candidate.fill_time, timeframe="5m",
                         direction=direction_name, price=candidate.entry,
                         execution_window_end=to_iso(fill_candle.timestamp + M5_SECONDS),
                         timestamp_basis="5m_execution_bar_open"))
    events.append(_event("target_zone", destination.origin_time, timeframe="4h",
                         direction=destination_kind, lower=destination.lower,
                         upper=destination.upper, price=target,
                         origin_time=to_iso(destination.origin_time),
                         confirmed_at=to_iso(destination.active_time),
                         selected_at=to_iso(candidate.fill_time),
                         basis="unmitigated_4h_zone_proximal_edge"))

    reward_risk = _reward_risk(candidate, target)
    if exit_index is None:
        exit_time = None
        exit_timestamp = None
        outcome = "open"
        bars_held = len(m5) - 1 - candidate.fill_index
    else:
        exit_timestamp = m5[exit_index].timestamp + M5_SECONDS
        exit_time = to_iso(exit_timestamp)
        profit = direction * (float(exit_price) - candidate.entry)
        outcome = "win" if profit > 0 else "loss"
        bars_held = exit_index - candidate.fill_index
        events.append(_event("final_exit", exit_timestamp, price=float(exit_price),
                             reason=exit_reason, timeframe="5m",
                             bar_open_time=to_iso(m5[exit_index].timestamp),
                             timestamp_basis="5m_exit_bar_close"))
    events.sort(key=lambda event: event["timestamp"])

    return {
        "strategy": "strategy_146_naked_4h_poi_draw",
        "setup": candidate.model,
        "symbol": symbol,
        "direction": direction_name,
        "entry_time": to_iso(candidate.fill_time),
        "entry_timestamp": candidate.fill_time,
        "entry_price": round(candidate.entry, 8),
        "stop_loss": round(candidate.stop, 8),
        "stop_basis": "15m_zone_distal_plus_buffer",
        "stop_reference_price": round(zone.distal, 8),
        "take_profit": round(target, 8),
        "target_basis": "unmitigated_4h_zone_proximal_edge",
        "planned_reward_risk": round(reward_risk, 2) if reward_risk is not None else None,
        "exit_time": exit_time,
        "exit_timestamp": exit_timestamp,
        "exit_price": round(float(exit_price), 8) if exit_price is not None else None,
        "outcome": outcome,
        "exit_reason": exit_reason,
        "bars_held_5m": bars_held,
        "h4_zone_type": destination_kind,
        "h4_narrative": "bullish" if destination.direction > 0 else "bearish",
        "h4_zone_id": destination.zone_id,
        "m15_zone_id": zone.zone_id,
        "events": events,
        "reason": ("Unmitigated native 4H " + destination_kind + " zone used as the destination; "
                   "entry from an opposing native 15m " + entry_kind + " zone with a 5m "
                   + candidate.model + "; target is the 4H zone proximal edge"),
    }


def generate_trades(
    candles4h: list[Candle], candles15m: list[Candle], candles5m: list[Candle],
    symbol: str = "UNKNOWN", params: Optional[Params] = None,
) -> list[dict]:
    """Generate symmetric, causal trades that travel toward unmitigated 4H zones."""
    settings = params or Params()
    h4 = _clean_native(candles4h, "4H", H4_SECONDS)
    m15 = _clean_native(candles15m, "15m", M15_SECONDS)
    m5 = _clean_native(candles5m, "5m", M5_SECONDS)
    h4, m15, m5 = _common_overlap(h4, m15, m5)

    emit_progress("strategy", 30, "Building native 4H destinations and 15m entry zones")
    zones4 = _dedupe_zones(_build_zones(h4, "4h", H4_SECONDS,
                                        settings.h4_swing_left, settings.h4_swing_right))
    zones15 = _dedupe_zones(_build_zones(m15, "15m", M15_SECONDS,
                                         settings.m15_swing_left, settings.m15_swing_right))
    five = Series(m5)
    swings5 = SwingIndex(_swings(m5, settings.m5_swing_left, settings.m5_swing_right, M5_SECONDS))

    emit_progress("strategy", 50, f"Mapping {len(zones4):,} 4H destinations")
    destinations = _destinations(zones4, five)
    emit_progress("strategy", 65, f"Locating returns to {len(zones15):,} 15m entry zones")
    alerts = _alerts(zones15, five, settings)
    setups = _find_setups(alerts, destinations, settings)

    emit_progress("strategy", 80, f"Confirming {len(setups):,} setups on native 5m")
    candidates: list[Candidate] = []
    for setup in setups:
        aggressive = _aggressive_candidate(setup, m5, swings5, settings)
        conservative = _conservative_candidate(setup, m5, swings5, settings)
        models = [item for item in (aggressive, conservative) if item is not None]
        if models:
            candidates.append(min(models, key=lambda item: (
                item.fill_time, item.signal_time,
                0 if item.model.startswith("aggressive") else 1,
            )))
    candidates.sort(key=lambda item: (item.fill_time,
                                      item.setup.entry_zone.bos_index,
                                      item.setup.entry_zone.direction))

    trades: list[dict] = []
    used_entry_zones: set[str] = set()
    last_exit_time = -1
    for candidate in candidates:
        zone_id = candidate.setup.entry_zone.zone_id
        if zone_id in used_entry_zones or candidate.fill_time < last_exit_time:
            continue
        target = candidate.setup.destination.zone.proximal
        reward_risk = _reward_risk(candidate, target)
        if reward_risk is None:
            continue
        if not (settings.min_target_reward_risk <= reward_risk <= settings.max_target_reward_risk):
            continue
        trade = _simulate(candidate, target, m5, symbol, settings)
        trade["trade_number"] = len(trades) + 1
        trades.append(trade)
        used_entry_zones.add(zone_id)
        last_exit_time = (int(trade["exit_timestamp"])
                          if trade["exit_timestamp"] is not None else 2**63 - 1)

    emit_progress("strategy", 100,
                  f"S146 generated {len(trades)} trades toward unmitigated 4H zones")
    return trades


def run_strategy(csv4h: str, csv15m: str, csv5m: str, output: str) -> list[dict]:
    symbol = (os.environ.get("BT_SYMBOL")
              or parse_csv_filename(csv5m).get("symbol")
              or parse_csv_filename(csv4h).get("symbol")
              or "UNKNOWN")
    emit_progress("strategy", 5, "Loading native 4H, 15m, and 5m inputs")
    try:
        trades = generate_trades(load_csv(csv4h), load_csv(csv15m), load_csv(csv5m),
                                 symbol=str(symbol).upper())
    except ValueError as exc:
        raise ValueError(
            "S146 input validation failed. "
            f"4H={os.path.basename(csv4h)}, "
            f"15m={os.path.basename(csv15m)}, "
            f"5m={os.path.basename(csv5m)}. {exc}"
        ) from exc
    save_trades(trades, output)
    print(f"Saved {len(trades)} trades to {output}")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strategy 146: indicator-free entries that target unmitigated native 4H zones"
    )
    parser.add_argument("--csv4h", required=True, help="Path to the required native 4-hour OHLCV CSV")
    parser.add_argument("--csv15m", required=True, help="Path to the required native 15-minute OHLCV CSV")
    parser.add_argument("--csv5m", required=True, help="Path to the required native 5-minute OHLCV CSV")
    parser.add_argument("--output", required=True, help="Path for dashboard-compatible trade JSON output")
    args = parser.parse_args()
    run_strategy(args.csv4h, args.csv15m, args.csv5m, args.output)


if __name__ == "__main__":
    main()
