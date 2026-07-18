#!/usr/bin/env python3
"""Find the minimum fetch window (bars) so the live detector (which sees only a
rolling window) matches the full-history backtest EMA/SMA and produces identical
signals. EWM(adjust=False) is recursive, so too small a window => wrong EMA200."""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
STRAT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, STRAT)

import strategy_97_trend_meanreversion as bt  # noqa: E402
from detection_s97 import detect_signal  # noqa: E402
from config import CONFIG  # noqa: E402

DATA = os.path.join(STRAT, "data")
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "XAUUSD"]


def occupancy(df, trades):
    pos_of = {ts: k for k, ts in enumerate(df.index)}
    occ = [False] * len(df)
    for t in trades:
        e = pd.Timestamp(t["entry_time"]); x = pd.Timestamp(t["exit_time"])
        if e in pos_of and x in pos_of:
            for k in range(pos_of[e], pos_of[x] + 1):
                occ[k] = True
    return occ


def entries_for_window(df, occ, window):
    """Live entries when the detector only sees `window` most-recent closed bars."""
    n = len(df)
    ent = set()
    i = CONFIG.s97_trend_ema + 1
    while i < n - 1:
        if occ[i]:
            i += 1; continue
        lo = 0 if window is None else max(0, i + 1 - window)
        sig = detect_signal(df.iloc[lo: i + 1])
        if sig is not None:
            ent.add((pd.Timestamp(df.index[i + 1]).isoformat(), sig["direction"]))
        i += 1
    return ent


def main():
    windows = [240, 500, 800, 1000, 1500, 2000, None]
    print(f"{'window':>7} | " + " ".join(f"{p:>7}" for p in PAIRS) + " |  total_diff")
    for w in windows:
        diffs = []
        total = 0
        for pair in PAIRS:
            csv = os.path.join(DATA, pair, "4h", f"{pair}_4h.csv")
            if not os.path.exists(csv):
                diffs.append("-"); continue
            df = bt._df_from_csv(csv)
            import tempfile
            trades = bt.run_strategy(df, os.path.join(tempfile.gettempdir(), "w.json"), symbol="W")
            bt_set = {(pd.Timestamp(t["entry_time"]).isoformat(), t["direction"]) for t in trades}
            occ = occupancy(df, trades)
            live = entries_for_window(df, occ, w)
            d = len(bt_set ^ live)
            diffs.append(str(d)); total += d
        label = "full" if w is None else str(w)
        print(f"{label:>7} | " + " ".join(f"{x:>7}" for x in diffs) + f" |  {total}")


if __name__ == "__main__":
    main()
