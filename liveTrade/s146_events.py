"""
Strategy 146 event journals — one file per timeframe plus one for trades.

    logs/s146_4h_events.log     every 4H destination zone event
    logs/s146_15m_events.log    every 15m entry-zone event
    logs/s146_5m_events.log     every 5m alert / signal / order / fill / exit
    logs/s146_trades.log        one complete record per trade actually taken

Every line is "<IST time> | <event_type> | <json payload>" so the files stay
human readable while remaining machine parseable. Timestamp fields inside the
JSON payload are serialized as readable UTC ISO datetimes; epoch seconds remain
internal only and are never written to new s146 journals.

Zones persist across cycles, so every event carries a de-duplication key and is
written only once. The key set is persisted, which means a restart does not
re-log history that was already journalled.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from logging_setup import LOG_DIR, PASS_DIR, format_ist

STREAMS = ("4h", "15m", "5m", "trade")

_FILES = {
    "4h": LOG_DIR / "s146_4h_events.log",
    "15m": LOG_DIR / "s146_15m_events.log",
    "5m": LOG_DIR / "s146_5m_events.log",
    "trade": LOG_DIR / "s146_trades.log",
}
_SEEN_PATH = PASS_DIR / "s146_seen_events.json"
_MAX_SEEN = 20000

_TIMESTAMP_KEYS = {
    "timestamp", "signal_bar_open", "signal_bar_close", "alert_bar_close",
    "origin_time", "confirmed_at", "destination_origin_time",
    "destination_confirmed_at", "entry_zone_origin_time",
    "entry_zone_confirmed_at", "bar_open_time", "bar_close_time",
    "opened_at", "closed_at", "placed_at", "filled_at", "arrival_time",
    "choch_time", "entry_time", "exit_time",
}


def _is_timestamp_key(key: str) -> bool:
    name = key.lower()
    return (
        name in _TIMESTAMP_KEYS
        or name.endswith("_time")
        or name.endswith("_at")
        or name.endswith("_bar_open")
        or name.endswith("_bar_close")
    )


def _format_log_timestamps(value: Any, key: str = "") -> Any:
    """Convert epoch timestamp fields to readable UTC without changing signals."""
    if isinstance(value, dict):
        return {name: _format_log_timestamps(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_format_log_timestamps(item, key) for item in value]
    if _is_timestamp_key(key) and isinstance(value, (int, float)) and value > 1_000_000_000:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    return value

_lock = threading.Lock()
_seen: set[str] = set()
_seen_order: list[str] = []
_loaded = False


def _load_seen() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_SEEN_PATH, "r", encoding="utf-8") as handle:
            keys = json.load(handle)
        if isinstance(keys, list):
            for key in keys:
                text = str(key)
                if text not in _seen:
                    _seen.add(text)
                    _seen_order.append(text)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def _persist_seen() -> None:
    try:
        _SEEN_PATH.parent.mkdir(exist_ok=True)
        with open(_SEEN_PATH, "w", encoding="utf-8") as handle:
            json.dump(_seen_order[-_MAX_SEEN:], handle)
    except OSError:
        pass


def log_event(stream: str, event_type: str, payload: dict, key: str | None = None) -> bool:
    """Append one event to its timeframe journal. Returns False when suppressed."""
    if stream not in _FILES:
        raise ValueError(f"unknown S146 event stream: {stream}")
    with _lock:
        _load_seen()
        dedupe_key = f"{stream}|{event_type}|{key}" if key is not None else None
        if dedupe_key is not None:
            if dedupe_key in _seen:
                return False
            _seen.add(dedupe_key)
            _seen_order.append(dedupe_key)
            if len(_seen_order) > _MAX_SEEN * 2:
                del _seen_order[:-_MAX_SEEN]
            _persist_seen()

        now = datetime.now(timezone.utc)
        record = {
            "ts_utc": now.isoformat(),
            "ts_ist": format_ist(now),
            "stream": stream,
            "type": event_type,
            **_format_log_timestamps(payload),
        }
        line = f"{format_ist(now)} | {event_type} | {json.dumps(record, default=str)}\n"
        try:
            _FILES[stream].parent.mkdir(exist_ok=True)
            with open(_FILES[stream], "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            return False
    return True


def log_4h(event_type: str, payload: dict, key: str | None = None) -> bool:
    return log_event("4h", event_type, payload, key)


def log_15m(event_type: str, payload: dict, key: str | None = None) -> bool:
    return log_event("15m", event_type, payload, key)


def log_5m(event_type: str, payload: dict, key: str | None = None) -> bool:
    return log_event("5m", event_type, payload, key)


def log_trade(event_type: str, payload: dict, key: str | None = None) -> bool:
    return log_event("trade", event_type, payload, key)


def journal_paths() -> dict[str, str]:
    return {stream: str(path) for stream, path in _FILES.items()}
