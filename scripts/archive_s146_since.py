#!/usr/bin/env python3
"""Create a read-only S146 trade and native MT5 bar archive."""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for item in (str(REPO), str(REPO / "liveTrade")):
    if item not in sys.path:
        sys.path.insert(0, item)

from scripts import export_s146_live_trades as trade_export  # noqa: E402
from backtests.s146_running_extreme import fetch_mt5 as bar_export  # noqa: E402

UTC = timezone.utc
MAGIC = 1460146
START_IST = datetime(2026, 8, 19, tzinfo=timezone(timedelta(hours=5, minutes=30)))
START_UTC = START_IST.astimezone(UTC)
TIMEFRAMES = {"4h": 14400, "15m": 900, "5m": 300}
SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF",
    "NZDJPY", "NZDCAD", "NZDCHF", "CADJPY", "CADCHF", "CHFJPY", "XAUUSD",
]
LOG_NAMES = (
    "engine.log", "s146_4h_events.log", "s146_15m_events.log",
    "s146_5m_events.log", "s146_trades.log",
)


def iso(value):
    dt = trade_export.parse_dt(value) if not isinstance(value, datetime) else value
    return dt.astimezone(UTC).isoformat() if dt else ""


def event_records(path: Path, source_name: str) -> list[dict]:
    records = trade_export.event_records(path)
    for record in records:
        record["_source_file"] = source_name
    return records


def load_events(log_dir: Path) -> list[dict]:
    streams = {
        "4h": log_dir / "s146_4h_events.log",
        "15m": log_dir / "s146_15m_events.log",
        "5m": log_dir / "s146_5m_events.log",
        "trade": log_dir / "s146_trades.log",
    }
    events: list[dict] = []
    for stream, path in streams.items():
        if not path.exists():
            continue
        for record in event_records(path, path.name):
            record.setdefault("_journal_stream", stream)
            events.append(record)
    return events


def event_ts(record: dict) -> datetime | None:
    value = record.get("ts_utc") or record.get("timestamp")
    try:
        return trade_export.parse_dt(value)
    except (TypeError, ValueError, OverflowError):
        return None


def supplement_position_history(deals: list, orders: list) -> tuple[list, list]:
    """Include broker exit records whose magic is not inherited from S146.

    Some broker-generated/client close records have magic=0 even though their
    position was opened by S146. Position-scoped history is the authoritative
    linkage for those records.
    """
    position_ids = {
        int(getattr(deal, "position_id", 0) or 0)
        for deal in deals
        if int(getattr(deal, "position_id", 0) or 0) > 0
    }
    if not position_ids or trade_export.mt5 is None:
        return deals, orders
    if not trade_export.mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed for position history: {trade_export.mt5.last_error()}")
    try:
        by_deal = {int(getattr(deal, "ticket", 0)): deal for deal in deals}
        by_order = {int(getattr(order, "ticket", 0)): order for order in orders}
        for position_id in position_ids:
            for deal in trade_export.mt5.history_deals_get(position=position_id) or []:
                by_deal.setdefault(int(getattr(deal, "ticket", 0)), deal)
            for order in trade_export.mt5.history_orders_get(position=position_id) or []:
                by_order.setdefault(int(getattr(order, "ticket", 0)), order)
        return list(by_deal.values()), list(by_order.values())
    finally:
        trade_export.mt5.shutdown()


