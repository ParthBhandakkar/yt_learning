#!/usr/bin/env python3
"""
Batch-backtest all discovered strategies on XAUUSD (1-year + full history).

Usage:
  set YT_DATA_ROOT=O:\\D temp\\UltimateTradeBot\\Data\\Exness\\structured\\history
  python batch_xauusd_backtest.py
  python batch_xauusd_backtest.py --windows 365
  python batch_xauusd_backtest.py --only s95,s90 --windows 365,0
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

from core import enrich_trades_pnl  # noqa: E402
from data_library import (  # noqa: E402
    DEFAULT_SYMBOL,
    find_instrument_csv,
    normalize_tf_folder,
    prepare_library_csv_window,
    resolve_strategy_data,
)

# Mirror dashboard.server discovery (avoid importing FastAPI app).
PATTERN_FILE = re.compile(r"^strategy_(\d+)_.*\.py$")
PATTERN_NAME = re.compile(r'Strategy\s+\d+:\s*(.+)')
PATTERN_CSV = re.compile(
    r"""parser\.add_argument\(\s*["'](--csv[^"']*)["']\s*,.*?help\s*=\s*["']([^"']*)["']""",
    re.S,
)
TF_HINTS = {
    "1m": "1-minute", "5m": "5-minute", "15m": "15-minute",
    "1h": "1-hour", "4h": "4-hour", "daily": "Daily",
    "gold_5m": "Gold 5-minute", "silver_5m": "Silver 5-minute",
    "gold": "Gold 1-hour", "silver": "Silver 1-hour",
}

OUT_DIR = ROOT / "dashboard" / "out"
BATCH_DIR = OUT_DIR / "batch_xauusd"
DEFAULT_TIMEOUT_SEC = int(os.environ.get("YT_BATCH_TIMEOUT_SEC", str(45 * 60)))
# Exness XAUUSD: 1 pip = $0.01; framework metals pip = $1 → ×100
EXNESS_PIP = 0.01
FRAMEWORK_METAL_PIP = 1.0
PYTHON = sys.executable


def infer_timeframe_hint(arg: str, help_text: str, docstring: str = "") -> str:
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
    for tf, label in sorted(TF_HINTS.items(), key=lambda x: -len(x[0])):
        if tf in arg.lower():
            return label
    d = docstring.lower()
    for pattern, label in patterns:
        if pattern in d:
            return label
    return ""


def discover_strategies() -> list[dict]:
    strategies = []
    for f in sorted(os.listdir(ROOT)):
        m = PATTERN_FILE.match(f)
        if not m:
            continue
        num = int(m.group(1))
        content = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        name = f"Strategy {num}"
        doc_match = PATTERN_NAME.search(content)
        if doc_match:
            name = re.sub(r"^Strategy \d+:\s*", "", doc_match.group(1).strip())
        csv_args = []
        for arg, help_text in PATTERN_CSV.findall(content):
            tf_hint = infer_timeframe_hint(arg, help_text, content)
            csv_args.append({"arg": arg, "help": help_text, "timeframe": tf_hint})
        strategies.append({
            "id": f"s{num:02d}",
            "file": f,
            "name": name,
            "num": num,
            "csv_args": csv_args,
        })
    return strategies


def trade_pnl_pips(t: dict) -> float:
    v = t.get("pnl_pips")
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def compute_stats(trades: list) -> dict:
    if not trades:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0, "total_pnl_pips": 0, "profit_factor": 0,
            "avg_win_pips": 0, "avg_loss_pips": 0,
            "total_pnl_R": 0, "avg_R": 0,
        }

    wins = [t for t in trades if t.get("outcome") == "win"]
    losses = [t for t in trades if t.get("outcome") == "loss"]
    total = len(trades)
    win_rate = round(len(wins) / total * 100, 2) if total else 0
    total_pnl = sum(trade_pnl_pips(t) for t in trades)
    gross_profit = sum(trade_pnl_pips(t) for t in wins)
    gross_loss = abs(sum(trade_pnl_pips(t) for t in losses))
    if gross_loss:
        profit_factor = round(gross_profit / gross_loss, 2)
    elif gross_profit:
        profit_factor = None
    else:
        profit_factor = 0

    r_vals = []
    for t in trades:
        if t.get("pnl_R") is not None:
            try:
                r_vals.append(float(t["pnl_R"]))
            except (TypeError, ValueError):
                pass
        elif t.get("entry_price") is not None and t.get("stop_loss") is not None:
            try:
                risk = abs(float(t["entry_price"]) - float(t["stop_loss"]))
                if risk > 0 and t.get("pnl_pips") is not None:
                    # Approximate R from pips / risk-in-pips when strategy didn't set pnl_R
                    from core import infer_pip_size
                    pip = infer_pip_size(float(t["entry_price"]))
                    risk_pips = risk / pip
                    if risk_pips > 0:
                        r_vals.append(float(t["pnl_pips"]) / risk_pips)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    total_r = round(sum(r_vals), 3) if r_vals else 0
    avg_r = round(sum(r_vals) / len(r_vals), 3) if r_vals else 0

    return {
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": win_rate,
        "total_pnl_pips": round(total_pnl, 1),
        "profit_factor": profit_factor,
        "avg_win_pips": round(gross_profit / len(wins), 1) if wins else 0,
        "avg_loss_pips": round(gross_loss / len(losses), 1) if losses else 0,
        "total_pnl_R": total_r,
        "avg_R": avg_r,
    }


def resolve_file_map(strategy: dict, symbol: str = "XAUUSD") -> tuple[dict[str, str], list[str]]:
    """Resolve CSVs; Strategy 13 needs XAGUSD for silver args."""
    csv_args = strategy["csv_args"]
    if strategy["file"] == "strategy_13_3step_ict_gold.py":
        from data_library import data_root

        file_map: dict[str, str] = {}
        labels: list[str] = []
        root = data_root()
        for ca in csv_args:
            arg = ca["arg"]
            arg_l = arg.lower()
            sym = "XAGUSD" if "silver" in arg_l else "XAUUSD"
            if "5m" in arg_l:
                tf_folder = "5m"
            else:
                tf_folder = "1h"
            path = find_instrument_csv(root, sym, tf_folder)
            if path is None:
                raise FileNotFoundError(f"Missing {sym}/{tf_folder} for {arg}")
            file_map[arg] = str(path)
            labels.append(f"{sym}/{tf_folder}/{path.name}")
        return file_map, labels

    return resolve_strategy_data(csv_args, symbol=symbol)


def load_trades(output_path: Path) -> list[dict]:
    if not output_path.exists():
        return []
    data = json.loads(output_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("trades"), list):
            return data["trades"]
    return []


def pf_sort_key(row: dict) -> tuple:
    pf = row.get("profit_factor")
    if pf is None:
        pf_val = float("inf")
    else:
        try:
            pf_val = float(pf)
        except (TypeError, ValueError):
            pf_val = -1.0
    total_r = float(row.get("total_pnl_R") or 0)
    total_pips = float(row.get("total_pnl_pips") or 0)
    return (pf_val, total_r, total_pips)


def run_one(
    strategy: dict,
    max_days: int,
    timeout_sec: int,
) -> dict[str, Any]:
    sid = strategy["id"]
    window_label = "full" if max_days <= 0 else f"{max_days}d"
    job_dir = BATCH_DIR / f"{sid}_{window_label}"
    job_dir.mkdir(parents=True, exist_ok=True)
    out_json = job_dir / "results.json"
    meta: dict[str, Any] = {
        "strategy_id": sid,
        "file": strategy["file"],
        "name": strategy["name"],
        "window": window_label,
        "max_days": max_days,
        "status": "pending",
    }

    try:
        file_map, labels = resolve_file_map(strategy, symbol=DEFAULT_SYMBOL)
        working_map, notes = prepare_library_csv_window(
            file_map, max_days if max_days > 0 else None, job_dir
        )
        meta["library_files"] = labels
        meta["trim_notes"] = notes

        cmd = [PYTHON, str(ROOT / strategy["file"])]
        for arg, path in working_map.items():
            cmd.extend([arg, path])
        cmd.extend(["--output", str(out_json)])
        if strategy["file"] == "strategy_95_mss_ob_refined.py":
            cmd.append("--strict-mss-causal")
        meta["cmd"] = cmd

        t0 = time.time()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(ROOT),
        )
        elapsed = round(time.time() - t0, 1)
        meta["elapsed_sec"] = elapsed
        meta["returncode"] = proc.returncode
        meta["stdout_tail"] = (proc.stdout or "")[-2000:]
        meta["stderr_tail"] = (proc.stderr or "")[-2000:]

        if proc.returncode != 0:
            meta["status"] = "error"
            meta["error"] = f"exit {proc.returncode}"
            return meta

        trades = load_trades(out_json)
        trades = enrich_trades_pnl(trades)
        stats = compute_stats(trades)
        meta["status"] = "ok"
        meta["stats"] = stats
        meta["trade_count"] = stats["total_trades"]
        # Persist enriched trades for inspection
        enriched_path = job_dir / "results_enriched.json"
        enriched_path.write_text(
            json.dumps({"stats": stats, "trades": trades}, indent=2, default=str),
            encoding="utf-8",
        )
        return meta
    except subprocess.TimeoutExpired:
        meta["status"] = "timeout"
        meta["error"] = f"timeout after {timeout_sec}s"
        return meta
    except Exception as exc:
        meta["status"] = "error"
        meta["error"] = str(exc)
        meta["traceback"] = traceback.format_exc()[-2000:]
        return meta


def _exness_pips_from_framework(framework_pips) -> Optional[float]:
    if framework_pips is None or framework_pips == "":
        return None
    try:
        return round(float(framework_pips) * (FRAMEWORK_METAL_PIP / EXNESS_PIP), 1)
    except (TypeError, ValueError):
        return None


def write_summary(
    rows: list[dict], summary_stem: str = "batch_xauusd_summary"
) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_json = OUT_DIR / f"{summary_stem}.json"
    summary_csv = OUT_DIR / f"{summary_stem}.csv"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "symbol": "XAUUSD",
        "broker": "Exness",
        "exness_pip_size": EXNESS_PIP,
        "bt_cost_price": os.environ.get("BT_COST_PRICE", ""),
        "python": PYTHON,
        "data_root": os.environ.get("YT_DATA_ROOT", ""),
        "batch_dir": str(BATCH_DIR),
        "results": rows,
    }
    summary_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    fieldnames = [
        "strategy_id", "name", "window", "status", "total_trades", "win_rate",
        "profit_factor", "total_pnl_pips", "total_pnl_exness_pips", "total_pnl_R",
        "avg_R", "elapsed_sec", "error",
    ]
    with open(summary_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            stats = r.get("stats") or {}
            fw = stats.get("total_pnl_pips", "")
            w.writerow({
                "strategy_id": r.get("strategy_id"),
                "name": r.get("name"),
                "window": r.get("window"),
                "status": r.get("status"),
                "total_trades": stats.get("total_trades", r.get("trade_count", "")),
                "win_rate": stats.get("win_rate", ""),
                "profit_factor": stats.get("profit_factor", ""),
                "total_pnl_pips": fw,
                "total_pnl_exness_pips": _exness_pips_from_framework(fw),
                "total_pnl_R": stats.get("total_pnl_R", ""),
                "avg_R": stats.get("avg_R", ""),
                "elapsed_sec": r.get("elapsed_sec", ""),
                "error": r.get("error", ""),
            })
    return summary_json, summary_csv


def pick_best(rows: list[dict], window: str, min_trades: int = 20) -> Optional[dict]:
    ok = [
        r for r in rows
        if r.get("window") == window
        and r.get("status") == "ok"
        and (r.get("stats") or {}).get("total_trades", 0) >= min_trades
    ]
    if not ok:
        # Fall back to any ok run with trades
        ok = [
            r for r in rows
            if r.get("window") == window
            and r.get("status") == "ok"
            and (r.get("stats") or {}).get("total_trades", 0) > 0
        ]
    if not ok:
        return None
    return max(ok, key=lambda r: pf_sort_key(r.get("stats") or {}))


def main():
    global BATCH_DIR

    parser = argparse.ArgumentParser(description="Batch XAUUSD strategy backtests")
    parser.add_argument(
        "--windows",
        default="365,0",
        help="Comma-separated max_days values (0 = full history)",
    )
    parser.add_argument("--only", default="", help="Comma-separated strategy ids e.g. s90,s95")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument(
        "--symbol",
        default=os.environ.get("YT_DEFAULT_SYMBOL", "XAUUSD"),
    )
    parser.add_argument(
        "--batch-dir",
        default="",
        help="Per-strategy job directory (default: dashboard/out/batch_xauusd)",
    )
    parser.add_argument(
        "--summary-stem",
        default="batch_xauusd_summary",
        help="Summary filename stem under dashboard/out/",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing results_enriched.json and re-run every job",
    )
    parser.add_argument(
        "--exness-cost",
        action="store_true",
        help="Force BT_COST_PRICE=0.45 (Exness Standard-style XAUUSD round-turn)",
    )
    args = parser.parse_args()

    if not os.environ.get("YT_DATA_ROOT"):
        os.environ["YT_DATA_ROOT"] = (
            r"O:\D temp\UltimateTradeBot\Data\Exness\structured\history"
        )
    if args.exness_cost and not os.environ.get("BT_COST_PRICE"):
        os.environ["BT_COST_PRICE"] = "0.45"
    if args.batch_dir:
        BATCH_DIR = Path(args.batch_dir)
        if not BATCH_DIR.is_absolute():
            BATCH_DIR = ROOT / BATCH_DIR

    windows = [int(x.strip()) for x in args.windows.split(",") if x.strip() != ""]
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    strategies = discover_strategies()
    if only:
        strategies = [s for s in strategies if s["id"] in only or f"s{s['num']}" in only]

    print(f"Python: {PYTHON}")
    print(f"YT_DATA_ROOT: {os.environ.get('YT_DATA_ROOT')}")
    print(f"BT_COST_PRICE: {os.environ.get('BT_COST_PRICE', '(default class model)')}")
    print(f"BATCH_DIR: {BATCH_DIR}")
    print(f"Strategies: {len(strategies)} | windows: {windows} | timeout: {args.timeout}s")
    print(f"no_resume={args.no_resume}")
    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for s in strategies:
        for max_days in windows:
            window_label = "full" if max_days <= 0 else f"{max_days}d"
            enriched = BATCH_DIR / f"{s['id']}_{window_label}" / "results_enriched.json"
            if (not args.no_resume) and enriched.exists():
                try:
                    data = json.loads(enriched.read_text(encoding="utf-8"))
                    row = {
                        "strategy_id": s["id"],
                        "file": s["file"],
                        "name": s["name"],
                        "window": window_label,
                        "max_days": max_days,
                        "status": "ok",
                        "stats": data.get("stats"),
                        "trade_count": (data.get("stats") or {}).get("total_trades", 0),
                        "elapsed_sec": None,
                        "resumed": True,
                    }
                    rows.append(row)
                    print(f"[skip/resume] {s['id']} {window_label} trades={row['trade_count']}")
                    write_summary(rows, args.summary_stem)
                    continue
                except Exception:
                    pass

            print(f"[run] {s['id']} {s['file']} window={window_label} ...", flush=True)
            row = run_one(s, max_days, args.timeout)
            rows.append(row)
            st = row.get("stats") or {}
            exness = _exness_pips_from_framework(st.get("total_pnl_pips"))
            print(
                f"  -> {row['status']} trades={st.get('total_trades', 0)} "
                f"PF={st.get('profit_factor')} pnl_R={st.get('total_pnl_R')} "
                f"exness_pips={exness} elapsed={row.get('elapsed_sec')}s "
                f"err={row.get('error', '')}",
                flush=True,
            )
            write_summary(rows, args.summary_stem)

    summary_json, summary_csv = write_summary(rows, args.summary_stem)
    print(f"\nWrote {summary_json}")
    print(f"Wrote {summary_csv}")

    for window in ["365d", "full"]:
        best = pick_best(rows, window, min_trades=20)
        if best:
            st = best.get("stats") or {}
            print(
                f"BEST {window}: {best['strategy_id']} {best['name']} | "
                f"trades={st.get('total_trades')} win={st.get('win_rate')}% "
                f"PF={st.get('profit_factor')} pnl_R={st.get('total_pnl_R')} "
                f"pips={st.get('total_pnl_pips')} "
                f"exness={_exness_pips_from_framework(st.get('total_pnl_pips'))}"
            )
        else:
            print(f"BEST {window}: none qualifying")


if __name__ == "__main__":
    main()
