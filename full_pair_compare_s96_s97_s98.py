#!/usr/bin/env python3
"""
Full multi-pair comparison: s96 (remote), s97 (remote), s98 (ours).

Metrics per pair + baskets:
  trades, win%, PF, total_R, avg_R, max_DD_R, return_pct (1% risk/R on $10k),
  net Exness pips, max_DD_exness_pips

Fair costs: strip pnl_R → 1x enrich; symbol-aware Exness pip/cost.
No BT_COST_PRICE global (class defaults per symbol).
"""

from __future__ import annotations

import copy
import csv
import json
import os
import sys
import traceback
from datetime import datetime
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
from data_library import data_root, find_instrument_csv  # noqa: E402
import strategy_96_mss_ob_tuned as s96  # noqa: E402
import strategy_97_trend_meanreversion as s97  # noqa: E402
from strategy_98_xau_trend_liquidity_trail import generate_trades as gen_s98  # noqa: E402

PAIRS = [
    "GBPUSD", "AUDUSD", "EURUSD", "NZDUSD",
    "USDCAD", "USDCHF", "USDJPY", "XAUUSD",
]
OUT = ROOT / "dashboard" / "out" / "full_pair_compare_s96_s97_s98"
START_EQUITY = 10_000.0
RISK_PCT = 0.01  # 1% of starting equity per 1R


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
        entry = t.get("entry_price")
        stop = t.get("stop_loss")
        pnl = t.get("pnl_pips")
        if entry is None or stop is None or pnl is None:
            continue
        sym = t.get("symbol")
        pip = infer_pip_size(float(entry), symbol=sym)
        risk = abs(float(entry) - float(stop)) / pip
        if risk <= 0:
            continue
        rs.append(float(pnl) / risk)
    return rs


def equity_metrics(r_list: list[float]) -> dict:
    if not r_list:
        return {
            "total_R": 0.0,
            "avg_R": 0.0,
            "max_DD_R": 0.0,
            "return_pct": 0.0,
            "max_DD_pct": 0.0,
            "final_equity": START_EQUITY,
        }
    eq_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    equity = START_EQUITY
    peak_eq = START_EQUITY
    max_dd_pct = 0.0
    risk_cash = START_EQUITY * RISK_PCT  # fixed fractional of initial
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
    """Sum net price PnL converted to Exness pips; max DD in Exness pips."""
    total = 0.0
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        if t.get("pnl_pips") is None:
            continue
        entry = float(t["entry_price"])
        fw_pip = infer_pip_size(entry, symbol=symbol)
        # framework pips → price → exness pips
        price_pnl = float(t["pnl_pips"]) * fw_pip
        ex = price_to_exness_pips(price_pnl, entry, symbol=symbol)
        total += ex
        eq += ex
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return round(total, 1), round(max_dd, 1)


def summarize(trades: list, symbol: str, strategy: str) -> dict:
    st = compute_stats(trades)
    rs = trade_R_list(trades)
    em = equity_metrics(rs)
    net_ex, dd_ex = net_exness_pips(trades, symbol)
    return {
        "strategy": strategy,
        "pair": symbol,
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
        "total_pnl_framework_pips": st["total_pnl_pips"],
    }


def run_s96(pair: str) -> dict:
    root = data_root()
    paths = {
        "4h": find_instrument_csv(root, pair, "4h"),
        "1h": find_instrument_csv(root, pair, "1h"),
        "15m": find_instrument_csv(root, pair, "15m"),
        "5m": find_instrument_csv(root, pair, "5m"),
    }
    if any(v is None for v in paths.values()):
        return {"strategy": "s96", "pair": pair, "status": "missing_data"}
    out = OUT / "trades" / f"s96_{pair}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = s96.run_strategy(
            s96._df_from_csv(str(paths["4h"])),
            s96._df_from_csv(str(paths["1h"])),
            s96._df_from_csv(str(paths["15m"])),
            s96._df_from_csv(str(paths["5m"])),
            str(out),
            symbol=pair,
        )
        trades = fair_enrich(raw, pair)
        row = summarize(trades, pair, "s96")
        row["tf"] = "4h+1h+15m+5m"
        return row
    except Exception as exc:
        return {
            "strategy": "s96",
            "pair": pair,
            "status": "error",
            "error": str(exc),
            "traceback": traceback.format_exc()[-1500:],
        }


def run_s97(pair: str, z: float = 2.5) -> dict:
    path = find_instrument_csv(data_root(), pair, "4h")
    if path is None:
        return {"strategy": f"s97_z{z}", "pair": pair, "status": "missing_data"}
    out = OUT / "trades" / f"s97_z{z}_{pair}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    old = s97.Z_ENTRY
    s97.Z_ENTRY = z
    try:
        raw = s97.run_strategy(s97._df_from_csv(str(path)), str(out), symbol=pair)
        trades = fair_enrich(raw, pair)
        row = summarize(trades, pair, f"s97_z{z}")
        row["tf"] = "4h"
        return row
    except Exception as exc:
        return {
            "strategy": f"s97_z{z}",
            "pair": pair,
            "status": "error",
            "error": str(exc),
        }
    finally:
        s97.Z_ENTRY = old


def run_s98(pair: str) -> dict:
    path = find_instrument_csv(data_root(), pair, "1h")
    if path is None:
        return {"strategy": "s98", "pair": pair, "status": "missing_data"}
    candles = load_csv(str(path))
    raw = gen_s98(
        candles,
        also_breakout=True,
        session_filter=False,
        atr_mult_trail=3.0,
        atr_mult_init=1.5,
    )
    out = OUT / "trades" / f"s98_{pair}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
    trades = fair_enrich(raw, pair)
    row = summarize(trades, pair, "s98")
    row["tf"] = "1h (+4h resample bias)"
    return row


