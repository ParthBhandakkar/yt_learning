"""
Server for the standalone strategy dashboard.
Auto-discovers strategy scripts, accepts CSV uploads, runs backtests, returns results.
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import gdown
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

STRATEGIES_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STRATEGIES_DIR))
from core import enrich_trades_pnl, load_csv, trade_pnl_pips
from data_library import (
    DEFAULT_SYMBOL,
    library_status,
    normalize_tf_folder,
    prepare_library_csv_window,
    resolve_strategy_data,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
OUT_DIR = Path(__file__).resolve().parent / "out"
TMP_DIR = OUT_DIR / "_tmp"
OUT_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Persistent store of previously-used Google Drive links, keyed "SYMBOL_TF"
# (e.g. "GBPUSD_1h") so the UI can suggest them instead of re-typing.
SAVED_LINKS_PATH = OUT_DIR / "saved_links.json"
_SAVED_LINKS_LOCK = threading.Lock()


def _load_saved_links() -> dict:
    try:
        with open(SAVED_LINKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_links(updates: dict) -> dict:
    with _SAVED_LINKS_LOCK:
        data = _load_saved_links()
        data.update({k: v for k, v in updates.items() if v})
        try:
            with open(SAVED_LINKS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
        return data

app = FastAPI(title="Standalone Strategy Backtester")

DELAYED_IMPORTS: dict[str, dict] = {}

CHART_SESSIONS: dict[str, dict] = {}
CHART_TF_ORDER = ["1m", "5m", "15m", "1h", "4h", "daily"]
CHART_WINDOW_SIZE = 150
BACKTEST_TIMEOUT_SEC = int(os.environ.get("YT_BACKTEST_TIMEOUT_SEC", "1800"))
DEFAULT_LIBRARY_MAX_DAYS = int(os.environ.get("YT_BACKTEST_MAX_DAYS", "365"))
BACKTEST_JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Strategy discovery
# ---------------------------------------------------------------------------

PATTERN_FILE = re.compile(r"strategy_(\d+)_.*\.py$")
PATTERN_CSV = re.compile(r'parser\.add_argument\("(--csv\w*)".*?help="(.*?)"')
PATTERN_NAME = re.compile(r'"""\s*\n\s*(.*?)\s*\n', re.DOTALL)
PATTERN_VIDEO = re.compile(r"Video:\s*(https?://\S+)")

TF_HINTS = {
    "1m": "1-minute", "5m": "5-minute", "15m": "15-minute",
    "1h": "1-hour", "4h": "4-hour", "daily": "Daily",
    "gold_5m": "Gold 5-minute", "silver_5m": "Silver 5-minute",
    "gold": "Gold 1-hour", "silver": "Silver 1-hour",
}


def infer_timeframe_hint(arg: str, help_text: str, docstring: str = "") -> str:
    # 1. Check help text (longest patterns first to avoid false matches)
    t = help_text.lower()
    patterns = sorted([
        ("15-minute", "15m"), ("5-minute", "5m"), ("1-minute", "1m"),
        ("4-hour", "4h"), ("1-hour", "1h"), ("daily", "Daily"),
        ("15m", "15m"), ("5m", "5m"), ("1m", "1m"),
        ("4h", "4h"), ("1h", "1h"),
    ], key=lambda x: -len(x[0]))
    for pattern, label in patterns:
        if pattern in t:
            return label
    # 2. Check arg name
    tf_in_arg = sorted(TF_HINTS.items(), key=lambda x: -len(x[0]))
    for tf, label in tf_in_arg:
        if tf in arg.lower():
            return label
    # 3. Fallback to docstring
    d = docstring.lower()
    for pattern, label in patterns:
        if pattern in d:
            return label
    return ""


def discover_strategies() -> list[dict]:
    strategies = []
    for f in sorted(os.listdir(STRATEGIES_DIR)):
        m = PATTERN_FILE.match(f)
        if not m:
            continue
        num = int(m.group(1))
        filepath = STRATEGIES_DIR / f
        content = filepath.read_text()

        name = f"Strategy {num}"
        doc_match = PATTERN_NAME.search(content)
        if doc_match:
            name = doc_match.group(1).strip()
            name = re.sub(r"^Strategy \d+:\s*", "", name)

        csv_args = []
        for arg, help_text in PATTERN_CSV.findall(content):
            tf_hint = infer_timeframe_hint(arg, help_text, content)
            csv_args.append({"arg": arg, "help": help_text, "timeframe": tf_hint})

        video = ""
        v_match = PATTERN_VIDEO.search(content)
        if v_match:
            video = v_match.group(1)

        strategies.append({
            "id": f"s{num:02d}",
            "file": f,
            "name": name,
            "num": num,
            "video": video,
            "csv_args": csv_args,
        })
    return strategies


