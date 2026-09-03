#!/usr/bin/env python3
"""
Strategy 147: 4H POI Reaction with 15m Confirmation and 5m Execution.

Where Strategy 146 travels TOWARD an unmitigated 4H zone, this model waits for
price to ARRIVE at a 4H zone and then trades the reaction away from it. The 4H
zone is the decision point, not the destination.

Sequence, all causal, three native feeds, nothing resampled:

  1. 4H builds structural zones. A confirmed zone becomes a point of interest and
     STAYS one: there is no age limit, by design. An old untested 4H zone is as
     valid as a fresh one.
  2. Price must ARRIVE at the zone. Arrival is detected on closed 15m bars.
  3. 15m must then confirm, in this order:
       a) a change of character in the reaction direction, i.e. a 15m close
          beyond the most recently confirmed opposing 15m swing, and
       b) only after that, a fresh aligned 15m zone, which becomes the refined
          entry area and supplies the stop.
  4. 5m executes on a return to that 15m zone, via either a liquidity-sweep
     liquidation candle or a conservative market-structure shift.

Risk and reward:
  * Stop sits just beyond the 15m refinement zone's distal edge, NOT beyond the
    4H zone. A 4H zone can be over a hundred pips tall; anchoring the stop there
    would make a 1.5R target a hundred-plus pip move and almost never fill. The
    15m zone is the level that actually invalidates the reaction thesis.
  * Take profit is a fixed 1.5R measured from the real entry fill.
  * Trading costs are recorded for reporting but do not reject a setup.

Lifecycle rules:
  * A zone may be traded again on later touches. Arrival episodes are separate:
    price must leave the zone and come back for a new attempt.
  * A zone is permanently invalidated once a 15m bar CLOSES fully beyond its
    distal edge. The structure that made it interesting is gone.
  * There is no holding limit. A trade runs until its stop or its target.

Entry is modelled as a market fill at the OPEN of the 5m bar after the signal bar
closes, which is what the live engine does in its default market entry mode.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Optional

from core.core import (
    Candle,
    emit_progress,
    infer_pip_size,
    load_csv,
    parse_csv_filename,
    round_turn_cost_pips,
    save_trades,
    to_iso,
)

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
    # Stop sits this far beyond the 15m refinement zone's distal edge.
    stop_buffer_bps: float = 0.5
    # Fixed reward. 1.5R is the whole point of the model, not a tunable target.
    reward_risk: float = 1.5
    # A 15m change of character must appear within this many 15m bars of arrival,
    # otherwise price is merely sitting in the zone with no reaction.
    max_wait_bars_15m_choch: int = 16
    # The aligned 15m refinement zone must appear within this many 15m bars of
    # the change of character.
    max_wait_bars_15m_zone: int = 16
    # Price must return to the refinement zone, and confirm on 5m, within this
    # many 5m bars of the zone becoming active.
    max_wait_bars_5m: int = 288
    # Liquidity lookback for the aggressive 5m model.
    max_liquidity_lookback_5m: int = 72
    # Floor on structural risk, guarding against a 15m zone so thin that the
    # stop sits inside normal spread noise.
    min_stop_bps: float = 1.0


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
class Arrival:
    """One visit of price to a 4H zone, detected on closed 15m bars."""
    zone: Zone
    index: int          # 15m index of the touching bar
    time: int           # close time of the touching bar
    episode: int        # 1-based visit counter for this zone


@dataclass(frozen=True)
class Reaction:
    """A completed 15m confirmation: change of character, then a fresh zone."""
    poi: Zone
    arrival: Arrival
    choch_index: int
    choch_time: int
    choch_level: float
    refine_zone: Zone


@dataclass(frozen=True)
class Candidate:
    reaction: Reaction
    direction: int
    model: str
    alert_index: int
    alert_time: int
    signal_index: int
    signal_time: int
    stop: float
    fill_index: int
    fill_time: int
    entry: float
    target: float
    confirmation_level: float
    swept_level: Optional[float]
    risk_pips: float
    target_pips: float
    cost_pips: float


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
    # Weekend, holiday and DST gaps exceed the native interval and must not be
    # mistaken for the wrong timeframe, so judge the dominant cadence.
    ordinary = sorted(difference for difference in differences if difference <= seconds * 4)
    typical = ordinary[len(ordinary) // 2] if ordinary else min(differences)
    if typical != seconds:
        raise ValueError(
            f"{label} input is not native {label}: observed cadence is {typical} seconds, "
            f"expected {seconds}. Provide the real native timeframe CSV; "
            "this strategy never resamples."
        )
    if any(difference < seconds for difference in differences):
        raise ValueError(f"{label} contains a sub-{label} interval")
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
        raise ValueError("insufficient common native data (need 20 4H, 100 15m, 300 5m bars)")
    return feeds


# ---------------------------------------------------------------------------
# Structure: swings and zones
# ---------------------------------------------------------------------------

def _swings(candles: list[Candle], left: int, right: int, seconds: int) -> list[Swing]:
    """Fractal swings, timestamped by when the right-hand side CONFIRMS them."""
    swings: list[Swing] = []
    for index in range(left, len(candles) - right):
        candle = candles[index]
        high_left = all(candle.high > candles[j].high for j in range(index - left, index))
        high_right = all(candle.high >= candles[j].high
                         for j in range(index + 1, index + right + 1))
        low_left = all(candle.low < candles[j].low for j in range(index - left, index))
        low_right = all(candle.low <= candles[j].low
                        for j in range(index + 1, index + right + 1))
        confirmation = candles[index + right].timestamp + seconds
        if high_left and high_right:
            swings.append(Swing(index, 1, candle.high, candle.timestamp, confirmation))
        if low_left and low_right:
            swings.append(Swing(index, -1, candle.low, candle.timestamp, confirmation))
    return sorted(swings, key=lambda item: (item.confirmed_time, item.index, -item.direction))


class SwingIndex:
    """Confirmed swings, queryable by 'what was known at time T'."""

    __slots__ = ("by_direction", "times")

    def __init__(self, swings: list[Swing]):
        self.by_direction: dict[int, list[Swing]] = {1: [], -1: []}
        for swing in swings:
            self.by_direction[swing.direction].append(swing)
        self.times = {
            direction: [item.confirmed_time for item in items]
            for direction, items in self.by_direction.items()
        }

    def latest_before(self, direction: int, moment: int) -> Optional[Swing]:
        """Most recently confirmed swing of `direction` known strictly before `moment`."""
        items = self.by_direction[direction]
        cutoff = bisect_right(self.times[direction], moment)
        return items[cutoff - 1] if cutoff > 0 else None

    def confirmed_between(self, direction: int, start: int, end: int) -> list[Swing]:
        items = self.by_direction[direction]
        times = self.times[direction]
        return items[bisect_left(times, start):bisect_right(times, end)]


def _zone_identity(
    timeframe: str, direction: int, origin_time: int, lower: float, upper: float
) -> str:
    """Stable id derived from WHAT the zone is, never from where it sits in an array.

    An index-based id is only unique inside one fixed candle array. Live, the feed
    is a rolling window, so an index-based id renumbers the same physical zone on
    every new bar, which breaks dedupe keys and cross-timeframe joins. Origin
    timestamp plus a digest of the boundaries is invariant under windowing.
    """
    digest = hashlib.blake2s(
        f"{lower:.10f}:{upper:.10f}".encode("utf-8"), digest_size=3
    ).hexdigest()
    return f"{timeframe}-{origin_time}-{direction}-{digest}"


def _last_opposite_candle(
    candles: list[Candle], start: int, end: int, direction: int
) -> Optional[int]:
    for index in range(end - 1, start - 1, -1):
        candle = candles[index]
        if ((direction > 0 and candle.close < candle.open)
                or (direction < 0 and candle.close > candle.open)):
            return index
    return None


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
                timeframe=timeframe, direction=direction, lower=lower, upper=upper,
                distal=lower if direction > 0 else upper,
                proximal=upper if direction > 0 else lower,
                origin_index=origin_index, origin_time=origin.timestamp,
                bos_index=bos_index, active_time=decision_time, broken_level=swing.price,
                strong_level=strong.price, weak_level=swing.price,
            ))
    return sorted(zones, key=lambda zone: (zone.active_time, zone.bos_index))


def _dedupe_zones(zones: list[Zone]) -> list[Zone]:
    """Collapse repeated confirmations of one physical zone, keeping the earliest."""
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


def _closed_through(candle: Candle, zone: Zone) -> bool:
    """True when a bar CLOSES fully beyond the zone's distal edge.

    This is the user-specified invalidation: the zone is not merely wicked
    through, price has accepted beyond the level that defined it.
    """
    return candle.close < zone.distal if zone.direction > 0 else candle.close > zone.distal


def _zone_distal_stop(zone: Zone, params: Params) -> float:
    buffer = abs(zone.distal) * params.stop_buffer_bps / 10000.0
    return zone.distal - buffer if zone.direction > 0 else zone.distal + buffer


# ---------------------------------------------------------------------------
# Step 2: arrival of price at a 4H point of interest
# ---------------------------------------------------------------------------

def _arrivals(zones4: list[Zone], m15: list[Candle], params: Params) -> list[Arrival]:
    """Every visit of price to every 4H zone, detected on closed 15m bars.

    A zone may be traded again on later touches, so each visit is its own
    opportunity. Price must LEAVE the zone before a new visit can begin,
    otherwise one long stay inside the zone would spawn an arrival per bar.

    Scanning stops permanently the first time a 15m bar closes fully beyond the
    distal edge: the zone is invalidated from that moment on.
    """
    starts = [candle.timestamp for candle in m15]
    arrivals: list[Arrival] = []
    for zone in zones4:
        # Only bars that closed at or after the zone became active can see it.
        index = bisect_left(starts, zone.active_time)
        inside = False
        episode = 0
        while index < len(m15):
            candle = m15[index]
            if _closed_through(candle, zone):
                break  # zone invalidated for good
            if _touches(candle, zone):
                if not inside:
                    episode += 1
                    arrivals.append(Arrival(
                        zone=zone, index=index,
                        time=candle.timestamp + M15_SECONDS, episode=episode,
                    ))
                inside = True
            else:
                inside = False
            index += 1
    arrivals.sort(key=lambda item: (item.time, item.zone.bos_index, item.zone.direction))
    return arrivals


# ---------------------------------------------------------------------------
# Step 3: the 15m reaction — change of character, THEN a fresh aligned zone
# ---------------------------------------------------------------------------

def _find_choch(
    arrival: Arrival, m15: list[Candle], swings15: SwingIndex, params: Params
) -> Optional[tuple[int, int, float]]:
    """First 15m close beyond the opposing swing, i.e. the reaction begins.

    For a 4H demand zone the reaction is bullish, so we need a close ABOVE the
    most recently confirmed 15m swing HIGH. The reference swing is resolved per
    candidate bar, using only swings confirmed before that bar closed, so this
    never reads the future.
    """
    direction = arrival.zone.direction
    limit = min(len(m15), arrival.index + 1 + params.max_wait_bars_15m_choch)
    for index in range(arrival.index, limit):
        candle = m15[index]
        decision_time = candle.timestamp + M15_SECONDS
        if _closed_through(candle, arrival.zone):
            return None  # zone invalidated before any reaction appeared
        reference = swings15.latest_before(1 if direction > 0 else -1, decision_time)
        if reference is None:
            continue
        # The swing must predate the bar that breaks it.
        if reference.index >= index:
            continue
        broke = (candle.close > reference.price if direction > 0
                 else candle.close < reference.price)
        if broke:
            return index, decision_time, reference.price
    return None


def _find_refinement_zone(
    reaction_direction: int, choch_time: int, zones15: list[Zone],
    zone_times: list[int], params: Params,
) -> Optional[Zone]:
    """First aligned 15m zone confirmed at or after the change of character.

    Ordering is enforced here: the zone must become active no earlier than the
    change of character, so the sequence is always arrival -> CHoCH -> zone.
    """
    horizon = choch_time + params.max_wait_bars_15m_zone * M15_SECONDS
    start = bisect_left(zone_times, choch_time)
    for index in range(start, len(zones15)):
        zone = zones15[index]
        if zone.active_time > horizon:
            return None
        if zone.direction == reaction_direction:
            return zone
    return None


def _reactions(
    arrivals: list[Arrival], m15: list[Candle], swings15: SwingIndex,
    zones15: list[Zone], params: Params,
) -> list[Reaction]:
    zone_times = [zone.active_time for zone in zones15]
    reactions: list[Reaction] = []
    for arrival in arrivals:
        found = _find_choch(arrival, m15, swings15, params)
        if found is None:
            continue
        choch_index, choch_time, choch_level = found
        refine = _find_refinement_zone(
            arrival.zone.direction, choch_time, zones15, zone_times, params)
        if refine is None:
            continue
        reactions.append(Reaction(
            poi=arrival.zone, arrival=arrival, choch_index=choch_index,
            choch_time=choch_time, choch_level=choch_level, refine_zone=refine,
        ))
    return reactions


# ---------------------------------------------------------------------------
# Step 4: the 5m execution models
# ---------------------------------------------------------------------------

def _swept_liquidity(
    candle: Candle, direction: int, swings5: SwingIndex, params: Params
) -> Optional[Swing]:
    """A recent opposing 5m extreme taken out by this bar's wick."""
    window_start = candle.timestamp - params.max_liquidity_lookback_5m * M5_SECONDS
    wanted = -1 if direction > 0 else 1
    levels = swings5.confirmed_between(wanted, window_start, candle.timestamp)
    for swing in reversed(levels):
        if direction > 0 and candle.low <= swing.price:
            return swing
        if direction < 0 and candle.high >= swing.price:
            return swing
    return None


