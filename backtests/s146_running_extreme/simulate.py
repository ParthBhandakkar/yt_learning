"""Spread-aware native M5 execution and management simulation."""
from __future__ import annotations

import bisect
import math
from typing import Any

try:
    from .schemas import json_safe
except ImportError:  # direct module invocation support
    from schemas import json_safe  # type: ignore

EPS = 1e-10
M5_SECONDS = 300


def _iso(stamp: int | float | None) -> str | None:
    if stamp is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(stamp), timezone.utc).isoformat().replace("+00:00", "Z")


def _event(event_type: str, stamp: int, signal: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"event": event_type, "event_type": event_type, "time_utc": _iso(stamp),
            "symbol": signal["symbol"], "signal_id": signal["signal_id"], **fields}


def _prices(bar: Any, point: float) -> dict[str, float]:
    c = bar.candle
    spread = max(0.0, float(bar.spread_points)) * point
    return {"bid_open": c.open, "bid_high": c.high, "bid_low": c.low, "bid_close": c.close,
            "ask_open": c.open + spread, "ask_high": c.high + spread,
            "ask_low": c.low + spread, "ask_close": c.close + spread,
            "spread_price": spread}


def simulate_trade(signal: dict[str, Any], bars: list[Any], starts: list[int], point: float,
                   config: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Simulate one already detector-qualified market signal.

    M5 input is native bid OHLC. Long execution/exit is bid; short execution/exit is
    ask (bid plus the bar spread). A bar that spans both stop and favorable levels
    is deliberately stop-first; this limitation is recorded on the trade.
    """
    direction = int(signal["_direction"])
    signal_time = int(signal["_signal_epoch"])
    fill_index = bisect.bisect_left(starts, signal_time)
    if fill_index >= len(bars):
        return None, {"reason": "no_next_m5_bar", "details": {}}
    fill_bar = bars[fill_index]
    fill = _prices(fill_bar, point)
    entry = fill["ask_open"] if direction > 0 else fill["bid_open"]
    stop = float(signal.get("effective_stop", signal["stop_price"]))
    structural_stop = float(signal.get("structural_stop", signal["stop_price"]))
    risk = direction * (entry - stop)
    if risk <= EPS:
        return None, {"reason": "invalid_fill_risk", "details": {"entry": entry, "stop": stop}}

    ladder = bool(getattr(config, "s146_ladder_enabled", True))
    partial_at = float(getattr(config, "s146_partial_at_r", 1.25))
    fraction = min(1.0, max(0.0, float(getattr(config, "s146_partial_fraction", 0.5)))) if ladder else 0.0
    step = max(EPS, float(getattr(config, "s146_trail_step_r", 0.5)))
    giveback = float(getattr(config, "s146_trail_giveback_r", 0.5))
    destination = float(signal["destination_target"])
    target = destination if ladder else entry + direction * float(getattr(config, "s146_tp_r", 1.25)) * risk
    target_r = direction * (target - entry) / risk
    events = list(signal.get("events") or [])
    events.append(_event("entry_filled", fill_bar.candle.timestamp, signal,
                         trade_id=signal.get("trade_id"), entry_price=entry,
                         structural_stop=structural_stop, effective_stop=stop, initial_risk=risk,
                         broker_target=destination, active_target=target,
                         fill_side="bid" if direction > 0 else "ask", spread_points=fill_bar.spread_points,
                         spread_price=fill["spread_price"], assumption="next M5 market open"))

    open_fraction = 1.0
    realized = 0.0
    partial_done = False
    active_stop = stop
    active_stop_r = -1.0
    peak_r = 0.0
    mfe_r = 0.0
    mae_r = 0.0
    highest_rung = math.floor(round(partial_at / step, 6)) * step if ladder else 0.0
    exit_price: float | None = None
    exit_epoch: int | None = None
    exit_reason = "open_at_data_end"
    bars_held = 0
    gap_detected = False
    ambiguous_bars = 0
    last_prices = fill
    previous_stamp: int | None = None

    def price_at(r_value: float) -> float:
        return entry + direction * risk * r_value

    max_hold = int(getattr(config, "s146_max_hold_bars_5m", 864))
    for index in range(fill_index, len(bars)):
        if max_hold > 0 and bars_held >= max_hold:
            break
        bar = bars[index]
        if previous_stamp is not None and bar.candle.timestamp - previous_stamp > M5_SECONDS:
            gap_detected = True
            events.append(_event("data_gap", bar.candle.timestamp, signal,
                                 previous_bar_open_utc=_iso(previous_stamp),
                                 next_bar_open_utc=_iso(bar.candle.timestamp),
                                 missing_intervals=max(0, (bar.candle.timestamp - previous_stamp) // M5_SECONDS - 1)))
        previous_stamp = bar.candle.timestamp
        prices = _prices(bar, point)
        last_prices = prices
        bars_held += 1
        adverse = prices["bid_low"] if direction > 0 else prices["ask_high"]
        favorable = prices["bid_high"] if direction > 0 else prices["ask_low"]
        favorable_r = direction * (favorable - entry) / risk
        adverse_r = direction * (adverse - entry) / risk
        mfe_r = max(mfe_r, favorable_r)
        mae_r = min(mae_r, adverse_r)
        hit_stop = adverse <= active_stop + EPS if direction > 0 else adverse >= active_stop - EPS
        hit_target = favorable >= target - EPS if direction > 0 else favorable <= target + EPS
        if hit_stop and hit_target:
            ambiguous_bars += 1
        if hit_stop:
            realized += open_fraction * active_stop_r
            exit_price, exit_epoch = active_stop, bar.candle.timestamp + M5_SECONDS
            exit_reason = "stop_loss" if active_stop_r < -EPS else ("breakeven_stop" if abs(active_stop_r) <= EPS else "trailing_stop")
            events.append(_event("final_exit", exit_epoch, signal, reason=exit_reason, exit_price=exit_price,
                                 open_fraction=open_fraction, realized_r=realized,
                                 ambiguity="stop_first", stop_first=True))
            break

        peak_r = max(peak_r, favorable_r)
        if ladder and not partial_done and fraction > 0 and peak_r + EPS >= partial_at:
            banked = open_fraction * fraction * partial_at
            realized += banked
            open_fraction *= 1.0 - fraction
            partial_done = True
            active_stop_r = max(active_stop_r, 0.0)
            active_stop = price_at(active_stop_r)
            events.append(_event("partial_banked", bar.candle.timestamp + M5_SECONDS, signal,
                                 price=price_at(partial_at), r_level=partial_at, fraction=fraction,
                                 banked_r=banked, remaining_fraction=open_fraction,
                                 assumed_volume_model="configured fraction; 0.01 lots represented as full runner"))
            events.append(_event("stop_moved", bar.candle.timestamp + M5_SECONDS, signal,
                                 from_r=-1.0, to_r=0.0, stop_price=active_stop, reason="partial_banked_be"))

        if ladder and partial_done:
            reached_rung = math.floor((peak_r + EPS) / step) * step
            next_rung = highest_rung + step
            while next_rung <= reached_rung + EPS:
                proposed_r = max(0.0, next_rung - giveback)
                if proposed_r > active_stop_r + EPS:
                    old_r = active_stop_r
                    active_stop_r = proposed_r
                    active_stop = price_at(active_stop_r)
                    events.append(_event("stop_moved", bar.candle.timestamp + M5_SECONDS, signal,
                                         from_r=old_r, to_r=active_stop_r, rung_r=next_rung,
                                         giveback_r=giveback, stop_price=active_stop,
                                         assumption="ratchet testable next M5 bar"))
                highest_rung = next_rung
                next_rung += step

        if hit_target:
            realized += open_fraction * target_r
            exit_price, exit_epoch = target, bar.candle.timestamp + M5_SECONDS
            exit_reason = "destination_hit" if ladder else "fixed_target"
            events.append(_event("destination_hit" if ladder else "final_exit", exit_epoch, signal,
                                 reason=exit_reason, exit_price=exit_price, open_fraction=open_fraction,
                                 realized_r=realized, broker_target_basis="4h_destination" if ladder else "fixed_r"))
            break

    if exit_epoch is None and max_hold > 0 and bars_held >= max_hold:
        last = bars[fill_index + bars_held - 1]
        exit_epoch = last.candle.timestamp + M5_SECONDS
        exit_price = last_prices["bid_close"] if direction > 0 else last_prices["ask_close"]
        final_r = direction * (exit_price - entry) / risk
        realized += open_fraction * final_r
        exit_reason = "time_expiry"
        events.append(_event("time_expiry", exit_epoch, signal, reason=exit_reason, exit_price=exit_price,
                             remaining_fraction=open_fraction, realized_r=realized))
    elif exit_epoch is None:
        mark = bars[-1] if bars else fill_bar
        mark_prices = _prices(mark, point)
        exit_price = mark_prices["bid_close"] if direction > 0 else mark_prices["ask_close"]
        events.append(_event("final_exit", mark.candle.timestamp + M5_SECONDS, signal,
                             reason="data_end_mark", exit_price=exit_price, status="open_at_data_end"))

    outcome = "open" if exit_epoch is None else ("win" if realized > EPS else "loss" if realized < -EPS else "breakeven")
    trade = {
        "trade_id": signal.get("trade_id") or f"trade-{signal['signal_id']}", "signal_id": signal["signal_id"],
        "strategy": "s146", "symbol": signal["symbol"], "broker_symbol": signal.get("broker_symbol"),
        "direction": signal["direction"], "side": signal["direction"], "model": signal["model"],
        "signal_time_utc": signal["signal_time_utc"], "entry_time_utc": _iso(fill_bar.candle.timestamp),
        "exit_time_utc": _iso(exit_epoch), "signal_price": signal.get("trigger"), "entry_price": entry,
        "fill_price": entry, "structural_stop": structural_stop, "effective_stop": stop, "stop_price": stop,
        "initial_risk": risk, "destination_target": destination, "active_target": target,
        "destination_reward_risk_at_fill": direction * (destination - entry) / risk,
        "exit_price": exit_price, "exit_reason": exit_reason, "status": outcome, "outcome": outcome,
        "r": realized, "realized_r": realized, "peak_r": peak_r, "mfe_r": mfe_r, "mae_r": mae_r,
        "partial_banked": partial_done, "partial_fraction": fraction if partial_done else 0.0,
        "remaining_fraction": open_fraction, "final_stop_price": active_stop, "final_stop_r": active_stop_r,
        "bars_held_5m": bars_held, "spread_at_fill_points": fill_bar.spread_points,
        "spread_at_fill_price": fill["spread_price"], "spread_pct_of_initial_risk": fill["spread_price"] / risk,
        "entry_zone_id": signal["entry_zone_id"], "entry_zone": signal["entry_zone"],
        "destination_zone_id": signal["destination_zone_id"], "destination_zone": signal["destination_zone"],
        "campaign_id": signal["campaign_id"], "campaign_started_at_utc": signal["campaign_started_at_utc"],
        "campaign_zone_count": signal["campaign_zone_count"], "running_extreme_zone_id": signal.get("running_extreme_zone_id"),
        "stop_basis": signal.get("stop_basis"), "stop_liquidity_pool": signal.get("stop_liquidity_pool"),
        "config_snapshot": signal.get("config_snapshot"), "events": events,
        "gap_detected": gap_detected, "ambiguous_bar_count": ambiguous_bars,
        "assumptions": ["native M5 bid OHLC", "long fill/exit uses bid; short fill/exit uses ask",
                        "ask = bid + spread_points * point", "stop-first same-bar ambiguity",
                        "new ratchets are testable on the next M5 bar"],
        "_entry_epoch": fill_bar.candle.timestamp, "_exit_epoch": exit_epoch,
    }
    return trade, None


__all__ = ["simulate_trade"]