_STRATEGIES = discover_strategies()


@app.get("/api/strategies")
def list_strategies():
    return _STRATEGIES


@app.get("/api/strategies/{sid}")
def get_strategy(sid: str):
    for s in _STRATEGIES:
        if s["id"] == sid:
            return s
    return JSONResponse({"error": "Strategy not found"}, status_code=404)


# ---------------------------------------------------------------------------
# Backtest execution
# ---------------------------------------------------------------------------

def match_files_to_args(csv_args: list[dict], uploaded: dict[str, str]) -> dict[str, str]:
    result = {}
    remaining = dict(uploaded)

    if len(csv_args) == 1:
        arg_name = csv_args[0]["arg"]
        if remaining:
            result[arg_name] = list(remaining.values())[0]
        return result

    tf_to_arg = {}
    for ca in csv_args:
        a = ca["arg"]
        for tf in ["daily", "1h", "4h", "5m", "15m", "1m", "gold_5m", "silver_5m", "gold", "silver"]:
            if tf in a:
                tf_to_arg[tf] = a
                break

    for tf_pattern, arg_name in tf_to_arg.items():
        for fname, fpath in list(remaining.items()):
            if tf_pattern.replace("_", "") in fname.lower().replace("_", ""):
                result[arg_name] = fpath
                del remaining[fname]
                break

    for fname, fpath in list(remaining.items()):
        for ca in csv_args:
            if ca["arg"] not in result:
                result[ca["arg"]] = fpath
                del remaining[fname]
                break

    return result


# ---------------------------------------------------------------------------
# Chart session (candlestick viewer)
# ---------------------------------------------------------------------------

def pick_chart_csv_path(file_map: dict[str, str], csv_args: list[dict]) -> Optional[str]:
    if len(file_map) == 1:
        return next(iter(file_map.values()))
    best_path: Optional[str] = None
    best_rank = len(CHART_TF_ORDER) + 1
    for ca in csv_args:
        arg = ca["arg"].lower()
        for i, tf in enumerate(CHART_TF_ORDER):
            if tf.replace("_", "") in arg.replace("_", ""):
                if i < best_rank and ca["arg"] in file_map:
                    best_rank = i
                    best_path = file_map[ca["arg"]]
                break
    return best_path or next(iter(file_map.values()), None)


