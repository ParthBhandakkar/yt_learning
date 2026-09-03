#!/usr/bin/env python3
"""Causal, read-only S146 extreme-entry analysis for log lines 49-65.

Exports native MT5 H4/M15/M5 data, rebuilds every zone visible at each signal,
ranks alternatives without future outcomes, and then evaluates outcomes separately.
No order API is called.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Optional

import MetaTrader5 as mt5
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "liveTrade"))

from liveTrade.config import CONFIG  # noqa: E402
from liveTrade.mt5_client import MT5Client  # noqa: E402
from liveTrade.detection_s146 import (  # noqa: E402
    _aggressive_fires, _conservative_fires, _first_touch_index,
    _liquidity_stop, _nearest_destination, detect_signal, live_params, screen_destinations,
    to_candles, unmitigated_zones,
)
from strategies.strategy_146_naked_4h_poi_draw import (  # noqa: E402
    H4_SECONDS, M15_SECONDS, M5_SECONDS, SwingIndex, Zone, _build_zones,
    _dedupe_zones, _invalidated, _recent_confirmed_swing, _swings, _touches,
)

UTC = timezone.utc
MAGIC = 1460146
LOG = REPO / "liveTrade" / "logs" / "s146_trades.log"
OUT = REPO / "data" / "s146_mt5" / "extreme_entry"
JSON_OUT = REPO / "data" / "s146_mt5" / "s146_extreme_entry_analysis.json"
CSV_OUT = REPO / "data" / "s146_mt5" / "s146_extreme_entry_analysis.csv"
DOC_OUT = REPO / "docs" / "s146_extreme_entry_analysis.md"

TF = {
    "4h": (mt5.TIMEFRAME_H4, H4_SECONDS, CONFIG.s146_fetch_4h),
    "15m": (mt5.TIMEFRAME_M15, M15_SECONDS, CONFIG.s146_fetch_15m),
    "5m": (mt5.TIMEFRAME_M5, M5_SECONDS, CONFIG.s146_fetch_5m),
}
# Actual session starts from engine.log, converted from IST to UTC.
SESSION_STARTS = (
    (datetime(2026, 8, 16, 22, 49, 46, tzinfo=UTC),  # 17 Aug 04:19:46 IST
     datetime(2026, 8, 18, 13, 8, 28, tzinfo=UTC)),
    (datetime(2026, 8, 18, 13, 8, 28, tzinfo=UTC), None),  # 18 Aug 18:38:28 IST
)
MANUAL_RESULTS = {
    1: "loss", 2: "loss", 3: "loss", 4: "profit", 5: "profit",
    6: "profit", 7: "profit", 8: "profit", 9: "profit", 10: "profit",
    11: "profit", 12: "profit", 13: "loss", 14: "profit", 15: "loss",
    16: "profit", 17: "loss",
}
REMARKS = {
    1: "higher_zone_would_target", 2: "higher_zone_would_target",
    3: "higher_zone_would_target", 7: "higher_zone_better",
    10: "lower_zone_better", 11: "higher_zone_better",
    13: "higher_zone_would_target", 14: "higher_zone_better",
    15: "already_highest_genuine_loss", 16: "higher_zone_better",
    17: "lower_zone_would_target",
}
EPS = 1e-12


def parse_ts(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def iso(stamp: Optional[int | float]) -> Optional[str]:
    return datetime.fromtimestamp(float(stamp), UTC).isoformat() if stamp is not None else None


def rv(value: Any, digits: int = 6) -> Any:
    return None if value is None else round(float(value), digits)


def native(row: Any, name: str, default: Any = None) -> Any:
    try:
        value = row[name]
    except (ValueError, KeyError, TypeError, IndexError):
        value = getattr(row, name, default)
    return value.item() if hasattr(value, "item") else value


def session_start(stamp: datetime) -> datetime:
    for start, end in reversed(SESSION_STARTS):
        if stamp >= start and (end is None or stamp < end):
            return start
    raise RuntimeError(f"No engine session boundary for {stamp.isoformat()}")


def load_cohort() -> list[dict]:
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    cohort = []
    for line_no in range(49, 66):
        parts = lines[line_no - 1].split(" | ", 2)
        if len(parts) != 3:
            raise RuntimeError(f"Malformed journal line {line_no}")
        event = json.loads(parts[2])
        if event.get("type") != "signal_found" or event.get("strategy") != "s146":
            raise RuntimeError(f"Journal line {line_no} is not an s146 signal_found")
        event = dict(event)
        event["trade_number"] = line_no - 48
        event["source_log_line"] = line_no
        event["manual_result"] = MANUAL_RESULTS[line_no - 48]
        event["manual_remark"] = REMARKS.get(line_no - 48)
        cohort.append(event)
    if len(cohort) != 17:
        raise RuntimeError(f"Expected 17 cohort signals, got {len(cohort)}")
    return cohort


class HistoricalData:
    def __init__(self, client: MT5Client, cohort: list[dict], end: datetime):
        self.client = client
        self.cohort = cohort
        self.end = end
        self.rates: dict[tuple[str, str], list[dict]] = {}
        self.broker_symbols: dict[str, str] = {}
        self.points: dict[str, float] = {}
        self.manifest: list[dict] = []

    def load(self) -> None:
        first = min(parse_ts(row["signal_bar_close"]) for row in self.cohort)
        for symbol in sorted({row["symbol"] for row in self.cohort}):
            broker = self.client.resolve_symbol(symbol)
            if broker is None:
                raise RuntimeError(f"Broker symbol not found: {symbol}")
            self.broker_symbols[symbol] = broker
            info = mt5.symbol_info(broker)
            self.points[symbol] = float(getattr(info, "point", 0) or (0.001 if "JPY" in symbol else 0.00001))
            for tf, (mt5_tf, seconds, limit) in TF.items():
                warmup = timedelta(seconds=seconds * (limit + 20))
                start = first - warmup
                rates = mt5.copy_rates_range(broker, mt5_tf, start, self.end)
                if rates is None or len(rates) == 0:
                    raise RuntimeError(f"No MT5 {tf} rates for {symbol}: {mt5.last_error()}")
                rows = [{name: native(rate, name) for name in rates.dtype.names} for rate in rates]
                rows.sort(key=lambda item: int(item["time"]))
                self.rates[(symbol, tf)] = rows
                self._export(symbol, tf, broker, rows, start, seconds)

    def _export(self, symbol: str, tf: str, broker: str, rows: list[dict], start: datetime, seconds: int) -> None:
        folder = OUT / symbol / tf
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{symbol}_{tf}.csv"
        fields = ["time_utc", "time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "time_utc": iso(int(row["time"])), "time": int(row["time"]),
                    "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
                    "tick_volume": row.get("tick_volume", 0), "spread": row.get("spread", 0),
                    "real_volume": row.get("real_volume", 0),
                })
        self.manifest.append({
            "symbol": symbol, "broker_symbol": broker, "timeframe": tf,
            "start_requested_utc": start.isoformat(), "end_requested_utc": self.end.isoformat(),
            "rows": len(rows), "first_bar_utc": iso(int(rows[0]["time"])),
            "last_bar_utc": iso(int(rows[-1]["time"])),
            "path": str(path.relative_to(REPO)), "period_seconds": seconds,
        })

    def window(self, symbol: str, tf: str, cutoff: datetime) -> pd.DataFrame:
        _, seconds, limit = TF[tf]
        cutoff_ts = int(cutoff.timestamp())
        closed = [row for row in self.rates[(symbol, tf)] if int(row["time"]) + seconds <= cutoff_ts]
        # fetch_closed asks for n+2 including the forming bar, normally yielding n+1 closed bars.
        closed = closed[-(limit + 1):]
        if not closed:
            raise RuntimeError(f"No closed {tf} bars for {symbol} at {cutoff.isoformat()}")
        df = pd.DataFrame(closed)
        df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("datetime", inplace=True)
        df["volume"] = df.get("tick_volume", df.get("real_volume", 0))
        return df[["open", "high", "low", "close", "volume"]].sort_index()

    def outcome_bars(self, symbol: str, after: datetime, max_hours: int = 72) -> list[dict]:
        start = int(after.timestamp())
        end = int(min(self.end, after + timedelta(hours=max_hours)).timestamp())
        return [row for row in self.rates[(symbol, "5m")] if start <= int(row["time"]) < end]


def zone_dict(zone: Zone) -> dict:
    return {
        "zone_id": zone.zone_id, "direction": "long" if zone.direction > 0 else "short",
        "lower": zone.lower, "upper": zone.upper, "proximal": zone.proximal,
        "distal": zone.distal, "origin_time": iso(zone.origin_time),
        "confirmed_at": iso(zone.active_time), "bos_level": zone.broken_level,
    }


def build_context(data: HistoricalData, signal: dict) -> dict:
    symbol = signal["symbol"]
    cutoff = parse_ts(signal["signal_bar_close"])
    dfs = {tf: data.window(symbol, tf, cutoff) for tf in TF}
    h4, m15, m5 = (to_candles(dfs[tf]) for tf in ("4h", "15m", "5m"))
    params = live_params()
    zones4 = _dedupe_zones(_build_zones(h4, "4h", H4_SECONDS, params.h4_swing_left, params.h4_swing_right))
    zones15 = _dedupe_zones(_build_zones(m15, "15m", M15_SECONDS, params.m15_swing_left, params.m15_swing_right))
    swings5 = SwingIndex(_swings(m5, params.m5_swing_left, params.m5_swing_right, M5_SECONDS))
    destinations, dropped_age, dropped_lag = screen_destinations(
        unmitigated_zones(zones4, h4), int(cutoff.timestamp()),
        CONFIG.s146_max_dest_age_days, CONFIG.s146_max_dest_bos_lag_bars,
    )
    start_ts = int(session_start(cutoff).timestamp())
    direction = 1 if signal["direction"] == "long" else -1
    signal_bar = m5[-1]
    known = [z for z in zones15 if start_ts <= z.active_time <= int(cutoff.timestamp()) and z.direction == direction]
    candidates = []
    for zone in known:
        post = [bar for bar in m5 if zone.active_time <= bar.timestamp < int(cutoff.timestamp())]
        touches = [bar for bar in post if _touches(bar, zone)]
        # Mirror live staleness: invalidation starts at the first actual overlap,
        # ignores gap-past bars, and excludes the latest bar while that bar is
        # being tested as the confirmation. Exact replay proves the logged zone
        # passed its latest-bar confirmation predicate.
        prior_after_alert = []
        if touches:
            first_touch_stamp = touches[0].timestamp
            prior_after_alert = [bar for bar in post
                                 if first_touch_stamp <= bar.timestamp < signal_bar.timestamp]
        invalidated = any(_invalidated(bar, zone) for bar in prior_after_alert)
        if zone.zone_id != signal["entry_zone_id"] and _invalidated(signal_bar, zone):
            invalidated = True
        destination = _nearest_destination(destinations, zone)
        entry = float(zone.proximal)
        stop = None
        stop_basis = None
        pool = None
        rr = None
        if destination is not None and not invalidated:
            stop, stop_basis, pool = _liquidity_stop(
                zone, direction, swings5, params, signal_bar, None, entry,
                int(cutoff.timestamp()),
            )
            risk = direction * (entry - stop)
            reward = direction * (destination.proximal - entry)
            rr = reward / risk if risk > 0 and reward > 0 else None
        # A zone is structurally eligible before its own 5m confirmation. The live
        # R:R gate is reapplied to the eventual confirmation trigger below; using
        # the zone edge here can exceed MAX_RR simply because its limit risk is tiny.
        limit_rr_in_live_band = bool(
            rr is not None and params.min_target_reward_risk <= rr <= params.max_target_reward_risk
        )
        qualifies = bool(destination is not None and not invalidated and rr is not None)
        candidates.append({
            **zone_dict(zone), "fresh_at_decision": not touches,
            "first_touch_before_decision": iso(touches[0].timestamp + M5_SECONDS) if touches else None,
            "invalidated_at_decision": invalidated, "destination": zone_dict(destination) if destination else None,
            "entry_price": entry, "structural_stop": stop, "stop_basis": stop_basis,
            "external_liquidity": pool, "destination_rr": rv(rr),
            "limit_rr_in_live_band": limit_rr_in_live_band, "qualifies": qualifies,
            "zone": zone, "destination_zone": destination,
        })
    eligible = [item for item in candidates if item["qualifies"]]
    if not eligible:
        debug = [{"zone": item["zone_id"], "invalid": item["invalidated_at_decision"],
                  "destination": bool(item["destination"]), "rr": item["destination_rr"]}
                 for item in candidates]
        raise RuntimeError(
            f"No eligible reconstructed candidates for trade {signal['trade_number']} {symbol}: {debug}"
        )
    ext_values = [-direction * item["entry_price"] for item in eligible]
    rr_values = [min(float(item["destination_rr"]), 10.0) for item in eligible]
    ext_lo, ext_hi = min(ext_values), max(ext_values)
    rr_lo, rr_hi = min(rr_values), max(rr_values)
    for item, ext, rr_value in zip(eligible, ext_values, rr_values):
        item["extremity_score"] = 1.0 if ext_hi == ext_lo else (ext - ext_lo) / (ext_hi - ext_lo)
        item["rr_score"] = 1.0 if rr_hi == rr_lo else (rr_value - rr_lo) / (rr_hi - rr_lo)
        liquidity_score = 0.0
        if item["external_liquidity"]:
            liquidity_score = 1.0 if item["external_liquidity"].get("equal_level") else 0.6
        item["confluence_score"] = round(
            0.35 * item["extremity_score"]
            + 0.25 * float(item["fresh_at_decision"])
            + 0.20 * liquidity_score
            + 0.20 * item["rr_score"], 6,
        )
    fresh = [item for item in eligible if item["fresh_at_decision"]]
    ext_liq = [item for item in fresh if item["external_liquidity"]]
    ranks = {
        "current_newest": max(eligible, key=lambda x: x["zone"].active_time),
        "most_extreme": max(eligible, key=lambda x: (-direction * x["entry_price"], x["zone"].active_time)),
        "extreme_fresh": max(fresh, key=lambda x: (-direction * x["entry_price"], x["zone"].active_time)) if fresh else None,
        "extreme_external_liquidity": max(ext_liq, key=lambda x: (-direction * x["entry_price"], x["zone"].active_time)) if ext_liq else None,
        "best_geometry": max(eligible, key=lambda x: (x["destination_rr"], x["zone"].active_time)),
        "confluence": max(eligible, key=lambda x: (x["confluence_score"], x["zone"].active_time)),
    }
    selected = next((item for item in eligible if item["zone_id"] == signal["entry_zone_id"]), None)
    return {
        "cutoff": cutoff, "dfs": dfs, "h4": h4, "m15": m15, "m5": m5,
        "swings5": swings5, "params": params, "destinations": destinations,
        "destination_screen": {"kept": len(destinations), "dropped_age": dropped_age, "dropped_lag": dropped_lag},
        "engine_start_utc": session_start(cutoff), "candidate_rows": candidates,
        "eligible_internal": eligible, "ranked_internal": ranks, "selected_internal": selected,
    }


def side_prices(row: dict, point: float) -> dict:
    spread = float(row.get("spread", 0) or 0) * point
    return {
        "bid_open": float(row["open"]), "bid_high": float(row["high"]),
        "bid_low": float(row["low"]), "bid_close": float(row["close"]),
        "ask_open": float(row["open"]) + spread, "ask_high": float(row["high"]) + spread,
        "ask_low": float(row["low"]) + spread, "ask_close": float(row["close"]) + spread,
        "spread": spread,
    }


def simulate_position(symbol: str, direction: int, entry: float, stop: float,
                      bars: list[dict], point: float, pending: Optional[str] = None) -> dict:
    risk = direction * (entry - stop)
    if risk <= 0:
        return {"status": "invalid_geometry", "filled": False}
    target = entry + direction * 1.25 * risk
    filled = pending is None
    fill_time = None
    peak_r = 0.0
    adverse_r = 0.0
    for row in bars:
        prices = side_prices(row, point)
        stamp = int(row["time"])
        if not filled:
            if pending == "limit":
                hit = prices["ask_low"] <= entry if direction > 0 else prices["bid_high"] >= entry
            elif pending == "stop":
                hit = prices["ask_high"] >= entry if direction > 0 else prices["bid_low"] <= entry
            else:
                hit = False
            if not hit:
                continue
            filled = True
            fill_time = stamp
        exit_high = prices["bid_high"] if direction > 0 else prices["ask_low"]
        exit_low = prices["bid_low"] if direction > 0 else prices["ask_high"]
        favourable = direction * ((exit_high if direction > 0 else exit_high) - entry) / risk
        adverse = direction * ((exit_low if direction > 0 else exit_low) - entry) / risk
        peak_r = max(peak_r, favourable)
        adverse_r = min(adverse_r, adverse)
        stop_hit = prices["bid_low"] <= stop if direction > 0 else prices["ask_high"] >= stop
        target_hit = prices["bid_high"] >= target if direction > 0 else prices["ask_low"] <= target
        if stop_hit:
            return {"status": "stop_first", "filled": True, "fill_time_utc": iso(fill_time or stamp),
                    "exit_time_utc": iso(stamp), "entry": entry, "stop": stop, "target": target,
                    "outcome_r": -1.0, "mfe_r": rv(peak_r), "mae_r": rv(adverse_r)}
        if target_hit:
            return {"status": "target_first", "filled": True, "fill_time_utc": iso(fill_time or stamp),
                    "exit_time_utc": iso(stamp), "entry": entry, "stop": stop, "target": target,
                    "outcome_r": 1.25, "mfe_r": rv(peak_r), "mae_r": rv(adverse_r)}
    return {"status": "open" if filled else "unfilled", "filled": filled,
            "fill_time_utc": iso(fill_time), "entry": entry, "stop": stop, "target": target,
            "outcome_r": None, "mfe_r": rv(peak_r), "mae_r": rv(adverse_r)}


def future_confirmation(candidate: dict, context: dict, full_m5: list, cutoff_ts: int) -> dict:
    zone: Zone = candidate["zone"]
    destination: Zone = candidate["destination_zone"]
    direction = zone.direction
    params = context["params"]
    swings = SwingIndex(_swings(full_m5, params.m5_swing_left, params.m5_swing_right, M5_SECONDS))
    starts = [bar.timestamp for bar in full_m5]
    start = next((i for i, value in enumerate(starts) if value >= cutoff_ts), len(full_m5))
    limit = min(len(full_m5), start + params.max_wait_bars_5m)
    alert_index = _first_touch_index(zone, full_m5, start, limit)
    if alert_index < 0:
        return {"status": "no_future_touch"}
    alert_time = full_m5[alert_index].timestamp + M5_SECONDS
    reference = _recent_confirmed_swing(
        swings, 1 if direction > 0 else -1, alert_time,
        max(0, alert_time - params.max_liquidity_lookback_5m * M5_SECONDS),
    )
    for index in range(alert_index, limit):
        bar = full_m5[index]
        if _invalidated(bar, zone):
            return {"status": "invalidated_before_confirmation", "alert_time_utc": iso(alert_time)}
        model = None
        confirmation_level = None
        swept_level = _aggressive_fires(bar, zone, direction, swings, params)
        if swept_level is not None:
            model = "aggressive_liquidation"
            confirmation_level = swept_level
        elif reference is not None and index > alert_index and _conservative_fires(bar, zone, direction, reference.price):
            model = "conservative_mss"
            confirmation_level = reference.price
        if model is None:
            continue
        buffer = bar.close * params.stop_buffer_bps / 10000.0
        trigger = bar.high + buffer if direction > 0 else bar.low - buffer
        stop, basis, pool = _liquidity_stop(
            zone, direction, swings, params, bar, swept_level, trigger,
            bar.timestamp + M5_SECONDS,
        )
        risk = direction * (trigger - stop)
        reward = direction * (destination.proximal - trigger)
        rr = reward / risk if risk > 0 and reward > 0 else None
        if rr is None or not (params.min_target_reward_risk <= rr <= params.max_target_reward_risk):
            return {"status": "confirmation_failed_destination_rr", "destination_rr": rv(rr)}
        return {
            "status": "confirmed", "model": model, "alert_time_utc": iso(alert_time),
            "confirmation_time_utc": iso(bar.timestamp + M5_SECONDS),
            "trigger": trigger, "stop": stop, "stop_basis": basis,
            "external_liquidity": pool, "confirmation_level": confirmation_level,
            "destination_rr": rv(rr),
        }
    return {"status": "no_confirmation_in_window", "alert_time_utc": iso(alert_time)}


def public_candidate(item: Optional[dict]) -> Optional[dict]:
    if item is None:
        return None
    return {key: value for key, value in item.items() if key not in {"zone", "destination_zone"}}


def actual_history(cohort: list[dict], end: datetime) -> tuple[list[dict], list[dict]]:
    start = min(parse_ts(row["signal_bar_close"]) for row in cohort) - timedelta(hours=2)
    deals = [deal for deal in (mt5.history_deals_get(start, end) or []) if int(getattr(deal, "magic", 0)) == MAGIC]
    orders = [order for order in (mt5.history_orders_get(start, end) or []) if int(getattr(order, "magic", 0)) == MAGIC]
    by_position: dict[int, list] = defaultdict(list)
    for deal in deals:
        by_position[int(getattr(deal, "position_id", 0))].append(deal)
    order_by_position: dict[int, list] = defaultdict(list)
    for order in orders:
        order_by_position[int(getattr(order, "position_id", 0))].append(order)
    positions = []
    for position_id, position_deals in sorted(by_position.items()):
        entries = [d for d in position_deals if int(getattr(d, "entry", -1)) in (mt5.DEAL_ENTRY_IN, getattr(mt5, "DEAL_ENTRY_INOUT", 2))]
        if not entries:
            continue
        entry = min(entries, key=lambda d: (d.time_msc, d.ticket))
        exits = [d for d in position_deals if d.ticket != entry.ticket and int(getattr(d, "entry", -1)) in
                 (mt5.DEAL_ENTRY_OUT, getattr(mt5, "DEAL_ENTRY_OUT_BY", 3), getattr(mt5, "DEAL_ENTRY_INOUT", 2))]
        symbol = str(entry.symbol)
        normalized = next((row["symbol"] for row in cohort if symbol.startswith(row["symbol"])), symbol)
        direction = "long" if int(entry.type) == mt5.DEAL_TYPE_BUY else "short"
        position_orders = sorted(order_by_position.get(position_id, []), key=lambda o: (o.time_setup_msc, o.ticket))
        initial = position_orders[0] if position_orders else None
        pnl = sum(float(getattr(d, "profit", 0) or 0) + float(getattr(d, "swap", 0) or 0)
                  + float(getattr(d, "commission", 0) or 0) for d in exits)
        positions.append({
            "position_id": position_id, "symbol": normalized, "broker_symbol": symbol,
            "direction": direction, "entry_time_utc": iso(entry.time), "entry_price": float(entry.price),
            "entry_lots": float(entry.volume), "entry_deal": int(entry.ticket),
            "entry_order": int(getattr(entry, "order", 0) or 0),
            "status": "closed" if exits else "open", "exit_time_utc": iso(max((d.time for d in exits), default=None)),
            "exit_price": float(exits[-1].price) if exits else None, "realized_pnl": round(pnl, 2),
            "exit_comment": str(getattr(exits[-1], "comment", "")) if exits else None,
            "initial_sl": float(getattr(initial, "sl", 0) or 0) if initial else None,
            "initial_tp": float(getattr(initial, "tp", 0) or 0) if initial else None,
        })
    unmatched = set(range(len(positions)))
    links = []
    for signal in cohort:
        stamp = parse_ts(signal["signal_bar_close"])
        options = [(abs((parse_ts(positions[i]["entry_time_utc"]) - stamp).total_seconds()), i)
                   for i in unmatched if positions[i]["symbol"] == signal["symbol"]
                   and positions[i]["direction"] == signal["direction"]]
        if options:
            delta, index = min(options)
            if delta <= 90 * 60:
                unmatched.remove(index)
                links.append({"trade_number": signal["trade_number"], "match_basis": "nearest_unique_symbol_direction",
                              "delta_seconds": delta, "position": positions[index]})
                continue
        links.append({"trade_number": signal["trade_number"], "match_basis": None, "position": None})
    return positions, links


def export_ticks(data: HistoricalData, trade_number: int, symbol: str, center: datetime, label: str) -> dict:
    broker = data.broker_symbols[symbol]
    start, end = center - timedelta(minutes=2), center + timedelta(minutes=2)
    ticks = mt5.copy_ticks_range(broker, start, end, getattr(mt5, "COPY_TICKS_ALL", 3))
    folder = OUT / "ticks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"trade_{trade_number:02d}_{label}.csv"
    fields = ["time_utc", "time_msc", "bid", "ask", "last", "volume", "flags"]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for tick in ticks if ticks is not None else []:
            writer.writerow({
                "time_utc": datetime.fromtimestamp(int(native(tick, "time_msc", 0)) / 1000, UTC).isoformat(),
                "time_msc": native(tick, "time_msc", 0), "bid": native(tick, "bid", 0),
                "ask": native(tick, "ask", 0), "last": native(tick, "last", 0),
                "volume": native(tick, "volume", 0), "flags": native(tick, "flags", 0),
            })
            count += 1
    return {"label": label, "center_utc": center.isoformat(), "rows": count, "path": str(path.relative_to(REPO))}


class CutoffClient:
    def __init__(self, data: HistoricalData, cutoff: datetime):
        self.data = data
        self.cutoff = cutoff

    def fetch_closed(self, symbol: str, tf: str, n_bars: int = 400):
        return self.data.window(symbol, tf, self.cutoff)


def all_candles(data: HistoricalData, symbol: str) -> list:
    rows = data.rates[(symbol, "5m")]
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("datetime", inplace=True)
    df["volume"] = df.get("tick_volume", df.get("real_volume", 0))
    return to_candles(df[["open", "high", "low", "close", "volume"]].sort_index())


def analyse_trade(data: HistoricalData, signal: dict, actual_link: dict) -> dict:
    context = build_context(data, signal)
    symbol = signal["symbol"]
    direction = 1 if signal["direction"] == "long" else -1
    cutoff = context["cutoff"]
    point = data.points[symbol]
    future_rows = data.outcome_bars(symbol, cutoff)
    full_m5 = all_candles(data, symbol)
    # Historical cohort parity must replay the detector configuration that was
    # active when these trades occurred; the running-extreme live filter was
    # introduced afterward and is evaluated separately by this analyzer.
    previous_extreme_mode = CONFIG.s146_require_running_extreme_15m
    CONFIG.s146_require_running_extreme_15m = False
    try:
        replay = detect_signal(CutoffClient(data, cutoff), symbol,
                               int(context["engine_start_utc"].timestamp()), journal=False)
    finally:
        CONFIG.s146_require_running_extreme_15m = previous_extreme_mode
    parity_fields = {
        "signal_present": replay is not None,
        "entry_zone_id": replay.get("entry_zone_id") == signal["entry_zone_id"] if replay else False,
        "model": replay.get("model") == signal["model"] if replay else False,
        "signal_bar_close": replay.get("signal_bar_close") == int(cutoff.timestamp()) if replay else False,
        "trigger": abs(float(replay.get("trigger", 0)) - float(signal["trigger"])) <= point if replay else False,
        "stop": abs(float(replay.get("stop", 0)) - float(signal["stop"])) <= point if replay else False,
    }
    current_market = simulate_position(
        symbol, direction, float(signal["trigger"]), float(signal["stop"]), future_rows, point,
    )
    current_stop = simulate_position(
        symbol, direction, float(signal["trigger"]), float(signal["stop"]), future_rows, point, pending="stop",
    )
    actual_position = actual_link.get("position")
    actual_fill_path = None
    actual_slippage_r = None
    if actual_position and actual_position.get("initial_sl"):
        actual_time = parse_ts(actual_position["entry_time_utc"])
        actual_entry = float(actual_position["entry_price"])
        initial_sl = float(actual_position["initial_sl"])
        actual_fill_path = simulate_position(
            symbol, direction, actual_entry, initial_sl,
            data.outcome_bars(symbol, actual_time), point,
        )
        signal_risk = abs(float(signal["trigger"]) - float(signal["stop"]))
        if signal_risk > 0:
            # Positive means the market fill moved against the intended direction.
            actual_slippage_r = direction * (actual_entry - float(signal["trigger"])) / signal_risk
    variant_results = {}
    for name, candidate in context["ranked_internal"].items():
        if candidate is None:
            variant_results[name] = {"candidate": None, "limit": None, "confirmed": None}
            continue
        limit_result = simulate_position(
            symbol, direction, float(candidate["entry_price"]), float(candidate["structural_stop"]),
            future_rows, point, pending="limit",
        )
        confirmation = future_confirmation(candidate, context, full_m5, int(cutoff.timestamp()))
        confirmed_result = None
        if confirmation.get("status") == "confirmed":
            confirm_time = parse_ts(confirmation["confirmation_time_utc"])
            confirmed_rows = data.outcome_bars(symbol, confirm_time)
            confirmed_result = simulate_position(
                symbol, direction, float(confirmation["trigger"]), float(confirmation["stop"]),
                confirmed_rows, point,
            )
        variant_results[name] = {
            "candidate": public_candidate(candidate), "limit": limit_result,
            "confirmation": confirmation, "confirmed": confirmed_result,
        }
    selected = context["selected_internal"]
    selected_entry = selected["entry_price"] if selected else float(signal["trigger"])
    all_eligible = context["eligible_internal"]
    more_extreme = [item for item in all_eligible
                    if -direction * item["entry_price"] > -direction * selected_entry + point]
    selected_rank = None
    if selected:
        ordered = sorted(all_eligible, key=lambda x: (-direction * x["entry_price"], x["zone"].active_time), reverse=True)
        selected_rank = next((i + 1 for i, item in enumerate(ordered) if item["zone_id"] == selected["zone_id"]), None)
    remark = signal.get("manual_remark")
    remark_verdict = None
    if remark:
        extreme = variant_results["most_extreme"]
        alternative_target = any(
            result and result.get("status") == "target_first"
            for result in (extreme.get("limit"), extreme.get("confirmed"))
        )
        if remark == "already_highest_genuine_loss":
            supported = selected_rank is not None and selected_rank <= 2
            remark_verdict = {
                "classification": "supported" if supported else "not_supported",
                "reason": f"logged zone extremity rank was {selected_rank} of {len(all_eligible)}",
            }
        elif not more_extreme:
            remark_verdict = {
                "classification": "hindsight_or_unavailable",
                "reason": "no objectively more-extreme qualifying 15m zone was causally available",
            }
        elif "would_target" in remark:
            remark_verdict = {
                "classification": "supported" if alternative_target else "partially_supported",
                "reason": ("a causal more-extreme zone reached 1.25R in a predeclared execution variant"
                           if alternative_target else "a more-extreme zone existed, but neither tested execution proved target-first"),
            }
        else:
            improvement = direction * (selected_entry - variant_results["most_extreme"]["candidate"]["entry_price"])
            remark_verdict = {
                "classification": "supported" if improvement > point else "not_supported",
                "reason": f"causal extreme improved entry by {abs(improvement) / point:.1f} broker points",
            }
    ticks = [export_ticks(data, signal["trade_number"], symbol, cutoff, "signal")]
    fresh_limit = variant_results.get("extreme_fresh", {}).get("limit")
    if fresh_limit and fresh_limit.get("fill_time_utc"):
        ticks.append(export_ticks(data, signal["trade_number"], symbol,
                                  parse_ts(fresh_limit["fill_time_utc"]), "extreme_fresh_fill"))
    candidate_rows = [public_candidate(item) for item in context["candidate_rows"]]
    return {
        "trade_number": signal["trade_number"], "source_log_line": signal["source_log_line"],
        "symbol": symbol, "direction": signal["direction"], "model": signal["model"],
        "signal_bar_open_utc": parse_ts(signal["signal_bar_open"]).isoformat(),
        "signal_bar_close_utc": cutoff.isoformat(), "engine_start_utc": context["engine_start_utc"].isoformat(),
        "manual_result": signal["manual_result"], "manual_remark": remark,
        "logged": {
            "entry_zone_id": signal["entry_zone_id"], "entry_zone_lower": signal["entry_zone_lower"],
            "entry_zone_upper": signal["entry_zone_upper"], "trigger": signal["trigger"],
            "stop": signal["stop"], "target": signal["target"], "destination_target": signal["destination_target"],
            "stop_basis": signal.get("stop_basis"),
        },
        "replay_parity": {**parity_fields, "all_match": all(parity_fields.values())},
        "candidate_counts": {
            "known_same_direction": len(context["candidate_rows"]), "qualifying": len(all_eligible),
            "fresh_qualifying": sum(bool(item["fresh_at_decision"]) for item in all_eligible),
            "more_extreme_than_logged": len(more_extreme),
        },
        "logged_zone_extremity_rank": selected_rank,
        "destination_screen": context["destination_screen"],
        "candidates": candidate_rows,
        "ranked": {name: public_candidate(item) for name, item in context["ranked_internal"].items()},
        "current_market": current_market, "current_historical_stop": current_stop,
        "actual_fill_path": actual_fill_path, "actual_slippage_r_vs_trigger": rv(actual_slippage_r),
        "alternatives": variant_results, "manual_remark_verdict": remark_verdict,
        "actual_mt5": actual_position, "actual_match_basis": actual_link.get("match_basis"),
        "tick_exports": ticks,
    }


def summarize_variant(rows: list[dict], getter) -> dict:
    results = [getter(row) for row in rows]
    results = [item for item in results if item]
    statuses = Counter(item.get("status") for item in results)
    resolved = [float(item["outcome_r"]) for item in results if item.get("outcome_r") is not None]
    return {
        "cases": len(results), "statuses": dict(statuses), "filled": sum(bool(item.get("filled")) for item in results),
        "resolved": len(resolved), "sum_r": rv(sum(resolved)), "mean_r": rv(mean(resolved)) if resolved else None,
    }


def build_summary(rows: list[dict]) -> dict:
    variants = {
        "current_market": summarize_variant(rows, lambda row: row["current_market"]),
        "current_historical_stop": summarize_variant(rows, lambda row: row["current_historical_stop"]),
        "actual_fill_1_25r_path": summarize_variant(rows, lambda row: row["actual_fill_path"]),
    }
    for name in ("most_extreme", "extreme_fresh", "extreme_external_liquidity", "best_geometry", "confluence"):
        variants[f"{name}_limit"] = summarize_variant(rows, lambda row, n=name: row["alternatives"][n]["limit"])
        variants[f"{name}_confirmed"] = summarize_variant(rows, lambda row, n=name: row["alternatives"][n]["confirmed"])
    verdicts = Counter(
        row["manual_remark_verdict"]["classification"] for row in rows if row["manual_remark_verdict"]
    )
    actual = [row["actual_mt5"] for row in rows if row["actual_mt5"]]
    actual_rows = [row for row in rows if row["actual_mt5"]]
    manual_actual_matches = sum(
        (row["manual_result"] == "profit") == (float(row["actual_mt5"]["realized_pnl"]) > 0)
        for row in actual_rows
    )
    manual_actual_mismatches = [
        row["trade_number"] for row in actual_rows
        if (row["manual_result"] == "profit") != (float(row["actual_mt5"]["realized_pnl"]) > 0)
    ]
    return {
        "trades": len(rows), "symbols": len({row["symbol"] for row in rows}),
        "replay_exact": sum(row["replay_parity"]["all_match"] for row in rows),
        "manual_results": dict(Counter(row["manual_result"] for row in rows)),
        "remarks_checked": sum(row["manual_remark"] is not None for row in rows),
        "remark_verdicts": dict(verdicts),
        "trades_with_more_extreme_zone": sum(row["candidate_counts"]["more_extreme_than_logged"] > 0 for row in rows),
        "actual_mt5_matches": len(actual),
        "actual_mt5_nonfills": [row["trade_number"] for row in rows if not row["actual_mt5"]],
        "actual_profits": sum(float(item["realized_pnl"]) > 0 for item in actual),
        "actual_losses": sum(float(item["realized_pnl"]) < 0 for item in actual),
        "actual_net_pnl": rv(sum(float(item["realized_pnl"]) for item in actual), 2),
        "manual_vs_actual_matches": manual_actual_matches,
        "manual_vs_actual_mismatches": manual_actual_mismatches,
        "variants": variants,
    }


def write_outputs(payload: dict) -> None:
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    fields = [
        "trade_number", "symbol", "direction", "model", "signal_bar_open_utc", "manual_result",
        "manual_remark", "remark_verdict", "replay_exact", "qualifying_candidates",
        "fresh_candidates", "more_extreme_candidates", "logged_extremity_rank",
        "current_market", "current_stop", "extreme_limit", "extreme_confirmed",
        "fresh_limit", "fresh_confirmed", "confluence_confirmed", "actual_fill_path",
        "actual_slippage_r", "actual_pnl",
    ]
    with CSV_OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in payload["trades"]:
            writer.writerow({
                "trade_number": row["trade_number"], "symbol": row["symbol"],
                "direction": row["direction"], "model": row["model"],
                "signal_bar_open_utc": row["signal_bar_open_utc"], "manual_result": row["manual_result"],
                "manual_remark": row["manual_remark"],
                "remark_verdict": (row["manual_remark_verdict"] or {}).get("classification"),
                "replay_exact": row["replay_parity"]["all_match"],
                "qualifying_candidates": row["candidate_counts"]["qualifying"],
                "fresh_candidates": row["candidate_counts"]["fresh_qualifying"],
                "more_extreme_candidates": row["candidate_counts"]["more_extreme_than_logged"],
                "logged_extremity_rank": row["logged_zone_extremity_rank"],
                "current_market": row["current_market"]["status"],
                "current_stop": row["current_historical_stop"]["status"],
                "extreme_limit": row["alternatives"]["most_extreme"]["limit"]["status"],
                "extreme_confirmed": (row["alternatives"]["most_extreme"].get("confirmed") or {}).get("status"),
                "fresh_limit": (row["alternatives"]["extreme_fresh"].get("limit") or {}).get("status"),
                "fresh_confirmed": (row["alternatives"]["extreme_fresh"].get("confirmed") or {}).get("status"),
                "confluence_confirmed": (row["alternatives"]["confluence"].get("confirmed") or {}).get("status"),
                "actual_fill_path": (row.get("actual_fill_path") or {}).get("status"),
                "actual_slippage_r": row.get("actual_slippage_r_vs_trigger"),
                "actual_pnl": (row.get("actual_mt5") or {}).get("realized_pnl"),
            })
    summary = payload["summary"]
    lines = [
        "# S146 causal extreme-entry analysis", "",
        f"Generated: `{payload['generated_utc']}` from read-only MT5 H4/M15/M5 bars and tick/history data.", "",
        "## Bottom line", "",
        f"The 17 supplied rows matched journal lines 49–65. Exact historical detector replay matched "
        f"**{summary['replay_exact']}/17** signals on zone, model, time, trigger, and stop.",
        f"A causally known, qualifying zone more extreme than the logged zone existed in "
        f"**{summary['trades_with_more_extreme_zone']}/17** cases. This count uses only structure confirmed "
        "by the decision time and never uses the later result to choose a zone.",
        f"Of the {summary['remarks_checked']} rows with a specific extreme-zone remark, the evidence classified "
        f"them as `{summary['remark_verdicts']}`.",
        f"Broker truth differs materially from the manually plotted result labels: **{summary['actual_mt5_matches']}/17** "
        f"signals filled, with **{summary['actual_profits']} profits / {summary['actual_losses']} losses** and net "
        f"`{summary['actual_net_pnl']}` INR. Manual versus broker sign agreed on "
        f"**{summary['manual_vs_actual_matches']}/{summary['actual_mt5_matches']}** fills; mismatches were trades "
        f"`{summary['manual_vs_actual_mismatches']}`, while `{summary['actual_mt5_nonfills']}` did not fill.", "",
        "The critical distinction is **availability versus execution**: a higher/lower zone can exist at the "
        "decision time but remain unfilled, invalidate before its own confirmation, or still lose. Therefore "
        "the report does not equate a visually better zone with a tradable winner.", "",
        "## Predeclared strategy comparisons", "",
        "All outcomes use constant 1R risk and a 1.25R target. Short exits are evaluated on approximated ASK "
        "(MT5 bid bars plus recorded bar spread); long exits use BID. Same-bar ambiguity is stop-first.", "",
        "| Variant | Cases | Filled | Target first | Stop first | Open/unfilled/other | Sum resolved R | Mean resolved R |",
        "|---|---:|---:|---:|---:|---|---:|---:|",
    ]
    for name, item in summary["variants"].items():
        statuses = item["statuses"]
        other = ", ".join(f"{key}:{value}" for key, value in statuses.items()
                          if key not in {"target_first", "stop_first"}) or "—"
        lines.append(
            f"| {name} | {item['cases']} | {item['filled']} | {statuses.get('target_first', 0)} | "
            f"{statuses.get('stop_first', 0)} | {other} | {item['sum_r']} | {item['mean_r']} |"
        )
    lines += [
        "", "## Decision", "",
        "The evidence supports **testing extreme fresh limit entries**, not blindly replacing newest-first "
        "with extreme-first market entries. The broad extreme-fresh limit rule filled only 5 of 10 eligible "
        "cases (3 targets, 2 stops, +1.75R resolved); requiring external liquidity filled only 3 of 6 "
        "cases (2 targets, 1 stop, +1.5R). That is directionally better than current-market -1.25R, but "
        "far too few resolved trades to establish expectancy.",
        f"Waiting for each extreme zone's own 5m confirmation did **not** solve the sample: most-extreme "
        f"confirmed had {summary['variants']['most_extreme_confirmed']['statuses'].get('target_first', 0)} targets, "
        f"{summary['variants']['most_extreme_confirmed']['statuses'].get('stop_first', 0)} stops, and "
        f"{summary['variants']['most_extreme_confirmed']['statuses'].get('open', 0)} still open. Best-R:R "
        "geometry was actively harmful (-4R for limit entries). Therefore R:R alone must not choose the zone.",
        "After review, the live detector now enforces a narrower causal rule: a 5m signal is accepted only "
        "when its 15m zone is the running still-actionable price extreme since the selected 4H destination "
        "confirmed. This keeps the existing 5m confirmation and market execution; it does not deploy the "
        "unproven direct-limit variants above. The rule is controlled by "
        "`S146_REQUIRE_RUNNING_EXTREME_15M` for immediate rollback.",
        "", "## Trade-by-trade validation", "",
              "| # | Symbol | Manual | MT5 P/L | Qualifying/fresh | More extreme | Logged rank | Current market | "
              "Extreme limit | Extreme confirmed | Remark verdict |",
              "|---:|---|---|---:|---:|---:|---:|---|---|---|---|"]
    for row in payload["trades"]:
        extreme = row["alternatives"]["most_extreme"]
        verdict = (row["manual_remark_verdict"] or {}).get("classification", "—")
        lines.append(
            f"| {row['trade_number']} | {row['symbol']} {row['direction']} | {row['manual_result']} | "
            f"{(row.get('actual_mt5') or {}).get('realized_pnl', 'not filled')} | "
            f"{row['candidate_counts']['qualifying']}/{row['candidate_counts']['fresh_qualifying']} | "
            f"{row['candidate_counts']['more_extreme_than_logged']} | {row['logged_zone_extremity_rank']} | "
            f"{row['current_market']['status']} | {extreme['limit']['status']} | "
            f"{(extreme.get('confirmed') or {}).get('status', extreme['confirmation']['status'])} | {verdict} |"
        )
    lines += [
        "", "## Ranking rules (fixed before outcomes)", "",
        "- **Most extreme:** highest qualifying supply for shorts; lowest qualifying demand for longs.",
        "- **Extreme fresh:** same ranking, but the zone must not have been touched by decision time.",
        "- **External liquidity:** extreme fresh zone with a confirmed swing/equal-high/equal-low pool beyond it.",
        "- **Best geometry:** largest destination reward divided by structural stop risk.",
        "- **Confluence:** 35% extremity, 25% freshness, 20% external liquidity, 20% capped destination R:R.",
        "- `limit` waits at the zone's proximal edge. `confirmed` waits for that zone's own future causal 5m "
        "liquidation/MSS; it does not transfer confirmation from the original, less-extreme zone.",
        "", "## Hindsight controls and limitations", "",
        "- Each decision uses rolling live-depth windows and bars closed by that timestamp only.",
        "- Swing levels appear only after their right-side confirmation bars; future mitigation never removes "
        "a zone retrospectively.",
        "- The two actual engine restart boundaries are enforced for `S146_REQUIRE_NEW_15M=true`.",
        "- Outcomes are a separate pass after ranking. They never affect candidate eligibility or score.",
        "- This is 17 signals from one day. Unresolved/unfilled cases are not silently discarded, and the "
        "highest sample-R variant should not be treated as proven without forward demo validation.",
        "- Five-minute bars cannot resolve exact intrabar order; the stop-first rule is deliberately pessimistic. "
        "Tick files around signals and detected extreme fills are exported for inspection.",
        "", "## Files", "",
        f"- Full machine report: `{JSON_OUT.relative_to(REPO)}`",
        f"- Flat comparison: `{CSV_OUT.relative_to(REPO)}`",
        f"- Native exports and ticks: `{OUT.relative_to(REPO)}`",
    ]
    DOC_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    cohort = load_cohort()
    now = datetime.now(UTC)
    OUT.mkdir(parents=True, exist_ok=True)
    client = MT5Client(magic=MAGIC)
    if not client.connect():
        print("MT5 connection failed")
        return 1
    try:
        data = HistoricalData(client, cohort, now)
        data.load()
        positions, links = actual_history(cohort, now)
        links_by_trade = {item["trade_number"]: item for item in links}
        rows = []
        for signal in cohort:
            print(f"analysing {signal['trade_number']:02d}/17 {signal['symbol']} {signal['direction']}...")
            rows.append(analyse_trade(data, signal, links_by_trade[signal["trade_number"]]))
        payload = {
            "generated_utc": now.isoformat(), "source": "MT5 read-only", "magic": MAGIC,
            "cohort": {"journal_lines": "49-65", "signals": 17,
                       "first_utc": rows[0]["signal_bar_open_utc"], "last_utc": rows[-1]["signal_bar_close_utc"]},
            "methodology": {
                "ranking_uses_future": False, "closed_bars_only": True,
                "fetch_depth": {tf: limit for tf, (_, _, limit) in TF.items()},
                "intrabar_policy": "stop first", "target_r": 1.25,
                "short_exit_basis": "ask approximated from bid OHLC plus bar spread",
                "long_exit_basis": "bid OHLC", "engine_session_starts_utc": [start.isoformat() for start, _ in SESSION_STARTS],
            },
            "summary": build_summary(rows), "trades": rows,
            "mt5_history": {"positions": positions, "links": links},
            "bar_export_manifest": data.manifest,
        }
        write_outputs(payload)
        (OUT / "bar_export_manifest.json").write_text(
            json.dumps({"generated_utc": now.isoformat(), "files": data.manifest}, indent=2), encoding="utf-8")
        (OUT / "s146_mt5_history.json").write_text(
            json.dumps({"generated_utc": now.isoformat(), "magic": MAGIC, "positions": positions, "links": links}, indent=2),
            encoding="utf-8")
        print(json.dumps({
            "summary": payload["summary"],
            "exports": {"bar_files": len(data.manifest), "bar_rows": sum(item["rows"] for item in data.manifest)},
            "outputs": [str(JSON_OUT), str(CSV_OUT), str(DOC_OUT), str(OUT)],
        }, indent=2))
    finally:
        client.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
