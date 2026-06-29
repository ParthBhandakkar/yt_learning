#!/usr/bin/env python3
"""
Batch backtest runner. Runs every strategy on XAUUSD library data, applies the
cost-aware PnL enrichment, and prints risk-normalized metrics for ranking.

Usage:
  python bt_runner.py                 # run all strategies
  python bt_runner.py s44 s78         # run a subset by id
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import enrich_trades_pnl
from bt_metrics import compute_metrics

DATA = HERE / "data" / "XAUUSD"
F1m = str(DATA / "1m" / "XAUUSD_1m_2021-07-02_2026-04-22.csv")
F5m = str(DATA / "5m" / "XAUUSD_5m_2021-07-02_2026-04-22.csv")
F15m = str(DATA / "15m" / "XAUUSD_15m_2021-07-02_2026-04-22.csv")
F1h = str(DATA / "1h" / "XAUUSD_1h_2021-03-02_2026-04-22.csv")
F4h = str(DATA / "4h" / "XAUUSD_4h_2021-03-02_2026-04-22.csv")
F1d = str(DATA / "1d" / "XAUUSD_1d_2016-11-22_2026-04-22.csv")

# strategy id -> (script, [(arg, path), ...], note)
STRATS = {
    "s01": ("strategy_01_orderflow_volume_profile.py", [("--csv", F5m)], ""),
    "s04": ("strategy_04_gold_frvp.py", [("--csv", F5m)], "gold native"),
    "s05": ("strategy_05_macro_vp_ict.py", [("--csv", F1m)], ""),
    "s06": ("strategy_06_fractal_inversion.py", [("--csv1h", F1h), ("--csv5m", F5m), ("--csv1m", F1m)], ""),
    "s08": ("strategy_08_vp_auction_breakout.py", [("--csv", F5m)], ""),
    "s13": ("strategy_13_3step_ict_gold.py",
            [("--csv_gold", F1h), ("--csv_silver", F1h), ("--csv_gold_5m", F5m), ("--csv_silver_5m", F5m)],
            "INVALID: no silver data; SMT uses gold-vs-gold"),
    "s17": ("strategy_17_1h_pattern_1h_1m.py", [("--csv1h", F1h), ("--csv5m", F5m), ("--csv1m", F1m)], ""),
    "s28": ("strategy_28_multitf_ifvg.py", [("--csv1h", F1h), ("--csv1m", F1m)], ""),
    "s29": ("strategy_29_tbv_core.py", [("--csv", F5m)], ""),
    "s31": ("strategy_31_10am_po3.py", [("--csv4h", F4h), ("--csv15m", F15m), ("--csv1m", F1m)], ""),
    "s39": ("strategy_39_mechanical_2day_bias.py", [("--csv_daily", F1d), ("--csv_15m", F15m)], "gold native"),
    "s42": ("strategy_42_8am_onecandle.py", [("--csv1h", F1h), ("--csv1m", F1m)], ""),
    "s44": ("strategy_44_lazy_liquidity_orb.py", [("--csv", F15m)], ""),
    "s52": ("strategy_52_draw_on_liquidity.py", [("--csv15m", F15m), ("--csv1m", F1m)], ""),
    "s54": ("strategy_54_liquidity_range.py", [("--csv", F5m)], ""),
    "s56": ("strategy_56_midas_scalping.py", [("--csv15m", F15m), ("--csv1m", F1m)], "gold native"),
    "s62": ("strategy_62_trend_continuation_purge.py", [("--csv1h", F1h), ("--csv5m", F5m)], ""),
    "s65": ("strategy_65_us30_judas_swing.py", [("--csv15m", F15m), ("--csv1m", F1m)], "intended US30; run on gold"),
    "s69": ("strategy_69_ict_market_maker.py", [("--csv4h", F4h), ("--csv15m", F15m)], ""),
    "s77": ("strategy_77_simple_ict_liquidity.py", [("--csv4h", F4h), ("--csv5m", F5m)], ""),
    "s78": ("strategy_78_easy_ict_judas_swing.py", [("--csv15m", F15m), ("--csv5m", F5m)], ""),
    "s81": ("strategy_81_ict_po3_1m_scalping.py", [("--csv1h", F1h), ("--csv1m", F1m)], ""),
}

OUT = HERE / "bt_results"
OUT.mkdir(exist_ok=True)


def run_one(sid: str) -> dict:
    script, args, note = STRATS[sid]
    out_json = OUT / f"{sid}_trades.json"
    cmd = [sys.executable, str(HERE / script)]
    for a, p in args:
        cmd += [a, p]
    cmd += ["--output", str(out_json)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return {"id": sid, "script": script, "note": note, "error": "timeout(3600s)", "secs": 3600}
    secs = round(time.time() - t0, 1)
    if r.returncode != 0:
        return {"id": sid, "script": script, "note": note,
                "error": (r.stderr or r.stdout)[-400:].strip(), "secs": secs}
    if not out_json.exists():
        return {"id": sid, "script": script, "note": note, "error": "no output", "secs": secs}
    trades = json.load(open(out_json))
    if not isinstance(trades, list):
        trades = [trades]
    enrich_trades_pnl(trades)
    json.dump(trades, open(out_json, "w"), indent=1, default=str)
    m = compute_metrics(trades)
    m.update({"id": sid, "script": script, "note": note, "secs": secs})
    return m


def main():
    ids = [a for a in sys.argv[1:] if a in STRATS]
    if not ids:
        ids = list(STRATS.keys())
    results = []
    for sid in ids:
        print(f"[run] {sid} {STRATS[sid][0]} ...", flush=True)
        m = run_one(sid)
        results.append(m)
        if "error" in m:
            print(f"   ERROR: {m['error']}", flush=True)
        else:
            print(f"   trades={m['closed_trades']:>5} win%={m['win_rate']:>5} "
                  f"expR={m['expectancy_R']:>7} totalR={m['total_R']:>8} "
                  f"PF={m['profit_factor']} maxDD_R={m['max_drawdown_R']} ({m['secs']}s)", flush=True)
    json.dump(results, open(OUT / "summary.json", "w"), indent=1, default=str)
    print("\n=== SUMMARY (sorted by total_R) ===", flush=True)
    ok = [r for r in results if "error" not in r]
    err = [r for r in results if "error" in r]
    ok.sort(key=lambda r: (r.get("total_R") or -9e9), reverse=True)
    hdr = f"{'id':>4} {'trades':>6} {'win%':>5} {'expR':>7} {'totalR':>8} {'PF':>6} {'ddR':>7} {'note'}"
    print(hdr)
    for r in ok:
        pf = r['profit_factor']
        print(f"{r['id']:>4} {r['closed_trades']:>6} {r['win_rate']:>5} {r['expectancy_R']:>7} "
              f"{r['total_R']:>8} {str(pf):>6} {r['max_drawdown_R']:>7} {r['note']}")
    for r in err:
        print(f"{r['id']:>4}  ERROR: {r['error'][:80]}  {r['note']}")


if __name__ == "__main__":
    main()
