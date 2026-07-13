#!/usr/bin/env python3
"""
Phased multi-pair compare: s96 / s97_z2.5 / s98 on rolling windows ending at
each CSV's latest bar (Exness library).

Windows (days back from each file's max timestamp):
  90 (~3m), 180 (~6m), 365 (1y), 730 (2y), 1095 (3y)

Fair costs: strip pnl_R, symbol-aware enrich. No global BT_COST_PRICE.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

os.environ.pop("BT_COST_PRICE", None)
os.environ.setdefault(
    "YT_DATA_ROOT",
    r"O:\D temp\UltimateTradeBot\Data\Exness\structured\history",
)

from batch_xauusd_backtest import compute_stats  # noqa: E402
from core import (  # noqa: E402
    enrich_trades_pnl,
    infer_pip_size,
    load_csv,
    price_to_exness_pips,
)
from data_library import (  # noqa: E402
    data_root,
    find_instrument_csv,
    prepare_library_csv_window,
    scan_csv_max_timestamp,
)
import strategy_96_mss_ob_tuned as s96  # noqa: E402
import strategy_97_trend_meanreversion as s97  # noqa: E402
from strategy_98_xau_trend_liquidity_trail import generate_trades as gen_s98  # noqa: E402

PAIRS = [
    "GBPUSD", "AUDUSD", "EURUSD", "NZDUSD",
    "USDCAD", "USDCHF", "USDJPY", "XAUUSD",
]
WINDOWS = [
    (90, "3m"),
    (180, "6m"),
    (365, "1y"),
    (730, "2y"),
    (1095, "3y"),
]
# Prefer D: for trim CSVs (O: often fills up on 5m windows). Final summary also
# copied to dashboard/out for README / repo convenience.
OUT = Path(os.environ.get(
    "YT_WINDOW_OUT",
    r"D:\temp\yt_learning_window_compare",
))
OUT_MIRROR = ROOT / "dashboard" / "out" / "full_pair_compare_windows"
START_EQUITY = 10_000.0
RISK_PCT = 0.01


def _cleanup_trims(job: Path) -> None:
    """Delete large trimmed CSVs after the strategy has loaded them into memory."""
    for p in job.glob("trim_*.csv"):
        try:
            p.unlink()
        except OSError:
            pass


def fair_enrich(trades: list, symbol: str) -> list:
    out = []
    for t in trades:
        d = copy.deepcopy(t)
        d.pop("pnl_R", None)
        d["symbol"] = symbol
        out.append(d)
    return enrich_trades_pnl(out)


def trade_R_list(trades: list) -> list[float]:
    rs = []
    for t in trades:
        if t.get("outcome") not in ("win", "loss", "breakeven"):
            continue
        entry, stop, pnl = t.get("entry_price"), t.get("stop_loss"), t.get("pnl_pips")
        if entry is None or stop is None or pnl is None:
            continue
        pip = infer_pip_size(float(entry), symbol=t.get("symbol"))
        risk = abs(float(entry) - float(stop)) / pip
        if risk > 0:
            rs.append(float(pnl) / risk)
    return rs


def equity_metrics(r_list: list[float]) -> dict:
    if not r_list:
        return {
            "total_R": 0.0, "avg_R": 0.0, "max_DD_R": 0.0,
            "return_pct": 0.0, "max_DD_pct": 0.0, "final_equity": START_EQUITY,
        }
    eq_r = peak_r = max_dd_r = 0.0
    equity = peak_eq = START_EQUITY
    max_dd_pct = 0.0
    risk_cash = START_EQUITY * RISK_PCT
    for r in r_list:
        eq_r += r
        peak_r = max(peak_r, eq_r)
        max_dd_r = max(max_dd_r, peak_r - eq_r)
        equity += r * risk_cash
        peak_eq = max(peak_eq, equity)
        if peak_eq > 0:
            max_dd_pct = max(max_dd_pct, (peak_eq - equity) / peak_eq * 100.0)
    return {
        "total_R": round(eq_r, 3),
        "avg_R": round(eq_r / len(r_list), 4),
        "max_DD_R": round(max_dd_r, 3),
        "return_pct": round((equity - START_EQUITY) / START_EQUITY * 100.0, 2),
        "max_DD_pct": round(max_dd_pct, 2),
        "final_equity": round(equity, 2),
    }


def net_exness_pips(trades: list, symbol: str) -> tuple[float, float]:
    total = eq = peak = max_dd = 0.0
    for t in trades:
        if t.get("pnl_pips") is None:
            continue
        entry = float(t["entry_price"])
        fw = infer_pip_size(entry, symbol=symbol)
        ex = price_to_exness_pips(float(t["pnl_pips"]) * fw, entry, symbol=symbol)
        total += ex
        eq += ex
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return round(total, 1), round(max_dd, 1)


def summarize(trades: list, symbol: str, strategy: str, window: str, max_days: int) -> dict:
    st = compute_stats(trades)
    em = equity_metrics(trade_R_list(trades))
    net_ex, dd_ex = net_exness_pips(trades, symbol)
    return {
        "strategy": strategy,
        "pair": symbol,
        "window": window,
        "max_days": max_days,
        "status": "ok",
        "total_trades": st["total_trades"],
        "win_rate": st["win_rate"],
        "profit_factor": st["profit_factor"],
        "total_R": em["total_R"],
        "avg_R": em["avg_R"],
        "max_DD_R": em["max_DD_R"],
        "return_pct": em["return_pct"],
        "max_DD_pct": em["max_DD_pct"],
        "final_equity": em["final_equity"],
        "net_exness_pips": net_ex,
        "max_DD_exness_pips": dd_ex,
    }


def run_s96(pair: str, max_days: int, window: str, job: Path) -> dict:
    root = data_root()
    paths = {
        "4h": find_instrument_csv(root, pair, "4h"),
        "1h": find_instrument_csv(root, pair, "1h"),
        "15m": find_instrument_csv(root, pair, "15m"),
        "5m": find_instrument_csv(root, pair, "5m"),
    }
    if any(v is None for v in paths.values()):
        return {"strategy": "s96", "pair": pair, "window": window, "status": "missing_data"}
    job.mkdir(parents=True, exist_ok=True)
    file_map = {
        "--csv4h": str(paths["4h"]),
        "--csv1h": str(paths["1h"]),
        "--csv15m": str(paths["15m"]),
        "--csv5m": str(paths["5m"]),
    }
    working, notes = prepare_library_csv_window(file_map, max_days, job)
    out = job / "s96_trades.json"
    try:
        raw = s96.run_strategy(
            s96._df_from_csv(working["--csv4h"]),
            s96._df_from_csv(working["--csv1h"]),
            s96._df_from_csv(working["--csv15m"]),
            s96._df_from_csv(working["--csv5m"]),
            str(out),
            symbol=pair,
        )
        row = summarize(fair_enrich(raw, pair), pair, "s96", window, max_days)
        row["trim_notes"] = notes
        c = load_csv(working["--csv1h"])
        if c:
            row["data_start"] = datetime.fromtimestamp(c[0].timestamp, tz=timezone.utc).date().isoformat()
            row["data_end"] = datetime.fromtimestamp(c[-1].timestamp, tz=timezone.utc).date().isoformat()
        return row
    except Exception as exc:
        return {
            "strategy": "s96", "pair": pair, "window": window, "status": "error",
            "error": str(exc), "traceback": traceback.format_exc()[-1200:],
        }
    finally:
        _cleanup_trims(job)


def run_s97(pair: str, max_days: int, window: str, job: Path, z: float = 2.5) -> dict:
    src = find_instrument_csv(data_root(), pair, "4h")
    if src is None:
        return {"strategy": f"s97_z{z}", "pair": pair, "window": window, "status": "missing_data"}
    job.mkdir(parents=True, exist_ok=True)
    working, notes = prepare_library_csv_window({"--csv4h": str(src)}, max_days, job)
    out = job / "s97_trades.json"
    old = s97.Z_ENTRY
    s97.Z_ENTRY = z
    try:
        raw = s97.run_strategy(
            s97._df_from_csv(working["--csv4h"]), str(out), symbol=pair
        )
        row = summarize(fair_enrich(raw, pair), pair, f"s97_z{z}", window, max_days)
        row["trim_notes"] = notes
        c = load_csv(working["--csv4h"])
        if c:
            row["data_start"] = datetime.fromtimestamp(c[0].timestamp, tz=timezone.utc).date().isoformat()
            row["data_end"] = datetime.fromtimestamp(c[-1].timestamp, tz=timezone.utc).date().isoformat()
        return row
    except Exception as exc:
        return {
            "strategy": f"s97_z{z}", "pair": pair, "window": window,
            "status": "error", "error": str(exc),
        }
    finally:
        s97.Z_ENTRY = old
        _cleanup_trims(job)


def run_s98(pair: str, max_days: int, window: str, job: Path) -> dict:
    src = find_instrument_csv(data_root(), pair, "1h")
    if src is None:
        return {"strategy": "s98", "pair": pair, "window": window, "status": "missing_data"}
    job.mkdir(parents=True, exist_ok=True)
    working, notes = prepare_library_csv_window({"--csv1h": str(src)}, max_days, job)
    try:
        candles = load_csv(working["--csv1h"])
        raw = gen_s98(
            candles, also_breakout=True, session_filter=False,
            atr_mult_trail=3.0, atr_mult_init=1.5,
        )
        (job / "s98_trades.json").write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        row = summarize(fair_enrich(raw, pair), pair, "s98", window, max_days)
        row["trim_notes"] = notes
        if candles:
            row["data_start"] = datetime.fromtimestamp(candles[0].timestamp, tz=timezone.utc).date().isoformat()
            row["data_end"] = datetime.fromtimestamp(candles[-1].timestamp, tz=timezone.utc).date().isoformat()
        return row
    finally:
        _cleanup_trims(job)


def basket(rows: list[dict], strategy: str, window: str) -> dict:
    sub = [
        r for r in rows
        if r.get("strategy") == strategy and r.get("window") == window and r.get("status") == "ok"
    ]
    if not sub:
        return {"strategy": strategy, "window": window, "status": "empty"}
    pos = sum(1 for r in sub if float(r.get("total_R") or 0) > 0)
    total_r = sum(float(r.get("total_R") or 0) for r in sub)
    trades = sum(int(r.get("total_trades") or 0) for r in sub)
    dollar = sum(float(r.get("return_pct") or 0) / 100.0 * START_EQUITY for r in sub)
    worst_dd = max(float(r.get("max_DD_pct") or 0) for r in sub)
    wsum = sum(float(r.get("avg_R") or 0) * int(r.get("total_trades") or 0) for r in sub)
    ends = {r.get("data_end") for r in sub if r.get("data_end")}
    starts = sorted({r.get("data_start") for r in sub if r.get("data_start")})
    return {
        "strategy": strategy,
        "window": window,
        "pairs": len(sub),
        "pairs_positive_R": pos,
        "pct_pairs_positive": round(100.0 * pos / len(sub), 1),
        "total_trades": trades,
        "total_R": round(total_r, 3),
        "avg_R_trade": round(wsum / trades, 4) if trades else 0.0,
        "basket_return_pct_parallel": round(dollar / START_EQUITY * 100.0, 2),
        "worst_pair_max_DD_pct": round(worst_dd, 2),
        "data_end": sorted(ends)[-1] if ends else "",
        "data_start_min": starts[0] if starts else "",
    }


def best_per_pair(rows: list[dict], window: str) -> list[dict]:
    out = []
    for pair in PAIRS:
        cands = [
            r for r in rows
            if r.get("pair") == pair and r.get("window") == window
            and r.get("status") == "ok" and int(r.get("total_trades") or 0) > 0
        ]
        if not cands:
            out.append({"window": window, "pair": pair, "winner": None})
            continue
        best = max(cands, key=lambda r: float(r.get("total_R") or -1e9))
        out.append({
            "window": window,
            "pair": pair,
            "winner": best["strategy"],
            "total_R": best["total_R"],
            "return_pct": best["return_pct"],
            "PF": best["profit_factor"],
            "trades": best["total_trades"],
            "max_DD_pct": best["max_DD_pct"],
            "data_start": best.get("data_start"),
            "data_end": best.get("data_end"),
        })
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Anchor end date from XAUUSD 1h
    anchor = find_instrument_csv(data_root(), "XAUUSD", "1h")
    max_ts = scan_csv_max_timestamp(anchor) if anchor else None
    anchor_end = (
        datetime.fromtimestamp(max_ts, tz=timezone.utc).date().isoformat()
        if max_ts else "unknown"
    )
    print(f"Anchor latest bar (XAUUSD 1h): {anchor_end}")
    print(f"Windows: {WINDOWS}")

    rows: list[dict] = []
    for max_days, label in WINDOWS:
        print(f"\n######## WINDOW {label} ({max_days}d) ending ~{anchor_end} ########", flush=True)
        for pair in PAIRS:
            base = OUT / label / pair
            print(f"=== {pair} [{label}] ===", flush=True)
            for name, runner in (
                ("s96", lambda d=base / "s96": run_s96(pair, max_days, label, d)),
                ("s97_z2.5", lambda d=base / "s97": run_s97(pair, max_days, label, d)),
                ("s98", lambda d=base / "s98": run_s98(pair, max_days, label, d)),
            ):
                print(f"  {name} ...", flush=True)
                r = runner()
                rows.append(r)
                if r.get("status") != "ok":
                    print(f"    -> {r.get('status')} {r.get('error', '')}")
                else:
                    print(
                        f"    {r.get('data_start')}->{r.get('data_end')} "
                        f"n={r['total_trades']} PF={r['profit_factor']} "
                        f"R={r['total_R']} ret%={r['return_pct']} DD%={r['max_DD_pct']}"
                    )

    baskets = []
    winners = []
    for _, label in WINDOWS:
        for strat in ("s96", "s97_z2.5", "s98"):
            baskets.append(basket(rows, strat, label))
        winners.extend(best_per_pair(rows, label))

    print("\n======== BASKETS ========")
    for b in baskets:
        print(b)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "anchor_end": anchor_end,
        "windows_days": WINDOWS,
        "equity_model": {"start": START_EQUITY, "risk_pct_of_initial_per_R": RISK_PCT},
        "per_pair": rows,
        "baskets": baskets,
        "best_per_pair": winners,
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    fields = [
        "strategy", "pair", "window", "max_days", "status", "data_start", "data_end",
        "total_trades", "win_rate", "profit_factor", "total_R", "avg_R", "max_DD_R",
        "return_pct", "max_DD_pct", "final_equity", "net_exness_pips",
        "max_DD_exness_pips", "error",
    ]
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with open(OUT / "basket.csv", "w", newline="", encoding="utf-8") as f:
        keys = [k for k in baskets[0].keys()]
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(baskets)

    with open(OUT / "best_per_pair.csv", "w", newline="", encoding="utf-8") as f:
        keys = list(winners[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(winners)

    # Mirror summary artifacts into the repo dashboard path (small files only).
    OUT_MIRROR.mkdir(parents=True, exist_ok=True)
    for name in ("summary.json", "summary.csv", "basket.csv", "best_per_pair.csv"):
        src = OUT / name
        if src.exists():
            (OUT_MIRROR / name).write_bytes(src.read_bytes())

    print(f"\nWrote {OUT}")
    print(f"Mirrored summaries to {OUT_MIRROR}")


if __name__ == "__main__":
    main()