def _aggressive_fires(
    candle: Candle, zone: Zone, direction: int, swings5: SwingIndex, params: Params
) -> Optional[float]:
    """Liquidity sweep, then a rejection close back in the zone's favour."""
    if _closed_through(candle, zone):
        return None
    swept = _swept_liquidity(candle, direction, swings5, params)
    if swept is None:
        return None
    directional_close = candle.close > candle.open if direction > 0 else candle.close < candle.open
    rejected = candle.close > zone.lower if direction > 0 else candle.close < zone.upper
    if not directional_close or not rejected:
        return None
    return swept.price


def _conservative_fires(
    candle: Candle, zone: Zone, direction: int, reference_price: float
) -> bool:
    if _closed_through(candle, zone):
        return False
    return candle.close > reference_price if direction > 0 else candle.close < reference_price


def _first_zone_touch_5m(zone: Zone, m5: list[Candle], start: int, limit: int) -> int:
    for index in range(start, min(limit, len(m5))):
        if _touches(m5[index], zone):
            return index
    return -1


def _build_candidate(
    reaction: Reaction, m5: list[Candle], starts5: list[int],
    swings5: SwingIndex, symbol: str, params: Params,
) -> Optional[Candidate]:
    """Turn a confirmed 15m reaction into an executable 5m candidate."""
    zone = reaction.refine_zone
    direction = zone.direction

    begin = bisect_left(starts5, zone.active_time)
    if begin >= len(m5):
        return None
    horizon = min(len(m5), begin + params.max_wait_bars_5m)

    alert_index = _first_zone_touch_5m(zone, m5, begin, horizon)
    if alert_index < 0:
        return None
    alert_time = m5[alert_index].timestamp + M5_SECONDS

    reference = swings5.latest_before(1 if direction > 0 else -1, alert_time)

    model: Optional[str] = None
    signal_index = -1
    confirmation_level = 0.0
    swept_level: Optional[float] = None
    # First confirmation after the alert wins; a later one is a different trade.
    for index in range(alert_index, horizon):
        candle = m5[index]
        if _closed_through(candle, zone):
            return None
        swept = _aggressive_fires(candle, zone, direction, swings5, params)
        if swept is not None:
            model, signal_index = "aggressive_liquidation", index
            confirmation_level, swept_level = swept, swept
            break
        if (reference is not None and index > alert_index
                and _conservative_fires(candle, zone, direction, reference.price)):
            model, signal_index = "conservative_mss", index
            confirmation_level = reference.price
            break
    if model is None:
        return None

    # Market fill at the open of the bar after the signal bar closes.
    fill_index = signal_index + 1
    if fill_index >= len(m5):
        return None
    entry = m5[fill_index].open
    fill_time = m5[fill_index].timestamp

    stop = _zone_distal_stop(zone, params)
    risk = direction * (entry - stop)
    if risk <= 0:
        return None

    pip = infer_pip_size(entry, symbol=symbol)
    risk_pips = risk / pip
    if risk_pips < params.min_stop_bps * entry / (10000.0 * pip):
        return None

    target = entry + direction * risk * params.reward_risk
    target_pips = risk_pips * params.reward_risk
    cost_pips = round_turn_cost_pips(entry, symbol=symbol)
    # Costs remain part of the candidate/trade record for transparent reporting;
    # they do not reject the structurally valid setup.

    return Candidate(
        reaction=reaction, direction=direction, model=model,
        alert_index=alert_index, alert_time=alert_time,
        signal_index=signal_index, signal_time=m5[signal_index].timestamp + M5_SECONDS,
        stop=stop, fill_index=fill_index, fill_time=fill_time, entry=entry, target=target,
        confirmation_level=confirmation_level, swept_level=swept_level,
        risk_pips=risk_pips, target_pips=target_pips, cost_pips=cost_pips,
    )


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


