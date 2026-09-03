"""
Live detection for Strategy 146 — entries that travel toward an unmitigated 4H zone.

Parity with the backtest is kept by importing the backtest module itself and
reusing its zone construction, liquidity model and entry predicates. Nothing is
re-implemented here except the parts that must differ live:

  * Destination freshness is measured on CLOSED 4H bars instead of the 5m array,
    because a 4H zone may be far older than the live 5m window. A 4H bar's
    high/low envelope contains every 5m extreme inside it, so "was this zone ever
    touched" is exact at 4H resolution.
  * The signal must land on the LATEST closed 5m bar, and it must be the FIRST
    bar since the alert that satisfies the model. That reproduces the backtest's
    "take the first confirmation" rule instead of entering a stale signal.
  * With S146_REQUIRE_RUNNING_EXTREME_15M enabled, a 5m execution is accepted
    only from the running 15m price extreme confirmed since its selected 4H
    destination activated. Shorts require the highest still-actionable supply;
    longs require the lowest still-actionable demand. The campaign boundary is
    structural and therefore survives engine restarts. The old engine-start
    boundary remains available when this filter is disabled.
  * The take profit is a FIXED multiple of the structural risk (S146_TP_R,
    default 1.25R) instead of the 4H destination's proximal edge. The destination
    still gates the entry — it must sit at least S146_MIN_RR away — so signal
    selection matches the backtest while the exit is banked much earlier. Compare
    live results only against a backtest run with the same fixed target.
  * Destinations are additionally screened for quality, which the backtest does
    not do: a 4H POI is discarded when its origin candle is older than
    S146_MAX_DEST_AGE_DAYS, or when more than S146_MAX_DEST_BOS_LAG_BARS 4H bars
    separate that origin candle from the break of structure that confirmed it.
    Both guard against drawing price toward structure that no longer describes
    the current regime. Re-run the backtest with matching limits when comparing.

Every fetch returns closed candles only, so detection never sees a forming bar.
"""
from __future__ import annotations

import sys
from bisect import bisect_left
from pathlib import Path
from typing import Optional

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.core import Candle  # noqa: E402
from strategies.strategy_146_naked_4h_poi_draw import (  # noqa: E402
    H4_SECONDS,
    M5_SECONDS,
    M15_SECONDS,
    Params,
    SwingIndex,
    Zone,
    _build_zones,
    _dedupe_zones,
    _invalidated,
    _liquidity_levels,
    _recent_confirmed_swing,
    _swept_liquidity,
    _swings,
    _touches,
    _zone_distal_stop,
)

from config import CONFIG  # noqa: E402
from logging_setup import get_engine_logger  # noqa: E402
import s146_events  # noqa: E402

log = get_engine_logger()


def live_params() -> Params:
    """Backtest Params with the live-tunable values applied."""
    return Params(
        stop_buffer_bps=CONFIG.s146_stop_buffer_bps,
        min_target_reward_risk=CONFIG.s146_min_rr,
        max_target_reward_risk=CONFIG.s146_max_rr,
        max_wait_bars_5m=CONFIG.s146_max_wait_bars_5m,
        max_hold_bars_5m=CONFIG.s146_max_hold_bars_5m,
    )


def to_candles(df) -> list[Candle]:
    """MT5 closed-bar DataFrame -> backtest Candle list."""
    candles: list[Candle] = []
    for row in df.itertuples(index=True):
        stamp = row.Index
        candles.append(Candle(
            time_utc=stamp.isoformat(),
            timestamp=int(stamp.timestamp()),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=int(getattr(row, "volume", 0) or 0),
        ))
    return candles


# Zone construction is the expensive part of a cycle: it walks every candle and
# every swing. 4H structure only changes once every 4 hours and 15m structure
# once every 15 minutes, yet the engine scans every 5 minutes, so most rebuilds
# reproduce a result that is already known. Cache per symbol+timeframe and
# invalidate on the latest closed bar, which is the only thing that can change
# the output. This is what keeps the 5m scan inside its own candle.
_zone_cache: dict[tuple, tuple[int, int, list[Zone]]] = {}
_CACHE_LIMIT = 512


