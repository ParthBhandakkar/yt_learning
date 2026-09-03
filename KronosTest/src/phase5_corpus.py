#!/usr/bin/env python3
"""Phase 5a: build the Kronos fine-tune corpus from Phase 1 bars.

Two things this must get right:

1. **No leakage into the Phase 6 test set.** Every bar used for fine-tuning
   closes strictly before the dataset's train/test cutoff, so the fine-tuned
   model has never seen the period it is later judged on.

2. **No cross-symbol windows.** The vendor's dataset sorts one CSV by timestamp
   and slides a window over consecutive rows. Concatenating 29 symbols would
   interleave them at shared timestamps and every window would mix instruments.
   So each symbol is written as its own file and a per-symbol window index is
   built by the companion dataset class.

Output layout:
    data/finetune/<timeframe>/<SYMBOL>.csv     timestamps,open,high,low,close,volume,amount
    data/finetune/<timeframe>/corpus.json      manifest + leakage record

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase5_corpus.py --timeframe 5m
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

paths.ensure_dirs()
UTC = timezone.utc

CORPUS_ROOT = paths.DATA / "finetune"
COLUMNS = ("timestamps", "open", "high", "low", "close", "volume", "amount")


def read_cutoff(audit_path: Path) -> tuple[int, str]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    split = audit["split"]
    return int(split["cutoff_epoch"]), str(split["cutoff_utc"])


def convert_symbol(symbol: str, timeframe: str, cutoff_epoch: int,
                   destination: Path) -> dict[str, Any]:
    """Write one symbol's bars in Kronos CSV format, truncated at the cutoff."""
    source = paths.raw_csv(symbol, timeframe)
    if not source.is_file():
        return {"symbol": symbol, "rows": 0, "error": "missing_source"}
    seconds = paths.TF_SECONDS[timeframe]
    rows: list[dict[str, Any]] = []
    dropped_after_cutoff = 0
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            try:
                epoch = int(float(record["time"]))
                candle = (float(record["open"]), float(record["high"]),
                          float(record["low"]), float(record["close"]))
                volume = float(record.get("tick_volume") or 0.0)
            except (KeyError, TypeError, ValueError):
                continue
            # The bar must be fully closed before the cutoff.
            if epoch + seconds > cutoff_epoch:
                dropped_after_cutoff += 1
                continue
            if not (candle[1] >= candle[2]):
                continue
            stamp = datetime.fromtimestamp(epoch, UTC)
            typical = (candle[0] + candle[1] + candle[2] + candle[3]) / 4.0
            rows.append({
                "timestamps": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "open": candle[0], "high": candle[1], "low": candle[2],
                "close": candle[3], "volume": volume,
                # Kronos expects an 'amount' column; tick volume times typical
                # price is the standard stand-in when true turnover is absent.
                "amount": round(volume * typical, 6),
            })
    rows.sort(key=lambda item: item["timestamps"])
    if not rows:
        return {"symbol": symbol, "rows": 0, "error": "no_rows_before_cutoff"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return {
        "symbol": symbol, "path": destination.name, "rows": len(rows),
        "first_bar_utc": rows[0]["timestamps"], "last_bar_utc": rows[-1]["timestamps"],
        "dropped_after_cutoff": dropped_after_cutoff,
    }


def build(timeframe: str, symbols: list[str], audit_path: Path) -> dict[str, Any]:
    cutoff_epoch, cutoff_iso = read_cutoff(audit_path)
    root = CORPUS_ROOT / timeframe
    root.mkdir(parents=True, exist_ok=True)
    print(f"corpus timeframe={timeframe}  cutoff {cutoff_iso}  (bars must close before this)")

    entries: list[dict[str, Any]] = []
    for symbol in symbols:
        entry = convert_symbol(symbol, timeframe, cutoff_epoch, root / f"{symbol}.csv")
        entries.append(entry)
        if entry.get("error"):
            print(f"  {symbol:<8} {entry['error']}")
        else:
            print(f"  {symbol:<8} {entry['rows']:>7,} bars  "
                  f"{entry['first_bar_utc']} -> {entry['last_bar_utc']}")

    usable = [entry for entry in entries if not entry.get("error")]
    total = sum(entry["rows"] for entry in usable)
    manifest = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": "phase5a_corpus", "timeframe": timeframe,
        "cutoff_epoch": cutoff_epoch, "cutoff_utc": cutoff_iso,
        "cutoff_source": str(audit_path),
        "symbols_written": len(usable), "symbols_failed": len(entries) - len(usable),
        "total_bars": total,
        "files": {entry["symbol"]: entry for entry in entries},
        "leakage_guarantee": (
            "Every bar in this corpus closes strictly before the Phase 2 train/test "
            "cutoff, so a model fine-tuned on it has not seen the evaluation period."),
        "format_notes": [
            "One CSV per symbol. Concatenating them would interleave instruments at "
            "shared timestamps, so the multi-symbol dataset indexes windows per file.",
            "amount = tick_volume * typical price, because MT5 forex feeds carry no "
            "true turnover.",
        ],
    }
    (root / "corpus.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(usable)} symbol file(s), {total:,} bars total")
    print(f"Manifest: {root / 'corpus.json'}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeframe", default="5m", choices=sorted(paths.TF_SECONDS))
    parser.add_argument("--symbols", nargs="+", help="override the S146 basket")
    parser.add_argument("--audit", type=Path,
                        default=paths.DATASET / "s146_signals_audit.json")
    args = parser.parse_args(argv)
    if not args.audit.is_file():
        raise SystemExit(f"audit not found: {args.audit}. Run phase2_dataset.py first.")
    symbols = [s.strip().upper() for value in (args.symbols or paths.S146_BASKET)
               for s in str(value).split(",") if s.strip()]
    build(args.timeframe, list(dict.fromkeys(symbols)), args.audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