def _simulate(candidate: Candidate, m5: list[Candle], symbol: str, params: Params) -> dict:
    direction = candidate.direction
    reaction = candidate.reaction
    poi = reaction.poi
    zone = reaction.refine_zone
    stop = candidate.stop
    target = candidate.target

    # No holding limit: the trade runs until stop or target, or the data ends.
    exit_index: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason = "open"
    for index in range(candidate.fill_index, len(m5)):
        candle = m5[index]
        stop_hit = candle.low <= stop if direction > 0 else candle.high >= stop
        target_hit = candle.high >= target if direction > 0 else candle.low <= target
        # Stop-first when one bar spans both levels: 5m data cannot say which came
        # first, and guessing in our favour would inflate every result.
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

    direction_name = "long" if direction > 0 else "short"
    poi_kind = "demand" if poi.direction > 0 else "supply"
    refine_kind = "demand" if zone.direction > 0 else "supply"
    signal_candle = m5[candidate.signal_index]

    events = [
        _event("order_block", poi.origin_time, timeframe="4h", direction=poi_kind,
               role="poi", lower=poi.lower, upper=poi.upper,
               origin_time=to_iso(poi.origin_time),
               confirmed_at=to_iso(poi.active_time), confirmation="4h_bos_close",
               bos_level=poi.broken_level, strong_level=poi.strong_level,
               weak_level=poi.weak_level,
               narrative="bullish" if poi.direction > 0 else "bearish"),
        _event("zone_alert", reaction.arrival.time, timeframe="15m", zone_timeframe="4h",
               direction=direction_name, level=poi.proximal,
               lower=poi.lower, upper=poi.upper, episode=reaction.arrival.episode,
               confirmation="15m_touch_bar_close", note="price arrived at the 4H POI"),
        _event("mss", reaction.choch_time, timeframe="15m",
               direction="bullish" if direction > 0 else "bearish",
               level=reaction.choch_level, confirmation="15m_choch_close",
               note="change of character in the reaction direction"),
        _event("order_block", zone.origin_time, timeframe="15m", direction=refine_kind,
               role="entry", lower=zone.lower, upper=zone.upper,
               origin_time=to_iso(zone.origin_time),
               confirmed_at=to_iso(zone.active_time), confirmation="15m_bos_close",
               bos_level=zone.broken_level),
        _event("zone_alert", candidate.alert_time, timeframe="5m", zone_timeframe="15m",
               direction=direction_name, level=zone.proximal,
               lower=zone.lower, upper=zone.upper, confirmation="5m_touch_bar_close"),
    ]
    signal_type = ("liquidation_candle" if candidate.model.startswith("aggressive") else "mss")
    events.append(_event(signal_type, candidate.signal_time, timeframe="5m",
                         direction=direction_name, stop=candidate.stop,
                         stop_basis="15m_refinement_zone_distal_plus_buffer",
                         stop_reference=zone.distal,
                         confirmation_level=candidate.confirmation_level,
                         swept_level=candidate.swept_level,
                         bar_open_time=to_iso(signal_candle.timestamp),
                         confirmation="5m_signal_bar_close"))
    events.append(_event("entry_market_filled", candidate.fill_time, timeframe="5m",
                         direction=direction_name, price=candidate.entry,
                         timestamp_basis="5m_execution_bar_open"))
    events.append(_event("target_level", candidate.fill_time, timeframe="5m",
                         direction=direction_name, price=target,
                         basis=f"fixed_{params.reward_risk}R_from_entry",
                         risk_pips=candidate.risk_pips, target_pips=candidate.target_pips,
                         round_turn_cost_pips=candidate.cost_pips))

    if exit_index is None:
        exit_time = None
        exit_timestamp = None
        outcome = "open"
        bars_held = len(m5) - 1 - candidate.fill_index
    else:
        exit_timestamp = m5[exit_index].timestamp + M5_SECONDS
        exit_time = to_iso(exit_timestamp)
        gross = direction * (float(exit_price) - candidate.entry)
        outcome = "win" if gross > 0 else "loss" if gross < 0 else "breakeven"
        bars_held = exit_index - candidate.fill_index
        events.append(_event("final_exit", exit_timestamp, price=float(exit_price),
                             reason=exit_reason, timeframe="5m",
                             bar_open_time=to_iso(m5[exit_index].timestamp),
                             timestamp_basis="5m_exit_bar_close"))
    events.sort(key=lambda event: event["timestamp"])

    realized_r = None
    if exit_price is not None and candidate.risk_pips > 0:
        pip = infer_pip_size(candidate.entry, symbol=symbol)
        realized_r = round(
            (direction * (float(exit_price) - candidate.entry) / pip) / candidate.risk_pips, 3)

    return {
        "strategy": "strategy_147_4h_poi_reaction",
        "setup": candidate.model,
        "symbol": symbol,
        "direction": direction_name,
        "entry_time": to_iso(candidate.fill_time),
        "entry_timestamp": candidate.fill_time,
        "entry_price": round(candidate.entry, 8),
        "stop_loss": round(candidate.stop, 8),
        "stop_basis": "15m_refinement_zone_distal_plus_buffer",
        "stop_reference_price": round(zone.distal, 8),
        "take_profit": round(target, 8),
        "target_basis": f"fixed_{params.reward_risk}R_from_entry",
        "planned_reward_risk": params.reward_risk,
        "realized_r": realized_r,
        "risk_pips": round(candidate.risk_pips, 2),
        "target_pips": round(candidate.target_pips, 2),
        "round_turn_cost_pips": round(candidate.cost_pips, 2),
        "exit_time": exit_time,
        "exit_timestamp": exit_timestamp,
        "exit_price": round(float(exit_price), 8) if exit_price is not None else None,
        "outcome": outcome,
        "exit_reason": exit_reason,
        "bars_held_5m": bars_held,
        "h4_zone_type": poi_kind,
        "h4_narrative": "bullish" if poi.direction > 0 else "bearish",
        "h4_zone_id": poi.zone_id,
        "h4_zone_lower": round(poi.lower, 8),
        "h4_zone_upper": round(poi.upper, 8),
        "h4_zone_origin_time": to_iso(poi.origin_time),
        "h4_zone_confirmed_at": to_iso(poi.active_time),
        "h4_arrival_time": to_iso(reaction.arrival.time),
        "h4_arrival_episode": reaction.arrival.episode,
        "m15_choch_time": to_iso(reaction.choch_time),
        "m15_choch_level": round(reaction.choch_level, 8),
        "m15_zone_id": zone.zone_id,
        "m15_zone_lower": round(zone.lower, 8),
        "m15_zone_upper": round(zone.upper, 8),
        "m15_zone_confirmed_at": to_iso(zone.active_time),
        "m5_alert_time": to_iso(candidate.alert_time),
        "m5_signal_time": to_iso(candidate.signal_time),
        "events": events,
        "reason": (
            f"Price arrived at a native 4H {poi_kind} POI (visit "
            f"{reaction.arrival.episode}); 15m printed a change of character in the "
            f"reaction direction and then a fresh {refine_kind} zone; 5m confirmed via "
            f"{candidate.model}; stop beyond the 15m zone distal edge and a fixed "
            f"{params.reward_risk}R target."
        ),
    }


