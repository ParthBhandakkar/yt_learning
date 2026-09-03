#!/usr/bin/env python3
"""
Mega compare: new YouTuber/Instagram proxies (s106+) vs baselines s96/s97/s98/s99.

Scope (pragmatic):
  Pairs: GBPUSD, EURUSD, USDJPY, XAUUSD, BTCUSD
  Windows: 3m (90d) + 1y (365d) from each CSV max timestamp
  s96 (5m stack) only on XAUUSD to avoid 5m trim cost on FX
  All new strategies on 1H generators

Fair costs: strip pnl_R, symbol-aware enrich. No global BT_COST_PRICE.
Large trims -> D: and deleted after each run.
"""

from __future__ import annotations

import copy
import csv
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

os.environ.pop("BT_COST_PRICE", None)
os.environ.setdefault(
    "YT_DATA_ROOT",
    r"O:\D temp\UltimateTradeBot\Data\Exness\structured\history",
)

from batch_xauusd_backtest import compute_stats  # noqa: E402
from core import enrich_trades_pnl, exness_spec, infer_pip_size, load_csv  # noqa: E402
from data_library import (  # noqa: E402
    data_root,
    find_instrument_csv,
    prepare_library_csv_window,
    scan_csv_max_timestamp,
)
import strategy_96_mss_ob_tuned as s96  # noqa: E402
import strategy_97_trend_meanreversion as s97  # noqa: E402
from strategy_98_xau_trend_liquidity_trail import generate_trades as gen_s98  # noqa: E402
from strategy_99_pos_golden_setup import generate_trades as gen_s99  # noqa: E402
from strategy_106_shreya_frx_london_fvg import generate_trades as gen_s106  # noqa: E402
from strategy_107_shreya_frx_sweep_fvg import generate_trades as gen_s107  # noqa: E402
from strategy_110_tmt_trendline_bounce import generate_trades as gen_s110  # noqa: E402
from strategy_111_mamba_breakout_sr import generate_trades as gen_s111  # noqa: E402
from strategy_112_bb_morning_range import generate_trades as gen_s112  # noqa: E402
from strategy_113_bb_sniper_retest import generate_trades as gen_s113  # noqa: E402
from strategy_114_sl_pdh_sweep import generate_trades as gen_s114  # noqa: E402
from strategy_115_up_asia_london_sweep import generate_trades as gen_s115  # noqa: E402
from strategy_120_fabio_orb_ivb import generate_trades as gen_s120  # noqa: E402
from strategy_121_fabio_amt_meanrev import generate_trades as gen_s121  # noqa: E402
from strategy_122_tg_trident import generate_trades as gen_s122  # noqa: E402
from strategy_123_umar_break_hold import generate_trades as gen_s123  # noqa: E402
from strategy_124_brando_sr_breakout import generate_trades as gen_s124  # noqa: E402
from strategy_125_kane_lab_reversal import generate_trades as gen_s125  # noqa: E402
from strategy_126_kane_lab_continuation import generate_trades as gen_s126  # noqa: E402
from strategy_127_kane_po3_fifty import generate_trades as gen_s127  # noqa: E402
from strategy_130_jadecap_daily_sweep import generate_trades as gen_s130  # noqa: E402
from strategy_131_jadecap_session_fvg import generate_trades as gen_s131  # noqa: E402
from strategy_132_marco_liquidity_trap import generate_trades as gen_s132  # noqa: E402
from strategy_133_marco_sd_choch import generate_trades as gen_s133  # noqa: E402
from strategy_134_temiz_first_red_day import generate_trades as gen_s134  # noqa: E402
from strategy_135_temiz_level_lower_high import generate_trades as gen_s135  # noqa: E402
from strategy_136_cimi_orb import generate_trades as gen_s136  # noqa: E402
from strategy_137_cimi_sweep_reclaim import generate_trades as gen_s137  # noqa: E402
from strategy_138_silfrain_trend_pullback import generate_trades as gen_s138  # noqa: E402
from strategy_139_nbb_830_killzone import generate_trades as gen_s139  # noqa: E402

PAIRS = ["GBPUSD", "EURUSD", "USDJPY", "XAUUSD", "BTCUSD"]
WINDOWS = [
    (90, "3m"),
    (365, "1y"),
]
OUT = Path(os.environ.get("YT_MEGA_OUT", r"D:\temp\yt_learning_mega_compare"))
OUT_MIRROR = ROOT / "dashboard" / "out" / "compare_mega_traders"
START_EQUITY = 10_000.0
RISK_PCT = 0.01


