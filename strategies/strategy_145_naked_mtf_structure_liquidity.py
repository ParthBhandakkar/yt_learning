#!/usr/bin/env python3
"""
Strategy 145: Naked 4H/15m/5m Structure, Liquidity and POI Execution.

A fully causal, indicator-free implementation of the supplied model. It reads
three native datasets independently: 4H establishes the latest structural
narrative and extreme unmitigated POI; 15m confirms counter-flow then an aligned
shift and refines the POI; 5m executes either a liquidity-sweep liquidation
candle or a conservative market-structure shift. No timeframe is resampled.

Every discretionary phrase is made explicit in Params and applied identically
to bullish and bearish setups. Orders become actionable only after the signal
candle closes. Targets are the nearest causal, unmitigated opposing 15m zone.
"""

from __future__ import annotations

import argparse
import math
import os
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Optional

from core.core import Candle, emit_progress, load_csv, parse_csv_filename, save_trades, to_iso

H4_SECONDS = 4 * 60 * 60
M15_SECONDS = 15 * 60
M5_SECONDS = 5 * 60


@dataclass(frozen=True)
class Params:
    h4_swing_left: int = 2
    h4_swing_right: int = 2
    m15_swing_left: int = 2
    m15_swing_right: int = 2
    m5_swing_left: int = 2
    m5_swing_right: int = 2
    equal_level_tolerance_bps: float = 2.0
    min_nested_overlap: float = 0.50
    stop_buffer_bps: float = 0.5
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
    formation_time: int  # swing candle time; where the price level formed
    confirmation_time: int  # when enough later bars confirmed the swing
    equal: bool


@dataclass(frozen=True)
class Setup:
    h4_zone: Zone
    m15_zone: Zone
    counter_shift: Zone
    alert_index: int
    alert_time: int
    liquidity: Optional[LiquidityLevel]


@dataclass(frozen=True)
class Candidate:
    setup: Setup
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


def _clean_native(candles: list[Candle], label: str, seconds: int) -> list[Candle]:
    """Validate one native feed without synthesizing or resampling candles.

    Market-data files legitimately contain gaps at weekends, session boundaries,
    holidays, and DST transitions. Those gaps must not be mistaken for a bad
    timeframe. We therefore validate the dominant cadence and reject intervals
    shorter than the declared candle duration, while allowing larger gaps.
    """
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

    # Establish the observed native cadence before checking individual gaps.
    # Weekend/holiday/DST gaps are larger than the normal interval and should
    # not change this result. A mislabeled 1H file supplied as 4H is reported
    # here with the actual cadence instead of an opaque sub-interval error.
    ordinary = sorted(difference for difference in differences if difference <= seconds * 4)
    typical = ordinary[len(ordinary) // 2] if ordinary else min(differences)
    if typical != seconds:
        raise ValueError(
            f"{label} input is not native {label}: observed cadence is "
            f"{typical} seconds ({typical // 3600}h{(typical % 3600) // 60:02d}m), "
            f"expected {seconds} seconds ({seconds // 3600}h{(seconds % 3600) // 60:02d}m). "
            "Provide the actual native timeframe CSV; this strategy never resamples."
        )

    # Once the dominant cadence is correct, a shorter-than-candle interval is
    # malformed/overlapping data. Larger intervals are accepted as market gaps.
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


def _build_zones_fast(
    candles: list[Candle], timeframe: str, seconds: int, left: int, right: int
) -> list[Zone]:
    """Build zones with incremental swing state instead of an O(n²) scan."""
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
                zone_id=f"{timeframe}-{bos_index}-{direction}", timeframe=timeframe,
                direction=direction, lower=lower, upper=upper,
                distal=lower if direction > 0 else upper,
                proximal=upper if direction > 0 else lower,
                origin_index=origin_index, origin_time=origin.timestamp,
                bos_index=bos_index, active_time=decision_time, broken_level=swing.price,
                strong_level=strong.price, weak_level=swing.price,
            ))
    return sorted(zones, key=lambda zone: (zone.active_time, zone.bos_index))


