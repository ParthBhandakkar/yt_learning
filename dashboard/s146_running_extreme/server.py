#!/usr/bin/env python3
"""Dependency-free, read-only dashboard server for S146 running-extreme runs."""
from __future__ import annotations

import argparse
import csv
import json
import math
import mimetypes
import re
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

DASHBOARD_ROOT = Path(__file__).resolve().parent
REPO_ROOT = DASHBOARD_ROOT.parents[1]
DATA_ROOT = REPO_ROOT / "data" / "s146_running_extreme"
RUNS_ROOT = DATA_ROOT / "runs"
RAW_ROOT = DATA_ROOT / "raw"
STATIC_ROOT = DASHBOARD_ROOT / "static"
HOST = "127.0.0.1"
PORT = 8016
MAX_PAGE_SIZE = 500
_CACHE: dict[Path, tuple[int, int, Any]] = {}


def clean_json(value: Any) -> Any:
    """Convert common non-JSON values and replace NaN/Infinity with null."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean_json(item) for item in value]
    return str(value)


def _stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def load_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    stamp = _stamp(path)
    cached = _CACHE.get(path)
    if cached and cached[:2] == stamp:
        return cached[2]
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default
    _CACHE[path] = (*stamp, value)
    return value


def coerce_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"none", "null", "nan", "na", "n/a", "inf", "+inf", "-inf"}:
        return None
    try:
        number = float(text)
        if math.isfinite(number):
            return int(number) if number.is_integer() and not any(c in text.lower() for c in ".e") else number
        return None
    except ValueError:
        return text


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    stamp = _stamp(path)
    cached = _CACHE.get(path)
    if cached and cached[:2] == stamp:
        return cached[2]
    rows: list[dict[str, Any]] = []
    try:
        if path.suffix.lower() == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = [{str(k): coerce_scalar(v) for k, v in row.items()} for row in csv.DictReader(handle)]
        elif path.suffix.lower() in {".jsonl", ".ndjson"}:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    if line.strip():
                        item = json.loads(line)
                        if isinstance(item, dict):
                            rows.append(item)
        elif path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(value, list):
                rows = [item for item in value if isinstance(item, dict)]
            elif isinstance(value, dict):
                nested = next((value.get(k) for k in ("trades", "events", "rows", "data") if isinstance(value.get(k), list)), None)
                rows = [item for item in (nested or []) if isinstance(item, dict)]
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError):
        rows = []
    _CACHE[path] = (*stamp, rows)
    return rows


def first_file(folder: Path, stems: tuple[str, ...]) -> Path | None:
    for stem in stems:
        for suffix in (".json", ".jsonl", ".ndjson", ".csv"):
            candidate = folder / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def pick(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None and value != "":
            return value
    return default


def number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def parse_time(value: Any) -> float | None:
    numeric = number(value)
    if numeric is not None:
        return numeric / 1000.0 if numeric > 100_000_000_000 else numeric
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def safe_run(run_id: str) -> Path:
    if not run_id or run_id in {".", ".."} or Path(run_id).name != run_id:
        raise ValueError("Invalid run id")
    folder = (RUNS_ROOT / run_id).resolve()
    if folder.parent != RUNS_ROOT.resolve() or not folder.is_dir():
        raise FileNotFoundError("Run not found")
    return folder


def load_named_json(folder: Path, names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        value = load_json(folder / name, {})
        if isinstance(value, dict):
            return value
    return {}


def load_trades(folder: Path) -> list[dict[str, Any]]:
    path = first_file(folder, ("trades", "trade_log", "results"))
    rows = load_rows(path) if path else []
    normalized = []
    for index, source in enumerate(rows):
        row = dict(source)
        row["_id"] = str(pick(row, "trade_id", "id", "ticket", "position_id", default=index + 1))
        row["_index"] = index
        row["_symbol"] = str(pick(row, "symbol", "instrument", default="Unknown"))
        row["_model"] = str(pick(row, "model", "entry_model", "setup", "signal_model", default="Unknown"))
        row["_exit"] = str(pick(row, "exit_reason", "close_reason", "reason", "exit_type", default="Unknown"))
        row["_r"] = number(pick(row, "r", "net_r", "realized_r", "result_r", "pnl_r", "r_multiple"), 0.0)
        outcome = str(pick(row, "outcome", "result", "status", default="")).lower()
        if not outcome or outcome not in {"win", "loss", "breakeven", "open"}:
            outcome = "win" if row["_r"] > 0 else "loss" if row["_r"] < 0 else "breakeven"
        row["_outcome"] = outcome
        normalized.append(row)
    return normalized


def load_signals(folder: Path) -> list[dict[str, Any]]:
    path = first_file(folder, ("detector_signals", "signals", "signal_log"))
    rows = load_rows(path) if path else []
    normalized = []
    for index, source in enumerate(rows):
        row = dict(source)
        row["_id"] = str(pick(row, "signal_id", "id", default=index + 1))
        row["_index"] = index
        row["_symbol"] = str(pick(row, "symbol", "instrument", default="Unknown"))
        status = str(pick(row, "execution_status", "status", default="unknown")).lower()
        row["_status"] = status
        rejection = row.get("execution_rejection")
        row["_rejection"] = (str(rejection.get("reason", ""))
                              if isinstance(rejection, dict) else "")
        normalized.append(row)
    return normalized


def load_events(folder: Path) -> list[dict[str, Any]]:
    path = first_file(folder, ("events", "event_log", "timeline"))
    return load_rows(path) if path else []


def trade_events(trade: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    embedded = trade.get("events")
    selected = [item for item in embedded if isinstance(item, dict)] if isinstance(embedded, list) else []
    trade_ids = {str(trade.get("_id")), str(pick(trade, "trade_id", "id", "ticket", "position_id", default=""))}
    for event in events:
        event_id = pick(event, "trade_id", "trade", "id", "ticket", "position_id")
        if event_id is not None and str(event_id) in trade_ids:
            selected.append(event)
    selected.sort(key=lambda item: parse_time(pick(item, "time_utc", "timestamp", "time", "datetime", "at")) or 0)
    return selected


def breakdown(trades: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        groups.setdefault(str(trade.get(field) or "Unknown"), []).append(trade)
    result = []
    for name, rows in groups.items():
        returns = [number(row.get("_r"), 0.0) or 0.0 for row in rows]
        wins = sum(value > 0 for value in returns)
        losses = sum(value < 0 for value in returns)
        gains, pain = sum(max(value, 0) for value in returns), abs(sum(min(value, 0) for value in returns))
        result.append({"name": name, "trades": len(rows), "wins": wins, "losses": losses,
                       "win_rate_pct": 100 * wins / len(rows) if rows else 0, "net_r": sum(returns),
                       "profit_factor": gains / pain if pain else (None if not gains else gains)})
    return sorted(result, key=lambda item: (-item["trades"], item["name"]))


def calculated_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [number(trade.get("_r"), 0.0) or 0.0 for trade in trades]
    wins = sum(value > 0 for value in returns)
    losses = sum(value < 0 for value in returns)
    breakeven = sum(value == 0 for value in returns)
    gains, pain = sum(max(value, 0) for value in returns), abs(sum(min(value, 0) for value in returns))
    equity = peak = drawdown = 0.0
    curve = []
    for index, value in enumerate(returns, 1):
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        curve.append({"trade": index, "equity_r": equity, "drawdown_r": peak - equity})
    return {
        "total_trades": len(trades), "wins": wins, "losses": losses, "breakeven": breakeven,
        "open_trades": sum(trade.get("_outcome") == "open" for trade in trades),
        "win_rate_pct": 100 * wins / (wins + losses) if wins + losses else 0.0,
        "net_r": sum(returns), "profit_factor": gains / pain if pain else (None if not gains else gains),
        "max_drawdown_r": drawdown, "equity_curve": curve,
        "breakdowns": {"symbol": breakdown(trades, "_symbol"), "model": breakdown(trades, "_model"),
                       "exit": breakdown(trades, "_exit")},
    }


def discover_runs() -> list[dict[str, Any]]:
    if not RUNS_ROOT.is_dir():
        return []
    found = []
    for folder in RUNS_ROOT.iterdir():
        if not folder.is_dir():
            continue
        manifest = load_named_json(folder, ("manifest.json", "metadata.json", "run.json", "config.json"))
        summary = load_named_json(folder, ("summary.json", "metrics.json", "stats.json"))
        modified = datetime.fromtimestamp(folder.stat().st_mtime).astimezone().isoformat()
        found.append({"run_id": folder.name, "modified": modified,
                      "created": pick(manifest, "created_utc", "created_at", "started_at", default=modified),
                      "label": pick(manifest, "label", "name", "description", default=folder.name),
                      "total_trades": pick(summary, "total_trades", "trades")})
    return sorted(found, key=lambda item: (str(item["created"]), item["run_id"]), reverse=True)


def run_summary(folder: Path) -> dict[str, Any]:
    manifest = load_named_json(folder, ("manifest.json", "metadata.json", "run.json", "config.json"))
    stored = load_named_json(folder, ("summary.json", "metrics.json", "stats.json"))
    trades = load_trades(folder)
    computed = calculated_summary(trades)
    metrics = {**computed, **stored}
    metrics["breakdowns"] = computed["breakdowns"]
    if "equity_curve" not in metrics or not isinstance(metrics["equity_curve"], list):
        metrics["equity_curve"] = computed["equity_curve"]
    return {"run_id": folder.name, "metadata": manifest, "summary": metrics,
            "available_filters": {"symbols": sorted({row["_symbol"] for row in trades}),
                                  "models": sorted({row["_model"] for row in trades}),
                                  "exits": sorted({row["_exit"] for row in trades}),
                                  "outcomes": sorted({row["_outcome"] for row in trades})}}


def filter_trades(rows: list[dict[str, Any]], query: dict[str, list[str]]) -> tuple[list[dict[str, Any]], int, int]:
    symbol = query.get("symbol", [""])[0].casefold()
    model = query.get("model", [""])[0].casefold()
    exit_name = query.get("exit", [""])[0].casefold()
    outcome = query.get("outcome", [""])[0].casefold()
    search = query.get("q", [""])[0].casefold()
    filtered = []
    for row in rows:
        if symbol and row["_symbol"].casefold() != symbol:
            continue
        if model and row["_model"].casefold() != model:
            continue
        if exit_name and row["_exit"].casefold() != exit_name:
            continue
        if outcome and row["_outcome"].casefold() != outcome:
            continue
        if search and search not in json.dumps(clean_json(row), ensure_ascii=False).casefold():
            continue
        filtered.append(row)
    sort = query.get("sort", ["index"])[0]
    reverse = query.get("direction", ["desc"])[0].lower() == "desc"
    sorters = {
        "r": lambda row: number(row.get("_r"), 0.0) or 0.0,
        "symbol": lambda row: row["_symbol"].casefold(), "model": lambda row: row["_model"].casefold(),
        "outcome": lambda row: row["_outcome"], "index": lambda row: row["_index"],
        "time": lambda row: parse_time(pick(row, "entry_time_utc", "entry_time", "opened_at", "time_utc", "timestamp")) or 0,
    }
    filtered.sort(key=sorters.get(sort, sorters["index"]), reverse=reverse)
    try:
        page = max(1, int(query.get("page", ["1"])[0]))
        page_size = min(MAX_PAGE_SIZE, max(1, int(query.get("page_size", ["50"])[0])))
    except ValueError:
        page, page_size = 1, 50
    return filtered, page, page_size


def page_rows(rows: list[dict[str, Any]], query: dict[str, list[str]],
              sort_key: Any, default_size: int = 50) -> tuple[list[dict[str, Any]], int, int]:
    reverse = query.get("direction", ["desc"])[0].lower() == "desc"
    rows.sort(key=sort_key, reverse=reverse)
    try:
        page = max(1, int(query.get("page", ["1"])[0]))
        page_size = min(MAX_PAGE_SIZE, max(1, int(query.get("page_size", [str(default_size)])[0])))
    except ValueError:
        page, page_size = 1, default_size
    return rows, page, page_size


def filter_signals(rows: list[dict[str, Any]], query: dict[str, list[str]]) -> tuple[list[dict[str, Any]], int, int]:
    symbol = query.get("symbol", [""])[0].casefold()
    status = query.get("status", [""])[0].casefold()
    search = query.get("q", [""])[0].casefold()
    filtered = []
    for row in rows:
        if symbol and row["_symbol"].casefold() != symbol:
            continue
        if status and row["_status"].casefold() != status:
            continue
        if search and search not in json.dumps(clean_json(row), ensure_ascii=False).casefold():
            continue
        filtered.append(row)
    return page_rows(filtered, query,
                     lambda row: parse_time(pick(row, "signal_time_utc", "signal_time", "time_utc", "timestamp")) or 0)


def filter_events(rows: list[dict[str, Any]], query: dict[str, list[str]]) -> tuple[list[dict[str, Any]], int, int]:
    symbol = query.get("symbol", [""])[0].casefold()
    event_name = query.get("event", [""])[0].casefold()
    search = query.get("q", [""])[0].casefold()
    filtered = []
    for row in rows:
        row_symbol = str(pick(row, "symbol", "instrument", default="")).casefold()
        row_event = str(pick(row, "event", "type", "name", "action", default="")).casefold()
        if symbol and row_symbol != symbol:
            continue
        if event_name and row_event != event_name:
            continue
        if search and search not in json.dumps(clean_json(row), ensure_ascii=False).casefold():
            continue
        filtered.append(row)
    return page_rows(filtered, query,
                     lambda row: parse_time(pick(row, "time_utc", "timestamp", "time", "datetime", "at")) or 0)


def find_trade(folder: Path, trade_id: str) -> dict[str, Any]:
    decoded = unquote(trade_id)
    for trade in load_trades(folder):
        if str(trade["_id"]) == decoded or str(trade["_index"] + 1) == decoded:
            return trade
    raise FileNotFoundError("Trade not found")


def candle_file(symbol: str) -> Path | None:
    safe_symbol = re.sub(r"[^A-Za-z0-9_.-]", "", symbol)
    if not safe_symbol or not RAW_ROOT.is_dir():
        return None
    candidates = []
    for path in RAW_ROOT.rglob("*.csv"):
        text = "/".join(part.casefold() for part in path.parts)
        score = int(safe_symbol.casefold() in path.stem.casefold()) * 4
        score += int(safe_symbol.casefold() in text) * 2
        score += int("5m" in text or "m5" in text)
        if score >= 3:
            candidates.append((score, path))
    return max(candidates, key=lambda item: (item[0], item[1].stat().st_mtime_ns))[1] if candidates else None


def candles_for_trade(trade: dict[str, Any], before: int, after: int) -> dict[str, Any]:
    symbol = trade["_symbol"]
    path = candle_file(symbol)
    if path is None:
        return {"symbol": symbol, "timeframe": "5m", "source": None, "candles": []}
    candles = load_rows(path)
    normalized = []
    for row in candles:
        stamp_value = pick(row, "time_utc", "datetime", "timestamp", "time", "date")
        stamp = parse_time(stamp_value)
        if stamp is None:
            continue
        normalized.append({"time": stamp_value, "timestamp": stamp,
                           "open": number(pick(row, "open", "o")), "high": number(pick(row, "high", "h")),
                           "low": number(pick(row, "low", "l")), "close": number(pick(row, "close", "c")),
                           "volume": number(pick(row, "tick_volume", "volume", "real_volume", "v")),
                           "spread": number(pick(row, "spread"))})
    normalized.sort(key=lambda row: row["timestamp"])
    entry = parse_time(pick(trade, "entry_time_utc", "entry_time", "opened_at", "open_time", "time_utc", "timestamp"))
    exit_time = parse_time(pick(trade, "exit_time_utc", "exit_time", "closed_at", "close_time")) or entry
    if entry is None or not normalized:
        window = normalized[:before + after + 1]
    else:
        start = next((i for i, row in enumerate(normalized) if row["timestamp"] >= entry), len(normalized) - 1)
        end = next((i for i, row in enumerate(normalized[start:], start) if row["timestamp"] >= (exit_time or entry)), start)
        window = normalized[max(0, start - before):min(len(normalized), end + after + 1)]
    return {"symbol": symbol, "timeframe": "5m", "source": str(path.relative_to(DATA_ROOT)),
            "entry_timestamp": entry, "exit_timestamp": exit_time, "candles": window}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "S146Dashboard/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(clean_json(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error_json(self, status: int, message: str) -> None:
        self.send_json({"error": message, "status": status}, status)

    def serve_static(self, route: str) -> None:
        relative = "index.html" if route in {"", "/"} else unquote(route.lstrip("/"))
        path = (STATIC_ROOT / relative).resolve()
        try:
            path.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_error_json(403, "Forbidden")
            return
        if not path.is_file():
            self.send_error_json(404, "Not found")
            return
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error_json(500, "Unable to read static file")
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
            mime += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/runs":
                self.send_json({"runs": discover_runs(), "data_root": str(DATA_ROOT)})
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/summary", parsed.path)
            if match:
                self.send_json(run_summary(safe_run(unquote(match.group(1)))))
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/signals", parsed.path)
            if match:
                folder = safe_run(unquote(match.group(1)))
                all_signals = load_signals(folder)
                filtered, page, page_size = filter_signals(all_signals, parse_qs(parsed.query))
                start = (page - 1) * page_size
                self.send_json({"run_id": folder.name, "signals": filtered[start:start + page_size],
                                "page": page, "page_size": page_size, "total": len(filtered),
                                "pages": max(1, math.ceil(len(filtered) / page_size)),
                                "available_filters": {
                                    "symbols": sorted({row["_symbol"] for row in all_signals}),
                                    "statuses": sorted({row["_status"] for row in all_signals}),
                                }})
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/events", parsed.path)
            if match:
                folder = safe_run(unquote(match.group(1)))
                all_events = load_events(folder)
                filtered, page, page_size = filter_events(all_events, parse_qs(parsed.query))
                start = (page - 1) * page_size
                self.send_json({"run_id": folder.name, "events": filtered[start:start + page_size],
                                "page": page, "page_size": page_size, "total": len(filtered),
                                "pages": max(1, math.ceil(len(filtered) / page_size)),
                                "available_filters": {
                                    "symbols": sorted({str(pick(row, "symbol", "instrument", default="Unknown")) for row in all_events}),
                                    "types": sorted({str(pick(row, "event", "type", "name", "action", default="Event")) for row in all_events}),
                                }})
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/trades", parsed.path)
            if match:
                folder = safe_run(unquote(match.group(1)))
                filtered, page, page_size = filter_trades(load_trades(folder), parse_qs(parsed.query))
                start = (page - 1) * page_size
                self.send_json({"run_id": folder.name, "trades": filtered[start:start + page_size],
                                "page": page, "page_size": page_size, "total": len(filtered),
                                "pages": max(1, math.ceil(len(filtered) / page_size))})
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/trades/([^/]+)/candles", parsed.path)
            if match:
                folder = safe_run(unquote(match.group(1)))
                trade = find_trade(folder, match.group(2))
                query = parse_qs(parsed.query)
                try:
                    before = min(1000, max(0, int(query.get("before", ["72"])[0])))
                    after = min(1000, max(0, int(query.get("after", ["144"])[0])))
                except ValueError:
                    before, after = 72, 144
                self.send_json(candles_for_trade(trade, before, after))
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/trades/([^/]+)", parsed.path)
            if match:
                folder = safe_run(unquote(match.group(1)))
                trade = find_trade(folder, match.group(2))
                self.send_json({"run_id": folder.name, "trade": trade,
                                "events": trade_events(trade, load_events(folder))})
                return
            if parsed.path.startswith("/api/"):
                self.send_error_json(404, "API endpoint not found")
                return
            self.serve_static(parsed.path)
        except FileNotFoundError as exc:
            self.send_error_json(404, str(exc))
        except ValueError as exc:
            self.send_error_json(400, str(exc))
        except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
            self.send_error_json(500, f"Data read failed: {exc}")
        except Exception as exc:  # Keep malformed source rows from terminating the server.
            self.send_error_json(500, f"Unexpected data error: {exc}")

    def _readonly(self) -> None:
        self.send_error_json(405, "Read-only server: only GET and HEAD are allowed")

    do_POST = do_PUT = do_PATCH = do_DELETE = _readonly  # type: ignore[assignment]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST, help=f"Bind host (default: {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"Bind port (default: {PORT})")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"S146 dashboard: http://{args.host}:{args.port}")
    print(f"Runs directory: {RUNS_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