def cached_zones(
    symbol: str, timeframe: str, candles: list[Candle],
    seconds: int, left: int, right: int,
) -> list[Zone]:
    """Deduped zones for `candles`, rebuilt only when the last closed bar moves."""
    if not candles:
        return []
    key = (symbol, timeframe, left, right)
    stamp = candles[-1].timestamp
    hit = _zone_cache.get(key)
    if hit is not None and hit[0] == stamp and hit[1] == len(candles):
        return hit[2]
    zones = _dedupe_zones(_build_zones(candles, timeframe, seconds, left, right))
    if len(_zone_cache) >= _CACHE_LIMIT:
        _zone_cache.clear()
    _zone_cache[key] = (stamp, len(candles), zones)
    return zones


def unmitigated_zones(zones: list[Zone], candles: list[Candle]) -> list[Zone]:
    """Zones never reached by any closed bar after they became active.

    The test is the backtest's `_touches` exactly: a bar must overlap the zone on
    BOTH sides. Suffix extremes are deliberately not used here, because a bar that
    gaps entirely past a zone satisfies one side of the test without ever touching
    it, which would wrongly retire a zone that is still unmitigated.
    """
    if not candles or not zones:
        return []
    lows = np.fromiter((c.low for c in candles), dtype=np.float64, count=len(candles))
    highs = np.fromiter((c.high for c in candles), dtype=np.float64, count=len(candles))
    starts = [c.timestamp for c in candles]

    fresh: list[Zone] = []
    for zone in zones:
        index = bisect_left(starts, zone.active_time)
        if index >= len(candles):
            # No closed bar exists after activation yet, so nothing can have
            # mitigated it.
            fresh.append(zone)
            continue
        touched = np.any(
            (lows[index:] <= zone.upper) & (highs[index:] >= zone.lower)
        )
        if not bool(touched):
            fresh.append(zone)
    return fresh


def screen_destinations(
    zones: list[Zone],
    reference_time: int,
    max_age_days: float,
    max_bos_lag_bars: float,
) -> tuple[list[Zone], int, int]:
    """Drop 4H destinations that are stale or whose BOS is far from their origin.

    Returns (kept, dropped_for_age, dropped_for_lag). A limit of 0 or less
    disables that check, which restores the unfiltered backtest behaviour.

    Age is measured from the zone's ORIGIN candle rather than its confirmation,
    because the origin candle is the structure a trader would actually mark on
    the chart, and it is the stricter of the two.
    """
    kept: list[Zone] = []
    dropped_age = 0
    dropped_lag = 0
    for zone in zones:
        if max_bos_lag_bars > 0:
            lag_bars = (zone.active_time - zone.origin_time) / H4_SECONDS
            if lag_bars > max_bos_lag_bars:
                dropped_lag += 1
                continue
        if max_age_days > 0:
            age_days = (reference_time - zone.origin_time) / 86400.0
            if age_days > max_age_days:
                dropped_age += 1
                continue
        kept.append(zone)
    return kept, dropped_age, dropped_lag


def _nearest_destination(
    destinations: list[Zone], entry_zone: Zone
) -> Optional[Zone]:
    """Nearest unmitigated 4H zone beyond the entry zone, confirmed before it."""
    wanted = -entry_zone.direction
    best: Optional[Zone] = None
    best_distance = float("inf")
    for zone in destinations:
        if zone.direction != wanted or zone.active_time >= entry_zone.active_time:
            continue
        if wanted > 0:
            # 4H demand below a 15m supply -> short toward it.
            if zone.proximal >= entry_zone.lower:
                continue
            distance = entry_zone.lower - zone.proximal
        else:
            # 4H supply above a 15m demand -> long toward it.
            if zone.proximal <= entry_zone.upper:
                continue
            distance = zone.proximal - entry_zone.upper
        if distance < best_distance:
            best, best_distance = zone, distance
    return best


def _aggressive_fires(
    bar: Candle, zone: Zone, direction: int, swings5: SwingIndex, params: Params
) -> Optional[float]:
    """Liquidity sweep then rejection close, matching the backtest predicate."""
    if _invalidated(bar, zone):
        return None
    swept = _swept_liquidity(bar, direction, swings5, params)
    if swept is None:
        return None
    directional_close = bar.close > bar.open if direction > 0 else bar.close < bar.open
    rejected = bar.close > zone.lower if direction > 0 else bar.close < zone.upper
    if not directional_close or not rejected:
        return None
    return swept.price


def _conservative_fires(
    bar: Candle, zone: Zone, direction: int, reference_price: float
) -> bool:
    if _invalidated(bar, zone):
        return False
    return bar.close > reference_price if direction > 0 else bar.close < reference_price


