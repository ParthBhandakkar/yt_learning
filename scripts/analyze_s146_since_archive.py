#!/usr/bin/env python3
"""Read-only deep analysis of the S146 archive since 19 Aug 2026 IST.

Reconstructs every trade's price path from archived native 5m bars, rebuilds the
4H/15m/5m structural context from the S146 journals, verifies the live exit
ladder, and scores counterfactual exit policies. No MT5 orders and no writes
outside the archive's analysis folder.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
for item in (str(REPO), str(REPO / "liveTrade")):
    if item not in sys.path:
        sys.path.insert(0, item)

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
ARCHIVE = REPO / "data" / "s146_mt5" / "archive_since_2026-08-19_IST"
OUT_DIR = ARCHIVE / "analysis"

PARTIAL_AT_R = 1.25
PARTIAL_FRACTION = 0.5
TRAIL_STEP_R = 0.5
TRAIL_GIVEBACK_R = 0.5
HOLD_LIMIT_BARS = 864


def parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, "", "None"):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp).astimezone(UTC)


def num(value: Any) -> Optional[float]:
    if value in (None, "", "None"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def rnd(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


def journal_records(path: Path) -> list[dict]:
    """Parse a S146 journal file of '<ist> | <type> | <json>' lines."""
    records: list[dict] = []
    if not path.exists():
        return records
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            start = line.find("{")
            if start < 0:
                continue
            try:
                payload = json.loads(line[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
    return records


class Bars:
    """Archived native bars for one symbol/timeframe."""

    __slots__ = ("time", "open", "high", "low", "close", "spread", "point")

    def __init__(self, path: Path, point: float):
        self.point = point or 1e-5
        self.time: list[int] = []
        self.open: list[float] = []
        self.high: list[float] = []
        self.low: list[float] = []
        self.close: list[float] = []
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
                self.spread.append(float(row["spread"] or 0) * self.point)

    def __len__(self) -> int:
        return len(self.time)

    def index_at_or_after(self, stamp: int) -> Optional[int]:
        index = bisect_left(self.time, stamp)
        return index if index < len(self.time) else None

    def index_containing(self, stamp: int, seconds: int = 300) -> Optional[int]:
        """Index of the bar whose window contains `stamp`, else the next bar.

        Trades that open and close inside one 5m bar otherwise yield no path at
        all, so the containing bar is used and flagged by the caller.
        """
        index = bisect_left(self.time, stamp)
        if index < len(self.time) and self.time[index] == stamp:
            return index
        previous = index - 1
        if previous >= 0 and self.time[previous] <= stamp < self.time[previous] + seconds:
            return previous
        return index if index < len(self.time) else None


def load_bars(manifest: dict) -> tuple[dict[str, Bars], dict[str, Bars], dict[str, float]]:
    five, fifteen, points = {}, {}, {}
    for symbol, meta in manifest["symbols"].items():
        point = float(meta.get("point") or 1e-5)
        points[symbol] = point
        five[symbol] = Bars(ARCHIVE / "bars" / meta["timeframes"]["5m"]["path"], point)
        fifteen[symbol] = Bars(ARCHIVE / "bars" / meta["timeframes"]["15m"]["path"], point)
    return five, fifteen, points


def side_prices(bars: Bars, index: int, is_long: bool) -> tuple[float, float]:
    """(favourable_extreme, adverse_extreme) on the side that closes the trade.

    Bars are bid. A long exits on the bid, a short exits on the ask, so short
    excursions are shifted by that bar's spread.
    """
    if is_long:
        return bars.high[index], bars.low[index]
    spread = bars.spread[index]
    return bars.low[index] + spread, bars.high[index] + spread


def ladder_stop_r(peak_r: float, current: Optional[float]) -> Optional[float]:
    """Exact mirror of TradeManagerS146._ladder_stop_r."""
    wanted = current
    if peak_r >= PARTIAL_AT_R:
        wanted = max(wanted, 0.0) if wanted is not None else 0.0
    if TRAIL_STEP_R > 0:
        rung = math.floor(round(peak_r / TRAIL_STEP_R, 6)) * TRAIL_STEP_R
        if rung > PARTIAL_AT_R:
            trailed = round(rung - TRAIL_GIVEBACK_R, 6)
            wanted = trailed if wanted is None else max(wanted, trailed)
    return wanted


def realized_r_from_deals(entry_record: dict, is_long: bool, entry_price: float,
                          risk: float) -> dict:
    """Volume-weighted R straight from broker fill prices.

    Independent of the engine's `loss_at_sl` money estimate, so partial closes
    and pip-value approximations cannot distort the result.
    """
    deals = entry_record.get("broker_deals") or []
    entered = [deal for deal in deals if deal.get("entry") == 0]
    exited = [deal for deal in deals if deal.get("entry") == 1]
    entry_volume = sum(float(deal.get("volume") or 0) for deal in entered)
    if not exited or entry_volume <= 0 or risk <= 0:
        return {"realized_r_price": None, "closed_fraction": 0.0, "exit_legs": len(exited)}
    total = 0.0
    closed = 0.0
    for deal in exited:
        volume = float(deal.get("volume") or 0)
        price = float(deal.get("price") or 0)
        if volume <= 0 or price <= 0:
            continue
        leg_r = ((price - entry_price) if is_long else (entry_price - price)) / risk
        total += (volume / entry_volume) * leg_r
        closed += volume / entry_volume
    return {"realized_r_price": total, "closed_fraction": closed, "exit_legs": len(exited)}


def walk_path(bars: Bars, start_index: int, is_long: bool, entry: float, risk: float,
              stop_at: Optional[datetime]) -> dict:
    """Excursion profile of the real trade window (entry -> actual exit)."""
    limit = int(stop_at.timestamp()) if stop_at else None
    peak_r = -math.inf
    trough_r = math.inf
    peak_index = start_index
    bars_used = 0
    for index in range(start_index, len(bars)):
        if limit is not None and bars.time[index] > limit:
            break
        bars_used += 1
        favourable, adverse = side_prices(bars, index, is_long)
        r_fav = ((favourable - entry) if is_long else (entry - favourable)) / risk
        r_adv = ((adverse - entry) if is_long else (entry - adverse)) / risk
        if r_fav > peak_r:
            peak_r, peak_index = r_fav, index
        trough_r = min(trough_r, r_adv)
    if bars_used == 0:
        return {"bars": 0, "mfe_r": None, "mae_r": None, "minutes_to_peak": None}
    return {
        "bars": bars_used,
        "mfe_r": peak_r,
        "mae_r": trough_r,
        "minutes_to_peak": (bars.time[peak_index] - bars.time[start_index]) / 60.0,
    }


def simulate(bars: Bars, start_index: int, is_long: bool, entry: float, stop: float,
             target: Optional[float], use_ladder: bool, partial_at: Optional[float],
             partial_fraction: float, breakeven_at: Optional[float],
             trail_after: Optional[float]) -> dict:
    """Score one exit policy on the archived path. Adverse wins same-bar ties."""
    risk = abs(entry - stop)
    if risk <= 0 or start_index is None or start_index >= len(bars):
        return {"r": None, "reason": "no_data", "bars": 0}
    open_fraction = 1.0
    realized = 0.0
    stop_r: Optional[float] = None
    peak_r = 0.0
    partial_done = False

    def price_at_r(level: float) -> float:
        return entry + risk * level if is_long else entry - risk * level

    for count, index in enumerate(range(start_index, len(bars)), start=1):
        favourable, adverse = side_prices(bars, index, is_long)
        stop_price = stop if stop_r is None else price_at_r(stop_r)
        hit_stop = adverse <= stop_price if is_long else adverse >= stop_price
        if hit_stop:
            realized += open_fraction * (stop_r if stop_r is not None else -1.0)
            reason = "trail_stop" if stop_r is not None else "stop_loss"
            return {"r": realized, "reason": reason, "bars": count}

        r_fav = ((favourable - entry) if is_long else (entry - favourable)) / risk
        peak_r = max(peak_r, r_fav)

        if partial_at is not None and not partial_done and peak_r >= partial_at and partial_fraction > 0:
            realized += open_fraction * partial_fraction * partial_at
            open_fraction *= (1.0 - partial_fraction)
            partial_done = True

        if target is not None:
            hit_target = favourable >= target if is_long else favourable <= target
            if hit_target:
                r_target = ((target - entry) if is_long else (entry - target)) / risk
                realized += open_fraction * r_target
                return {"r": realized, "reason": "take_profit", "bars": count}

        if use_ladder:
            stop_r = ladder_stop_r(peak_r, stop_r)
        else:
            if breakeven_at is not None and peak_r >= breakeven_at:
                stop_r = max(stop_r, 0.0) if stop_r is not None else 0.0
            if trail_after is not None and peak_r >= trail_after and TRAIL_STEP_R > 0:
                rung = math.floor(round(peak_r / TRAIL_STEP_R, 6)) * TRAIL_STEP_R
                trailed = round(rung - TRAIL_GIVEBACK_R, 6)
                if trailed > 0:
                    stop_r = trailed if stop_r is None else max(stop_r, trailed)

        if count >= HOLD_LIMIT_BARS:
            exit_price = bars.close[index]
            r_exit = ((exit_price - entry) if is_long else (entry - exit_price)) / risk
            realized += open_fraction * r_exit
            return {"r": realized, "reason": "time_expiry", "bars": count}

    exit_price = bars.close[len(bars) - 1]
    r_exit = ((exit_price - entry) if is_long else (entry - exit_price)) / risk
    realized += open_fraction * r_exit
    return {"r": realized, "reason": "archive_end", "bars": len(bars) - start_index}


def zone_universe(records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """entry_zone_formed events grouped by (symbol, demand|supply)."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for record in records:
        if record.get("type") != "entry_zone_formed":
            continue
        symbol = record.get("symbol")
        zone_id = record.get("zone_id")
        direction = record.get("direction")
        if not symbol or not zone_id or direction not in {"demand", "supply"}:
            continue
        key = (str(symbol), str(zone_id))
        if key in seen:
            continue
        seen.add(key)
        confirmed = parse_dt(record.get("confirmed_at"))
        proximal = num(record.get("proximal"))
        if confirmed is None or proximal is None:
            continue
        grouped[(str(symbol), direction)].append({
            "zone_id": str(zone_id),
            "confirmed_at": confirmed,
            "origin_time": parse_dt(record.get("origin_time")),
            "proximal": proximal,
            "distal": num(record.get("distal")),
            "lower": num(record.get("lower")),
            "upper": num(record.get("upper")),
        })
    for items in grouped.values():
        items.sort(key=lambda item: item["confirmed_at"])
    return grouped