def generate_trades(
    h4_raw: list[Candle], m15_raw: list[Candle], m5_raw: list[Candle],
    symbol: str = "UNKNOWN", settings: Optional[Params] = None,
) -> list[dict]:
    settings = settings or Params()
    emit_progress("strategy", 10, "Validating native 4H, 15m, and 5m feeds")
    h4 = _clean_native(h4_raw, "4h", H4_SECONDS)
    m15 = _clean_native(m15_raw, "15m", M15_SECONDS)
    m5 = _clean_native(m5_raw, "5m", M5_SECONDS)
    h4, m15, m5 = _common_overlap(h4, m15, m5)

    emit_progress("strategy", 30, "Building 4H points of interest and 15m structure")
    zones4 = _dedupe_zones(_build_zones(h4, "4h", H4_SECONDS,
                                       settings.h4_swing_left, settings.h4_swing_right))
    zones15 = _dedupe_zones(_build_zones(m15, "15m", M15_SECONDS,
                                         settings.m15_swing_left, settings.m15_swing_right))
    swings15 = SwingIndex(_swings(m15, settings.m15_swing_left,
                                  settings.m15_swing_right, M15_SECONDS))
    swings5 = SwingIndex(_swings(m5, settings.m5_swing_left,
                                 settings.m5_swing_right, M5_SECONDS))
    starts5 = [candle.timestamp for candle in m5]

    emit_progress("strategy", 50, "Detecting arrivals at 4H points of interest")
    arrivals = _arrivals(zones4, m15, settings)

    emit_progress("strategy", 65, "Confirming 15m change of character and refinement zones")
    reactions = _reactions(arrivals, m15, swings15, zones15, settings)

    emit_progress("strategy", 80, "Executing 5m confirmations")
    candidates: list[Candidate] = []
    for reaction in reactions:
        candidate = _build_candidate(reaction, m5, starts5, swings5, symbol, settings)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item.fill_time, item.reaction.refine_zone.bos_index))

    # One position at a time, which is what the live engine enforces per symbol.
    trades: list[dict] = []
    last_exit_time = -1
    for candidate in candidates:
        if candidate.fill_time < last_exit_time:
            continue
        trade = _simulate(candidate, m5, symbol, settings)
        trade["trade_number"] = len(trades) + 1
        trades.append(trade)
        last_exit_time = (int(trade["exit_timestamp"])
                          if trade["exit_timestamp"] is not None else 2**63 - 1)

    emit_progress("strategy", 100,
                  f"S147 generated {len(trades)} trades from {len(arrivals)} 4H POI arrivals")
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
            "S147 input validation failed. "
            f"4H={os.path.basename(csv4h)}, 15m={os.path.basename(csv15m)}, "
            f"5m={os.path.basename(csv5m)}. {exc}"
        ) from exc
    save_trades(trades, output)
    print(f"Saved {len(trades)} trades to {output}")
    return trades


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strategy 147: 4H POI reaction with 15m confirmation, 5m entry, fixed 1.5R"
    )
    parser.add_argument("--csv4h", required=True,
                        help="Path to the required native 4-hour OHLCV CSV")
    parser.add_argument("--csv15m", required=True,
                        help="Path to the required native 15-minute OHLCV CSV")
    parser.add_argument("--csv5m", required=True,
                        help="Path to the required native 5-minute OHLCV CSV")
    parser.add_argument("--output", required=True,
                        help="Path for dashboard-compatible trade JSON output")
    args = parser.parse_args()
    run_strategy(args.csv4h, args.csv15m, args.csv5m, args.output)


if __name__ == "__main__":
    main()
