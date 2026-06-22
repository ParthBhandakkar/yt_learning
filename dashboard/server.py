"""
Server for the standalone strategy dashboard.
Auto-discovers strategy scripts, accepts CSV uploads, runs backtests, returns results.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import gdown
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

STRATEGIES_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
OUT_DIR = Path(__file__).resolve().parent / "out"

OUT_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Standalone Strategy Backtester")

DELAYED_IMPORTS: dict[str, dict] = {}

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

    total_pnl = sum(t.get("pnl_pips", 0) for t in trades)
    gross_profit = sum(t.get("pnl_pips", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl_pips", 0) for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else (float("inf") if gross_profit else 0)

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

    all_pips = [t.get("pnl_pips", 0) for t in trades]

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


class DriveBacktestRequest(BaseModel):
    strategy_id: str
    drive_files: dict[str, str]  # arg -> drive URL or file ID


@app.post("/api/drive/list-folder")
async def drive_list_folder(req: DriveFolderRequest):
    try:
        m = DRIVE_FOLDER_PATTERN.search(req.url.strip())
        folder_id = m.group(1) if m else req.url.strip()
        if not folder_id or len(folder_id) < 10:
            return JSONResponse({"error": "Could not extract folder ID from URL"}, status_code=400)

        files = gdown.download_folder(id=folder_id, output="/tmp", skip_download=True)
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


@app.post("/api/backtest/drive")
async def run_backtest_drive(req: DriveBacktestRequest):
    strategy = next((s for s in _STRATEGIES if s["id"] == req.strategy_id), None)
    if not strategy:
        return JSONResponse({"error": "Strategy not found"}, status_code=404)

    with tempfile.TemporaryDirectory() as tmpdir:
        downloaded = {}
        for arg_name, url_or_id in req.drive_files.items():
            file_id = url_or_id.strip()
            m = DRIVE_FILE_PATTERN.search(file_id)
            if m:
                file_id = m.group(1)

            ext = ".csv"
            out_path = os.path.join(tmpdir, f"{arg_name.replace('--', '')}_{file_id[:8]}{ext}")

            try:
                gdown.download(id=file_id, output=out_path, quiet=True)
                if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                    return JSONResponse({"error": f"Failed to download file for {arg_name}"}, status_code=500)
                downloaded[arg_name] = out_path
            except Exception as e:
                return JSONResponse({"error": f"Error downloading {arg_name}: {e}"}, status_code=500)

        if len(downloaded) != len(strategy["csv_args"]):
            missing = [a["arg"] for a in strategy["csv_args"] if a["arg"] not in downloaded]
            return JSONResponse({"error": f"Missing files for: {missing}"}, status_code=400)

        return _execute_backtest(strategy, downloaded, tmpdir)


def _execute_backtest(strategy: dict, file_map: dict[str, str], tmpdir: str) -> dict:
    import shutil
    script = STRATEGIES_DIR / strategy["file"]
    output_path = os.path.join(tmpdir, "results.json")
    cmd = [sys.executable, str(script)]

    renamed = {}
    for i, (arg_name, fpath) in enumerate(file_map.items()):
        safe_name = f"data_1h_20200101_20991231_{i}.csv"
        new_path = os.path.join(tmpdir, safe_name)
        shutil.copy2(fpath, new_path)
        cmd.extend([arg_name, new_path])
    cmd.extend(["--output", output_path])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
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

        stats = compute_stats(trades)
        return {"trades": trades, "stats": stats, "stdout": result.stdout.strip()}

    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Backtest timed out after 180s"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# Refactor existing backtest to use _execute_backtest
@app.post("/api/backtest")
async def run_backtest(strategy_id: str = Form(...), files: list[UploadFile] = File(...)):
    strategy = next((s for s in _STRATEGIES if s["id"] == strategy_id), None)
    if not strategy:
        return JSONResponse({"error": "Strategy not found"}, status_code=404)

    with tempfile.TemporaryDirectory() as tmpdir:
        uploaded = {}
        for f in files:
            fpath = os.path.join(tmpdir, f.filename)
            with open(fpath, "wb") as out:
                out.write(await f.read())
            uploaded[f.filename] = fpath

        if not uploaded:
            return JSONResponse({"error": "No CSV files uploaded"}, status_code=400)

        arg_paths = match_files_to_args(strategy["csv_args"], uploaded)
        if len(arg_paths) != len(strategy["csv_args"]):
            missing = [a["arg"] for a in strategy["csv_args"] if a["arg"] not in arg_paths]
            return JSONResponse({
                "error": f"Could not match files to all required CSV arguments: {missing}. "
                         f"Uploaded: {list(uploaded.keys())}"
            }, status_code=400)

        return _execute_backtest(strategy, arg_paths, tmpdir)


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8001, reload=True)