def _cleanup_trims(job: Path) -> None:
    for p in job.glob("trim_*.csv"):
        try:
            p.unlink()
        except OSError:
            pass


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
        entry, stop, pnl = t.get("entry_price"), t.get("stop_loss"), t.get("pnl_pips")
        if entry is None or stop is None or pnl is None:
            continue
        pip = infer_pip_size(float(entry), symbol=t.get("symbol"))
        risk = abs(float(entry) - float(stop)) / pip
        if risk > 0:
            rs.append(float(pnl) / risk)
    return rs


def equity_metrics(r_list: list[float]) -> dict:
    if not r_list:
        return {
            "total_R": 0.0, "avg_R": 0.0, "max_DD_R": 0.0,
            "return_pct": 0.0, "max_DD_pct": 0.0, "final_equity": START_EQUITY,
        }
    eq_r = peak_r = max_dd_r = 0.0
    equity = peak_eq = START_EQUITY
    max_dd_pct = 0.0
    risk_cash = START_EQUITY * RISK_PCT
    for r in r_list:
        eq_r += r
        peak_r = max(peak_r, eq_r)
        max_dd_r = max(max_dd_r, peak_r - eq_r)
        equity += r * risk_cash
        peak_eq = max(peak_eq, equity)
        max_dd_pct = max(max_dd_pct, (peak_eq - equity) / peak_eq * 100.0 if peak_eq else 0.0)
    return {
        "total_R": round(eq_r, 3),
        "avg_R": round(eq_r / len(r_list), 4),
        "max_DD_R": round(max_dd_r, 3),
        "return_pct": round((equity / START_EQUITY - 1.0) * 100.0, 2),
        "max_DD_pct": round(max_dd_pct, 2),
        "final_equity": round(equity, 2),
    }


def summarize(trades: list, pair: str, strategy: str, window: str, max_days: int) -> dict:
    closed = [t for t in trades if t.get("outcome") in ("win", "loss", "breakeven")]
    stats = compute_stats(closed) if closed else {
        "total_trades": 0, "win_rate": 0, "profit_factor": 0,
    }
    rs = trade_R_list(closed)
    em = equity_metrics(rs)
    net_ex = 0.0
    dd_ex = 0.0
    if closed:
        eq = peak = 0.0
        for t in closed:
            pips_fw = float(t.get("pnl_pips") or 0)
            fw = infer_pip_size(float(t["entry_price"]), symbol=pair)
            spec = exness_spec(pair) or {}
            broker = float(spec.get("pip", fw))
            ex_move = pips_fw * (fw / broker) if broker else pips_fw
            net_ex += ex_move
            eq += ex_move
            peak = max(peak, eq)
            dd_ex = max(dd_ex, peak - eq)

    return {
        "strategy": strategy,
        "pair": pair,
        "window": window,
        "max_days": max_days,
        "status": "ok",
        "total_trades": int(stats.get("total_trades") or len(closed)),
        "win_rate": stats.get("win_rate", 0),
        "profit_factor": stats.get("profit_factor", 0),
        "total_R": em["total_R"],
        "avg_R": em["avg_R"],
        "max_DD_R": em["max_DD_R"],
        "return_pct": em["return_pct"],
        "max_DD_pct": em["max_DD_pct"],
        "final_equity": em["final_equity"],
        "net_exness_pips": round(net_ex, 1),
        "max_DD_exness_pips": round(dd_ex, 1),
    }


def _window_paths(pair: str, max_days: int, job: Path, tfs: list[str]) -> tuple[dict, list]:
    root = data_root()
    file_map = {}
    for tf in tfs:
        p = find_instrument_csv(root, pair, tf)
        if p is None:
            return {}, []
        key = f"--csv{tf}" if tf != "1h" else "--csv1h"
        if tf == "4h":
            key = "--csv4h"
        elif tf == "15m":
            key = "--csv15m"
        elif tf == "5m":
            key = "--csv5m"
        file_map[key] = str(p)
    job.mkdir(parents=True, exist_ok=True)
    if max_days and max_days > 0:
        return prepare_library_csv_window(file_map, max_days, job)
    return file_map, [f"full span for {pair}"]