def _parse_trade_ts(iso_str: str) -> Optional[int]:
    if not iso_str:
        return None
    try:
        return int(datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return None


def create_chart_session(csv_path: str, trades: list) -> str:
    session_id = uuid.uuid4().hex[:12]
    candles = load_csv(csv_path)
    CHART_SESSIONS[session_id] = {
        "candles": candles,
        "trades": trades,
        "total": len(candles),
    }
    return session_id


def _bars_from_candles(candles, start: int, count: int) -> list[dict]:
    return [
        {"time": c.timestamp, "open": c.open, "high": c.high, "low": c.low, "close": c.close}
        for c in candles[start:start + count]
    ]


def _snap_marker_time(candles, start: int, count: int, ts: int) -> int:
    chunk = candles[start:start + count]
    if not chunk:
        return ts
    best = chunk[0].timestamp
    best_diff = abs(best - ts)
    for c in chunk:
        diff = abs(c.timestamp - ts)
        if diff < best_diff:
            best_diff = diff
            best = c.timestamp
    return best


def _markers_for_range(trades: list, candles, bar_start: int, bar_count: int, t_min: int, t_max: int) -> list[dict]:
    markers = []
    for i, t in enumerate(trades):
        entry_ts = _parse_trade_ts(t.get("entry_time"))
        exit_ts = _parse_trade_ts(t.get("exit_time"))
        direction = (t.get("direction") or "").lower()
        outcome = t.get("outcome", "")

        if entry_ts and t_min <= entry_ts <= t_max:
            markers.append({
                "time": _snap_marker_time(candles, bar_start, bar_count, entry_ts),
                "position": "belowBar" if direction == "long" else "aboveBar",
                "color": "#58a6ff",
                "shape": "arrowUp" if direction == "long" else "arrowDown",
                "text": f"E{i + 1}",
            })
        if exit_ts and t_min <= exit_ts <= t_max:
            color = "#3fb950" if outcome == "win" else "#f85149" if outcome == "loss" else "#d29922"
            markers.append({
                "time": _snap_marker_time(candles, bar_start, bar_count, exit_ts),
                "position": "aboveBar" if direction == "long" else "belowBar",
                "color": color,
                "shape": "circle",
                "text": f"X{i + 1}",
            })
    markers.sort(key=lambda m: m["time"])
    return markers


def _locate_window_start(candles, ts: int, window: int = CHART_WINDOW_SIZE) -> int:
    if not candles:
        return 0
    lo, hi = 0, len(candles) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if candles[mid].timestamp < ts:
            lo = mid + 1
        else:
            hi = mid
    center = max(0, lo - window // 4)
    return min(center, max(0, len(candles) - window))


@app.get("/api/chart/{session_id}")
async def get_chart_window(session_id: str, start: int = 0, count: int = CHART_WINDOW_SIZE):
    session = CHART_SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"error": "Chart session not found"}, status_code=404)

    count = min(max(count, 10), 500)
    start = max(start, 0)
    total = session["total"]
    candles = session["candles"]
    if start >= total:
        return {"bars": [], "markers": [], "start": start, "count": 0, "total": total}

    actual = min(count, total - start)
    bars = _bars_from_candles(candles, start, actual)
    t_min = bars[0]["time"]
    t_max = bars[-1]["time"]
    markers = _markers_for_range(session["trades"], candles, start, actual, t_min, t_max)
    return {
        "bars": bars,
        "markers": markers,
        "start": start,
        "count": actual,
        "total": total,
        "window_size": CHART_WINDOW_SIZE,
    }


@app.get("/api/chart/{session_id}/locate")
async def locate_chart_time(session_id: str, ts: int):
    session = CHART_SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"error": "Chart session not found"}, status_code=404)
    start = _locate_window_start(session["candles"], ts)
    return {"start": start, "total": session["total"]}


