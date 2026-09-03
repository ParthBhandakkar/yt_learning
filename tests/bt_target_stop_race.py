#!/usr/bin/env python3
"""
Proper target-vs-stop race for the drawdown-doc question.

Reframed goal (per user): "if we capture the target pips, we are good."
So the ONLY question that matters is:

    From entry, does price touch the TARGET before it touches the STOP?

This is a first-touch race. Resolving it correctly needs the finest timeframe
available, because when a single bar's range covers BOTH levels, OHLC alone
cannot tell you which was hit first. We:

  - race on the finest timeframe that covers each trade's entry timestamp,
  - when a single bar straddles both levels we mark it AMBIGUOUS and report a
    conservative bound (stop-first) and an optimistic bound (target-first),
  - report hit-rate (target-before-stop) and expectancy in R.

R = stop distance (risk). Win = +target/stop R. Loss = -1 R.
Expectancy_R = p_win*(target/stop) - p_loss.
"""
import json
import csv
import os
from datetime import datetime

DATA_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

PIP = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001,
    "USDCAD": 0.0001, "USDCHF": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.01,
}

# (target_1x, target_2x) native pips  -- from backtest_drawdown_analysis.md
TARGETS = {
    "XAUUSD": (100, 200), "EURUSD": (3, 6), "GBPUSD": (3.5, 7), "AUDUSD": (2, 4),
    "NZDUSD": (1.5, 3), "USDCAD": (3.5, 7), "USDCHF": (2.5, 5), "USDJPY": (5, 10),
}

# (stop_1x, stop_2x) native pips -- the doc's "min stop for >60% survival"
STOPS = {
    "XAUUSD": (619, 650), "EURUSD": (19, 19), "GBPUSD": (18, 18), "AUDUSD": (12, 14),
    "NZDUSD": (13, 13), "USDCAD": (13, 15), "USDCHF": (13, 13), "USDJPY": (19, 21),
}

# finest -> coarsest preference
TF_PREFERENCE = ["1m", "5m", "15m", "1h", "1H", "4h"]


def load_tf(pair, tf):
    folder = os.path.join(DATA_ROOT, pair, tf)
    if not os.path.isdir(folder):
        return None
    path = os.path.join(folder, f"{pair}_{tf}.csv")
    if not os.path.exists(path):
        # fall back to any csv in the timeframe folder (handles odd filenames)
        csvs = [f for f in os.listdir(folder) if f.lower().endswith(".csv")]
        if not csvs:
            return None
        path = os.path.join(folder, csvs[0])
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append((int(float(r["time"])), float(r["high"]), float(r["low"])))
    rows.sort()
    return rows


def load_pair_frames(pair):
    """Return list of (tf_name, rows, ts_list) from finest to coarsest available."""
    frames = []
    seen = set()
    for tf in TF_PREFERENCE:
        if tf.lower() in seen:
            continue
        rows = load_tf(pair, tf)
        if rows:
            frames.append((tf, rows, [x[0] for x in rows]))
            seen.add(tf.lower())
    return frames


def _ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def race_one(rows, ts_list, entry_ts, entry_price, direction, target_px, stop_px):
    """Race a single trade on one timeframe.
    Returns one of: 'win', 'loss', 'ambiguous', 'open'."""
    # first bar at or after entry
    lo, hi = 0, len(ts_list)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts_list[mid] < entry_ts:
            lo = mid + 1
        else:
            hi = mid
    start = lo
    if start >= len(rows):
        return "open"
    if direction == "long":
        tgt_lvl = entry_price + target_px
        stp_lvl = entry_price - stop_px
        for j in range(start, len(rows)):
            _, h, l = rows[j]
            ht = h >= tgt_lvl
            hs = l <= stp_lvl
            if ht and hs:
                return "ambiguous"
            if ht:
                return "win"
            if hs:
                return "loss"
    else:
        tgt_lvl = entry_price - target_px
        stp_lvl = entry_price + stop_px
        for j in range(start, len(rows)):
            _, h, l = rows[j]
            ht = l <= tgt_lvl
            hs = h >= stp_lvl
            if ht and hs:
                return "ambiguous"
            if ht:
                return "win"
            if hs:
                return "loss"
    return "open"


