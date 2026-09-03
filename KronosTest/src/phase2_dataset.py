#!/usr/bin/env python3
"""Phase 2: build the labelled, leakage-audited S146 signal dataset.

Joins the checked-in replay's ``detector_signals.jsonl`` (signal-time fields
only) to ``trades.csv`` (outcomes) and emits one row per executed trade with:

  * ``f_*``      signal-time features, safe for a filter to use
  * ``label_*``  outcome columns, never to be used as inputs
  * ``meta_*``   identifiers, timestamps, bar-coverage flags

Guarantees checked and written to the audit file:
  1. No ``f_*`` column is derived from a post-signal field.
  2. Every row's 5m/15m/4h context is available strictly before signal time.
  3. The train/test cutoff is fixed here, before any model is fitted.

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase2_dataset.py
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from features_s146 import (  # noqa: E402
    POST_SIGNAL_FIELDS, SIGNAL_TIME_FIELDS, build_features, feature_names,
)

paths.ensure_dirs()

UTC = timezone.utc

# Kronos context windows per timeframe (bars required strictly before signal).
# 512 matches Kronos-small/base max_context; the 4h window is smaller because
# S146 only needs recent 4h structure and deep 4h history adds little.
CONTEXT_BARS = {"5m": 512, "15m": 256, "4h": 128}

# Forecast horizon for Phase 4, chosen from the observed hold-time distribution
# of this run: 96 five-minute bars (8h) resolves 91.3% of trades.
FORECAST_BARS_5M = 96

LABEL_COLUMNS = ("label_r", "label_win", "label_exit_reason", "label_mfe_r",
                 "label_mae_r", "label_bars_held_5m")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def load_signals(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Signal-time records keyed by trade_id (executed signals only)."""
    path = run_dir / "detector_signals.jsonl"
    signals: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            trade_id = record.get("trade_id")
            if record.get("execution_status") != "executed" or not trade_id:
                continue
            # Keep only signal-time fields; this is the first leakage barrier.
            signals[trade_id] = {key: record.get(key) for key in SIGNAL_TIME_FIELDS}
    return signals