def _build_zones_legacy(
    candles: list[Candle], timeframe: str, seconds: int, left: int, right: int
) -> list[Zone]:
    """Legacy reference implementation retained for comparison."""
    swings = _swings(candles, left, right, seconds)
    broken: set[tuple[int, int]] = set()
    zones: list[Zone] = []
    for bos_index, candle in enumerate(candles):
        decision_time = candle.timestamp + seconds
        eligible = [s for s in swings if s.confirmed_time <= decision_time and s.index < bos_index]
        for direction in (1, -1):
            relevant = [s for s in eligible if s.direction == direction and (s.index, direction) not in broken]
            crossed_swings = [s for s in relevant
                              if (candle.close > s.price if direction > 0 else candle.close < s.price)]
            if not crossed_swings:
                continue
            swing = max(crossed_swings, key=lambda item: item.index)
            opposite = [s for s in eligible if s.direction == -direction and s.index < swing.index]
            if not opposite:
                continue
            strong = max(opposite, key=lambda item: item.index)
            origin_index = _last_opposite_candle(candles, strong.index, bos_index, direction)
            if origin_index is None:
                continue
            origin = candles[origin_index]
            if direction > 0:
                lower, upper = origin.low, max(origin.open, origin.close)
                distal, proximal = lower, upper
            else:
                lower, upper = min(origin.open, origin.close), origin.high
                distal, proximal = upper, lower
            if upper <= lower:
                continue
            broken.update((item.index, direction) for item in crossed_swings)
            zones.append(Zone(
                zone_id=f"{timeframe}-{bos_index}-{direction}", timeframe=timeframe,
                direction=direction, lower=lower, upper=upper, distal=distal,
                proximal=proximal, origin_index=origin_index, origin_time=origin.timestamp,
                bos_index=bos_index, active_time=decision_time, broken_level=swing.price,
                strong_level=strong.price, weak_level=swing.price,
            ))
    return sorted(zones, key=lambda zone: (zone.active_time, zone.bos_index))


def _touches(candle: Candle, zone: Zone) -> bool:
    return candle.low <= zone.upper and candle.high >= zone.lower


def _invalidated(candle: Candle, zone: Zone) -> bool:
    return candle.close < zone.distal if zone.direction > 0 else candle.close > zone.distal


def _bars_between(
    candles: list[Candle], starts: list[int], start_time: int, end_time: int, seconds: int
) -> list[Candle]:
    left = bisect_left(starts, start_time)
    right = bisect_right(starts, end_time - seconds)
    return candles[left:right]


def _zone_overlap(first: Zone, second: Zone) -> float:
    overlap = max(0.0, min(first.upper, second.upper) - max(first.lower, second.lower))
    return overlap / (second.upper - second.lower)


def _latest_zone(zones: list[Zone], time: int) -> Optional[Zone]:
    eligible = [zone for zone in zones if zone.active_time <= time]
    return max(eligible, key=lambda zone: zone.active_time) if eligible else None


class SwingIndex:
    """Confirmed swings with binary-search access by confirmation time."""

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
    """Map confirmed swing stops and retain both formation and confirmation times."""
    eligible = [s for s in swings if minimum_time <= s.confirmed_time <= available_time]
    levels: list[LiquidityLevel] = []
    last_same: dict[int, Swing] = {}
    for swing in eligible:
        equal = False
        price = swing.price
        formation_time = swing.time
        confirmation_time = swing.confirmed_time
        previous = last_same.get(swing.direction)
        if previous is not None:
            tolerance = max(abs(previous.price), abs(swing.price)) * tolerance_bps / 10000.0
            if abs(previous.price - swing.price) <= tolerance:
                equal = True
                price = (previous.price + swing.price) / 2.0
                # An equal-high/low pool exists only when its second swing prints.
                formation_time = swing.time
                confirmation_time = swing.confirmed_time
        last_same[swing.direction] = swing
        levels.append(LiquidityLevel(
            swing.direction, price, formation_time, confirmation_time, equal
        ))
    return levels


