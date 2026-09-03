#!/usr/bin/env python3
"""Read-only native MT5 H4/M15/M5 downloader for S146."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[2]
for item in (str(REPO), str(REPO / "liveTrade")):
    if item not in sys.path:
        sys.path.insert(0, item)

from liveTrade import config as live_config  # noqa: E402
# liveTrade modules use top-level imports when run from liveTrade itself.
sys.modules.setdefault("config", live_config)
from liveTrade.mt5_client import MT5Client, mt5  # noqa: E402

UTC = timezone.utc
DATA_ROOT = REPO / "data" / "s146_running_extreme"
FIELDS = ["time_utc", "time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
TF_SECONDS = {"4h": 14400, "15m": 900, "5m": 300}


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def iso(value: datetime | int | float | None) -> str | None:
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromtimestamp(float(value), UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def default_symbols() -> list[str]:
    """Resolve the current configured basket at invocation time."""
    return [str(symbol).strip().upper() for symbol in live_config.CONFIG.symbols if str(symbol).strip()]


def _chunks(start: datetime, end: datetime) -> Iterable[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        boundary = min(end, cursor + timedelta(days=30))
        yield cursor, boundary
        cursor = boundary


def _rate_value(row: Any, name: str, default: float = 0) -> Any:
    try:
        return row[name]
    except (KeyError, TypeError, ValueError, IndexError):
        return default


def _fetch_timeframe(broker: str, mt5_tf: int, seconds: int, start: datetime,
                     end: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 is required for fetch; raw replay does not require it")
    by_time: dict[int, dict[str, Any]] = {}
    requests: list[dict[str, Any]] = []
    closed_cutoff = min(end, datetime.now(UTC))
    for chunk_start, chunk_end in _chunks(start, end):
        rates = mt5.copy_rates_range(broker, mt5_tf, chunk_start, chunk_end)
        count = 0 if rates is None else len(rates)
        requests.append({"start_utc": iso(chunk_start), "end_utc": iso(chunk_end), "rows_received": count})
        if rates is None:
            raise RuntimeError(f"copy_rates_range failed for {broker}: {mt5.last_error()}")
        for row in rates:
            stamp = int(_rate_value(row, "time", 0) or 0)
            if stamp <= 0 or stamp < int(start.timestamp()) or stamp + seconds > int(closed_cutoff.timestamp()):
                continue
            values = [float(_rate_value(row, name, math.nan)) for name in ("open", "high", "low", "close")]
            if not all(math.isfinite(value) for value in values) or values[1] < values[2]:
                continue
            by_time[stamp] = {
                "time_utc": iso(stamp), "time": stamp, "open": values[0], "high": values[1],
                "low": values[2], "close": values[3],
                "tick_volume": int(_rate_value(row, "tick_volume", 0) or 0),
                "spread": int(_rate_value(row, "spread", 0) or 0),
                "real_volume": int(_rate_value(row, "real_volume", 0) or 0),
            }
    return [by_time[key] for key in sorted(by_time)], requests


def _gap_report(rows: list[dict[str, Any]], seconds: int) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    missing = 0
    for previous, current in zip(rows, rows[1:]):
        delta = int(current["time"]) - int(previous["time"])
        if delta > seconds:
            absent = max(0, delta // seconds - 1)
            missing += absent
            if len(gaps) < 500:
                gaps.append({"after_utc": previous["time_utc"], "before_utc": current["time_utc"],
                             "seconds": delta, "missing_intervals": absent})
    return {"count": len(gaps), "missing_intervals": missing, "details": gaps,
            "details_truncated": missing > len(gaps)}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def fetch_history(raw_root: Path, symbols: list[str], start: datetime, end: datetime,
                  warmup_days: int = 30, progress=print) -> dict[str, Any]:
    """Fetch closed native bars through MT5Client without any trading calls."""
    if mt5 is None:
        raise RuntimeError("MetaTrader5 is not installed; use run with an existing raw folder")
    client = MT5Client(magic=1460146)
    if not client.connect():
        raise RuntimeError("MT5 read-only connection failed")
    fetch_start = start - timedelta(days=max(0, warmup_days))
    manifest: dict[str, Any] = {
        "schema_version": 2, "created_utc": iso(datetime.now(UTC)), "read_only": True,
        "no_orders_placed": True, "requested_score_range": {"start_utc": iso(start), "end_utc": iso(end)},
        "requested_fetch_range": {"start_utc": iso(fetch_start), "end_utc": iso(end)},
        "warmup_days": warmup_days, "chunk_max_days": 30, "timeframes": list(TF_SECONDS), "symbols": {},
    }
    try:
        constants = {"4h": mt5.TIMEFRAME_H4, "15m": mt5.TIMEFRAME_M15, "5m": mt5.TIMEFRAME_M5}
        for canonical in symbols:
            symbol = canonical.strip().upper()
            progress(f"fetch {symbol}: resolving")
            broker = client.resolve_symbol(symbol)
            if not broker:
                raise RuntimeError(f"Broker symbol not found for {symbol}")
            info = client.symbol_info(symbol)
            if info is None:
                raise RuntimeError(f"Broker symbol info unavailable for {symbol}")
            entry: dict[str, Any] = {"canonical_symbol": symbol, "broker_symbol": broker,
                                     "point": float(getattr(info, "point", 0.0) or 0.0),
                                     "digits": int(getattr(info, "digits", 0) or 0), "timeframes": {}}
            for label, seconds in TF_SECONDS.items():
                rows, requests = _fetch_timeframe(broker, constants[label], seconds, fetch_start, end)
                path = raw_root / symbol / label / f"{symbol}_{label}.csv"
                _write_csv(path, rows)
                requested_seconds = max(1, int((end - fetch_start).total_seconds()))
                actual_seconds = 0 if not rows else int(rows[-1]["time"]) + seconds - int(rows[0]["time"])
                entry["timeframes"][label] = {
                    "path": str(path.relative_to(raw_root)).replace("\\", "/"), "rows": len(rows),
                    "chunk_count": len(requests), "chunk_requests": requests,
                    "requested_range": {"start_utc": iso(fetch_start), "end_utc": iso(end)},
                    "actual_range": {"first_bar_open_utc": rows[0]["time_utc"] if rows else None,
                                     "last_bar_open_utc": rows[-1]["time_utc"] if rows else None,
                                     "last_bar_close_utc": iso(int(rows[-1]["time"]) + seconds) if rows else None},
                    "coverage": {"calendar_fraction": min(1.0, actual_seconds / requested_seconds),
                                 "score_start_covered": bool(rows and int(rows[0]["time"]) <= int(start.timestamp())),
                                 "score_end_covered": bool(rows and int(rows[-1]["time"]) + seconds >= int(end.timestamp()))},
                    "gaps": _gap_report(rows, seconds),
                }
                progress(f"fetch {symbol} {label}: {len(rows):,} closed rows")
            manifest["symbols"][symbol] = entry
    finally:
        client.shutdown()
    raw_root.mkdir(parents=True, exist_ok=True)
    (raw_root / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_utc)
    parser.add_argument("--end", type=parse_utc)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--raw-root", type=Path, default=DATA_ROOT / "raw")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    end = (args.end or datetime.now(UTC)).replace(microsecond=0)
    start = args.start or end - timedelta(days=args.days)
    if start >= end or args.days <= 0 or args.warmup_days < 0:
        raise SystemExit("start must precede end; days must be positive; warmup-days must be non-negative")
    symbols = [s.upper() for value in (args.symbols or default_symbols()) for s in value.split(",") if s.strip()]
    fetch_history(args.raw_root, symbols, start, end, args.warmup_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