def sanitize_for_json(obj: Any) -> Any:
    """Replace inf/nan floats so Starlette JSON responses do not crash."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    return obj


def compute_stats(trades: list) -> dict:
    default = lambda: {"total_trades": 0, "winning_trades": 0, "losing_trades": 0, "open_trades": 0,
        "win_rate": 0, "total_pnl_pips": 0, "profit_factor": 0, "avg_win_pips": 0, "avg_loss_pips": 0,
        "max_consecutive_wins": 0, "max_consecutive_losses": 0, "best_trade_pips": 0, "worst_trade_pips": 0}
    if not trades:
        return default()

    wins = [t for t in trades if t.get("outcome") == "win"]
    losses = [t for t in trades if t.get("outcome") == "loss"]
    opens = [t for t in trades if t.get("outcome") == "open"]
    total = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = round(win_count / total * 100, 2) if total else 0

    total_pnl = sum(trade_pnl_pips(t) for t in trades)
    gross_profit = sum(trade_pnl_pips(t) for t in wins)
    gross_loss = abs(sum(trade_pnl_pips(t) for t in losses))
    if gross_loss:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit:
        profit_factor = None  # wins only; UI shows infinity
    else:
        profit_factor = 0

    avg_win = round(gross_profit / win_count, 1) if wins else 0
    avg_loss = round(gross_loss / loss_count, 1) if losses else 0

    max_cons_w = max_cons_l = cw = cl = 0
    for t in trades:
        if t.get("outcome") == "win":
            cw += 1; cl = 0
            max_cons_w = max(max_cons_w, cw)
        elif t.get("outcome") == "loss":
            cl += 1; cw = 0
            max_cons_l = max(max_cons_l, cl)

    all_pips = [trade_pnl_pips(t) for t in trades]

    return {
        "total_trades": total,
        "winning_trades": win_count,
        "losing_trades": loss_count,
        "open_trades": len(opens),
        "win_rate": win_rate,
        "total_pnl_pips": round(total_pnl, 1),
        "profit_factor": profit_factor,
        "avg_win_pips": avg_win,
        "avg_loss_pips": avg_loss,
        "max_consecutive_wins": max_cons_w,
        "max_consecutive_losses": max_cons_l,
        "best_trade_pips": round(max(all_pips), 1) if all_pips else 0,
        "worst_trade_pips": round(min(all_pips), 1) if all_pips else 0,
    }


# ---------------------------------------------------------------------------
# Google Drive integration
# ---------------------------------------------------------------------------

DRIVE_FILE_PATTERN = re.compile(r"(?:https?://drive\.google\.com/file/d/|^|\s)([a-zA-Z0-9_-]{20,})")
DRIVE_FOLDER_PATTERN = re.compile(r"(?:https?://drive\.google\.com/drive/folders/|^|\s)([a-zA-Z0-9_-]{20,})")


class DriveFolderRequest(BaseModel):
    url: str


class SaveResultsRequest(BaseModel):
    strategy_id: str
    trades: list
    stats: dict


class DriveBacktestRequest(BaseModel):
    strategy_id: str
    drive_files: dict[str, str]  # arg -> drive URL or file ID
    symbol: str = DEFAULT_SYMBOL


class LibraryBacktestRequest(BaseModel):
    strategy_id: str
    symbol: str = DEFAULT_SYMBOL
    max_days: Optional[int] = None  # None = default window; 0 or negative = full history


def persist_backtest_results(strategy: dict, trades: list, stats: dict) -> str:
    """Save backtest output under dashboard/out/ inside the project."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = strategy["file"].replace(".py", "")
    filename = f"{strategy['id']}_{stem}_{ts}.json"
    out_path = OUT_DIR / filename
    payload = {
        "strategy_id": strategy["id"],
        "strategy_name": strategy["name"],
        "strategy_file": strategy["file"],
        "saved_at": datetime.now().isoformat(),
        "stats": stats,
        "trades": trades,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return str(out_path.relative_to(STRATEGIES_DIR)).replace("\\", "/")


@app.post("/api/save-results")
async def save_results(req: SaveResultsRequest):
    strategy = next((s for s in _STRATEGIES if s["id"] == req.strategy_id), None)
    if not strategy:
        return JSONResponse({"error": "Strategy not found"}, status_code=404)
    if not req.trades:
        return JSONResponse({"error": "No trades to save"}, status_code=400)
    saved_to = persist_backtest_results(strategy, req.trades, req.stats)
    return {"saved_to": saved_to, "message": f"Results saved to {saved_to}"}


@app.post("/api/drive/list-folder")
async def drive_list_folder(req: DriveFolderRequest):
    try:
        m = DRIVE_FOLDER_PATTERN.search(req.url.strip())
        folder_id = m.group(1) if m else req.url.strip()
        if not folder_id or len(folder_id) < 10:
            return JSONResponse({"error": "Could not extract folder ID from URL"}, status_code=400)

        files = gdown.download_folder(id=folder_id, output=str(TMP_DIR), skip_download=True)
        csv_files = []
        for f in (files or []):
            if hasattr(f, "name") and f.name.endswith(".csv"):
                csv_files.append({"name": f.name, "id": getattr(f, "id", ""), "size": getattr(f, "size", 0)})
            elif isinstance(f, dict):
                name = f.get("name") or f.get("title", "")
                if name.endswith(".csv"):
                    csv_files.append({"name": name, "id": f.get("id", ""), "size": f.get("size", 0)})
            elif isinstance(f, str) and f.endswith(".csv"):
                csv_files.append({"name": f, "id": "", "size": 0})

        return {"files": csv_files, "folder_id": folder_id}
    except Exception as e:
        return JSONResponse({"error": f"Drive error: {e}"}, status_code=500)


@app.get("/api/backtest/jobs/{job_id}")
def get_backtest_job(job_id: str):
    with JOBS_LOCK:
        job = BACKTEST_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    elapsed = max(0, int(time.time() - job["started_at"]))
    return {**job, "elapsed_sec": elapsed}


@app.get("/api/saved-links")
def get_saved_links():
    """Previously-used Drive links keyed 'SYMBOL_TF' (e.g. GBPUSD_1h)."""
    return _load_saved_links()


@app.post("/api/backtest/drive")
async def run_backtest_drive(req: DriveBacktestRequest):
    strategy = next((s for s in _STRATEGIES if s["id"] == req.strategy_id), None)
    if not strategy:
        return JSONResponse({"error": "Strategy not found"}, status_code=404)

    # Remember the links per SYMBOL_TF so the UI can suggest them next time.
    sym = (req.symbol or DEFAULT_SYMBOL).strip().upper()
    tf_by_arg = {
        ca["arg"]: normalize_tf_folder(ca.get("timeframe", ""), ca["arg"])
        for ca in strategy["csv_args"]
    }
    link_updates = {}
    for arg_name, url in req.drive_files.items():
        tf = tf_by_arg.get(arg_name)
        if tf and url and url.strip():
            link_updates[f"{sym}_{tf}"] = url.strip()
    if link_updates:
        _save_links(link_updates)

    job_dir = TMP_DIR / str(uuid.uuid4())
    job_dir.mkdir(parents=True, exist_ok=True)
    downloaded = {}
    for arg_name, url_or_id in req.drive_files.items():
        file_id = url_or_id.strip()
        m = DRIVE_FILE_PATTERN.search(file_id)
        if m:
            file_id = m.group(1)

        ext = ".csv"
        out_path = job_dir / f"{arg_name.replace('--', '')}_{file_id[:8]}{ext}"

        try:
            gdown.download(id=file_id, output=str(out_path), quiet=True)
            if not out_path.exists() or out_path.stat().st_size == 0:
                return JSONResponse({"error": f"Failed to download file for {arg_name}"}, status_code=500)
            downloaded[arg_name] = str(out_path)
        except Exception as e:
            return JSONResponse({"error": f"Error downloading {arg_name}: {e}"}, status_code=500)

    if len(downloaded) != len(strategy["csv_args"]):
        missing = [a["arg"] for a in strategy["csv_args"] if a["arg"] not in downloaded]
        return JSONResponse({"error": f"Missing files for: {missing}"}, status_code=400)

    return _start_backtest_job(
        strategy,
        downloaded,
        data_source="drive",
        symbol=sym,
        job_id=job_dir.name,
    )


@app.get("/api/data/library")
def get_data_library(strategy_id: str, symbol: str = DEFAULT_SYMBOL):
    strategy = next((s for s in _STRATEGIES if s["id"] == strategy_id), None)
    if not strategy:
        return JSONResponse({"error": "Strategy not found"}, status_code=404)
    return library_status(strategy["csv_args"], symbol=symbol)


def _update_job(job_id: str, **kwargs) -> None:
    with JOBS_LOCK:
        job = BACKTEST_JOBS.get(job_id)
        if not job:
            return
        job.update(kwargs)
        job["updated_at"] = time.time()


def _parse_progress_line(job_id: str, line: str) -> None:
    if not line.startswith("BT_PROGRESS "):
        return
    parts = line.strip().split(" ", 3)
    if len(parts) < 3:
        return
    try:
        phase = parts[1]
        pct = int(parts[2])
        msg = parts[3] if len(parts) > 3 else ""
    except ValueError:
        return
    if phase == "load_csv":
        progress = 15 + int(pct * 0.1)
    elif phase == "strategy":
        progress = 30 + int(pct * 0.55)
    else:
        progress = 20 + int(pct * 0.6)
    _update_job(job_id, phase=phase, progress=min(90, progress), message=msg or phase)


def _resolve_library_max_days(max_days: Optional[int]) -> Optional[int]:
    if max_days is None:
        return DEFAULT_LIBRARY_MAX_DAYS
    if max_days <= 0:
        return None
    return max_days


def _start_backtest_job(
    strategy: dict,
    file_map: dict[str, str],
    *,
    data_source: str,
    symbol: str,
    library_files: Optional[list[str]] = None,
    max_days: Optional[int] = None,
    job_id: Optional[str] = None,
) -> dict:
    job_id = job_id or str(uuid.uuid4())
    with JOBS_LOCK:
        BACKTEST_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "message": "Queued...",
            "log_tail": [],
            "started_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": None,
        }
    meta = {
        "data_source": data_source,
        "symbol": symbol,
        "library_files": library_files,
        "max_days": max_days,
    }
    thread = threading.Thread(
        target=_backtest_job_worker,
        args=(job_id, strategy, file_map, meta),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id}