def build_trade_rows(start: datetime, end: datetime, log_dir: Path) -> tuple[list[dict], list[dict]]:
    events = load_events(log_dir)
    trade_events = [event for event in events if event.get("_journal_stream") == "trade"]
    signals = {
        str(event.get("signal_id")): event
        for event in trade_events
        if event.get("_event_type") == "signal_found" and event.get("signal_id")
    }
    deals, orders, open_positions = trade_export.mt5_snapshot(start, end)
    deals, orders = supplement_position_history(deals, orders)
    by_position: dict[int, list] = {}
    for deal in deals:
        by_position.setdefault(int(getattr(deal, "position_id", 0)), []).append(deal)
    orders_by_position: dict[int, list] = {}
    for order in orders:
        orders_by_position.setdefault(int(getattr(order, "position_id", 0)), []).append(order)

    rows: list[dict] = []
    details: list[dict] = []
    in_codes = (trade_export.mt5.DEAL_ENTRY_IN,
                getattr(trade_export.mt5, "DEAL_ENTRY_INOUT", 2))
    out_codes = (trade_export.mt5.DEAL_ENTRY_OUT,
                 getattr(trade_export.mt5, "DEAL_ENTRY_OUT_BY", 3),
                 getattr(trade_export.mt5, "DEAL_ENTRY_INOUT", 2))
    for position_id, position_deals in by_position.items():
        entries = [deal for deal in position_deals if getattr(deal, "entry", None) in in_codes]
        if not entries:
            continue
        entry = min(entries, key=lambda deal: deal.time_msc or deal.time * 1000)
        entry_time = trade_export.broker_dt(entry)
        if entry_time < start or entry_time > end:
            continue
        exits = [deal for deal in position_deals
                 if deal.ticket != entry.ticket and getattr(deal, "entry", None) in out_codes]
        exits.sort(key=lambda deal: deal.time_msc or deal.time * 1000)
        position_orders = sorted(
            orders_by_position.get(position_id, []),
            key=lambda order: order.time_setup_msc or order.time_setup * 1000,
        )
        entry_order = next(
            (order for order in orders if int(getattr(order, "ticket", 0)) == int(getattr(entry, "order", 0))),
            position_orders[0] if position_orders else None,
        )
        order_ticket = int(getattr(entry_order, "ticket", 0) or getattr(entry, "order", 0) or 0)
        entry_deal_id = int(entry.ticket)
        deal_ids = {int(deal.ticket) for deal in position_deals}
        signal_id = ""
        for event in events:
            if (int(event.get("order_ticket") or 0) == order_ticket
                    or int(event.get("deal_ticket") or 0) == entry_deal_id):
                signal_id = str(event.get("signal_id") or "")
                if signal_id:
                    break
        signal = signals.get(signal_id, {})
        broker_symbol = str(entry.symbol)
        symbol = str(signal.get("symbol") or broker_symbol.removesuffix("m"))
        direction = "long" if entry.type == trade_export.mt5.DEAL_TYPE_BUY else "short"
        is_open = position_id in open_positions
        realized = round(sum(trade_export.deal_cost(deal) for deal in exits), 2)
        exit_reason = ""
        if exits and not is_open:
            last = exits[-1]
            reason = getattr(last, "reason", None)
            exit_reason = {
                getattr(trade_export.mt5, "DEAL_REASON_TP", 5): "take_profit",
                getattr(trade_export.mt5, "DEAL_REASON_SL", 4): "stop_loss",
                getattr(trade_export.mt5, "DEAL_REASON_CLIENT", 0): "client_close",
            }.get(reason, str(getattr(last, "comment", "")))
        linked: list[dict] = []
        zone_ids = {
            signal.get("entry_zone_id"), signal.get("destination_zone_id"),
        }
        for item in signal.get("event_timeline", []) or []:
            zone_ids.add(item.get("zone_id"))
        for event in events:
            same_id = str(event.get("signal_id") or "") == signal_id and signal_id
            same_ticket = (int(event.get("order_ticket") or 0) == order_ticket
                           or int(event.get("position_ticket") or 0) == position_id
                           or int(event.get("deal_ticket") or 0) in deal_ids)
            same_zone = event.get("zone_id") in zone_ids and event.get("symbol") == symbol
            if same_id or same_ticket or same_zone:
                linked.append(event)
        event_times = {trade_export.broker_dt(deal).isoformat(): f"broker_deal:{deal.ticket}"
                       for deal in position_deals}
        for event in linked:
            stamp = trade_export.parse_dt(event.get("ts_utc"))
            if stamp:
                event_times[stamp.isoformat()] = f"{event.get('_source_file')}:{event.get('_event_type')}"
        event_times = sorted(event_times.items())
        row = {
            "Trade DateTime": entry_time.isoformat(),
            "Events DateTime": "; ".join(stamp for stamp, _ in event_times),
            "Result": trade_export.result_for(realized, bool(exits), is_open),
            "4H Event DateTime": trade_export.timeline_datetimes(signal, "4h"),
            "15m Event DateTime": trade_export.timeline_datetimes(signal, "15m"),
            "5m Event DateTime": trade_export.timeline_datetimes(signal, "5m"),
            "4H Origin Time": trade_export.timeline_field(signal, "4h", "origin_time"),
            "15m Origin Time": trade_export.timeline_field(signal, "15m", "origin_time"),
            "5m Zone Alert Origin Time": trade_export.timeline_field(signal, "5m", "alert_bar_close"),
            "5m Trigger Bar Open Time": trade_export.timeline_field(signal, "5m", "bar_open_time"),
            "Signal DateTime": iso(signal.get("ts_utc")),
            "Signal Bar Close": iso(signal.get("signal_bar_close")),
            "Strategy": signal.get("strategy", "s146"), "Symbol": symbol,
            "Broker Symbol": broker_symbol, "Direction": direction,
            "Model": signal.get("model", ""), "Signal ID": signal_id,
            "Position ID": position_id, "Order Ticket": order_ticket,
            "Entry Deal Ticket": entry_deal_id,
            "Exit Deal Tickets": ";".join(str(deal.ticket) for deal in exits),
            "Entry Price": round(float(entry.price), 8),
            "Exit Price": round(float(exits[-1].price), 8) if exits else "",
            "Lots": round(float(entry.volume), 8),
            "Initial Stop": float(getattr(entry_order, "sl", 0) or 0) if entry_order else signal.get("stop", ""),
            "Initial Target": float(getattr(entry_order, "tp", 0) or 0) if entry_order else signal.get("target", ""),
            "Exit DateTime": trade_export.broker_dt(exits[-1]).isoformat() if exits and not is_open else "",
            "Last Exit DateTime": trade_export.broker_dt(exits[-1]).isoformat() if exits else "",
            "Exit Reason": exit_reason,
            "Realized P/L": realized if exits else "",
            "Unrealized P/L": round(float(getattr(open_positions[position_id], "profit", 0)), 2) if is_open else "",
            "Reward/Risk": signal.get("reward_risk", ""), "Target R": signal.get("target_r_multiple", ""),
            "Loss at SL": signal.get("loss_at_sl", ""), "Spread": signal.get("spread_at_signal", ""),
            "Entry Zone ID": signal.get("entry_zone_id", ""),
            "Destination Zone ID": signal.get("destination_zone_id", ""),
            "Stop Basis": signal.get("stop_basis", ""),
            "Event Types": "; ".join(f"{stamp}={kind}" for stamp, kind in event_times),
            "Source": "MT5 broker history + all S146 journals",
        }
        rows.append(row)
        details.append({"trade": row, "signal_record": signal, "linked_journal_events": linked,
                        "broker_deals": [deal_to_dict(deal) for deal in position_deals],
                        "broker_orders": [order_to_dict(order) for order in position_orders]})
    rows.sort(key=lambda row: row["Trade DateTime"])
    details.sort(key=lambda item: item["trade"]["Trade DateTime"])
    return rows, details