def campaign_context(universe: dict, symbol: str, is_long: bool, destination_confirmed: Optional[datetime],
                     signal_close: Optional[datetime], entry_zone_id: str, risk: float,
                     entry_proximal: Optional[float]) -> dict:
    """Every same-direction 15m zone confirmed between 4H activation and the signal."""
    side = "demand" if is_long else "supply"
    zones = universe.get((symbol, side), [])
    if destination_confirmed is None or signal_close is None:
        return {"campaign_zones_all": None}
    window = [zone for zone in zones
              if destination_confirmed <= zone["confirmed_at"] <= signal_close]
    if not window:
        return {"campaign_zones_all": 0}
    extreme = min(window, key=lambda z: z["proximal"]) if is_long \
        else max(window, key=lambda z: z["proximal"])
    ordered = sorted(window, key=lambda z: z["proximal"], reverse=not is_long)
    rank = next((position for position, zone in enumerate(ordered, start=1)
                 if zone["zone_id"] == entry_zone_id), None)
    gap = None
    if entry_proximal is not None and risk > 0:
        raw = (entry_proximal - extreme["proximal"]) if is_long \
            else (extreme["proximal"] - entry_proximal)
        gap = raw / risk
    return {
        "campaign_zones_all": len(window),
        "campaign_extreme_zone_id": extreme["zone_id"],
        "entry_zone_is_extreme_all": extreme["zone_id"] == entry_zone_id,
        "entry_zone_extreme_rank": rank,
        "r_from_extreme_zone": gap,
        "extreme_zone_confirmed_utc": extreme["confirmed_at"].isoformat(),
    }