def _backtest_job_worker(
    job_id: str,
    strategy: dict,
    file_map: dict[str, str],
    meta: dict,
) -> None:
    job_dir = TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        _update_job(
            job_id,
            status="running",
            phase="preparing",
            progress=3,
            message="Preparing market data...",
        )
        working_map = dict(file_map)
        trim_notes: list[str] = []
        max_days = meta.get("max_days")
        if max_days:
            def prep_progress(pct: int, msg: str) -> None:
                _update_job(job_id, phase="preparing", progress=3 + min(12, pct), message=msg)

            working_map, trim_notes = prepare_library_csv_window(
                file_map, max_days, job_dir, prep_progress
            )

        payload = _execute_backtest_job(job_id, strategy, working_map, str(job_dir))
        payload["data_source"] = meta.get("data_source", "unknown")
        payload["symbol"] = str(meta.get("symbol", DEFAULT_SYMBOL)).upper()
        if meta.get("library_files"):
            payload["library_files"] = meta["library_files"]
        if trim_notes:
            payload["data_window"] = trim_notes
        _update_job(
            job_id,
            status="completed",
            phase="done",
            progress=100,
            message=f"Completed with {payload['stats'].get('total_trades', 0)} trades",
            result=payload,
        )
    except subprocess.TimeoutExpired:
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            progress=100,
            error=f"Backtest timed out after {BACKTEST_TIMEOUT_SEC}s",
            message=f"Timed out after {BACKTEST_TIMEOUT_SEC}s",
        )
    except Exception as e:
        _update_job(
            job_id,
            status="failed",
            phase="failed",
            progress=100,
            error=str(e),
            message=str(e),
        )


