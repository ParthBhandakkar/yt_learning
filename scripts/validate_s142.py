#!/usr/bin/env python3
"""
Walk-forward validation for Strategy 142.

Per instrument: first 50% TRAIN, next 25% VALIDATION, latest 25% SEALED TEST.
Normal runs never print sealed-test results. Use --sealed exactly once after all
parameters are frozen. Costs are deducted in R before every metric.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategies"))

from core import load_csv, round_turn_cost_price
import strategy_142_mtf_pullback_reclaim as s142

FILES = [
    ("EURUSD", ROOT / "data/EURUSD/4h/EURUSD_4h.csv"),
    ("GBPUSD", ROOT / "data/GBPUSD/4h/GBPUSD_4h.csv"),
    ("AUDUSD", ROOT / "data/AUDUSD/4h/AUDUSD_4h.csv"),
    ("NZDUSD", ROOT / "data/NZDUSD/4h/NZDUSD_4h.csv"),
    ("USDCAD", ROOT / "data/USDCAD/4h/USDCAD_4h.csv"),
    ("USDCHF", ROOT / "data/USDCHF/4h/USDCHF_4h.csv"),
    ("USDJPY", ROOT / "data/USDJPY/4h/USDJPY_4h.csv"),
    ("XAUUSD", ROOT / "data/XAUUSD/4h/XAUUSD_4h.csv"),
]

CACHE = {}


def candles(path):
    key = str(path)
    if key not in CACHE:
        CACHE[key] = load_csv(key)
    return CACHE[key]


def epoch(iso):
    return int(datetime.fromisoformat(iso).timestamp())


def net_r(t):
    risk = abs(float(t["entry_price"]) - float(t["stop_loss"]))
    if risk <= 0:
        return 0.0
    cost = round_turn_cost_price(float(t["entry_price"]), symbol=t.get("symbol"))
    return float(t["gross_R"]) - cost / risk


def metrics(trades):
    if not trades:
        return {"n": 0, "win": 0.0, "exp": 0.0, "pf": 0.0,
                "total": 0.0, "avg_w": 0.0, "avg_l": 0.0}
    rs = [t["_net_r"] for t in trades]
    w = [r for r in rs if r > 0]
    lo = [-r for r in rs if r < 0]
    return {
        "n": len(rs), "win": len(w) / len(rs), "exp": sum(rs) / len(rs),
        "pf": sum(w) / sum(lo) if lo else float("inf"), "total": sum(rs),
        "avg_w": sum(w) / len(w) if w else 0.0,
        "avg_l": sum(lo) / len(lo) if lo else 0.0,
    }


def run(params, *, include_sealed=False):
    """Generate only data that the requested protocol stage is allowed to see.

    Development mode truncates every price series at the 75% boundary before
    strategy generation. The hidden OHLC path is therefore never evaluated,
    costed, or placed in a bucket. Only an explicit sealed run sees full data.
    """
    out = {"train": {}, "validation": {}, "sealed": {}}
    for sym, path in FILES:
        full = candles(path)
        if not full:
            continue
        start, end = full[0].timestamp, full[-1].timestamp
        span = end - start
        cut1, cut2 = start + int(0.50 * span), start + int(0.75 * span)
        allowed = full if include_sealed else [bar for bar in full if bar.timestamp < cut2]
        tr = s142.generate_trades(allowed, symbol=sym, params=params)
        for t in tr:
            t["_net_r"] = net_r(t)
            et = epoch(t["entry_time"])
            if et >= cut2 and not include_sealed:
                raise RuntimeError("sealed trade generated during development run")
            bucket = "train" if et < cut1 else "validation" if et < cut2 else "sealed"
            out[bucket].setdefault(sym, []).append(t)
        for bucket in out:
            out[bucket].setdefault(sym, [])
    return out


def print_bucket(name, by_symbol):
    pooled = [t for rows in by_symbol.values() for t in rows]
    m = metrics(pooled)
    print(f"{name:11s} n={m['n']:>4} win={m['win']:>6.1%} exp={m['exp']:+.3f}R "
          f"PF={m['pf']:.2f} total={m['total']:+.1f}R "
          f"avgW/L={m['avg_w']:.2f}/{m['avg_l']:.2f}R")
    pos = 0
    for sym, rows in by_symbol.items():
        q = metrics(rows)
        pos += q["exp"] > 0
        print(f"  {sym:7s} n={q['n']:>3} win={q['win']:>6.1%} "
              f"exp={q['exp']:+.3f}R PF={q['pf']:.2f}")
    return m, pos


def evaluate(params, sealed=False, label=""):
    print(f"\n=== {label or params or 'defaults'} ===")
    result = run(params, include_sealed=sealed)
    tm, tp = print_bucket("TRAIN", result["train"])
    vm, vp = print_bucket("VALIDATION", result["validation"])
    print(f"positive instruments: train {tp}/8, validation {vp}/8")
    if sealed:
        print("\n*** SEALED TEST OPENED — DO NOT RETUNE AFTER THIS ***")
        sm, sp = print_bucket("SEALED", result["sealed"])
        print(f"SEALED promotion: win>=60%={sm['win'] >= .60}, "
              f"exp>0={sm['exp'] > 0}, instruments positive={sp}/8")
    return tm, vm


def number(x):
    try:
        return int(x)
    except ValueError:
        return float(x)


def parse_set(text):
    out = {}
    for item in text.split(","):
        if item.strip():
            k, v = item.split("=", 1)
            key = k.strip()
            if key not in s142.P:
                raise ValueError(f"unknown s142 parameter: {key}")
            out[key] = number(v.strip())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="")
    ap.add_argument("--sweep", default="", help="key=v1|v2|v3")
    ap.add_argument("--sealed", action="store_true")
    args = ap.parse_args()
    if args.sealed and args.sweep:
        ap.error("--sealed cannot be combined with --sweep")
    base = parse_set(args.set)
    if args.sweep:
        key, values = args.sweep.split("=", 1)
        key = key.strip()
        if key not in s142.P:
            ap.error(f"unknown s142 parameter: {key}")
        for value in values.split("|"):
            val = number(value)
            evaluate({**base, key: val}, label=f"{key}={val}")
    else:
        evaluate(base, sealed=args.sealed)


if __name__ == "__main__":
    main()
