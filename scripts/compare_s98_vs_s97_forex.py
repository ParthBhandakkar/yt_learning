#!/usr/bin/env python3
"""
Fair multi-pair comparison:
  - Remote s97 (claims basket edge on 8 FX+gold pairs, 4H)
  - Local s98 (our best on XAUUSD; run same pairs on 1H)

Same cost path for both: drop pnl_R and use enrich_trades_pnl (1x round-turn).
Data: Exness structured history via YT_DATA_ROOT.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

os.environ.setdefault("BT_COST_PRICE", "")  # use class defaults (FX vs metals)
os.environ.setdefault(
    "YT_DATA_ROOT",
    r"O:\D temp\UltimateTradeBot\Data\Exness\structured\history",
)

import numpy as np  # noqa: E402

from batch_xauusd_backtest import compute_stats  # noqa: E402
from core import enrich_trades_pnl  # noqa: E402
from data_library import find_instrument_csv, data_root  # noqa: E402
import strategy_97_trend_meanreversion as s97  # noqa: E402
from strategy_98_xau_trend_liquidity_trail import generate_trades as gen_s98  # noqa: E402
from core import load_csv  # noqa: E402

PAIRS = [
    "GBPUSD", "AUDUSD", "EURUSD", "NZDUSD",
    "USDCAD", "USDCHF", "USDJPY", "XAUUSD",
]
OUT = ROOT / "dashboard" / "out" / "compare_s98_vs_s97_forex"


def fair_enrich(trades: list) -> list:
    cleaned = []
    for t in trades:
        d = copy.deepcopy(t)
        d.pop("pnl_R", None)
        cleaned.append(d)
    return enrich_trades_pnl(cleaned)


def r_stats(trades: list) -> dict:
    """Expectancy / total R from entry-stop risk after fair enrich."""
    rs = []
    for t in trades:
        if t.get("outcome") not in ("win", "loss", "breakeven"):
            continue
        entry = t.get("entry_price")
        stop = t.get("stop_loss")
        pnl = t.get("pnl_pips")
        if entry is None or stop is None or pnl is None:
            continue
        from core import infer_pip_size
        pip = infer_pip_size(float(entry))
        risk = abs(float(entry) - float(stop)) / pip
        if risk <= 0:
            continue
        rs.append(float(pnl) / risk)
    return {
        "total_R": round(sum(rs), 3) if rs else 0.0,
        "avg_R": round(sum(rs) / len(rs), 4) if rs else 0.0,
        "n_R": len(rs),
    }


def run_s97(pair: str, z_entry: float) -> dict:
    path = find_instrument_csv(data_root(), pair, "4h")
    if path is None:
        return {"pair": pair, "strategy": f"s97_z{z_entry}", "status": "missing_data"}
    # temporarily override module tunable
    old = s97.Z_ENTRY
    s97.Z_ENTRY = z_entry
    try:
        out = OUT / "trades" / f"s97_z{z_entry}_{pair}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = s97.run_strategy(s97._df_from_csv(str(path)), str(out), symbol=pair)
    finally:
        s97.Z_ENTRY = old
    trades = fair_enrich(raw)
    st = compute_stats(trades)
    st.update(r_stats(trades))
    return {
        "pair": pair,
        "strategy": f"s97_z{z_entry}",
        "status": "ok",
        "tf": "4h",
        "csv": str(path),
        **st,
    }


def run_s98(pair: str) -> dict:
    path = find_instrument_csv(data_root(), pair, "1h")
    if path is None:
        return {"pair": pair, "strategy": "s98", "status": "missing_data"}
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
    trades = fair_enrich(raw)
    st = compute_stats(trades)
    st.update(r_stats(trades))
    return {
        "pair": pair,
        "strategy": "s98",
        "status": "ok",
        "tf": "1h",
        "csv": str(path),
        **st,
    }


def basket(rows: list[dict], strategy: str) -> dict:
    subset = [r for r in rows if r.get("strategy") == strategy and r.get("status") == "ok"]
    pos = sum(1 for r in subset if float(r.get("total_R") or 0) > 0)
    total_r = sum(float(r.get("total_R") or 0) for r in subset)
    trades = sum(int(r.get("total_trades") or 0) for r in subset)
    # pooled expectancy weighted by trades
    wsum = 0.0
    w = 0
    for r in subset:
        n = int(r.get("total_trades") or 0)
        if n <= 0:
            continue
        wsum += float(r.get("avg_R") or 0) * n
        w += n
    return {
        "strategy": strategy,
        "pairs": len(subset),
        "pairs_positive_R": pos,
        "total_trades": trades,
        "total_R": round(total_r, 3),
        "avg_R_trade": round(wsum / w, 4) if w else 0.0,
        "pct_pairs_positive": round(100.0 * pos / len(subset), 1) if subset else 0.0,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"YT_DATA_ROOT={os.environ.get('YT_DATA_ROOT')}")
    print(f"BT_COST_PRICE={os.environ.get('BT_COST_PRICE') or '(class defaults)'}")
    rows: list[dict] = []

    for pair in PAIRS:
        print(f"\n=== {pair} ===")
        for z in (2.0, 2.5):
            print(f"  s97 Z={z} ...", flush=True)
            r = run_s97(pair, z)
            rows.append(r)
            print(
                f"    trades={r.get('total_trades')} WR={r.get('win_rate')} "
                f"PF={r.get('profit_factor')} total_R={r.get('total_R')} avg_R={r.get('avg_R')}"
            )
        print("  s98 ...", flush=True)
        r = run_s98(pair)
        rows.append(r)
        print(
            f"    trades={r.get('total_trades')} WR={r.get('win_rate')} "
            f"PF={r.get('profit_factor')} total_R={r.get('total_R')} avg_R={r.get('avg_R')}"
        )

    baskets = [
        basket(rows, "s97_z2.0"),
        basket(rows, "s97_z2.5"),
        basket(rows, "s98"),
    ]

    print("\n" + "=" * 72)
    print("BASKET SUMMARY (fair 1x cost enrich)")
    for b in baskets:
        print(
            f"{b['strategy']:10s} pairs+={b['pairs_positive_R']}/{b['pairs']} "
            f"({b['pct_pairs_positive']}%) trades={b['total_trades']} "
            f"total_R={b['total_R']:+.2f} avg_R/trade={b['avg_R_trade']:+.4f}"
        )

    summary = {
        "generated_at": datetime.now().isoformat(),
        "claim": "Remote s97 claims cross-pair edge on 8 instruments (4H ATR mean-rev)",
        "challenger": "Local s98 (our XAUUSD best) on same pairs via 1H",
        "cost": "class defaults via enrich_trades_pnl; pnl_R stripped for 1x RT fairness",
        "pairs": PAIRS,
        "per_pair": rows,
        "baskets": baskets,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    fields = [
        "strategy", "pair", "status", "tf", "total_trades", "win_rate",
        "profit_factor", "total_pnl_pips", "total_R", "avg_R",
    ]
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with open(OUT / "basket.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(baskets[0].keys()))
        w.writeheader()
        w.writerows(baskets)

    print(f"\nWrote {OUT / 'summary.json'}")
    print(f"Wrote {OUT / 'summary.csv'}")
    print(f"Wrote {OUT / 'basket.csv'}")


if __name__ == "__main__":
    main()