def basket(rows: list[dict], strategy: str) -> dict:
    sub = [r for r in rows if r.get("strategy") == strategy and r.get("status") == "ok"]
    if not sub:
        return {"strategy": strategy, "status": "empty"}
    pos = sum(1 for r in sub if float(r.get("total_R") or 0) > 0)
    # Combined equity: sum of per-pair return_pct is NOT valid; sum R then scale
    total_r = sum(float(r.get("total_R") or 0) for r in sub)
    trades = sum(int(r.get("total_trades") or 0) for r in sub)
    # Approximate basket return: each pair independently risks 1% of SAME $10k
    # (parallel books) → dollar PnL = sum(return_pct/100 * 10k)
    dollar = sum(float(r.get("return_pct") or 0) / 100.0 * START_EQUITY for r in sub)
    # Max DD_R cannot simply sum; report worst pair DD and sum of DDs as range
    worst_dd_r = max(float(r.get("max_DD_R") or 0) for r in sub)
    worst_dd_pct = max(float(r.get("max_DD_pct") or 0) for r in sub)
    net_ex = sum(float(r.get("net_exness_pips") or 0) for r in sub)
    wsum = sum(float(r.get("avg_R") or 0) * int(r.get("total_trades") or 0) for r in sub)
    return {
        "strategy": strategy,
        "pairs": len(sub),
        "pairs_positive_R": pos,
        "pct_pairs_positive": round(100.0 * pos / len(sub), 1),
        "total_trades": trades,
        "total_R": round(total_r, 3),
        "avg_R_trade": round(wsum / trades, 4) if trades else 0.0,
        "basket_return_pct_parallel": round(dollar / START_EQUITY * 100.0, 2),
        "worst_pair_max_DD_R": round(worst_dd_r, 3),
        "worst_pair_max_DD_pct": round(worst_dd_pct, 2),
        "net_exness_pips_sum": round(net_ex, 1),
    }


def best_for(rows: list[dict]) -> dict:
    """Per-pair winner by total_R among ok runs."""
    out = {}
    for pair in PAIRS:
        cands = [
            r for r in rows
            if r.get("pair") == pair and r.get("status") == "ok"
            and r.get("total_trades", 0) > 0
        ]
        if not cands:
            out[pair] = {"winner": None, "reason": "no trades"}
            continue
        best = max(cands, key=lambda r: float(r.get("total_R") or -1e9))
        out[pair] = {
            "winner": best["strategy"],
            "total_R": best["total_R"],
            "return_pct": best["return_pct"],
            "PF": best["profit_factor"],
            "trades": best["total_trades"],
            "max_DD_pct": best["max_DD_pct"],
            "net_exness_pips": best["net_exness_pips"],
        }
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"YT_DATA_ROOT={os.environ.get('YT_DATA_ROOT')}")
    print("BT_COST_PRICE=(per-symbol class defaults)")
    print(f"Equity model: start=${START_EQUITY:.0f}, risk={RISK_PCT*100:.0f}% of initial per 1R")

    rows: list[dict] = []
    for pair in PAIRS:
        print(f"\n=== {pair} ===", flush=True)
        for label, fn in (
            ("s96", lambda: run_s96(pair)),
            ("s97_z2.5", lambda: run_s97(pair, 2.5)),
            ("s98", lambda: run_s98(pair)),
        ):
            print(f"  {label} ...", flush=True)
            r = fn()
            rows.append(r)
            if r.get("status") != "ok":
                print(f"    -> {r.get('status')} {r.get('error', '')}")
            else:
                print(
                    f"    trades={r['total_trades']} WR={r['win_rate']} PF={r['profit_factor']} "
                    f"R={r['total_R']} ret%={r['return_pct']} DD%={r['max_DD_pct']} "
                    f"exnPips={r['net_exness_pips']}"
                )

    baskets = [basket(rows, s) for s in ("s96", "s97_z2.5", "s98")]
    winners = best_for(rows)

    print("\n" + "=" * 72)
    print("BASKETS")
    for b in baskets:
        print(b)
    print("\nBEST PER PAIR (by total_R)")
    for pair, w in winners.items():
        print(f"  {pair}: {w}")

    payload = {
        "generated_at": datetime.now().isoformat(),
        "equity_model": {
            "start": START_EQUITY,
            "risk_pct_of_initial_per_R": RISK_PCT,
            "note": "return_pct assumes fixed $ risk = 1% of $10k per 1R",
        },
        "causality": {
            "s96": "Strict 1H-close causal MSS; next-bar style 5m tap after MSS close; documented leakage-free vs s95",
            "s97": "Indicators at bar i; signal on close; fill next open; stops use prior-bar indicators",
            "s98": "4H bias from completed bars only; 1H signal on close; fill next 1H open; ATR trail",
        },
        "per_pair": rows,
        "baskets": baskets,
        "best_per_pair": winners,
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    fields = [
        "strategy", "pair", "status", "tf", "total_trades", "win_rate", "profit_factor",
        "total_R", "avg_R", "max_DD_R", "return_pct", "max_DD_pct", "final_equity",
        "net_exness_pips", "max_DD_exness_pips", "error",
    ]
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with open(OUT / "basket.csv", "w", newline="", encoding="utf-8") as f:
        keys = list(baskets[0].keys())
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(baskets)

    with open(OUT / "best_per_pair.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pair", "winner", "total_R", "return_pct", "PF", "trades", "max_DD_pct", "net_exness_pips"])
        for pair, info in winners.items():
            w.writerow([
                pair, info.get("winner"), info.get("total_R"), info.get("return_pct"),
                info.get("PF"), info.get("trades"), info.get("max_DD_pct"),
                info.get("net_exness_pips"),
            ])

    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
