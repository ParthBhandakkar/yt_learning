#!/usr/bin/env python3
"""Compare remote live s96/s97 vs our s98 (+ s90/s91/s13) on XAUUSD with Exness costs."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

os.environ.setdefault("BT_COST_PRICE", "0.45")
os.environ.setdefault(
    "YT_DATA_ROOT",
    r"O:\D temp\UltimateTradeBot\Data\Exness\structured\history",
)

from core import (  # noqa: E402
    EXNESS_XAUUSD_PIP,
    enrich_trades_pnl,
    framework_pips_to_exness,
    load_csv,
    round_turn_cost_price,
)
from data_library import find_instrument_csv, data_root, prepare_library_csv_window  # noqa: E402
from batch_xauusd_backtest import compute_stats, discover_strategies, run_one  # noqa: E402

OUT = ROOT / "dashboard" / "out" / "compare_remote_live"
PYTHON = sys.executable


def stats_with_exness(trades: list) -> dict:
    trades = enrich_trades_pnl(trades)
    st = compute_stats(trades)
    ref = 2000.0
    for t in trades:
        if t.get("entry_price"):
            ref = float(t["entry_price"])
            break
    fw = float(st.get("total_pnl_pips") or 0)
    st["total_pnl_exness_pips"] = round(framework_pips_to_exness(fw, ref), 1)
    st["cost_exness_pips"] = round(round_turn_cost_price(ref) / EXNESS_XAUUSD_PIP, 1)
    return st, trades


def window_path(src: Path, max_days: int, job: Path) -> Path:
    file_map = {"--csv": str(src)}
    working, _ = prepare_library_csv_window(
        file_map, max_days if max_days > 0 else None, job
    )
    return Path(working["--csv"])


def run_s97(max_days: int) -> dict:
    import strategy_97_trend_meanreversion as s97

    root = data_root()
    csv4h = find_instrument_csv(root, "XAUUSD", "4h")
    job = OUT / f"s97_{'full' if max_days <= 0 else f'{max_days}d'}"
    job.mkdir(parents=True, exist_ok=True)
    path = window_path(csv4h, max_days, job)
    out_json = job / "results.json"
    t0 = datetime.now()
    trades = s97.run_strategy(s97._df_from_csv(str(path)), str(out_json), symbol="XAUUSD")
    st, trades = stats_with_exness(trades)
    elapsed = (datetime.now() - t0).total_seconds()
    (job / "results_enriched.json").write_text(
        json.dumps({"stats": st, "trades": trades}, indent=2, default=str), encoding="utf-8"
    )
    return {
        "strategy_id": "s97",
        "name": "Trend Mean-Reversion (remote live)",
        "window": "full" if max_days <= 0 else f"{max_days}d",
        "status": "ok",
        "stats": st,
        "elapsed_sec": round(elapsed, 1),
        "source": "origin/live",
    }


def run_s96_remote(max_days: int) -> dict:
    import strategy_96_mss_ob_tuned as s96

    root = data_root()
    tfs = {"4h": "4h", "1h": "1h", "15m": "15m", "5m": "5m"}
    paths = {k: find_instrument_csv(root, "XAUUSD", v) for k, v in tfs.items()}
    missing = [k for k, p in paths.items() if p is None]
    if missing:
        return {
            "strategy_id": "s96",
            "name": "Tuned MSS+OB (remote live)",
            "window": "full" if max_days <= 0 else f"{max_days}d",
            "status": "error",
            "error": f"missing TFs: {missing}",
            "stats": {},
            "source": "origin/live",
        }

    job = OUT / f"s96_remote_{'full' if max_days <= 0 else f'{max_days}d'}"
    job.mkdir(parents=True, exist_ok=True)
    file_map = {
        "--csv4h": str(paths["4h"]),
        "--csv1h": str(paths["1h"]),
        "--csv15m": str(paths["15m"]),
        "--csv5m": str(paths["5m"]),
    }
    working, notes = prepare_library_csv_window(
        file_map, max_days if max_days > 0 else None, job
    )
    out_json = job / "results.json"
    t0 = datetime.now()
    try:
        trades = s96.run_strategy(
            s96._df_from_csv(working["--csv4h"]),
            s96._df_from_csv(working["--csv1h"]),
            s96._df_from_csv(working["--csv15m"]),
            s96._df_from_csv(working["--csv5m"]),
            str(out_json),
            symbol="XAUUSD",
        )
        st, trades = stats_with_exness(trades)
        status = "ok"
        err = ""
    except Exception as exc:
        st, trades = {}, []
        status = "error"
        err = str(exc)
    elapsed = (datetime.now() - t0).total_seconds()
    (job / "results_enriched.json").write_text(
        json.dumps({"stats": st, "trades": trades, "notes": notes, "error": err},
                   indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "strategy_id": "s96",
        "name": "Tuned MSS+OB (remote live)",
        "window": "full" if max_days <= 0 else f"{max_days}d",
        "status": status,
        "stats": st,
        "elapsed_sec": round(elapsed, 1),
        "error": err,
        "source": "origin/live",
        "trim_notes": notes,
    }


def run_via_batch(sid: str, max_days: int) -> dict:
    strategies = {s["id"]: s for s in discover_strategies()}
    # Prefer unique file match for s98
    if sid == "s98":
        s = next(x for x in discover_strategies() if x["file"].startswith("strategy_98_"))
    elif sid == "s96":
        s = next(x for x in discover_strategies() if x["file"] == "strategy_96_mss_ob_tuned.py")
    else:
        s = strategies[sid]
    # Point batch dir into compare folder
    import batch_xauusd_backtest as batch

    batch.BATCH_DIR = OUT / "batch_jobs"
    batch.BATCH_DIR.mkdir(parents=True, exist_ok=True)
    row = run_one(s, max_days, timeout_sec=1800)
    st = row.get("stats") or {}
    if st:
        fw = st.get("total_pnl_pips")
        if fw is not None and fw != "":
            st["total_pnl_exness_pips"] = round(framework_pips_to_exness(float(fw)), 1)
    row["stats"] = st
    row["source"] = "local"
    return row


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"BT_COST_PRICE={os.environ.get('BT_COST_PRICE')}")
    rows = []

    for max_days in (365, 0):
        label = "full" if max_days <= 0 else f"{max_days}d"
        print(f"\n=== window {label} ===")

        print("Running remote s96 (MSS+OB tuned)...")
        r = run_s96_remote(max_days)
        rows.append(r)
        st = r.get("stats") or {}
        print(f"  s96 remote -> {r['status']} trades={st.get('total_trades')} "
              f"PF={st.get('profit_factor')} exness={st.get('total_pnl_exness_pips')} "
              f"R={st.get('total_pnl_R')} err={r.get('error','')}")

        print("Running remote s97 (trend mean-reversion)...")
        r = run_s97(max_days)
        rows.append(r)
        st = r.get("stats") or {}
        print(f"  s97 remote -> {r['status']} trades={st.get('total_trades')} "
              f"PF={st.get('profit_factor')} exness={st.get('total_pnl_exness_pips')} "
              f"R={st.get('total_pnl_R')}")

        for sid in ("s98", "s90", "s91", "s13"):
            print(f"Running baseline {sid}...")
            r = run_via_batch(sid, max_days)
            rows.append(r)
            st = r.get("stats") or {}
            print(f"  {sid} -> {r.get('status')} trades={st.get('total_trades')} "
                  f"PF={st.get('profit_factor')} exness={st.get('total_pnl_exness_pips')} "
                  f"R={st.get('total_pnl_R')} err={r.get('error','')}")

    summary = {
        "generated_at": datetime.now().isoformat(),
        "bt_cost_price": os.environ.get("BT_COST_PRICE"),
        "exness_pip": EXNESS_XAUUSD_PIP,
        "note": "Remote s96/s97 from origin/live; local unified strategy renumbered to s98",
        "results": rows,
    }
    path = OUT / "summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    import csv
    csv_path = OUT / "summary.csv"
    fields = [
        "strategy_id", "name", "source", "window", "status", "total_trades",
        "win_rate", "profit_factor", "total_pnl_exness_pips", "total_pnl_pips",
        "total_pnl_R", "avg_R", "elapsed_sec", "error",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            st = r.get("stats") or {}
            w.writerow({
                "strategy_id": r.get("strategy_id"),
                "name": r.get("name"),
                "source": r.get("source"),
                "window": r.get("window"),
                "status": r.get("status"),
                "total_trades": st.get("total_trades", ""),
                "win_rate": st.get("win_rate", ""),
                "profit_factor": st.get("profit_factor", ""),
                "total_pnl_exness_pips": st.get("total_pnl_exness_pips", ""),
                "total_pnl_pips": st.get("total_pnl_pips", ""),
                "total_pnl_R": st.get("total_pnl_R", ""),
                "avg_R": st.get("avg_R", ""),
                "elapsed_sec": r.get("elapsed_sec", ""),
                "error": r.get("error", ""),
            })
    print(f"\nWrote {path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
