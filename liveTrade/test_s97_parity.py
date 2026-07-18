#!/usr/bin/env python3
"""
Parity test: prove the LIVE s97 detector (detection_s97.detect_signal) fires the
EXACT same entries as the s97 backtest (strategy_97_trend_meanreversion).

Method: replay each pair's 4H CSV bar-by-bar. At each closed bar i, feed the
history up to i to the live detector while enforcing the same "one position at a
time" gating the live engine uses. Compare the resulting entry bars / directions
against the backtest's trades.

Run:  python test_s97_parity.py
"""
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


def backtest_trades(df, symbol):
    import tempfile
    out = os.path.join(tempfile.gettempdir(), f"s97_bt_{symbol}.json")
    return bt.run_strategy(df, out, symbol=symbol)


def replay_live(df):
    """Walk bars using the backtest's EXACT in-position windows for gating, then
    evaluate the live detector only on flat bars (exactly how the backtest gates
    entries). A live signal on bar i implies an entry at bar i+1.
    Returns (signals, trades)."""
    n = len(df)
    trades = backtest_trades(df, "PARITY")

    idx_list = list(df.index)
    pos_of = {ts: k for k, ts in enumerate(idx_list)}
    occupied = [False] * n
    for t in trades:
        e = pd.Timestamp(t["entry_time"]); x = pd.Timestamp(t["exit_time"])
        if e in pos_of and x in pos_of:
            for k in range(pos_of[e], pos_of[x] + 1):
                occupied[k] = True

    signals = []
    i = CONFIG.s97_trend_ema + 1
    while i < n - 1:
        if occupied[i]:
            i += 1
            continue
        sig = detect_signal(df.iloc[: i + 1])
        if sig is not None:
            entry_idx = i + 1
            signals.append((
                pd.Timestamp(df.index[i]).isoformat(),
                pd.Timestamp(df.index[entry_idx]).isoformat(),
                sig["direction"],
                round(sig["risk"], 8),
            ))
        i += 1
    return signals, trades


def main():
    total_bt = total_live = matched = 0
    for pair in PAIRS:
        csv = os.path.join(DATA, pair, "4h", f"{pair}_4h.csv")
        if not os.path.exists(csv):
            print(f"{pair}: no CSV, skip")
            continue
        df = bt._df_from_csv(csv)
        live, trades = replay_live(df)

        bt_entries = [(pd.Timestamp(t["entry_time"]).isoformat(), t["direction"]) for t in trades]
        live_entries = [(e, d) for (_s, e, d, _r) in live]

        bt_set = set(bt_entries)
        live_set = set(live_entries)
        inter = bt_set & live_set
        only_bt = bt_set - live_set
        only_live = live_set - bt_set

        total_bt += len(bt_set)
        total_live += len(live_set)
        matched += len(inter)
        status = "OK" if not only_bt and not only_live else "DIFF"
        print(f"{pair:8s} bt={len(bt_set):4d} live={len(live_set):4d} "
              f"match={len(inter):4d} only_bt={len(only_bt)} only_live={len(only_live)} [{status}]")
        for e, d in sorted(only_bt)[:3]:
            print(f"    only_bt : {d:5s} {e}")
        for e, d in sorted(only_live)[:3]:
            print(f"    only_liv: {d:5s} {e}")

    print("-" * 60)
    print(f"TOTAL bt={total_bt} live={total_live} matched={matched} "
          f"({matched/total_bt*100:.1f}% of backtest entries reproduced live)")


if __name__ == "__main__":
    main()