def _refined_zones(
    h4_zone: Zone, zones15: list[Zone], expiry_time: int, params: Params,
) -> list[tuple[Zone, Zone]]:
    """Refine the 4H POI on 15m: counter-flow pullback, then an aligned 15m BOS.

    The refinement forms while price works toward the POI, so it is collected
    across the whole life of the narrative rather than being required to exist
    before price ever arrives. Each refined zone still carries the counter-flow
    shift that preceded it, and only bars already closed are ever used.
    """
    counter_shifts = [zone for zone in zones15
                      if zone.direction == -h4_zone.direction
                      and h4_zone.active_time < zone.active_time < expiry_time]
    if not counter_shifts:
        return []
    first_counter = min(counter_shifts, key=lambda zone: zone.active_time)

    refined: list[tuple[Zone, Zone]] = []
    for zone in zones15:
        if zone.direction != h4_zone.direction:
            continue
        if not (first_counter.active_time < zone.active_time < expiry_time):
            continue
        if _zone_overlap(h4_zone, zone) < params.min_nested_overlap:
            continue
        preceding = [item for item in counter_shifts if item.active_time < zone.active_time]
        if preceding:
            refined.append((zone, max(preceding, key=lambda item: item.active_time)))
    return sorted(refined, key=lambda pair: pair[0].active_time)


def _next_h4_narrative_time(zones4: list[Zone], zone: Zone) -> int:
    """The narrative ends only when 4H structure breaks the other way.

    A POI stays actionable while the higher-timeframe story is unchanged. Any
    same-direction continuation BOS keeps that story intact, so only an
    opposing 4H break retires the zone.
    """
    later = [item.active_time for item in zones4
             if item.active_time > zone.active_time and item.direction == -zone.direction]
    return min(later) if later else 2**63 - 1


def _h4_zone_expiry(zones4: list[Zone], zone: Zone, h4: list[Candle]) -> int:
    """Retire the POI at the opposing 4H break or when its distal line closes through."""
    narrative_end = _next_h4_narrative_time(zones4, zone)
    for candle in h4:
        close_time = candle.timestamp + H4_SECONDS
        if close_time <= zone.active_time:
            continue
        if close_time >= narrative_end:
            break
        if _invalidated(candle, zone):
            return close_time
    return narrative_end


def _find_setups(
    h4: list[Candle], m5: list[Candle], zones4: list[Zone],
    zones15: list[Zone], params: Params,
) -> list[Setup]:
    """Alert on the first 5m return to a fresh refined 15m POI inside the 4H POI.

    Order of operations matches the model: the 4H POI defines the narrative, the
    15m pullback and aligned shift refine it, an alert sits at the refined zone
    edge, and price is allowed to come to that zone.
    """
    starts5 = [c.timestamp for c in m5]
    swings4 = _swings(h4, params.h4_swing_left, params.h4_swing_right, H4_SECONDS)
    setups: list[Setup] = []
    for h4_zone in zones4:
        expiry = _h4_zone_expiry(zones4, h4_zone, h4)
        candidates = _refined_zones(h4_zone, zones15, expiry, params)
        if not candidates:
            continue

        chosen: Optional[Setup] = None
        for refined, counter in candidates:
            first_index = bisect_left(starts5, refined.active_time)
            for index in range(first_index, len(m5)):
                candle = m5[index]
                close_time = candle.timestamp + M5_SECONDS
                if close_time >= expiry or _invalidated(candle, refined):
                    break
                if not _touches(candle, refined):
                    continue
                h4_liquidity = _liquidity_levels(
                    swings4, close_time, params.equal_level_tolerance_bps,
                    minimum_time=h4_zone.origin_time,
                )
                desired_side = -1 if h4_zone.direction > 0 else 1
                relevant = [level for level in h4_liquidity if level.direction == desired_side]
                liquidity = max(relevant, key=lambda level: (level.equal, level.confirmation_time),
                                default=None)
                chosen = Setup(h4_zone, refined, counter, index, close_time, liquidity)
                break
            if chosen is not None:
                break  # one trade per 4H POI, taken at its first valid refinement
        if chosen is not None:
            setups.append(chosen)
    return sorted(setups, key=lambda setup: setup.alert_time)


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
        if candle.timestamp + M5_SECONDS >= expiry_time or _invalidated(candle, zone):
            return None
        triggered = candle.high >= trigger if direction > 0 else candle.low <= trigger
        if not triggered:
            continue
        entry = max(trigger, candle.open) if direction > 0 else min(trigger, candle.open)
        if (direction > 0 and entry <= stop) or (direction < 0 and entry >= stop):
            return None
        return index, candle.timestamp, entry
    return None


