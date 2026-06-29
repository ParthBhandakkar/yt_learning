#!/usr/bin/env python3
"""
Refine the RAW Strategy 09 signal (no lookahead, no cheating) to maximise
realistic win rate WHILE staying profitable.

Signal core (unchanged, causal): 4H liquidity-sweep bias -> first 1H MSS within
16h -> 15M order block in OTE -> 5M tap. Smart SL (OB extreme -> worst 5M wick
-> +buffer).

Refinements (all standard, realistic, causal):
  - PARTIAL + BREAKEVEN: bank half at +partial_R, move stop to entry for the
    rest. Converts many full losers into small wins/scratches -> higher win rate
    and lower variance, no future peeking.
  - Final target: remainder runs to final_R.
  - Optional QUALITY GATES (causal): MSS displacement (big body), session window.
Outcome of each trade is scored in R including the partial.
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


def sim_partial_be(df5, entry_time, direction, entry, sl, partial_R, final_R,
                   cost_pips, pip, max_bars=8640):
    """Half off at +partial_R (then stop->BE), remainder to +final_R. Returns net R."""
    lo = df5["low"].values; hi = df5["high"].values; cl = df5["close"].values
    start = df5.index.searchsorted(entry_time)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    is_long = direction == "long"
    tp1 = entry + partial_R * risk if is_long else entry - partial_R * risk
    tpf = entry + final_R * risk if is_long else entry - final_R * risk
    cur_stop = sl
    half_done = False
    realized = 0.0  # in R, gross
    cost_R = (cost_pips * pip) / risk  # one round-turn in R
    for j in range(start, min(start + max_bars, len(df5))):
        if is_long:
            hit_stop = lo[j] <= cur_stop
            hit_p1 = (not half_done) and hi[j] >= tp1
            hit_pf = half_done and hi[j] >= tpf
        else:
            hit_stop = hi[j] >= cur_stop
            hit_p1 = (not half_done) and lo[j] <= tp1
            hit_pf = half_done and lo[j] <= tpf
        # conservative: stop first
        if hit_stop:
            stop_R = (cur_stop - entry) / risk if is_long else (entry - cur_stop) / risk
            realized += 0.5 * stop_R if half_done else 1.0 * stop_R
            # cost: 1.5 round-turns if a partial was already taken, else 1.0
            return realized - (1.5 * cost_R if half_done else cost_R)
        if hit_p1:
            realized += 0.5 * partial_R
            half_done = True
            cur_stop = entry  # breakeven
            continue
        if hit_pf:
            realized += 0.5 * final_R
            return realized - 1.5 * cost_R
    # ran out: close remainder at last close
    j = min(start + max_bars, len(df5)) - 1
    if j < start:
        return None
    last = cl[j]
    rem_R = (last - entry) / risk if is_long else (entry - last) / risk
    realized += (0.5 if half_done else 1.0) * rem_R
    return realized - (1.5 * cost_R if half_done else cost_R)


def displacement_ok(df1, mss_ts, min_body_pct):
    i = df1.index.searchsorted(mss_ts)
    if i >= len(df1):
        i = len(df1) - 1
    c = df1.iloc[i]
    body_pct = abs(c["close"] - c["open"]) / c["close"] * 100
    return body_pct >= min_body_pct


def run_pair(sym, df1, df15, df5, partial_R, final_R, min_body_pct, session):
    df4 = RP.resample_df(df1, "4h")
    if len(df4) < 55 or len(df5) < 50:
        return []
    mss_list = detect_mss(df1, lookback=5, require_body_close=True)
    out = []
    open_until = None
    for bias in RP.biases_all(df4):
        ttl_end = bias.sweep_timestamp + pd.Timedelta(hours=RP.BIAS_TTL_HOURS)
        mss = RP.first_mss_within(mss_list, bias, ttl_end)
        if mss is None:
            continue
        if min_body_pct > 0 and not displacement_ok(df1, mss.timestamp, min_body_pct):
            continue
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
        if session and not (7 <= tap.hour <= 20):  # London+NY UTC window
            continue
        direction = "long" if bias.direction == BiasType.BULLISH else "short"
        sl = RP.compute_smart_sl(ob.order_block, bias.direction, df5, tap, sym)
        entry = ob.entry_price
        if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
            continue
        pip = infer_pip_size(entry); cost = round_turn_cost_pips(entry)
        r = sim_partial_be(df5, tap, direction, entry, sl, partial_R, final_R, cost, pip)
        if r is None:
            continue
        open_until = tap
        out.append((tap, r))
    return out


PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
         "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
         "GBPAUD", "GBPCHF", "GBPJPY"]


def evaluate(partial_R, final_R, min_body_pct, session, label, oos_frac=0.0):
    allR = []; pos_pairs = 0; npair = 0; per = []
    for sym in PAIRS:
        f1 = f"data/{sym}/1h/{sym}_1h.csv"; f15 = f"data/{sym}/15m/{sym}_15m.csv"; f5 = f"data/{sym}/5m/{sym}_5m.csv"
        if not all(os.path.exists(x) for x in (f1, f15, f5)):
            continue
        df1 = RP.df_from_csv(f1); df15 = RP.df_from_csv(f15); df5 = RP.df_from_csv(f5)
        trades = run_pair(sym, df1, df15, df5, partial_R, final_R, min_body_pct, session)
        if oos_frac > 0 and df5 is not None and len(df5) > 0:
            # keep only trades in the most recent oos_frac of the pair's 5m span
            t0 = df5.index[0]; t1 = df5.index[-1]
            cut = t0 + (t1 - t0) * (1 - oos_frac)
            trades = [(t, r) for (t, r) in trades if t >= cut]
        R = [r for _, r in trades]
        if not R:
            continue
        npair += 1
        tot = sum(R)
        if tot > 0: pos_pairs += 1
        allR.extend(R); per.append((sym, len(R), round(tot, 1), round((np.array(R) > 0).mean() * 100, 1)))
    if not allR:
        print(f"{label}: no trades"); return
    a = np.array(allR)
    win = (a > 0).mean() * 100
    print(f"{label}: trades={len(a)} WIN%={win:.1f} netExpR={a.mean():+.3f} totR={a.sum():+.1f} "
          f"profitable_pairs={pos_pairs}/{npair}")
    for s, n, t, w in per:
        print(f"      {s}: n={n:>3} win={w:>5}% totR={t:+.1f}")
    return win, a.mean(), a.sum(), pos_pairs, npair


if __name__ == "__main__":
    os.environ.setdefault("BT_COST_MULT", "0.4")  # raw/ECN execution
    print(f"(cost_mult={os.environ['BT_COST_MULT']} ~raw spread, fair 1.5x cost on partials)\n")
    print("===== CHOSEN CONFIG: partial 0.5R + breakeven, final 1.5R, displacement gate =====")
    print("\n--- FULL SAMPLE (all available history per pair) ---")
    evaluate(0.5, 1.5, 0.10, False, "FULL  ")
    print("\n--- OUT-OF-SAMPLE (most recent 40% of each pair, fixed rule) ---")
    evaluate(0.5, 1.5, 0.10, False, "OOS40 ", oos_frac=0.40)
