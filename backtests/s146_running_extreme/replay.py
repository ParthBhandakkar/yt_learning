#!/usr/bin/env python3
"""Efficient causal replay of live S146 qualification with broker-destination exits."""
from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

UTC = timezone.utc
EPS = 1e-10
REPO = Path(__file__).resolve().parents[2]
LIVE = REPO / "liveTrade"
for item in (str(REPO), str(LIVE)):
    if item not in sys.path:
        sys.path.insert(0, item)

from liveTrade import config as live_config  # noqa: E402
sys.modules["config"] = live_config
CONFIG = live_config.CONFIG
from liveTrade.detection_s146 import (  # noqa: E402
    _aggressive_fires, _conservative_fires, _first_touch_index, _liquidity_stop,
    _nearest_destination, live_params, screen_destinations, to_candles,
    unmitigated_zones,
)
from strategies.strategy_146_naked_4h_poi_draw import (  # noqa: E402
    Candle, H4_SECONDS, M15_SECONDS, M5_SECONDS, SwingIndex, Zone,
    _build_zones, _dedupe_zones, _invalidated, _recent_confirmed_swing,
    _swings, _touches,
)

try:
    from .schemas import config_snapshot as schema_config_snapshot, write_csv, write_json, write_jsonl
    from .simulate import simulate_trade
except ImportError:  # direct module invocation support
    from schemas import config_snapshot as schema_config_snapshot, write_csv, write_json, write_jsonl  # type: ignore
    from simulate import simulate_trade  # type: ignore


def iso(stamp: int | float | datetime | None) -> str | None:
    if stamp is None:
        return None
    dt = stamp if isinstance(stamp, datetime) else datetime.fromtimestamp(float(stamp), UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def stable_id(*parts: Any, size: int = 16) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:size]


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default

@dataclass(frozen=True)
class Bar:
    candle: Candle
    spread_points: float


@dataclass(frozen=True)
class ZoneState:
    start_index: int
    alert_index: int
    alert_time: Optional[int]
    reference_price: Optional[float]
    confirmation_index: Optional[int]
    confirmation_time: Optional[int]
    model: Optional[str]
    confirmation_level: Optional[float]
    swept_level: Optional[float]
    invalidation_index: Optional[int]
    expiry_index: int


def _parse_epoch(row: dict[str, str]) -> Optional[int]:
    raw = (row.get("time") or "").strip()
    try:
        value = int(float(raw))
        if value > 100_000_000_000:
            value //= 1000
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    raw = (row.get("time_utc") or row.get("datetime") or row.get("timestamp") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp())
    except ValueError:
        return None


def load_bars(path: Path, seconds: int) -> tuple[list[Bar], dict[str, int]]:
    """Load, validate, dedupe and sort a raw file without pandas."""
    rows: dict[int, Bar] = {}
    stats = Counter()
    if not path.is_file():
        return [], {"missing_file": 1, "accepted": 0}
    try:
        with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as handle:
            reader = csv.DictReader(handle)
            for source in reader:
                stats["read"] += 1
                stamp = _parse_epoch(source)
                if stamp is None:
                    stats["malformed_time"] += 1
                    continue
                values = [finite(source.get(name), math.nan) for name in ("open", "high", "low", "close")]
                if not all(math.isfinite(value) for value in values) or values[1] < values[2]:
                    stats["malformed_ohlc"] += 1
                    continue
                candle = Candle(iso(stamp) or "", stamp, values[0], values[1], values[2], values[3],
                                int(finite(source.get("tick_volume") or source.get("volume"), 0)))
                if stamp in rows:
                    stats["duplicates"] += 1
                rows[stamp] = Bar(candle, max(0.0, finite(source.get("spread"), 0.0)))
    except (OSError, csv.Error):
        stats["read_error"] += 1
    result = [rows[key] for key in sorted(rows)]
    stats["accepted"] = len(result)
    return result, dict(stats)


def _candles(bars: list[Bar]) -> list[Candle]:
    return [bar.candle for bar in bars]


def _first_h4_touch(zone: Zone, h4: list[Candle]) -> Optional[int]:
    start = bisect.bisect_left([bar.timestamp for bar in h4], zone.active_time)
    for bar in h4[start:]:
        if _touches(bar, zone):
            return bar.timestamp + H4_SECONDS
    return None


def _zone_state(zone: Zone, m5: list[Candle], starts: list[int], swings: SwingIndex,
                params: Any, until_epoch: Optional[int] = None) -> ZoneState:
    start = bisect.bisect_left(starts, zone.active_time)
    end_index = len(m5) if until_epoch is None else bisect.bisect_left(starts, until_epoch)
    expiry = min(end_index, start + params.max_wait_bars_5m)
    alert = _first_touch_index(zone, m5, start, expiry)
    if alert < 0:
        return ZoneState(start, -1, None, None, None, None, None, None, None, None, expiry)
    alert_time = m5[alert].timestamp + M5_SECONDS
    reference = _recent_confirmed_swing(
        swings, 1 if zone.direction > 0 else -1, alert_time,
        max(0, alert_time - params.max_liquidity_lookback_5m * M5_SECONDS),
    )
    for index in range(alert, end_index):
        bar = m5[index]
        if _invalidated(bar, zone):
            return ZoneState(start, alert, alert_time, getattr(reference, "price", None),
                             None, None, None, None, None, index, end_index)
        swept = _aggressive_fires(bar, zone, zone.direction, swings, params)
        if swept is not None:
            return ZoneState(start, alert, alert_time, getattr(reference, "price", None),
                             index, bar.timestamp + M5_SECONDS, "aggressive_liquidation",
                             float(swept), float(swept), None, end_index)
        if (reference is not None and index > alert
                and _conservative_fires(bar, zone, zone.direction, reference.price)):
            return ZoneState(start, alert, alert_time, reference.price,
                             index, bar.timestamp + M5_SECONDS, "conservative_mss",
                             reference.price, None, None, end_index)
    return ZoneState(start, alert, alert_time, getattr(reference, "price", None),
                     None, None, None, None, None, None, end_index)