def _liquidity_stop(
    zone: Zone,
    direction: int,
    swings5: SwingIndex,
    params: Params,
    signal_bar: Candle,
    swept_level: Optional[float],
    trigger: float,
    available_time: int,
) -> tuple[float, str, Optional[dict]]:
    """Hide the stop behind the nearest liquidity pool outside the entry zone.

    This reproduces how the levels were being marked by hand. Two things move the
    stop past the plain zone-distal edge:

      * the wick that triggered the entry. A liquidation entry has just run the
        stops beyond the zone, so a stop at the zone edge sits INSIDE the move
        that caused the trade and gets picked off on the retest.
      * the closest pool of resting orders beyond that. Equal highs/lows are
        preferred over a single swing because that is where stops actually cluster
        (`_liquidity_levels` flags them via `equal`).

    The zone's distal edge remains the floor: a pool inside the zone would put the
    stop inside the structure the trade is built on, so only levels at or beyond
    the protected edge are considered. A pool further away than
    S146_STOP_LIQ_MAX_MULT times the plain zone-distal stop is rejected as "too
    far", which is what keeps the widening bounded.

    Returns (stop_price, basis, pool_details_or_None).
    """
    long_side = direction > 0

    def with_buffer(price: float) -> float:
        pad = abs(price) * params.stop_buffer_bps / 10000.0
        return price - pad if long_side else price + pad

    # The edge the stop must sit beyond, before looking for pools.
    protect = zone.distal
    wick_extended = False
    candidates = [signal_bar.low] if long_side else [signal_bar.high]
    if swept_level is not None:
        candidates.append(float(swept_level))
    for level in candidates:
        if (long_side and level < protect) or (not long_side and level > protect):
            protect = level
            wick_extended = True

    base = with_buffer(protect)
    base_basis = "swept_wick_plus_buffer" if wick_extended else "15m_zone_distal_plus_buffer"
    if not CONFIG.s146_stop_use_liquidity:
        return base, base_basis, None

    lookback = max(1, CONFIG.s146_stop_liq_lookback_5m)
    window_start = max(0, available_time - lookback * M5_SECONDS)
    levels = _liquidity_levels(
        swings5.window(window_start, available_time),
        available_time,
        params.equal_level_tolerance_bps,
        minimum_time=window_start,
    )
    # A long hides behind sell-side liquidity (lows); a short behind buy-side.
    wanted_side = -1 if long_side else 1
    pools = [
        level for level in levels
        if level.direction == wanted_side
        and ((level.price <= protect) if long_side else (level.price >= protect))
    ]
    if not pools:
        return base, base_basis, None

    # Nearest to the protected edge first.
    pools.sort(key=lambda level: level.price, reverse=long_side)
    limit = CONFIG.s146_stop_liq_max_mult * abs(trigger - base)
    equal_pools = [level for level in pools if level.equal]
    for group in (equal_pools, pools):
        for pool in group:
            candidate = with_buffer(pool.price)
            if abs(trigger - candidate) <= limit:
                return candidate, "liquidity_pool_plus_buffer", {
                    "price": round(float(pool.price), 8),
                    "equal_level": bool(pool.equal),
                    "formation_time": int(pool.formation_time),
                    "confirmation_time": int(pool.confirmation_time),
                    "protected_edge": round(float(protect), 8),
                    "zone_distal_stop": round(float(_zone_distal_stop(zone, params)), 8),
                }
    return base, base_basis, None


def _first_touch_index(zone: Zone, m5: list[Candle], start: int, limit: int) -> int:
    for index in range(start, min(limit, len(m5))):
        if _touches(m5[index], zone):
            return index
    return -1


