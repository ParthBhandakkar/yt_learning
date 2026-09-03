#!/usr/bin/env python3
"""Development-only breakdown for s142; never reads the sealed bucket."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "strategies"), str(ROOT / "scripts")]
from validate_s142 import run, metrics

PARAMS = {}  # Strategy defaults were frozen before the sealed test.
r = run(PARAMS)
rows = [t for b in ("train", "validation") for ts in r[b].values() for t in ts]
for key, groups in [
    ("direction", ["long", "short"]),
    ("setup", sorted({t["setup"] for t in rows})),
]:
    print("\n", key)
    for g in groups:
        q = metrics([t for t in rows if t[key] == g])
        print(g, q)
for b in ("train", "validation"):
    print("\n", b, "direction")
    pool = [t for ts in r[b].values() for t in ts]
    for d in ("long", "short"):
        print(d, metrics([t for t in pool if t["direction"] == d]))