def _actionable(state: ZoneState, index: int) -> bool:
    if state.start_index > index or index >= state.expiry_index:
        return False
    if state.invalidation_index is not None and state.invalidation_index <= index:
        return False
    return state.confirmation_index is None or state.confirmation_index >= index


def _destinations_at(zones4: list[Zone], first_touches: dict[str, Optional[int]],
                     h4: list[Candle], cutoff: int) -> tuple[list[Zone], int, int]:
    # The live primitive is evaluated on the closed H4 prefix only. The cached
    # first-touch map keeps this query bounded while this call documents and
    # enforces the exact native unmitigated-zone definition.
    h4_closed = [bar for bar in h4 if bar.timestamp + H4_SECONDS <= cutoff]
    fresh_ids = {zone.zone_id for zone in unmitigated_zones(zones4, h4_closed)} if h4_closed else set()
    known = [zone for zone in zones4 if zone.active_time <= cutoff
             and zone.zone_id in fresh_ids
             and (first_touches.get(zone.zone_id) is None or first_touches[zone.zone_id] > cutoff)]
    return screen_destinations(known, cutoff, CONFIG.s146_max_dest_age_days,
                               CONFIG.s146_max_dest_bos_lag_bars)


def _zone_payload(zone: Zone) -> dict[str, Any]:
    return {"zone_id": zone.zone_id, "direction": "demand" if zone.direction > 0 else "supply",
            "lower": zone.lower, "upper": zone.upper, "distal": zone.distal,
            "proximal": zone.proximal, "origin_time_utc": iso(zone.origin_time),
            "confirmed_at_utc": iso(zone.active_time), "bos_level": zone.broken_level}


def _event(event: str, stamp: int, symbol: str, **fields: Any) -> dict[str, Any]:
    return {"event": event, "event_type": event, "type": event, "time_utc": iso(stamp), "symbol": symbol, **fields}

def _signal_events(symbol: str, zone: Zone, destination: Zone, state: ZoneState,
                   signal_bar: Candle, signal_id: str, model: str,
                   trigger: float, stop: float, target: float) -> list[dict[str, Any]]:
    common = {"signal_id": signal_id, "entry_zone_id": zone.zone_id,
              "destination_zone_id": destination.zone_id}
    return [
        _event("destination_available", destination.active_time, symbol, **common,
               timeframe="4h", role="destination", **_zone_payload(destination)),
        _event("entry_zone_formed", zone.active_time, symbol, **common,
               timeframe="15m", role="entry", **_zone_payload(zone)),
        _event("zone_alert", int(state.alert_time or zone.active_time), symbol, **common,
               timeframe="5m", lower=zone.lower, upper=zone.upper),
        _event("confirmation", signal_bar.timestamp + M5_SECONDS, symbol, **common,
               timeframe="5m", model=model, confirmation_level=state.confirmation_level,
               swept_level=state.swept_level, bar_open_time_utc=iso(signal_bar.timestamp)),
        _event("signal", signal_bar.timestamp + M5_SECONDS, symbol, **common,
               direction="long" if zone.direction > 0 else "short", trigger=trigger,
               structural_stop=stop, broker_target=target),
    ]



def _public(value: Any) -> Any:
    """Strip private epoch/cache fields and make JSON-safe primitives."""
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if isinstance(value, float):
        return round(value, 10) if math.isfinite(value) else None
    if isinstance(value, datetime):
        return iso(value)
    return value


def _side_prices(bar: Bar, point: float) -> dict[str, float]:
    spread = bar.spread_points * point
    c = bar.candle
    return {"bid_open": c.open, "bid_high": c.high, "bid_low": c.low, "bid_close": c.close,
            "ask_open": c.open + spread, "ask_high": c.high + spread,
            "ask_low": c.low + spread, "ask_close": c.close + spread,
            "spread_price": spread}


