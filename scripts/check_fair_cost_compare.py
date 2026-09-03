#!/usr/bin/env python3
"""Re-score remote vs local trades with identical 1x price-based cost path."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

os.environ["BT_COST_PRICE"] = "0.45"

from batch_xauusd_backtest import compute_stats  # noqa: E402
from core import enrich_trades_pnl, framework_pips_to_exness  # noqa: E402

BASE = ROOT / "dashboard" / "out" / "compare_remote_live"


def load_trades(path: Path) -> list:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw.get("trades") or []
    return raw


def fair_enrich(trades: list) -> list:
    cleaned = []
    for t in trades:
        d = copy.deepcopy(t)
        d.pop("pnl_R", None)  # force entry/exit price path (1x RT cost)
        cleaned.append(d)
    return enrich_trades_pnl(cleaned)


def show(label: str, path: Path) -> None:
    raw = load_trades(path)
    as_run = enrich_trades_pnl([copy.deepcopy(t) for t in raw])
    fair = fair_enrich(raw)
    for name, trades in (("as_run(pnl_R 1.5x cost)", as_run), ("fair(price 1.0x cost)", fair)):
        st = compute_stats(trades)
        fw = float(st["total_pnl_pips"] or 0)
        print(
            f"{label:10s} {name:26s} n={st['total_trades']:4d} "
            f"WR={st['win_rate']:>6} PF={st['profit_factor']} "
            f"exness={framework_pips_to_exness(fw):10.1f} R={st['total_pnl_R']}"
        )


def main():
    print(f"BT_COST_PRICE={os.environ['BT_COST_PRICE']}")
    show("s96 full", BASE / "s96_remote_full" / "results.json")
    show("s97 full", BASE / "s97_full" / "results.json")
    show("s97 365d", BASE / "s97_365d" / "results.json")
    show("s98 full", BASE / "batch_jobs" / "s98_full" / "results.json")
    show("s98 365d", BASE / "batch_jobs" / "s98_365d" / "results.json")


if __name__ == "__main__":
    main()
