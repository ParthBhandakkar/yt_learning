#!/usr/bin/env python3
"""
Audit our pip/cost model against Exness contract conventions per symbol.

Exness references (Help Center / contract specs):
  - Most FX: pip = 0.0001, contract = 100_000, ~$10/pip per 1.0 lot (XXXUSD)
  - JPY pairs: pip = 0.01, contract = 100_000, pip$ = 1000/USDJPY per lot
  - XAUUSD: pip = 0.01, contract = 100 oz, $1/pip per 1.0 lot
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Force class defaults (no global BT_COST_PRICE)
os.environ.pop("BT_COST_PRICE", None)

from core import (  # noqa: E402
    EXNESS_INSTRUMENT_SPECS,
    EXNESS_XAUUSD_PIP,
    infer_pip_size,
    round_turn_cost_price,
    round_turn_cost_pips,
)

# Representative mid prices for the heuristic path
REF_PX = {
    "EURUSD": 1.0850,
    "GBPUSD": 1.2700,
    "AUDUSD": 0.6600,
    "NZDUSD": 0.6100,
    "USDCAD": 1.3600,
    "USDCHF": 0.8800,
    "USDJPY": 150.00,
    "XAUUSD": 2350.00,
}

# Broker truth (Exness-style retail CFD)
EXNESS_TRUTH = {
    "EURUSD": {"pip": 0.0001, "contract": 100_000, "pip_value_1lot_usd": 10.0},
    "GBPUSD": {"pip": 0.0001, "contract": 100_000, "pip_value_1lot_usd": 10.0},
    "AUDUSD": {"pip": 0.0001, "contract": 100_000, "pip_value_1lot_usd": 10.0},
    "NZDUSD": {"pip": 0.0001, "contract": 100_000, "pip_value_1lot_usd": 10.0},
    "USDCAD": {"pip": 0.0001, "contract": 100_000, "pip_value_1lot_usd": "~10/USDCAD"},
    "USDCHF": {"pip": 0.0001, "contract": 100_000, "pip_value_1lot_usd": "~10/USDCHF"},
    "USDJPY": {"pip": 0.01, "contract": 100_000, "pip_value_1lot_usd": round(1000 / 150, 2)},
    "XAUUSD": {"pip": 0.01, "contract": 100, "pip_value_1lot_usd": 1.0},
}


def main():
    print("Exness-vs-framework audit (price-heuristic + symbol-aware)")
    print("=" * 100)
    hdr = (
        f"{'pair':8s} {'px':>8s} {'heur_pip':>10s} {'sym_pip':>10s} "
        f"{'exn_pip':>10s} {'pipOK':>6s} {'RT_price':>10s} {'RT_exnPips':>10s} "
        f"{'lot':>8s} {'$ /exnPip':>10s}"
    )
    print(hdr)
    print("-" * 100)
    issues = []
    for sym, px in REF_PX.items():
        truth = EXNESS_TRUTH[sym]
        heur = infer_pip_size(px)  # price only
        sym_pip = infer_pip_size(px, symbol=sym)
        cost = round_turn_cost_price(px, symbol=sym)
        exn_pip = truth["pip"]
        pip_ok = abs(sym_pip - exn_pip) < 1e-12 or (
            sym == "XAUUSD" and abs(sym_pip - 1.0) < 1e-12  # framework metal unit
        )
        # For display: Exness RT pips using broker pip
        rt_exn = cost / exn_pip
        spec = EXNESS_INSTRUMENT_SPECS.get(sym, {})
        print(
            f"{sym:8s} {px:8.4f} {heur:10.6f} {sym_pip:10.6f} {exn_pip:10.6f} "
            f"{'YES' if pip_ok else 'NO':>6s} {cost:10.5f} {rt_exn:10.2f} "
            f"{spec.get('contract', '?'):>8} {str(truth['pip_value_1lot_usd']):>10}"
        )
        if abs(heur - exn_pip) > 1e-12 and sym != "XAUUSD":
            issues.append(f"{sym}: price-heuristic pip {heur} != Exness {exn_pip}")
        if sym == "XAUUSD" and abs(heur - 1.0) > 1e-12:
            issues.append("XAUUSD: expected framework pip 1.0")
        if abs(sym_pip - exn_pip) > 1e-12 and sym != "XAUUSD":
            issues.append(f"{sym}: symbol-aware pip {sym_pip} != Exness {exn_pip}")
        if sym == "XAUUSD" and abs(EXNESS_XAUUSD_PIP - 0.01) > 1e-12:
            issues.append("EXNESS_XAUUSD_PIP should be 0.01")

    print("=" * 100)
    print("Notes:")
    print("  - Backtests size in R / price, not lots. Lot/$ pip-value is for live sizing only.")
    print("  - XAUUSD framework pip stays $1 for legacy stats; Exness pip is $0.01 (x100).")
    print("  - Do NOT set BT_COST_PRICE globally when mixing gold and FX (units differ).")
    if issues:
        print("\nISSUES:")
        for i in issues:
            print("  -", i)
        sys.exit(1)
    print("\nAll symbol-aware pip sizes match Exness (XAUUSD framework unit intentional).")
    sys.exit(0)


if __name__ == "__main__":
    main()