def run_s96(pair: str, max_days: int, window: str, job: Path) -> dict:
    try:
        working, notes = _window_paths(pair, max_days, job, ["4h", "1h", "15m", "5m"])
        if not working:
            return {"strategy": "s96", "pair": pair, "window": window, "status": "missing_data"}
        out = job / "s96_trades.json"
        raw = s96.run_strategy(
            s96._df_from_csv(working["--csv4h"]),
            s96._df_from_csv(working["--csv1h"]),
            s96._df_from_csv(working["--csv15m"]),
            s96._df_from_csv(working["--csv5m"]),
            str(out),
            symbol=pair,
        )
        row = summarize(fair_enrich(raw, pair), pair, "s96", window, max_days)
        c = load_csv(working["--csv1h"])
        if c:
            row["data_start"] = datetime.fromtimestamp(c[0].timestamp, tz=timezone.utc).date().isoformat()
            row["data_end"] = datetime.fromtimestamp(c[-1].timestamp, tz=timezone.utc).date().isoformat()
        row["trim_notes"] = notes
        row["tf_used"] = "4h+1h+15m+5m"
        return row
    except Exception as exc:
        return {
            "strategy": "s96", "pair": pair, "window": window, "status": "error",
            "error": str(exc), "traceback": traceback.format_exc()[-800:],
        }
    finally:
        _cleanup_trims(job)


def run_s97(pair: str, max_days: int, window: str, job: Path, z: float = 2.5) -> dict:
    old = s97.Z_ENTRY
    s97.Z_ENTRY = z
    try:
        working, notes = _window_paths(pair, max_days, job, ["4h"])
        if not working:
            return {"strategy": f"s97_z{z}", "pair": pair, "window": window, "status": "missing_data"}
        out = job / "s97_trades.json"
        raw = s97.run_strategy(s97._df_from_csv(working["--csv4h"]), str(out), symbol=pair)
        row = summarize(fair_enrich(raw, pair), pair, f"s97_z{z}", window, max_days)
        c = load_csv(working["--csv4h"])
        if c:
            row["data_start"] = datetime.fromtimestamp(c[0].timestamp, tz=timezone.utc).date().isoformat()
            row["data_end"] = datetime.fromtimestamp(c[-1].timestamp, tz=timezone.utc).date().isoformat()
        row["trim_notes"] = notes
        row["tf_used"] = "4h"
        return row
    except Exception as exc:
        return {
            "strategy": f"s97_z{z}", "pair": pair, "window": window,
            "status": "error", "error": str(exc),
        }
    finally:
        s97.Z_ENTRY = old
        _cleanup_trims(job)


def _run_1h(gen, name: str, pair: str, max_days: int, window: str, job: Path, **kw) -> dict:
    try:
        working, notes = _window_paths(pair, max_days, job, ["1h"])
        if not working:
            return {"strategy": name, "pair": pair, "window": window, "status": "missing_data"}
        path = working.get("--csv1h") or next(iter(working.values()), None)
        if not path:
            return {"strategy": name, "pair": pair, "window": window, "status": "missing_data"}
        candles = load_csv(path)
        try:
            raw = gen(candles, symbol=pair, **kw)
        except TypeError:
            raw = gen(candles, **kw)
            for t in raw:
                t["symbol"] = pair
        (job / f"{name}_trades.json").write_text(
            json.dumps(raw, indent=2, default=str), encoding="utf-8"
        )
        row = summarize(fair_enrich(raw, pair), pair, name, window, max_days)
        if candles:
            row["data_start"] = datetime.fromtimestamp(
                candles[0].timestamp, tz=timezone.utc
            ).date().isoformat()
            row["data_end"] = datetime.fromtimestamp(
                candles[-1].timestamp, tz=timezone.utc
            ).date().isoformat()
        row["trim_notes"] = notes
        row["tf_used"] = "1h"
        return row
    except Exception as exc:
        return {
            "strategy": name, "pair": pair, "window": window,
            "status": "error", "error": str(exc),
            "traceback": traceback.format_exc()[-800:],
        }
    finally:
        _cleanup_trims(job)


def basket(rows: list[dict], strategy: str, window: str) -> dict:
    sub = [
        r for r in rows
        if r.get("strategy") == strategy and r.get("window") == window and r.get("status") == "ok"
    ]
    if not sub:
        return {"strategy": strategy, "window": window, "status": "empty"}
    pos = sum(1 for r in sub if float(r.get("total_R") or 0) > 0)
    total_r = sum(float(r.get("total_R") or 0) for r in sub)
    trades = sum(int(r.get("total_trades") or 0) for r in sub)
    dollar = sum(float(r.get("return_pct") or 0) / 100.0 * START_EQUITY for r in sub)
    worst_dd = max(float(r.get("max_DD_pct") or 0) for r in sub)
    wsum = sum(float(r.get("avg_R") or 0) * int(r.get("total_trades") or 0) for r in sub)
    return {
        "strategy": strategy,
        "window": window,
        "pairs": len(sub),
        "pairs_positive_R": pos,
        "pct_pairs_positive": round(100.0 * pos / len(sub), 1),
        "total_trades": trades,
        "total_R": round(total_r, 3),
        "avg_R_trade": round(wsum / trades, 4) if trades else 0.0,
        "basket_return_pct_parallel": round(dollar / START_EQUITY * 100.0, 2),
        "worst_pair_max_DD_pct": round(worst_dd, 2),
    }


