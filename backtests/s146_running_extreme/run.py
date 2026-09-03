#!/usr/bin/env python3
"""Fetch and/or run the one-year causal S146 running-extreme replay."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIVE = REPO / "liveTrade"
for item in (str(REPO), str(LIVE)):
    if item not in sys.path:
        sys.path.insert(0, item)

from liveTrade import config as live_config  # noqa: E402
sys.modules["config"] = live_config
from liveTrade.run import DEFAULT_SYMBOLS  # noqa: E402
from backtests.s146_running_extreme.fetch_mt5 import fetch_history  # noqa: E402
from backtests.s146_running_extreme.replay import replay_history  # noqa: E402

UTC = timezone.utc
DATA_ROOT = Path(r"d:\WorkZera\Projects\strategies\yt_learning\data\s146_running_extreme")
RAW_ROOT = DATA_ROOT / "raw"
RUNS_ROOT = DATA_ROOT / "runs"


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def _symbols(values: list[str] | None) -> list[str]:
    source = values if values else list(DEFAULT_SYMBOLS["s146"])
    result: list[str] = []
    for value in source:
        for symbol in value.split(","):
            canonical = symbol.strip().upper()
            if canonical and canonical not in result:
                result.append(canonical)
    return result


def _run_id(value: str | None, end: datetime) -> str:
    candidate = value or f"s146-{end.strftime('%Y%m%dT%H%M%SZ')}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", candidate):
        raise SystemExit("run-id must be 1-100 safe filename characters")
    return candidate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch-only", action="store_true", help="download raw bars and stop")
    mode.add_argument("--replay-only", action="store_true", help="use existing raw bars without MT5")
    parser.add_argument("--start", type=parse_utc, help="score start, ISO-8601 UTC")
    parser.add_argument("--end", type=parse_utc, help="score end, ISO-8601 UTC (default: now)")
    parser.add_argument("--days", type=int, default=365, help="score days when --start is omitted")
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", help="canonical symbols; comma or space separated")
    parser.add_argument("--run-id", help="output folder name under data\\s146_running_extreme\\runs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    end = (args.end or datetime.now(UTC)).replace(microsecond=0)
    start = args.start or end - timedelta(days=args.days)
    if start >= end:
        raise SystemExit("--start must precede --end")
    if args.days <= 0 or args.warmup_days < 0:
        raise SystemExit("--days must be positive and --warmup-days non-negative")
    symbols = _symbols(args.symbols)
    if not symbols:
        raise SystemExit("at least one symbol is required")
    run_id = _run_id(args.run_id, end)
    print(f"S146 range {start.isoformat()} to {end.isoformat()} | {len(symbols)} symbol(s)")

    if not args.replay_only:
        print(f"Fetching native closed bars with {args.warmup_days} warmup day(s) into {RAW_ROOT}")
        fetch_history(RAW_ROOT, symbols, start, end, args.warmup_days, progress=print)
    if args.fetch_only:
        print(f"Fetch complete: {RAW_ROOT / 'manifest.json'}")
        return 0

    run_dir = RUNS_ROOT / run_id
    print(f"Replaying sequentially into {run_dir}")
    result = replay_history(RAW_ROOT, run_dir, symbols, start, end, run_id, progress=print)
    summary = result["summary"]
    print(f"Complete: {summary['total_trades']} trades, {summary['net_r']:.3f}R net")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