def _zone_still_actionable(
    zone: Zone,
    m5: list[Candle],
    starts5: list[int],
    last_index: int,
    swings5: SwingIndex,
    params: Params,
) -> bool:
    """Whether a confirmed 15m zone can still produce its first 5m execution.

    Untouched zones remain candidates until max_wait expires. Once touched, a
    close beyond the distal edge or any earlier aggressive/conservative
    confirmation consumes the setup. The latest bar is deliberately excluded
    from the consumed-confirmation scan because it is the bar being considered
    now; it may make the running extreme actionable on this cycle.
    """
    start = bisect_left(starts5, zone.active_time)
    if start > last_index:
        return False
    limit = start + params.max_wait_bars_5m
    alert_index = _first_touch_index(zone, m5, start, limit)
    if alert_index < 0:
        return last_index < limit

    alert_time = m5[alert_index].timestamp + M5_SECONDS
    reference = _recent_confirmed_swing(
        swings5, 1 if zone.direction > 0 else -1, alert_time,
        max(0, alert_time - params.max_liquidity_lookback_5m * M5_SECONDS),
    )
    for index in range(alert_index, last_index):
        bar = m5[index]
        if _invalidated(bar, zone):
            return False
        if _aggressive_fires(bar, zone, zone.direction, swings5, params) is not None:
            return False
        if (reference is not None and index > alert_index
                and _conservative_fires(bar, zone, zone.direction, reference.price)):
            return False

    # A competing zone invalidated by the latest close must not block a valid
    # signal elsewhere. For the signaling zone this is also safe: both live 5m
    # predicates reject an invalidated latest bar.
    return not _invalidated(m5[last_index], zone)


def _running_extreme(
    zone: Zone,
    destination: Zone,
    zones15: list[Zone],
    destinations: list[Zone],
    m5: list[Candle],
    starts5: list[int],
    last_index: int,
    swings5: SwingIndex,
    params: Params,
) -> tuple[bool, Zone, int]:
    """Return whether `zone` is the actionable price extreme for its 4H draw.

    The campaign begins when the selected 4H destination confirms, not when the
    Python process starts. Only same-direction 15m zones confirmed after that
    point, linked to that same nearest destination, and still capable of their
    first 5m execution participate. Shorts prefer the highest proximal entry;
    longs prefer the lowest. No future zone can influence this ranking.
    """
    actionable: list[Zone] = []
    for candidate in zones15:
        if candidate.direction != zone.direction:
            continue
        if candidate.active_time <= destination.active_time:
            continue
        candidate_destination = _nearest_destination(destinations, candidate)
        if candidate_destination is None or candidate_destination.zone_id != destination.zone_id:
            continue
        if not _zone_still_actionable(
            candidate, m5, starts5, last_index, swings5, params
        ):
            continue
        actionable.append(candidate)

    # The caller has already proved that `zone` fires now. Retain it defensively
    # if a feed-boundary edge case made the generic state check omit it.
    if not any(candidate.zone_id == zone.zone_id for candidate in actionable):
        actionable.append(zone)

    if zone.direction > 0:
        extreme = min(actionable, key=lambda item: (item.proximal, -item.active_time))
        is_extreme = zone.proximal <= extreme.proximal
    else:
        extreme = max(actionable, key=lambda item: (item.proximal, item.active_time))
        is_extreme = zone.proximal >= extreme.proximal
    # Equal-price nested zones are both at the running extreme; do not reject one
    # merely because a tie-break selected a different zone id.
    return is_extreme, (zone if is_extreme else extreme), len(actionable)