def best_per_pair(rows: list[dict], window: str) -> list[dict]:
    out = []
    for pair in PAIRS:
        cands = [
            r for r in rows
            if r.get("pair") == pair and r.get("window") == window
            and r.get("status") == "ok" and int(r.get("total_trades") or 0) > 0
        ]
        if not cands:
            out.append({"window": window, "pair": pair, "winner": None})
            continue
        best = max(cands, key=lambda r: float(r.get("total_R") or -1e9))
        out.append({
            "window": window,
            "pair": pair,
            "winner": best["strategy"],
            "total_R": best["total_R"],
            "return_pct": best["return_pct"],
            "PF": best["profit_factor"],
            "trades": best["total_trades"],
            "max_DD_pct": best["max_DD_pct"],
        })
    return out


def fx_only_basket(rows: list[dict], strategy: str, window: str) -> dict:
    fx_pairs = {"GBPUSD", "EURUSD", "USDJPY"}
    sub = [
        r for r in rows
        if r.get("strategy") == strategy and r.get("window") == window
        and r.get("status") == "ok" and r.get("pair") in fx_pairs
    ]
    if not sub:
        return {"strategy": strategy, "window": window, "scope": "fx3", "status": "empty"}
    pos = sum(1 for r in sub if float(r.get("total_R") or 0) > 0)
    total_r = sum(float(r.get("total_R") or 0) for r in sub)
    trades = sum(int(r.get("total_trades") or 0) for r in sub)
    wsum = sum(float(r.get("avg_R") or 0) * int(r.get("total_trades") or 0) for r in sub)
    worst_dd = max(float(r.get("max_DD_pct") or 0) for r in sub)
    return {
        "strategy": strategy,
        "window": window,
        "scope": "fx3",
        "pairs": len(sub),
        "pairs_positive_R": pos,
        "total_R": round(total_r, 3),
        "avg_R_trade": round(wsum / trades, 4) if trades else 0.0,
        "worst_pair_max_DD_pct": round(worst_dd, 2),
    }


def build_runners() -> list[tuple[str, object]]:
    """Return (name, runner) where runner(pair, max_days, window, job) -> dict."""
    one_h = [
        ("s98", gen_s98, {"also_breakout": True, "session_filter": False}),
        ("s99", gen_s99, {}),
        ("s106", gen_s106, {}),
        ("s107", gen_s107, {}),
        ("s110", gen_s110, {}),
        ("s111", gen_s111, {}),
        ("s112", gen_s112, {}),
        ("s113", gen_s113, {}),
        ("s114", gen_s114, {}),
        ("s115", gen_s115, {}),
        ("s120", gen_s120, {}),
        ("s121", gen_s121, {}),
        ("s122", gen_s122, {}),
        ("s123", gen_s123, {}),
        ("s124", gen_s124, {}),
        ("s125", gen_s125, {}),
        ("s126", gen_s126, {}),
        ("s127", gen_s127, {}),
        ("s130", gen_s130, {}),
        ("s131", gen_s131, {}),
        ("s132", gen_s132, {}),
        ("s133", gen_s133, {}),
        ("s134", gen_s134, {}),
        ("s135", gen_s135, {}),
        ("s136", gen_s136, {}),
        ("s137", gen_s137, {}),
        ("s138", gen_s138, {}),
        ("s139", gen_s139, {}),
    ]
    runners: list[tuple[str, object]] = [
        ("s97_z2.5", lambda pair, md, w, d: run_s97(pair, md, w, d)),
    ]
    for name, gen, kw in one_h:
        runners.append((
            name,
            lambda pair, md, w, d, g=gen, n=name, k=kw: _run_1h(g, n, pair, md, w, d, **k),
        ))
    return runners


