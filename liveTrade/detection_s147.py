"""
Live detection for Strategy 147 — trade the reaction AT a 4H point of interest.

Parity with the backtest is kept by importing the backtest module itself and
reusing its swing detection, zone construction, liquidity model and entry
predicates. Nothing is re-implemented here except the parts that must differ
live:

  * The 5m confirmation must land on the LATEST closed 5m bar. That reproduces
    the backtest's "first confirmation after the alert wins" rule instead of
    entering a signal that already went stale several bars ago.
  * Arrival at the 4H POI, the 15m change of character and the 15m refinement
    zone are all resolved from closed bars only, newest structure first.
  * There is no age limit on the 4H POI, by design. An old untested zone is as
    valid as a fresh one, which is the opposite of the S146 policy.

Every fetch returns closed candles only, so detection never sees a forming bar.
Journalled events carry epoch plus readable UTC and IST for every formation
time, so a signal can be checked on a chart without converting timestamps.
"""
from __future__ import annotations

import sys
from bisect import bisect_left
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.core import Candle, infer_pip_size, round_turn_cost_pips  # noqa: E402
from strategies.strategy_147_4h_poi_reaction import (  # noqa: E402
    H4_SECONDS,
    M5_SECONDS,
    M15_SECONDS,
    Params,
    SwingIndex,
    Zone,
    _aggressive_fires,
    _build_zones,
    _closed_through,
    _conservative_fires,
    _dedupe_zones,
    _first_zone_touch_5m,
    _swings,
    _touches,
    _zone_distal_stop,
)

from config import CONFIG  # noqa: E402
from logging_setup import get_engine_logger  # noqa: E402
import s147_events  # noqa: E402

log = get_engine_logger()


