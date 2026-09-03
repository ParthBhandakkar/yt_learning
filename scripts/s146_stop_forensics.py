"""Tick-level forensics on s146 stop-outs.

Read-only. Answers one question the 5m bars cannot: was the stop reached by the
market, or only by the spread? MT5 triggers a SHORT stop on ASK while the
structural stop was derived from BID bar highs, so a short can be stopped at a
level the bid never traded. Long stops trigger on BID, the same basis as the
bars, so the two sides are not symmetric.

Outputs JSON/CSV under data/s146_mt5 plus a Markdown summary in docs.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

import MetaTrader5 as mt5

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "s146_mt5"
RECON = DATA / "s146_reconciliation.json"
LOG = REPO / "liveTrade" / "logs" / "s146_trades.log"
JSON_OUT = DATA / "s146_stop_forensics.json"
CSV_OUT = DATA / "s146_stop_forensics.csv"
DOC_OUT = REPO / "docs" / "s146_stop_forensics.md"
UTC = timezone.utc
MAGIC = 1460146
EPS = 1e-12


def parse_ts(value) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def load_signals() -> dict:
    out = {}
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(" | ", 2)
        if len(parts) != 3:
            continue
        try:
            event = json.loads(parts[2])
        except json.JSONDecodeError:
            continue
        if event.get("type") != "signal_found" or event.get("strategy") != "s146":
            continue
        key = (event["symbol"], event["direction"],
               int(parse_ts(event["signal_bar_open"]).timestamp()))
        out[key] = event
    return out


def resolve(symbol: str) -> str | None:
    for candidate in (symbol + "m", symbol, symbol + ".m"):
        if mt5.symbol_select(candidate, True):
            return candidate
    return None


def atr(rates, periods: int = 14) -> float | None:
    """Wilder-style average true range over the last `periods` closed bars."""
    if rates is None or len(rates) < periods + 1:
        return None
    trs = []
    for index in range(1, len(rates)):
        high = float(rates[index]["high"])
        low = float(rates[index]["low"])
        prev_close = float(rates[index - 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    window = trs[-periods:]
    return sum(window) / len(window) if window else None


def tick_window(broker_sym: str, start: datetime, end: datetime):
    flag = getattr(mt5, "COPY_TICKS_ALL", 3)
    ticks = mt5.copy_ticks_range(broker_sym, start, end, flag)
    return ticks if ticks is not None and len(ticks) else None


def tick_extremes(ticks) -> dict | None:
    """Bid/ask extremes and spread statistics for a tick window."""
    if ticks is None:
        return None
    bids = [float(t["bid"]) for t in ticks if float(t["bid"]) > 0]
    asks = [float(t["ask"]) for t in ticks if float(t["ask"]) > 0]
    if not bids or not asks:
        return None
    spreads = [a - b for a, b in zip(asks, bids) if a > 0 and b > 0]
    return {
        "ticks": len(ticks),
        "bid_min": min(bids), "bid_max": max(bids),
        "ask_min": min(asks), "ask_max": max(asks),
        "spread_min": min(spreads), "spread_max": max(spreads),
        "spread_mean": mean(spreads),
    }


def first_trigger(ticks, direction: str, stop: float) -> dict | None:
    """Exact stop trigger: ask >= stop for a short, bid <= stop for a long.

    Also records the opposite-side extreme up to that moment, which is what
    shows whether the market itself reached the level.
    """
    if ticks is None:
        return None
    long_side = direction == "long"
    other_extreme = None
    for tick in ticks:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        if bid <= 0 or ask <= 0:
            continue
        probe = bid if long_side else ask
        other = ask if long_side else bid
        other_extreme = other if other_extreme is None else (
            max(other_extreme, other) if not long_side else min(other_extreme, other))
        hit = probe <= stop + EPS if long_side else probe >= stop - EPS
        if hit:
            stamp = int(tick["time_msc"]) / 1000.0 if "time_msc" in ticks.dtype.names else float(tick["time"])
            return {
                "trigger_time_utc": datetime.fromtimestamp(stamp, UTC).isoformat(),
                "trigger_bid": bid, "trigger_ask": ask,
                "spread_at_trigger": ask - bid,
                "opposite_side_extreme_before_trigger": other_extreme,
            }
    return None


def analyse(comparison: dict, signal: dict, now: datetime) -> dict:
    symbol = comparison["symbol"]
    direction = comparison["direction"]
    broker_sym = resolve(symbol)
    stop = float(signal["stop"])
    trigger = float(signal["trigger"])
    mt5_row = comparison.get("mt5")
    signal_close = parse_ts(signal["signal_bar_close"])
    if mt5_row:
        entry_time = parse_ts(mt5_row["entry_time_utc"])
        entry_price = float(mt5_row["entry_price"])
        basis = "actual_fill"
        end = parse_ts(mt5_row["exit_time_utc"]) + timedelta(minutes=2) if mt5_row.get("exit_time_utc") else now
    else:
        entry_time = signal_close
        entry_price = trigger
        basis = "counterfactual_trigger"
        end = min(entry_time + timedelta(hours=12), now)

    si = mt5.symbol_info(broker_sym) if broker_sym else None
    point = float(getattr(si, "point", 0.0) or 0.0) if si else 0.0
    risk = (entry_price - stop) if direction == "long" else (stop - entry_price)

    # Volatility context measured strictly BEFORE the signal.
    m5 = mt5.copy_rates_range(broker_sym, mt5.TIMEFRAME_M5,
                              signal_close - timedelta(hours=8), signal_close) if broker_sym else None
    m15 = mt5.copy_rates_range(broker_sym, mt5.TIMEFRAME_M15,
                               signal_close - timedelta(hours=48), signal_close) if broker_sym else None
    atr5 = atr(m5)
    atr15 = atr(m15)

    entry_ticks = tick_window(broker_sym, entry_time - timedelta(minutes=1),
                              entry_time + timedelta(minutes=1)) if broker_sym else None
    entry_stats = tick_extremes(entry_ticks)
    spread_at_entry = entry_stats["spread_mean"] if entry_stats else None

    path_ticks = tick_window(broker_sym, entry_time, end) if broker_sym else None
    path_stats = tick_extremes(path_ticks)
    trigger_info = first_trigger(path_ticks, direction, stop)

    # Did the market itself ever reach the structural stop over the whole window?
    # For a short the ask always crosses the level before the bid, so the only
    # meaningful test is whether the BID (the traded/bar basis) got there at all.
    market_reached = None
    spread_only_stop = None
    if path_stats is not None:
        # A long's stop triggers on bid, which is also the bar basis, so the
        # trigger and the market are the same event: a long is never spread-only.
        market_reached = (path_stats["bid_min"] <= stop + EPS) if direction == "long" \
            else (path_stats["bid_max"] >= stop - EPS)
        if trigger_info is not None:
            spread_only_stop = (direction == "short") and not market_reached
    # How much of the intended risk the spread consumes: a short's stop fires one
    # spread early in bid terms, so the effective risk is shorter than intended.
    effective_risk = risk - (spread_at_entry or 0.0) if direction == "short" else risk
    return {
        "trade_number": comparison["trade_number"], "symbol": symbol,
        "direction": direction, "broker_symbol": broker_sym,
        "execution_basis": basis, "model": signal.get("model"),
        "stop_basis": signal.get("stop_basis"),
        "entry_time_utc": entry_time.isoformat(), "entry_price": entry_price,
        "structural_stop": stop, "risk_price": risk,
        "risk_points": (risk / point) if point else None,
        "point": point,
        "spread_at_entry": spread_at_entry,
        "spread_pct_of_risk": (spread_at_entry / risk * 100.0) if (spread_at_entry and risk > 0) else None,
        "effective_risk_after_spread": effective_risk,
        "effective_risk_pct_of_intended": (effective_risk / risk * 100.0) if risk > 0 else None,
        "round_trip_cost_r": ((2.0 * spread_at_entry) / risk) if (spread_at_entry and risk > 0) else None,
        "atr_5m_before_signal": atr5,
        "atr_15m_before_signal": atr15,
        "risk_over_atr5m": (risk / atr5) if (atr5 and atr5 > 0) else None,
        "risk_over_atr15m": (risk / atr15) if (atr15 and atr15 > 0) else None,
        "alert_to_confirmation_minutes": round(
            (signal_close - parse_ts(signal["alert_bar_close"])).total_seconds() / 60.0, 1),
        "zone_to_confirmation_minutes": round(
            (signal_close - parse_ts(signal["entry_zone_confirmed_at"])).total_seconds() / 60.0, 1),
        "tick_path": path_stats, "stop_trigger": trigger_info,
        "market_reached_structural_stop": market_reached,
        "stop_caused_by_spread_only": spread_only_stop,
        "destination_target": signal.get("destination_target"),
        "destination_reward_risk": signal.get("destination_reward_risk"),
        "actual_result": comparison.get("actual_result"),
        "actual_pnl": (mt5_row or {}).get("realized_pnl"),
        "broker_initial_sl": (mt5_row or {}).get("initial_order_sl"),
    }


def simulate_padded(rows: list[dict], now: datetime) -> dict:
    """Outcome when the SHORT stop is padded by the spread it triggers on.

    A short's stop fires on ask, so padding by the entry spread makes the
    bid-equivalent stop match the structural level. Lots are scaled by
    1/multiplier so the money risked is unchanged.
    """
    variants = {}
    for pad_mult in (0.0, 1.0, 1.5, 2.0):
        results = []
        for row in rows:
            symbol = row["symbol"]
            broker_sym = row["broker_symbol"]
            direction = row["direction"]
            spread = row["spread_at_entry"] or 0.0
            entry = row["entry_price"]
            base_risk = row["risk_price"]
            if not broker_sym or base_risk is None or base_risk <= 0:
                continue
            # Longs trigger on bid, the same basis as the bars, so no pad.
            pad = spread * pad_mult if direction == "short" else 0.0
            stop = row["structural_stop"] + pad if direction == "short" else row["structural_stop"]
            risk = (entry - stop) if direction == "long" else (stop - entry)
            if risk <= 0:
                continue
            mult = risk / base_risk
            target = entry + 1.25 * base_risk if direction == "long" else entry - 1.25 * base_risk
            start = parse_ts(row["entry_time_utc"])
            end = min(start + timedelta(hours=24), now)
            rates = mt5.copy_rates_range(broker_sym, mt5.TIMEFRAME_M5, start, end)
            outcome, r_value = "censored", None
            for rate in rates if rates is not None else []:
                if int(rate["time"]) < int(start.timestamp()):
                    continue
                spread_price = float(rate["spread"]) * (row["point"] or 0.0)
                if direction == "long":
                    adverse_hit = float(rate["low"]) <= stop
                    favour_hit = float(rate["high"]) >= target
                else:
                    adverse_hit = float(rate["high"]) + spread_price >= stop
                    favour_hit = float(rate["low"]) + spread_price <= target
                if adverse_hit or favour_hit:
                    if adverse_hit:
                        outcome, r_value = "stop_first", -1.0 * mult
                    else:
                        outcome, r_value = "target_first", 1.25
                    break
            results.append({"trade_number": row["trade_number"], "symbol": symbol,
                            "direction": direction, "stop_multiple": round(mult, 3),
                            "outcome": outcome,
                            "money_r": None if r_value is None else round(r_value / mult, 4)})
        resolved = [item["money_r"] for item in results if item["money_r"] is not None]
        wins = sum(1 for item in results if item["outcome"] == "target_first")
        variants[str(pad_mult)] = {
            "trades": len(results), "target_first": wins,
            "stop_first": sum(1 for item in results if item["outcome"] == "stop_first"),
            "censored": sum(1 for item in results if item["outcome"] == "censored"),
            "sum_money_r": round(sum(resolved), 3) if resolved else None,
            "mean_money_r": round(mean(resolved), 4) if resolved else None,
            "per_trade": results,
        }
    return variants


def simulate_exit_grid(rows: list[dict], now: datetime) -> dict:
    """Net expectancy for a small predeclared grid of stop pads and targets.

    Money-R is net of a round-trip spread cost, which is the cost the 1.25R exit
    has to clear. Shorts are resolved on ask (bar high plus bar spread) so the
    stop and target use the prices that actually trigger.
    """
    pads = (0.0, 1.5)
    targets = ("1.0R", "1.25R", "1.5R", "2.0R", "3.0R", "destination")
    grid = {}
    cache: dict[tuple[str, int], object] = {}
    for pad_mult in pads:
        for target_name in targets:
            results = []
            for row in rows:
                broker_sym = row["broker_symbol"]
                base_risk = row["risk_price"]
                if not broker_sym or not base_risk or base_risk <= 0:
                    continue
                direction = row["direction"]
                entry = row["entry_price"]
                spread = row["spread_at_entry"] or 0.0
                point = row["point"] or 0.0
                pad = spread * pad_mult if direction == "short" else 0.0
                stop = row["structural_stop"] + pad if direction == "short" else row["structural_stop"]
                risk = (entry - stop) if direction == "long" else (stop - entry)
                if risk <= 0:
                    continue
                mult = risk / base_risk
                if target_name == "destination":
                    destination = row.get("destination_target")
                    if not destination:
                        continue
                    target = float(destination)
                else:
                    r_mult = float(target_name.rstrip("R"))
                    target = (entry + r_mult * base_risk) if direction == "long" \
                        else (entry - r_mult * base_risk)
                start = parse_ts(row["entry_time_utc"])
                end = min(start + timedelta(hours=24), now)
                key = (broker_sym, int(start.timestamp()))
                if key not in cache:
                    cache[key] = mt5.copy_rates_range(broker_sym, mt5.TIMEFRAME_M5, start, end)
                rates = cache[key]
                outcome, gross = "censored", None
                for rate in rates if rates is not None else []:
                    if int(rate["time"]) < int(start.timestamp()):
                        continue
                    bar_spread = float(rate["spread"]) * point
                    if direction == "long":
                        adverse = float(rate["low"]) <= stop
                        favour = float(rate["high"]) >= target
                    else:
                        adverse = float(rate["high"]) + bar_spread >= stop
                        favour = float(rate["low"]) + bar_spread <= target
                    if adverse or favour:
                        if adverse:
                            outcome, gross = "stop_first", -risk
                        else:
                            outcome, gross = "target_first", abs(target - entry)
                        break
                if gross is None:
                    results.append({"trade_number": row["trade_number"], "outcome": outcome,
                                    "money_r": None})
                    continue
                # Constant money risk: lots are divided by the stop multiple, so a
                # stop always costs 1.0 money-R and a win is scaled the same way.
                denominator = mult * base_risk
                net_money_r = ((gross - 2.0 * spread) / denominator) if gross > 0 \
                    else (gross / denominator)
                results.append({"trade_number": row["trade_number"], "symbol": row["symbol"],
                                "outcome": outcome, "stop_multiple": round(mult, 3),
                                "money_r": round(net_money_r, 4)})
            resolved = [item["money_r"] for item in results if item["money_r"] is not None]
            grid[f"pad{pad_mult}x_target{target_name}"] = {
                "trades": len(results),
                "target_first": sum(1 for i in results if i["outcome"] == "target_first"),
                "stop_first": sum(1 for i in results if i["outcome"] == "stop_first"),
                "censored": sum(1 for i in results if i["outcome"] == "censored"),
                "sum_net_money_r": round(sum(resolved), 3) if resolved else None,
                "mean_net_money_r": round(mean(resolved), 4) if resolved else None,
            }
    return grid


def simulate_cost_filter(rows: list[dict], now: datetime) -> dict:
    """Skip signals whose live spread is a large share of the intended risk.

    The trade has to clear roughly two spreads before it earns anything, and a
    short's stop is one spread tighter than intended, so a high spread-to-risk
    ratio removes the edge before the market moves. Thresholds are predeclared.
    """
    grid = {}
    for threshold in (None, 0.35, 0.30, 0.25, 0.20):
        for pad_mult in (0.0, 1.5):
            for target_r in (1.25, 1.5):
                kept, skipped = [], []
                for row in rows:
                    ratio = row.get("round_trip_cost_r")
                    cost_share = (row.get("spread_pct_of_risk") or 0.0) / 100.0
                    if threshold is not None and cost_share > threshold:
                        skipped.append(row["trade_number"])
                        continue
                    kept.append(row)
                results = _resolve_variant(kept, now, pad_mult, target_r)
                resolved = [item for item in results if item is not None]
                grid[f"cost<={threshold}_pad{pad_mult}x_tp{target_r}R"] = {
                    "kept": len(kept), "skipped": skipped,
                    "resolved": len(resolved),
                    "sum_net_money_r": round(sum(resolved), 3) if resolved else None,
                    "mean_net_money_r": round(mean(resolved), 4) if resolved else None,
                    "wins": sum(1 for value in resolved if value > 0),
                }
    return grid


def _resolve_variant(rows: list[dict], now: datetime, pad_mult: float,
                     target_r: float) -> list[float | None]:
    out = []
    for row in rows:
        broker_sym = row["broker_symbol"]
        base_risk = row["risk_price"]
        if not broker_sym or not base_risk or base_risk <= 0:
            continue
        direction = row["direction"]
        entry = row["entry_price"]
        spread = row["spread_at_entry"] or 0.0
        point = row["point"] or 0.0
        pad = spread * pad_mult if direction == "short" else 0.0
        stop = row["structural_stop"] + pad if direction == "short" else row["structural_stop"]
        risk = (entry - stop) if direction == "long" else (stop - entry)
        if risk <= 0:
            continue
        mult = risk / base_risk
        target = (entry + target_r * base_risk) if direction == "long" \
            else (entry - target_r * base_risk)
        start = parse_ts(row["entry_time_utc"])
        end = min(start + timedelta(hours=24), now)
        rates = mt5.copy_rates_range(broker_sym, mt5.TIMEFRAME_M5, start, end)
        value = None
        for rate in rates if rates is not None else []:
            if int(rate["time"]) < int(start.timestamp()):
                continue
            bar_spread = float(rate["spread"]) * point
            if direction == "long":
                adverse = float(rate["low"]) <= stop
                favour = float(rate["high"]) >= target
            else:
                adverse = float(rate["high"]) + bar_spread >= stop
                favour = float(rate["low"]) + bar_spread <= target
            if adverse or favour:
                gross = -risk if adverse else abs(target - entry)
                denominator = mult * base_risk
                value = ((gross - 2.0 * spread) / denominator) if gross > 0 else gross / denominator
                break
        out.append(round(value, 4) if value is not None else None)
    return out


def write_outputs(payload: dict) -> None:
    JSON_OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    fields = ["trade_number", "symbol", "direction", "execution_basis", "stop_basis",
              "risk_points", "spread_at_entry", "spread_pct_of_risk", "risk_over_atr5m",
              "risk_over_atr15m", "alert_to_confirmation_minutes",
              "market_reached_structural_stop", "stop_caused_by_spread_only",
              "actual_result", "actual_pnl"]
    with CSV_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in payload["trades"]:
            writer.writerow(row)

    summary = payload["summary"]
    spread_only = [row for row in payload["trades"] if row["stop_caused_by_spread_only"]]
    lines = [
        "# s146 stop forensics (tick level)", "",
        f"Generated: `{payload['generated_utc']}`. Source: MT5 tick history, magic `{MAGIC}`.", "",
        "## What the ticks show", "",
        f"- Stop triggers reconstructed from ticks: {summary['stop_triggers_found']} of {summary['trades']} trades.",
        f"- Stops where the market itself never reached the structural level (spread-only stop-outs): "
        f"**{summary['spread_only_stops']}**.",
        f"- Median spread as a share of the structural risk: **{summary['median_spread_pct_of_risk']}%** "
        f"(max {summary['max_spread_pct_of_risk']}%).",
        f"- Median structural risk vs 5m ATR before the signal: **{summary['median_risk_over_atr5m']}x**.",
        "",
        "A short's stop fires on ASK, but the stop level was derived from BID bar highs, so a short is "
        "stopped roughly one spread earlier than the level intended. Long stops fire on BID, the same "
        "basis as the bars, so longs do not have this bias.", "",
        "## Per-trade evidence", "",
        "| # | Symbol | Dir | Basis | Risk (points) | Spread at entry | Spread % of risk | Risk / 5m ATR | Market hit stop? | Spread-only stop | Broker P/L |",
        "|---:|---|---|---|---:|---:|---:|---:|---|---|---:|",
    ]
    for row in payload["trades"]:
        lines.append(
            f"| {row['trade_number']} | {row['symbol']} | {row['direction']} | {row['execution_basis']} | "
            f"{_fmt(row['risk_points'], 1)} | {_fmt(row['spread_at_entry'], 6)} | "
            f"{_fmt(row['spread_pct_of_risk'], 1)} | {_fmt(row['risk_over_atr5m'], 2)} | "
            f"{row['market_reached_structural_stop']} | {row['stop_caused_by_spread_only']} | "
            f"{row['actual_pnl'] if row['actual_pnl'] is not None else '—'} |")
    if spread_only:
        lines += ["", "### Spread-only stop-outs", ""]
        for row in spread_only:
            info = row["stop_trigger"] or {}
            lines.append(
                f"- **{row['symbol']} {row['direction']}** (#{row['trade_number']}): stop `{row['structural_stop']}`, "
                f"ask reached `{_fmt(info.get('trigger_ask'), 5)}` while bid only reached "
                f"`{_fmt(info.get('opposite_side_extreme_before_trigger'), 5)}`; "
                f"spread at trigger `{_fmt(info.get('spread_at_trigger'), 6)}`.")
    lines += ["", "## Spread-padded stop simulation (constant money risk)", "",
              "The short stop is padded by `mult x entry spread`; lots are divided by the same stop "
              "multiplier so the money at risk is unchanged. Target stays at the original 1.25R distance.", "",
              "| Pad multiple | Target first | Stop first | Censored | Sum money-R | Mean money-R |",
              "|---|---:|---:|---:|---:|---:|"]
    for name, item in payload["padded_stop_simulation"].items():
        lines.append(f"| {name}x spread | {item['target_first']} | {item['stop_first']} | "
                     f"{item['censored']} | {item['sum_money_r']} | {item['mean_money_r']} |")
    lines += ["", "## Cost gate simulation (the fix that is being adopted)", "",
              "Skip a signal when the live spread is more than the stated share of the intended risk. "
              "Money-R is net of a round-trip spread and held at constant money risk.", "",
              "| Variant | Kept | Wins | Sum net money-R | Mean net money-R | Skipped trades |",
              "|---|---:|---:|---:|---:|---|"]
    for name, item in payload["cost_filter_simulation"].items():
        skipped = ", ".join(str(value) for value in item["skipped"]) or "—"
        lines.append(f"| {name} | {item['kept']} | {item['wins']} | {item['sum_net_money_r']} | "
                     f"{item['mean_net_money_r']} | {skipped} |")
    lines += ["", "## Limitations", "",
              "- 16 signals from one trading day; treat every number as descriptive.",
              "- Counterfactual rows never reached the broker and are labelled as such.",
              "- The simulation uses 5m bars for path resolution after the tick-verified stop question, "
              "assumes stop-before-target inside a bar, and excludes commission and slippage.",
              "- Padding widens risk in price terms, so lots must fall to keep money risk constant. "
              "This is not a minimum stop-distance signal filter: no signal is rejected."]
    DOC_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt(value, digits: int) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def main() -> int:
    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        return 1
    try:
        now = datetime.now(UTC)
        recon = json.loads(RECON.read_text(encoding="utf-8"))
        signals = load_signals()
        rows = []
        for comparison in sorted(recon["comparisons"], key=lambda item: item["trade_number"]):
            key = (comparison["symbol"], comparison["direction"],
                   int(parse_ts(comparison["manual_signal_open_utc"]).timestamp()))
            signal = signals.get(key)
            if signal is None:
                continue
            rows.append(analyse(comparison, signal, now))
        spread_pcts = [r["spread_pct_of_risk"] for r in rows if r["spread_pct_of_risk"]]
        atr_ratios = [r["risk_over_atr5m"] for r in rows if r["risk_over_atr5m"]]
        spread_pcts_sorted = sorted(spread_pcts)
        atr_sorted = sorted(atr_ratios)
        payload = {
            "generated_utc": now.isoformat(), "magic": MAGIC,
            "summary": {
                "trades": len(rows),
                "stop_triggers_found": sum(1 for r in rows if r["stop_trigger"]),
                "spread_only_stops": sum(1 for r in rows if r["stop_caused_by_spread_only"]),
                "median_spread_pct_of_risk": round(spread_pcts_sorted[len(spread_pcts_sorted) // 2], 1) if spread_pcts_sorted else None,
                "max_spread_pct_of_risk": round(max(spread_pcts), 1) if spread_pcts else None,
                "median_risk_over_atr5m": round(atr_sorted[len(atr_sorted) // 2], 2) if atr_sorted else None,
                "shorts": sum(1 for r in rows if r["direction"] == "short"),
                "longs": sum(1 for r in rows if r["direction"] == "long"),
            },
            "trades": rows,
            "padded_stop_simulation": simulate_padded(rows, now),
            "exit_grid_net_of_cost": simulate_exit_grid(rows, now),
            "cost_filter_simulation": simulate_cost_filter(rows, now),
        }
        write_outputs(payload)
        print(json.dumps({"summary": payload["summary"],
                          "padded": {k: {kk: vv for kk, vv in v.items() if kk != "per_trade"}
                                     for k, v in payload["padded_stop_simulation"].items()},
                          "cost_filter": payload["cost_filter_simulation"],
                          "outputs": [str(JSON_OUT), str(CSV_OUT), str(DOC_OUT)]},
                         indent=2, default=str))
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