def _simulate(signal: dict[str, Any], bars: list[Bar], starts: list[int], point: float) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """Fill at the next M5 open and replay spread-aware ladder exits."""
    direction = int(signal["_direction"])
    signal_time = int(signal["_signal_epoch"])
    fill_index = bisect.bisect_left(starts, signal_time)
    if fill_index >= len(bars):
        return None, {"reason": "no_next_m5_bar", "details": {}}
    fill_bar = bars[fill_index]
    fill_prices = _side_prices(fill_bar, point)
    entry = fill_prices["ask_open"] if direction > 0 else fill_prices["bid_open"]
    stop = float(signal["stop_price"])
    risk = direction * (entry - stop)
    if risk <= EPS:
        return None, {"reason": "invalid_fill_risk", "details": {"entry": entry, "stop": stop}}
    spread_ratio = fill_prices["spread_price"] / risk

    target = float(signal["destination_target"])
    target_r = direction * (target - entry) / risk
    events = list(signal["events"])
    events.append(_event("fill", fill_bar.candle.timestamp, signal["symbol"],
                         signal_id=signal["signal_id"], entry_price=entry,
                         structural_stop=stop, initial_risk=risk, broker_target=target,
                         fill_side="ask" if direction > 0 else "bid",
                         spread_points=fill_bar.spread_points,
                         spread_price=fill_prices["spread_price"],
                         assumption="market fill at next M5 open"))
    ladder = bool(CONFIG.s146_ladder_enabled)
    partial_at = float(CONFIG.s146_partial_at_r)
    fraction = min(1.0, max(0.0, float(CONFIG.s146_partial_fraction))) if ladder else 0.0
    step = max(EPS, float(CONFIG.s146_trail_step_r))
    giveback = float(CONFIG.s146_trail_giveback_r)
    open_fraction = 1.0
    realized = 0.0
    partial_done = False
    active_stop = stop
    active_stop_r = -1.0
    peak_r = 0.0
    mfe_r = 0.0
    mae_r = 0.0
    highest_rung = math.floor(round(partial_at / step, 6)) * step if ladder else 0.0
    exit_price: Optional[float] = None
    exit_epoch: Optional[int] = None
    exit_reason = "open"
    bars_held = 0
    last_prices = fill_prices

    def price_at(r_value: float) -> float:
        return entry + direction * risk * r_value

    max_hold = int(CONFIG.s146_max_hold_bars_5m)
    for index in range(fill_index, len(bars)):
        if max_hold > 0 and bars_held >= max_hold:
            break
        bar = bars[index]
        prices = _side_prices(bar, point)
        last_prices = prices
        bars_held += 1
        adverse = prices["bid_low"] if direction > 0 else prices["ask_high"]
        favorable = prices["bid_high"] if direction > 0 else prices["ask_low"]
        favorable_r = direction * (favorable - entry) / risk
        adverse_r = direction * (adverse - entry) / risk
        mfe_r = max(mfe_r, favorable_r)
        mae_r = min(mae_r, adverse_r)
        hit_stop = adverse <= active_stop + EPS if direction > 0 else adverse >= active_stop - EPS
        if hit_stop:
            realized += open_fraction * active_stop_r
            exit_price, exit_epoch = active_stop, bar.candle.timestamp + M5_SECONDS
            exit_reason = "stop_loss" if active_stop_r < -EPS else (
                "breakeven_stop" if abs(active_stop_r) <= EPS else "trailing_stop")
            events.append(_event("exit", exit_epoch, signal["symbol"], signal_id=signal["signal_id"],
                                 reason=exit_reason, exit_price=exit_price,
                                 open_fraction=open_fraction, realized_r=realized,
                                 same_bar_assumption="stop checked before favorable levels"))
            break

        favorable_r = direction * (favorable - entry) / risk
        peak_r = max(peak_r, favorable_r)
        if ladder and not partial_done and fraction > 0 and peak_r + EPS >= partial_at:
            banked = open_fraction * fraction * partial_at
            realized += banked
            open_fraction *= 1.0 - fraction
            partial_done = True
            active_stop_r = max(active_stop_r, 0.0)
            active_stop = price_at(active_stop_r)
            events.append(_event("partial", bar.candle.timestamp + M5_SECONDS, signal["symbol"],
                                 signal_id=signal["signal_id"], price=price_at(partial_at),
                                 r_level=partial_at, fraction=fraction, banked_r=banked,
                                 remaining_fraction=open_fraction, stop_moved_to="breakeven"))

        if ladder and partial_done:
            reached_rung = math.floor((peak_r + EPS) / step) * step
            next_rung = highest_rung + step
            while next_rung <= reached_rung + EPS:
                proposed_r = max(0.0, next_rung - giveback)
                if proposed_r > active_stop_r + EPS:
                    active_stop_r = proposed_r
                    active_stop = price_at(active_stop_r)
                    events.append(_event("ratchet", bar.candle.timestamp + M5_SECONDS,
                                         signal["symbol"], signal_id=signal["signal_id"],
                                         rung_r=next_rung, giveback_r=giveback,
                                         stop_r=active_stop_r, stop_price=active_stop,
                                         assumption="new stop becomes testable on the next M5 bar"))
                highest_rung = next_rung
                next_rung += step

        target_hit = favorable >= target - EPS if direction > 0 else favorable <= target + EPS
        if target_hit:
            realized += open_fraction * target_r
            exit_price, exit_epoch, exit_reason = target, bar.candle.timestamp + M5_SECONDS, "destination_target"
            events.append(_event("exit", exit_epoch, signal["symbol"], signal_id=signal["signal_id"],
                                 reason=exit_reason, exit_price=exit_price,
                                 open_fraction=open_fraction, realized_r=realized,
                                 broker_target_basis="4h_destination_proximal"))
            break

    if exit_epoch is None and max_hold > 0 and bars_held >= max_hold:
        last = bars[fill_index + bars_held - 1]
        exit_epoch = last.candle.timestamp + M5_SECONDS
        exit_price = last_prices["bid_close"] if direction > 0 else last_prices["ask_close"]
        final_r = direction * (exit_price - entry) / risk
        realized += open_fraction * final_r
        exit_reason = "time_expiry"
        events.append(_event("expiry", exit_epoch, signal["symbol"], signal_id=signal["signal_id"],
                             reason=exit_reason, exit_price=exit_price,
                             remaining_fraction=open_fraction, realized_r=realized))
    elif exit_epoch is None:
        mark = bars[-1] if bars else fill_bar
        mark_prices = _side_prices(mark, point)
        exit_price = mark_prices["bid_close"] if direction > 0 else mark_prices["ask_close"]
        mark_r = direction * (exit_price - entry) / risk
        events.append(_event("mark", mark.candle.timestamp + M5_SECONDS, signal["symbol"],
                             signal_id=signal["signal_id"], mark_price=exit_price,
                             unrealized_remaining_r=open_fraction * mark_r,
                             realized_partial_r=realized, status="open_at_data_end"))

    result_r = realized if exit_epoch is not None else realized
    outcome = "open" if exit_epoch is None else ("win" if result_r > EPS else "loss" if result_r < -EPS else "breakeven")
    trade = {
        "trade_id": stable_id("trade", signal["signal_id"]), "signal_id": signal["signal_id"],
        "strategy": "s146", "symbol": signal["symbol"], "broker_symbol": signal.get("broker_symbol"),
        "direction": signal["direction"], "side": signal["direction"], "model": signal["model"],
        "signal_time_utc": signal["signal_time_utc"], "entry_time_utc": iso(fill_bar.candle.timestamp),
        "exit_time_utc": iso(exit_epoch), "entry_price": entry, "stop_price": stop,
        "initial_risk": risk, "destination_target": target, "destination_reward_risk_at_fill": target_r,
        "exit_price": exit_price, "exit_reason": exit_reason, "status": outcome,
        "outcome": outcome, "r": result_r, "realized_r": result_r,
        "realized_partial_r": realized if exit_epoch is None else None,
        "peak_r": peak_r, "mfe_r": mfe_r, "mae_r": mae_r,
        "partial_banked": partial_done, "partial_fraction": fraction if partial_done else 0.0,
        "remaining_fraction": open_fraction, "final_stop_price": active_stop, "final_stop_r": active_stop_r,
        "bars_held_5m": bars_held, "spread_at_fill_points": fill_bar.spread_points,
        "spread_at_fill_price": fill_prices["spread_price"], "spread_pct_of_initial_risk": spread_ratio,
        "entry_zone_id": signal["entry_zone_id"], "entry_zone": signal["entry_zone"],
        "destination_zone_id": signal["destination_zone_id"], "destination_zone": signal["destination_zone"],
        "campaign_id": signal["campaign_id"],
        "campaign_started_at_utc": signal["campaign_started_at_utc"],
        "campaign_zone_count": signal["campaign_zone_count"], "stop_basis": signal["stop_basis"],
        "stop_liquidity_pool": signal["stop_liquidity_pool"], "events": events,
        "assumptions": ["M5 input is bid OHLC", "long fill uses ask; long exits use bid",
                        "short fill uses bid; short exits use ask", "spread is constant within each M5 bar",
                        "stop is checked first when one bar spans stop and favorable levels"],
        "_entry_epoch": fill_bar.candle.timestamp, "_exit_epoch": exit_epoch,
    }
    return trade, None

