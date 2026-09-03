"""
Strategy 147 event journals — one file per timeframe plus one for trades.

    logs/s147_4h_events.log     every 4H point-of-interest event and arrival
    logs/s147_15m_events.log    change of character and refinement-zone events
    logs/s147_5m_events.log     alert / signal / order / fill / exit events
    logs/s147_trades.log        one complete record per trade actually taken

Each line is "<IST time> | <event_type> | <json payload>", so the files stay
human readable while remaining machine parseable.

Every timestamp in a payload is written THREE ways: the raw epoch for joining,
plus explicit UTC and IST strings so a formation time can be read straight off
the line and typed into a chart without converting anything by hand. Use
`stamp()` to build those triples.

Zones persist across cycles, so every event carries a de-duplication key and is
written only once. The key set is persisted, so a restart does not re-log
history that was already journalled.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from logging_setup import LOG_DIR, PASS_DIR, format_ist

STREAMS = ("4h", "15m", "5m", "trade")

_FILES = {
    "4h": LOG_DIR / "s147_4h_events.log",
    "15m": LOG_DIR / "s147_15m_events.log",
    "5m": LOG_DIR / "s147_5m_events.log",
    "trade": LOG_DIR / "s147_trades.log",
}
_SEEN_PATH = PASS_DIR / "s147_seen_events.json"
_MAX_SEEN = 20000

_lock = threading.Lock()
_seen: set[str] = set()
_seen_order: list[str] = []
_loaded = False


def journal_paths() -> str:
    return ", ".join(str(path) for path in _FILES.values())


def iso_utc(epoch: int | float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat()


def ist(epoch: int | float | None) -> str | None:
    if epoch is None:
        return None
    return format_ist(datetime.fromtimestamp(int(epoch), timezone.utc))


def stamp(name: str, epoch: int | float | None) -> dict:
    """Epoch plus readable UTC and IST for one moment.

    stamp("origin", 1785168000) ->
        {"origin": 1785168000,
         "origin_utc": "2026-07-27T16:00:00+00:00",
         "origin_ist": "27th July 2026 21:30:00 IST"}
    """
    if epoch is None:
        return {name: None, f"{name}_utc": None, f"{name}_ist": None}
    epoch = int(epoch)
    return {name: epoch, f"{name}_utc": iso_utc(epoch), f"{name}_ist": ist(epoch)}


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
        raise ValueError(f"unknown S147 event stream: {stream}")
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
            **payload,
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
