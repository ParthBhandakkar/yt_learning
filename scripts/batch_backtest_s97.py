#!/usr/bin/env python3
"""Multi-pair backtest + cost-aware evaluation for Strategy 97 (4H only)."""
import glob, os, sys
import numpy as np

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import round_turn_cost_price
import strategy_97_trend_meanreversion as s97

PAIRS = {p: f"data/{p}" for p in
         ["GBPUSD", "AUDUSD", "EURUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY", "XAUUSD"]}


def find4h(d):
    for pat in (os.path.join(d, "4h", "*.csv"), os.path.join(d, "*_4h_*.csv"), os.path.join(d, "*_4h.csv")):
        c = glob.glob(pat)
        if c:
            return sorted(c, key=len)[0]
    return None


def net(t):
    r = abs(float(t["entry_price"]) - float(t["stop_loss"]))
    return float(t["pnl_R"]) - round_turn_cost_price(float(t["entry_price"])) / r if r > 0 else None


def main():
    allt = []
    print(f"{'pair':8s} {'n':>4s} {'net_exp':>8s} {'win%':>6s} {'totalR':>8s}  IS/OOS")
    for p, d in PAIRS.items():
        f = find4h(d)
        if not f:
            print(f"{p}: no 4h"); continue
        df = s97._df_from_csv(f)
        tr = s97.run_strategy(df, f"/tmp/s97_{p}.json", symbol=p)
        xs = [x for x in (net(t) for t in tr) if x is not None]
        allt.extend(tr)
        if not xs:
            continue
        ordered = [net(t) for t in sorted(tr, key=lambda z: z["entry_time"]) if net(t) is not None]
        cut = int(len(ordered) * 0.6)
        isx, oos = ordered[:cut], ordered[cut:]
        print(f"{p:8s} {len(xs):4d} {np.mean(xs):+8.3f} {np.mean([v>0 for v in xs])*100:5.0f}% "
              f"{sum(xs):+8.1f}  IS {np.mean(isx):+.3f}({len(isx)}) OOS {np.mean(oos):+.3f}({len(oos)})")
    xs = [x for x in (net(t) for t in allt) if x is not None]
    ordered = [net(t) for t in sorted(allt, key=lambda z: z["entry_time"]) if net(t) is not None]
    cut = int(len(ordered) * 0.6)
    print("-" * 78)
    print(f"{'BASKET':8s} {len(xs):4d} {np.mean(xs):+8.3f} {np.mean([v>0 for v in xs])*100:5.0f}% "
          f"{sum(xs):+8.1f}  IS {np.mean(ordered[:cut]):+.3f} OOS {np.mean(ordered[cut:]):+.3f}")


if __name__ == "__main__":
    main()
