"""Signal-time S146 feature construction.

Every feature here is derived only from fields that existed when the 5m
confirmation bar closed. Nothing from the fill or the trade outcome is allowed.
The split is enforced by two explicit allowlists plus a denylist that the
dataset builder audits, so a future edit cannot quietly add a leaking column.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc

# Fields in detector_signals.jsonl that are known when the signal bar closes.
SIGNAL_TIME_FIELDS = (
    "signal_id", "trade_id", "symbol", "broker_symbol", "direction", "model",
    "signal_time_utc", "signal_bar_open_utc", "alert_time_utc",
    "trigger", "stop_price", "structural_stop", "effective_stop",
    "destination_target", "destination_reward_risk",
    "confirmation_level", "swept_level",
    "entry_zone", "entry_zone_id", "destination_zone", "destination_zone_id",
    "campaign_id", "campaign_started_at_utc", "campaign_zone_count",
    "running_extreme_zone_id", "running_extreme_proximal",
    "entry_zone_is_running_extreme", "stop_basis", "stop_liquidity_pool",
    "spread_at_signal_points", "spread_at_signal_price",
    "spread_pct_of_structural_risk", "spread_cost_limit",
    "execution_status",
)

# Anything produced by the fill or afterwards. These may only become labels.
POST_SIGNAL_FIELDS = frozenset({
    "entry_price", "fill_price", "entry_time_utc", "exit_price", "exit_time_utc",
    "exit_reason", "status", "outcome", "r", "realized_r", "realized_partial_r",
    "peak_r", "mfe_r", "mae_r", "partial_banked", "partial_fraction",
    "remaining_fraction", "final_stop_price", "final_stop_r", "bars_held_5m",
    "initial_risk", "active_target", "destination_reward_risk_at_fill",
    "spread_at_fill_points", "spread_at_fill_price", "spread_pct_of_initial_risk",
    "gap_detected", "ambiguous_bar_count", "events",
})

MODELS = ("aggressive_liquidation", "conservative_mss")
STOP_BASES = ("15m_zone_distal_plus_buffer", "liquidity_pool", "swept_level",
              "signal_bar_extreme")


def _epoch(value: Any) -> float | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp()
    except ValueError:
        return None


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or abs(denominator) < 1e-12:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def build_features(signal: dict[str, Any], point: float) -> dict[str, Any]:
    """Return the signal-time feature row for one detector signal."""
    direction = 1 if str(signal.get("direction", "")).lower() == "long" else -1
    trigger = _finite(signal.get("trigger"))
    structural = _finite(signal.get("structural_stop"))
    effective = _finite(signal.get("effective_stop")) or structural
    target = _finite(signal.get("destination_target"))

    signal_epoch = _epoch(signal.get("signal_time_utc"))
    alert_epoch = _epoch(signal.get("alert_time_utc"))
    campaign_epoch = _epoch(signal.get("campaign_started_at_utc"))

    entry_zone = signal.get("entry_zone") or {}
    dest_zone = signal.get("destination_zone") or {}
    entry_confirmed = _epoch(entry_zone.get("confirmed_at_utc"))
    dest_confirmed = _epoch(dest_zone.get("confirmed_at_utc"))

    # Risk is measured at the trigger, not the fill: the fill price is not known
    # when the filter has to decide.
    risk_price = None
    if trigger is not None and structural is not None:
        risk_price = direction * (trigger - structural)
        if risk_price <= 0:
            risk_price = None
    reward_price = None
    if trigger is not None and target is not None:
        reward_price = direction * (target - trigger)
        if reward_price <= 0:
            reward_price = None

    entry_height = None
    if entry_zone:
        upper, lower = _finite(entry_zone.get("upper")), _finite(entry_zone.get("lower"))
        if upper is not None and lower is not None:
            entry_height = abs(upper - lower)
    dest_height = None
    if dest_zone:
        upper, lower = _finite(dest_zone.get("upper")), _finite(dest_zone.get("lower"))
        if upper is not None and lower is not None:
            dest_height = abs(upper - lower)

    stamp = datetime.fromtimestamp(signal_epoch, UTC) if signal_epoch else None
    hour = stamp.hour if stamp else None

    row: dict[str, Any] = {
        # --- direction / model ------------------------------------------------
        "f_direction": direction,
        "f_is_long": 1 if direction > 0 else 0,
        # --- geometry ---------------------------------------------------------
        "f_reward_risk": _finite(signal.get("destination_reward_risk")),
        "f_risk_points": _safe_div(risk_price, point),
        "f_reward_points": _safe_div(reward_price, point),
        "f_risk_rel_price": _safe_div(risk_price, trigger),
        "f_entry_zone_height_r": _safe_div(entry_height, risk_price),
        "f_dest_zone_height_r": _safe_div(dest_height, risk_price),
        "f_stop_pad_r": _safe_div(
            None if (effective is None or structural is None) else abs(effective - structural),
            risk_price),
        # --- cost -------------------------------------------------------------
        "f_spread_points": _finite(signal.get("spread_at_signal_points")),
        "f_spread_pct_of_risk": _finite(signal.get("spread_pct_of_structural_risk")),
        # --- structure / campaign --------------------------------------------
        "f_campaign_zone_count": _finite(signal.get("campaign_zone_count")),
        "f_is_running_extreme": 1 if signal.get("entry_zone_is_running_extreme") else 0,
        "f_has_liquidity_pool": 1 if signal.get("stop_liquidity_pool") else 0,
        # --- timing (all backward looking) -----------------------------------
        "f_wait_minutes": _safe_div(
            None if (signal_epoch is None or alert_epoch is None) else signal_epoch - alert_epoch, 60.0),
        "f_entry_zone_age_hours": _safe_div(
            None if (signal_epoch is None or entry_confirmed is None) else signal_epoch - entry_confirmed, 3600.0),
        "f_dest_zone_age_hours": _safe_div(
            None if (signal_epoch is None or dest_confirmed is None) else signal_epoch - dest_confirmed, 3600.0),
        "f_campaign_age_hours": _safe_div(
            None if (signal_epoch is None or campaign_epoch is None) else signal_epoch - campaign_epoch, 3600.0),
        # --- session ----------------------------------------------------------
        "f_hour_utc": hour,
        "f_weekday": stamp.weekday() if stamp else None,
        "f_hour_sin": None if hour is None else math.sin(2 * math.pi * hour / 24.0),
        "f_hour_cos": None if hour is None else math.cos(2 * math.pi * hour / 24.0),
    }
    for model in MODELS:
        row[f"f_model_{model}"] = 1 if signal.get("model") == model else 0
    for basis in STOP_BASES:
        row[f"f_stop_basis_{basis}"] = 1 if signal.get("stop_basis") == basis else 0
    return row


def feature_names(sample: dict[str, Any]) -> list[str]:
    return sorted(key for key in sample if key.startswith("f_"))


__all__ = ["build_features", "feature_names", "SIGNAL_TIME_FIELDS",
           "POST_SIGNAL_FIELDS", "MODELS", "STOP_BASES"]
