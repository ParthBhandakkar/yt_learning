#!/usr/bin/env python3
"""Multi-pair backtest + cost-aware evaluation for Strategy 96.

For each pair directory it locates the 5m/15m/1h/4h CSVs (by glob), runs the
tuned strategy, and reports net-of-cost expectancy, win rate, trade count and a
temporal 60/40 out-of-sample split. Aggregates across pairs at the end.

Usage:
  python3 batch_backtest_s96.py <pair_dir> [<pair_dir> ...]
Each <pair_dir> must contain subfolders 5m/ 15m/ 1h/ 4h with one CSV each,
OR be a flat dir with files named *_<tf>_*.csv.
"""
import glob
import json
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
sys.path.insert(0, os.path.join(THIS, "strategy_09_mss_ob_entry"))

from core import round_turn_cost_price
import strategy_96_mss_ob_tuned as s96


def find_tf(pair_dir, tf):
    # try subfolder first
    cands = glob.glob(os.path.join(pair_dir, tf, "*.csv"))
    if not cands:
        cands = glob.glob(os.path.join(pair_dir, f"*_{tf}_*.csv"))
    if not cands:
        cands = glob.glob(os.path.join(pair_dir, f"*_{tf}.csv"))
    return sorted(cands, key=len)[0] if cands else None


def net_R(t):
    e = float(t["entry_price"]); sl = float(t["stop_loss"]); r = abs(e - sl)
    if r <= 0:
        return None
    return float(t["pnl_R"]) - round_turn_cost_price(e) / r


def eval_trades(trades):
    xs = [x for x in (net_R(t) for t in trades) if x is not None]
    if not xs:
        return None
    xs_time = sorted(trades, key=lambda t: t["entry_time"])
    ordered = [net_R(t) for t in xs_time if net_R(t) is not None]
    cut = int(len(ordered) * 0.6)
    def stat(seg):
        if not seg:
            return (0, 0.0, 0.0)
        return (len(seg), sum(seg) / len(seg), sum(1 for v in seg if v > 0) / len(seg) * 100)
    return {"n": len(xs), "exp": sum(xs) / len(xs),
            "win": sum(1 for v in xs if v > 0) / len(xs) * 100,
            "total": sum(xs),
            "is": stat(ordered[:cut]), "oos": stat(ordered[cut:])}


def run_pair(pair_dir):
    name = os.path.basename(pair_dir.rstrip("/"))
    files = {tf: find_tf(pair_dir, tf) for tf in ("4h", "1h", "15m", "5m")}
    if any(v is None for v in files.values()):
        print(f"[{name}] SKIP missing timeframes: "
              f"{[tf for tf,v in files.items() if v is None]}")
        return None
    dfs = {tf: s96._df_from_csv(files[tf]) for tf in files}
    out = f"/tmp/s96_{name}.json"
    trades = s96.run_strategy(dfs["4h"], dfs["1h"], dfs["15m"], dfs["5m"], out, symbol=name)
    r = eval_trades(trades)
    if r is None:
        print(f"[{name}] 0 trades")
        return None
    print(f"[{name:8s}] n={r['n']:3d} exp={r['exp']:+.3f}R win={r['win']:4.1f}% "
          f"total={r['total']:+6.1f}R | IS n={r['is'][0]:2d} {r['is'][1]:+.3f}R {r['is'][2]:.0f}% "
          f"| OOS n={r['oos'][0]:2d} {r['oos'][1]:+.3f}R {r['oos'][2]:.0f}%")
    return trades


def main(dirs):
    all_trades = []
    for d in dirs:
        t = run_pair(d)
        if t:
            all_trades.extend(t)
    if all_trades:
        print("-" * 100)
        r = eval_trades(all_trades)
        print(f"[BASKET  ] n={r['n']:3d} exp={r['exp']:+.3f}R win={r['win']:4.1f}% "
              f"total={r['total']:+6.1f}R | IS n={r['is'][0]:2d} {r['is'][1]:+.3f}R {r['is'][2]:.0f}% "
              f"| OOS n={r['oos'][0]:2d} {r['oos'][1]:+.3f}R {r['oos'][2]:.0f}%")


if __name__ == "__main__":
    main(sys.argv[1:])
