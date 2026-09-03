#!/usr/bin/env python3
"""
Cross-instrument validation for strategy 141.

Runs the SAME parameters on every instrument/timeframe available locally, then
reports pooled expectancy, per-symbol counts, leave-one-symbol-out stability and
an in-sample / out-of-sample split by time. No per-symbol tuning is allowed:
one parameter set, all markets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategies"))

from core import load_csv, round_turn_cost_price  # noqa: E402
from strategy_141_mtf_zone_reclaim import generate_trades, summarize  # noqa: E402

DATA = ROOT / "data"

SETS = {
    "4h": [
        ("EURUSD", DATA / "EURUSD/4h/EURUSD_4h.csv"),
        ("GBPUSD", DATA / "GBPUSD/4h/GBPUSD_4h.csv"),
        ("AUDUSD", DATA / "AUDUSD/4h/AUDUSD_4h.csv"),
        ("NZDUSD", DATA / "NZDUSD/4h/NZDUSD_4h.csv"),
        ("USDCAD", DATA / "USDCAD/4h/USDCAD_4h.csv"),
        ("USDCHF", DATA / "USDCHF/4h/USDCHF_4h.csv"),
        ("USDJPY", DATA / "USDJPY/4h/USDJPY_4h.csv"),
        ("XAUUSD", DATA / "XAUUSD/4h/XAUUSD_4h.csv"),
    ],
    "1h": [
        ("GBPUSD", DATA / "GBPUSD/1h/GBPUSD_1h.csv"),
        ("XAUUSD", DATA / "XAUUSD/1H/XAUUSD_1h_2021-03-02_2026-07-01.csv"),
    ],
    "15m": [
        ("GBPUSD", DATA / "GBPUSD/15m/GBPUSD_15m.csv"),
    ],
}

_CACHE: dict[str, list] = {}


def candles_for(path: Path):
    key = str(path)
    if key not in _CACHE:
        _CACHE[key] = load_csv(str(path))
    return _CACHE[key]


def net_R(trade) -> float:
    """pnl_R minus round-turn cost expressed in R (1.5x: entry + two exits)."""
    risk = abs(float(trade["entry_price"]) - float(trade["stop_loss"]))
    if risk <= 0:
        return 0.0
    cost_R = 1.5 * round_turn_cost_price(float(trade["entry_price"]),
                                         symbol=trade.get("symbol")) / risk
    return float(trade["pnl_R"]) - cost_R


def run_set(tf: str, params: dict) -> dict:
    per_symbol = {}
    pooled = []
    for sym, path in SETS[tf]:
        if not path.exists():
            continue
        trades = generate_trades(candles_for(path), symbol=sym, params=params)
        for t in trades:
            t["_net_R"] = net_R(t)
        per_symbol[sym] = trades
        pooled.extend(trades)
    return {"per_symbol": per_symbol, "pooled": pooled}


def stat(trades, key="_net_R") -> dict:
    if not trades:
        return {"n": 0, "exp": 0.0, "total": 0.0, "win": 0.0, "pf": 0.0}
    rs = [t[key] for t in trades]
    w = [r for r in rs if r > 0]
    lo = [-r for r in rs if r < 0]
    return {
        "n": len(rs),
        "exp": sum(rs) / len(rs),
        "total": sum(rs),
        "win": len(w) / len(rs),
        "pf": (sum(w) / sum(lo)) if lo else float("inf"),
        "avg_w": (sum(w) / len(w)) if w else 0.0,
        "avg_l": (sum(lo) / len(lo)) if lo else 0.0,
    }


def report(tf: str, res: dict, label: str = "") -> dict:
    pooled = res["pooled"]
    p = stat(pooled)
    print(f"\n=== {tf} base {label} ===")
    print(f"POOLED  n={p['n']:>5}  net {p['total']:+8.1f}R  exp {p['exp']:+.4f}R  "
          f"win {p['win']:.1%}  PF {p['pf']:.2f}  avgW {p.get('avg_w', 0):.2f}R  "
          f"avgL {p.get('avg_l', 0):.2f}R")
    pos = 0
    for sym, tr in res["per_symbol"].items():
        s = stat(tr)
        flag = "+" if s["exp"] > 0 else "-"
        pos += 1 if s["exp"] > 0 else 0
        print(f"  {flag} {sym:7s} n={s['n']:>4}  net {s['total']:+8.1f}R  "
              f"exp {s['exp']:+.4f}R  win {s['win']:.1%}  PF {s['pf']:.2f}")
    print(f"  symbols positive: {pos}/{len(res['per_symbol'])}")

    # leave-one-symbol-out
    loo = []
    for sym in res["per_symbol"]:
        rest = [t for s2, tr in res["per_symbol"].items() if s2 != sym for t in tr]
        loo.append((sym, stat(rest)["exp"]))
    if loo:
        worst = min(loo, key=lambda x: x[1])
        print(f"  leave-one-out worst: excl {worst[0]} -> {worst[1]:+.4f}R")

    # temporal IS/OOS split (60/40 by entry time)
    ordered = sorted(pooled, key=lambda t: t["entry_time"])
    cut = int(len(ordered) * 0.6)
    is_s, oos_s = stat(ordered[:cut]), stat(ordered[cut:])
    print(f"  IS  n={is_s['n']:>5} exp {is_s['exp']:+.4f}R | "
          f"OOS n={oos_s['n']:>5} exp {oos_s['exp']:+.4f}R")
    return {"pooled": p, "loo_worst": (min(loo, key=lambda x: x[1])[1] if loo else 0.0),
            "pos": pos, "n_sym": len(res["per_symbol"]),
            "is": is_s["exp"], "oos": oos_s["exp"]}


def num(x: str):
    x = x.strip()
    try:
        return int(x)
    except ValueError:
        return float(x)


def parse_kv(text: str) -> dict:
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = num(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h,1h")
    ap.add_argument("--set", default="", help="param overrides, e.g. min_rr=2.5,disp_atr=0.6")
    ap.add_argument("--sweep", default="", help="param=v1|v2|v3")
    args = ap.parse_args()

    base = parse_kv(args.set)
    tfs = [t.strip() for t in args.tf.split(",") if t.strip()]

    if args.sweep:
        key, raw = args.sweep.split("=", 1)
        for v in [num(x) for x in raw.split("|")]:
            params = {**base, key.strip(): v}
            print(f"\n########## {key} = {v} ##########")
            for tf in tfs:
                report(tf, run_set(tf, params), label=f"{key}={v}")
        return

    for tf in tfs:
        report(tf, run_set(tf, base))


if __name__ == "__main__":
    os.environ.pop("BT_COST_PRICE", None)
    main()