def should_run(name: str, pair: str) -> bool:
    if name == "s96":
        return pair == "XAUUSD"
    return True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    anchor = find_instrument_csv(data_root(), "XAUUSD", "1h")
    max_ts = scan_csv_max_timestamp(anchor) if anchor else None
    anchor_end = (
        datetime.fromtimestamp(max_ts, tz=timezone.utc).date().isoformat()
        if max_ts else "unknown"
    )
    runners = build_runners()
    # s96 gold-only baseline appended separately
    strat_names = ["s96"] + [r[0] for r in runners]

    print(f"Anchor latest bar (XAUUSD 1h): {anchor_end}")
    print(f"Windows: {WINDOWS}")
    print(f"Pairs: {PAIRS}")
    print(f"Strategies: {len(strat_names)}")

    rows: list[dict] = []
    for max_days, label in WINDOWS:
        print(f"\n######## WINDOW {label} ({max_days}d) ########", flush=True)
        for pair in PAIRS:
            print(f"=== {pair} [{label}] ===", flush=True)
            if pair == "XAUUSD":
                print("  s96 (gold-only 5m stack) ...", flush=True)
                job = OUT / label / pair / "s96"
                r96 = run_s96(pair, max_days, label, job)
                rows.append(r96)
                if r96.get("status") != "ok":
                    print(f"    -> {r96.get('status')} {r96.get('error', '')}")
                else:
                    print(
                        f"    n={r96['total_trades']} PF={r96['profit_factor']} "
                        f"R={r96['total_R']} ret%={r96['return_pct']} DD%={r96['max_DD_pct']}"
                    )
            for name, fn in runners:
                if not should_run(name, pair):
                    continue
                job = OUT / label / pair / name.split("_")[0]
                print(f"  {name} ...", flush=True)
                r = fn(pair, max_days, label, job)
                rows.append(r)
                if r.get("status") != "ok":
                    print(f"    -> {r.get('status')} {r.get('error', '')}")
                else:
                    print(
                        f"    {r.get('data_start')}->{r.get('data_end')} "
                        f"n={r['total_trades']} PF={r['profit_factor']} "
                        f"R={r['total_R']} ret%={r['return_pct']} DD%={r['max_DD_pct']}"
                    )

    baskets = []
    fx_baskets = []
    winners = []
    for _, label in WINDOWS:
        for strat in strat_names:
            baskets.append(basket(rows, strat, label))
            fx_baskets.append(fx_only_basket(rows, strat, label))
        winners.extend(best_per_pair(rows, label))

    print("\n======== BASKETS (5 pairs) ========")
    for b in sorted(baskets, key=lambda x: (-float(x.get("total_R") or -1e9), x.get("strategy", ""))):
        if b.get("window") == "1y" and b.get("status") != "empty":
            print(b)

    print("\n======== FX3 BASKETS (1y) ========")
    for b in sorted(fx_baskets, key=lambda x: (-float(x.get("total_R") or -1e9), x.get("strategy", ""))):
        if b.get("window") == "1y" and b.get("status") != "empty":
            print(b)

    payload = {
        "generated_at": datetime.now().isoformat(),
        "anchor_end": anchor_end,
        "windows_days": WINDOWS,
        "pairs": PAIRS,
        "strategies": strat_names,
        "equity_model": {"start": START_EQUITY, "risk_pct_of_initial_per_R": RISK_PCT},
        "per_pair": rows,
        "baskets": baskets,
        "fx3_baskets": fx_baskets,
        "best_per_pair": winners,
        "note": "s106+ are mechanical proxies; s96 only on XAUUSD in this harness",
    }
    (OUT / "summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    fields = [
        "strategy", "pair", "window", "max_days", "status", "data_start", "data_end", "tf_used",
        "total_trades", "win_rate", "profit_factor", "total_R", "avg_R", "max_DD_R",
        "return_pct", "max_DD_pct", "final_equity", "net_exness_pips",
        "max_DD_exness_pips", "error",
    ]
    with open(OUT / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with open(OUT / "basket.csv", "w", newline="", encoding="utf-8") as f:
        keys = list(baskets[0].keys()) if baskets else []
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(baskets)

    with open(OUT / "fx3_basket.csv", "w", newline="", encoding="utf-8") as f:
        keys = sorted({k for row in fx_baskets for k in row})
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(fx_baskets)

    with open(OUT / "best_per_pair.csv", "w", newline="", encoding="utf-8") as f:
        keys = list(winners[0].keys()) if winners else []
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(winners)

    OUT_MIRROR.mkdir(parents=True, exist_ok=True)
    for name in ("summary.json", "summary.csv", "basket.csv", "fx3_basket.csv", "best_per_pair.csv"):
        src = OUT / name
        if src.exists():
            (OUT_MIRROR / name).write_bytes(src.read_bytes())

    print(f"\nWrote {OUT}")
    print(f"Mirrored summaries to {OUT_MIRROR}")


if __name__ == "__main__":
    main()
