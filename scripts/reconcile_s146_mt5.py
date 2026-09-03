"""Export MT5 bars and reconcile the supplied s146 manual trade table.

Read-only: connects to MT5, filters magic 1460146, writes under data/s146_mt5,
and never overwrites the existing data/<SYMBOL> historical feeds.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

REPO = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(REPO / "liveTrade"))
from mt5_client import MT5Client

OUT = REPO / "data" / "s146_mt5"
MAGIC = 1460146
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
MANUAL_ROWS = [
    {"n": 1, "result": "Profit", "symbol": "USDCAD", "direction": "long", "signal_open": "2026-08-17T11:10:00+00:00", "trigger": 1.386049298, "stop": 1.385475723, "target": 1.386766266, "rr": 1.25, "lots": .19, "loss_at_sl": 823.46, "margin": 994.72},
    {"n": 2, "result": "Profit", "symbol": "GBPUSD", "direction": "short", "signal_open": "2026-08-17T12:05:00+00:00", "trigger": 1.355762208, "stop": 1.356267811, "target": 1.355130204, "rr": 1.25, "lots": .14, "loss_at_sl": 741.33, "margin": 975.21},
    {"n": 3, "result": "Profit", "symbol": "EURUSD", "direction": "short", "signal_open": "2026-08-17T13:10:00+00:00", "trigger": 1.159442024, "stop": 1.160263011, "target": 1.158415791, "rr": 1.25, "lots": .17, "loss_at_sl": 462.91, "margin": 1013.49},
    {"n": 4, "result": "Profit", "symbol": "NZDJPY", "direction": "short", "signal_open": "2026-08-17T13:40:00+00:00", "trigger": 94.192290159, "stop": 94.293714459, "target": 94.06550978, "rr": 1.25, "lots": .33, "loss_at_sl": 1201.33, "margin": 1002.92},
    {"n": 5, "result": "Profit", "symbol": "AUDUSD", "direction": "short", "signal_open": "2026-08-17T14:20:00+00:00", "trigger": .711994396, "stop": .7126606313, "target": .7111616019, "rr": 1.25, "lots": .27, "loss_at_sl": 886.86, "margin": 988.99},
    {"n": 6, "result": "Profit", "symbol": "GBPAUD", "direction": "long", "signal_open": "2026-08-17T14:55:00+00:00", "trigger": 1.905845281, "stop": 1.903674812, "target": 1.908558365, "rr": 1.25, "lots": .14, "loss_at_sl": 2268.17, "margin": 995.93},
    {"n": 7, "result": "Profit", "symbol": "AUDJPY", "direction": "short", "signal_open": "2026-08-17T16:50:00+00:00", "trigger": 113.32233341, "stop": 113.4256711, "target": 113.1931613, "rr": 1.25, "lots": .27, "loss_at_sl": 841.07, "margin": 988.98},
    {"n": 8, "result": "Profit", "symbol": "CADJPY", "direction": "short", "signal_open": "2026-08-17T17:35:00+00:00", "trigger": 114.9522518, "stop": 115.0232509, "target": 114.8635028, "rr": 1.25, "lots": .27, "loss_at_sl": 1264.31, "margin": 1002.21},
    {"n": 9, "result": "Loss", "symbol": "NZDCHF", "direction": "short", "signal_open": "2026-08-17T17:50:00+00:00", "trigger": .478536072, "stop": .478883943, "target": .4781012333, "rr": 1.25, "lots": .33, "loss_at_sl": 1488.99, "margin": 1002.71},
    {"n": 10, "result": "Profit", "symbol": "AUDNZD", "direction": "short", "signal_open": "2026-08-17T18:15:00+00:00", "trigger": 1.203709812, "stop": 1.204350215, "target": 1.202909308, "rr": 1.25, "lots": .27, "loss_at_sl": 1074.31, "margin": 987.86},
    {"n": 11, "result": "Loss", "symbol": "GBPCAD", "direction": "long", "signal_open": "2026-08-17T18:30:00+00:00", "trigger": 1.878393912, "stop": 1.877551118, "target": 1.879447405, "rr": 1.25, "lots": .14, "loss_at_sl": 894.12, "margin": 997.24},
    {"n": 12, "result": "Loss", "symbol": "EURAUD", "direction": "long", "signal_open": "2026-08-17T18:50:00+00:00", "trigger": 1.629391466, "stop": 1.628598566, "target": 1.630382591, "rr": 1.25, "lots": .16, "loss_at_sl": 947.78, "margin": 974.29},
    {"n": 13, "result": "Profit", "symbol": "USDCAD", "direction": "long", "signal_open": "2026-08-17T21:15:00+00:00", "trigger": 1.387329361, "stop": 1.386630665, "target": 1.388202729, "rr": 1.25, "lots": .19, "loss_at_sl": 1007.68, "margin": 999.99},
    {"n": 14, "result": "Loss", "symbol": "GBPJPY", "direction": "short", "signal_open": "2026-08-17T22:15:00+00:00", "trigger": 215.9312028, "stop": 215.9947992, "target": 215.8517072, "rr": 1.25, "lots": .14, "loss_at_sl": 587.58, "margin": 975.96},
    {"n": 15, "result": "Profit", "symbol": "CHFJPY", "direction": "short", "signal_open": "2026-08-17T22:15:00+00:00", "trigger": 196.5581715, "stop": 196.6648328, "target": 196.4248449, "rr": 1.25, "lots": .16, "loss_at_sl": 1126.31, "margin": 1015.31},
    {"n": 16, "result": "Profit", "symbol": "CADJPY", "direction": "short", "signal_open": "2026-08-17T22:20:00+00:00", "trigger": 114.8962549, "stop": 114.9367466, "target": 114.8456403, "rr": 1.25, "lots": .27, "loss_at_sl": 719.96, "margin": 999.50},
]

def parse_ts(value) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def epoch(value) -> int:
    return int(parse_ts(value).timestamp())


def fmt(value) -> str:
    return parse_ts(value).isoformat()


def native_value(row, field):
    value = row[field]
    return value.item() if hasattr(value, "item") else value


def export_bars(client: MT5Client, now: datetime) -> list[dict]:
    starts: dict[str, datetime] = {}
    for trade in MANUAL_ROWS:
        signal = parse_ts(trade["signal_open"])
        start = signal.replace(hour=0, minute=0, second=0, microsecond=0)
        starts[trade["symbol"]] = min(starts.get(trade["symbol"], start), start)

    manifest = []
    for symbol, start in sorted(starts.items()):
        broker_symbol = client.resolve_symbol(symbol)
        if broker_symbol is None:
            manifest.append({"symbol": symbol, "status": "not_found"})
            continue
        for tf, mt5_tf, minutes in (("5m", mt5.TIMEFRAME_M5, 5), ("15m", mt5.TIMEFRAME_M15, 15)):
            rates = mt5.copy_rates_range(broker_symbol, mt5_tf, start, now)
            rows = []
            for rate in rates if rates is not None else []:
                stamp = int(native_value(rate, "time"))
                close_time = datetime.fromtimestamp(stamp, UTC) + timedelta(minutes=minutes)
                if close_time > now:
                    continue
                dt_ist = close_time - timedelta(minutes=minutes) + IST.utcoffset(now)
                rows.append({
                    "time_utc": datetime.fromtimestamp(stamp, UTC).isoformat(),
                    "Day_IST": dt_ist.date().isoformat(),
                    "Time_IST": dt_ist.time().isoformat(),
                    "time": stamp,
                    "open": native_value(rate, "open"),
                    "high": native_value(rate, "high"),
                    "low": native_value(rate, "low"),
                    "close": native_value(rate, "close"),
                    "tick_volume": native_value(rate, "tick_volume"),
                    "spread": native_value(rate, "spread"),
                    "real_volume": native_value(rate, "real_volume"),
                })
            folder = OUT / symbol / tf
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{symbol}_{tf}.csv"
            fields = ["time_utc", "Day_IST", "Time_IST", "time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            manifest.append({
                "symbol": symbol, "broker_symbol": broker_symbol, "timeframe": tf,
                "start_requested_utc": start.isoformat(), "end_utc": now.isoformat(),
                "rows": len(rows), "first_bar_utc": rows[0]["time_utc"] if rows else None,
                "last_bar_utc": rows[-1]["time_utc"] if rows else None,
                "path": str(path.relative_to(REPO)),
            })
    return manifest


def load_trade_events() -> tuple[list[dict], list[dict]]:
    events = []
    path = REPO / "liveTrade" / "logs" / "s146_trades.log"
    if not path.exists():
        return [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(" | ", 2)
        if len(parts) != 3:
            continue
        try:
            payload = json.loads(parts[2])
        except json.JSONDecodeError:
            continue
        events.append(payload)
    signals = [e for e in events if e.get("type") == "signal_found"]
    opened = [e for e in events if e.get("type") in ("trade_opened", "opened")]
    return signals, opened

def get_history(now: datetime):
    start = min(parse_ts(t["signal_open"]) for t in MANUAL_ROWS) - timedelta(hours=2)
    deals = list(mt5.history_deals_get(start, now) or [])
    deals = [d for d in deals if getattr(d, "magic", 0) == MAGIC]
    orders = list(mt5.history_orders_get(start, now) or [])
    orders = [o for o in orders if getattr(o, "magic", 0) == MAGIC]
    by_position: dict[int, list] = defaultdict(list)
    for deal in deals:
        by_position[int(getattr(deal, "position_id", 0))].append(deal)
    order_by_position: dict[int, list] = defaultdict(list)
    for order in orders:
        order_by_position[int(getattr(order, "position_id", 0))].append(order)
    groups = []
    for position_id, position_deals in sorted(by_position.items()):
        entries = [d for d in position_deals if getattr(d, "entry", None) in (mt5.DEAL_ENTRY_IN, getattr(mt5, "DEAL_ENTRY_INOUT", 2))]
        if not entries:
            continue
        entry = min(entries, key=lambda d: d.time)
        exits = [d for d in position_deals if d.ticket != entry.ticket and getattr(d, "entry", None) in (mt5.DEAL_ENTRY_OUT, getattr(mt5, "DEAL_ENTRY_OUT_BY", 3), getattr(mt5, "DEAL_ENTRY_INOUT", 2))]
        entry_time = datetime.fromtimestamp(entry.time, UTC)
        symbol = str(entry.symbol).removesuffix("m")
        direction = "long" if entry.type == mt5.DEAL_TYPE_BUY else "short"
        realized = sum(float(getattr(d, "profit", 0) or 0) + float(getattr(d, "swap", 0) or 0) + float(getattr(d, "commission", 0) or 0) for d in exits)
        order_rows = sorted(order_by_position.get(position_id, []), key=lambda o: o.time_setup)
        initial_order = order_rows[0] if order_rows else None
        groups.append({
            "position_id": position_id, "symbol": symbol, "broker_symbol": entry.symbol,
            "direction": direction, "entry_time_utc": entry_time.isoformat(),
            "entry_price": float(entry.price), "entry_lots": float(entry.volume),
            "entry_deal": int(entry.ticket), "status": "closed" if exits else "open",
            "exit_time_utc": datetime.fromtimestamp(max(d.time for d in exits), UTC).isoformat() if exits else None,
            "exit_price": float(exits[-1].price) if exits else None,
            "realized_pnl": round(realized, 2),
            "exit_reason": str(getattr(exits[-1], "comment", "")) if exits else None,
            "initial_order_sl": float(getattr(initial_order, "sl", 0) or 0) if initial_order else None,
            "initial_order_tp": float(getattr(initial_order, "tp", 0) or 0) if initial_order else None,
            "deal_count": len(position_deals),
        })
    return groups, deals


def normalize_event_time(value):
    return epoch(value) if value is not None else None


def first_manual_hit(manual: dict, start_value=None):
    path = OUT / manual["symbol"] / "5m" / f'{manual["symbol"]}_5m.csv'
    if not path.exists():
        return {"status": "no_bars"}
    start_dt = parse_ts(start_value or manual["signal_open"])
    # OHLC cannot sequence events inside a candle; use its containing 5m bar.
    signal_ts = (int(start_dt.timestamp()) // 300) * 300
    long = manual["direction"] == "long"
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            stamp = int(row["time"])
            if stamp < signal_ts:
                continue
            high = float(row["high"])
            low = float(row["low"])
            target_hit = high >= manual["target"] if long else low <= manual["target"]
            stop_hit = low <= manual["stop"] if long else high >= manual["stop"]
            if target_hit or stop_hit:
                if target_hit and stop_hit:
                    status = "target_and_stop_same_bar"
                elif target_hit:
                    status = "target_first"
                else:
                    status = "stop_first"
                return {"status": status, "bar_open_utc": datetime.fromtimestamp(stamp, UTC).isoformat(), "high": high, "low": low}
    return {"status": "neither_hit_in_export"}


def reconcile(groups: list[dict], signals: list[dict], opened: list[dict]) -> list[dict]:
    results = []
    for manual in MANUAL_ROWS:
        signal_ts = epoch(manual["signal_open"])
        symbol = manual["symbol"]
        direction = manual["direction"]
        exact_signals = [s for s in signals if s.get("symbol") == symbol and s.get("direction") == direction and normalize_event_time(s.get("signal_bar_open")) == signal_ts]
        exact_opened = [o for o in opened if o.get("symbol") == symbol and o.get("direction") == direction and normalize_event_time(o.get("signal_bar_open")) == signal_ts]
        position_id = None
        if exact_opened:
            position_id = int(exact_opened[-1].get("position_ticket") or 0) or None
        group = next((g for g in groups if position_id and g["position_id"] == position_id), None)
        if group is None and exact_signals:
            # A signal can be logged without a broker fill; only use a close-time
            # match as a fallback and mark it as inferred rather than exact.
            candidates = [g for g in groups if g["symbol"] == symbol and g["direction"] == direction]
            candidates = [g for g in candidates if abs(epoch(g["entry_time_utc"]) - signal_ts) <= 3600]
            if candidates:
                group = min(candidates, key=lambda g: abs(epoch(g["entry_time_utc"]) - signal_ts))
        actual_result = None
        if group:
            actual_result = "Profit" if group["realized_pnl"] > 0 else ("Loss" if group["realized_pnl"] < 0 else "Flat")
        row = {
            "trade_number": manual["n"], "manual_result": manual["result"], "symbol": symbol,
            "direction": direction, "manual_signal_open_utc": manual["signal_open"],
            "manual_trigger": manual["trigger"], "manual_stop": manual["stop"], "manual_target": manual["target"],
            "manual_lots": manual["lots"], "manual_loss_at_sl": manual["loss_at_sl"],
            "manual_est_margin": manual["margin"], "manual_bar_first_hit": first_manual_hit(manual, group["entry_time_utc"] if group else None),
            "signal_log_match": bool(exact_signals),
            "opened_log_match": bool(exact_opened), "mt5": group,
            "actual_result": actual_result,
            "result_matches": actual_result == manual["result"] if actual_result else None,
        }
        results.append(row)
    return results


def write_report(manifest, groups, comparisons, now):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bar_export_manifest.json").write_text(json.dumps({
        "generated_utc": now.isoformat(), "source": "MT5", "magic": MAGIC, "files": manifest,
    }, indent=2), encoding="utf-8")
    (OUT / "s146_mt5_history.json").write_text(json.dumps({
        "generated_utc": now.isoformat(), "magic": MAGIC, "positions": groups,
    }, indent=2), encoding="utf-8")
    (OUT / "s146_reconciliation.json").write_text(json.dumps({
        "generated_utc": now.isoformat(), "manual_rows": MANUAL_ROWS, "comparisons": comparisons,
    }, indent=2), encoding="utf-8")
    lines = [
        "# s146 MT5 reconciliation",
        "",
        f"Generated: {now.isoformat()} | magic: `{MAGIC}`",
        "",
        "MT5 is the source of truth for actual fills, broker exits, and realized P/L. "
        "The manual result column reflects the supplied chart/table result.",
        "",
        "| # | Symbol | Dir | Manual result | 5m bars: manual levels | MT5 status/result | Entry UTC | Entry price | Exit price | Realized P/L | Exit/comment | Match |",
        "|---:|---|---|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for row in comparisons:
        g = row["mt5"] or {}
        lines.append("| {trade_number} | {symbol} | {direction} | {manual_result} | {manual_hit} | {status} / {actual_result} | {entry_time_utc} | {entry_price} | {exit_price} | {realized_pnl} | {exit_reason} | {result_matches} |".format(
            trade_number=row["trade_number"], symbol=row["symbol"], direction=row["direction"],
            manual_result=row["manual_result"], manual_hit=row["manual_bar_first_hit"].get("status", "—"),
            status=g.get("status", "no MT5 match"),
            actual_result=row["actual_result"] or "—", entry_time_utc=g.get("entry_time_utc", "—"),
            entry_price=g.get("entry_price", "—"), exit_price=g.get("exit_price", "—"),
            realized_pnl=g.get("realized_pnl", "—"), exit_reason=g.get("exit_reason", "—"),
            result_matches=row["result_matches"] if row["result_matches"] is not None else "—"))
    (OUT / "s146_reconciliation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    now = datetime.now(UTC)
    client = MT5Client(magic=MAGIC)
    if not client.connect():
        print("MT5 connection failed")
        return 1
    try:
        manifest = export_bars(client, now)
        signals, opened = load_trade_events()
        groups, deals = get_history(now)
        comparisons = reconcile(groups, signals, opened)
        write_report(manifest, groups, comparisons, now)
        print(json.dumps({
            "bars": {"files": len(manifest), "rows": sum(int(x.get("rows", 0)) for x in manifest)},
            "mt5_positions": len(groups), "mt5_deals": len(deals),
            "manual_rows": len(MANUAL_ROWS),
            "matched_signal_logs": sum(x["signal_log_match"] for x in comparisons),
            "matched_mt5": sum(bool(x["mt5"]) for x in comparisons),
            "output": str(OUT),
        }, indent=2))
    finally:
        client.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