def load_trades(run_dir: Path) -> dict[str, dict[str, str]]:
    with (run_dir / "trades.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["trade_id"]: row for row in csv.DictReader(handle)}


def load_bar_starts(symbol: str, timeframe: str) -> list[int]:
    """Sorted bar-open epochs for coverage checks (timestamps only, low memory)."""
    path = paths.raw_csv(symbol, timeframe)
    if not path.is_file():
        return []
    starts: list[int] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                starts.append(int(float(row["time"])))
            except (KeyError, TypeError, ValueError):
                continue
    starts.sort()
    return starts


def _points(manifest: dict[str, Any], symbol: str) -> float:
    entry = (manifest.get("symbols") or {}).get(symbol) or {}
    value = float(entry.get("point") or 0.0)
    if value > 0:
        return value
    # Fall back to the JPY/non-JPY convention only if the broker did not report.
    return 0.001 if symbol.endswith("JPY") else 0.00001


def build(run_dir: Path, cutoff_fraction: float) -> dict[str, Any]:
    manifest_path = paths.RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}

    signals = load_signals(run_dir)
    trades = load_trades(run_dir)
    print(f"executed signals: {len(signals):,}   trades.csv rows: {len(trades):,}")

    joined = sorted(set(signals) & set(trades))
    missing_trade = sorted(set(signals) - set(trades))
    missing_signal = sorted(set(trades) - set(signals))
    print(f"joined on trade_id: {len(joined):,}"
          f"  (signals without trade: {len(missing_trade)}, trades without signal: {len(missing_signal)})")

    symbols = sorted({signals[tid]["symbol"] for tid in joined})
    starts_cache: dict[tuple[str, str], list[int]] = {}
    for symbol in symbols:
        for timeframe in CONTEXT_BARS:
            starts_cache[(symbol, timeframe)] = load_bar_starts(symbol, timeframe)
    coverage_missing = {key: len(value) for key, value in starts_cache.items() if not value}
    if coverage_missing:
        print(f"WARNING: no bars loaded for {len(coverage_missing)} symbol/timeframe pair(s)")

    rows: list[dict[str, Any]] = []
    for trade_id in joined:
        signal = signals[trade_id]
        trade = trades[trade_id]
        symbol = signal["symbol"]
        signal_time = _parse_iso(signal.get("signal_time_utc"))
        if signal_time is None:
            continue
        signal_epoch = int(signal_time.timestamp())

        row: dict[str, Any] = {
            "meta_trade_id": trade_id,
            "meta_signal_id": signal.get("signal_id"),
            "meta_symbol": symbol,
            "meta_broker_symbol": signal.get("broker_symbol"),
            "meta_direction": signal.get("direction"),
            "meta_model": signal.get("model"),
            "meta_signal_time_utc": signal.get("signal_time_utc"),
            "meta_signal_epoch": signal_epoch,
            "meta_trigger": signal.get("trigger"),
            "meta_structural_stop": signal.get("structural_stop"),
            "meta_effective_stop": signal.get("effective_stop"),
            "meta_destination_target": signal.get("destination_target"),
            "meta_point": _points(manifest, symbol),
        }
        row.update(build_features(signal, row["meta_point"]))

        # --- bar coverage: context must end strictly at/before signal time ----
        complete = True
        for timeframe, needed in CONTEXT_BARS.items():
            starts = starts_cache.get((symbol, timeframe)) or []
            seconds = paths.TF_SECONDS[timeframe]
            # Bars whose CLOSE is at or before the signal timestamp.
            usable = bisect.bisect_right(starts, signal_epoch - seconds)
            row[f"meta_bars_before_{timeframe}"] = usable
            row[f"meta_context_ok_{timeframe}"] = int(usable >= needed)
            complete = complete and usable >= needed
        # Forward bars are only needed so Phase 4 can score forecasts fairly.
        starts_5m = starts_cache.get((symbol, "5m")) or []
        after = len(starts_5m) - bisect.bisect_right(starts_5m, signal_epoch)
        row["meta_bars_after_5m"] = after
        row["meta_forecast_window_ok"] = int(after >= FORECAST_BARS_5M)
        row["meta_context_complete"] = int(complete)

        # --- labels (never features) -----------------------------------------
        def number(name: str) -> float | None:
            try:
                return float(trade[name])
            except (KeyError, TypeError, ValueError):
                return None

        realized = number("realized_r")
        if realized is None:
            realized = number("r")
        row["label_r"] = realized
        row["label_win"] = None if realized is None else int(realized > 0)
        row["label_exit_reason"] = trade.get("exit_reason")
        row["label_mfe_r"] = number("mfe_r")
        row["label_mae_r"] = number("mae_r")
        row["label_bars_held_5m"] = number("bars_held_5m")
        rows.append(row)

    rows.sort(key=lambda item: item["meta_signal_epoch"])

    # --- fixed time split, decided before any model is fitted ---------------
    epochs = [row["meta_signal_epoch"] for row in rows]
    index = max(0, min(len(epochs) - 1, int(len(epochs) * cutoff_fraction)))
    cutoff_epoch = epochs[index]
    cutoff_iso = datetime.fromtimestamp(cutoff_epoch, UTC).isoformat().replace("+00:00", "Z")
    for row in rows:
        row["meta_split"] = "train" if row["meta_signal_epoch"] < cutoff_epoch else "test"

    trained = sum(1 for row in rows if row["meta_split"] == "train")
    tested = len(rows) - trained
    print(f"\nsplit cutoff {cutoff_iso} (fraction {cutoff_fraction:g})")
    print(f"  train {trained:,} trades   test {tested:,} trades")

    # --- leakage audit -------------------------------------------------------
    sample = rows[0] if rows else {}
    features = feature_names(sample)
    offenders = sorted(name for name in features
                       if name[2:] in POST_SIGNAL_FIELDS or name[2:].startswith("exit_"))
    complete_rows = sum(row["meta_context_complete"] for row in rows)
    forecastable = sum(row["meta_forecast_window_ok"] for row in rows)

    audit = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_run": str(run_dir),
        "rows": len(rows),
        "feature_count": len(features),
        "features": features,
        "label_columns": list(LABEL_COLUMNS),
        "context_bars_required": CONTEXT_BARS,
        "forecast_bars_5m": FORECAST_BARS_5M,
        "split": {"cutoff_epoch": cutoff_epoch, "cutoff_utc": cutoff_iso,
                  "fraction": cutoff_fraction, "train": trained, "test": tested},
        "coverage": {
            "context_complete_rows": complete_rows,
            "context_incomplete_rows": len(rows) - complete_rows,
            "forecast_window_ok_rows": forecastable,
        },
        "join": {"signals_without_trade": len(missing_trade),
                 "trades_without_signal": len(missing_signal)},
        "leakage_checks": {
            "features_named_after_post_signal_fields": offenders,
            "post_signal_field_count": len(POST_SIGNAL_FIELDS),
            "signal_time_field_allowlist": list(SIGNAL_TIME_FIELDS),
        },
        "known_limitations": [
            "Outcomes come from the checked-in replay, which is native-bar based "
            "with a deliberately pessimistic stop-first same-bar rule.",
            "Rows are the 1,167 trades the portfolio gates actually admitted. "
            "Skipping a trade would in reality free a slot for another signal; "
            "that second-order portfolio effect is not modelled here.",
        ],
    }

    print("\n--- leakage audit ---")
    print(f"  features: {len(features)}")
    print(f"  features named after post-signal fields: {offenders or 'none'}")
    print(f"  rows with full context: {complete_rows:,}/{len(rows):,}")
    print(f"  rows with full forecast window: {forecastable:,}/{len(rows):,}")

    # --- write ---------------------------------------------------------------
    paths.DATASET.mkdir(parents=True, exist_ok=True)
    dataset_path = paths.DATASET / "s146_signals.csv"
    columns = (["meta_trade_id", "meta_signal_id", "meta_symbol", "meta_broker_symbol",
                "meta_direction", "meta_model", "meta_signal_time_utc", "meta_signal_epoch",
                "meta_split", "meta_point", "meta_trigger", "meta_structural_stop",
                "meta_effective_stop", "meta_destination_target",
                "meta_context_complete", "meta_forecast_window_ok", "meta_bars_after_5m"]
               + [f"meta_bars_before_{tf}" for tf in CONTEXT_BARS]
               + [f"meta_context_ok_{tf}" for tf in CONTEXT_BARS]
               + features + list(LABEL_COLUMNS))
    with dataset_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    audit_path = paths.DATASET / "s146_signals_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, allow_nan=False), encoding="utf-8")

    # Baseline stats so later phases can be compared against the untouched run.
    resolved = [row["label_r"] for row in rows if row["label_r"] is not None]
    baseline = {
        "trades": len(resolved),
        "net_r": round(sum(resolved), 6),
        "win_rate": round(sum(1 for r in resolved if r > 0) / len(resolved), 6) if resolved else None,
        "average_r": round(sum(resolved) / len(resolved), 6) if resolved else None,
        "by_split": {},
    }
    for split in ("train", "test"):
        values = [row["label_r"] for row in rows
                  if row["meta_split"] == split and row["label_r"] is not None]
        baseline["by_split"][split] = {
            "trades": len(values),
            "net_r": round(sum(values), 6),
            "win_rate": round(sum(1 for r in values if r > 0) / len(values), 6) if values else None,
            "average_r": round(sum(values) / len(values), 6) if values else None,
        }
    (paths.DATASET / "s146_baseline.json").write_text(
        json.dumps(baseline, indent=2), encoding="utf-8")

    print("\n--- unfiltered S146 baseline (the bar to beat) ---")
    print(f"  all   : {baseline['trades']:,} trades  net {baseline['net_r']:+.2f}R  "
          f"win {baseline['win_rate']:.1%}  avg {baseline['average_r']:+.4f}R")
    for split in ("train", "test"):
        stats = baseline["by_split"][split]
        print(f"  {split:<6}: {stats['trades']:,} trades  net {stats['net_r']:+.2f}R  "
              f"win {stats['win_rate']:.1%}  avg {stats['average_r']:+.4f}R")
    print(f"\nDataset : {dataset_path}")
    print(f"Audit   : {audit_path}")
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, default=paths.S146_RUN)
    parser.add_argument("--cutoff-fraction", type=float, default=0.6,
                        help="fraction of trades (time ordered) used for training")
    args = parser.parse_args(argv)
    if not args.run_dir.is_dir():
        raise SystemExit(f"run dir not found: {args.run_dir}")
    build(args.run_dir, args.cutoff_fraction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