def race_trade(frames, entry_ts, entry_price, direction, target_px, stop_px):
    """Use the finest frame that covers entry_ts; report which TF was used."""
    for tf, rows, ts_list in frames:
        if ts_list and ts_list[0] <= entry_ts <= ts_list[-1]:
            return race_one(rows, ts_list, entry_ts, entry_price,
                            direction, target_px, stop_px), tf
    # entry outside all frames -> use coarsest anyway
    tf, rows, ts_list = frames[-1]
    return race_one(rows, ts_list, entry_ts, entry_price,
                    direction, target_px, stop_px), tf


def summarize(pair, label, target_pips, stop_pips, trades, frames):
    pip = PIP[pair]
    tpx, spx = target_pips * pip, stop_pips * pip
    counts = {"win": 0, "loss": 0, "ambiguous": 0, "open": 0}
    tf_used = {}
    for t in trades:
        et = _ts(t["entry_time"])
        res, tf = race_trade(frames, et, float(t["entry_price"]),
                             t["direction"], tpx, spx)
        counts[res] += 1
        tf_used[tf] = tf_used.get(tf, 0) + 1
    n = len(trades)
    rr = target_pips / stop_pips  # reward:risk per win
    decided = counts["win"] + counts["loss"]
    # bounds: conservative counts ambiguous as loss, optimistic as win
    win_lo = counts["win"]
    win_hi = counts["win"] + counts["ambiguous"]
    dec_incl = decided + counts["ambiguous"]

    def expectancy(nwin):
        nloss = dec_incl - nwin
        if dec_incl == 0:
            return 0.0, 0.0
        wr = nwin / dec_incl
        exp_r = wr * rr - (1 - wr)
        return wr * 100, exp_r

    wr_lo, exp_lo = expectancy(win_lo)
    wr_hi, exp_hi = expectancy(win_hi)
    tf_str = ", ".join(f"{k}:{v}" for k, v in sorted(tf_used.items()))
    print(f"  {label:9s} tgt={target_pips}p stop={stop_pips}p  R:R=1:{1/rr:.2f} (win=+{rr:.2f}R)")
    print(f"     n={n}  win={counts['win']} loss={counts['loss']} "
          f"ambiguous={counts['ambiguous']} open={counts['open']}   [TF {tf_str}]")
    if counts["ambiguous"] == 0:
        print(f"     TARGET-before-STOP hit rate: {wr_lo:.1f}%   expectancy: {exp_lo:+.3f}R per trade")
    else:
        print(f"     hit rate: {wr_lo:.1f}%..{wr_hi:.1f}%  "
              f"expectancy: {exp_lo:+.3f}R..{exp_hi:+.3f}R  (ambiguous bracketed)")


def main():
    # ---- s97 all pairs (4H strategy) ----
    s97 = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "s97_all_pairs_trades.json")))
    print("=" * 78)
    print("S97 — target-before-stop race (finest available timeframe)")
    print("=" * 78)
    for pair in sorted(s97.keys()):
        frames = load_pair_frames(pair)
        finest = frames[0][0] if frames else "NONE"
        print(f"\n{pair}  (finest TF available: {finest})")
        t1, t2 = TARGETS[pair]
        s1, s2 = STOPS[pair]
        summarize(pair, "1x-target", t1, s1, s97[pair], frames)
        summarize(pair, "2x-target", t2, s2, s97[pair], frames)

    # ---- s98 XAUUSD (1H strategy) ----
    print("\n" + "=" * 78)
    print("S98 — XAUUSD 1H  (target-before-stop race)")
    print("=" * 78)
    s98_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "dashboard", "out",
                            "s98_strategy_98_xau_trend_liquidity_trail_20260717_141427.json")
    s98 = json.load(open(s98_path))["trades"]
    frames = load_pair_frames("XAUUSD")
    print(f"\nXAUUSD  (finest TF available: {frames[0][0]})")
    summarize("XAUUSD", "s98-100p", 100, 387, s98, frames)
    summarize("XAUUSD", "s98-200p", 200, 507, s98, frames)


if __name__ == "__main__":
    main()