def ladder_actuals(entry: dict, signal_id: str, position_id: int) -> dict:
    """What the live ladder actually did, from the 5m journal."""
    partials, moves = [], []
    for event in entry.get("linked_journal_events", []):
        kind = event.get("_event_type")
        if kind not in {"partial_banked", "ladder_stop_moved"}:
            continue
        same = (str(event.get("signal_id") or "") == signal_id and signal_id) \
            or int(event.get("position_ticket") or 0) == position_id
        if not same:
            continue
        (partials if kind == "partial_banked" else moves).append(event)
    ok_moves = [event for event in moves if event.get("ok")]
    sl_levels = [num(event.get("sl_r")) for event in ok_moves]
    sl_levels = [level for level in sl_levels if level is not None]
    peaks = [num(event.get("peak_r")) for event in moves + partials]
    peaks = [peak for peak in peaks if peak is not None]
    return {
        "live_partial_banked": any(event.get("ok") for event in partials),
        "live_partial_events": len(partials),
        "live_stop_moves_ok": len(ok_moves),
        "live_final_stop_r": max(sl_levels) if sl_levels else None,
        "live_stop_went_positive": bool(sl_levels and max(sl_levels) > 0),
        "live_peak_r_journal": max(peaks) if peaks else None,
        "live_stop_clamped_by_broker": any(event.get("clamped_by_broker") for event in moves),
    }


def classify(mfe_r: Optional[float], realized_r: Optional[float], reason: str,
             partial: bool) -> str:
    """Plain-language outcome bucket."""
    if realized_r is None or mfe_r is None:
        return "unresolved"
    if reason == "take_profit" and realized_r > 0:
        return "target_hit"
    if realized_r > 0.05:
        return "trailed_winner" if partial else "partial_winner"
    if -0.05 <= realized_r <= 0.05:
        return "breakeven_after_run"
    if mfe_r < 0.25:
        return "stopped_never_our_way"
    if mfe_r < PARTIAL_AT_R:
        return "ran_then_reverted_before_partial"
    return "ran_past_partial_then_lost"