def _execute_backtest_job(
    job_id: str,
    strategy: dict,
    file_map: dict[str, str],
    tmpdir: str,
) -> dict:
    script = STRATEGIES_DIR / strategy["file"]
    output_path = os.path.join(tmpdir, "results.json")
    cmd = [sys.executable, str(script)]

    for arg_name, fpath in file_map.items():
        cmd.extend([arg_name, fpath])
    cmd.extend(["--output", output_path])

    _update_job(
        job_id,
        phase="running",
        progress=18,
        message=f"Running {strategy['name']}...",
    )

    env = {**os.environ, "BT_PROGRESS": "1", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )

    stdout_parts: list[str] = []
    log_tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        stdout_parts.append(line)
        log_tail.append(line)
        if len(log_tail) > 40:
            log_tail.pop(0)
        _parse_progress_line(job_id, line)
        if "Saved" in line and "trades" in line:
            _update_job(job_id, message=line, progress=88)
        _update_job(job_id, log_tail=list(log_tail))

    try:
        proc.wait(timeout=BACKTEST_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        raise

    stdout = "\n".join(stdout_parts)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Backtest failed (exit code {proc.returncode})\n{stdout[-2000:]}"
        )

    if not os.path.exists(output_path):
        raise RuntimeError(f"No output file generated\n{stdout[-2000:]}")

    _update_job(job_id, phase="finalizing", progress=92, message="Computing stats...")

    with open(output_path) as f:
        trades = json.load(f)

    if not isinstance(trades, list):
        trades = [trades]

    enrich_trades_pnl(trades)
    stats = compute_stats(trades)
    trades = sanitize_for_json(trades)
    stats = sanitize_for_json(stats)
    saved_to = persist_backtest_results(strategy, trades, stats)
    chart_path = pick_chart_csv_path(file_map, strategy["csv_args"])
    chart_session = create_chart_session(chart_path, trades) if chart_path else None
    return {
        "trades": trades,
        "stats": stats,
        "stdout": stdout.strip(),
        "saved_to": saved_to,
        "chart_session": chart_session,
    }


