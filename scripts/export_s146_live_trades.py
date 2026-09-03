"""Export broker-confirmed S146 trades from MT5 with strategy journal linkage."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

UTC = timezone.utc
MAGIC = 1460146
ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "liveTrade" / "logs"
DEFAULT_OUTPUT = ROOT / "data" / "s146_mt5" / "s146_live_trades_since_2026-08-19_IST.csv"


def parse_dt(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def iso(value):
    dt = parse_dt(value)
    return dt.isoformat() if dt else ""


def event_records(path: Path):
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            parts = line.rstrip("\n").split(" | ", 2)
            if len(parts) != 3:
                continue
            try:
                payload = json.loads(parts[2])
            except json.JSONDecodeError:
                continue
            payload["_event_type"] = parts[1]
            payload["_line_number"] = line_number
            records.append(payload)
    return records


def number(value):
    if value in (None, ""):
        return ""
    return value


def broker_dt(item, milliseconds=True):
    if milliseconds and getattr(item, "time_msc", 0):
        return datetime.fromtimestamp(item.time_msc / 1000, UTC)
    return datetime.fromtimestamp(item.time, UTC)


def deal_cost(deal):
    return sum(float(getattr(deal, name, 0) or 0) for name in ("profit", "swap", "commission", "fee"))


def timeline_datetimes(signal, stream):
    """Return all strategy-timeline timestamps for one timeframe."""
    values = []
    for event in signal.get("event_timeline", []) or []:
        if event.get("stream") == stream and event.get("timestamp"):
            value = iso(event.get("timestamp"))
            if value and value not in values:
                values.append(value)
    return "; ".join(values)


def timeline_field(signal, stream, field):
    """Return a source timestamp field for each matching timeline event."""
    values = []
    for event in signal.get("event_timeline", []) or []:
        if event.get("stream") != stream or not event.get(field):
            continue
        value = iso(event.get(field))
        if value and value not in values:
            values.append(value)
    return "; ".join(values)


def result_for(realized, has_exit, is_open):
    if is_open:
        return "Open"
    if not has_exit:
        return "Unknown"
    if realized > 0.005:
        return "Profit"
    if realized < -0.005:
        return "Loss"
    return "Flat"


def mt5_snapshot(start, end):
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        deals = [d for d in (mt5.history_deals_get(start, end) or [])
                 if getattr(d, "magic", 0) == MAGIC]
        orders = [o for o in (mt5.history_orders_get(start, end) or [])
                  if getattr(o, "magic", 0) == MAGIC]
        positions = {int(p.ticket): p for p in (mt5.positions_get() or [])
                     if getattr(p, "magic", 0) == MAGIC}
        return deals, orders, positions
    finally:
        mt5.shutdown()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2026-08-18T18:30:00+00:00")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    start = parse_dt(args.since)
    end = datetime.now(UTC)

    trade_log = event_records(LOG_DIR / "s146_trades.log")
    five_minute_log = event_records(LOG_DIR / "s146_5m_events.log")
    signals = {
        str(p.get("signal_id")): p
        for p in trade_log
        if p.get("_event_type") == "signal_found"
        and parse_dt(p.get("ts_utc")) >= start
    }
    all_events = [p for p in trade_log + five_minute_log
                  if parse_dt(p.get("ts_utc")) and parse_dt(p.get("ts_utc")) >= start]
    deals, orders, open_positions = mt5_snapshot(start, end)

    by_position = defaultdict(list)
    for deal in deals:
        by_position[int(getattr(deal, "position_id", 0))].append(deal)
    orders_by_position = defaultdict(list)
    for order in orders:
        orders_by_position[int(getattr(order, "position_id", 0))].append(order)

    rows = []
    for position_id, position_deals in by_position.items():
        entries = [d for d in position_deals
                   if getattr(d, "entry", None) in (mt5.DEAL_ENTRY_IN,
                                                     getattr(mt5, "DEAL_ENTRY_INOUT", 2))]
        if not entries:
            continue
        entry = min(entries, key=lambda d: d.time_msc or d.time * 1000)
        entry_time = broker_dt(entry)
        if entry_time < start:
            continue
        exits = [d for d in position_deals if d.ticket != entry.ticket and
                 getattr(d, "entry", None) in (mt5.DEAL_ENTRY_OUT,
                                                 getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
                                                 getattr(mt5, "DEAL_ENTRY_INOUT", 2))]
        exits.sort(key=lambda d: d.time_msc or d.time * 1000)
        position_orders = sorted(orders_by_position.get(position_id, []),
                                 key=lambda o: o.time_setup_msc or o.time_setup * 1000)
        entry_order = next((o for o in position_orders if o.ticket == entry.order),
                           position_orders[0] if position_orders else None)
        signal_id = ""
        order_ticket = int(getattr(entry_order, "ticket", 0) or getattr(entry, "order", 0) or 0)
        for event in all_events:
            if int(event.get("order_ticket") or 0) == order_ticket or int(event.get("deal_ticket") or 0) == int(entry.ticket):
                signal_id = str(event.get("signal_id") or "")
                if signal_id:
                    break
        signal = signals.get(signal_id, {})
        realized = round(sum(deal_cost(d) for d in exits), 2)
        broker_symbol = str(entry.symbol)
        symbol = broker_symbol.removesuffix("m")
        direction = "long" if entry.type == mt5.DEAL_TYPE_BUY else "short"
        deal_ids = {int(d.ticket) for d in position_deals}
        linked_events = []
        for event in all_events:
            event_signal = str(event.get("signal_id") or "")
            same = (event_signal and event_signal == signal_id)
            same = same or int(event.get("order_ticket") or 0) == order_ticket
            same = same or int(event.get("position_ticket") or 0) == position_id
            same = same or int(event.get("deal_ticket") or 0) in deal_ids
            if same:
                linked_events.append(event)
        event_times = {broker_dt(d).isoformat(): f"broker_deal:{d.ticket}" for d in position_deals}
        for event in linked_events:
            event_times[iso(event.get("ts_utc"))] = str(event.get("_event_type"))
        event_times = sorted((k, v) for k, v in event_times.items() if k)
        is_open = position_id in open_positions
        exit_reason = ""
        if exits and not is_open:
            last = exits[-1]
            reason = getattr(last, "reason", None)
            exit_reason = {getattr(mt5, "DEAL_REASON_TP", 5): "take_profit",
                           getattr(mt5, "DEAL_REASON_SL", 4): "stop_loss"}.get(reason,
                                                                                str(getattr(last, "comment", "")))
        row = {
            "Trade DateTime": entry_time.isoformat(),
            "Events DateTime": "; ".join(t for t, _ in event_times),
            "Result": result_for(realized, bool(exits), is_open),
            "4H Event DateTime": timeline_datetimes(signal, "4h"),
            "15m Event DateTime": timeline_datetimes(signal, "15m"),
            "5m Event DateTime": timeline_datetimes(signal, "5m"),
            "4H Origin Time": timeline_field(signal, "4h", "origin_time"),
            "15m Origin Time": timeline_field(signal, "15m", "origin_time"),
            "5m Zone Alert Origin Time": timeline_field(signal, "5m", "alert_bar_close"),
            "5m Trigger Bar Open Time": timeline_field(signal, "5m", "bar_open_time"),
            "Signal DateTime": iso(signal.get("ts_utc")),
            "Signal Bar Close": iso(signal.get("signal_bar_close")),
            "Strategy": signal.get("strategy", "s146"),
            "Symbol": signal.get("symbol", symbol),
            "Broker Symbol": broker_symbol,
            "Direction": direction,
            "Model": signal.get("model", ""),
            "Signal ID": signal_id,
            "Position ID": position_id,
            "Order Ticket": order_ticket,
            "Entry Deal Ticket": int(entry.ticket),
            "Exit Deal Tickets": ";".join(str(d.ticket) for d in exits),
            "Entry Price": number(round(float(entry.price), 8)),
            "Exit Price": number(round(float(exits[-1].price), 8)) if exits else "",
            "Lots": number(round(float(entry.volume), 8)),
            "Initial Stop": number(getattr(entry_order, "sl", None)) if entry_order else signal.get("stop", ""),
            "Initial Target": number(getattr(entry_order, "tp", None)) if entry_order else signal.get("target", ""),
            "Exit DateTime": broker_dt(exits[-1]).isoformat() if exits and not is_open else "",
            "Last Exit DateTime": broker_dt(exits[-1]).isoformat() if exits else "",
            "Exit Reason": exit_reason,
            "Realized P/L": realized if exits else "",
            "Unrealized P/L": round(float(getattr(open_positions.get(position_id), "profit", 0)), 2) if position_id in open_positions else "",
            "Reward/Risk": signal.get("reward_risk", ""),
            "Target R": signal.get("target_r_multiple", ""),
            "Loss at SL": signal.get("loss_at_sl", ""),
            "Spread": signal.get("spread_at_signal", ""),
            "Entry Zone ID": signal.get("entry_zone_id", ""),
            "Destination Zone ID": signal.get("destination_zone_id", ""),
            "Stop Basis": signal.get("stop_basis", ""),
            "Event Types": "; ".join(f"{t}={v}" for _, (t, v) in enumerate(event_times)),
            "Source": "MT5 broker history + S146 journals",
        }
        rows.append(row)

    rows.sort(key=lambda row: row["Trade DateTime"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["Trade DateTime", "Events DateTime", "Result"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    rejected = sum(1 for event in five_minute_log
                   if event.get("_event_type") == "order_rejected"
                   and parse_dt(event.get("ts_utc")) >= start)
    print(json.dumps({"output": str(output), "rows": len(rows), "rejected_signals_excluded": rejected,
                      "closed": sum(row["Result"] != "Open" for row in rows),
                      "open": sum(row["Result"] == "Open" for row in rows)}, indent=2))


if __name__ == "__main__":
    main()
