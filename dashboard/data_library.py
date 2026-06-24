"""
Resolve instrument CSV paths from the local Exness structured history folder.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path
from typing import Callable, Optional

DEFAULT_DATA_ROOT = Path(
    os.environ.get(
        "YT_DATA_ROOT",
        r"O:\D temp\UltimateTradeBot\Data\Exness\structured\history",
    )
)
DEFAULT_SYMBOL = os.environ.get("YT_DEFAULT_SYMBOL", "XAUUSD")

# Strategy timeframe hints / arg names -> folder under {root}/{symbol}/
TF_FOLDER_MAP: dict[str, str] = {
    "1m": "1m",
    "1-minute": "1m",
    "5m": "5m",
    "5-minute": "5m",
    "6m": "6m",
    "10m": "10m",
    "12m": "12m",
    "15m": "15m",
    "15-minute": "15m",
    "20m": "20m",
    "30m": "30m",
    "1h": "1h",
    "1-hour": "1h",
    "2h": "2h",
    "3h": "3h",
    "4h": "4h",
    "4-hour": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "daily": "1d",
    "1d": "1d",
    "1w": "1w",
    "gold": "1h",
    "gold_5m": "5m",
    "silver": "1h",
    "silver_5m": "5m",
}

ARG_TF_PATTERNS: list[tuple[str, str]] = [
    ("csv1m", "1m"),
    ("csv5m", "5m"),
    ("csv15m", "15m"),
    ("csv30m", "30m"),
    ("csv1h", "1h"),
    ("csv4h", "4h"),
    ("csvdaily", "1d"),
    ("daily", "1d"),
    ("4h", "4h"),
    ("1h", "1h"),
    ("15m", "15m"),
    ("5m", "5m"),
    ("1m", "1m"),
]


def data_root() -> Path:
    return Path(os.environ.get("YT_DATA_ROOT", str(DEFAULT_DATA_ROOT)))


def normalize_tf_folder(tf_hint: str, arg: str) -> str:
    hint_key = (tf_hint or "").strip().lower()
    if hint_key in TF_FOLDER_MAP:
        return TF_FOLDER_MAP[hint_key]

    arg_l = arg.lower().replace("-", "")
    for needle, folder in ARG_TF_PATTERNS:
        if needle in arg_l:
            return folder

    # Single generic --csv on gold strategies is usually 5m.
    return "5m"


def list_symbol_timeframes(root: Path, symbol: str) -> list[str]:
    sym_dir = root / symbol.upper()
    if not sym_dir.is_dir():
        return []
    return sorted(
        p.name for p in sym_dir.iterdir() if p.is_dir() and any(p.glob("*.csv"))
    )


def find_instrument_csv(root: Path, symbol: str, tf_folder: str) -> Optional[Path]:
    folder = root / symbol.upper() / tf_folder
    if not folder.is_dir():
        return None
    csvs = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return csvs[0] if csvs else None


def resolve_strategy_data(
    csv_args: list[dict],
    symbol: str = DEFAULT_SYMBOL,
    root: Optional[Path] = None,
) -> tuple[dict[str, str], list[str]]:
    """
    Map strategy CSV args to filesystem paths.
    Returns (arg -> path, list of human-readable labels for UI).
    """
    root = root or data_root()
    symbol = symbol.upper()
    file_map: dict[str, str] = {}
    labels: list[str] = []
    missing: list[str] = []

    for ca in csv_args:
        arg = ca["arg"]
        tf_folder = normalize_tf_folder(ca.get("timeframe", ""), arg)
        path = find_instrument_csv(root, symbol, tf_folder)
        if path is None:
            missing.append(f"{arg} ({tf_folder})")
            continue
        file_map[arg] = str(path)
        labels.append(f"{symbol}/{tf_folder}/{path.name}")

    if missing:
        available = list_symbol_timeframes(root, symbol)
        raise FileNotFoundError(
            f"Missing library CSV for {symbol}: {', '.join(missing)}. "
            f"Available timeframes: {', '.join(available) or 'none'}"
        )

    return file_map, labels


def library_status(
    csv_args: list[dict],
    symbol: str = DEFAULT_SYMBOL,
    root: Optional[Path] = None,
) -> dict:
    root = root or data_root()
    symbol = symbol.upper()
    matches: list[dict] = []
    ready = True

    for ca in csv_args:
        tf_folder = normalize_tf_folder(ca.get("timeframe", ""), ca["arg"])
        path = find_instrument_csv(root, symbol, tf_folder)
        matches.append({
            "arg": ca["arg"],
            "timeframe": tf_folder,
            "path": str(path) if path else None,
            "filename": path.name if path else None,
            "found": path is not None,
        })
        if path is None:
            ready = False

    return {
        "root": str(root),
        "symbol": symbol,
        "ready": ready,
        "matches": matches,
        "available_timeframes": list_symbol_timeframes(root, symbol),
    }


def scan_csv_max_timestamp(path: Path) -> Optional[int]:
    from core import detect_csv_columns, _parse_timestamp

    max_ts: Optional[int] = None
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return None
        cols = detect_csv_columns(list(reader.fieldnames))
        for row in reader:
            try:
                ts, _ = _parse_timestamp(row, cols)
            except (ValueError, KeyError, TypeError):
                continue
            max_ts = ts if max_ts is None else max(max_ts, ts)
    return max_ts


def write_csv_from_timestamp(
    src: Path,
    dst: Path,
    start_ts: int,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> int:
    from core import detect_csv_columns, _parse_timestamp

    kept = 0
    read = 0
    with open(src, "r", encoding="utf-8-sig") as fin, open(dst, "w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin)
        if not reader.fieldnames:
            return 0
        fieldnames = list(reader.fieldnames)
        cols = detect_csv_columns(fieldnames)
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        for row in reader:
            read += 1
            try:
                ts, _ = _parse_timestamp(row, cols)
            except (ValueError, KeyError, TypeError):
                continue
            if ts < start_ts:
                continue
            writer.writerow(row)
            kept += 1
            if on_progress and read % 100000 == 0:
                on_progress(min(20, read // 100000), f"Filtering {src.name}: {read:,} rows scanned")
    return kept


def prepare_library_csv_window(
    arg_paths: dict[str, str],
    max_days: Optional[int],
    out_dir: Path,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> tuple[dict[str, str], list[str]]:
    """Optionally trim library CSVs to the most recent N days into out_dir."""
    if not max_days or max_days <= 0:
        return arg_paths, []

    trimmed: dict[str, str] = {}
    notes: list[str] = []
    total = len(arg_paths)

    for idx, (arg, src_s) in enumerate(arg_paths.items()):
        src = Path(src_s)
        if on_progress:
            on_progress(
                min(14, 2 + int(12 * idx / max(total, 1))),
                f"Preparing {src.name}...",
            )
        max_ts = scan_csv_max_timestamp(src)
        if max_ts is None:
            trimmed[arg] = str(src)
            continue
        start_ts = max_ts - int(max_days * 86400)
        out = out_dir / f"trim_{src.name}"
        kept = write_csv_from_timestamp(src, out, start_ts, on_progress)
        trimmed[arg] = str(out)
        notes.append(f"{src.name}: using last {max_days} days ({kept:,} rows)")

    return trimmed, notes
