#!/usr/bin/env python3
"""Phase 1: deep read-only MT5 history for the full S146 basket.

Reuses the already-validated bar-fetch internals from
``backtests/s146_running_extreme/fetch_mt5.py`` (chunked ``copy_rates_range``,
closed-bar filter, OHLC validation, gap report, atomic CSV write) rather than
reimplementing them. The only thing added here is per-timeframe request ranges
and per-symbol isolation.

Why per-timeframe ranges: the terminal reports ``maxbars`` (100,000 on this
machine), which caps how much intraday history MT5 will serve. Asking for five
years of 5m produces thousands of empty requests against a terminal that is
also running the live engine, so each timeframe asks only for what can exist.

Read-only: historical rate APIs and symbol metadata only. No order is placed,
modified or closed.

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase1_fetch.py --probe
    .venv\\Scripts\\python.exe src\\phase1_fetch.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

paths.ensure_dirs()
paths.add_repo_to_syspath()

from backtests.s146_running_extreme.fetch_mt5 import (  # noqa: E402
    _fetch_timeframe, _gap_report, _write_csv, iso,
)
from liveTrade.mt5_client import MT5Client, mt5  # noqa: E402

paths.redirect_live_logs()  # keep every write inside KronosTest

UTC = timezone.utc

# Requested span per timeframe. 4h is limited by broker depth (~12.6y here);
# 15m and 5m are limited by the terminal's maxbars cap. Asking for slightly
# more than the cap allows confirms the true edge without wasting many calls.
DEFAULT_YEARS = {"4h": 11.0, "15m": 3.2, "5m": 1.1}


def _tf_constants() -> dict[str, int]:
    return {"4h": mt5.TIMEFRAME_H4, "15m": mt5.TIMEFRAME_M15, "5m": mt5.TIMEFRAME_M5}


def probe(symbols: list[str], years_back: int = 13) -> dict[str, Any]:
    """Report usable history per timeframe, year by year, without writing bars.

    copy_rates_from_pos returns nothing when the requested count exceeds what the
    terminal will serve, so intraday depth is measured with explicit calendar
    ranges instead.
    """
    client = MT5Client(magic=1460146)
    if not client.connect():
        raise SystemExit("MT5 read-only connection failed. Is the terminal running and logged in?")
    constants = _tf_constants()
    now = datetime.now(UTC)
    terminal = mt5.terminal_info()
    report: dict[str, Any] = {
        "probed_utc": iso(now), "years_back": years_back,
        "terminal_maxbars": getattr(terminal, "maxbars", None),
        "terminal_build": getattr(terminal, "build", None), "symbols": {},
    }
    print(f"terminal maxbars={report['terminal_maxbars']} build={report['terminal_build']}")
    try:
        for symbol in symbols:
            broker = client.resolve_symbol(symbol)
            if not broker:
                report["symbols"][symbol] = {"broker_symbol": None, "error": "not_found"}
                print(f"{symbol:<8} NOT FOUND on broker")
                continue
            entry: dict[str, Any] = {"broker_symbol": broker, "timeframes": {}}
            print(f"{symbol:<8} {broker}")
            for label in paths.TF_SECONDS:
                per_year: dict[str, int] = {}
                for offset in range(years_back):
                    year = now.year - offset
                    start = datetime(year, 1, 1, tzinfo=UTC)
                    end = min(now, datetime(year + 1, 1, 1, tzinfo=UTC))
                    if start >= now:
                        continue
                    rates = mt5.copy_rates_range(broker, constants[label], start, end)
                    per_year[str(year)] = 0 if rates is None else len(rates)
                total = sum(per_year.values())
                # A single row for a whole year is an MT5 boundary artifact, not history.
                usable = sorted(int(y) for y, n in per_year.items() if n > 10)
                entry["timeframes"][label] = {
                    "total_bars": total, "rows_per_year": per_year,
                    "earliest_year_with_data": usable[0] if usable else None,
                    "years_with_data": len(usable),
                }
                span = f"{usable[0]}-{usable[-1]}" if usable else "none"
                print(f"  {label:<4} total={total:>9,}  years={span}  ({len(usable)} yr)")
            report["symbols"][symbol] = entry
    finally:
        client.shutdown()
    out = paths.RAW / "probe_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nProbe report: {out}")
    return report


def _row_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def fetch_all(symbols: list[str], years: dict[str, float], end: datetime,
              force: bool = False, timeframes: list[str] | None = None) -> dict[str, Any]:
    """Fetch every symbol/timeframe independently with per-timeframe ranges."""
    selected = tuple(timeframes) if timeframes else tuple(paths.TF_SECONDS)
    client = MT5Client(magic=1460146)
    if not client.connect():
        raise SystemExit("MT5 read-only connection failed")
    constants = _tf_constants()
    terminal = mt5.terminal_info()

    manifest_path = paths.RAW / "manifest.json"
    combined: dict[str, Any] = {
        "schema_version": 1, "phase": "phase1_deep_history",
        "created_utc": iso(datetime.now(UTC)), "read_only": True, "no_orders_placed": True,
        "terminal_maxbars": getattr(terminal, "maxbars", None),
        "terminal_build": getattr(terminal, "build", None),
        "requested_end_utc": iso(end),
        "requested_years_per_timeframe": years,
        "chunk_max_days": 30,
        "source": "backtests/s146_running_extreme/fetch_mt5 internals",
        "symbols": {}, "failures": {},
    }
    if manifest_path.is_file() and not force:
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            combined["symbols"].update(previous.get("symbols") or {})
        except (OSError, json.JSONDecodeError):
            pass

    try:
        for index, symbol in enumerate(symbols, start=1):
            head = f"[{index}/{len(symbols)}] {symbol}"
            broker = client.resolve_symbol(symbol)
            if not broker:
                combined["failures"][symbol] = "broker_symbol_not_found"
                print(f"{head}: NOT FOUND on broker")
                continue
            info = client.symbol_info(symbol)
            entry: dict[str, Any] = {
                "canonical_symbol": symbol, "broker_symbol": broker,
                "point": float(getattr(info, "point", 0.0) or 0.0),
                "digits": int(getattr(info, "digits", 0) or 0),
                "timeframes": {},
            }
            parts: list[str] = []
            previous_tfs = (combined["symbols"].get(symbol, {}).get("timeframes") or {})
            for label, seconds in paths.TF_SECONDS.items():
                path = paths.raw_csv(symbol, label)
                start = end - timedelta(days=years[label] * 365.25)
                if label not in selected:
                    if label in previous_tfs:
                        entry["timeframes"][label] = previous_tfs[label]
                        parts.append(f"{label}=skip({previous_tfs[label].get('rows', 0):,})")
                    continue
                if not force and _row_count(path) > 0:
                    existing = previous_tfs.get(label)
                    if existing:
                        entry["timeframes"][label] = existing
                        parts.append(f"{label}=kept({existing.get('rows', 0):,})")
                        continue
                try:
                    rows, requests = _fetch_timeframe(broker, constants[label], seconds, start, end)
                except Exception as exc:
                    combined["failures"][f"{symbol}/{label}"] = f"{type(exc).__name__}: {exc}"
                    parts.append(f"{label}=FAILED")
                    continue
                _write_csv(path, rows)
                first = rows[0] if rows else None
                last = rows[-1] if rows else None
                span_years = (0.0 if not rows
                              else (int(last["time"]) - int(first["time"])) / (365.25 * 86400))
                entry["timeframes"][label] = {
                    "path": str(path.relative_to(paths.RAW)).replace("\\", "/"),
                    "rows": len(rows), "chunk_count": len(requests),
                    "requested_range": {"start_utc": iso(start), "end_utc": iso(end)},
                    "actual_range": {
                        "first_bar_open_utc": first["time_utc"] if first else None,
                        "last_bar_open_utc": last["time_utc"] if last else None,
                        "last_bar_close_utc": iso(int(last["time"]) + seconds) if last else None,
                    },
                    "actual_span_years": round(span_years, 3),
                    "requested_start_covered": bool(rows and int(first["time"]) <= int(start.timestamp()) + seconds),
                    "gaps": _gap_report(rows, seconds),
                }
                parts.append(f"{label}={len(rows):,}({span_years:.1f}y)")
            combined["symbols"][symbol] = entry
            print(f"{head}: " + "  ".join(parts))
            combined["created_utc"] = iso(datetime.now(UTC))
            manifest_path.write_text(json.dumps(combined, indent=2, allow_nan=False), encoding="utf-8")
    finally:
        client.shutdown()

    total_bars = sum(int(tf.get("rows", 0))
                     for sym in combined["symbols"].values()
                     for tf in (sym.get("timeframes") or {}).values())
    print(f"\nSymbols stored: {len(combined['symbols'])}  failures: {len(combined['failures'])}")
    print(f"Total bars on disk: {total_bars:,}")
    print(f"Combined manifest: {manifest_path}")
    return combined


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true",
                        help="report broker/terminal history depth and exit (no download)")
    parser.add_argument("--probe-years", type=int, default=13,
                        help="calendar years to look back when probing")
    parser.add_argument("--years-4h", type=float, default=DEFAULT_YEARS["4h"])
    parser.add_argument("--years-15m", type=float, default=DEFAULT_YEARS["15m"])
    parser.add_argument("--years-5m", type=float, default=DEFAULT_YEARS["5m"])
    parser.add_argument("--end", help="ISO-8601 UTC end; default now")
    parser.add_argument("--symbols", nargs="+", help="override the S146 basket")
    parser.add_argument("--timeframes", nargs="+", choices=sorted(paths.TF_SECONDS),
                        help="restrict the pass to these timeframes")
    parser.add_argument("--force", action="store_true", help="refetch timeframes already on disk")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if mt5 is None:
        raise SystemExit("MetaTrader5 package is unavailable in this interpreter")
    symbols = [s.strip().upper() for value in (args.symbols or paths.S146_BASKET)
               for s in str(value).split(",") if s.strip()]
    symbols = list(dict.fromkeys(symbols))
    if args.probe:
        probe(symbols, years_back=max(1, args.probe_years))
        return 0
    end = (datetime.fromisoformat(args.end.replace("Z", "+00:00")).astimezone(UTC)
           if args.end else datetime.now(UTC)).replace(microsecond=0)
    years = {"4h": args.years_4h, "15m": args.years_15m, "5m": args.years_5m}
    print(f"Phase 1: {len(symbols)} symbol(s), end {iso(end)}")
    print("Requested years per timeframe: "
          + ", ".join(f"{k}={v:g}" for k, v in years.items()))
    if args.timeframes:
        print(f"Timeframes this pass: {', '.join(args.timeframes)}")
    print(f"Destination {paths.RAW}\n")
    fetch_all(symbols, years, end, force=args.force, timeframes=args.timeframes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