def deal_to_dict(deal) -> dict:
    names = (
        "ticket", "order", "time", "time_msc", "type", "entry", "magic", "position_id",
        "reason", "volume", "price", "profit", "swap", "commission", "fee", "symbol", "comment",
    )
    result = {}
    for name in names:
        value = getattr(deal, name, None)
        if name in {"time", "time_msc"} and value:
            result[name + "_utc"] = trade_export.broker_dt(deal).isoformat() if name == "time" else datetime.fromtimestamp(value / 1000, UTC).isoformat()
        else:
            result[name] = value
    return result


def order_to_dict(order) -> dict:
    names = (
        "ticket", "time_setup", "time_setup_msc", "time_done", "time_done_msc", "type", "state",
        "magic", "position_id", "position_by_id", "volume_initial", "volume_current", "price_open",
        "sl", "tp", "price_current", "symbol", "comment", "reason",
    )
    result = {}
    for name in names:
        value = getattr(order, name, None)
        if name.endswith("_msc") and value:
            result[name + "_utc"] = datetime.fromtimestamp(value / 1000, UTC).isoformat()
        elif name.startswith("time_") and value:
            result[name + "_utc"] = datetime.fromtimestamp(value, UTC).isoformat()
        else:
            result[name] = value
    return result


def write_trade_outputs(rows: list[dict], details: list[dict], out_dir: Path) -> dict:
    csv_path = out_dir / "s146_trades.csv"
    fields = list(rows[0]) if rows else ["Trade DateTime", "Result", "Symbol"]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    json_path = out_dir / "s146_trade_details.json"
    json_path.write_text(json.dumps({
        "schema_version": 1, "source": "MT5 broker history + all S146 journals",
        "read_only": True, "trades": details,
    }, indent=2, default=str), encoding="utf-8")
    return {"csv": str(csv_path.relative_to(out_dir)), "json": str(json_path.relative_to(out_dir)),
            "trades": len(rows)}


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return trade_export.parse_dt(value)
    except (TypeError, ValueError, OverflowError):
        return None


