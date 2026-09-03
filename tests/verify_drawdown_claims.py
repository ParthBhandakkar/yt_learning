#!/usr/bin/env python3
"""
Independent verification of drawdown_analysis_verified.md claims about s98/s97.

1) Reproduce real s98 (ATR trail) and s97 (Z=2.5) gold backtests with fair Exness costs.
2) Race their fixed-target reframe (100p/387p, 200p/507p) on XAUUSD 1m first-touch.
3) Capital simulation on the *actual* s98 R-stream at 1/2/10/15% risk.

Outputs JSON + prints tables to stdout.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("BT_COST_PRICE", None)
os.environ.setdefault(
    "YT_DATA_ROOT",
    r"O:\D temp\UltimateTradeBot\Data\Exness\structured\history",
)

from batch_xauusd_backtest import compute_stats  # noqa: E402
from core import (  # noqa: E402
    EXNESS_XAUUSD_PIP,
    enrich_trades_pnl,
    infer_pip_size,
    load_csv,
)
from data_library import data_root, find_instrument_csv  # noqa: E402
import strategy_97_trend_meanreversion as s97  # noqa: E402
from strategy_98_xau_trend_liquidity_trail import generate_trades as gen_s98  # noqa: E402

OUT = ROOT / "dashboard" / "out" / "verify_drawdown_claims"
START_EQUITY = 10_000.0
PIP = EXNESS_XAUUSD_PIP  # 0.01


def fair_enrich(trades: list, symbol: str) -> list:
    out = []
    for t in trades:
        d = copy.deepcopy(t)
        d.pop("pnl_R", None)
        d["symbol"] = symbol
        out.append(d)
    return enrich_trades_pnl(out)


def trade_R_list(trades: list) -> list[float]:
    rs = []
    for t in trades:
        if t.get("outcome") not in ("win", "loss", "breakeven"):
            continue
        entry = t.get("entry_price")
        stop = t.get("stop_loss")
        pnl = t.get("pnl_pips")
        if entry is None or stop is None or pnl is None:
            continue
        pip = infer_pip_size(float(entry), symbol=t.get("symbol"))
        risk = abs(float(entry) - float(stop)) / pip
        if risk <= 0:
            continue
        rs.append(float(pnl) / risk)
    return rs


def equity_metrics(r_list: list[float], risk_pct: float = 0.01) -> dict:
    if not r_list:
        return {
            "total_R": 0.0,
            "avg_R": 0.0,
            "max_DD_R": 0.0,
            "return_pct": 0.0,
            "max_DD_pct": 0.0,
            "final_equity": START_EQUITY,
        }
    eq_r = peak_r = max_dd_r = 0.0
    equity = peak_eq = START_EQUITY
    max_dd_pct = 0.0
    risk_cash = START_EQUITY * risk_pct
    for r in r_list:
        eq_r += r
        peak_r = max(peak_r, eq_r)
        max_dd_r = max(max_dd_r, peak_r - eq_r)
        equity += r * risk_cash
        peak_eq = max(peak_eq, equity)
        if peak_eq > 0:
            max_dd_pct = max(max_dd_pct, (peak_eq - equity) / peak_eq * 100.0)
    return {
        "total_R": round(eq_r, 3),
        "avg_R": round(eq_r / len(r_list), 4),
        "max_DD_R": round(max_dd_r, 3),
        "return_pct": round((equity - START_EQUITY) / START_EQUITY * 100.0, 2),
        "max_DD_pct": round(max_dd_pct, 2),
        "final_equity": round(equity, 2),
    }


def summarize(trades: list, symbol: str, label: str, window: str = "full") -> dict:
    st = compute_stats(trades)
    rs = trade_R_list(trades)
    em = equity_metrics(rs, 0.01)
    return {
        "label": label,
        "window": window,
        "symbol": symbol,
        "total_trades": len(trades),
        "win_rate": st.get("win_rate"),
        "profit_factor": st.get("profit_factor"),
        "total_pnl_pips": st.get("total_pnl_pips"),
        **em,
    }


def filter_window(trades: list, days: int) -> list:
    if not trades:
        return []
    times = []
    for t in trades:
        et = t.get("entry_time")
        if not et:
            continue
        times.append(datetime.fromisoformat(str(et).replace("Z", "+00:00")))
    if not times:
        return trades
    end = max(times)
    cut = end.timestamp() - days * 86400
    out = []
    for t in trades:
        et = t.get("entry_time")
        if not et:
            continue
        ts = datetime.fromisoformat(str(et).replace("Z", "+00:00")).timestamp()
        if ts >= cut:
            out.append(t)
    return out


def reproduce_real_strategies() -> dict:
    print("\n=== 1) Reproduce real s98 / s97 on XAUUSD ===")
    path_1h = find_instrument_csv(data_root(), "XAUUSD", "1h")
    path_4h = find_instrument_csv(data_root(), "XAUUSD", "4h")
    assert path_1h, "missing XAUUSD 1h"
    assert path_4h, "missing XAUUSD 4h"

    candles = load_csv(str(path_1h))
    raw98 = gen_s98(
        candles,
        also_breakout=True,
        session_filter=False,
        atr_mult_trail=3.0,
        atr_mult_init=1.5,
    )
    t98 = fair_enrich(raw98, "XAUUSD")
    row98_full = summarize(t98, "XAUUSD", "s98_atr_trail", "full")
    row98_1y = summarize(filter_window(t98, 365), "XAUUSD", "s98_atr_trail", "1y")
    print("s98 full:", json.dumps(row98_full, indent=2))
    print("s98 1y  :", json.dumps(row98_1y, indent=2))

    old_z = s97.Z_ENTRY
    s97.Z_ENTRY = 2.5
    try:
        raw97 = s97.run_strategy(
            s97._df_from_csv(str(path_4h)),
            str(OUT / "s97_z2.5_XAUUSD_raw.json"),
            symbol="XAUUSD",
        )
    finally:
        s97.Z_ENTRY = old_z
    t97 = fair_enrich(raw97, "XAUUSD")
    row97_full = summarize(t97, "XAUUSD", "s97_z2.5_actual_exits", "full")
    row97_1y = summarize(filter_window(t97, 365), "XAUUSD", "s97_z2.5_actual_exits", "1y")
    print("s97 full:", json.dumps(row97_full, indent=2))
    print("s97 1y  :", json.dumps(row97_1y, indent=2))

    # Persist for later race / risk sim
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "s98_XAUUSD_trades.json").write_text(
        json.dumps(t98, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "s97_XAUUSD_trades.json").write_text(
        json.dumps(t97, indent=2, default=str), encoding="utf-8"
    )
    return {
        "s98_full": row98_full,
        "s98_1y": row98_1y,
        "s97_full": row97_full,
        "s97_1y": row97_1y,
        "s98_trades": t98,
        "s97_trades": t97,
        "r_stream_s98": trade_R_list(t98),
    }


def _load_1m_arrays(path: Path):
    """Load 1m OHLCV as numpy arrays keyed by unix timestamp (bar open)."""
    candles = load_csv(str(path))
    ts = np.array([bar.timestamp for bar in candles], dtype=np.int64)
    o = np.array([bar.open for bar in candles], dtype=np.float64)
    h = np.array([bar.high for bar in candles], dtype=np.float64)
    l = np.array([bar.low for bar in candles], dtype=np.float64)
    cl = np.array([bar.close for bar in candles], dtype=np.float64)
    return ts, o, h, l, cl


def race_first_touch(
    trades: list,
    ts1m: np.ndarray,
    h1m: np.ndarray,
    l1m: np.ndarray,
    target_pips: float,
    stop_pips: float,
    max_bars: int = 10_000,
) -> dict:
    """
    First-touch race: target vs stop from each trade's entry.
    Uses Exness gold pips (0.01). If a 1m bar straddles both, stop-first (conservative).
    """
    target_dist = target_pips * PIP
    stop_dist = stop_pips * PIP
    wins = losses = ambiguous = skipped = 0
    outcomes = []  # +1 win, -1 loss

    for t in trades:
        entry = float(t["entry_price"])
        direction = t["direction"]
        et = t.get("entry_time")
        if not et:
            skipped += 1
            continue
        entry_ts = int(datetime.fromisoformat(str(et).replace("Z", "+00:00")).timestamp())
        # Find first 1m bar at/after entry
        i0 = int(np.searchsorted(ts1m, entry_ts, side="left"))
        if i0 >= len(ts1m):
            skipped += 1
            continue

        if direction == "long":
            tgt = entry + target_dist
            stp = entry - stop_dist
        else:
            tgt = entry - target_dist
            stp = entry + stop_dist

        hit = None
        for j in range(i0, min(i0 + max_bars, len(ts1m))):
            hi, lo = float(h1m[j]), float(l1m[j])
            if direction == "long":
                hit_stop = lo <= stp
                hit_tgt = hi >= tgt
            else:
                hit_stop = hi >= stp
                hit_tgt = lo <= tgt
            if hit_stop and hit_tgt:
                # Ambiguous intrabar — count as stop-first (conservative)
                hit = "loss"
                ambiguous += 1
                break
            if hit_stop:
                hit = "loss"
                break
            if hit_tgt:
                hit = "win"
                break
        if hit is None:
            skipped += 1
            continue
        if hit == "win":
            wins += 1
            outcomes.append(1)
        else:
            losses += 1
            outcomes.append(-1)

    n = wins + losses
    hit_rate = (wins / n * 100.0) if n else 0.0
    # Gross expectancy in R where 1R = stop distance, win pays target/stop R
    rr = target_pips / stop_pips
    gross_exp_R = (hit_rate / 100.0) * rr + (1.0 - hit_rate / 100.0) * (-1.0) if n else 0.0

    # Net expectancy for various round-turn costs (Exness pips)
    cost_rows = []
    for cost_pips in (3.0, 10.0, 30.0, 40.0):
        # cost in R units = cost_pips / stop_pips
        cost_R = cost_pips / stop_pips
        net_exp = gross_exp_R - cost_R
        # Break-even cost: solve gross_exp_R - cost/stop = 0 => cost = gross_exp_R * stop
        be_cost = gross_exp_R * stop_pips
        cost_rows.append({
            "typ_cost_exness_pips": cost_pips,
            "cost_R": round(cost_R, 4),
            "net_expectancy_R": round(net_exp, 4),
            "verdict": "positive" if net_exp > 0 else "negative",
        })

    return {
        "target_pips": target_pips,
        "stop_pips": stop_pips,
        "reward_risk": round(rr, 4),
        "n": n,
        "wins": wins,
        "losses": losses,
        "ambiguous_stop_first": ambiguous,
        "skipped": skipped,
        "hit_rate_pct": round(hit_rate, 2),
        "gross_expectancy_R": round(gross_exp_R, 4),
        "break_even_cost_exness_pips": round(gross_exp_R * stop_pips, 2),
        "cost_sensitivity": cost_rows,
    }


def run_fixed_target_race(trades: list) -> dict:
    print("\n=== 2) Fixed-target race on XAUUSD 1m (their reframe) ===")
    path_1m = find_instrument_csv(data_root(), "XAUUSD", "1m")
    assert path_1m, "missing XAUUSD 1m"
    print(f"Loading 1m: {path_1m}")
    ts, _o, h, l, _c = _load_1m_arrays(Path(path_1m))
    print(f"1m bars: {len(ts)}")

    # Their claimed stop floors from the doc
    setups = [
        ("s98_100p", 100.0, 387.0),
        ("s98_200p", 200.0, 507.0),
    ]
    results = {}
    for name, tgt, stp in setups:
        print(f"Racing {name}: target={tgt}p stop={stp}p ...")
        results[name] = race_first_touch(trades, ts, h, l, tgt, stp)
        print(json.dumps(results[name], indent=2))
    return results


def capital_sim(r_list: list[float], risk_pct: float, mode: str = "compound") -> dict:
    """mode: compound = risk% of current equity; flat = risk% of initial."""
    equity = START_EQUITY
    peak = START_EQUITY
    max_dd = 0.0
    flat_risk = START_EQUITY * risk_pct
    blown_at = None
    for i, r in enumerate(r_list):
        if mode == "compound":
            equity *= 1.0 + risk_pct * r
        else:
            equity += flat_risk * r
        if equity <= 0:
            equity = 0.0
            blown_at = i + 1
            break
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return {
        "mode": mode,
        "risk_pct": risk_pct * 100.0,
        "final_equity": round(equity, 2),
        "max_DD_pct": round(max_dd, 2),
        "return_pct": round((equity - START_EQUITY) / START_EQUITY * 100.0, 2),
        "blown_at_trade": blown_at,
    }


def run_risk_sim(r_stream: list[float]) -> dict:
    print("\n=== 3) Risk sizing on actual s98 R-stream ===")
    print(f"R-stream length: {len(r_stream)}, sumR={sum(r_stream):.2f}")
    rows = []
    for pct in (0.01, 0.02, 0.10, 0.15):
        for mode in ("compound", "flat"):
            row = capital_sim(r_stream, pct, mode)
            rows.append(row)
            print(
                f"  risk={pct*100:.0f}% {mode:9s} -> final=${row['final_equity']:,.0f} "
                f"DD={row['max_DD_pct']:.1f}% blown={row['blown_at_trade']}"
            )
    # Longest losing streak in R units (consecutive negative R)
    streak = max_streak = 0
    for r in r_stream:
        if r < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "n_trades": len(r_stream),
        "sum_R": round(sum(r_stream), 3),
        "longest_losing_streak": max_streak,
        "simulations": rows,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"YT_DATA_ROOT={os.environ.get('YT_DATA_ROOT')}")
    print(f"OUT={OUT}")

    real = reproduce_real_strategies()
    race = run_fixed_target_race(real["s98_trades"])
    risk = run_risk_sim(real["r_stream_s98"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "committed_compare_reference": {
            "s98_XAUUSD_full": {
                "trades": 982,
                "PF": 1.51,
                "total_R": 164.052,
                "win_rate": 39.61,
                "max_DD_pct": 19.06,
            },
            "note": "From dashboard/out/full_pair_compare_s96_s97_s98/summary.csv",
        },
        "reproduction": {
            "s98_full": real["s98_full"],
            "s98_1y": real["s98_1y"],
            "s97_full": real["s97_full"],
            "s97_1y": real["s97_1y"],
        },
        "fixed_target_race": race,
        "risk_sim_actual_s98": risk,
        "verdict_hints": {
            "real_s98_profitable_after_costs": real["s98_full"]["total_R"] > 0
            and (real["s98_full"].get("profit_factor") or 0) > 1,
            "doc_reframe_is_not_strategy_spec": True,
            "s97_gold_actual_exits_negative_or_weak": real["s97_full"]["total_R"] < 0,
        },
    }
    out_path = OUT / "verification_summary.json"
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return payload


if __name__ == "__main__":
    main()
