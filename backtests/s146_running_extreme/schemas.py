"""JSON-safe schemas and artifact writers for the isolated S146 backtest."""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc


def json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        dt = value
        if isinstance(value, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 10) if math.isfinite(value) else None
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except Exception:
            pass
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(json_safe(row), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(json_safe(row))


def config_snapshot(config: Any) -> dict[str, Any]:
    """Export only reproducibility settings; credentials and mail secrets never enter artifacts."""
    secret_words = ("password", "pass", "secret", "token", "smtp", "login", "mt5_path", "email")
    names = [name for name in dir(config) if not name.startswith("_") and not callable(getattr(config, name, None))]
    selected: dict[str, Any] = {}
    for name in names:
        if any(word in name.lower() for word in secret_words):
            continue
        try:
            selected[name] = json_safe(getattr(config, name))
        except Exception:
            continue
    return selected


def run_manifest(**fields: Any) -> dict[str, Any]:
    return json_safe({"schema_version": 2, "artifact_type": "s146_running_extreme_run", **fields})
