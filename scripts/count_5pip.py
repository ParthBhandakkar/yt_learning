#!/usr/bin/env python3
"""For each S97 trade, walk 5M bars from entry and count how many reached at
least +5 pips favorable BEFORE the stop-loss was hit (stop checked before the
favorable extreme within a bar = conservative)."""
import glob, os, sys
import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)
from core import load_csv, infer_pip_size
import strategy_97_trend_meanreversion as s97

PAIRS = {
    "GBPUSD": ("data/GBPUSD/4h/GBPUSD_4h.csv", "data/GBPUSD/5m/GBPUSD_5m.csv"),
    "AUDUSD": ("data/AUDUSD/4h/AUDUSD_4h.csv", glob.glob("/tmp/drive_data/AUDUSD/5m/*.csv")[0]),
    "EURUSD": ("data/EURUSD/4h/EURUSD_4h.csv", "/tmp/drive_data/EURUSD/5m/EURUSD_5m.csv"),
    "NZDUSD": ("data/NZDUSD/4h/NZDUSD_4h.csv", "/tmp/drive_data/NZDUSD/5m/NZDUSD_5m.csv"),
    "USDCAD": ("data/USDCAD/4h/USDCAD_4h.csv", "/tmp/drive_data/USDCAD/5m/USDCAD_5m.csv"),
    "USDCHF": ("data/USDCHF/4h/USDCHF_4h.csv", "/tmp/drive_data/USDCHF/5m/USDCHF_5m.csv"),
    "USDJPY": ("data/USDJPY/4h/USDJPY_4h.csv", "/tmp/drive_data/USDJPY/5m/USDJPY_5m.csv"),
    "XAUUSD": ("data/XAUUSD/4h/XAUUSD_4h.csv", "/tmp/drive_data/XAUUSD/5m/XAUUSD_5m.csv"),
}
PIP_THRESH = 5.0


def df5_of(path):
    c = load_csv(path)
    idx = pd.to_datetime([x.timestamp for x in c], unit="s", utc=True)
    return pd.DataFrame({"high": [x.high for x in c], "low": [x.low for x in c]}, index=idx)


def main():
    tot_trades = tot_hit5 = 0
    print(f"{'pair':8s} {'trades':>7s} {'reached>=5p':>11s} {'pct':>6s}  (pip size)")
    for p, (f4, f5) in PAIRS.items():
        trades = s97.run_strategy(s97._df_from_csv(f4), f"/tmp/c5_{p}.json", symbol=p)
        df5 = df5_of(f5)
        hi = df5["high"].values; lo = df5["low"].values
        n = len(df5)
        hit5 = 0
        pip = None
        for t in trades:
            entry = float(t["entry_price"]); stop = float(t["stop_loss"])
            is_long = t["direction"] == "long"
            pip = infer_pip_size(entry)
            thresh = PIP_THRESH * pip
            et = pd.Timestamp(t["entry_time"]); xt = pd.Timestamp(t["exit_time"])
            start = df5.index.searchsorted(et)
            end = min(df5.index.searchsorted(xt) + 1, n)
            reached = False
            for j in range(start, end):
                # stop first (conservative)
                if (lo[j] <= stop) if is_long else (hi[j] >= stop):
                    break
                fav = (hi[j] - entry) if is_long else (entry - lo[j])
                if fav >= thresh:
                    reached = True
                    break
            if reached:
                hit5 += 1
        tot_trades += len(trades); tot_hit5 += hit5
        pct = hit5/len(trades)*100 if trades else 0
        print(f"{p:8s} {len(trades):7d} {hit5:11d} {pct:5.1f}%  (1 pip = {pip})")
        os.remove(f"/tmp/c5_{p}.json")
    print("-"*46)
    print(f"{'TOTAL':8s} {tot_trades:7d} {tot_hit5:11d} {tot_hit5/tot_trades*100:5.1f}%")


if __name__ == "__main__":
    main()