def _zone_distal_stop(zone: Zone, params: Params) -> float:
    """Protect the refined 15m thesis beyond its distal zone boundary."""
    buffer = abs(zone.distal) * params.stop_buffer_bps / 10000.0
    return zone.distal - buffer if zone.direction > 0 else zone.distal + buffer


def _aggressive_candidate(
    setup: Setup, m5: list[Candle], swings5: SwingIndex, params: Params,
    expiry_time: int,
) -> Optional[Candidate]:
    direction = setup.h4_zone.direction
    for index in range(setup.alert_index, len(m5)):
        candle = m5[index]
        close_time = candle.timestamp + M5_SECONDS
        if close_time >= expiry_time or _invalidated(candle, setup.m15_zone):
            return None
        swept = _swept_liquidity(candle, direction, swings5, params)
        directional_close = candle.close > candle.open if direction > 0 else candle.close < candle.open
        rejected = candle.close > setup.m15_zone.lower if direction > 0 else candle.close < setup.m15_zone.upper
        if swept is None or not directional_close or not rejected:
            continue
        buffer = candle.close * params.stop_buffer_bps / 10000.0
        trigger = candle.high + buffer if direction > 0 else candle.low - buffer
        stop = _zone_distal_stop(setup.m15_zone, params)
        fill = _order_fill(m5, index, trigger, direction, stop, setup.m15_zone,
                           expiry_time)
        if fill is None:
            return None
        fill_index, fill_time, entry = fill
        return Candidate(setup, "aggressive_liquidation", index, close_time,
                         trigger, stop, fill_index, fill_time, entry,
                         swept.price, swept.price)
    return None


def _conservative_candidate(
    setup: Setup, m5: list[Candle], swings5: SwingIndex, params: Params,
    expiry_time: int,
) -> Optional[Candidate]:
    direction = setup.h4_zone.direction
    reference = _recent_confirmed_swing(
        swings5, 1 if direction > 0 else -1, setup.alert_time,
        max(0, setup.alert_time - params.max_liquidity_lookback_5m * M5_SECONDS),
    )
    if reference is None:
        return None
    for index in range(setup.alert_index + 1, len(m5)):
        candle = m5[index]
        close_time = candle.timestamp + M5_SECONDS
        if close_time >= expiry_time or _invalidated(candle, setup.m15_zone):
            return None
        shifted = candle.close > reference.price if direction > 0 else candle.close < reference.price
        if not shifted:
            continue
        buffer = candle.close * params.stop_buffer_bps / 10000.0
        trigger = candle.high + buffer if direction > 0 else candle.low - buffer
        stop = _zone_distal_stop(setup.m15_zone, params)
        fill = _order_fill(m5, index, trigger, direction, stop, setup.m15_zone,
                           expiry_time)
        if fill is None:
            return None
        fill_index, fill_time, entry = fill
        return Candidate(setup, "conservative_mss", index, close_time,
                         trigger, stop, fill_index, fill_time, entry,
                         reference.price, None)
    return None


def _zone_state_at(zone: Zone, candles15: list[Candle], starts15: list[int], time: int) -> str:
    for candle in _bars_between(candles15, starts15, zone.active_time, time, M15_SECONDS):
        if _invalidated(candle, zone):
            return "invalid"
        if _touches(candle, zone):
            return "mitigated"
    return "fresh"


