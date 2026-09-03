#!/usr/bin/env python3
"""CLI for the isolated S146 running-extreme fetch and raw replay."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
for item in (str(REPO), str(REPO / "liveTrade")):
    if item not in sys.path:
        sys.path.insert(0, item)

from liveTrade import config as live_config  # noqa: E402
sys.modules.setdefault("config", live_config)
try:
    from .fetch_mt5 import DATA_ROOT, default_symbols, fetch_history, parse_utc  # noqa: E402
    from .replay import replay_history  # noqa: E402
except ImportError:  # direct `py ...\\cli.py` invocation
    from fetch_mt5 import DATA_ROOT, default_symbols, fetch_history, parse_utc  # type: ignore # noqa: E402
    from replay import replay_history  # type: ignore # noqa: E402

UTC = timezone.utc
RAW_ROOT = DATA_ROOT / "raw"
RUNS_ROOT = DATA_ROOT / "runs"


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _symbols(values: list[str] | None) -> list[str]:
    source = values or default_symbols()
    result: list[str] = []
    for value in source:
        for token in value.split(","):
            symbol = token.strip().upper()
            if symbol and symbol not in result:
                result.append(symbol)
    return result


def _run_id(value: str | None, end: datetime) -> str:
    candidate = value or f"s146-{end.strftime('%Y%m%dT%H%M%SZ')}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", candidate):
        raise SystemExit("run-id must contain only safe filename characters")
    return candidate


def _manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _manifest_end(value: dict) -> datetime | None:
    raw = ((value.get("requested_score_range") or {}).get("end_utc"))
    return parse_utc(raw) if raw else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="download native closed MT5 bars read-only")
    fetch.add_argument("--start", type=parse_utc)
    fetch.add_argument("--end", type=parse_utc)
    fetch.add_argument("--days", type=int, default=365)
    fetch.add_argument("--warmup-days", type=int, default=30)
    fetch.add_argument("--symbols", nargs="+")
    fetch.add_argument("--raw-root", type=Path, default=RAW_ROOT)

    run = sub.add_parser("run", help="causal replay existing raw bars; never connects to MT5")
    run.add_argument("--start", type=parse_utc)
    run.add_argument("--end", type=parse_utc)
    run.add_argument("--days", type=int, default=365)
    run.add_argument("--run-id")
    run.add_argument("--symbols", nargs="+")
    run.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    run.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    run.add_argument("--include-portfolio-gates", type=_bool, default=True,
                     metavar="true|false")
    return parser


def _fetch(args: argparse.Namespace) -> int:
    end = (args.end or datetime.now(UTC)).replace(microsecond=0)
    start = args.start or end - timedelta(days=args.days)
    if args.days <= 0 or start >= end or args.warmup_days < 0:
        raise SystemExit("start must precede end; days must be positive; warmup-days must be non-negative")
    symbols = _symbols(args.symbols)
    fetch_history(args.raw_root, symbols, start, end, args.warmup_days, progress=print)
    print(f"Raw native data written to {args.raw_root}")
    return 0


def _run(args: argparse.Namespace) -> int:
    raw = _manifest(args.raw_root / "manifest.json")
    manifest_end = _manifest_end(raw)
    end = (args.end or manifest_end or datetime.now(UTC)).replace(microsecond=0)
    start = args.start or end - timedelta(days=args.days)
    if args.days <= 0 or start >= end:
        raise SystemExit("start must precede end and days must be positive")
    symbols = _symbols(args.symbols)
    if not symbols:
        raise SystemExit("no symbols configured")
    run_id = _run_id(args.run_id, end)
    run_dir = args.runs_root / run_id
    print(f"Replay {start.isoformat()} to {end.isoformat()} | {len(symbols)} symbol(s) | raw only")
    result = replay_history(args.raw_root, run_dir, symbols, start, end, run_id,
                            include_portfolio_gates=args.include_portfolio_gates, progress=print)
    print(f"Complete: {result['summary']['trades']} trades, {result['summary']['net_r']:.3f}R net")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _fetch(args) if args.command == "fetch" else _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
