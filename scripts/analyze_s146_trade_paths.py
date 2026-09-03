"""Analyze the 16 reconciled s146 signals against exported MT5 5m bars.

The script is read-only with respect to MT5/live trading. It distinguishes actual
fills from rejected-signal counterfactuals and writes JSON, CSV, and Markdown.
OHLC results are conservative when stop and target occur in the same candle.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "s146_mt5"
RECON_PATH = DATA / "s146_reconciliation.json"
LOG_PATH = REPO / "liveTrade" / "logs" / "s146_trades.log"
EVENT_LOG_PATH = REPO / "liveTrade" / "logs" / "s146_5m_events.log"
JSON_OUT = DATA / "s146_path_analysis.json"
CSV_OUT = DATA / "s146_path_analysis.csv"
REPORT_OUT = REPO / "docs" / "s146_trade_path_analysis.md"
UTC = timezone.utc
WINDOW_HOURS = (1, 3, 6, 12, 24)
FAVOURABLE_LEVELS = (0.5, 1.0, 1.25, 2.0)
STOP_MULTIPLIERS = (1.0, 1.25, 1.5)
EPS = 1e-9


def parse_ts(value) -> datetime:
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def ts(value) -> int:
    return int(parse_ts(value).timestamp())


def iso(stamp: int | None) -> str | None:
    return datetime.fromtimestamp(stamp, UTC).isoformat() if stamp is not None else None


def rounded(value, digits=4):
    return None if value is None else round(float(value), digits)


def load_signals() -> dict[tuple[str, str, int], dict]:
    signals = {}
    for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(" | ", 2)
        if len(parts) != 3:
            continue
        try:
            event = json.loads(parts[2])
        except json.JSONDecodeError:
            continue
        if event.get("type") != "signal_found" or event.get("strategy") != "s146":
            continue
        key = (event["symbol"], event["direction"], ts(event["signal_bar_open"]))
        signals[key] = event
    return signals


def load_management_events() -> dict[int, list[str]]:
    by_ticket = defaultdict(list)
    for line in EVENT_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(" | ", 2)
        if len(parts) != 3:
            continue
        try:
            event = json.loads(parts[2])
        except json.JSONDecodeError:
            continue
        ticket = event.get("position_ticket")
        if ticket:
            by_ticket[int(ticket)].append(str(event.get("type")))
    return dict(by_ticket)


def inferred_point(symbol: str) -> float:
    # Exported MT5 rates omit point/digits. These are the broker quote increments
    # for the FX symbols in this sample and are used only to approximate short
    # exits (ask = bid + spread * point).
    return 0.001 if symbol.endswith("JPY") else 0.00001


def load_bars(symbol: str) -> list[dict]:
    path = DATA / symbol / "5m" / f"{symbol}_5m.csv"
    point = inferred_point(symbol)
    bars = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            spread_price = float(row["spread"]) * point
            bars.append({
                "time": int(row["time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "spread_price": spread_price,
            })
    bars.sort(key=lambda x: x["time"])
    return bars


def execution_ohlc(bar: dict, direction: str) -> tuple[float, float, float, float]:
    if direction == "long":
        return bar["open"], bar["high"], bar["low"], bar["close"]
    spread = bar["spread_price"]
    return tuple(bar[name] + spread for name in ("open", "high", "low", "close"))


def r_values(bar: dict, direction: str, entry: float, risk: float) -> tuple[float, float, float]:
    _open, high, low, close = execution_ohlc(bar, direction)
    if direction == "long":
        return (high - entry) / risk, (low - entry) / risk, (close - entry) / risk
    return (entry - low) / risk, (entry - high) / risk, (entry - close) / risk


def bars_from(all_bars: list[dict], start: int) -> list[dict]:
    return [bar for bar in all_bars if bar["time"] >= start]


def passage(bars: list[dict], direction: str, entry: float, risk: float,
            level: float, favourable: bool) -> dict | None:
    for index, bar in enumerate(bars):
        fav, adv, _close = r_values(bar, direction, entry, risk)
        hit = fav + EPS >= level if favourable else adv - EPS <= level
        if hit:
            return {"bar_index": index, "bar_open_utc": iso(bar["time"]),
                    "minutes": index * 5, "r_level": level}
    return None


def fixed_outcome(bars: list[dict], direction: str, entry: float, risk: float,
                  stop_r: float = 1.0, target_r: float = 1.25) -> dict:
    for index, bar in enumerate(bars):
        fav, adv, _close = r_values(bar, direction, entry, risk)
        stop_hit = adv <= -stop_r + EPS
        target_hit = fav >= target_r - EPS
        if stop_hit or target_hit:
            if stop_hit and target_hit:
                status, result_r = "ambiguous_same_bar_stop_assumed", -stop_r
            elif stop_hit:
                status, result_r = "stop_first", -stop_r
            else:
                status, result_r = "target_first", target_r
            return {"status": status, "result_original_r": rounded(result_r),
                    "bar_index": index, "bar_open_utc": iso(bar["time"]),
                    "minutes": index * 5}
    return {"status": "censored", "result_original_r": None,
            "bar_index": None, "bar_open_utc": None, "minutes": None}


def window_excursions(bars: list[dict], direction: str, entry: float, risk: float) -> dict:
    if not bars:
        return {}
    result = {}
    for hours in (*WINDOW_HOURS, None):
        subset = bars if hours is None else bars[: hours * 12]
        key = "full" if hours is None else f"{hours}h"
        if not subset:
            result[key] = {"mfe_r": None, "mae_r": None, "bars": 0}
            continue
        values = [r_values(bar, direction, entry, risk) for bar in subset]
        result[key] = {"mfe_r": rounded(max(v[0] for v in values)),
                       "mae_r": rounded(min(v[1] for v in values)),
                       "bars": len(subset)}
    return result


def post_stop_analysis(bars: list[dict], direction: str, entry: float, risk: float,
                       stop_event: dict | None) -> dict | None:
    if stop_event is None:
        return None
    after = bars[int(stop_event["bar_index"]) + 1:]
    recovery = {}
    for level in (0.0, 0.5, 1.0, 1.25):
        hit = passage(after, direction, entry, risk, level, True)
        if hit:
            hit["minutes_after_stop"] = (hit["bar_index"] + 1) * 5
        recovery[str(level)] = hit
    values = [r_values(bar, direction, entry, risk)[0] for bar in after]
    return {"mfe_r_after_stop": rounded(max(values)) if values else None,
            "recovery": recovery}


def required_stop_to_target(bars: list[dict], direction: str, entry: float,
                            risk: float, target_r: float = 1.25) -> dict:
    target = passage(bars, direction, entry, risk, target_r, True)
    if not target:
        return {"target_reached": False, "required_stop_multiple": None,
                "same_bar_path_ambiguous": False}
    through = bars[: int(target["bar_index"]) + 1]
    adverse = [r_values(bar, direction, entry, risk)[1] for bar in through]
    required = max(0.0, -min(adverse)) if adverse else 0.0
    target_bar = through[-1]
    fav, adv, _close = r_values(target_bar, direction, entry, risk)
    return {"target_reached": True, "required_stop_multiple": rounded(required),
            "same_bar_path_ambiguous": adv <= -1.0 + EPS and fav >= target_r - EPS,
            "target_bar_open_utc": target["bar_open_utc"]}


def ladder_stop_r(peak_r: float, current: float | None, partial_at=1.25,
                  step=0.5, giveback=0.5) -> float | None:
    wanted = current
    if peak_r >= partial_at:
        wanted = max(wanted, 0.0) if wanted is not None else 0.0
    rung = math.floor(round(peak_r / step, 6)) * step if step > 0 else 0.0
    if rung > partial_at:
        trailed = round(rung - giveback, 6)
        wanted = trailed if wanted is None else max(wanted, trailed)
    return wanted


def ladder_outcome(bars: list[dict], direction: str, entry: float, risk: float,
                   destination: float, partial_at=1.25) -> dict:
    destination_r = ((destination - entry) if direction == "long" else
                     (entry - destination)) / risk
    open_fraction, realized_r, peak_r = 1.0, 0.0, 0.0
    partial_done, sl_r = False, None
    for index, bar in enumerate(bars):
        fav, adv, _close = r_values(bar, direction, entry, risk)
        active_stop_r = -1.0 if sl_r is None else sl_r
        if adv <= active_stop_r + EPS:
            realized_r += open_fraction * active_stop_r
            return {"status": "stop", "result_r": rounded(realized_r),
                    "partial_done": partial_done, "peak_r": rounded(peak_r),
                    "final_sl_r": sl_r, "bar_open_utc": iso(bar["time"]),
                    "minutes": index * 5, "destination_r": rounded(destination_r)}
        peak_r = max(peak_r, fav)
        if not partial_done and peak_r >= partial_at - EPS:
            realized_r += 0.5 * partial_at
            open_fraction = 0.5
            partial_done = True
            sl_r = ladder_stop_r(peak_r, sl_r, partial_at=partial_at)
        if fav >= destination_r - EPS:
            realized_r += open_fraction * destination_r
            return {"status": "destination", "result_r": rounded(realized_r),
                    "partial_done": partial_done, "peak_r": rounded(peak_r),
                    "final_sl_r": sl_r, "bar_open_utc": iso(bar["time"]),
                    "minutes": index * 5, "destination_r": rounded(destination_r)}
        sl_r = ladder_stop_r(peak_r, sl_r, partial_at=partial_at)
    return {"status": "censored", "result_r": None,
            "realized_partial_r": rounded(realized_r), "partial_done": partial_done,
            "peak_r": rounded(peak_r), "final_sl_r": sl_r,
            "bar_open_utc": None, "minutes": None,
            "destination_r": rounded(destination_r)}


def delayed_reclaim(bars: list[dict], direction: str, entry: float, stop: float) -> dict:
    risk = abs(entry - stop)
    adverse_seen = False
    for index, bar in enumerate(bars[:-1]):
        _fav, adv, close_r = r_values(bar, direction, entry, risk)
        adverse_seen = adverse_seen or adv <= -0.5 + EPS
        if adverse_seen and close_r >= 0.0:
            next_bar = bars[index + 1]
            delayed_entry = execution_ohlc(next_bar, direction)[0]
            delayed_risk = ((delayed_entry - stop) if direction == "long" else
                            (stop - delayed_entry))
            if delayed_risk <= 0:
                return {"status": "invalid_entry_beyond_stop"}
            outcome = fixed_outcome(bars[index + 1:], direction, delayed_entry,
                                    delayed_risk, 1.0, 1.25)
            return {"status": "entry_found", "entry_bar_open_utc": iso(next_bar["time"]),
                    "entry_price": delayed_entry, "risk_price": delayed_risk,
                    "outcome": outcome}
    return {"status": "no_reclaim_entry"}


def analyze_path(all_bars: list[dict], direction: str, entry: float, stop: float,
                 destination: float, start: int, anchor: str) -> dict:
    risk = (entry - stop) if direction == "long" else (stop - entry)
    if risk <= 0:
        return {"anchor": anchor, "error": "entry_not_inside_structural_stop"}
    bars = bars_from(all_bars, start)
    available_hours = ((bars[-1]["time"] + 300 - start) / 3600) if bars else 0.0
    passages = {str(level): passage(bars, direction, entry, risk, level, True)
                for level in FAVOURABLE_LEVELS}
    stop_event = passage(bars, direction, entry, risk, -1.0, False)
    destination_r = ((destination - entry) if direction == "long" else
                     (entry - destination)) / risk
    passages["destination"] = passage(bars, direction, entry, risk,
                                       destination_r, True)
    expanded = {}
    for mult in STOP_MULTIPLIERS[1:]:
        widened = fixed_outcome(bars, direction, entry, risk, mult, 1.25 * mult)
        value = widened.get("result_original_r")
        widened["constant_money_result_r"] = rounded(value / mult) if value is not None else None
        widened["lot_fraction_vs_current"] = rounded(1.0 / mult)
        same_target = fixed_outcome(bars, direction, entry, risk, mult, 1.25)
        same_value = same_target.get("result_original_r")
        same_target["constant_money_result_r"] = (
            rounded(same_value / mult) if same_value is not None else None)
        widened["same_original_target"] = same_target
        expanded[str(mult)] = widened
    fixed = fixed_outcome(bars, direction, entry, risk)
    stopped_first = fixed["status"] in ("stop_first", "ambiguous_same_bar_stop_assumed")
    return {
        "anchor": anchor, "start_utc": iso(start), "entry_price": entry,
        "stop_price": stop, "risk_price": risk, "destination_price": destination,
        "destination_r": rounded(destination_r), "available_hours": rounded(available_hours, 2),
        "right_censored_before_24h": available_hours < 24.0,
        "excursions": window_excursions(bars, direction, entry, risk),
        "first_passage": passages, "stop_passage": stop_event,
        "fixed_1_25r": fixed,
        "post_stop": post_stop_analysis(bars, direction, entry, risk, stop_event)
        if stopped_first else None,
        "required_stop_to_1_25r": required_stop_to_target(bars, direction, entry, risk),
        "expanded_stop_constant_money_risk": expanded,
        "current_ladder": ladder_outcome(bars, direction, entry, risk, destination, 1.25),
        "ladder_partial_at_1r": ladder_outcome(bars, direction, entry, risk, destination, 1.0),
        "delayed_reclaim_after_minus_0_5r": delayed_reclaim(bars, direction, entry, stop),
    }


def summarize_paths(rows: list[dict], path_name: str) -> dict:
    paths = [row[path_name] for row in rows if row.get(path_name) and not row[path_name].get("error")]
    fixed = Counter(path["fixed_1_25r"]["status"] for path in paths)
    resolved_fixed = [path["fixed_1_25r"]["result_original_r"] for path in paths
                      if path["fixed_1_25r"]["result_original_r"] is not None]
    eventual = {str(level): sum(path["first_passage"][str(level)] is not None for path in paths)
                for level in FAVOURABLE_LEVELS}
    stopped = [path for path in paths if path["fixed_1_25r"]["status"] in
               ("stop_first", "ambiguous_same_bar_stop_assumed")]
    recoveries = {
        str(level): sum(bool(path.get("post_stop") and
                             path["post_stop"]["recovery"].get(str(level))) for path in stopped)
        for level in (0.0, 0.5, 1.0, 1.25)
    }
    alternatives = {}
    for mult in STOP_MULTIPLIERS[1:]:
        outcomes = [path["expanded_stop_constant_money_risk"][str(mult)] for path in paths]
        values = [item["constant_money_result_r"] for item in outcomes
                  if item["constant_money_result_r"] is not None]
        same_outcomes = [item["same_original_target"] for item in outcomes]
        same_values = [item["constant_money_result_r"] for item in same_outcomes
                       if item["constant_money_result_r"] is not None]
        alternatives[str(mult)] = {
            "statuses": dict(Counter(item["status"] for item in outcomes)),
            "resolved": len(values), "mean_constant_money_r": rounded(mean(values)) if values else None,
            "sum_constant_money_r": rounded(sum(values)) if values else None,
            "same_original_target": {
                "statuses": dict(Counter(item["status"] for item in same_outcomes)),
                "resolved": len(same_values),
                "mean_constant_money_r": rounded(mean(same_values)) if same_values else None,
                "sum_constant_money_r": rounded(sum(same_values)) if same_values else None,
            },
        }
    ladder_summary = {}
    for field in ("current_ladder", "ladder_partial_at_1r"):
        values = [path[field]["result_r"] for path in paths if path[field]["result_r"] is not None]
        ladder_summary[field] = {
            "statuses": dict(Counter(path[field]["status"] for path in paths)),
            "resolved": len(values), "mean_r": rounded(mean(values)) if values else None,
            "sum_r": rounded(sum(values)) if values else None,
        }
    delayed = [path["delayed_reclaim_after_minus_0_5r"] for path in paths]
    delayed_values = [item["outcome"]["result_original_r"] for item in delayed
                      if item.get("outcome", {}).get("result_original_r") is not None]
    return {
        "count": len(paths), "available_under_24h": sum(path["right_censored_before_24h"] for path in paths),
        "fixed_1_25r_statuses": dict(fixed), "fixed_resolved": len(resolved_fixed),
        "fixed_mean_r_resolved_only": rounded(mean(resolved_fixed)) if resolved_fixed else None,
        "fixed_sum_r_resolved_only": rounded(sum(resolved_fixed)) if resolved_fixed else None,
        "eventual_favourable_passage_counts": eventual,
        "stopped_count": len(stopped), "post_stop_recovery_counts": recoveries,
        "expanded_stops": alternatives, "ladders": ladder_summary,
        "delayed_reclaim": {"entries_found": sum(item["status"] == "entry_found" for item in delayed),
                            "resolved": len(delayed_values),
                            "mean_r_resolved_only": rounded(mean(delayed_values)) if delayed_values else None,
                            "statuses": dict(Counter(item["status"] for item in delayed))},
    }


def group_summary(rows: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    result = {}
    for name, members in groups.items():
        statuses = [row["signal_path"]["fixed_1_25r"]["status"] for row in members]
        result[name] = {"count": len(members), "statuses": dict(Counter(statuses)),
                        "eventual_1_25r": sum(row["signal_path"]["first_passage"]["1.25"]
                                             is not None for row in members)}
    return result


def build_rows(recon: dict, signals: dict,
               management_events: dict[int, list[str]]) -> list[dict]:
    failure_reason = {2: "no_money", 5: "no_money", 11: "no_money",
                      12: "no_money", 13: "no_money", 14: "invalid_stops",
                      16: "invalid_stops"}
    bar_cache = {}
    rows = []
    for comparison in sorted(recon["comparisons"], key=lambda x: x["trade_number"]):
        number = comparison["trade_number"]
        symbol, direction = comparison["symbol"], comparison["direction"]
        signal_key = (symbol, direction, ts(comparison["manual_signal_open_utc"]))
        signal = signals.get(signal_key)
        if signal is None:
            raise RuntimeError(f"Missing exact signal log match for trade {number}")
        bars = bar_cache.setdefault(symbol, load_bars(symbol))
        signal_start = ts(signal["signal_bar_close"])
        signal_path = analyze_path(bars, direction, float(signal["trigger"]),
                                   float(signal["stop"]), float(signal["destination_target"]),
                                   signal_start, "signal_trigger_counterfactual")
        mt5_row = comparison.get("mt5")
        actual_path = None
        slippage_r = None
        if mt5_row:
            fill_time = ts(mt5_row["entry_time_utc"])
            actual_start = (fill_time // 300 + 1) * 300
            actual_path = analyze_path(bars, direction, float(mt5_row["entry_price"]),
                                       float(signal["stop"]), float(signal["destination_target"]),
                                       actual_start, "actual_fill_next_complete_5m_bar")
            signal_risk = abs(float(signal["trigger"]) - float(signal["stop"]))
            signed_slippage = ((float(mt5_row["entry_price"]) - float(signal["trigger"]))
                               if direction == "long" else
                               (float(signal["trigger"]) - float(mt5_row["entry_price"])))
            slippage_r = signed_slippage / signal_risk
        event_types = management_events.get(int(mt5_row["position_id"]), []) if mt5_row else []
        stop_modified = False
        unexplained_stop_modification = False
        if mt5_row and mt5_row.get("exit_reason", "").lower().startswith("[sl"):
            initial_sl = float(mt5_row.get("initial_order_sl") or 0.0)
            exit_price = float(mt5_row.get("exit_price") or 0.0)
            stop_modified = abs(exit_price - initial_sl) > 2 * inferred_point(symbol)
            unexplained_stop_modification = stop_modified and "ladder_stop_moved" not in event_types
        rows.append({
            "trade_number": number, "symbol": symbol, "direction": direction,
            "model": signal.get("model"), "stop_basis": signal.get("stop_basis"),
            "liquidity_stop": bool(signal.get("stop_liquidity_pool")),
            "signal_bar_open_utc": signal.get("signal_bar_open"),
            "signal_bar_close_utc": signal.get("signal_bar_close"),
            "alert_bar_close_utc": signal.get("alert_bar_close"),
            "manual_result": comparison.get("manual_result"),
            "execution_class": "actual_mt5_fill" if mt5_row else "rejected_counterfactual",
            "execution_failure": failure_reason.get(number),
            "mt5_match_method": "inferred_nearest_same_symbol_direction" if mt5_row else None,
            "mt5": mt5_row, "actual_result": comparison.get("actual_result"),
            "management_event_types": sorted(set(event_types)),
            "broker_stop_modified_from_initial": stop_modified,
            "unexplained_stop_modification": unexplained_stop_modification,
            "actual_slippage_r_vs_signal_trigger": rounded(slippage_r),
            "signal_path": signal_path, "actual_fill_path": actual_path,
        })
    return rows


def write_csv(rows: list[dict]) -> None:
    fields = ["trade_number", "symbol", "direction", "model", "stop_basis",
              "execution_class", "execution_failure", "actual_result", "actual_pnl",
              "slippage_r", "signal_available_hours", "signal_fixed_1_25r",
              "signal_mfe_r", "signal_mae_r", "signal_stop_hit", "signal_eventual_1_25r",
              "signal_post_stop_recovered_1_25r", "signal_required_stop_mult_to_1_25r",
              "actual_available_hours", "actual_fixed_1_25r", "actual_mfe_r", "actual_mae_r"]
    with CSV_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            signal = row["signal_path"]
            actual = row.get("actual_fill_path")
            post = signal.get("post_stop") or {}
            writer.writerow({
                "trade_number": row["trade_number"], "symbol": row["symbol"],
                "direction": row["direction"], "model": row["model"],
                "stop_basis": row["stop_basis"], "execution_class": row["execution_class"],
                "execution_failure": row["execution_failure"], "actual_result": row["actual_result"],
                "actual_pnl": (row.get("mt5") or {}).get("realized_pnl"),
                "slippage_r": row["actual_slippage_r_vs_signal_trigger"],
                "signal_available_hours": signal["available_hours"],
                "signal_fixed_1_25r": signal["fixed_1_25r"]["status"],
                "signal_mfe_r": signal["excursions"]["full"]["mfe_r"],
                "signal_mae_r": signal["excursions"]["full"]["mae_r"],
                "signal_stop_hit": bool(signal["stop_passage"]),
                "signal_eventual_1_25r": bool(signal["first_passage"]["1.25"]),
                "signal_post_stop_recovered_1_25r": bool(post.get("recovery", {}).get("1.25")),
                "signal_required_stop_mult_to_1_25r": signal["required_stop_to_1_25r"]["required_stop_multiple"],
                "actual_available_hours": actual["available_hours"] if actual else None,
                "actual_fixed_1_25r": actual["fixed_1_25r"]["status"] if actual else None,
                "actual_mfe_r": actual["excursions"]["full"]["mfe_r"] if actual else None,
                "actual_mae_r": actual["excursions"]["full"]["mae_r"] if actual else None,
            })


def strategy_line(label: str, item: dict, mean_key: str, sum_key: str) -> str:
    statuses = ", ".join(f"{k}={v}" for k, v in item["statuses"].items())
    return f"| {label} | {item['resolved']} | {item.get(mean_key)} | {item.get(sum_key)} | {statuses} |"


def write_report(payload: dict) -> None:
    rows, summary = payload["trades"], payload["summary"]
    signal_summary = summary["signal_paths"]
    actual_summary = summary["actual_fill_paths"]
    broker = summary["actual_mt5"]
    first_times = [bar["time"] for row in rows for bar in load_bars(row["symbol"])[:1]]
    last_times = [bar["time"] + 300 for row in rows for bar in load_bars(row["symbol"])[-1:]]
    lines = [
        "# s146 trade-path analysis", "",
        f"Generated: `{payload['generated_utc']}`. Sample: 16 signals; "
        f"{broker['count']} actual MT5 fills and {16 - broker['count']} rejected counterfactuals.", "",
        "## Verdict", "",
        f"The data supports **directional potential**, not yet a proven profitable strategy. "
        f"From the signal reference, {signal_summary['eventual_favourable_passage_counts']['1.25']} / "
        f"{signal_summary['count']} paths eventually reached +1.25R in the available export, but only "
        f"{signal_summary['fixed_1_25r_statuses'].get('target_first', 0)} reached it before the original stop "
        f"without same-bar ambiguity. Of {signal_summary['stopped_count']} paths that touched -1R, "
        f"{signal_summary['post_stop_recovery_counts']['1.25']} later reached +1.25R after a subsequent bar.", "",
        f"Actual broker truth was {broker['profits']} profitable and {broker['losses']} losing fills, "
        f"net `{broker['net_pnl']}` account-currency units. However, the largest winner contributed "
        f"`{broker['largest_winner_pnl']}`; excluding it, the other eight fills netted "
        f"`{broker['net_pnl_excluding_largest_winner']}`. The positive total is therefore highly "
        "concentrated, not evidence of stable expectancy. Five signals failed with `No money`; two "
        "failed with `Invalid stops`. Those seven are opportunity analysis only, not MT5 performance. "
        f"Also, {broker['unexplained_stop_modifications']} filled positions exited at an SL materially "
        "different from the initial broker SL without a recorded `ladder_stop_moved` event. Their broker "
        "P/L therefore includes manual or unjournaled management and is not a clean test of current code.", "",
        f"Coverage is only `{iso(min(first_times))}` through `{iso(max(last_times))}`. "
        f"{signal_summary['available_under_24h']} / 16 signal paths have less than 24 hours after entry, "
        "so late signals are right-censored and no 72-hour expectancy claim is possible.", "",
        "## Per-trade evidence", "",
        "`Fixed` is conservative 5m first-passage at the logged trigger/structural stop. "
        "`Recovery` means price touched the stop, then reached +1.25R on a later candle.", "",
        "| # | Symbol | Model | Stop basis | Execution | Hours | Fixed 1.25R | MFE R | MAE R | Stop then +1.25R | Stop needed to survive to +1.25R | Broker P/L |",
        "|---:|---|---|---|---|---:|---|---:|---:|---|---:|---:|",
    ]
    for row in rows:
        path = row["signal_path"]
        recovered = bool((path.get("post_stop") or {}).get("recovery", {}).get("1.25"))
        required = path["required_stop_to_1_25r"]["required_stop_multiple"]
        pnl = (row.get("mt5") or {}).get("realized_pnl", "—")
        lines.append(f"| {row['trade_number']} | {row['symbol']} {row['direction']} | {row['model']} | "
                     f"{row['stop_basis']} | {row['execution_class']} | {path['available_hours']} | "
                     f"{path['fixed_1_25r']['status']} | {path['excursions']['full']['mfe_r']} | "
                     f"{path['excursions']['full']['mae_r']} | {recovered} | {required} | {pnl} |")
    lines.extend(["", "## Predeclared alternative simulations", "",
                  "Results below are in constant-money-risk R. Censored trades are excluded from means; "
                  "therefore these are diagnostics, not comparable final backtests.", "",
                  "| Variant | Resolved | Mean R | Sum R | Outcomes |",
                  "|---|---:|---:|---:|---|"])
    fixed_item = {"resolved": signal_summary["fixed_resolved"],
                  "mean": signal_summary["fixed_mean_r_resolved_only"],
                  "sum": signal_summary["fixed_sum_r_resolved_only"],
                  "statuses": signal_summary["fixed_1_25r_statuses"]}
    lines.append(strategy_line("Original stop + full exit at 1.25R", fixed_item, "mean", "sum"))
    for mult in ("1.25", "1.5"):
        item = signal_summary["expanded_stops"][mult]
        lines.append(strategy_line(f"{mult}x stop, lot reduced, target 1.25 widened-R",
                                   item, "mean_constant_money_r", "sum_constant_money_r"))
        lines.append(strategy_line(f"{mult}x stop, lot reduced, keep original TP",
                                   item["same_original_target"],
                                   "mean_constant_money_r", "sum_constant_money_r"))
    for key, label in (("current_ladder", "Current ladder: 50% at 1.25R"),
                       ("ladder_partial_at_1r", "Diagnostic ladder: 50% at 1.0R")):
        item = signal_summary["ladders"][key]
        lines.append(strategy_line(label, item, "mean_r", "sum_r"))
    lines.extend([
        "", "## What to change first", "",
        "1. **Fix execution viability before tuning the signal.** Size from current free margin and broker "
        "order checks, not a static margin allocation. This is not reintroducing the removed account-risk "
        "cap; it prevents valid signals from becoming `No money`. Normalize/validate SL and TP against "
        "broker `stops_level`, current bid/ask, digits, and freeze level immediately before submission; "
        "retry an `Invalid stops` entry only with corrected broker-valid levels while preserving the "
        "structural stop for management.",
        "2. **Do not conclude that every stopped trade merely needed a wider stop.** Use the per-trade "
        "`required_stop_multiple` evidence. Demo-test only the predeclared 1.25x and 1.5x variants with "
        "lot size divided by the same multiplier. Never widen the stop while keeping lots unchanged.",
        "3. **Keep live fills and backtests aligned.** The current live engine enters at market after the "
        "confirmation close, while the historical strategy uses a next-bar resting stop. Build one "
        "execution model and evaluate it with spread/slippage. The actual-fill path table deliberately "
        "starts at the next complete bar to avoid using pre-fill prices.",
        "4. **Treat the ladder as unproven.** Compare full 1.25R, the current 1.25R partial, and a 1R "
        "partial over a much larger sample. A partial can improve hit-rate and reduce variance while "
        "lowering expectancy when runners fail. Do not choose it from these 16 trades alone.",
        "5. **Add portfolio controls.** Several simultaneous shorts share USD/JPY/GBP/AUD exposure and "
        "reuse the same 4H destination. Cap aggregate currency-direction exposure and permit one active "
        "campaign per destination zone; this addresses correlated loss clusters without filtering on "
        "minimum stop distance.",
        "6. **Log exact execution evidence.** Persist order request/result, ticket-to-signal linkage, every "
        "partial and SL modification, and tick bid/ask around entry/exit. The current reconciliation links "
        "fills by nearest symbol/direction because `trade_opened` linkage is absent.",
        "", "## What not to change from this sample", "",
        "Do not optimize many confirmation delays, stop multipliers, partial levels, or symbol exclusions "
        "against one trading day. Do not remove broker SL/TP, disable `S146_DEMO_ONLY`, add the declined "
        "minimum-stop-distance filter, or interpret eventual direction as tradable expectancy.",
        "", "## Method and limitations", "",
        "- Actual fills use MT5 entry price/time and the logged original structural stop. Rejected signals "
        "use trigger at signal close and are explicitly counterfactual.",
        "- For actual fills, analysis begins at the next full 5m candle. Same-candle fill-to-extreme paths "
        "are intentionally excluded.",
        "- MT5 rate bars are bid-based. Long exits use bid OHLC; short exits approximate ask by adding the "
        "bar spread times inferred FX point (0.001 JPY pairs, otherwise 0.00001). Tick history is required "
        "for exact sequencing.",
        "- If stop and target are touched in one candle, stop is assumed. Ladder simulations also check "
        "the active stop before favorable movement, avoiding optimistic intrabar ordering.",
        "- The 1.25x/1.5x simulations report both a target re-anchored to 1.25 widened-R and the "
        "original TP. Lots are reduced by the stop multiplier to preserve money risk. They do not "
        "include commissions or slippage.",
        "- Actual MT5 realized P/L remains the source of truth; OHLC simulations diagnose paths only.",
    ])
    REPORT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    recon = json.loads(RECON_PATH.read_text(encoding="utf-8"))
    signals = load_signals()
    management_events = load_management_events()
    rows = build_rows(recon, signals, management_events)
    actual_rows = [row for row in rows if row.get("mt5")]
    pnls = [float(row["mt5"]["realized_pnl"]) for row in actual_rows]
    slippages = [row["actual_slippage_r_vs_signal_trigger"] for row in actual_rows
                 if row["actual_slippage_r_vs_signal_trigger"] is not None]
    payload = {
        "generated_utc": datetime.now(UTC).isoformat(),
        "methodology": {
            "actual_start": "next complete 5m bar after MT5 fill",
            "counterfactual_start": "signal_bar_close using logged trigger",
            "short_exit_price": "bid OHLC plus bar spread times inferred FX point",
            "intrabar_policy": "stop first / conservative",
            "money_risk_policy": "expanded stop simulations divide lots by stop multiplier",
        },
        "summary": {
            "actual_mt5": {
                "count": len(actual_rows), "profits": sum(p > 0 for p in pnls),
                "losses": sum(p < 0 for p in pnls), "net_pnl": rounded(sum(pnls), 2),
                "mean_pnl": rounded(mean(pnls), 2) if pnls else None,
                "largest_winner_pnl": rounded(max(pnls), 2) if pnls else None,
                "net_pnl_excluding_largest_winner": (
                    rounded(sum(pnls) - max(pnls), 2) if pnls else None),
                "unexplained_stop_modifications": sum(
                    row["unexplained_stop_modification"] for row in actual_rows),
                "mean_slippage_r_vs_trigger": rounded(mean(slippages)) if slippages else None,
            },
            "execution_failures": dict(Counter(row["execution_failure"] for row in rows
                                                if row["execution_failure"])),
            "signal_paths": summarize_paths(rows, "signal_path"),
            "actual_fill_paths": summarize_paths(actual_rows, "actual_fill_path"),
            "by_model": group_summary(rows, "model"),
            "by_stop_basis": group_summary(rows, "stop_basis"),
        },
        "trades": rows,
    }
    JSON_OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_csv(rows)
    write_report(payload)
    print(json.dumps({
        "trades": len(rows), "actual_fills": len(actual_rows),
        "net_actual_pnl": payload["summary"]["actual_mt5"]["net_pnl"],
        "signal_path_summary": payload["summary"]["signal_paths"],
        "outputs": [str(JSON_OUT), str(CSV_OUT), str(REPORT_OUT)],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
