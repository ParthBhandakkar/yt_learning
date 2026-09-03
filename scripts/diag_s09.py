#!/usr/bin/env python3
"""
Forensic diagnosis of Strategy 09 on forex (no lookahead, no filters — isolate
the raw signal's edge). Answers: where does it bleed, and does the entry have
ANY directional edge?

Per trade we record gross R, net R, max-favourable-excursion (MFE) and
max-adverse-excursion (MAE) in units of risk, and the exit type. We also run a
DIRECTION-FLIP test: same setups, opposite direction. If flipped is not clearly
better, the signal has no directional edge (geometry doesn't predict FX).
"""
import os, sys
import logging
logging.disable(logging.CRITICAL)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "strategy_09_mss_ob_entry"))

import numpy as np
import pandas as pd
from core import infer_pip_size, round_turn_cost_pips
from scripts.utils.indicators import detect_mss
from strategy import BiasType
import replay_s09 as RP


def simulate_mfe_mae(df5, entry_time, direction, entry, sl, tp, max_bars=8640):
    lo = df5["low"].values; hi = df5["high"].values; cl = df5["close"].values
    start = df5.index.searchsorted(entry_time)
    is_long = direction == "long"
    best = entry; worst = entry
    for j in range(start, min(start + max_bars, len(df5))):
        best = max(best, hi[j]) if is_long else min(best, lo[j])
        worst = min(worst, lo[j]) if is_long else max(worst, hi[j])
        if is_long:
            if lo[j] <= sl: return df5.index[j], sl, "SL", best, worst
            if hi[j] >= tp: return df5.index[j], tp, "TP", best, worst
        else:
            if hi[j] >= sl: return df5.index[j], sl, "SL", best, worst
            if lo[j] <= tp: return df5.index[j], tp, "TP", best, worst
    j = min(start + max_bars, len(df5)) - 1
    return (df5.index[j], cl[j], "timeout", best, worst) if j >= start else (None, None, None, None, None)


def collect(sym, df1, df15, df5):
    df4 = RP.resample_df(df1, "4h")
    if len(df4) < 55 or len(df5) < 50:
        return None
    mss_list = detect_mss(df1, lookback=5, require_body_close=True)
    funnel = {"biases": 0, "with_mss": 0, "trades": 0}
    rows = []
    open_until = None
    for bias in RP.biases_all(df4):
        funnel["biases"] += 1
        ttl_end = bias.sweep_timestamp + pd.Timedelta(hours=RP.BIAS_TTL_HOURS)
        mss = RP.first_mss_within(mss_list, bias, ttl_end)
        if mss is None:
            continue
        funnel["with_mss"] += 1
        ms = df5.index.searchsorted(mss.timestamp); me = df5.index.searchsorted(ttl_end)
        win = df5.iloc[ms:me]
        if len(win) < 3:
            continue
        ob = RP.STRAT.find_ob_entry(df15, win, bias, mss)
        if ob is None:
            continue
        tap = ob.timestamp
        if open_until is not None and tap <= open_until:
            continue
        direction = "long" if bias.direction == BiasType.BULLISH else "short"
        sl = RP.compute_smart_sl(ob.order_block, bias.direction, df5, tap, sym)
        entry = ob.entry_price
        if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
            continue
        tp = RP.compute_tp(entry, sl, 1.5)
        et, ep, kind, best, worst = simulate_mfe_mae(df5, tap, direction, entry, sl, tp)
        if et is None:
            continue
        open_until = et
        funnel["trades"] += 1
        ps = infer_pip_size(entry); risk = abs(entry - sl) / ps
        if risk <= 0:
            continue
        cost = round_turn_cost_pips(entry)
        if direction == "long":
            gross = (ep - entry) / ps; mfe = (best - entry) / ps; mae = (entry - worst) / ps
        else:
            gross = (entry - ep) / ps; mfe = (entry - best) / ps; mae = (worst - entry) / ps
        rows.append({"dir": direction, "risk": risk, "grossR": gross / risk,
                     "netR": (gross - cost) / risk, "mfeR": mfe / risk, "maeR": mae / risk,
                     "kind": kind})
    return funnel, rows


def summarize(name, rows):
    n = len(rows)
    if n == 0:
        print(f"  {name}: no trades"); return
    g = np.array([r["grossR"] for r in rows]); net = np.array([r["netR"] for r in rows])
    wins = (g > 0).mean() * 100
    tp = sum(1 for r in rows if r["kind"] == "TP"); sl = sum(1 for r in rows if r["kind"] == "SL")
    to = sum(1 for r in rows if r["kind"] == "timeout")
    mfe = np.array([r["mfeR"] for r in rows]); mae = np.array([r["maeR"] for r in rows])
    print(f"  {name}: n={n} win={wins:.1f}% grossExpR={g.mean():+.3f} netExpR={net.mean():+.3f} "
          f"| TP={tp} SL={sl} TO={to} | avgMFE={mfe.mean():.2f}R avgMAE={mae.mean():.2f}R "
          f"| reachedTarget(1.5R)={ (mfe>=1.5).mean()*100:.0f}%")


def main():
    pairs = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
    all_rows = []
    funnels = {"biases": 0, "with_mss": 0, "trades": 0}
    for sym in pairs:
        f1 = f"data/{sym}/1h/{sym}_1h.csv"; f15 = f"data/{sym}/15m/{sym}_15m.csv"; f5 = f"data/{sym}/5m/{sym}_5m.csv"
        if not all(os.path.exists(x) for x in (f1, f15, f5)):
            continue
        df1 = RP.df_from_csv(f1); df15 = RP.df_from_csv(f15); df5 = RP.df_from_csv(f5)
        res = collect(sym, df1, df15, df5)
        if res is None:
            continue
        funnel, rows = res
        for k in funnels: funnels[k] += funnel[k]
        all_rows.extend(rows)
        print(f"{sym}: setups bias={funnel['biases']} -> MSS={funnel['with_mss']} -> trades={funnel['trades']}")

    print("\n===== FUNNEL (all pairs) =====")
    print(f"  4H biases: {funnels['biases']}  ->  with 1H MSS: {funnels['with_mss']}  ->  with OB+5M tap (trades): {funnels['trades']}")
    print("\n===== SIGNAL EDGE (raw, no filters) =====")
    summarize("AS-TRADED (with 4H bias)", all_rows)
    flipped = [{**r, "grossR": -r["grossR"], "netR": -r["grossR"] - (r["grossR"] - r["netR"]),
                "mfeR": r["maeR"], "maeR": r["mfeR"]} for r in all_rows]
    # net for flipped: gross flips sign, cost still subtracted -> approximate
    for r, fr in zip(all_rows, flipped):
        cost_R = r["grossR"] - r["netR"]
        fr["netR"] = -r["grossR"] - cost_R
    summarize("FLIPPED (opposite dir)  ", flipped)
    g = np.array([r["grossR"] for r in all_rows])
    print(f"\n  >>> Direction edge (gross): as-traded {g.mean():+.3f}R vs flipped {-g.mean():+.3f}R per trade")


if __name__ == "__main__":
    main()