def _nearest_target(
    candidate: Candidate, zones15: list[Zone], candles15: list[Candle], starts15: list[int]
) -> Optional[tuple[Zone, float]]:
    """Nearest still-unmitigated opposing 15m zone ahead of the entry."""
    direction = candidate.setup.h4_zone.direction
    reachable: list[tuple[float, Zone, float]] = []
    for zone in zones15:
        if zone.direction != -direction or zone.active_time > candidate.fill_time:
            continue
        target = zone.lower if direction > 0 else zone.upper
        distance = direction * (target - candidate.entry)
        if distance > 0:
            reachable.append((distance, zone, target))
    # Freshness is the expensive check, so only pay for it nearest-first.
    for distance, zone, target in sorted(reachable, key=lambda item: item[0]):
        if _zone_state_at(zone, candles15, starts15, candidate.fill_time) == "fresh":
            return zone, target
    return None


def _event(event_type: str, timestamp: int, **fields: object) -> dict:
    event = {"timestamp": to_iso(timestamp), "type": event_type}
    for key, value in fields.items():
        if isinstance(value, float):
            event[key] = round(value, 8)
        elif value is not None:
            event[key] = value
    return event


def _simulate(
    candidate: Candidate, target_zone: Zone, target: float, m5: list[Candle], symbol: str
) -> dict:
    direction = candidate.setup.h4_zone.direction
    exit_index: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason = "open"
    # Fill is intrabar; stop-first is deliberately conservative if both are touched.
    for index in range(candidate.fill_index, len(m5)):
        candle = m5[index]
        stop_hit = candle.low <= candidate.stop if direction > 0 else candle.high >= candidate.stop
        target_hit = candle.high >= target if direction > 0 else candle.low <= target
        if stop_hit:
            gap = candle.open <= candidate.stop if direction > 0 else candle.open >= candidate.stop
            exit_price = (min(candidate.stop, candle.open) if direction > 0 and gap
                          else max(candidate.stop, candle.open) if direction < 0 and gap
                          else candidate.stop)
            exit_index, exit_reason = index, "stop_loss"
            break
        if target_hit:
            gap = candle.open >= target if direction > 0 else candle.open <= target
            exit_price = (max(target, candle.open) if direction > 0 and gap
                          else min(target, candle.open) if direction < 0 and gap
                          else target)
            exit_index, exit_reason = index, "take_profit"
            break

    setup = candidate.setup
    h4 = setup.h4_zone
    refined = setup.m15_zone
    direction_name = "long" if direction > 0 else "short"
    liquidity_side = "sell_side" if direction > 0 else "buy_side"
    signal_candle = m5[candidate.signal_index]
    fill_candle = m5[candidate.fill_index]
    events = [
        # The order-block candle forms at origin_time, but is only actionable
        # after the BOS candle closes at active_time.
        _event("order_block", h4.origin_time, timeframe="4h",
               direction="demand" if direction > 0 else "supply",
               lower=h4.lower, upper=h4.upper, origin_time=to_iso(h4.origin_time),
               confirmed_at=to_iso(h4.active_time), confirmation="4h_bos_close",
               bos_level=h4.broken_level, strong_level=h4.strong_level,
               weak_level=h4.weak_level, narrative="bullish" if direction > 0 else "bearish"),
    ]
    if setup.liquidity is not None:
        events.append(_event("liquidity_level_formed", setup.liquidity.formation_time,
                             timeframe="4h", direction=liquidity_side,
                             price=setup.liquidity.price, equal_level=setup.liquidity.equal,
                             confirmed_at=to_iso(setup.liquidity.confirmation_time)))
    events.extend([
        _event("internal_pullback_shift", setup.counter_shift.active_time,
               timeframe="15m", direction="bearish" if direction > 0 else "bullish",
               level=setup.counter_shift.broken_level, confirmation="15m_bos_close"),
        _event("mss", refined.active_time, timeframe="15m",
               direction="bullish" if direction > 0 else "bearish",
               level=refined.broken_level, strong_level=refined.strong_level,
               weak_level=refined.weak_level, confirmation="15m_bos_close"),
        # This is the original 15m order-block candle; confirmation makes it
        # eligible for trading only after the aligned 15m BOS has closed.
        _event("order_block", refined.origin_time, timeframe="15m",
               direction="demand" if direction > 0 else "supply",
               lower=refined.lower, upper=refined.upper,
               origin_time=to_iso(refined.origin_time),
               confirmed_at=to_iso(refined.active_time), confirmation="15m_bos_close"),
        # The alert is caused by the closing 5m touch bar, although it refers
        # to a 15m zone.
        _event("zone_alert", setup.alert_time, timeframe="5m", zone_timeframe="15m",
               direction=direction_name, level=refined.proximal,
               lower=refined.lower, upper=refined.upper,
               confirmation="5m_touch_bar_close"),
    ])
    signal_type = "liquidation_candle" if candidate.model.startswith("aggressive") else "mss"
    events.append(_event(signal_type, candidate.signal_time, timeframe="5m",
                         direction=direction_name, trigger=candidate.trigger,
                         stop=candidate.stop, stop_basis="15m_zone_distal_plus_buffer",
                         stop_reference=refined.distal,
                         confirmation_level=candidate.confirmation_level,
                         swept_level=candidate.swept_level,
                         bar_open_time=to_iso(signal_candle.timestamp),
                         confirmation="5m_signal_bar_close"))
    events.extend([
        # OHLC data cannot identify the exact intrabar crossing. The event time
        # is therefore the execution bar's opening timestamp, with its full
        # observable window included for transparent charting.
        _event("entry_stop_filled", candidate.fill_time, timeframe="5m",
               direction=direction_name, price=candidate.entry,
               execution_window_end=to_iso(fill_candle.timestamp + M5_SECONDS),
               timestamp_basis="5m_execution_bar_open"),
        _event("target_zone", target_zone.origin_time, timeframe="15m",
               direction="supply" if direction > 0 else "demand",
               lower=target_zone.lower, upper=target_zone.upper, price=target,
               origin_time=to_iso(target_zone.origin_time),
               confirmed_at=to_iso(target_zone.active_time),
               selected_at=to_iso(candidate.fill_time), confirmation="15m_bos_close"),
    ])

    if exit_index is None:
        exit_time = None
        outcome = "open"
    else:
        exit_time_int = m5[exit_index].timestamp + M5_SECONDS
        exit_time = to_iso(exit_time_int)
        outcome = "win" if exit_reason == "take_profit" else "loss"
        exit_candle = m5[exit_index]
        events.append(_event(
            "final_exit", exit_time_int, price=float(exit_price), reason=exit_reason,
            timeframe="5m", bar_open_time=to_iso(exit_candle.timestamp),
            timestamp_basis="5m_exit_bar_close",
        ))
    events.sort(key=lambda event: event["timestamp"])
    return {
        "strategy": "strategy_145_naked_mtf_structure_liquidity",
        "setup": candidate.model,
        "symbol": symbol,
        "direction": direction_name,
        "entry_time": to_iso(candidate.fill_time),
        "entry_timestamp": candidate.fill_time,
        "entry_price": round(candidate.entry, 8),
        "stop_loss": round(candidate.stop, 8),
        "stop_basis": "15m_zone_distal_plus_buffer",
        "stop_reference_price": round(refined.distal, 8),
        "take_profit": round(target, 8),
        "exit_time": exit_time,
        "exit_timestamp": (m5[exit_index].timestamp + M5_SECONDS) if exit_index is not None else None,
        "exit_price": round(float(exit_price), 8) if exit_price is not None else None,
        "outcome": outcome,
        "exit_reason": exit_reason,
        "h4_narrative": "bullish" if direction > 0 else "bearish",
        "h4_zone_id": h4.zone_id,
        "m15_zone_id": refined.zone_id,
        "events": events,
        "reason": ("Native 4H latest BOS extreme POI + 15m counter-flow and aligned BOS refinement + "
                   f"5m {candidate.model}; target nearest fresh opposing 15m zone"),
    }


