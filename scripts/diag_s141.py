#!/usr/bin/env python3
"""Funnel diagnostics for strategy 141: which gate removes how many setups."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "strategies"))

from core import load_csv  # noqa: E402
import strategy_141_mtf_zone_reclaim as s  # noqa: E402

SERIES = [
    ("EURUSD", "4h", ROOT / "data/EURUSD/4h/EURUSD_4h.csv"),
    ("GBPUSD", "1h", ROOT / "data/GBPUSD/1h/GBPUSD_1h.csv"),
    ("XAUUSD", "1h", ROOT / "data/XAUUSD/1H/XAUUSD_1h_2021-03-02_2026-07-01.csv"),
]

VARIANTS = [
    ("defaults", {}),
    ("pivot_w=1", {"pivot_w": 1}),
    ("no_imbalance", {"require_imbalance": 0}),
    ("disp=0.5", {"disp_atr": 0.5}),
    ("slope=0.05", {"htf_slope_min": 0.05}),
    ("age=150", {"zone_max_age_mid": 150}),
    ("body=0.35", {"conf_body": 0.35}),
    ("touch=4", {"max_touches": 4}),
    ("wide", {"pivot_w": 1, "require_imbalance": 0, "disp_atr": 0.5,
              "htf_slope_min": 0.05, "zone_max_age_mid": 150,
              "conf_body": 0.35, "max_touches": 4, "conf_max_bars": 24}),
]


def main():
    for sym, tf, path in SERIES:
        candles = load_csv(str(path))
        print(f"\n--- {sym} {tf} ({len(candles):,} bars) ---")
        print(f"{'variant':14s} {'zones':>6} {'taps':>6} {'conf':>5} "
              f"{'rejRisk':>8} {'rejRR':>6} {'trades':>7}")
        for label, ov in VARIANTS:
            s.generate_trades(candles, symbol=sym, params=ov)
            d = s.LAST_DIAG
            print(f"{label:14s} {d['zones']:>6} {d['taps']:>6} {d['confirmed']:>5} "
                  f"{d['rej_risk']:>8} {d['rej_rr']:>6} {d['trades']:>7}")


if __name__ == "__main__":
    main()
