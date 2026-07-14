"""
Per-timeframe logging + per-timeframe "passed criteria" files.

- logs/engine.log                : top-level engine events
- logs/<tf>.log                  : one rotating log per timeframe cycle (4h/1h/15m/5m)
- passes/<tf>_passes.jsonl       : one JSON line every time a symbol PASSES that TF's
                                   criteria (independent file per timeframe, append-only)
- passes/trades.jsonl            : executed trades

Log timestamps are shown in Indian Standard Time (IST, UTC+05:30).
Internal scheduling and risk checks stay on UTC.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
PASS_DIR = HERE / "passes"
LOG_DIR.mkdir(exist_ok=True)
PASS_DIR.mkdir(exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))
TIMEFRAMES = ("4h", "1h", "15m", "5m")
_loggers: dict[str, logging.Logger] = {}
_file_locks: dict[str, threading.Lock] = {tf: threading.Lock() for tf in TIMEFRAMES}
_trade_lock = threading.Lock()


class ISTFormatter(logging.Formatter):
    """Format log asctime in IST regardless of the OS local timezone."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=IST)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")


_FMT = ISTFormatter("%(asctime)s IST | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")


def format_ist(dt: datetime) -> str:
    """Human-readable IST string for cycle / email lines."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _make_logger(name: str, filename: str) -> logging.Logger:
    lg = logging.getLogger(f"live.{name}")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        fh = RotatingFileHandler(LOG_DIR / filename, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(_FMT)
        lg.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(_FMT)
        lg.addHandler(sh)
    return lg


def get_engine_logger() -> logging.Logger:
    if "engine" not in _loggers:
        _loggers["engine"] = _make_logger("engine", "engine.log")
    return _loggers["engine"]


def get_tf_logger(tf: str) -> logging.Logger:
    if tf not in _loggers:
        _loggers[tf] = _make_logger(tf, f"{tf}.log")
    return _loggers[tf]


def record_pass(tf: str, record: dict) -> None:
    """Append a JSON line to the timeframe's own passes file (independent per TF)."""
    now_utc = datetime.now(timezone.utc)
    record = {
        "ts_utc": now_utc.isoformat(),
        "ts_ist": format_ist(now_utc),
        "timeframe": tf,
        **record,
    }
    path = PASS_DIR / f"{tf}_passes.jsonl"
    with _file_locks.get(tf, threading.Lock()):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")


def record_trade(record: dict) -> None:
    now_utc = datetime.now(timezone.utc)
    record = {"ts_utc": now_utc.isoformat(), "ts_ist": format_ist(now_utc), **record}
    with _trade_lock:
        with open(PASS_DIR / "trades.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