def _simulate(signal: dict[str, Any], bars: list[Bar], starts: list[int], point: float):
    """Compatibility wrapper; the canonical simulator lives in simulate.py."""
    return simulate_trade(signal, bars, starts, point, CONFIG)


def _raw_path(raw_root: Path, symbol: str, timeframe: str) -> Path:
    nested = raw_root / symbol / timeframe / f"{symbol}_{timeframe}.csv"
    legacy = raw_root / symbol / f"{timeframe}.csv"
    return nested if nested.is_file() else legacy


def replay_symbol(symbol: str, raw_root: Path, score_start: int, score_end: int,
                  metadata: dict[str, Any], progress: Callable[[str], None] = print
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Replay one symbol while retaining only signals and hypothetical paths."""
    h4_bars, h4_stats = load_bars(_raw_path(raw_root, symbol, "4h"), H4_SECONDS)
    m15_bars, m15_stats = load_bars(_raw_path(raw_root, symbol, "15m"), M15_SECONDS)
    m5_bars, m5_stats = load_bars(_raw_path(raw_root, symbol, "5m"), M5_SECONDS)
    h4, m15, m5 = _candles(h4_bars), _candles(m15_bars), _candles(m5_bars)
    stats: dict[str, Any] = {"symbol": symbol, "input": {"4h": h4_stats, "15m": m15_stats, "5m": m5_stats}}
    if len(h4) < 5 or len(m15) < 5 or len(m5) < 5:
        stats["error"] = "insufficient_or_missing_data"
        return [], [_event("symbol_skipped", score_start, symbol, reason=stats["error"])], stats

    params = live_params()
    progress(f"replay {symbol}: building H4/M15 zones and M5 swings once")
    zones4 = _dedupe_zones(_build_zones(h4, "4h", H4_SECONDS,
                                        params.h4_swing_left, params.h4_swing_right))
    zones15 = _dedupe_zones(_build_zones(m15, "15m", M15_SECONDS,
                                         params.m15_swing_left, params.m15_swing_right))
    swings5 = SwingIndex(_swings(m5, params.m5_swing_left, params.m5_swing_right, M5_SECONDS))
    starts5 = [bar.timestamp for bar in m5]
    first_touches = {zone.zone_id: _first_h4_touch(zone, h4) for zone in zones4}
    configured_age = float(CONFIG.s146_max_dest_age_days)
    state_lookback = max(int(params.max_wait_bars_5m) * M5_SECONDS,
                         int(configured_age * 86400)) if configured_age > 0 else max(0, score_start - starts5[0])
    state_zones = [zone for zone in zones15
                   if score_start - state_lookback <= zone.active_time <= score_end]
    states = {zone.zone_id: _zone_state(zone, m5, starts5, swings5, params, score_end)
              for zone in state_zones}
    by_confirmation: dict[int, list[Zone]] = defaultdict(list)
    for zone in state_zones:
        state = states[zone.zone_id]
        if state.confirmation_time is not None and score_start <= state.confirmation_time < score_end:
            by_confirmation[state.confirmation_time].append(zone)

    symbol_meta = (metadata.get("symbols") or {}).get(symbol, {}) if isinstance(metadata, dict) else {}
    point = finite(symbol_meta.get("point"), 0.00001)
    broker_symbol = symbol_meta.get("broker_symbol", symbol)
    signals: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    rejection_counts = Counter()

    for cutoff in sorted(by_confirmation):
        index = bisect.bisect_left(starts5, cutoff) - 1
        if index < 0 or index >= len(m5):
            continue
        signal_bar = m5[index]
        destinations, dropped_age, dropped_lag = _destinations_at(zones4, first_touches, h4, cutoff)
        if dropped_age or dropped_lag:
            events.append(_event("destinations_screened", cutoff, symbol,
                                 kept=len(destinations), dropped_age=dropped_age, dropped_bos_lag=dropped_lag))
        # Same-close candidates are evaluated newest active zone first. Only a detector-valid
        # candidate suppresses older candidates; structural/RR rejections deliberately fall through.
        candidates = sorted(by_confirmation[cutoff], key=lambda item: item.active_time, reverse=True)
        for zone in candidates:
            state = states[zone.zone_id]
            destination = _nearest_destination(destinations, zone)
            if destination is None:
                rejection_counts["no_destination"] += 1
                events.append(_event("signal_rejected_no_destination", cutoff, symbol,
                                     zone_id=zone.zone_id, model=state.model))
                continue

            actionable: list[Zone] = []
            for other in zones15:
                if other.direction != zone.direction or other.active_time <= destination.active_time or other.active_time > cutoff:
                    continue
                other_destination = _nearest_destination(destinations, other)
                if other_destination is None or other_destination.zone_id != destination.zone_id:
                    continue
                other_state = states.get(other.zone_id)
                if other_state is not None and _actionable(other_state, index):
                    actionable.append(other)
            if not any(item.zone_id == zone.zone_id for item in actionable):
                actionable.append(zone)
            if zone.direction > 0:
                extreme = min(actionable, key=lambda item: (item.proximal, -item.active_time))
                is_extreme = zone.proximal <= extreme.proximal + EPS
            else:
                extreme = max(actionable, key=lambda item: (item.proximal, item.active_time))
                is_extreme = zone.proximal + EPS >= extreme.proximal
            if CONFIG.s146_require_running_extreme_15m and not is_extreme:
                rejection_counts["non_extreme"] += 1
                events.append(_event("signal_rejected_non_extreme", cutoff, symbol,
                                     candidate_zone_id=zone.zone_id, candidate_proximal=zone.proximal,
                                     running_extreme_zone_id=extreme.zone_id,
                                     running_extreme_proximal=extreme.proximal,
                                     campaign_zone_count=len(actionable),
                                     destination_zone_id=destination.zone_id,
                                     reason="candidate_is_not_running_price_extreme"))
                continue

            direction = zone.direction
            buffer = signal_bar.close * params.stop_buffer_bps / 10000.0
            trigger = signal_bar.high + buffer if direction > 0 else signal_bar.low - buffer
            structural_stop, stop_basis, stop_pool = _liquidity_stop(
                zone, direction, swings5, params, signal_bar, state.swept_level,
                trigger, cutoff,
            )
            target = destination.proximal
            structural_risk = direction * (trigger - structural_stop)
            reward = direction * (target - trigger)
            rr = reward / structural_risk if structural_risk > EPS and reward > EPS else None
            if rr is None or not (params.min_target_reward_risk <= rr <= params.max_target_reward_risk):
                rejection_counts["reward_risk"] += 1
                events.append(_event("signal_rejected_reward_risk", cutoff, symbol,
                                     zone_id=zone.zone_id, model=state.model, reward_risk=rr,
                                     minimum=params.min_target_reward_risk,
                                     maximum=params.max_target_reward_risk))
                continue

            signal_id = stable_id("s146", symbol, direction, signal_bar.timestamp, zone.zone_id)
            spread_at_signal_points = m5_bars[index].spread_points
            spread_at_signal_price = spread_at_signal_points * point
            spread_cost_share = spread_at_signal_price / abs(trigger - structural_stop) if abs(trigger - structural_stop) > EPS else float("inf")
            effective_stop = structural_stop
            if direction < 0 and CONFIG.s146_short_stop_spread_pad > 0:
                padded = structural_stop + spread_at_signal_price * CONFIG.s146_short_stop_spread_pad
                if padded > trigger:
                    effective_stop = padded
            timeline = _signal_events(symbol, zone, destination, state, signal_bar,
                                      signal_id, str(state.model), trigger, structural_stop, target)
            signal: dict[str, Any] = {
                "signal_id": signal_id, "strategy": "s146", "symbol": symbol,
                "broker_symbol": broker_symbol, "direction": "long" if direction > 0 else "short",
                "model": state.model, "signal_time_utc": iso(cutoff),
                "signal_bar_open_utc": iso(signal_bar.timestamp), "trigger": trigger,
                "stop_price": structural_stop, "structural_stop": structural_stop,
                "effective_stop": effective_stop, "destination_target": target,
                "destination_reward_risk": rr, "confirmation_level": state.confirmation_level,
                "swept_level": state.swept_level, "alert_time_utc": iso(state.alert_time),
                "entry_zone_id": zone.zone_id, "entry_zone": _zone_payload(zone),
                "destination_zone_id": destination.zone_id, "destination_zone": _zone_payload(destination),
                "campaign_id": f"{symbol}:{destination.zone_id}",
                "campaign_started_at_utc": iso(destination.active_time),
                "campaign_zone_count": len(actionable),
                "running_extreme_zone_id": zone.zone_id if is_extreme else extreme.zone_id,
                "running_extreme_proximal": zone.proximal if is_extreme else extreme.proximal,
                "entry_zone_is_running_extreme": is_extreme,
                "stop_basis": stop_basis, "stop_liquidity_pool": stop_pool,
                "spread_at_signal_points": spread_at_signal_points,
                "spread_at_signal_price": spread_at_signal_price,
                "spread_pct_of_structural_risk": spread_cost_share,
                "spread_cost_limit": float(CONFIG.s146_max_spread_pct_of_risk),
                "config_snapshot": schema_config_snapshot(CONFIG), "events": timeline,
                "execution_status": "pending_portfolio_merge",
                "_direction": direction, "_signal_epoch": cutoff, "_point": point,
            }
            hypothetical, execution_rejection = simulate_trade(signal, m5_bars, starts5, point, CONFIG)
            if CONFIG.s146_max_spread_pct_of_risk > 0 and spread_cost_share > CONFIG.s146_max_spread_pct_of_risk:
                rejection_counts["spread_cost"] += 1
                execution_rejection = {"reason": "spread_cost", "details": {
                    "spread_price": spread_at_signal_price, "structural_risk": abs(trigger - structural_stop),
                    "spread_pct_of_risk": spread_cost_share, "limit": CONFIG.s146_max_spread_pct_of_risk}}
                events.append(_event("signal_rejected_spread_cost", cutoff, symbol,
                                     signal_id=signal_id, zone_id=zone.zone_id,
                                     spread_pct_of_risk=spread_cost_share,
                                     limit=CONFIG.s146_max_spread_pct_of_risk))
            signal["_hypothetical"] = hypothetical
            signal["_execution_rejection"] = execution_rejection
            signals.append(signal)
            # A detector-valid signal always remains in the export, even when its
            # subsequent spread or portfolio gate rejects the hypothetical trade.
            break

    stats.update({"zones_h4": len(zones4), "zones_m15": len(zones15),
                  "precomputed_confirmations": sum(state.confirmation_time is not None for state in states.values()),
                  "detector_signals": len(signals), "rejections": dict(rejection_counts)})
    progress(f"replay {symbol}: {len(signals)} detector-valid signal(s)")
    return signals, events, stats

def _currency_legs(symbol: str, direction: str) -> list[str]:
    letters = "".join(character for character in symbol.upper() if character.isalpha())
    if len(letters) < 6:
        return []
    base, quote = letters[:3], letters[3:6]
    return [f"{base}_{'long' if direction == 'long' else 'short'}",
            f"{quote}_{'short' if direction == 'long' else 'long'}"]


def merge_portfolio(signals: list[dict[str, Any]], base_events: list[dict[str, Any]],
                    include_portfolio_gates: bool = True,
                    progress: Callable[[str], None] = print
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Chronologically admit independently simulated trades through live exposure gates."""
    active: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    events = list(base_events)
    for signal in sorted(signals, key=lambda item: (item["_signal_epoch"], item["symbol"], item["signal_id"])):
        cutoff = int(signal["_signal_epoch"])
        active = [trade for trade in active
                  if trade.get("_exit_epoch") is None or int(trade["_exit_epoch"]) > cutoff]
        rejection = signal.get("_execution_rejection")
        reason = rejection.get("reason") if rejection else None
        details = dict(rejection.get("details") or {}) if rejection else {}
        hypothetical = signal.get("_hypothetical")

        if (include_portfolio_gates and CONFIG.one_trade_per_pair
                and reason is None and hypothetical is not None):
            same_pair = [trade for trade in active if trade["symbol"] == signal["symbol"]]
            if same_pair:
                reason, details = "one_open_per_canonical_pair", {"active": len(same_pair)}
        if include_portfolio_gates and reason is None and CONFIG.max_concurrent > 0 and len(active) >= CONFIG.max_concurrent:
            reason, details = "max_concurrent", {"active": len(active), "limit": CONFIG.max_concurrent}
        if include_portfolio_gates and reason is None:
            destination_id = signal["destination_zone_id"]
            destination_count = sum(trade["destination_zone_id"] == destination_id for trade in active)
            if CONFIG.s146_max_same_destination > 0 and destination_count >= CONFIG.s146_max_same_destination:
                reason, details = "same_destination_cap", {
                    "destination_zone_id": destination_id, "active": destination_count,
                    "limit": CONFIG.s146_max_same_destination,
                }
        if include_portfolio_gates and reason is None:
            currency_counts = Counter()
            for open_trade in active:
                for leg in _currency_legs(open_trade["symbol"], open_trade["direction"]):
                    currency_counts[leg] += 1
            for leg in _currency_legs(signal["symbol"], signal["direction"]):
                if CONFIG.s146_max_currency_exposure > 0 and currency_counts[leg] >= CONFIG.s146_max_currency_exposure:
                    reason, details = "currency_exposure_cap", {
                        "leg": leg, "active": currency_counts[leg], "limit": CONFIG.s146_max_currency_exposure,
                    }
                    break

        if reason is not None or hypothetical is None:
            signal["execution_status"] = "rejected"
            signal["execution_rejection"] = {"reason": reason or "simulation_unavailable", "details": details}
            for sequence, item in enumerate(signal["events"]):
                item = dict(item)
                item["event_id"] = stable_id("event", signal["signal_id"], "rejected", sequence, item["event"])
                events.append(item)
            events.append(_event("execution_rejected", cutoff, signal["symbol"],
                                 signal_id=signal["signal_id"], reason=reason or "simulation_unavailable",
                                 details=details, event_id=stable_id("event", signal["signal_id"], "reject")))
            continue

        trade = hypothetical
        signal["execution_status"] = "executed"
        signal["trade_id"] = trade["trade_id"]
        for sequence, item in enumerate(trade["events"]):
            item["trade_id"] = trade["trade_id"]
            item["event_id"] = stable_id("event", trade["trade_id"], sequence, item["event"])
            events.append(item)
        trades.append(trade)
        active.append(trade)
    progress(f"portfolio merge: {len(trades)} executed / {len(signals)} detector-valid")
    return trades, events


def config_snapshot() -> dict[str, Any]:
    """Sanitized, reproducible strategy/execution settings (never credentials)."""
    names = [
        "one_trade_per_pair", "max_concurrent", "s146_stop_buffer_bps", "s146_min_rr",
        "s146_max_rr", "s146_max_wait_bars_5m", "s146_max_hold_bars_5m",
        "s146_entry_mode", "s146_ladder_enabled", "s146_partial_at_r",
        "s146_partial_fraction", "s146_trail_step_r", "s146_trail_giveback_r",
        "s146_stop_use_liquidity", "s146_stop_liq_lookback_5m", "s146_stop_liq_max_mult",
        "s146_max_spread_pct_of_risk", "s146_short_stop_spread_pad",
        "s146_max_same_destination", "s146_max_currency_exposure",
        "s146_require_running_extreme_15m", "s146_max_dest_age_days",
        "s146_max_dest_bos_lag_bars",
    ]
    return {name: getattr(CONFIG, name) for name in names}


def _breakdown(trades: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.get(field) or "Unknown")].append(trade)
    result = []
    for name, rows in grouped.items():
        resolved = [row for row in rows if row["outcome"] != "open"]
        values = [float(row["r"]) for row in resolved]
        wins = sum(value > EPS for value in values)
        losses = sum(value < -EPS for value in values)
        gains = sum(max(value, 0.0) for value in values)
        pain = abs(sum(min(value, 0.0) for value in values))
        result.append({"name": name, "trades": len(rows), "resolved": len(resolved),
                       "wins": wins, "losses": losses,
                       "win_rate_pct": 100.0 * wins / len(resolved) if resolved else 0.0,
                       "net_r": sum(float(row["r"]) for row in rows),
                       "profit_factor": gains / pain if pain else (gains if gains else None)})
    return sorted(result, key=lambda item: (-item["trades"], item["name"]))


