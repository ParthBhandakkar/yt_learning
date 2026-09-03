#!/usr/bin/env python3
"""Development-only structural breakdown for s143; sealed data is not generated."""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "strategies"), str(ROOT / "scripts")]
from validate_s143 import UNIVERSE, metrics, run
from strategy_143_mtf_supply_demand_reversal import Params

result = run(Params(), include_sealed=False)
for bucket in ("train", "validation"):
    rows = [trade for symbol, *_ in UNIVERSE for trade in result[bucket][symbol]]
    print(f"\n{bucket.upper()} pooled", metrics(rows))
    for direction in ("long", "short"):
        selected = [trade for trade in rows if trade["direction"] == direction]
        print(direction, metrics(selected), "exits", Counter(t["exit_reason"] for t in selected))
    print("all exits", Counter(t["exit_reason"] for t in rows))