def _execute_backtest(strategy: dict, file_map: dict[str, str], tmpdir: str) -> dict:
    script = STRATEGIES_DIR / strategy["file"]
    output_path = os.path.join(tmpdir, "results.json")
    cmd = [sys.executable, str(script)]

    for arg_name, fpath in file_map.items():
        cmd.extend([arg_name, fpath])
    cmd.extend(["--output", output_path])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=BACKTEST_TIMEOUT_SEC)
        if result.returncode != 0:
            return JSONResponse({
                "error": f"Backtest failed (exit code {result.returncode})",
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }, status_code=500)

        if not os.path.exists(output_path):
            return JSONResponse({
                "error": "No output file generated",
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }, status_code=500)

        with open(output_path) as f:
            trades = json.load(f)

        if not isinstance(trades, list):
            trades = [trades]

        enrich_trades_pnl(trades)
        stats = compute_stats(trades)
        trades = sanitize_for_json(trades)
        stats = sanitize_for_json(stats)
        saved_to = persist_backtest_results(strategy, trades, stats)
        chart_path = pick_chart_csv_path(file_map, strategy["csv_args"])
        chart_session = create_chart_session(chart_path, trades) if chart_path else None
        payload = {
            "trades": trades,
            "stats": stats,
            "stdout": result.stdout.strip(),
            "saved_to": saved_to,
            "chart_session": chart_session,
        }
        return payload

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": f"Backtest timed out after {BACKTEST_TIMEOUT_SEC}s"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


def _package_backtest_result(
    strategy: dict,
    arg_paths: dict[str, str],
    *,
    data_source: str,
    symbol: str,
    library_files: Optional[list[str]] = None,
):
    with tempfile.TemporaryDirectory() as tmpdir:
        result = _execute_backtest(strategy, arg_paths, tmpdir)
        if isinstance(result, JSONResponse):
            return result
        result["data_source"] = data_source
        result["symbol"] = symbol.upper()
        if library_files:
            result["library_files"] = library_files
        return result


# Refactor existing backtest to use _execute_backtest
@app.post("/api/backtest/library")
async def run_backtest_library(req: LibraryBacktestRequest):
    """Run backtest using CSVs from the local instrument data library (no upload)."""
    strategy = next((s for s in _STRATEGIES if s["id"] == req.strategy_id), None)
    if not strategy:
        return JSONResponse({"error": "Strategy not found"}, status_code=404)
    try:
        arg_paths, library_files = resolve_strategy_data(strategy["csv_args"], symbol=req.symbol)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return _start_backtest_job(
        strategy,
        arg_paths,
        data_source="library",
        symbol=req.symbol,
        library_files=library_files,
        max_days=_resolve_library_max_days(req.max_days),
    )


@app.post("/api/backtest")
async def run_backtest(
    strategy_id: str = Form(...),
    symbol: str = Form(DEFAULT_SYMBOL),
    max_days: str = Form(""),
    files: list[UploadFile] | None = File(default=None),
):
    strategy = next((s for s in _STRATEGIES if s["id"] == strategy_id), None)
    if not strategy:
        return JSONResponse({"error": "Strategy not found"}, status_code=404)

    parsed_max_days: Optional[int] = None
    if max_days.strip():
        try:
            parsed_max_days = int(max_days)
        except ValueError:
            return JSONResponse({"error": "Invalid max_days value"}, status_code=400)

    if not files:
        try:
            arg_paths, library_files = resolve_strategy_data(
                strategy["csv_args"], symbol=symbol
            )
        except FileNotFoundError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return _start_backtest_job(
            strategy,
            arg_paths,
            data_source="library",
            symbol=symbol,
            library_files=library_files,
            max_days=_resolve_library_max_days(parsed_max_days),
        )

    job_dir = TMP_DIR / str(uuid.uuid4())
    job_dir.mkdir(parents=True, exist_ok=True)
    uploaded = {}
    for f in files:
        if not f.filename:
            continue
        fpath = job_dir / f.filename
        with open(fpath, "wb") as out:
            out.write(await f.read())
        uploaded[f.filename] = str(fpath)

    if not uploaded:
        return JSONResponse({"error": "No CSV files uploaded"}, status_code=400)

    arg_paths = match_files_to_args(strategy["csv_args"], uploaded)
    if len(arg_paths) != len(strategy["csv_args"]):
        missing = [a["arg"] for a in strategy["csv_args"] if a["arg"] not in arg_paths]
        return JSONResponse({
            "error": f"Could not match files to all required CSV arguments: {missing}. "
                     f"Uploaded: {list(uploaded.keys())}"
        }, status_code=400)

    return _start_backtest_job(
        strategy,
        arg_paths,
        data_source="upload",
        symbol=symbol,
        max_days=_resolve_library_max_days(parsed_max_days),
        job_id=job_dir.name,
    )


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8001, reload=True)