def build_summary(trades: list[dict[str, Any]], signals: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda item: (item.get("_exit_epoch") or 2**63, item["trade_id"]))
    resolved = [trade for trade in ordered if trade.get("outcome") != "open"]
    values = [float(trade.get("r", 0.0)) for trade in resolved]
    wins = sum(value > EPS for value in values)
    losses = sum(value < -EPS for value in values)
    gains = sum(max(value, 0.0) for value in values)
    pain = abs(sum(min(value, 0.0) for value in values))
    equity = peak = max_dd = 0.0
    curve = []
    for number, trade in enumerate(ordered, 1):
        equity += float(trade.get("r", 0.0))
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        curve.append({"trade": number, "trade_id": trade["trade_id"],
                      "time_utc": trade.get("exit_time_utc") or trade.get("entry_time_utc"),
                      "equity_r": equity, "drawdown_r": dd})
    rejection_counts = Counter((signal.get("execution_rejection") or {}).get("reason")
                               for signal in signals if signal.get("execution_status") == "rejected")
    spread_rejections = sum(1 for signal in signals if (signal.get("execution_rejection") or {}).get("reason") == "spread_cost")
    portfolio_rejections = sum(value for key, value in rejection_counts.items() if key and key != "spread_cost")
    return {
        "total_detector_signals": len(signals), "detector_signals": len(signals),
        "accepted_signals": sum(signal.get("execution_status") == "executed" for signal in signals),
        "portfolio_rejections": portfolio_rejections, "spread_rejections": spread_rejections,
        "execution_rejections": sum(rejection_counts.values()),
        "execution_rejections_by_reason": {key: value for key, value in rejection_counts.items() if key},
        "trades": len(trades), "total_trades": len(trades), "resolved_trades": len(resolved),
        "wins": wins, "losses": losses, "breakeven": sum(abs(value) <= EPS for value in values),
        "open_trades": sum(trade.get("outcome") == "open" for trade in trades),
        "win_rate": wins / (wins + losses) if wins + losses else 0.0,
        "win_rate_pct": 100.0 * wins / (wins + losses) if wins + losses else 0.0,
        "net_r": sum(float(trade.get("r", 0.0)) for trade in trades),
        "resolved_net_r": sum(values), "average_r": sum(values) / len(values) if values else 0.0,
        "profit_factor": gains / pain if pain else (gains if gains else None),
        "max_drawdown_r": max_dd, "equity_curve": curve,
        "breakdowns": {"symbol": _breakdown(trades, "symbol"), "model": _breakdown(trades, "model"),
                       "exit": _breakdown(trades, "exit_reason"), "direction": _breakdown(trades, "direction")},
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_public(value), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(_public(row), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def _write_trades_csv(path: Path, trades: list[dict[str, Any]]) -> None:
    fields = [
        "trade_id", "signal_id", "strategy", "symbol", "broker_symbol", "direction", "model",
        "signal_time_utc", "entry_time_utc", "exit_time_utc", "signal_price", "entry_price", "fill_price",
        "structural_stop", "effective_stop", "stop_price", "initial_risk", "destination_target", "active_target",
        "destination_reward_risk_at_fill", "exit_price", "exit_reason", "status", "outcome", "r", "realized_r",
        "peak_r", "mfe_r", "mae_r", "partial_banked", "partial_fraction", "remaining_fraction",
        "final_stop_price", "final_stop_r", "bars_held_5m", "spread_at_fill_points", "spread_at_fill_price",
        "spread_pct_of_initial_risk",
        "entry_zone_id", "destination_zone_id", "campaign_zone_count", "stop_basis",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for trade in trades:
            writer.writerow(_public(trade))


def replay_history(raw_root: Path, run_dir: Path, symbols: list[str], score_start: datetime,
                   score_end: datetime, run_id: str, include_portfolio_gates: bool = True,
                   progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Replay symbols sequentially, merge signals, and write the complete run artifact set."""
    raw_manifest_path = raw_root / "manifest.json"
    try:
        raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw_manifest = {"symbols": {}}
    start_epoch, end_epoch = int(score_start.timestamp()), int(score_end.timestamp())
    all_signals: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    symbol_stats: dict[str, Any] = {}
    for position, symbol in enumerate(symbols, 1):
        progress(f"[{position}/{len(symbols)}] replay {symbol}")
        signals, events, stats = replay_symbol(
            symbol, raw_root, start_epoch, end_epoch, raw_manifest, progress
        )
        all_signals.extend(signals)
        all_events.extend(events)
        symbol_stats[symbol] = stats

    all_signals.sort(key=lambda item: (item["_signal_epoch"], item["symbol"], item["signal_id"]))
    trades, events = merge_portfolio(all_signals, all_events, include_portfolio_gates, progress)
    events.sort(key=lambda item: (item.get("time_utc") or "", item.get("symbol") or "",
                                  item.get("event") or "", item.get("signal_id") or ""))
    for sequence, event in enumerate(events):
        event.setdefault("event_id", stable_id("event", run_id, sequence, event.get("event"),
                                               event.get("time_utc"), event.get("symbol")))
    trades.sort(key=lambda item: (item["_entry_epoch"], item["trade_id"]))
    summary = build_summary(trades, all_signals)
    cfg = schema_config_snapshot(CONFIG)
    manifest = {
        "schema_version": 2, "run_id": run_id, "label": f"S146 running extreme {run_id}",
        "created_utc": iso(datetime.now(UTC)), "strategy": "s146",
        "score_start_utc": iso(score_start), "score_end_utc": iso(score_end),
        "symbols": symbols, "symbol_count": len(symbols), "include_portfolio_gates": include_portfolio_gates,
        "code": {"package": "backtests.s146_running_extreme", "replay": "causal-native-v2"},
        "raw_root": str(raw_root), "raw_manifest": str(raw_manifest_path),
        "execution_model": "next_m5_market_spread_aware_destination_ladder",
        "causality": "closed native H4/M15/M5; full structure built once and gated by activation/confirmation time",
        "artifacts": ["manifest.json", "config.json", "events.jsonl", "detector_signals.jsonl",
                      "trades.json", "trades.csv", "summary.json", "rejections.json"],
        "assumptions": [
            "M5 OHLC is bid; long fills use ask and long exits use bid; short fills use bid and short exits use ask",
            "same-bar ambiguity is stop-first",
            "newly ratcheted stops become testable on the next M5 bar",
            "H4 mitigation is known only from closed H4 bars",
        ],
        "symbol_stats": symbol_stats,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "config.json", cfg)
    _write_jsonl(run_dir / "events.jsonl", events)
    _write_jsonl(run_dir / "detector_signals.jsonl", all_signals)
    _write_json(run_dir / "rejections.json", [signal for signal in all_signals
                                                if signal.get("execution_status") == "rejected"])
    _write_json(run_dir / "trades.json", trades)
    _write_trades_csv(run_dir / "trades.csv", trades)
    _write_json(run_dir / "summary.json", summary)
    progress(f"wrote run {run_id}: {len(all_signals)} signals, {len(trades)} trades, net {summary['net_r']:.3f}R")
    return {"manifest": _public(manifest), "config": _public(cfg), "summary": _public(summary),
            "run_dir": str(run_dir), "detector_signals": len(all_signals), "trades": len(trades)}


__all__ = ["replay_history", "replay_symbol", "merge_portfolio", "build_summary", "config_snapshot"]
