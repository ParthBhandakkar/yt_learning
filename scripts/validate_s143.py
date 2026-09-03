#!/usr/bin/env python3
"""Reproducible multi-symbol walk-forward validator for Strategy 143.

For each symbol's common 4H/5m span, the first 60% is TRAIN, the next
20% is VALIDATION, and the latest 20% is SEALED. Development runs pass
only candles ending before the sealed boundary to the strategy.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategies"))

import core
import strategy_143_mtf_supply_demand_reversal as s143

H4_SECONDS = 4 * 60 * 60
M5_SECONDS = 5 * 60

# Symbol, 4H input, 5m input, Google Drive ID (None means local-only).
UNIVERSE = (
    ("EURUSD", ROOT / "data/EURUSD/4h/EURUSD_4h.csv", ROOT / "dashboard/out/_tmp/s143_full_EURUSD_5m.csv", "1q-8BGa182ki16uruEWZYre2owmQvyM7g"),
    ("GBPUSD", ROOT / "data/GBPUSD/4h/GBPUSD_4h.csv", ROOT / "data/GBPUSD/5m/GBPUSD_5m.csv", None),
    ("AUDUSD", ROOT / "data/AUDUSD/4h/AUDUSD_4h.csv", ROOT / "dashboard/out/_tmp/s143_full_AUDUSD_5m.csv", "15muKybRfO4dIechoc4QZybb_05y-oidw"),
    ("NZDUSD", ROOT / "data/NZDUSD/4h/NZDUSD_4h.csv", ROOT / "dashboard/out/_tmp/s143_full_NZDUSD_5m.csv", "1sOe0aTKsHLn-IOItGuKYR1H_lcSo2T-j"),
    ("USDCAD", ROOT / "data/USDCAD/4h/USDCAD_4h.csv", ROOT / "dashboard/out/_tmp/s143_full_USDCAD_5m.csv", "17BSKoWxRJCbhu80DyO9fAsWiQ2w2d-N_"),
    ("USDCHF", ROOT / "data/USDCHF/4h/USDCHF_4h.csv", ROOT / "dashboard/out/_tmp/s143_full_USDCHF_5m.csv", "19m6kqBiSA0mTrjXmRZ_KEeZQMlvbfE6W"),
    ("USDJPY", ROOT / "data/USDJPY/4h/USDJPY_4h.csv", ROOT / "dashboard/out/_tmp/s143_full_USDJPY_5m.csv", "15JIkWOUt_z7mran8eh4uI7ow34jYiopf"),
    ("XAUUSD", ROOT / "data/XAUUSD/4h/XAUUSD_4h.csv", ROOT / "dashboard/out/_tmp/s143_full_XAUUSD_5m.csv", "1IZp86Ml3_Bggg8SB_HsLtwDT7wBump3s"),
)

CANDLE_CACHE: dict[Path, list[core.Candle]] = {}
PARAM_DEFAULTS = s143.Params()
PARAM_FIELDS = {field.name: getattr(PARAM_DEFAULTS, field.name) for field in fields(PARAM_DEFAULTS)}


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def download_missing() -> None:
    pending = [(symbol, path, file_id) for symbol, _, path, file_id in UNIVERSE
               if file_id is not None and not _nonempty(path)]
    if not pending:
        print("All downloadable 5m inputs are already present and non-empty.")
        return
    try:
        import gdown
    except ImportError as exc:
        raise RuntimeError("--download requires the installed gdown package") from exc

    for symbol, path, file_id in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {symbol} 5m -> {path}", flush=True)
        result = gdown.download(id=file_id, output=str(path), quiet=False)
        if result is None or not _nonempty(path):
            raise RuntimeError(f"gdown did not produce a non-empty file for {symbol}: {path}")


def require_inputs() -> None:
    missing: list[str] = []
    for symbol, h4_path, m5_path, _ in UNIVERSE:
        if not _nonempty(h4_path):
            missing.append(f"{symbol} 4H: {h4_path}")
        if not _nonempty(m5_path):
            missing.append(f"{symbol} 5m: {m5_path}")
    if missing:
        raise FileNotFoundError("Missing or empty inputs:\n  " + "\n  ".join(missing))


def candles(path: Path) -> list[core.Candle]:
    key = path.resolve()
    if key not in CANDLE_CACHE:
        loaded = core.load_csv(str(key))
        CANDLE_CACHE[key] = sorted(loaded, key=lambda candle: candle.timestamp)
    return CANDLE_CACHE[key]


def common_histories(
    h4: list[core.Candle], m5: list[core.Candle]
) -> tuple[list[core.Candle], list[core.Candle], int, int]:
    if not h4 or not m5:
        raise ValueError("4H and 5m histories must both be non-empty")
    start = max(h4[0].timestamp, m5[0].timestamp)
    end = min(h4[-1].timestamp + H4_SECONDS, m5[-1].timestamp + M5_SECONDS)
    if end <= start:
        raise ValueError("4H and 5m histories have no common timestamp span")
    common_h4 = [bar for bar in h4 if bar.timestamp >= start and bar.timestamp + H4_SECONDS <= end]
    common_m5 = [bar for bar in m5 if bar.timestamp >= start and bar.timestamp + M5_SECONDS <= end]
    if not common_h4 or not common_m5:
        raise ValueError("No complete candles exist in the common timestamp span")
    return common_h4, common_m5, start, end


def net_r(trade: dict[str, Any], symbol: str) -> float:
    entry = float(trade["entry_price"])
    risk_price = float(trade["risk_price"])
    if not math.isfinite(risk_price) or risk_price <= 0:
        raise ValueError(f"invalid risk_price for {symbol}: {risk_price!r}")
    return float(trade["gross_R"]) - core.round_turn_cost_price(entry, symbol=symbol) / risk_price


def metrics(trades: list[dict[str, Any]]) -> dict[str, float | int]:
    values = [float(trade["_net_R"]) for trade in trades]
    if not values:
        return {"n": 0, "win": 0.0, "expectancy": 0.0, "pf": 0.0,
                "total": 0.0, "avg_win": 0.0, "avg_loss": 0.0}
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    pf = gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)
    return {
        "n": len(values),
        "win": len(wins) / len(values),
        "expectancy": sum(values) / len(values),
        "pf": pf,
        "total": sum(values),
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
    }


def run(params: s143.Params, *, include_sealed: bool) -> dict[str, dict[str, list[dict[str, Any]]]]:
    bucket_names = ["train", "validation"] + (["sealed"] if include_sealed else [])
    result = {name: {symbol: [] for symbol, *_ in UNIVERSE} for name in bucket_names}

    for symbol, h4_path, m5_path, _ in UNIVERSE:
        h4, m5, start, end = common_histories(candles(h4_path), candles(m5_path))
        span = end - start
        train_cut = start + (span * 60) // 100
        sealed_cut = start + (span * 80) // 100

        if include_sealed:
            strategy_h4, strategy_m5 = h4, m5
        else:
            # Use candle close times, not opens: no bar containing sealed OHLC may
            # reach generate_trades. Strictness also prevents a boundary entry.
            strategy_h4 = [bar for bar in h4 if bar.timestamp + H4_SECONDS < sealed_cut]
            strategy_m5 = [bar for bar in m5 if bar.timestamp + M5_SECONDS < sealed_cut]

        trades = s143.generate_trades(strategy_h4, strategy_m5, symbol=symbol, params=params)
        for trade in trades:
            entry_time = int(trade["entry_timestamp"])
            if not include_sealed and entry_time >= sealed_cut:
                raise RuntimeError(f"{symbol}: sealed trade generated during a normal run")
            if entry_time < train_cut:
                bucket = "train"
            elif entry_time < sealed_cut:
                bucket = "validation"
            else:
                bucket = "sealed"
            if bucket == "sealed" and not include_sealed:
                raise RuntimeError(f"{symbol}: attempted to evaluate sealed output")
            trade["_net_R"] = net_r(trade, symbol)
            result[bucket][symbol].append(trade)

        print(
            f"{symbol}: common={_utc(start)}..{_utc(end)} "
            f"train_cut={_utc(train_cut)} sealed_cut={_utc(sealed_cut)}",
            flush=True,
        )
    return result


def _utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def print_bucket(name: str, by_symbol: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, float | int], int]:
    pooled = [trade for symbol, *_ in UNIVERSE for trade in by_symbol[symbol]]
    summary = metrics(pooled)
    print(
        f"{name:10s} n={summary['n']:>4} net_win={summary['win']:>6.1%} "
        f"expectancy={summary['expectancy']:+.3f}R PF={summary['pf']:.2f} "
        f"total={summary['total']:+.2f}R "
        f"avg_win/loss={summary['avg_win']:+.3f}/{summary['avg_loss']:+.3f}R"
    )
    positive = 0
    for symbol, *_ in UNIVERSE:
        item = metrics(by_symbol[symbol])
        positive += item["total"] > 0
        print(
            f"  {symbol:7s} n={item['n']:>3} net_win={item['win']:>6.1%} "
            f"expectancy={item['expectancy']:+.3f}R PF={item['pf']:.2f} "
            f"total={item['total']:+.2f}R "
            f"avg_win/loss={item['avg_win']:+.3f}/{item['avg_loss']:+.3f}R"
        )
    print(f"  positive-symbol breadth: {positive}/{len(UNIVERSE)}")
    return summary, positive


def evaluate(params: s143.Params, *, sealed: bool, label: str) -> None:
    print(f"\n=== {label} ===")
    print("params=" + json.dumps(asdict(params), sort_keys=True, separators=(",", ":")))
    result = run(params, include_sealed=sealed)
    print_bucket("TRAIN", result["train"])
    print_bucket("VALIDATION", result["validation"])
    if sealed:
        print("\n*** SEALED OPENED — DO NOT RETUNE AFTER VIEWING ***")
        print_bucket("SEALED", result["sealed"])


def parse_value(key: str, text: str) -> int | float:
    if key not in PARAM_FIELDS:
        raise ValueError(f"unknown Strategy 143 parameter: {key}")
    raw = text.strip()
    if not raw:
        raise ValueError(f"missing value for parameter: {key}")
    default = PARAM_FIELDS[key]
    try:
        if isinstance(default, bool):
            lowered = raw.lower()
            if lowered not in {"true", "false"}:
                raise ValueError("expected true or false")
            return lowered == "true"
        if isinstance(default, int):
            return int(raw)
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value
    except ValueError as exc:
        raise ValueError(f"invalid value for {key}: {raw!r} ({exc})") from exc


def parse_set(text: str) -> dict[str, int | float]:
    updates: dict[str, int | float] = {}
    if not text.strip():
        return updates
    for assignment in text.split(","):
        if "=" not in assignment:
            raise ValueError(f"invalid --set item (expected key=value): {assignment!r}")
        key, raw = assignment.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("empty parameter name in --set")
        if key in updates:
            raise ValueError(f"duplicate --set parameter: {key}")
        updates[key] = parse_value(key, raw)
    return updates


def parse_sweep(text: str) -> tuple[str, list[int | float]]:
    if text.count("=") != 1:
        raise ValueError("--sweep must have exactly one dimension: key=v1|v2")
    key, raw_values = text.split("=", 1)
    key = key.strip()
    if key not in PARAM_FIELDS:
        raise ValueError(f"unknown Strategy 143 parameter: {key}")
    parts = raw_values.split("|")
    if len(parts) < 2 or any(not part.strip() for part in parts):
        raise ValueError("--sweep requires at least two non-empty values: key=v1|v2")
    return key, [parse_value(key, part) for part in parts]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reproducible 60/20/20 multi-symbol validator for Strategy 143"
    )
    parser.add_argument("--set", default="", metavar="KEY=VALUE,...",
                        help="override validated Params fields")
    parser.add_argument("--sweep", default="", metavar="KEY=V1|V2",
                        help="one-dimensional Params sweep")
    parser.add_argument("--sealed", action="store_true",
                        help="open and report the latest 20%% using full histories")
    parser.add_argument("--download", action="store_true",
                        help="use gdown to fetch only missing/empty Drive-backed 5m inputs")
    args = parser.parse_args()

    if args.sealed and args.sweep:
        parser.error("--sealed cannot be combined with any --sweep")
    try:
        updates = parse_set(args.set)
        sweep = parse_sweep(args.sweep) if args.sweep else None
    except ValueError as exc:
        parser.error(str(exc))

    if args.download:
        try:
            download_missing()
        except (OSError, RuntimeError) as exc:
            parser.error(str(exc))
    try:
        require_inputs()
    except FileNotFoundError as exc:
        parser.error(str(exc))

    base = replace(PARAM_DEFAULTS, **updates)
    if sweep is None:
        evaluate(base, sealed=args.sealed, label="defaults" if not updates else "custom set")
        return

    key, values = sweep
    for value in values:
        params = replace(base, **{key: value})
        evaluate(params, sealed=False, label=f"{key}={value}")


if __name__ == "__main__":
    main()
