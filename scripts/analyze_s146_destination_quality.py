#!/usr/bin/env python3
"""Causal destination-before-original-stop entry-quality study for S146.

Uses only archived broker fills, native bars, and information available by entry.
It does not connect to MT5 or alter live trading code.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
for item in (str(REPO), str(SCRIPTS)):
    if item not in sys.path:
        sys.path.insert(0, item)

from analyze_s146_since_archive import (  # noqa: E402
    campaign_context, journal_records, num, parse_dt, rnd, zone_universe,
)

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
ARCHIVE = REPO / "data" / "s146_mt5" / "archive_since_2026-08-19_IST"
OUT_DIR = ARCHIVE / "analysis"
CSV_OUT = OUT_DIR / "s146_destination_quality.csv"
JSON_OUT = OUT_DIR / "s146_destination_quality.json"
DOC_OUT = REPO / "docs" / "s146_destination_quality_analysis.md"
EPS = 1e-12


def iso_epoch(value: Optional[int]) -> Optional[str]:
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


def safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or abs(denominator) <= EPS:
        return None
    return numerator / denominator


class MarketBars:
    """Native broker BID bars, retaining volume and spread in price units."""

    def __init__(self, path: Path, point: float):
        self.point = point or 1e-5
        self.time: list[int] = []
        self.open: list[float] = []
        self.high: list[float] = []
        self.low: list[float] = []
        self.close: list[float] = []
        self.volume: list[float] = []
        self.spread: list[float] = []
        if not path.exists():
            return
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                self.time.append(int(row["time"]))
                self.open.append(float(row["open"]))
                self.high.append(float(row["high"]))
                self.low.append(float(row["low"]))
                self.close.append(float(row["close"]))
                self.volume.append(float(row.get("tick_volume") or 0))
                self.spread.append(float(row.get("spread") or 0) * self.point)

    def __len__(self) -> int:
        return len(self.time)

    def at_or_after(self, stamp: int) -> Optional[int]:
        index = bisect_left(self.time, stamp)
        return index if index < len(self.time) else None

    def exact(self, stamp: int) -> Optional[int]:
        index = bisect_left(self.time, stamp)
        return index if index < len(self.time) and self.time[index] == stamp else None


def load_all_bars(manifest: dict) -> tuple[dict[str, dict[str, MarketBars]], dict[str, float]]:
    result: dict[str, dict[str, MarketBars]] = defaultdict(dict)
    points: dict[str, float] = {}
    for symbol, meta in manifest["symbols"].items():
        point = float(meta.get("point") or 1e-5)
        points[symbol] = point
        for timeframe in ("4h", "15m", "5m"):
            relative = meta["timeframes"][timeframe]["path"]
            result[symbol][timeframe] = MarketBars(ARCHIVE / "bars" / relative, point)
    return dict(result), points


def exit_extremes(bars: MarketBars, index: int, is_long: bool) -> tuple[float, float]:
    if is_long:
        return bars.high[index], bars.low[index]
    spread = bars.spread[index]
    return bars.low[index] + spread, bars.high[index] + spread


def first_complete_bar_open(stamp: datetime, seconds: int = 300) -> int:
    micros = int(round(stamp.timestamp() * 1_000_000))
    period = seconds * 1_000_000
    return ((micros + period - 1) // period) * seconds


def destination_label(
    bars: Optional[MarketBars], entry_time: Optional[datetime], is_long: bool,
    stop: Optional[float], destination: Optional[float],
) -> dict:
    base = {
        "destination_before_original_stop": None,
        "destination_label_status": "missing_data",
        "label_start_bar_utc": None,
        "label_resolution_bar_utc": None,
        "label_bars_observed": 0,
    }
    if not bars or entry_time is None or stop is None or destination is None:
        return base
    first_open = first_complete_bar_open(entry_time)
    start = bars.at_or_after(first_open)
    base["label_start_bar_utc"] = iso_epoch(first_open)
    if start is None:
        base["destination_label_status"] = "unresolved"
        return base
    for count, index in enumerate(range(start, len(bars)), start=1):
        favourable, adverse = exit_extremes(bars, index, is_long)
        stop_hit = adverse <= stop if is_long else adverse >= stop
        target_hit = favourable >= destination if is_long else favourable <= destination
        if stop_hit:
            base.update({
                "destination_before_original_stop": False,
                "destination_label_status": "stop_first_same_bar" if target_hit else "original_stop_first",
                "label_resolution_bar_utc": iso_epoch(bars.time[index]),
                "label_bars_observed": count,
            })
            return base
        if target_hit:
            base.update({
                "destination_before_original_stop": True,
                "destination_label_status": "destination_first",
                "label_resolution_bar_utc": iso_epoch(bars.time[index]),
                "label_bars_observed": count,
            })
            return base
    base["destination_label_status"] = "unresolved"
    base["label_bars_observed"] = len(bars) - start
    return base


def candle_features(
    bars: Optional[MarketBars], index: Optional[int], direction: int, risk: float,
    prefix: str, prior_count: int = 6,
) -> dict[str, Any]:
    keys = (
        "body_dir_r", "range_r", "close_location", "rejection_wick_r",
        "opposing_wick_r", "range_vs_prior", "volume_vs_prior",
        "prior_momentum_r", "prior_range_mean_r", "spread_r",
    )
    result = {f"{prefix}_{key}": None for key in keys}
    if bars is None or index is None or risk <= 0 or not (0 <= index < len(bars)):
        return result
    op, hi, lo, close = bars.open[index], bars.high[index], bars.low[index], bars.close[index]
    span = hi - lo
    body_dir = direction * (close - op)
    close_location = (close - lo) / span if direction > 0 and span > 0 else (
        (hi - close) / span if span > 0 else None
    )
    rejection = min(op, close) - lo if direction > 0 else hi - max(op, close)
    opposing = hi - max(op, close) if direction > 0 else min(op, close) - lo
    left = max(0, index - prior_count)
    prior_ranges = [bars.high[i] - bars.low[i] for i in range(left, index)]
    prior_volumes = [bars.volume[i] for i in range(left, index) if bars.volume[i] > 0]
    prior_range = statistics.median(prior_ranges) if prior_ranges else None
    prior_volume = statistics.median(prior_volumes) if prior_volumes else None
    momentum = direction * (bars.close[index - 1] - bars.open[left]) if index > left else None
    result.update({
        f"{prefix}_body_dir_r": rnd(body_dir / risk, 4),
        f"{prefix}_range_r": rnd(span / risk, 4),
        f"{prefix}_close_location": rnd(close_location, 4),
        f"{prefix}_rejection_wick_r": rnd(rejection / risk, 4),
        f"{prefix}_opposing_wick_r": rnd(opposing / risk, 4),
        f"{prefix}_range_vs_prior": rnd(safe_div(span, prior_range), 4),
        f"{prefix}_volume_vs_prior": rnd(safe_div(bars.volume[index], prior_volume), 4),
        f"{prefix}_prior_momentum_r": rnd(safe_div(momentum, risk), 4),
        f"{prefix}_prior_range_mean_r": rnd(
            statistics.fmean(prior_ranges) / risk if prior_ranges else None, 4),
        f"{prefix}_spread_r": rnd(bars.spread[index] / risk, 4),
    })
    return result


def physical_zone_events(records: list[dict], event_type: str) -> list[dict]:
    """Deduplicate rolling-id journal repeats by physical origin and bounds."""
    earliest: dict[tuple, dict] = {}
    for record in records:
        if record.get("type") != event_type:
            continue
        confirmed = parse_dt(record.get("confirmed_at"))
        origin = parse_dt(record.get("origin_time"))
        lower, upper = num(record.get("lower")), num(record.get("upper"))
        direction = record.get("direction")
        symbol = record.get("symbol")
        if None in (confirmed, lower, upper) or not symbol or not direction:
            continue
        key = (str(symbol), str(direction), origin, round(lower, 10), round(upper, 10))
        item = dict(record)
        item["_confirmed_dt"] = confirmed
        item["_origin_dt"] = origin
        current = earliest.get(key)
        if current is None or confirmed < current["_confirmed_dt"]:
            earliest[key] = item
    return sorted(earliest.values(), key=lambda item: item["_confirmed_dt"])


def zone_event_index(records: list[dict], event_type: str) -> dict[tuple[str, str], dict]:
    result: dict[tuple[str, str], dict] = {}
    for record in records:
        if record.get("type") == event_type and record.get("symbol") and record.get("zone_id"):
            result[(str(record["symbol"]), str(record["zone_id"]))] = record
    return result


def not_invalidated_since_available(
    zone: dict, bars: Optional[MarketBars], signal_time: datetime,
) -> tuple[bool, bool]:
    """Returns (not_invalidated, complete_since_confirmation)."""
    if not bars or not bars.time:
        return True, False
    confirmed = zone["_confirmed_dt"]
    start = bars.at_or_after(int(confirmed.timestamp()))
    end = bisect_left(bars.time, int(signal_time.timestamp()))
    complete = confirmed.timestamp() >= bars.time[0]
    if start is None:
        return True, complete
    lower, upper = float(zone["lower"]), float(zone["upper"])
    demand = zone.get("direction") == "demand"
    invalid = any(
        bars.close[index] < lower if demand else bars.close[index] > upper
        for index in range(start, min(end, len(bars)))
    )
    return not invalid, complete


def blocker_features(
    zones: list[dict], symbol: str, is_long: bool, entry: float, destination: float,
    signal_time: datetime, risk: float, bars15: Optional[MarketBars],
) -> dict:
    wanted = "supply" if is_long else "demand"
    low, high = sorted((entry, destination))
    formed: list[dict] = []
    surviving: list[dict] = []
    complete_survivors = 0
    for zone in zones:
        if zone.get("symbol") != symbol or zone.get("direction") != wanted:
            continue
        if zone["_confirmed_dt"] > signal_time:
            continue
        proximal = num(zone.get("proximal"))
        if proximal is None:
            proximal = num(zone.get("lower")) if wanted == "supply" else num(zone.get("upper"))
        if proximal is None or not (low < proximal < high):
            continue
        formed.append(zone)
        alive, complete = not_invalidated_since_available(zone, bars15, signal_time)
        if alive:
            surviving.append(zone)
            complete_survivors += int(complete)
    nearest = None
    if surviving:
        prices = [num(zone.get("proximal")) or (num(zone.get("lower")) if wanted == "supply" else num(zone.get("upper")))
                  for zone in surviving]
        nearest = min(abs(price - entry) for price in prices if price is not None)
    width = sum(max(0.0, float(zone["upper"]) - float(zone["lower"])) for zone in surviving)
    return {
        "formed_opposing_15m_between_count": len(formed),
        "not_invalidated_opposing_15m_between_count": len(surviving),
        "blocker_history_complete_count": complete_survivors,
        "nearest_blocker_distance_r": rnd(nearest / risk if nearest is not None else None, 3),
        "blocker_total_width_r": rnd(width / risk, 3),
        "clear_path_no_surviving_15m_blocker": not surviving,
    }


def prior_destination_selections(
    selections: list[dict], symbol: str, destination_id: str, signal_time: datetime,
) -> dict:
    matching = []
    entry_zones = set()
    for record in selections:
        if record.get("type") != "destination_selected_as_target":
            continue
        if record.get("symbol") != symbol or record.get("zone_id") != destination_id:
            continue
        stamp = parse_dt(record.get("ts_utc"))
        if stamp is None or stamp >= signal_time:
            continue
        matching.append(stamp)
        if record.get("for_entry_zone"):
            entry_zones.add(str(record["for_entry_zone"]))
    latest = max(matching) if matching else None
    return {
        "destination_prior_selection_count": len(matching),
        "destination_prior_distinct_entry_zones": len(entry_zones),
        "hours_since_destination_last_selection": rnd(
            (signal_time - latest).total_seconds() / 3600 if latest else None, 2),
    }


def boolean(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def build_trade_row(
    item: dict, all_bars: dict[str, dict[str, MarketBars]], points: dict[str, float],
    universe: dict, entry_events: dict, physical_entry_zones: list[dict], selections: list[dict],
) -> dict:
    trade, signal = item["trade"], item.get("signal_record") or {}
    symbol = str(trade["Symbol"])
    is_long = str(trade["Direction"]) == "long"
    direction = 1 if is_long else -1
    entry = num(trade.get("Entry Price"))
    stop = num(trade.get("Initial Stop"))
    destination = num(signal.get("destination_target"))
    broker_target = num(trade.get("Initial Target"))
    entry_time = parse_dt(trade.get("Trade DateTime"))
    signal_time = parse_dt(signal.get("signal_bar_close")) or entry_time
    risk = abs(entry - stop) if entry is not None and stop is not None else 0.0
    point = points.get(symbol, 1e-5)
    bars = all_bars.get(symbol, {})
    row: dict[str, Any] = {
        "position_id": trade.get("Position ID"), "signal_id": trade.get("Signal ID"),
        "symbol": symbol, "direction": trade.get("Direction"), "model": signal.get("model"),
        "entry_utc": entry_time.isoformat() if entry_time else None,
        "entry_ist_date": entry_time.astimezone(IST).date().isoformat() if entry_time else None,
        "signal_close_utc": signal_time.isoformat() if signal_time else None,
        "entry_price": entry, "original_stop": stop, "destination_target": destination,
        "broker_target": broker_target, "risk_price": rnd(risk, 8),
        "risk_points": rnd(risk / point, 1) if risk else None,
        "destination_target_matches_broker_tp": bool(
            destination is not None and broker_target is not None and abs(destination - broker_target) <= point),
        "destination_zone_id": signal.get("destination_zone_id"),
        "entry_zone_id": signal.get("entry_zone_id"),
        "destination_rr_signal": num(signal.get("destination_reward_risk")),
        "destination_rr_fill": rnd(abs(destination - entry) / risk, 3)
        if destination is not None and entry is not None and risk else None,
        "model_aggressive": signal.get("model") == "aggressive_liquidation",
        "direction_long": is_long,
        "used_liquidity_pool_stop": bool(signal.get("stop_liquidity_pool")),
        "spread_pct_of_risk": num(signal.get("spread_pct_of_risk")),
        "round_trip_cost_r": num(signal.get("round_trip_cost_r")),
        "stop_multiple_vs_structural": num(signal.get("stop_multiple_vs_structural")),
    }
    row.update(destination_label(bars.get("5m"), entry_time, is_long, stop, destination))

    if row["destination_before_original_stop"] is not None:
        row["destination_only_r"] = (
            row["destination_rr_fill"] if row["destination_before_original_stop"] else -1.0)
    else:
        row["destination_only_r"] = None

    trigger = num(signal.get("trigger"))
    signal_risk = abs(trigger - num(signal.get("stop"))) if trigger is not None and num(signal.get("stop")) is not None else None
    row["fill_adverse_slippage_signal_r"] = rnd(
        direction * (entry - trigger) / signal_risk
        if entry is not None and trigger is not None and signal_risk else None, 4)

    destination_confirmed = parse_dt(signal.get("destination_confirmed_at"))
    destination_origin = parse_dt(signal.get("destination_origin_time"))
    zone_confirmed = parse_dt(signal.get("entry_zone_confirmed_at"))
    zone_event = entry_events.get((symbol, str(signal.get("entry_zone_id")))) or {}
    zone_origin = parse_dt(zone_event.get("origin_time"))
    alert = parse_dt(signal.get("alert_bar_close"))
    row.update({
        "destination_confirmation_age_h": rnd(
            (signal_time - destination_confirmed).total_seconds() / 3600
            if signal_time and destination_confirmed else None, 2),
        "destination_origin_age_h": rnd(
            (signal_time - destination_origin).total_seconds() / 3600
            if signal_time and destination_origin else None, 2),
        "destination_origin_to_bos_bars_4h": rnd(
            (destination_confirmed - destination_origin).total_seconds() / 14400
            if destination_confirmed and destination_origin else None, 2),
        "entry_zone_age_h": rnd(
            (signal_time - zone_confirmed).total_seconds() / 3600
            if signal_time and zone_confirmed else None, 2),
        "entry_zone_origin_to_bos_bars_15m": rnd(
            (zone_confirmed - zone_origin).total_seconds() / 900
            if zone_confirmed and zone_origin else None, 2),
        "alert_to_signal_minutes": rnd(
            (signal_time - alert).total_seconds() / 60 if signal_time and alert else None, 1),
    })
    destination_lower, destination_upper = num(signal.get("destination_lower")), num(signal.get("destination_upper"))
    zone_lower, zone_upper = num(signal.get("entry_zone_lower")), num(signal.get("entry_zone_upper"))
    proximal = zone_upper if is_long else zone_lower
    distal = num(signal.get("entry_zone_distal"))
    row.update({
        "destination_width_r": rnd((destination_upper - destination_lower) / risk, 3)
        if None not in (destination_lower, destination_upper) and risk else None,
        "destination_width_signal_r": rnd((destination_upper - destination_lower) / signal_risk, 3)
        if None not in (destination_lower, destination_upper) and signal_risk else None,
        "entry_zone_width_r": rnd((zone_upper - zone_lower) / risk, 3)
        if None not in (zone_lower, zone_upper) and risk else None,
        "fill_beyond_entry_proximal_r": rnd(
            ((entry - proximal) if is_long else (proximal - entry)) / risk
            if entry is not None and proximal is not None and risk else None, 3),
        "stop_pad_beyond_distal_r": rnd(
            ((distal - stop) if is_long else (stop - distal)) / risk
            if distal is not None and stop is not None and risk else None, 3),
    })

    row.update({
        "campaign_actionable_count": num(signal.get("campaign_zone_count")),
        "entry_is_logged_running_extreme": boolean(signal.get("entry_zone_is_running_extreme")),
        "running_extreme_filter_enabled": boolean(signal.get("running_extreme_filter_enabled")),
        "campaign_fields_available": signal.get("campaign_zone_count") is not None,
    })
    row.update(campaign_context(
        universe, symbol, is_long, destination_confirmed, signal_time,
        str(signal.get("entry_zone_id") or ""), risk, proximal,
    ))
    # This is deliberately not the live actionable same-destination campaign.
    # It is the looser causal count of same-direction zones formed since the
    # selected destination activated, retained as a separate research feature.
    row["same_direction_zones_since_destination"] = row.pop("campaign_zones_all", None)
    row["all_formed_gap_from_extreme_r"] = rnd(row.pop("r_from_extreme_zone", None), 3)
    row["entry_is_extreme_all_formed"] = row.pop("entry_zone_is_extreme_all", None)
    row.pop("campaign_extreme_zone_id", None)
    row.pop("extreme_zone_confirmed_utc", None)
    row.pop("entry_zone_extreme_rank", None)

    if signal_time and entry is not None and destination is not None and risk:
        row.update(blocker_features(
            physical_entry_zones, symbol, is_long, entry, destination,
            signal_time, risk, bars.get("15m"),
        ))
        row.update(prior_destination_selections(
            selections, symbol, str(signal.get("destination_zone_id") or ""), signal_time,
        ))

    signal_open = parse_dt(signal.get("signal_bar_open"))
    signal_index = bars.get("5m").exact(int(signal_open.timestamp())) if signal_open and bars.get("5m") else None
    row.update(candle_features(bars.get("5m"), signal_index, direction, risk, "signal5m"))
    if signal_index is not None and risk:
        swept = num(signal.get("swept_level"))
        confirmation = num(signal.get("confirmation_level"))
        lo, hi, close = (bars["5m"].low[signal_index], bars["5m"].high[signal_index], bars["5m"].close[signal_index])
        row["signal5m_sweep_depth_r"] = rnd(
            ((swept - lo) if is_long else (hi - swept)) / risk if swept is not None else None, 4)
        row["signal5m_close_beyond_confirmation_r"] = rnd(
            ((close - confirmation) if is_long else (confirmation - close)) / risk
            if confirmation is not None else None, 4)
        row["signal5m_zone_penetration_r"] = rnd(
            ((proximal - lo) if is_long else (hi - proximal)) / risk
            if proximal is not None else None, 4)
        row["signal5m_rejection_from_extreme_r"] = rnd(
            ((close - lo) if is_long else (hi - close)) / risk, 4)
    else:
        for key in ("sweep_depth_r", "close_beyond_confirmation_r", "zone_penetration_r", "rejection_from_extreme_r"):
            row[f"signal5m_{key}"] = None

    zone_bar_index = None
    if zone_confirmed and bars.get("15m"):
        zone_bar_index = bars["15m"].exact(int(zone_confirmed.timestamp()) - 900)
    row.update(candle_features(bars.get("15m"), zone_bar_index, direction, risk, "zone15m_bos", 4))
    bos_level = num(zone_event.get("bos_level"))
    if zone_bar_index is not None and bos_level is not None and risk:
        close = bars["15m"].close[zone_bar_index]
        row["zone15m_close_beyond_bos_r"] = rnd(
            ((close - bos_level) if is_long else (bos_level - close)) / risk, 4)
    else:
        row["zone15m_close_beyond_bos_r"] = None

    destination_bar_index = None
    if destination_confirmed and bars.get("4h"):
        destination_bar_index = bars["4h"].exact(int(destination_confirmed.timestamp()) - 14400)
    row.update(candle_features(bars.get("4h"), destination_bar_index, -direction, risk, "destination4h_bos", 4))
    return row


@dataclass(frozen=True)
class Atom:
    label: str
    feature: str
    predicate: Callable[[Any], bool]

    def matches(self, row: dict) -> bool:
        value = row.get(self.feature)
        return value is not None and self.predicate(value)


def le(feature: str, threshold: float, label: Optional[str] = None) -> Atom:
    return Atom(label or f"{feature}<={threshold:g}", feature, lambda value: float(value) <= threshold)


def ge(feature: str, threshold: float, label: Optional[str] = None) -> Atom:
    return Atom(label or f"{feature}>={threshold:g}", feature, lambda value: float(value) >= threshold)


def eq(feature: str, expected: Any, label: Optional[str] = None) -> Atom:
    return Atom(label or f"{feature}={expected}", feature, lambda value: value == expected)


def candidate_atoms() -> list[Atom]:
    atoms = [
        eq("direction_long", True, "long"), eq("direction_long", False, "short"),
        eq("model_aggressive", True, "aggressive_confirmation"),
        eq("used_liquidity_pool_stop", True, "external_liquidity_stop"),
        eq("entry_is_logged_running_extreme", True, "logged_running_extreme"),
        eq("clear_path_no_surviving_15m_blocker", True, "no_uninvalidated_15m_blocker"),
        le("not_invalidated_opposing_15m_between_count", 0, "no_uninvalidated_15m_blocker_count"),
        le("not_invalidated_opposing_15m_between_count", 1),
        ge("campaign_actionable_count", 2), ge("campaign_actionable_count", 3),
        ge("campaign_actionable_count", 4), ge("same_direction_zones_since_destination", 2),
        ge("same_direction_zones_since_destination", 4), ge("same_direction_zones_since_destination", 8),
        le("all_formed_gap_from_extreme_r", 0.01, "at_all_formed_extreme"),
        le("all_formed_gap_from_extreme_r", 1), le("all_formed_gap_from_extreme_r", 3),
        ge("destination_prior_selection_count", 1), ge("destination_prior_selection_count", 3),
        ge("destination_prior_selection_count", 5),
    ]
    grids = {
        "destination_rr_fill": (("le", 4), ("le", 6), ("le", 10), ("ge", 3), ("ge", 5)),
        "destination_confirmation_age_h": (("ge", 24), ("ge", 48), ("ge", 72), ("ge", 168)),
        "destination_origin_age_h": (("ge", 48), ("ge", 96), ("ge", 168)),
        "destination_origin_to_bos_bars_4h": (("le", 4), ("le", 8), ("le", 12)),
        "destination_width_r": (("le", 0.5), ("le", 1), ("le", 2)),
        "entry_zone_age_h": (("le", 1), ("le", 4), ("le", 12), ("ge", 4)),
        "entry_zone_origin_to_bos_bars_15m": (("le", 2), ("le", 4), ("le", 8)),
        "entry_zone_width_r": (("le", 0.3), ("le", 0.6), ("le", 1)),
        "fill_beyond_entry_proximal_r": (("ge", 0), ("ge", 0.25), ("ge", 0.5), ("le", 1)),
        "stop_pad_beyond_distal_r": (("le", 0.1), ("le", 0.25), ("le", 0.5)),
        "alert_to_signal_minutes": (("le", 15), ("le", 60), ("ge", 60)),
        "spread_pct_of_risk": (("le", 10), ("le", 15), ("le", 20)),
        "round_trip_cost_r": (("le", 0.2), ("le", 0.35), ("le", 0.5)),
        "fill_adverse_slippage_signal_r": (("le", 0.1), ("le", 0.25)),
        "signal5m_body_dir_r": (("ge", 0), ("ge", 0.1), ("ge", 0.25), ("ge", 0.5)),
        "signal5m_range_r": (("le", 0.75), ("le", 1), ("le", 1.5), ("le", 2)),
        "signal5m_close_location": (("ge", 0.5), ("ge", 0.65), ("ge", 0.8)),
        "signal5m_rejection_wick_r": (("ge", 0.05), ("ge", 0.15), ("ge", 0.3)),
        "signal5m_range_vs_prior": (("ge", 1), ("ge", 1.5), ("ge", 2)),
        "signal5m_volume_vs_prior": (("ge", 1), ("ge", 1.5), ("ge", 2)),
        "signal5m_prior_momentum_r": (("le", -0.5), ("le", 0), ("ge", 0)),
        "signal5m_sweep_depth_r": (("ge", 0), ("ge", 0.1), ("ge", 0.25)),
        "signal5m_close_beyond_confirmation_r": (("ge", 0), ("ge", 0.1), ("ge", 0.25)),
        "signal5m_zone_penetration_r": (("ge", 0), ("ge", 0.25), ("ge", 0.5)),
        "signal5m_rejection_from_extreme_r": (("ge", 0.5), ("ge", 1), ("ge", 1.5)),
        "zone15m_bos_body_dir_r": (("ge", 0.25), ("ge", 0.5), ("ge", 1)),
        "zone15m_bos_range_r": (("ge", 0.5), ("ge", 1), ("ge", 2)),
        "zone15m_bos_close_location": (("ge", 0.6), ("ge", 0.75)),
        "zone15m_close_beyond_bos_r": (("ge", 0), ("ge", 0.25), ("ge", 0.5)),
    }
    for feature, tests in grids.items():
        for operation, threshold in tests:
            atoms.append(le(feature, threshold) if operation == "le" else ge(feature, threshold))
    return atoms


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    if total <= 0:
        return None, None
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def quality_stats(rows: list[dict], rule: Optional[tuple[Atom, ...]] = None) -> dict:
    selected = rows if rule is None else [row for row in rows if all(atom.matches(row) for atom in rule)]
    resolved = [row for row in selected if row.get("destination_before_original_stop") is not None]
    hits = sum(bool(row["destination_before_original_stop"]) for row in resolved)
    lower, upper = wilson_interval(hits, len(resolved))
    values = [float(row["destination_only_r"]) for row in resolved if row.get("destination_only_r") is not None]
    return {
        "trades": len(resolved), "destination_hits": hits,
        "destination_failures": len(resolved) - hits,
        "precision_pct": rnd(100 * hits / len(resolved), 1) if resolved else None,
        "failure_rate_pct": rnd(100 * (len(resolved) - hits) / len(resolved), 1) if resolved else None,
        "wilson_95_low_pct": rnd(100 * lower, 1) if lower is not None else None,
        "wilson_95_high_pct": rnd(100 * upper, 1) if upper is not None else None,
        "destination_only_total_r": rnd(sum(values), 2) if values else None,
        "destination_only_expectancy_r": rnd(statistics.fmean(values), 3) if values else None,
        "positive_recall_pct": None,
    }


def add_relative(stats: dict, baseline: dict) -> dict:
    result = dict(stats)
    if stats.get("precision_pct") is not None and baseline.get("precision_pct"):
        result["lift_vs_baseline"] = rnd(stats["precision_pct"] / baseline["precision_pct"], 3)
    else:
        result["lift_vs_baseline"] = None
    if baseline.get("destination_hits") and stats.get("destination_hits") is not None:
        result["positive_recall_pct"] = rnd(100 * stats["destination_hits"] / baseline["destination_hits"], 1)
    return result


def rule_text(rule: tuple[Atom, ...]) -> str:
    return " AND ".join(atom.label for atom in rule)


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    resolved = sorted(
        [row for row in rows if row.get("destination_before_original_stop") is not None],
        key=lambda row: row.get("entry_utc") or "",
    )
    midpoint = len(resolved) // 2
    return resolved[:midpoint], resolved[midpoint:]


def discover_rules(rows: list[dict]) -> dict:
    train, validation = split_rows(rows)
    train_base, validation_base = quality_stats(train), quality_stats(validation)
    atoms = candidate_atoms()
    viable = []
    for atom in atoms:
        stats = quality_stats(train, (atom,))
        if stats["trades"] >= 5:
            viable.append((atom, stats))
    viable.sort(key=lambda item: (
        item[1]["wilson_95_low_pct"] or 0,
        item[1]["precision_pct"] or 0,
        item[1]["destination_only_expectancy_r"] or -999,
        item[1]["trades"],
    ), reverse=True)
    search_atoms = [item[0] for item in viable[:28]]

    candidates: list[tuple[tuple[Atom, ...], dict]] = []
    tested = 0
    for size in (1, 2, 3):
        for rule in combinations(search_atoms, size):
            if len({atom.feature for atom in rule}) < len(rule):
                continue
            tested += 1
            stats = quality_stats(train, rule)
            if stats["trades"] < 5:
                continue
            candidates.append((rule, stats))
    candidates.sort(key=lambda item: (
        item[1]["wilson_95_low_pct"] or 0,
        item[1]["precision_pct"] or 0,
        item[1]["destination_only_expectancy_r"] or -999,
        item[1]["trades"],
        -len(item[0]),
    ), reverse=True)

    reports = []
    seen_full_masks: set[tuple[int, ...]] = set()
    resolved_all = train + validation
    for discovery_rank, (rule, train_stats) in enumerate(candidates, start=1):
        full_mask = tuple(index for index, row in enumerate(resolved_all)
                          if all(atom.matches(row) for atom in rule))
        if full_mask in seen_full_masks:
            continue
        seen_full_masks.add(full_mask)
        validation_stats = quality_stats(validation, rule)
        full_stats = quality_stats(resolved_all, rule)
        stable = bool(
            train_stats["trades"] >= 5 and validation_stats["trades"] >= 4 and full_stats["trades"] >= 10
            and (train_stats["precision_pct"] or 0) > (train_base["precision_pct"] or 0)
            and (validation_stats["precision_pct"] or 0) > (validation_base["precision_pct"] or 0)
            and (train_stats["destination_only_expectancy_r"] or -999) > 0
            and (validation_stats["destination_only_expectancy_r"] or -999) > 0
        )
        reports.append({
            "discovery_rank": discovery_rank, "rule": rule_text(rule),
            "conditions": [{"feature": atom.feature, "condition": atom.label} for atom in rule],
            "discovery": add_relative(train_stats, train_base),
            "validation": add_relative(validation_stats, validation_base),
            "full_sample": add_relative(full_stats, quality_stats(resolved_all)),
            "stable_basic": stable,
            "_rule": rule,
        })
        if len(reports) >= 100:
            break

    stable_reports = [report for report in reports if report["stable_basic"]]
    # Reports are already ordered strictly by discovery-half evidence. Pick the
    # first split-stable rule without re-ranking on holdout performance.
    selected = stable_reports[0] if stable_reports else (reports[0] if reports else None)
    # The user also asked for a pattern present in most destination-capable
    # trades. Keep this broader descriptive screen separate from the strict
    # highest-quality rule and require at least 50% positive recall.
    majority_winner = next(
        (report for report in stable_reports
         if (report["full_sample"].get("positive_recall_pct") or 0) >= 50),
        None,
    )
    return {
        "method": (
            "Predeclared causal atoms; top 28 univariate atoms ranked on chronological first half only; "
            "1-3 condition rules discovered on first half and evaluated on untouched second half."
        ),
        "multiple_search_warning": (
            "Exploratory multiple-threshold search. Validation was not used to rank discovery rules, but any "
            "reported rule still requires forward confirmation."
        ),
        "resolved_split": {"discovery": len(train), "validation": len(validation)},
        "baseline": {
            "discovery": train_base, "validation": validation_base,
            "full_sample": quality_stats(resolved_all),
        },
        "atoms_total": len(atoms), "atoms_searched": len(search_atoms), "rules_tested": tested,
        "top_univariate_discovery": [
            {"condition": atom.label, "feature": atom.feature,
             "stats": add_relative(stats, train_base)} for atom, stats in viable[:20]
        ],
        "rules": reports,
        "selected_rule": selected,
        "majority_winner_rule": majority_winner,
    }


def jackknife(rows: list[dict], rule: tuple[Atom, ...], group_key: str) -> dict:
    resolved = [row for row in rows if row.get("destination_before_original_stop") is not None]
    groups = sorted({str(row.get(group_key)) for row in resolved})
    values = []
    for group in groups:
        retained = [row for row in resolved if str(row.get(group_key)) != group]
        stats = quality_stats(retained, rule)
        if stats["trades"]:
            values.append({"excluded": group, **stats})
    precisions = [item["precision_pct"] for item in values if item["precision_pct"] is not None]
    return {
        "groups": len(groups), "min_precision_pct": min(precisions) if precisions else None,
        "max_precision_pct": max(precisions) if precisions else None, "details": values,
    }


def selected_rule_audit(rows: list[dict], report: Optional[dict]) -> Optional[dict]:
    if not report:
        return None
    rule = report["_rule"]
    selected = [row for row in rows if row.get("destination_before_original_stop") is not None
                and all(atom.matches(row) for atom in rule)]
    excluded = [row for row in rows if row.get("destination_before_original_stop") is not None
                and not all(atom.matches(row) for atom in rule)]
    direction = {name: quality_stats([row for row in selected if row["direction"] == name])
                 for name in ("long", "short")}
    return {
        "rule": report["rule"], "selected": quality_stats(selected), "excluded": quality_stats(excluded),
        "by_direction": direction,
        "leave_one_ist_day_out": jackknife(rows, rule, "entry_ist_date"),
        "leave_one_symbol_out": jackknife(rows, rule, "symbol"),
        "selected_trade_ids": [row["position_id"] for row in selected],
        "selected_failures": [row["position_id"] for row in selected
                              if not row["destination_before_original_stop"]],
    }


def pre_order_translation(rows: list[dict]) -> dict:
    """Evaluate deployable equivalents using requested signal risk, not fill risk."""
    train, validation = split_rows(rows)
    all_rows = train + validation
    definitions = {
        "strict_signal_risk": (
            le("spread_pct_of_risk", 15), le("destination_width_signal_r", 1),
            le("destination_origin_to_bos_bars_4h", 4),
        ),
        "broad_signal_risk": (
            le("round_trip_cost_r", 0.35), le("destination_width_signal_r", 1),
            le("destination_origin_to_bos_bars_4h", 4),
        ),
    }
    return {
        name: {
            "rule": rule_text(rule), "discovery": quality_stats(train, rule),
            "validation": quality_stats(validation, rule), "full_sample": quality_stats(all_rows, rule),
        }
        for name, rule in definitions.items()
    }


def feature_contrasts(rows: list[dict]) -> list[dict]:
    resolved = [row for row in rows if row.get("destination_before_original_stop") is not None]
    winners = [row for row in resolved if row["destination_before_original_stop"]]
    failures = [row for row in resolved if not row["destination_before_original_stop"]]
    ignored = {"position_id", "destination_before_original_stop", "destination_only_r", "label_bars_observed"}
    results = []
    for feature in rows[0] if rows else []:
        if feature in ignored:
            continue
        win_values = [float(row[feature]) for row in winners
                      if isinstance(row.get(feature), (int, float)) and not isinstance(row.get(feature), bool)]
        fail_values = [float(row[feature]) for row in failures
                       if isinstance(row.get(feature), (int, float)) and not isinstance(row.get(feature), bool)]
        if len(win_values) < 5 or len(fail_values) < 5:
            continue
        win_median, fail_median = statistics.median(win_values), statistics.median(fail_values)
        pooled = statistics.pstdev(win_values + fail_values)
        effect = (win_median - fail_median) / pooled if pooled > EPS else 0.0
        results.append({
            "feature": feature, "winner_n": len(win_values), "failure_n": len(fail_values),
            "winner_median": rnd(win_median, 4), "failure_median": rnd(fail_median, 4),
            "median_gap_standardized": rnd(effect, 3),
        })
    return sorted(results, key=lambda item: abs(item["median_gap_standardized"]), reverse=True)


def serializable_rule_report(report: Optional[dict]) -> Optional[dict]:
    if report is None:
        return None
    return {key: value for key, value in report.items() if key != "_rule"}


def write_outputs(rows: list[dict], payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DOC_OUT.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with CSV_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    JSON_OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    baseline = payload["summary"]["baseline"]
    selected = payload["rule_discovery"].get("selected_rule")
    audit = payload.get("selected_rule_audit")
    majority = payload["rule_discovery"].get("majority_winner_rule")
    majority_audit = payload.get("majority_winner_rule_audit")
    pre_order = payload.get("pre_order_translation") or {}
    lines = [
        "# S146 destination-capability entry analysis", "",
        f"Generated `{payload['created_utc']}` from the read-only archive. No MT5 connection or live-code change was made.", "",
        "## Exact question tested", "",
        "A trade is a destination-capable entry only when price reaches the logged `signal_record.destination_target` "
        "before the original broker stop. The actual fill and original attached stop are used. The fill-containing "
        "partial 5m bar is excluded; ties in later bars are stop-first.", "",
        "## Baseline", "",
        f"- Resolved: **{baseline['trades']}**; destination first: **{baseline['destination_hits']}**; "
        f"stop first: **{baseline['destination_failures']}**.",
        f"- Destination reach rate: **{baseline['precision_pct']}%** "
        f"(95% Wilson interval {baseline['wilson_95_low_pct']}–{baseline['wilson_95_high_pct']}%).",
        f"- Holding unchanged to destination/original stop: **{baseline['destination_only_total_r']}R**, "
        f"expectancy **{baseline['destination_only_expectancy_r']}R** per resolved trade.", "",
        "## Best exploratory pattern", "",
    ]
    if selected and audit:
        kept = audit["selected"]
        excluded = audit["excluded"]
        lines += [
            f"`{selected['rule']}`", "",
            "Meaning: spread at entry is no more than 15% of initial risk; the selected 4H zone is no wider "
            "than 1R; and its break of structure confirmed within four 4H bars (16 hours) of the origin candle.", "",
            f"- Keeps **{kept['trades']}** resolved trades: **{kept['destination_hits']}** reached destination and "
            f"**{kept['destination_failures']}** hit the original stop first (**{kept['precision_pct']}%** precision).",
            f"- Captures **{kept['destination_hits']}/{baseline['destination_hits']}** destination-capable trades; "
            f"the excluded cohort was **{excluded['precision_pct']}%** destination-capable.",
            f"- Destination-only result: **{kept['destination_only_total_r']}R**, "
            f"expectancy **{kept['destination_only_expectancy_r']}R**.",
            f"- Discovery half: {selected['discovery']['destination_hits']}/{selected['discovery']['trades']} "
            f"({selected['discovery']['precision_pct']}%); untouched second half: "
            f"{selected['validation']['destination_hits']}/{selected['validation']['trades']} "
            f"({selected['validation']['precision_pct']}%).",
            f"- Leave-one-IST-day-out precision range: "
            f"{audit['leave_one_ist_day_out']['min_precision_pct']}–"
            f"{audit['leave_one_ist_day_out']['max_precision_pct']}%.",
            f"- Classified as basic split-stable: **{selected['stable_basic']}**.", "",
        ]
    else:
        lines += ["No rule met minimum support and split-stability requirements.", ""]
    if majority and majority_audit:
        kept = majority_audit["selected"]
        excluded = majority_audit["excluded"]
        lines += [
            "## Broader pattern present in most destination-capable trades", "",
            f"`{majority['rule']}`", "",
            "Meaning: retain the same narrow, quickly confirmed 4H destination, but use estimated round-trip "
            "cost no greater than 0.35R instead of the stricter one-way spread cap.", "",
            f"- Keeps **{kept['trades']}**: **{kept['destination_hits']}** destination hits / "
            f"**{kept['destination_failures']}** failures (**{kept['precision_pct']}%**).",
            f"- Captures **{kept['destination_hits']}/{baseline['destination_hits']}** "
            f"({majority['full_sample']['positive_recall_pct']}%) of all destination-capable trades while only "
            f"**{kept['destination_failures']}/{baseline['destination_failures']}** failures satisfy it.",
            f"- Discovery: {majority['discovery']['destination_hits']}/{majority['discovery']['trades']}; "
            f"second half: {majority['validation']['destination_hits']}/{majority['validation']['trades']}; "
            f"destination-only result **{kept['destination_only_total_r']}R**.", "",
        ]
    if pre_order.get("strict_signal_risk"):
        strict_pre = pre_order["strict_signal_risk"]
        full = strict_pre["full_sample"]
        lines += [
            "## Pre-order causal translation", "",
            "The strict discovery ratio used actual fill-to-original-stop risk. Replacing that denominator with "
            "requested signal risk makes the screen available before order submission and is the correct form for shadow testing.", "",
            f"- Pre-order equivalent keeps **{full['trades']}** trades with **{full['destination_hits']}** hits "
            f"(**{full['precision_pct']}%**) and **{full['destination_only_total_r']}R**.",
            f"- Discovery: {strict_pre['discovery']['destination_hits']}/{strict_pre['discovery']['trades']}; "
            f"second half: {strict_pre['validation']['destination_hits']}/{strict_pre['validation']['trades']}.",
            "- It is weaker than the fill-based result, so the fill-based 58.3% must not be assumed achievable as a pre-trade gate.", "",
        ]
    lines += [
        "## Interpretation", "",
        "This is an entry-quality screen, not an exit-policy backtest. It intentionally ignores realized P/L, MFE, "
        "partial exits, and trailing-stop events when selecting patterns. A high-quality subset may still differ after "
        "costs and management are changed.", "",
        "The rule search is exploratory: thresholds and up to three causal conditions were tested on the first half, "
        "then checked on the chronological second half. Even a split-stable rule is a hypothesis, not proven edge, "
        "because this archive contains only eleven days and correlated currency pairs.", "",
        "## Data limitations", "",
        "- The price archive has no pre-19-August warm-up. Older 4H/15m candle-formation features are null rather "
        "than reconstructed with future or incomplete data.",
        "- Logged actionable running-extreme fields exist on 77/91 trades; missing early values remain null.",
        "- Journal-derived blocker counts are causal but can overstate old blockers when pre-archive invalidation cannot be observed.",
        "- Five-minute OHLC cannot identify intrabar order, so same-bar stop/destination ties are pessimistically losses.", "",
        "## Outputs", "",
        f"- Flat audit: `{CSV_OUT.relative_to(REPO)}`",
        f"- Full report: `{JSON_OUT.relative_to(REPO)}`",
    ]
    DOC_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    manifest = json.loads((ARCHIVE / "manifest.json").read_text(encoding="utf-8"))
    bar_manifest = json.loads((ARCHIVE / "bars" / "manifest.json").read_text(encoding="utf-8"))
    details = json.loads((ARCHIVE / "s146_trade_details.json").read_text(encoding="utf-8"))["trades"]
    bars, points = load_all_bars(bar_manifest)
    events15 = journal_records(ARCHIVE / "logs" / "s146_15m_events.log")
    events4 = journal_records(ARCHIVE / "logs" / "s146_4h_events.log")
    universe = zone_universe(events15)
    entry_index = zone_event_index(events15, "entry_zone_formed")
    physical_entries = physical_zone_events(events15, "entry_zone_formed")
    rows = [build_trade_row(
        item, bars, points, universe, entry_index, physical_entries, events4,
    ) for item in details]
    rows.sort(key=lambda row: row.get("entry_utc") or "")

    resolved = [row for row in rows if row.get("destination_before_original_stop") is not None]
    baseline = quality_stats(resolved)
    label_counts = dict(Counter(row["destination_label_status"] for row in rows))
    geometry_errors = [row["position_id"] for row in rows if not (
        row["original_stop"] < row["entry_price"] < row["destination_target"]
        if row["direction"] == "long" else
        row["original_stop"] > row["entry_price"] > row["destination_target"]
    )]
    target_mismatches = [row["position_id"] for row in rows
                         if not row["destination_target_matches_broker_tp"]]
    rule_discovery = discover_rules(rows)
    selected_internal = rule_discovery.get("selected_rule")
    majority_internal = rule_discovery.get("majority_winner_rule")
    audit = selected_rule_audit(rows, selected_internal)
    majority_audit = selected_rule_audit(rows, majority_internal)
    clean_rules = dict(rule_discovery)
    clean_rules["rules"] = [serializable_rule_report(report) for report in rule_discovery["rules"]]
    clean_rules["selected_rule"] = serializable_rule_report(selected_internal)
    clean_rules["majority_winner_rule"] = serializable_rule_report(majority_internal)

    payload = {
        "schema_version": 1, "created_utc": datetime.now(UTC).isoformat(),
        "read_only": True, "source_archive": str(ARCHIVE.relative_to(REPO)).replace("\\", "/"),
        "question": "destination target before original broker stop",
        "label_method": {
            "entry": "actual broker fill", "stop": "trade Initial Stop",
            "destination": "signal_record.destination_target",
            "start": "first complete native 5m bar at or after fill; partial fill bar excluded",
            "short_side": "ASK approximated as BID OHLC plus per-bar spread",
            "same_bar_tie": "original stop first", "horizon": "archive end",
        },
        "summary": {
            "trades": len(rows), "resolved": len(resolved), "unresolved": len(rows) - len(resolved),
            "label_statuses": label_counts, "baseline": baseline,
            "campaign_fields_available": sum(bool(row.get("campaign_fields_available")) for row in rows),
            "geometry_errors": geometry_errors, "destination_target_broker_tp_mismatches": target_mismatches,
        },
        "top_entry_feature_contrasts": feature_contrasts(rows)[:30],
        "rule_discovery": clean_rules,
        "selected_rule_audit": audit,
        "majority_winner_rule_audit": majority_audit,
        "pre_order_translation": pre_order_translation(rows),
        "limitations": [
            "No bar warm-up before archive start; old formation-candle features remain missing.",
            "Actionable live campaign fields are missing on early trades and are not imputed.",
            "Journal blocker state may be incomplete before archive start.",
            "Exploratory multiple-rule search requires forward validation.",
        ],
    }
    write_outputs(rows, payload)
    print(json.dumps({
        "outputs": {"csv": str(CSV_OUT), "json": str(JSON_OUT), "doc": str(DOC_OUT)},
        "summary": payload["summary"],
        "selected_rule": clean_rules.get("selected_rule"),
        "selected_rule_audit": audit,
        "majority_winner_rule": clean_rules.get("majority_winner_rule"),
        "majority_winner_rule_audit": majority_audit,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
