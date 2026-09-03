#!/usr/bin/env python3
"""Signal-funnel diagnostics for Strategy 144 without sealed partition access."""
import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "strategies")]
from core import load_csv
import strategy_144_ny_orb_session_reversal as s

parser = argparse.ArgumentParser()
parser.add_argument("--csv5m", required=True)
parser.add_argument("--symbol", required=True)
args = parser.parse_args()
p = s.Params()
candles = s._clean_candles(load_csv(args.csv5m))
index = {c.timestamp: i for i, c in enumerate(candles)}
trs = s._true_ranges(candles)
dates = sorted({(datetime.fromtimestamp(c.timestamp, s.UTC) + s.NY_OFFSET).date() for c in candles})
counts = Counter()
for day in dates:
    a0, a1 = s._ny_to_utc_ts(day - s.timedelta(days=1), 19), s._ny_to_utc_ts(day, 0)
    l0, l1 = s._ny_to_utc_ts(day, 2), s._ny_to_utc_ts(day, 8)
    o0, o1 = s._ny_to_utc_ts(day, 9, 30), s._ny_to_utc_ts(day, 9, 45)
    end = s._ny_to_utc_ts(day, 16)
    asian, london = s._window(index, candles, a0, a1), s._window(index, candles, l0, l1)
    orb, window = s._window(index, candles, o0, o1), s._window(index, candles, o1, end)
    missing = []
    for label, value in (("asian", asian), ("london", london), ("orb", orb), ("trade", window)):
        if value is None:
            counts[f"missing_{label}"] += 1
            missing.append(label)
    if missing:
        counts["incomplete_day"] += 1
        continue
    counts["complete_day"] += 1
    atr = s._atr_at(trs, london[-1][0], p.atr_period)
    direction, low, high, _ = s._session_direction(asian, london, atr, p) if atr else (None, False, False, 0)
    counts[f"sweep_{int(low)}{int(high)}"] += 1
    if direction is None:
        counts["no_bias"] += 1
        continue
    counts[f"bias_{direction}"] += 1
    oh, ol = max(c.high for _, c in orb), min(c.low for _, c in orb)
    if any(s._displacement(candles, idx, direction, oh, ol, s._atr_at(trs, idx, p.atr_period) or 0, p) for idx, _ in window):
        counts["displacement_day"] += 1
print(args.symbol, "bars", len(candles), "dates", len(dates), counts)
print("trades", len(s.generate_trades(candles, args.symbol, p)))