def analyse(entry: dict, five: dict[str, Bars], universe: dict, points: dict[str, float],
            archive_end: datetime) -> dict:
    trade = entry["trade"]
    signal = entry.get("signal_record") or {}
    symbol = str(trade["Symbol"])
    is_long = str(trade["Direction"]) == "long"
    entry_price = num(trade["Entry Price"])
    stop = num(trade["Initial Stop"]) or num(signal.get("stop"))
    target = num(trade["Initial Target"]) or num(signal.get("destination_target"))
    entry_time = parse_dt(trade["Trade DateTime"])
    exit_time = parse_dt(trade.get("Last Exit DateTime")) or archive_end
    realized_money = num(trade.get("Realized P/L"))
    loss_at_sl = num(trade.get("Loss at SL")) or num(signal.get("loss_at_sl"))
    risk = abs(entry_price - stop) if entry_price and stop else None
    point = points.get(symbol, 1e-5)
    bars = five.get(symbol)

    row: dict[str, Any] = {
        "position_id": trade["Position ID"],
        "symbol": symbol,
        "direction": trade["Direction"],
        "model": trade.get("Model") or signal.get("model"),
        "result": trade["Result"],
        "exit_reason": trade.get("Exit Reason"),
        "signal_id": trade.get("Signal ID"),
        "entry_utc": entry_time.isoformat() if entry_time else None,
        "entry_ist": entry_time.astimezone(IST).isoformat() if entry_time else None,
        "exit_utc": exit_time.isoformat() if exit_time else None,
        "hour_utc": entry_time.hour if entry_time else None,
        "weekday": entry_time.strftime("%a") if entry_time else None,
        "lots": num(trade.get("Lots")),
        "entry_price": entry_price,
        "initial_stop": stop,
        "broker_target": target,
        "risk_price": rnd(risk, 8),
        "risk_points": rnd(risk / point, 1) if risk else None,
        "realized_money": realized_money,
        "loss_at_sl_money": loss_at_sl,
        "stop_basis": trade.get("Stop Basis") or signal.get("stop_basis"),
        "entry_zone_id": trade.get("Entry Zone ID"),
        "destination_zone_id": trade.get("Destination Zone ID"),
        "destination_rr": num(signal.get("destination_reward_risk")),
        "spread_at_signal_points": rnd((num(signal.get("spread_at_signal")) or 0) / point, 1),
        "spread_pct_of_risk": num(signal.get("spread_pct_of_risk")),
        "round_trip_cost_r": num(signal.get("round_trip_cost_r")),
        "stop_multiple_vs_structural": num(signal.get("stop_multiple_vs_structural")),
        "used_liquidity_pool_stop": bool(signal.get("stop_liquidity_pool")),
        "minutes_held": round((exit_time - entry_time).total_seconds() / 60.0, 1)
        if entry_time and exit_time else None,
    }

    money_r = (realized_money / loss_at_sl) if realized_money is not None and loss_at_sl else None
    priced = realized_r_from_deals(entry, is_long, entry_price, risk or 0.0)
    row["realized_r_money_est"] = rnd(money_r, 3)
    row["realized_r"] = rnd(priced["realized_r_price"], 3)
    row["exit_legs"] = priced["exit_legs"]
    row["closed_fraction"] = rnd(priced["closed_fraction"], 3)

    # ---- structural timing -------------------------------------------------
    destination_confirmed = parse_dt(signal.get("destination_confirmed_at"))
    zone_confirmed = parse_dt(signal.get("entry_zone_confirmed_at"))
    alert_close = parse_dt(signal.get("alert_bar_close"))
    signal_close = parse_dt(signal.get("signal_bar_close"))
    row["destination_confirmed_utc"] = destination_confirmed.isoformat() if destination_confirmed else None
    row["zone_confirmed_utc"] = zone_confirmed.isoformat() if zone_confirmed else None
    row["destination_age_hours"] = round((signal_close - destination_confirmed).total_seconds() / 3600.0, 2) \
        if destination_confirmed and signal_close else None
    row["zone_age_hours"] = round((signal_close - zone_confirmed).total_seconds() / 3600.0, 2) \
        if zone_confirmed and signal_close else None
    row["alert_to_confirm_minutes"] = round((signal_close - alert_close).total_seconds() / 60.0, 1) \
        if alert_close and signal_close else None

    # ---- entry zone geometry ----------------------------------------------
    zone_lower = num(signal.get("entry_zone_lower"))
    zone_upper = num(signal.get("entry_zone_upper"))
    proximal = zone_upper if is_long else zone_lower
    distal = num(signal.get("entry_zone_distal"))
    if zone_lower is not None and zone_upper is not None and risk:
        row["zone_width_r"] = rnd((zone_upper - zone_lower) / risk, 3)
        row["zone_width_points"] = rnd((zone_upper - zone_lower) / point, 1)
    if proximal is not None and entry_price is not None and risk:
        beyond = (entry_price - proximal) if is_long else (proximal - entry_price)
        row["entry_beyond_proximal_r"] = rnd(beyond / risk, 3)
    if distal is not None and stop is not None and risk:
        pad = (distal - stop) if is_long else (stop - distal)
        row["stop_pad_beyond_distal_r"] = rnd(pad / risk, 3)

    row.update(campaign_context(universe, symbol, is_long, destination_confirmed,
                                signal_close, str(trade.get("Entry Zone ID") or ""),
                                risk or 0.0, proximal))
    row["r_from_extreme_zone"] = rnd(row.get("r_from_extreme_zone"), 3)
    row.update(ladder_actuals(entry, str(trade.get("Signal ID") or ""), int(trade["Position ID"])))

    # ---- realised path -----------------------------------------------------
    start_index = bars.index_containing(int(entry_time.timestamp())) if bars and entry_time else None
    if bars and start_index is not None and risk:
        row["entry_bar_open_utc"] = datetime.fromtimestamp(bars.time[start_index], UTC).isoformat()
        row["entry_bar_partial"] = bars.time[start_index] < int(entry_time.timestamp())
        walked = walk_path(bars, start_index, is_long, entry_price, risk, exit_time)
        row["mfe_r"] = rnd(walked["mfe_r"], 3)
        row["mae_r"] = rnd(walked["mae_r"], 3)
        row["minutes_to_peak"] = walked["minutes_to_peak"]
        row["bars_in_window"] = walked["bars"]
        row["reached_partial_1_25r"] = bool(walked["mfe_r"] is not None and walked["mfe_r"] >= PARTIAL_AT_R)
        row["reached_first_trail_1_5r"] = bool(walked["mfe_r"] is not None and walked["mfe_r"] >= 1.5)
        row["reached_1r"] = bool(walked["mfe_r"] is not None and walked["mfe_r"] >= 1.0)
        row["reached_0_5r"] = bool(walked["mfe_r"] is not None and walked["mfe_r"] >= 0.5)

        scenarios = {
            "live_ladder_sim": dict(target=target, use_ladder=True, partial_at=PARTIAL_AT_R,
                                    partial_fraction=PARTIAL_FRACTION, breakeven_at=None, trail_after=None),
            "fixed_0_75r": dict(target=None, use_ladder=False, partial_at=None, partial_fraction=0.0,
                                breakeven_at=None, trail_after=None, fixed_r=0.75),
            "fixed_1r": dict(target=None, use_ladder=False, partial_at=None, partial_fraction=0.0,
                             breakeven_at=None, trail_after=None, fixed_r=1.0),
            "fixed_1_25r": dict(target=None, use_ladder=False, partial_at=None, partial_fraction=0.0,
                                breakeven_at=None, trail_after=None, fixed_r=1.25),
            "fixed_2r": dict(target=None, use_ladder=False, partial_at=None, partial_fraction=0.0,
                             breakeven_at=None, trail_after=None, fixed_r=2.0),
            "destination_only": dict(target=target, use_ladder=False, partial_at=None,
                                     partial_fraction=0.0, breakeven_at=None, trail_after=None),
            "partial_1r_then_be": dict(target=target, use_ladder=False, partial_at=1.0,
                                       partial_fraction=0.5, breakeven_at=1.0, trail_after=1.5),
            "no_partial_trail_from_1_5r": dict(target=target, use_ladder=False, partial_at=None,
                                               partial_fraction=0.0, breakeven_at=None, trail_after=1.5),
            "be_at_0_75r_then_dest": dict(target=target, use_ladder=False, partial_at=None,
                                          partial_fraction=0.0, breakeven_at=0.75, trail_after=1.5),
            "be_at_1r_then_dest": dict(target=target, use_ladder=False, partial_at=None,
                                       partial_fraction=0.0, breakeven_at=1.0, trail_after=1.5),
            "be_at_1r_target_2r": dict(target=None, use_ladder=False, partial_at=None,
                                       partial_fraction=0.0, breakeven_at=1.0, trail_after=None,
                                       fixed_r=2.0),
        }
        for name, config in scenarios.items():
            fixed_r = config.pop("fixed_r", None)
            resolved_target = config.pop("target")
            if fixed_r is not None:
                resolved_target = entry_price + risk * fixed_r if is_long else entry_price - risk * fixed_r
            outcome = simulate(bars, start_index, is_long, entry_price, stop,
                               resolved_target, **config)
            row[f"scn_{name}_r"] = rnd(outcome["r"], 3)
            row[f"scn_{name}_reason"] = outcome["reason"]
    row["outcome_class"] = classify(row.get("mfe_r"), row.get("realized_r"),
                                    str(row.get("exit_reason") or ""),
                                    bool(row.get("live_partial_banked")))
    return row


