#!/usr/bin/env python3
"""Phase 9: causal S146 spread/session gate study.

The replay is run with portfolio gates and the existing spread rejection disabled
*inside this process only*. That preserves detector-valid signals whose spread is
large, so this phase can test spread and UTC-session gates as signal-level filters.
No live code or MT5 execution is modified.

Thresholds are selected from a fixed candidate list using only the chronological
training period. The later period is used once for evaluation. Results are
signal-level independent outcomes: skipping one trade is not replayed through the
portfolio allocator, so freed slots and changed downstream selection are a known
second-order limitation.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

paths.ensure_dirs()
paths.add_repo_to_syspath()
UTC = timezone.utc

# Declared before reading outcomes. These are deliberately small and fixed rather
# than an unconstrained search over every observed spread/session value.
SPREAD_PCT_THRESHOLDS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.75)
SESSION_POLICIES = {
    "all_sessions": frozenset(("Asia", "London", "New_York", "Late_rollover")),
    "exclude_late_rollover": frozenset(("Asia", "London", "New_York")),
    "london_new_york": frozenset(("London", "New_York")),
    "london_only": frozenset(("London",)),
    "new_york_only": frozenset(("New_York",)),
    "asia_only": frozenset(("Asia",)),
}
ABSOLUTE_SPREAD_BINS = ((0.0, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 10.0),
                        (10.0, 25.0), (25.0, 50.0), (50.0, 100.0), (100.0, math.inf))


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(UTC) if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    except ValueError:
        return None


def iso_epoch(value: str | None) -> int | None:
    stamp = parse_time(value)
    return None if stamp is None else int(stamp.timestamp())


def session_for_hour(hour: int) -> str:
    if 0 <= hour <= 7:
        return "Asia"
    if 8 <= hour <= 12:
        return "London"
    if 13 <= hour <= 20:
        return "New_York"
    return "Late_rollover"


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def ensure_inside(path: Path) -> Path:
    resolved = path.resolve()
    root = paths.KRONOS_TEST.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"refusing to write outside KronosTest: {resolved}")
    return resolved


def load_rows(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    signals = read_jsonl(run_dir / "detector_signals.jsonl")
    trades_value = json.loads((run_dir / "trades.json").read_text(encoding="utf-8"))
    trades = trades_value if isinstance(trades_value, list) else []
    trades_by_signal = {str(row.get("signal_id")): row for row in trades}
    detector_rows: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    for signal in signals:
        stamp = parse_time(signal.get("signal_time_utc"))
        if stamp is None:
            continue
        hour = stamp.hour
        detector_rows.append({
            "signal_id": signal.get("signal_id"), "symbol": signal.get("symbol"),
            "signal_time_utc": signal.get("signal_time_utc"), "epoch": int(stamp.timestamp()),
            "hour": hour, "session": session_for_hour(hour),
            "spread_pct": number(signal.get("spread_pct_of_structural_risk")),
            "spread_points": number(signal.get("spread_at_signal_points")),
        })
        trade = trades_by_signal.get(str(signal.get("signal_id")))
        if trade is None:
            continue
        realized = number(trade.get("realized_r"))
        if realized is None:
            realized = number(trade.get("r"))
        if realized is None:
            continue
        exit_stamp = parse_time(trade.get("exit_time_utc"))
        fills.append({
            **detector_rows[-1], "trade_id": trade.get("trade_id"),
            "r": realized, "outcome": trade.get("outcome", "open"),
            "exit_epoch": int(exit_stamp.timestamp()) if exit_stamp else None,
            "exit_time_utc": trade.get("exit_time_utc"),
        })
    detector_rows.sort(key=lambda row: (row["epoch"], str(row.get("symbol"))))
    fills.sort(key=lambda row: (row["epoch"], str(row.get("symbol")), str(row.get("signal_id"))))
    return fills, {"detector_rows": detector_rows, "signals": signals, "trades": trades}


def returns_in_exit_order(rows: list[dict[str, Any]]) -> list[float]:
    ordered = sorted(rows, key=lambda row: (row["exit_epoch"] if row["exit_epoch"] is not None else 2**63,
                                            row["epoch"], str(row.get("signal_id"))))
    return [float(row["r"]) for row in ordered if row.get("outcome") != "open"]


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row.get("outcome") != "open"]
    values = np.asarray([float(row["r"]) for row in resolved], dtype=float)
    net = float(sum(float(row["r"]) for row in rows))
    wins = int((values > 1e-10).sum()) if values.size else 0
    losses = int((values < -1e-10).sum()) if values.size else 0
    gross_win = float(values[values > 0].sum()) if values.size else 0.0
    gross_loss = float(-values[values < 0].sum()) if values.size else 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in returns_in_exit_order(rows):
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "fills": len(rows), "resolved": len(resolved), "open": len(rows) - len(resolved),
        "wins": wins, "losses": losses,
        "win_rate_pct": round(100.0 * wins / (wins + losses), 4) if wins + losses else 0.0,
        "net_r": round(net, 6),
        "average_r": round(float(values.mean()), 6) if values.size else 0.0,
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else (None if not gross_win else None),
        "max_drawdown_r": round(max_dd, 6), "gross_win_r": round(gross_win, 6),
        "gross_loss_r": round(gross_loss, 6),
    }


def spec_mask(rows: list[dict[str, Any]], spec: dict[str, Any]) -> np.ndarray:
    keep = np.ones(len(rows), dtype=bool)
    threshold = spec.get("spread_pct_max")
    if threshold is not None:
        values = np.asarray([row["spread_pct"] if row["spread_pct"] is not None else np.nan for row in rows])
        keep &= np.isfinite(values) & (values <= float(threshold) + 1e-12)
    sessions = spec.get("sessions")
    if sessions is not None:
        keep &= np.asarray([row["session"] in sessions for row in rows], dtype=bool)
    return keep


def candidate_specs(kind: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [{"name": "no_gate", "kind": "baseline"}]
    if kind in ("spread", "combined"):
        for threshold in SPREAD_PCT_THRESHOLDS:
            specs.append({"name": f"spread_pct_le_{threshold:g}", "kind": kind,
                          "spread_pct_max": threshold})
    if kind in ("session", "combined"):
        for name, sessions in SESSION_POLICIES.items():
            if name != "all_sessions":
                specs.append({"name": f"session_{name}", "kind": kind, "sessions": sessions})
    if kind == "combined":
        spread_specs = [spec for spec in specs if spec.get("spread_pct_max") is not None]
        session_specs = [spec for spec in specs if spec.get("sessions") is not None]
        specs = [{"name": "no_gate", "kind": "baseline"}]
        for spread in spread_specs:
            for session in session_specs:
                specs.append({"name": f"{spread['name']}__{session['name']}", "kind": kind,
                              "spread_pct_max": spread["spread_pct_max"], "sessions": session["sessions"]})
    return specs


def choose_spec(train: list[dict[str, Any]], specs: list[dict[str, Any]], min_fraction: float) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    minimum = max(1, int(math.ceil(len(train) * min_fraction)))
    scored: list[dict[str, Any]] = []
    for spec in specs:
        mask = spec_mask(train, spec)
        kept = int(mask.sum())
        if kept < minimum:
            continue
        chosen_rows = [row for row, value in zip(train, mask) if value]
        scored.append({"name": spec["name"], "train_net_r": stats(chosen_rows)["net_r"],
                       "train_fills": kept, "train_kept_fraction": round(kept / len(train), 6),
                       "spread_pct_max": spec.get("spread_pct_max"),
                       "sessions": sorted(spec["sessions"]) if spec.get("sessions") else None})
    if not scored:
        raise RuntimeError("no gate candidate meets the minimum training retention")
    scored.sort(key=lambda row: (-row["train_net_r"], -row["train_fills"], row["name"]))
    selected_row = scored[0]
    selected = next(spec for spec in specs if spec["name"] == selected_row["name"])
    return selected, selected_row, scored


def group_stats(rows: list[dict[str, Any]], mask: np.ndarray, key: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    names = sorted({str(key(row)) for row in rows})
    result: dict[str, Any] = {}
    for name in names:
        before = [row for row in rows if str(key(row)) == name]
        after = [row for row, keep in zip(rows, mask) if keep and str(key(row)) == name]
        result[name] = {"before": stats(before), "after": stats(after),
                        "signal_level_delta_net_r": round(stats(after)["net_r"] - stats(before)["net_r"], 6)}
    return result


def nulls(train: list[dict[str, Any]], test: list[dict[str, Any]], specs: list[dict[str, Any]],
          selected: dict[str, Any], observed_mask: np.ndarray, iterations: int, seed: int,
          min_fraction: float) -> dict[str, Any]:
    observed = float(sum(row["r"] for row, keep in zip(test, observed_mask) if keep))
    kept = int(observed_mask.sum())
    rng = np.random.default_rng(seed)
    random_draws = np.empty(iterations, dtype=float)
    for index in range(iterations):
        picked = rng.choice(len(test), size=kept, replace=False) if kept else []
        random_draws[index] = float(sum(test[int(position)]["r"] for position in picked))
    permutation_draws = np.empty(iterations, dtype=float)
    masks = [(spec, spec_mask(test, spec)) for spec in specs]
    minimum = max(1, int(math.ceil(len(train) * min_fraction)))
    eligible = [(spec, spec_mask(train, spec), test_mask) for spec, test_mask in masks
                if int(spec_mask(train, spec).sum()) >= minimum]
    train_masks = [(spec, mask) for spec, mask, _ in eligible]
    test_masks = [(spec, test_mask) for spec, _, test_mask in eligible]
    train_values = np.asarray([float(row["r"]) for row in train], dtype=float)
    for index in range(iterations):
        shuffled = rng.permutation(train_values)
        ranked: list[tuple[float, int, str, int]] = []
        for position, (spec, mask) in enumerate(train_masks):
            values = shuffled[mask]
            ranked.append((float(values.sum()), -int(mask.sum()), spec["name"], position))
        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        chosen_position = ranked[0][3]
        selected_test_mask = test_masks[chosen_position][1]
        permutation_draws[index] = float(sum(row["r"] for row, keep in zip(test, selected_test_mask) if keep))
    def describe(draws: np.ndarray) -> dict[str, Any]:
        return {"iterations": int(iterations), "null_mean_net_r": round(float(draws.mean()), 6),
                "null_std_net_r": round(float(draws.std()), 6),
                "null_p05_net_r": round(float(np.percentile(draws, 5)), 6),
                "null_p95_net_r": round(float(np.percentile(draws, 95)), 6),
                "observed_net_r": round(observed, 6),
                "p_value": round(float((draws >= observed).mean()), 6)}
    return {"kept_test_fills": kept, "random_subset_null": describe(random_draws),
            "permutation_null": describe(permutation_draws)}


def absolute_bin(value: float | None) -> str:
    if value is None:
        return "missing"
    for lower, upper in ABSOLUTE_SPREAD_BINS:
        if lower < value <= upper or (lower == 0 and value == 0):
            return f"({lower:g},{upper:g}]" if upper != math.inf else f">{lower:g}"
    return "missing"


def gate_report(name: str, train: list[dict[str, Any]], test: list[dict[str, Any]],
                detector_test: list[dict[str, Any]], spec: dict[str, Any], selection: dict[str, Any],
                candidates: list[dict[str, Any]], all_specs: list[dict[str, Any]], iterations: int,
                seed: int, min_fraction: float) -> dict[str, Any]:
    train_mask = spec_mask(train, spec)
    test_mask = spec_mask(test, spec)
    test_rows = [row for row, keep in zip(test, test_mask) if keep]
    detector_mask = spec_mask(detector_test, spec)
    before = stats(test)
    after = stats(test_rows)
    return {
        "name": name, "selected_spec": {"name": spec["name"], "spread_pct_max": spec.get("spread_pct_max"),
                                           "sessions": sorted(spec["sessions"]) if spec.get("sessions") else None},
        "selection": {"chosen_on": "chronological_training_period_only", "minimum_train_retention": min_fraction,
                      "selected_train": selection, "top_train_candidates": candidates[:10]},
        "test": {"detector_signals": int(detector_mask.sum()), "detector_retention": round(float(detector_mask.mean()), 6),
                 "before": before, "after": after,
                 "fill_retention": round(float(test_mask.mean()), 6) if len(test_mask) else 0.0},
        "nulls": nulls(train, test, all_specs, spec, test_mask, iterations, seed, min_fraction),
        "by_symbol": group_stats(test, test_mask, lambda row: row["symbol"]),
        "by_month": group_stats(test, test_mask, lambda row: (parse_time(row["signal_time_utc"]) or datetime.fromtimestamp(0, UTC)).strftime("%Y-%m")),
    }


def run_analysis(run_dir: Path, output: Path, cutoff: datetime, iterations: int,
                  min_fraction: float, seed: int, original_spread_limit: float) -> dict[str, Any]:
    fills, raw = load_rows(run_dir)
    train = [row for row in fills if row["epoch"] < int(cutoff.timestamp())]
    test = [row for row in fills if row["epoch"] >= int(cutoff.timestamp())]
    detector_rows = raw["detector_rows"]
    detector_test = [row for row in detector_rows if row["epoch"] >= int(cutoff.timestamp())]
    if not train or not test:
        raise RuntimeError(f"chronological split is empty: train={len(train)}, test={len(test)}")

    reports: dict[str, Any] = {}
    for kind in ("spread", "session", "combined"):
        specs = candidate_specs(kind)
        selected, selected_row, candidates = choose_spec(train, specs, min_fraction)
        reports[kind] = gate_report(kind, train, test, detector_test, selected, selected_row,
                                    candidates, specs, iterations, seed + len(reports), min_fraction)

    overall = {
        "all_detector_signals": len(raw["detector_rows"]),
        "all_simulated_fills": len(fills), "fills_without_trade": len(raw["detector_rows"]) - len(fills),
        "train": {"signals": sum(row["epoch"] < int(cutoff.timestamp()) for row in detector_rows),
                  "fills": len(train), "stats": stats(train)},
        "test": {"signals": len(detector_test), "fills": len(test), "stats": stats(test)},
    }
    report = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": "phase9_spread_session_gate", "replay_dir": str(run_dir),
        "raw_replay": {"portfolio_gates": False, "existing_spread_rejection_disabled": True,
                        "original_s146_max_spread_pct_of_risk": original_spread_limit,
                        "study_is_read_only": True},
        "split": {"cutoff_utc": cutoff.isoformat().replace("+00:00", "Z"),
                  "train_before_cutoff": True, "test_on_or_after_cutoff": True},
        "declared_candidates": {"spread_pct_thresholds": list(SPREAD_PCT_THRESHOLDS),
                                "session_buckets_utc": {"Asia": "00:00-07:59", "London": "08:00-12:59",
                                                        "New_York": "13:00-20:59", "Late_rollover": "21:00-23:59"},
                                "session_policies": {key: sorted(value) for key, value in SESSION_POLICIES.items()},
                                "absolute_spread_bins_points": [[lower, None if math.isinf(upper) else upper]
                                                                    for lower, upper in ABSOLUTE_SPREAD_BINS]},
        "overall": overall, "selected_gates": reports,
        "descriptive_test_breakdowns": {
            "by_hour_utc": group_stats(test, np.ones(len(test), dtype=bool), lambda row: f"{row['hour']:02d}"),
            "by_session": group_stats(test, np.ones(len(test), dtype=bool), lambda row: row["session"]),
            "by_absolute_spread_points": group_stats(test, np.ones(len(test), dtype=bool), lambda row: absolute_bin(row["spread_points"])),
            "by_spread_pct_of_structural_risk": group_stats(test, np.ones(len(test), dtype=bool),
                lambda row: "missing" if row["spread_pct"] is None else f"{min(0.99, math.floor(row['spread_pct'] * 10) / 10):.1f}-{min(1.0, math.floor(row['spread_pct'] * 10) / 10 + 0.1):.1f}"),
        },
        "interpretation": [
            "The candidate gate is chosen only on the earlier chronological training period; the later period is not used for threshold selection.",
            "Net R is the sum of independent hypothetical replay outcomes. This is not a second portfolio replay: skipping a signal can free a pair/currency/concurrency slot and change later selection.",
            "Session is a UTC calendar proxy, not a measured liquidity regime. Absolute spread points are descriptive because point scales differ across symbols; spread percentage of structural risk is the more comparable gate.",
            "P-values compare the selected test result with equal-size random subsets and with the same candidate-selection process trained on permuted training labels. They do not correct for every possible strategy idea outside this declared candidate family.",
            "The replay uses native H4/M15/M5 closed-prefix causality, next-M5 execution, spread-side pricing, live ladder management, and stop-first same-bar ambiguity.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


def run_replay(raw_root: Path, replay_dir: Path, run_id: str) -> tuple[Path, float]:
    manifest = json.loads((raw_root / "manifest.json").read_text(encoding="utf-8"))
    requested = manifest.get("requested_score_range") or {}
    start = parse_time(requested.get("start_utc"))
    end = parse_time(requested.get("end_utc"))
    if start is None or end is None:
        raise RuntimeError("raw manifest has no requested score range")
    paths.redirect_live_logs()
    from backtests.s146_running_extreme import replay as replay_module  # noqa: PLC0415
    original = float(replay_module.CONFIG.s146_max_spread_pct_of_risk)
    replay_module.CONFIG.s146_max_spread_pct_of_risk = 0.0
    try:
        replay_module.replay_history(raw_root, replay_dir, list(paths.S146_BASKET), start, end,
                                     run_id, include_portfolio_gates=False, progress=print)
    finally:
        replay_module.CONFIG.s146_max_spread_pct_of_risk = original
    return replay_dir, original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-root", type=Path, default=paths.S146_LEGACY_RAW)
    parser.add_argument("--replay-dir", type=Path, default=paths.DATA / "gate_runs" / "s146-no-gates")
    parser.add_argument("--run-id", default="s146-no-gates")
    parser.add_argument("--cutoff", default="2026-03-31T06:55:00Z")
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--min-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--skip-replay", action="store_true")
    args = parser.parse_args(argv)
    if args.iterations <= 0 or not 0 < args.min_fraction <= 1:
        raise SystemExit("iterations must be positive and min-fraction must be in (0, 1]")
    raw_root = args.raw_root.resolve()
    replay_dir = ensure_inside(args.replay_dir)
    cutoff = parse_time(args.cutoff)
    if cutoff is None:
        raise SystemExit("cutoff must be an ISO UTC timestamp")
    original = float("nan")
    if not args.skip_replay:
        replay_dir, original = run_replay(raw_root, replay_dir, args.run_id)
    else:
        try:
            paths.add_repo_to_syspath()
            from liveTrade.config import CONFIG  # noqa: PLC0415
            original = float(CONFIG.s146_max_spread_pct_of_risk)
        except (ImportError, AttributeError, TypeError, ValueError):
            original = float("nan")
    if not (replay_dir / "detector_signals.jsonl").is_file() or not (replay_dir / "trades.json").is_file():
        raise SystemExit(f"replay artifacts missing under {replay_dir}")
    report_path = ensure_inside(paths.OUT / "phase9_spread_session_gate.json")
    report = run_analysis(replay_dir, report_path, cutoff, args.iterations,
                          args.min_fraction, args.seed, original)
    print(f"\nPhase 9: {report['overall']['all_detector_signals']} detector signals, "
          f"{report['overall']['all_simulated_fills']} simulated fills")
    for kind, item in report["selected_gates"].items():
        selected = item["selected_spec"]
        after = item["test"]["after"]
        print(f"  {kind}: {selected['name']} -> {after['fills']} test fills, "
              f"net {after['net_r']:+.2f}R, avg {after['average_r']:+.4f}R, "
              f"random p={item['nulls']['random_subset_null']['p_value']:.3f}, "
              f"perm p={item['nulls']['permutation_null']['p_value']:.3f}")
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