def first_at_or_after(values: list[int], target: int) -> int | None:
    index = bisect.bisect_left(values, target)
    return index if index < len(values) else None


def last_at_or_before(values: list[int], target: int) -> int | None:
    index = bisect.bisect_right(values, target) - 1
    return index if index >= 0 else None


def load_bar_times(path: Path) -> list[int]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [int(row["time"]) for row in csv.DictReader(handle)]


def build_trade_bar_map(rows: list[dict], bars_root: Path, end: datetime, out_dir: Path) -> dict:
    map_rows: list[dict] = []
    cache: dict[Path, list[int]] = {}
    for row in rows:
        trade_key = str(row.get("Position ID") or row.get("Order Ticket") or row.get("Entry Deal Ticket"))
        entry = parse_iso(row.get("Trade DateTime", ""))
        finish = parse_iso(row.get("Last Exit DateTime", "")) or end
        if entry is None:
            continue
        for timeframe, seconds in TIMEFRAMES.items():
            path = bars_root / str(row["Symbol"]) / timeframe / f"{row['Symbol']}_{timeframe}.csv"
            if path not in cache and path.exists():
                cache[path] = load_bar_times(path)
            values = cache.get(path, [])
            entry_open = (int(entry.timestamp()) // seconds) * seconds
            finish_open = (int(finish.timestamp()) // seconds) * seconds
            entry_index = first_at_or_after(values, entry_open)
            finish_index = last_at_or_before(values, finish_open)
            count = (finish_index - entry_index + 1) if entry_index is not None and finish_index is not None and finish_index >= entry_index else 0
            map_rows.append({
                "Trade Key": trade_key, "Position ID": row.get("Position ID", ""),
                "Order Ticket": row.get("Order Ticket", ""), "Symbol": row["Symbol"],
                "Broker Symbol": row["Broker Symbol"], "Direction": row["Direction"],
                "Result": row["Result"], "Entry Time UTC": entry.isoformat(),
                "Exit Or Archive End UTC": finish.isoformat(), "Timeframe": timeframe,
                "Bars CSV": str(path.relative_to(out_dir)).replace("\\", "/") if path.exists() else "",
                "Entry Bar Open UTC": datetime.fromtimestamp(values[entry_index], UTC).isoformat() if entry_index is not None else "",
                "Exit Bar Open UTC": datetime.fromtimestamp(values[finish_index], UTC).isoformat() if finish_index is not None else "",
                "Entry Bar Row Index": entry_index if entry_index is not None else "",
                "Exit Bar Row Index": finish_index if finish_index is not None else "",
                "Bars In Trade Window": count,
            })
    map_path = out_dir / "trade_bar_map.csv"
    fields = list(map_rows[0]) if map_rows else ["Trade Key", "Timeframe"]
    with map_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(map_rows)
    return {"path": str(map_path.relative_to(out_dir)), "rows": len(map_rows),
            "trades_mapped": len({row["Trade Key"] for row in map_rows})}


def copy_logs(log_dir: Path, out_dir: Path) -> list[str]:
    destination = out_dir / "logs"
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in LOG_NAMES:
        source = log_dir / name
        if source.exists():
            shutil.copy2(source, destination / name)
            copied.append(str((destination / name).relative_to(out_dir)).replace("\\", "/"))
    state = REPO / "liveTrade" / "passes" / "s146_active.json"
    if state.exists():
        shutil.copy2(state, out_dir / "s146_active_snapshot.json")
        copied.append("s146_active_snapshot.json")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START_UTC.isoformat(), type=trade_export.parse_dt)
    parser.add_argument("--end", default=None, type=trade_export.parse_dt)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    start = args.start.astimezone(UTC)
    end = (args.end or datetime.now(UTC)).replace(microsecond=0)
    if start >= end:
        raise SystemExit("start must precede end")
    out_dir = args.output or (REPO / "data" / "s146_mt5" / "archive_since_2026-08-19_IST")
    out_dir.mkdir(parents=True, exist_ok=True)
    bars_root = out_dir / "bars"

    print(f"Archive range UTC: {start.isoformat()} -> {end.isoformat()}")
    print(f"Archive range IST: {start.astimezone(START_IST.tzinfo).isoformat()} -> {end.astimezone(START_IST.tzinfo).isoformat()}")
    print(f"Fetching {len(SYMBOLS)} symbols x {len(TIMEFRAMES)} timeframes; read-only MT5")
    bar_manifest = bar_export.fetch_history(
        bars_root, SYMBOLS, start, end, warmup_days=0,
        progress=print,
    )
    rows, details = build_trade_rows(start, end, REPO / "liveTrade" / "logs")
    trade_outputs = write_trade_outputs(rows, details, out_dir)
    bar_map = build_trade_bar_map(rows, bars_root, end, out_dir)
    copied_logs = copy_logs(REPO / "liveTrade" / "logs", out_dir)

    manifest = {
        "schema_version": 1,
        "archive_type": "s146_trade_and_price_archive",
        "created_utc": datetime.now(UTC).isoformat(),
        "requested_start_utc": start.isoformat(),
        "requested_start_ist": start.astimezone(START_IST.tzinfo).isoformat(),
        "captured_end_utc": end.isoformat(),
        "captured_end_ist": end.astimezone(START_IST.tzinfo).isoformat(),
        "source": "MT5 native broker history + S146 journals",
        "read_only": True,
        "no_orders_placed": True,
        "magic": MAGIC,
        "symbols": SYMBOLS,
        "timeframes": {name: {"seconds": seconds, "bar_kind": "closed OHLCV + spread"}
                       for name, seconds in TIMEFRAMES.items()},
        "trade_outputs": trade_outputs,
        "trade_bar_map": bar_map,
        "journal_copies": copied_logs,
        "bar_manifest": "bars/manifest.json",
        "bar_rows": sum(
            item["timeframes"][timeframe]["rows"]
            for item in bar_manifest["symbols"].values()
            for timeframe in TIMEFRAMES
        ),
        "bar_files": len(SYMBOLS) * len(TIMEFRAMES),
        "bar_export": bar_manifest,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False, default=str), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(out_dir), "trades": len(rows),
        "closed_trades": sum(row["Result"] != "Open" for row in rows),
        "open_trades": sum(row["Result"] == "Open" for row in rows),
        "symbols": len(SYMBOLS), "timeframes": list(TIMEFRAMES),
        "bar_files": len(SYMBOLS) * len(TIMEFRAMES),
        "bar_rows": manifest["bar_rows"], "trade_bar_map_rows": bar_map["rows"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