def stats(values: list[float]) -> dict:
    clean = [value for value in values if value is not None]
    if not clean:
        return {"n": 0}
    return {
        "n": len(clean),
        "sum": rnd(sum(clean), 3),
        "mean": rnd(statistics.fmean(clean), 3),
        "median": rnd(statistics.median(clean), 3),
        "min": rnd(min(clean), 3),
        "max": rnd(max(clean), 3),
    }


def cohort(rows: list[dict], label_of) -> dict:
    grouped: dict[Any, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[label_of(row)].append(row)
    summary = {}
    for key, items in grouped.items():
        realized = [row["realized_r"] for row in items if row.get("realized_r") is not None]
        money = [row["realized_money"] for row in items if row.get("realized_money") is not None]
        wins = sum(1 for value in realized if value > 0.05)
        summary[str(key)] = {
            "trades": len(items),
            "wins": wins,
            "losses": sum(1 for value in realized if value < -0.05),
            "win_rate_pct": rnd(100.0 * wins / len(realized), 1) if realized else None,
            "total_r": rnd(sum(realized), 3),
            "expectancy_r": rnd(statistics.fmean(realized), 3) if realized else None,
            "total_money": rnd(sum(money), 2),
            "avg_mfe_r": rnd(statistics.fmean([row["mfe_r"] for row in items
                                               if row.get("mfe_r") is not None]), 3)
            if any(row.get("mfe_r") is not None for row in items) else None,
        }
    return dict(sorted(summary.items(), key=lambda item: item[1]["total_r"] or 0, reverse=True))


def bucket(value: Optional[float], edges: list[float], labels: list[str]) -> str:
    if value is None:
        return "unknown"
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


EXCLUSION_TESTS: dict[str, Any] = {
    "single_zone_campaign": lambda row: (row.get("campaign_zones_all") or 0) == 1,
    "entry_inside_zone_no_sweep": lambda row: (row.get("entry_beyond_proximal_r") or 0) <= 0,
    "zone_width_ge_1r": lambda row: (row.get("zone_width_r") or 0) >= 1.0,
    "conservative_mss": lambda row: row.get("model") == "conservative_mss",
    "alert_delay_15_60m": lambda row: 15 <= (row.get("alert_to_confirm_minutes") or 0) < 60,
    "gap_from_extreme_ge_3r": lambda row: (row.get("r_from_extreme_zone") or 0) >= 3.0,
    "cost_035_050r": lambda row: 0.35 <= (row.get("round_trip_cost_r") or 0) < 0.5,
    "spread_gt_20pct_of_risk": lambda row: (row.get("spread_pct_of_risk") or 0) > 20,
    "destination_age_lt_24h": lambda row: (row.get("destination_age_hours") or 0) < 24,
    "zone_age_ge_12h": lambda row: (row.get("zone_age_hours") or 0) >= 12,
    "destination_rr_6_10": lambda row: 6 <= (row.get("destination_rr") or 0) < 10,
    "short_direction": lambda row: row["direction"] == "short",
}

KEEP_SETS: dict[str, Any] = {
    "A_structure": lambda row: (row.get("campaign_zones_all") or 0) > 1
    and (row.get("entry_beyond_proximal_r") or 0) > 0
    and (row.get("zone_width_r") or 0) < 1.0
    and row.get("model") != "conservative_mss"
    and (row.get("r_from_extreme_zone") or 0) < 3.0,
    "A_plus_cost_cap": lambda row: KEEP_SETS["A_structure"](row)
    and (row.get("spread_pct_of_risk") or 0) <= 20,
    "A_plus_cost_plus_mature_destination": lambda row: KEEP_SETS["A_plus_cost_cap"](row)
    and (row.get("destination_age_hours") or 0) >= 24,
}


def slice_score(rows: list[dict], key: str = "realized_r") -> dict:
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return {"trades": 0}
    wins = sum(1 for value in values if value > 0.05)
    return {
        "trades": len(values),
        "win_rate_pct": rnd(100.0 * wins / len(values), 1),
        "total_r": rnd(sum(values), 2),
        "expectancy_r": rnd(statistics.fmean(values), 3),
    }


def filter_lab(rows: list[dict]) -> dict:
    """Out-of-sample check: does a rule lose money in BOTH halves of the period?"""
    ordered = sorted([row for row in rows if row.get("realized_r") is not None],
                     key=lambda row: row["entry_utc"] or "")
    midpoint = len(ordered) // 2
    halves = {"all": ordered, "first_half": ordered[:midpoint], "second_half": ordered[midpoint:]}
    exclusions = {}
    for name, predicate in EXCLUSION_TESTS.items():
        per_half = {label: slice_score([row for row in group if predicate(row)])
                    for label, group in halves.items()}
        first, second = per_half["first_half"], per_half["second_half"]
        exclusions[name] = {
            "cohort_by_half": per_half,
            "consistently_negative": bool(
                first.get("trades", 0) >= 2 and second.get("trades", 0) >= 2
                and (first.get("total_r") or 0) < 0 and (second.get("total_r") or 0) < 0
            ),
        }
    keeps = {}
    for name, predicate in KEEP_SETS.items():
        keeps[name] = {
            label: {
                "live_exits": slice_score([row for row in group if predicate(row)]),
                "best_exit_policy": slice_score(
                    [row for row in group if predicate(row)],
                    "scn_no_partial_trail_from_1_5r_r"),
            }
            for label, group in halves.items()
        }
    return {
        "method": "period split in half by entry time; a rule is only trustworthy "
                  "if its excluded cohort loses in both halves and a keep-set "
                  "stays positive in both halves",
        "half_boundaries": {
            label: {"first_entry": group[0]["entry_utc"], "last_entry": group[-1]["entry_utc"],
                    "trades": len(group)}
            for label, group in halves.items() if group
        },
        "baseline_by_half": {label: slice_score(group) for label, group in halves.items()},
        "exclusion_candidates": exclusions,
        "keep_sets": keeps,
        "any_keep_set_positive_in_both_halves": any(
            (data["first_half"]["live_exits"].get("total_r") or 0) > 0
            and (data["second_half"]["live_exits"].get("total_r") or 0) > 0
            for data in keeps.values()
        ),
    }


def build_summary(rows: list[dict]) -> dict:
    closed = [row for row in rows if row["result"] != "Open"]
    realized = [row["realized_r"] for row in closed if row.get("realized_r") is not None]
    money = [row["realized_money"] for row in closed if row.get("realized_money") is not None]
    wins = [value for value in realized if value > 0.05]
    losses = [value for value in realized if value < -0.05]
    scenario_names = [key[4:-2] for key in rows[0] if key.startswith("scn_") and key.endswith("_r")]
    scenarios = {}
    for name in scenario_names:
        values = [row.get(f"scn_{name}_r") for row in rows]
        values = [value for value in values if value is not None]
        wins_s = sum(1 for value in values if value > 0.05)
        scenarios[name] = {
            "trades": len(values),
            "total_r": rnd(sum(values), 2),
            "expectancy_r": rnd(statistics.fmean(values), 3) if values else None,
            "win_rate_pct": rnd(100.0 * wins_s / len(values), 1) if values else None,
        }
    return {
        "trades_total": len(rows),
        "trades_closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": rnd(100.0 * len(wins) / len(realized), 1) if realized else None,
        "total_r": rnd(sum(realized), 2),
        "expectancy_r": rnd(statistics.fmean(realized), 3) if realized else None,
        "total_money": rnd(sum(money), 2),
        "avg_win_r": rnd(statistics.fmean(wins), 3) if wins else None,
        "avg_loss_r": rnd(statistics.fmean(losses), 3) if losses else None,
        "payoff_ratio": rnd(abs(statistics.fmean(wins) / statistics.fmean(losses)), 3)
        if wins and losses else None,
        "risk_normalisation": {
            "loss_at_sl_money": stats([row.get("loss_at_sl_money") for row in rows]),
            "money_r_vs_price_r_gap": stats([
                (row["realized_r_money_est"] - row["realized_r"])
                for row in rows
                if row.get("realized_r_money_est") is not None and row.get("realized_r") is not None
            ]),
        },
        "cost_drag": {
            "round_trip_cost_r": stats([row.get("round_trip_cost_r") for row in rows]),
            "spread_pct_of_risk": stats([row.get("spread_pct_of_risk") for row in rows]),
        },
        "excursion": {
            "mfe_r": stats([row.get("mfe_r") for row in rows]),
            "mae_r": stats([row.get("mae_r") for row in rows]),
            "risk_points": stats([row.get("risk_points") for row in rows]),
            "minutes_held": stats([row.get("minutes_held") for row in rows]),
        },
        "reach_rates_pct": {
            level: rnd(100.0 * sum(1 for row in rows if row.get(key)) / len(rows), 1)
            for level, key in (("0.5R", "reached_0_5r"), ("1.0R", "reached_1r"),
                               ("1.25R_partial", "reached_partial_1_25r"),
                               ("1.5R_first_trail", "reached_first_trail_1_5r"))
        },
        "ladder_live": {
            "partial_banked": sum(1 for row in rows if row.get("live_partial_banked")),
            "stop_moved_positive": sum(1 for row in rows if row.get("live_stop_went_positive")),
            "stop_clamped_by_broker": sum(1 for row in rows if row.get("live_stop_clamped_by_broker")),
        },
        "outcome_classes": dict(Counter(row["outcome_class"] for row in rows).most_common()),
        "scenarios": dict(sorted(scenarios.items(),
                                 key=lambda item: item[1]["total_r"] or 0, reverse=True)),
        "filter_lab": filter_lab(rows),
        "by_outcome_class": cohort(rows, lambda row: row["outcome_class"]),
        "by_symbol": cohort(rows, lambda row: row["symbol"]),
        "by_direction": cohort(rows, lambda row: row["direction"]),
        "by_model": cohort(rows, lambda row: row["model"]),
        "by_stop_basis": cohort(rows, lambda row: row["stop_basis"]),
        "by_liquidity_pool_stop": cohort(rows, lambda row: f"liq_pool={row['used_liquidity_pool_stop']}"),
        "by_hour_utc": cohort(rows, lambda row: f"{row['hour_utc']:02d}h" if row["hour_utc"] is not None else "unknown"),
        "by_weekday": cohort(rows, lambda row: row["weekday"]),
        "by_cost_bucket": cohort(rows, lambda row: bucket(
            row.get("round_trip_cost_r"), [0.2, 0.35, 0.5],
            ["cost<0.20R", "cost_0.20-0.35R", "cost_0.35-0.50R", "cost>=0.50R"])),
        "by_risk_points": cohort(rows, lambda row: bucket(
            row.get("risk_points"), [40, 80, 150],
            ["risk<40pt", "risk_40-80pt", "risk_80-150pt", "risk>=150pt"])),
        "by_destination_rr": cohort(rows, lambda row: bucket(
            row.get("destination_rr"), [3, 6, 10],
            ["destRR<3", "destRR_3-6", "destRR_6-10", "destRR>=10"])),
        "by_zone_age": cohort(rows, lambda row: bucket(
            row.get("zone_age_hours"), [1, 4, 12],
            ["zone<1h", "zone_1-4h", "zone_4-12h", "zone>=12h"])),
        "by_destination_age": cohort(rows, lambda row: bucket(
            row.get("destination_age_hours"), [24, 72, 168],
            ["dest<1d", "dest_1-3d", "dest_3-7d", "dest>=7d"])),
        "by_campaign_zones": cohort(rows, lambda row: bucket(
            row.get("campaign_zones_all"), [2, 4, 8],
            ["zones_1", "zones_2-3", "zones_4-7", "zones_8+"])),
        "by_extreme_all": cohort(rows, lambda row: f"extreme_of_all_formed={row.get('entry_zone_is_extreme_all')}"),
        "by_gap_from_extreme": cohort(rows, lambda row: bucket(
            row.get("r_from_extreme_zone"), [0.01, 1.0, 3.0],
            ["at_extreme", "gap<1R", "gap_1-3R", "gap>=3R"])),
        "by_entry_beyond_proximal": cohort(rows, lambda row: bucket(
            row.get("entry_beyond_proximal_r"), [0.0, 0.5, 1.0],
            ["inside_zone", "beyond_0-0.5R", "beyond_0.5-1R", "beyond>=1R"])),
        "by_zone_width": cohort(rows, lambda row: bucket(
            row.get("zone_width_r"), [0.3, 0.6, 1.0],
            ["width<0.3R", "width_0.3-0.6R", "width_0.6-1R", "width>=1R"])),
        "by_alert_delay": cohort(rows, lambda row: bucket(
            row.get("alert_to_confirm_minutes"), [1, 15, 60],
            ["same_bar", "delay_5-15m", "delay_15-60m", "delay>=60m"])),
    }


def write_outputs(rows: list[dict], summary: dict, manifest: dict) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    csv_path = OUT_DIR / "s146_trade_deep_analysis.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    json_path = OUT_DIR / "s146_deep_analysis.json"
    json_path.write_text(json.dumps({
        "schema_version": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "read_only": True,
        "source_archive": str(ARCHIVE.relative_to(REPO)).replace("\\", "/"),
        "archive_range_utc": {
            "start": manifest["requested_start_utc"],
            "end": manifest["captured_end_utc"],
        },
        "ladder_config": {
            "partial_at_r": PARTIAL_AT_R, "partial_fraction": PARTIAL_FRACTION,
            "trail_step_r": TRAIL_STEP_R, "trail_giveback_r": TRAIL_GIVEBACK_R,
        },
        "path_model": "archived native 5m bars; bid bars with per-bar spread applied "
                      "to the short exit side; adverse extreme wins same-bar ties",
        "summary": summary,
        "trades": rows,
    }, indent=2, default=str), encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path)}


def main() -> int:
    manifest = json.loads((ARCHIVE / "manifest.json").read_text(encoding="utf-8"))
    bar_manifest = json.loads((ARCHIVE / "bars" / "manifest.json").read_text(encoding="utf-8"))
    details = json.loads((ARCHIVE / "s146_trade_details.json").read_text(encoding="utf-8"))["trades"]
    five, _fifteen, points = load_bars(bar_manifest)
    universe = zone_universe(journal_records(ARCHIVE / "logs" / "s146_15m_events.log"))
    archive_end = parse_dt(manifest["captured_end_utc"]) or datetime.now(UTC)

    rows = [analyse(entry, five, universe, points, archive_end) for entry in details]
    rows.sort(key=lambda row: row["entry_utc"] or "")
    summary = build_summary(rows)
    written = write_outputs(rows, summary, manifest)
    print(json.dumps({"outputs": written, "summary": summary}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
