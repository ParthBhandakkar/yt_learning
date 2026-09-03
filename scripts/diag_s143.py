#!/usr/bin/env python3
"""Causal signal-funnel diagnostics for Strategy 143."""
import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "strategies")]
from core import load_csv
import strategy_143_mtf_supply_demand_reversal as s

parser = argparse.ArgumentParser()
parser.add_argument("--csv4h", required=True)
parser.add_argument("--csv5m", required=True)
parser.add_argument("--symbol", required=True)
args = parser.parse_args()

p = s.Params()
h4 = s._clean_candles(load_csv(args.csv4h), "4H")
m5 = s._clean_candles(load_csv(args.csv5m), "5m")
h4, m5 = s._common_overlap(h4, m5)
h1 = s._complete_resample(m5, 60)
m15 = s._complete_resample(m5, 15)
zones = s._build_zones(h4, p)
rejected = s._resolve_first_returns(zones, h1, p)
m15_times = s.np.fromiter((c.timestamp + s.M15_SECONDS for c in m15), dtype=s.np.int64)
m5_times = s.np.fromiter((c.timestamp + s.M5_SECONDS for c in m5), dtype=s.np.int64)
atr15, atr5 = s._atr(m15, p.atr_period), s._atr(m5, p.atr_period)
partials = []
for rejection in rejected:
    signal = s._find_m15_signal(rejection, m15, m15_times, atr15, p)
    if signal is not None:
        partials.append((rejection, signal))
candidates = [s._find_candidate(x, m5, m5_times, atr5, zones, args.symbol, p) for x in partials]
trades = s.generate_trades(h4, m5, symbol=args.symbol, params=p)
print({"symbol": args.symbol, "h4": len(h4), "h1": len(h1), "m15": len(m15), "m5": len(m5),
       "zones": len(zones), "zone_directions": Counter(z.direction for z in zones),
       "first_returns": Counter(z.end_reason for z in zones), "rejections": len(rejected),
       "m15_signals": len(partials), "admitted_candidates": sum(x is not None for x in candidates),
       "trades": len(trades)})

# Candidate admission and final trade counts above use the strategy's current
# implementation directly, avoiding duplicated diagnostic logic.