def detect_signal(
    client,
    symbol: str,
    engine_start_ts: int,
    journal: bool = True,
) -> Optional[dict]:
    """Return an actionable S146 signal for `symbol`, or None.

    The returned dict carries the entry trigger, the structural stop, the 4H
    destination target, and the linked 4H/15M/5M event trail for logging and
    email.  The event timeline is copied into every downstream trade record.
    """
    params = live_params()

    df4 = client.fetch_closed(symbol, "4h", CONFIG.s146_fetch_4h)
    df15 = client.fetch_closed(symbol, "15m", CONFIG.s146_fetch_15m)
    df5 = client.fetch_closed(symbol, "5m", CONFIG.s146_fetch_5m)
    if df4 is None or df15 is None or df5 is None:
        log.warning(f"{symbol}: missing closed candles (4h/15m/5m) — skipping cycle")
        return None
    if len(df4) < 40 or len(df15) < 120 or len(df5) < 200:
        log.warning(f"{symbol}: not enough closed history yet "
                    f"(4h={len(df4)} 15m={len(df15)} 5m={len(df5)})")
        return None

    h4 = to_candles(df4)
    m15 = to_candles(df15)
    m5 = to_candles(df5)

    zones4 = cached_zones(symbol, "4h", h4, H4_SECONDS,
                          params.h4_swing_left, params.h4_swing_right)
    zones15 = cached_zones(symbol, "15m", m15, M15_SECONDS,
                           params.m15_swing_left, params.m15_swing_right)
    swings5 = SwingIndex(_swings(m5, params.m5_swing_left, params.m5_swing_right, M5_SECONDS))
    starts5 = [c.timestamp for c in m5]
    last_index = len(m5) - 1
    last_bar = m5[last_index]

    # "Now" is the close of the latest closed 5m bar, so the age test is
    # reproducible from the logs instead of depending on wall-clock drift.
    reference_time = last_bar.timestamp + M5_SECONDS
    destinations, dropped_age, dropped_lag = screen_destinations(
        unmitigated_zones(zones4, h4),
        reference_time,
        CONFIG.s146_max_dest_age_days,
        CONFIG.s146_max_dest_bos_lag_bars,
    )
    if journal and (dropped_age or dropped_lag):
        s146_events.log_4h("destinations_screened", {
            "symbol": symbol,
            "kept": len(destinations),
            "dropped_stale": dropped_age,
            "dropped_bos_lag": dropped_lag,
            "max_age_days": CONFIG.s146_max_dest_age_days,
            "max_bos_lag_bars_4h": CONFIG.s146_max_dest_bos_lag_bars,
            "reference_time": reference_time,
        }, key=f"{symbol}:screened:{len(destinations)}:{dropped_age}:{dropped_lag}")

    if journal:
        for zone in destinations:
            s146_events.log_4h("destination_available", {
                "symbol": symbol,
                "zone_id": zone.zone_id,
                "direction": "demand" if zone.direction > 0 else "supply",
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "proximal": round(zone.proximal, 8),
                "distal": round(zone.distal, 8),
                "origin_time": zone.origin_time,
                "confirmed_at": zone.active_time,
                "state": "unmitigated",
            }, key=f"{symbol}:{zone.zone_id}")

    # Running-extreme mode reconstructs the campaign from the selected 4H
    # destination's confirmation, so an engine restart cannot forget a valid
    # older 15m extreme. The legacy engine-start boundary remains available as
    # an immediate rollback path when the filter is disabled.
    if CONFIG.s146_require_running_extreme_15m:
        candidates = list(zones15)
    else:
        candidates = [z for z in zones15 if z.active_time >= engine_start_ts] \
            if CONFIG.s146_require_new_15m else list(zones15)
    if journal:
        for zone in candidates:
            s146_events.log_15m("entry_zone_formed", {
                "symbol": symbol,
                "zone_id": zone.zone_id,
                "direction": "demand" if zone.direction > 0 else "supply",
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "proximal": round(zone.proximal, 8),
                "distal": round(zone.distal, 8),
                "origin_time": zone.origin_time,
                "confirmed_at": zone.active_time,
                "bos_level": round(zone.broken_level, 8),
            }, key=f"{symbol}:{zone.zone_id}")

    # Newest zones first: the freshest structure is the most relevant.
    for zone in sorted(candidates, key=lambda z: z.active_time, reverse=True):
        direction = zone.direction
        start = bisect_left(starts5, zone.active_time)
        if start > last_index:
            continue
        limit = start + params.max_wait_bars_5m
        alert_index = _first_touch_index(zone, m5, start, limit)
        if alert_index < 0 or alert_index > last_index:
            continue
        alert_time = m5[alert_index].timestamp + M5_SECONDS
        if journal:
            s146_events.log_5m("zone_alert", {
                "symbol": symbol,
                "zone_timeframe": "15m",
                "zone_id": zone.zone_id,
                "direction": "long" if direction > 0 else "short",
                "level": round(zone.proximal, 8),
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "alert_bar_close": alert_time,
            }, key=f"{symbol}:{zone.zone_id}:alert")

        destination = _nearest_destination(destinations, zone)
        if destination is None:
            continue

        reference = _recent_confirmed_swing(
            swings5, 1 if direction > 0 else -1, alert_time,
            max(0, alert_time - params.max_liquidity_lookback_5m * M5_SECONDS),
        )

        # Reproduce "first confirmation since the alert wins": if any earlier bar
        # already fired, this signal is stale and must not be taken now.
        stale = False
        for index in range(alert_index, last_index):
            bar = m5[index]
            if _invalidated(bar, zone):
                stale = True
                break
            if _aggressive_fires(bar, zone, direction, swings5, params) is not None:
                stale = True
                break
            if (reference is not None and index > alert_index
                    and _conservative_fires(bar, zone, direction, reference.price)):
                stale = True
                break
        if stale:
            continue

        model = None
        confirmation_level = None
        swept_level = None
        swept = _aggressive_fires(last_bar, zone, direction, swings5, params)
        if swept is not None:
            model = "aggressive_liquidation"
            confirmation_level = swept
            swept_level = swept
        elif (reference is not None and last_index > alert_index
                and _conservative_fires(last_bar, zone, direction, reference.price)):
            model = "conservative_mss"
            confirmation_level = reference.price
        if model is None:
            continue

        campaign_zone_count = 1
        running_extreme = zone
        if CONFIG.s146_require_running_extreme_15m:
            is_extreme, running_extreme, campaign_zone_count = _running_extreme(
                zone, destination, zones15, destinations, m5, starts5,
                last_index, swings5, params,
            )
            if not is_extreme:
                if journal:
                    log.info(
                        f"S146 {symbol}: skipped non-extreme 15m zone {zone.zone_id} "
                        f"@ {zone.proximal:.5f}; running extreme {running_extreme.zone_id} "
                        f"@ {running_extreme.proximal:.5f} across "
                        f"{campaign_zone_count} actionable campaign zone(s)"
                    )
                    s146_events.log_15m("signal_rejected_non_extreme", {
                        "symbol": symbol,
                        "direction": "long" if direction > 0 else "short",
                        "candidate_zone_id": zone.zone_id,
                        "candidate_proximal": round(zone.proximal, 8),
                        "candidate_lower": round(zone.lower, 8),
                        "candidate_upper": round(zone.upper, 8),
                        "candidate_confirmed_at": zone.active_time,
                        "running_extreme_zone_id": running_extreme.zone_id,
                        "running_extreme_proximal": round(running_extreme.proximal, 8),
                        "running_extreme_lower": round(running_extreme.lower, 8),
                        "running_extreme_upper": round(running_extreme.upper, 8),
                        "running_extreme_confirmed_at": running_extreme.active_time,
                        "campaign_zone_count": campaign_zone_count,
                        "destination_zone_id": destination.zone_id,
                        "campaign_started_at": destination.active_time,
                        "signal_bar_close": last_bar.timestamp + M5_SECONDS,
                        "reason": "candidate_is_not_running_price_extreme",
                    }, key=(f"{symbol}:{zone.zone_id}:non_extreme:"
                            f"{running_extreme.zone_id}:{last_bar.timestamp}"))
                continue

        buffer = last_bar.close * params.stop_buffer_bps / 10000.0
        trigger = last_bar.high + buffer if direction > 0 else last_bar.low - buffer
        # Stop hides behind the nearest liquidity beyond the entry zone, with the
        # zone's distal edge as both fallback and floor.
        stop, stop_basis, stop_pool = _liquidity_stop(
            zone, direction, swings5, params, last_bar, swept_level, trigger,
            last_bar.timestamp + M5_SECONDS,
        )
        destination_target = destination.proximal
        risk = direction * (trigger - stop)
        reward = direction * (destination_target - trigger)
        if risk <= 0 or reward <= 0:
            continue
        destination_reward_risk = reward / risk
        # The 4H destination is still the qualifier: it must sit far enough away
        # for the trade to be worth taking. Entry selection is therefore identical
        # to the backtest even though the exit below is closer.
        if not (params.min_target_reward_risk
                <= destination_reward_risk
                <= params.max_target_reward_risk):
            if journal:
                s146_events.log_5m("signal_rejected_reward_risk", {
                    "symbol": symbol,
                    "zone_id": zone.zone_id,
                    "model": model,
                    "reward_risk": round(destination_reward_risk, 2),
                    "min_required": params.min_target_reward_risk,
                    "max_allowed": params.max_target_reward_risk,
                }, key=f"{symbol}:{zone.zone_id}:rr:{last_bar.timestamp}")
            continue

        # Actual take profit: a fixed multiple of the structural risk, banked well
        # before the 4H draw completes. Re-anchored to the real fill after entry.
        tp_r = CONFIG.s146_tp_r
        target = trigger + direction * tp_r * risk
        reward_risk = round(tp_r, 2)

        direction_name = "long" if direction > 0 else "short"
        event_timeline = [
            {
                "stream": "4h",
                "event": "destination_available",
                "timestamp": destination.active_time,
                "symbol": symbol,
                "zone_id": destination.zone_id,
                "lower": round(destination.lower, 8),
                "upper": round(destination.upper, 8),
                "origin_time": destination.origin_time,
                "confirmed_at": destination.active_time,
            },
            {
                "stream": "15m",
                "event": "entry_zone_formed",
                "timestamp": zone.active_time,
                "symbol": symbol,
                "zone_id": zone.zone_id,
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "origin_time": zone.origin_time,
                "confirmed_at": zone.active_time,
                "bos_level": round(zone.broken_level, 8),
            },
            {
                "stream": "5m",
                "event": "zone_alert",
                "timestamp": alert_time,
                "symbol": symbol,
                "zone_id": zone.zone_id,
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "alert_bar_close": alert_time,
            },
            {
                "stream": "5m",
                "event": model,
                "timestamp": last_bar.timestamp + M5_SECONDS,
                "symbol": symbol,
                "zone_id": zone.zone_id,
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "bar_open_time": last_bar.timestamp,
                "bar_close_time": last_bar.timestamp + M5_SECONDS,
                "trigger": round(trigger, 8),
                "stop": round(stop, 8),
                "target": round(target, 8),
                "target_r_multiple": reward_risk,
                "destination_target": round(destination_target, 8),
                "destination_reward_risk": round(destination_reward_risk, 2),
                "confirmation_level": round(float(confirmation_level), 8),
                "swept_level": round(float(swept_level), 8)
                if swept_level is not None else None,
            },
        ]
        signal = {
            "strategy": "s146",
            "symbol": symbol,
            "direction": direction_name,
            "model": model,
            "signal_bar_open": last_bar.timestamp,
            "signal_bar_close": last_bar.timestamp + M5_SECONDS,
            "trigger": float(trigger),
            "stop": float(stop),
            "target": float(target),
            "reward_risk": reward_risk,
            "target_r_multiple": tp_r,
            "destination_target": float(destination_target),
            "destination_reward_risk": round(destination_reward_risk, 2),
            "confirmation_level": float(confirmation_level),
            "swept_level": float(swept_level) if swept_level is not None else None,
            "event_timeline": event_timeline,
            "entry_zone_id": zone.zone_id,
            "entry_zone_lower": round(zone.lower, 8),
            "entry_zone_upper": round(zone.upper, 8),
            "entry_zone_distal": round(zone.distal, 8),
            "entry_zone_confirmed_at": zone.active_time,
            "destination_zone_id": destination.zone_id,
            "destination_type": "demand" if destination.direction > 0 else "supply",
            "destination_lower": round(destination.lower, 8),
            "destination_upper": round(destination.upper, 8),
            "destination_origin_time": destination.origin_time,
            "destination_confirmed_at": destination.active_time,
            "stop_basis": stop_basis,
            "stop_liquidity_pool": stop_pool,
            "target_basis": f"{tp_r}R_from_entry",
            "destination_target_basis": "unmitigated_4h_zone_proximal_edge",
            "alert_bar_close": alert_time,
            "running_extreme_filter_enabled": CONFIG.s146_require_running_extreme_15m,
            "entry_zone_is_running_extreme": running_extreme.zone_id == zone.zone_id,
            "running_extreme_zone_id": running_extreme.zone_id,
            "running_extreme_proximal": round(running_extreme.proximal, 8),
            "campaign_zone_count": campaign_zone_count,
            "campaign_started_at": destination.active_time,
        }
        if journal:
            s146_events.log_4h("destination_selected_as_target", {
                "symbol": symbol,
                "zone_id": destination.zone_id,
                "direction": signal["destination_type"],
                "target_price": round(destination_target, 8),
                "destination_reward_risk": round(destination_reward_risk, 2),
                "for_entry_zone": zone.zone_id,
            }, key=f"{symbol}:{destination.zone_id}:target:{zone.zone_id}")
            s146_events.log_5m(model, {
                "symbol": symbol,
                "direction": direction_name,
                "trigger": round(trigger, 8),
                "stop": round(stop, 8),
                "target": round(target, 8),
                "reward_risk": signal["reward_risk"],
                "destination_target": round(destination_target, 8),
                "destination_reward_risk": round(destination_reward_risk, 2),
                "confirmation_level": round(float(confirmation_level), 8),
                "swept_level": round(float(swept_level), 8) if swept_level is not None else None,
                "bar_open_time": last_bar.timestamp,
                "entry_zone_id": zone.zone_id,
                "destination_zone_id": destination.zone_id,
            }, key=f"{symbol}:{zone.zone_id}:signal:{last_bar.timestamp}")
        return signal

    return None