def generate_trades(
    candles4h: list[Candle], candles15m: list[Candle], candles5m: list[Candle],
    symbol: str = "UNKNOWN", params: Optional[Params] = None,
) -> list[dict]:
    """Generate symmetric, causal trades exclusively from three native feeds."""
    settings = params or Params()
    h4 = _clean_native(candles4h, "4H", H4_SECONDS)
    m15 = _clean_native(candles15m, "15m", M15_SECONDS)
    m5 = _clean_native(candles5m, "5m", M5_SECONDS)
    h4, m15, m5 = _common_overlap(h4, m15, m5)

    zones4 = _build_zones_fast(h4, "4h", H4_SECONDS,
                               settings.h4_swing_left, settings.h4_swing_right)
    zones15 = _build_zones_fast(m15, "15m", M15_SECONDS,
                                settings.m15_swing_left, settings.m15_swing_right)
    swings5 = SwingIndex(_swings(m5, settings.m5_swing_left, settings.m5_swing_right, M5_SECONDS))
    starts15 = [candle.timestamp for candle in m15]
    setups = _find_setups(h4, m5, zones4, zones15, settings)

    candidates: list[Candidate] = []
    for setup in setups:
        expiry = _h4_zone_expiry(zones4, setup.h4_zone, h4)
        aggressive = _aggressive_candidate(setup, m5, swings5, settings, expiry)
        conservative = _conservative_candidate(setup, m5, swings5, settings, expiry)
        models = [candidate for candidate in (aggressive, conservative) if candidate is not None]
        if models:
            candidates.append(min(models, key=lambda item: (item.fill_time, item.signal_time,
                                                              0 if item.model.startswith("aggressive") else 1)))
    candidates.sort(key=lambda item: (item.fill_time, item.setup.h4_zone.zone_id))

    trades: list[dict] = []
    used_h4_zones: set[str] = set()
    used_m15_zones: set[str] = set()
    last_exit_time = -1
    for candidate in candidates:
        if candidate.setup.h4_zone.zone_id in used_h4_zones:
            continue
        if candidate.setup.m15_zone.zone_id in used_m15_zones or candidate.fill_time < last_exit_time:
            continue
        target = _nearest_target(candidate, zones15, m15, starts15)
        if target is None:
            continue
        target_zone, target_price = target
        trade = _simulate(candidate, target_zone, target_price, m5, symbol)
        trade["trade_number"] = len(trades) + 1
        trades.append(trade)
        used_h4_zones.add(candidate.setup.h4_zone.zone_id)
        used_m15_zones.add(candidate.setup.m15_zone.zone_id)
        last_exit_time = (int(trade["exit_timestamp"])
                          if trade["exit_timestamp"] is not None else 2**63 - 1)

    emit_progress("strategy", 100,
                  f"S145 generated {len(trades)} trades from native 4H/15m/5m structure")
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
            "S145 input validation failed. "
            f"4H={os.path.basename(csv4h)}, "
            f"15m={os.path.basename(csv15m)}, "
            f"5m={os.path.basename(csv5m)}. {exc}"
        ) from exc
    save_trades(trades, output)
    print(f"Saved {len(trades)} trades to {output}")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strategy 145: indicator-free native 4H/15m/5m structure and liquidity execution"
    )
    parser.add_argument("--csv4h", required=True, help="Path to the required native 4-hour OHLCV CSV")
    parser.add_argument("--csv15m", required=True, help="Path to the required native 15-minute OHLCV CSV")
    parser.add_argument("--csv5m", required=True, help="Path to the required native 5-minute OHLCV CSV")
    parser.add_argument("--output", required=True, help="Path for dashboard-compatible trade JSON output")
    args = parser.parse_args()
    run_strategy(args.csv4h, args.csv15m, args.csv5m, args.output)


if __name__ == "__main__":
    main()
