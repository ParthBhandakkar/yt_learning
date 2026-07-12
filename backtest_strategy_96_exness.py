#!/usr/bin/env python3
"""
Backtest Strategy 96 on XAUUSD with Exness-calibrated costs.

Sets BT_COST_PRICE from Exness Standard-style XAUUSD round-turn ($0.40 default
= ~25-30 Exness pips spread + slippage). Reports both framework ($1) and Exness
($0.01) pip units.

Usage:
  set YT_DATA_ROOT=O:\\D temp\\UltimateTradeBot\\Data\\Exness\\structured\\history
  D:\\Python\\Python3_12_8\\python.exe backtest_strategy_96_exness.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

# Exness Standard-style XAUUSD round-turn in PRICE units ($).
# 1 Exness pip = $0.01; ~25-35 pip avg spread + slippage ≈ $0.40-$0.50.
os.environ.setdefault("BT_COST_PRICE", "0.45")

from core import (  # noqa: E402
    EXNESS_XAUUSD_PIP,
    enrich_trades_pnl,
    framework_pips_to_exness,
    load_csv,
    round_turn_cost_price,
)
from data_library import find_instrument_csv, data_root, prepare_library_csv_window  # noqa: E402
from strategy_90_trend_breakout_atr import generate_trades as gen_s90  # noqa: E402
from strategy_91_mtf_liquidity_reversion import generate_trades as gen_s91  # noqa: E402
from strategy_98_xau_trend_liquidity_trail import generate_trades as gen_s96  # noqa: E402

OUT_DIR = ROOT / "dashboard" / "out" / "strategy_96_exness"
PYTHON_NOTE = sys.executable


def compute_stats(trades: list) -> dict:
    closed = [t for t in trades if t.get("outcome") in ("win", "loss", "breakeven")]
    if not closed:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0.0, "profit_factor": 0.0,
            "total_pnl_framework_pips": 0.0, "total_pnl_exness_pips": 0.0,
            "avg_win_exness": 0.0, "avg_loss_exness": 0.0,
            "total_pnl_R": 0.0, "avg_R": 0.0, "max_dd_exness": 0.0,
        }

    wins = [t for t in closed if t.get("outcome") == "win"]
    losses = [t for t in closed if t.get("outcome") == "loss"]
    pnls_f = [float(t.get("pnl_pips") or 0) for t in closed]
    ref = float(closed[0].get("entry_price") or 2000)
    pnls_e = [framework_pips_to_exness(p, ref) for p in pnls_f]

    gp = sum(p for p in pnls_f if p > 0)
    gl = abs(sum(p for p in pnls_f if p < 0))
    if gl > 0:
        pf = round(gp / gl, 2)
    elif gp > 0:
        pf = None
    else:
        pf = 0.0

    r_vals = []
    for t in closed:
        entry = t.get("entry_price")
        stop = t.get("stop_loss")
        pnl = t.get("pnl_pips")
        if entry is None or stop is None or pnl is None:
            continue
        risk = abs(float(entry) - float(stop))
        if risk <= 0:
            continue
        # framework pip = $1 for gold => risk_pips = risk / 1.0
        r_vals.append(float(pnl) / risk)

    # Equity DD in Exness pips
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls_e:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    return {
        "total_trades": len(closed),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(100.0 * len(wins) / len(closed), 2),
        "profit_factor": pf,
        "total_pnl_framework_pips": round(sum(pnls_f), 1),
        "total_pnl_exness_pips": round(sum(pnls_e), 1),
        "avg_win_exness": round(
            framework_pips_to_exness(
                sum(float(t.get("pnl_pips") or 0) for t in wins) / len(wins), ref
            ),
            1,
        ) if wins else 0.0,
        "avg_loss_exness": round(
            framework_pips_to_exness(
                abs(sum(float(t.get("pnl_pips") or 0) for t in losses) / len(losses)), ref
            ),
            1,
        ) if losses else 0.0,
        "total_pnl_R": round(sum(r_vals), 3) if r_vals else 0.0,
        "avg_R": round(sum(r_vals) / len(r_vals), 3) if r_vals else 0.0,
        "max_dd_exness": round(max_dd, 1),
        "cost_price": round_turn_cost_price(ref),
        "cost_exness_pips_per_trade": round(round_turn_cost_price(ref) / EXNESS_XAUUSD_PIP, 1),
        "exness_pip_size": EXNESS_XAUUSD_PIP,
    }


def window_candles(candles, max_days: int):
    if not max_days or max_days <= 0 or not candles:
        return candles
    max_ts = max(c.timestamp for c in candles)
    start = max_ts - max_days * 86400
    return [c for c in candles if c.timestamp >= start]


def run_variant(name, candles, gen_fn, kwargs, max_days: int) -> dict:
    subset = window_candles(candles, max_days)
    trades = gen_fn(subset, **kwargs)
    trades = enrich_trades_pnl(trades)
    stats = compute_stats(trades)
    return {
        "name": name,
        "window": "full" if max_days <= 0 else f"{max_days}d",
        "stats": stats,
        "trades": trades,
        "n_bars": len(subset),
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    root = data_root()
    csv_1h = find_instrument_csv(root, "XAUUSD", "1h")
    csv_4h = find_instrument_csv(root, "XAUUSD", "4h")
    if csv_1h is None:
        raise SystemExit(f"No XAUUSD 1h CSV under {root}")

    # Also prepare windowed copies for reproducibility notes
    job = OUT_DIR / "data"
    job.mkdir(exist_ok=True)
    prepare_library_csv_window({"--csv1h": str(csv_1h)}, None, job)

    candles = load_csv(str(csv_1h))
    print(f"Loaded {len(candles)} 1h bars from {csv_1h}")
    print(f"4h ref: {csv_4h}")
    print(f"BT_COST_PRICE={os.environ.get('BT_COST_PRICE')} "
          f"(Exness pip={EXNESS_XAUUSD_PIP})")

    variants = [
        (
            "s96_unified_default",
            gen_s96,
            dict(
                also_breakout=True,
                session_filter=True,
                atr_mult_trail=3.0,
                atr_mult_init=1.5,
            ),
        ),
        (
            "s96_reclaim_only",
            gen_s96,
            dict(
                also_breakout=False,
                session_filter=True,
                atr_mult_trail=3.0,
                atr_mult_init=1.5,
            ),
        ),
        (
            "s96_no_session",
            gen_s96,
            dict(
                also_breakout=True,
                session_filter=False,
                atr_mult_trail=3.0,
                atr_mult_init=1.5,
            ),
        ),
        (
            "s90_baseline_1h",
            gen_s90,
            dict(donchian=20, trend_len=50, atr_len=14, atr_mult_init=2.0, atr_mult_trail=3.0),
        ),
        (
            "s91_baseline_1h",
            gen_s91,
            dict(htf_ema=50, rr=2.0),
        ),
    ]

    rows = []
    for max_days in (365, 0):
        for name, fn, kw in variants:
            print(f"Running {name} window={max_days or 'full'} ...")
            result = run_variant(name, candles, fn, kw, max_days)
            stats = result["stats"]
            rows.append({
                "name": name,
                "window": result["window"],
                "n_bars": result["n_bars"],
                **stats,
            })
            out_trades = OUT_DIR / f"{name}_{result['window']}_trades.json"
            out_trades.write_text(
                json.dumps({"stats": stats, "trades": result["trades"]}, indent=2, default=str),
                encoding="utf-8",
            )
            pf = stats["profit_factor"]
            print(
                f"  trades={stats['total_trades']} WR={stats['win_rate']}% "
                f"PF={pf} ExnessPnL={stats['total_pnl_exness_pips']} "
                f"R={stats['total_pnl_R']} DD={stats['max_dd_exness']}"
            )

    summary = {
        "generated_at": datetime.now().isoformat(),
        "symbol": "XAUUSD",
        "broker": "Exness",
        "exness_pip_size": EXNESS_XAUUSD_PIP,
        "bt_cost_price": float(os.environ["BT_COST_PRICE"]),
        "csv_1h": str(csv_1h),
        "python": PYTHON_NOTE,
        "results": rows,
    }
    summary_path = OUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # CSV
    import csv
    csv_path = OUT_DIR / "summary.csv"
    fields = [
        "name", "window", "total_trades", "win_rate", "profit_factor",
        "total_pnl_exness_pips", "total_pnl_framework_pips", "total_pnl_R",
        "avg_R", "avg_win_exness", "avg_loss_exness", "max_dd_exness",
        "cost_exness_pips_per_trade",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"\nWrote {summary_path}")
    print(f"Wrote {csv_path}")

    # Pick best by PF then Exness PnL among s96 variants with >= 20 trades
    candidates = [
        r for r in rows
        if r["name"].startswith("s96") and r["total_trades"] >= 20
        and r.get("profit_factor") not in (None, 0)
    ]
    if candidates:
        best = max(
            candidates,
            key=lambda r: (
                float(r["profit_factor"] or 0),
                float(r["total_pnl_exness_pips"] or 0),
            ),
        )
        print(
            f"\nBEST s96 (>=20 trades): {best['name']} [{best['window']}] "
            f"PF={best['profit_factor']} ExnessPnL={best['total_pnl_exness_pips']} "
            f"trades={best['total_trades']}"
        )


if __name__ == "__main__":
    main()