def live_params() -> Params:
    """Backtest Params with the live-tunable values applied."""
    return Params(
        stop_buffer_bps=CONFIG.s147_stop_buffer_bps,
        reward_risk=CONFIG.s147_reward_risk,
        max_wait_bars_15m_choch=CONFIG.s147_max_wait_bars_15m_choch,
        max_wait_bars_15m_zone=CONFIG.s147_max_wait_bars_15m_zone,
        max_wait_bars_5m=CONFIG.s147_max_wait_bars_5m,
        min_stop_bps=CONFIG.s147_min_stop_bps,
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


# Zone construction walks every candle and every swing, but 4H structure only
# changes once every four hours and 15m once every fifteen minutes, while the
# engine scans every five. Cache per symbol+timeframe, invalidated by the latest
# closed bar, which is the only thing that can change the result.
_zone_cache: dict[tuple, tuple[int, int, list[Zone]]] = {}
_CACHE_LIMIT = 512


def cached_zones(
    symbol: str, timeframe: str, candles: list[Candle],
    seconds: int, left: int, right: int,
) -> list[Zone]:
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


def live_poi_state(zone: Zone, m15: list[Candle]) -> tuple[bool, Optional[int], int]:
    """Current standing of one 4H POI against closed 15m bars.

    Returns (invalidated, arrival_time_of_the_latest_visit, visit_count).

    A visit ends when price leaves the zone, so a long stay inside counts once
    and a later return is a new opportunity. The POI dies permanently the first
    time a 15m bar CLOSES fully beyond its distal edge.
    """
    starts = [candle.timestamp for candle in m15]
    index = bisect_left(starts, zone.active_time)
    inside = False
    visits = 0
    latest_arrival: Optional[int] = None
    while index < len(m15):
        candle = m15[index]
        if _closed_through(candle, zone):
            return True, latest_arrival, visits
        if _touches(candle, zone):
            if not inside:
                visits += 1
                latest_arrival = candle.timestamp + M15_SECONDS
            inside = True
        else:
            inside = False
        index += 1
    return False, latest_arrival, visits


def find_choch(
    zone: Zone, arrival_time: int, m15: list[Candle],
    swings15: SwingIndex, params: Params,
) -> Optional[tuple[int, float]]:
    """First 15m close beyond the opposing swing after arrival. (time, level)."""
    starts = [candle.timestamp for candle in m15]
    start = bisect_left(starts, arrival_time - M15_SECONDS)
    limit = min(len(m15), start + 1 + params.max_wait_bars_15m_choch)
    direction = zone.direction
    for index in range(start, limit):
        candle = m15[index]
        decision_time = candle.timestamp + M15_SECONDS
        if decision_time < arrival_time:
            continue
        if _closed_through(candle, zone):
            return None
        reference = swings15.latest_before(1 if direction > 0 else -1, decision_time)
        if reference is None or reference.index >= index:
            continue
        broke = (candle.close > reference.price if direction > 0
                 else candle.close < reference.price)
        if broke:
            return decision_time, reference.price
    return None


def find_refinement_zone(
    reaction_direction: int, choch_time: int, zones15: list[Zone], params: Params
) -> Optional[Zone]:
    """First aligned 15m zone confirmed at or after the change of character."""
    horizon = choch_time + params.max_wait_bars_15m_zone * M15_SECONDS
    for zone in zones15:
        if zone.active_time < choch_time:
            continue
        if zone.active_time > horizon:
            return None
        if zone.direction == reaction_direction:
            return zone
    return None


def detect_signal(
    client,
    symbol: str,
    engine_start_ts: int,
    journal: bool = True,
) -> Optional[dict]:
    """Return an actionable S147 signal for `symbol`, or None.

    The returned dict carries the entry, the structural stop, the fixed-R target
    and the full 4H/15m/5m event trail with readable formation times, which the
    engine and trade manager copy into every downstream trade record.
    """
    params = live_params()

    df4 = client.fetch_closed(symbol, "4h", CONFIG.s147_fetch_4h)
    df15 = client.fetch_closed(symbol, "15m", CONFIG.s147_fetch_15m)
    df5 = client.fetch_closed(symbol, "5m", CONFIG.s147_fetch_5m)
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
    swings15 = SwingIndex(_swings(m15, params.m15_swing_left,
                                  params.m15_swing_right, M15_SECONDS))
    swings5 = SwingIndex(_swings(m5, params.m5_swing_left,
                                 params.m5_swing_right, M5_SECONDS))
    starts5 = [candle.timestamp for candle in m5]
    last_index = len(m5) - 1
    last_bar = m5[last_index]

    # ---- 4H: which points of interest has price actually arrived at? ----
    live_pois: list[tuple[Zone, int, int]] = []
    for zone in zones4:
        invalidated, arrival_time, visits = live_poi_state(zone, m15)
        if invalidated or arrival_time is None:
            continue
        live_pois.append((zone, arrival_time, visits))
        if journal:
            s147_events.log_4h("poi_available", {
                "symbol": symbol,
                "zone_id": zone.zone_id,
                "direction": "demand" if zone.direction > 0 else "supply",
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "proximal": round(zone.proximal, 8),
                "distal": round(zone.distal, 8),
                "bos_level": round(zone.broken_level, 8),
                **s147_events.stamp("origin_time", zone.origin_time),
                **s147_events.stamp("confirmed_at", zone.active_time),
            }, key=f"{symbol}:{zone.zone_id}")
            s147_events.log_4h("price_arrived_at_poi", {
                "symbol": symbol,
                "zone_id": zone.zone_id,
                "direction": "demand" if zone.direction > 0 else "supply",
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "visit": visits,
                "detected_on": "closed_15m_bar",
                **s147_events.stamp("arrival_time", arrival_time),
                **s147_events.stamp("origin_time", zone.origin_time),
                **s147_events.stamp("confirmed_at", zone.active_time),
            }, key=f"{symbol}:{zone.zone_id}:arrival:{visits}")

    if not live_pois:
        return None

    # Newest arrival first: the freshest reaction is the most relevant.
    live_pois.sort(key=lambda item: item[1], reverse=True)

    for poi, arrival_time, visits in live_pois:
        direction = poi.direction
        direction_name = "long" if direction > 0 else "short"

        # ---- 15m, step a: change of character in the reaction direction ----
        choch = find_choch(poi, arrival_time, m15, swings15, params)
        if choch is None:
            continue
        choch_time, choch_level = choch
        if journal:
            s147_events.log_15m("change_of_character", {
                "symbol": symbol,
                "poi_zone_id": poi.zone_id,
                "direction": "bullish" if direction > 0 else "bearish",
                "level": round(choch_level, 8),
                "visit": visits,
                "confirmation": "15m_close_beyond_confirmed_opposing_swing",
                **s147_events.stamp("choch_time", choch_time),
                **s147_events.stamp("arrival_time", arrival_time),
            }, key=f"{symbol}:{poi.zone_id}:choch:{choch_time}")

        # ---- 15m, step b: a fresh aligned zone, only AFTER the change ----
        zone = find_refinement_zone(direction, choch_time, zones15, params)
        if zone is None:
            continue
        if journal:
            s147_events.log_15m("refinement_zone_formed", {
                "symbol": symbol,
                "poi_zone_id": poi.zone_id,
                "zone_id": zone.zone_id,
                "direction": "demand" if zone.direction > 0 else "supply",
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                "proximal": round(zone.proximal, 8),
                "distal": round(zone.distal, 8),
                "bos_level": round(zone.broken_level, 8),
                **s147_events.stamp("origin_time", zone.origin_time),
                **s147_events.stamp("confirmed_at", zone.active_time),
                **s147_events.stamp("choch_time", choch_time),
            }, key=f"{symbol}:{zone.zone_id}:refine")

        # ---- 5m: return to the refinement zone, then confirm ----
        begin = bisect_left(starts5, zone.active_time)
        if begin >= len(m5):
            continue
        horizon = min(len(m5), begin + params.max_wait_bars_5m)
        alert_index = _first_zone_touch_5m(zone, m5, begin, horizon)
        if alert_index < 0 or alert_index > last_index:
            continue
        alert_time = m5[alert_index].timestamp + M5_SECONDS
        if journal:
            s147_events.log_5m("zone_alert", {
                "symbol": symbol,
                "zone_timeframe": "15m",
                "zone_id": zone.zone_id,
                "poi_zone_id": poi.zone_id,
                "direction": direction_name,
                "level": round(zone.proximal, 8),
                "lower": round(zone.lower, 8),
                "upper": round(zone.upper, 8),
                **s147_events.stamp("alert_bar_close", alert_time),
            }, key=f"{symbol}:{zone.zone_id}:alert")

        reference = swings5.latest_before(1 if direction > 0 else -1, alert_time)

        # The first confirmation since the alert wins. If an earlier bar already
        # fired, this signal is stale and must not be taken now.
        stale = False
        for index in range(alert_index, last_index):
            candle = m5[index]
            if _closed_through(candle, zone):
                stale = True
                break
            if _aggressive_fires(candle, zone, direction, swings5, params) is not None:
                stale = True
                break
            if (reference is not None and index > alert_index
                    and _conservative_fires(candle, zone, direction, reference.price)):
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

        # Market entry: the backtest fills at the open of the bar after the
        # signal bar, which live is "now", so the trigger is the current price.
        entry = last_bar.close
        stop = _zone_distal_stop(zone, params)
        risk = direction * (entry - stop)
        if risk <= 0:
            continue

        pip = infer_pip_size(entry, symbol=symbol)
        risk_pips = risk / pip
        if risk_pips < params.min_stop_bps * entry / (10000.0 * pip):
            if journal:
                s147_events.log_5m("signal_rejected_stop_too_tight", {
                    "symbol": symbol, "zone_id": zone.zone_id,
                    "risk_pips": round(risk_pips, 2),
                    "min_stop_bps": params.min_stop_bps,
                }, key=f"{symbol}:{zone.zone_id}:tight:{last_bar.timestamp}")
            continue

        target = entry + direction * risk * params.reward_risk
        target_pips = risk_pips * params.reward_risk
        cost_pips = round_turn_cost_pips(entry, symbol=symbol)
        # Costs are reported with the signal but do not reject the setup.

        event_timeline = [
            {"stream": "4h", "event": "poi_available", "symbol": symbol,
             "zone_id": poi.zone_id, "lower": round(poi.lower, 8),
             "upper": round(poi.upper, 8),
             **s147_events.stamp("origin_time", poi.origin_time),
             **s147_events.stamp("confirmed_at", poi.active_time)},
            {"stream": "4h", "event": "price_arrived_at_poi", "symbol": symbol,
             "zone_id": poi.zone_id, "lower": round(poi.lower, 8),
             "upper": round(poi.upper, 8), "visit": visits,
             **s147_events.stamp("arrival_time", arrival_time)},
            {"stream": "15m", "event": "change_of_character", "symbol": symbol,
             "poi_zone_id": poi.zone_id, "level": round(choch_level, 8),
             **s147_events.stamp("choch_time", choch_time)},
            {"stream": "15m", "event": "refinement_zone_formed", "symbol": symbol,
             "zone_id": zone.zone_id, "lower": round(zone.lower, 8),
             "upper": round(zone.upper, 8),
             **s147_events.stamp("origin_time", zone.origin_time),
             **s147_events.stamp("confirmed_at", zone.active_time)},
            {"stream": "5m", "event": "zone_alert", "symbol": symbol,
             "zone_id": zone.zone_id, "lower": round(zone.lower, 8),
             "upper": round(zone.upper, 8),
             **s147_events.stamp("alert_bar_close", alert_time)},
            {"stream": "5m", "event": model, "symbol": symbol,
             "zone_id": zone.zone_id, "lower": round(zone.lower, 8),
             "upper": round(zone.upper, 8),
             "confirmation_level": round(float(confirmation_level), 8),
             "swept_level": round(float(swept_level), 8) if swept_level is not None else None,
             **s147_events.stamp("bar_open_time", last_bar.timestamp),
             **s147_events.stamp("bar_close_time", last_bar.timestamp + M5_SECONDS)},
        ]

        signal = {
            "strategy": "s147",
            "symbol": symbol,
            "direction": direction_name,
            "model": model,
            **s147_events.stamp("signal_bar_open", last_bar.timestamp),
            **s147_events.stamp("signal_bar_close", last_bar.timestamp + M5_SECONDS),
            "trigger": float(entry),
            "stop": float(stop),
            "target": float(target),
            "reward_risk": params.reward_risk,
            "risk_pips": round(risk_pips, 2),
            "target_pips": round(target_pips, 2),
            "round_turn_cost_pips": round(cost_pips, 2),
            "confirmation_level": float(confirmation_level),
            "swept_level": float(swept_level) if swept_level is not None else None,
            "event_timeline": event_timeline,
            "poi_zone_id": poi.zone_id,
            "poi_type": "demand" if poi.direction > 0 else "supply",
            "poi_lower": round(poi.lower, 8),
            "poi_upper": round(poi.upper, 8),
            **s147_events.stamp("poi_origin_time", poi.origin_time),
            **s147_events.stamp("poi_confirmed_at", poi.active_time),
            **s147_events.stamp("poi_arrival_time", arrival_time),
            "poi_visit": visits,
            **s147_events.stamp("choch_time", choch_time),
            "choch_level": round(choch_level, 8),
            "entry_zone_id": zone.zone_id,
            "entry_zone_lower": round(zone.lower, 8),
            "entry_zone_upper": round(zone.upper, 8),
            "entry_zone_distal": round(zone.distal, 8),
            **s147_events.stamp("entry_zone_origin_time", zone.origin_time),
            **s147_events.stamp("entry_zone_confirmed_at", zone.active_time),
            **s147_events.stamp("alert_bar_close", alert_time),
            "stop_basis": "15m_refinement_zone_distal_plus_buffer",
            "target_basis": f"fixed_{params.reward_risk}R_from_entry",
        }
        if journal:
            s147_events.log_5m(model, {
                "symbol": symbol,
                "direction": direction_name,
                "poi_zone_id": poi.zone_id,
                "entry_zone_id": zone.zone_id,
                "trigger": round(entry, 8),
                "stop": round(stop, 8),
                "target": round(target, 8),
                "reward_risk": params.reward_risk,
                "risk_pips": round(risk_pips, 2),
                "confirmation_level": round(float(confirmation_level), 8),
                "swept_level": round(float(swept_level), 8) if swept_level is not None else None,
                **s147_events.stamp("bar_open_time", last_bar.timestamp),
                **s147_events.stamp("bar_close_time", last_bar.timestamp + M5_SECONDS),
            }, key=f"{symbol}:{zone.zone_id}:signal:{last_bar.timestamp}")
        return signal

    return None
