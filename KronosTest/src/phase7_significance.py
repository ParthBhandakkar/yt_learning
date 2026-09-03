#!/usr/bin/env python3
"""Phase 7: is any measured 'edge' distinguishable from noise?

A single shuffled-label control is not enough. In Phase 3 the control landed at
-18.67R and in Phase 6 at +12.71R, a ~31R swing from pure noise, which is wider
than every improvement either phase reported. So the only defensible way to read
those numbers is against a full null distribution.

Two nulls are built:

  1. **random subset** - keep k random test trades, many times. Answers "is
     keeping *these* k trades better than keeping *any* k trades?"
  2. **permutation** - shuffle the training labels, refit, reselect the
     threshold, score the real test set, many times. Answers "does fitting on
     real labels beat fitting on noise?"

Empirical p-values follow directly. Anything that cannot clear these is a
coin flip, whatever its headline net R.

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase7_significance.py --iterations 300
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from evaluation import choose_threshold, summarize  # noqa: E402
from kronos_runner import KRONOS_FEATURES  # noqa: E402
from phase3_baseline import _impute, _standardize, fit_scores  # noqa: E402

paths.ensure_dirs()
UTC = timezone.utc


def load(dataset_path: Path, forecast_path: Path | None) -> pd.DataFrame:
    frame = pd.read_csv(dataset_path)
    frame = frame[frame["label_r"].notna()].copy()
    if forecast_path and forecast_path.is_file():
        forecasts = pd.read_csv(forecast_path)
        forecasts = forecasts[forecasts["k_p_target_first"].notna()]
        columns = ["meta_trade_id", *[c for c in KRONOS_FEATURES if c in forecasts.columns]]
        frame = frame.merge(forecasts[columns], on="meta_trade_id", how="inner")
    return frame


def varying(frame: pd.DataFrame, prefix: str) -> list[str]:
    return [c for c in sorted(frame.columns)
            if c.startswith(prefix) and c != "k_paths"
            and frame[c].nunique(dropna=True) > 1]


def random_subset_null(test_r: np.ndarray, keep: int, iterations: int,
                       seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    draws = np.empty(iterations, dtype=float)
    for index in range(iterations):
        picked = rng.choice(test_r.size, size=keep, replace=False)
        draws[index] = test_r[picked].sum()
    return draws


def permutation_null(train_x: np.ndarray, test_x: np.ndarray, train_r: np.ndarray,
                     test_r: np.ndarray, model: str, min_fraction: float,
                     iterations: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    draws = np.empty(iterations, dtype=float)
    for index in range(iterations):
        shuffled = rng.permutation(train_r)
        train_scores, test_scores = fit_scores(
            train_x, (shuffled > 0).astype(int), test_x, shuffled, model)
        chosen = choose_threshold(train_scores, shuffled, min_fraction=min_fraction)
        keep = np.asarray(test_scores, dtype=float) >= chosen["threshold"]
        draws[index] = float(test_r[keep].sum()) if keep.any() else 0.0
    return draws


def describe(draws: np.ndarray, observed: float) -> dict[str, Any]:
    return {
        "iterations": int(draws.size),
        "null_mean_net_r": round(float(draws.mean()), 4),
        "null_std_net_r": round(float(draws.std()), 4),
        "null_p05_net_r": round(float(np.percentile(draws, 5)), 4),
        "null_p50_net_r": round(float(np.percentile(draws, 50)), 4),
        "null_p95_net_r": round(float(np.percentile(draws, 95)), 4),
        "observed_net_r": round(float(observed), 4),
        # One-sided: how often does noise match or beat what we measured?
        "p_value": round(float((draws >= observed).mean()), 4),
    }


def run(dataset_path: Path, forecast_path: Path | None, iterations: int,
        min_fraction: float, seed: int) -> dict[str, Any]:
    frame = load(dataset_path, forecast_path)
    train = frame[frame["meta_split"] == "train"]
    test = frame[frame["meta_split"] == "test"]
    train_r = train["label_r"].to_numpy(dtype=float)
    test_r = test["label_r"].to_numpy(dtype=float)
    train_y = (train_r > 0).astype(int)

    s146 = varying(frame, "f_")
    kronos = varying(frame, "k_")
    feature_sets = {"s146_only": s146}
    if kronos:
        feature_sets["kronos_only"] = kronos
        feature_sets["combined"] = s146 + kronos

    before = summarize(test_r)
    print(f"train {len(train):,}  test {len(test):,}  "
          f"unfiltered test net {before.net_r:+.2f}R")
    print(f"iterations per null: {iterations}\n")

    findings: dict[str, Any] = {}
    for set_name, columns in feature_sets.items():
        train_x, test_x = _impute(train[columns].to_numpy(dtype=float, na_value=np.nan),
                                  test[columns].to_numpy(dtype=float, na_value=np.nan))
        train_x, test_x = _standardize(train_x, test_x)
        for model in ("logistic", "gbdt"):
            name = f"{set_name}::{model}"
            train_scores, test_scores = fit_scores(train_x, train_y, test_x, train_r, model)
            chosen = choose_threshold(train_scores, train_r, min_fraction=min_fraction)
            keep = np.asarray(test_scores, dtype=float) >= chosen["threshold"]
            observed = float(test_r[keep].sum())
            kept = int(keep.sum())

            subset = describe(random_subset_null(test_r, kept, iterations, seed), observed)
            permuted = describe(permutation_null(train_x, test_x, train_r, test_r,
                                                 model, min_fraction, iterations, seed + 1),
                                observed)
            findings[name] = {
                "kept_trades": kept, "kept_fraction": round(kept / test_r.size, 4),
                "observed_net_r": round(observed, 4),
                "observed_average_r": round(observed / kept, 6) if kept else None,
                "random_subset_null": subset,
                "permutation_null": permuted,
                "verdict": ("indistinguishable from noise"
                            if min(subset["p_value"], permuted["p_value"]) > 0.05
                            else "survives both nulls at p<=0.05"
                            if max(subset["p_value"], permuted["p_value"]) <= 0.05
                            else "mixed: passes one null, fails the other"),
            }
            print(f"  {name}")
            print(f"    kept {kept}/{test_r.size}  observed net {observed:+.2f}R")
            print(f"    random-subset null: mean {subset['null_mean_net_r']:+.2f}R  "
                  f"sd {subset['null_std_net_r']:.2f}  "
                  f"90% [{subset['null_p05_net_r']:+.2f}, {subset['null_p95_net_r']:+.2f}]  "
                  f"p={subset['p_value']:.3f}")
            print(f"    permutation  null: mean {permuted['null_mean_net_r']:+.2f}R  "
                  f"sd {permuted['null_std_net_r']:.2f}  "
                  f"90% [{permuted['null_p05_net_r']:+.2f}, {permuted['null_p95_net_r']:+.2f}]  "
                  f"p={permuted['p_value']:.3f}")
            print(f"    -> {findings[name]['verdict']}\n")

    report = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": "phase7_significance",
        "dataset": str(dataset_path),
        "forecasts": str(forecast_path) if forecast_path else None,
        "iterations": iterations, "min_kept_fraction": min_fraction, "seed": seed,
        "test_unfiltered": before.as_dict(),
        "findings": findings,
        "how_to_read": [
            "p_value is the share of noise runs that matched or beat the measured "
            "net R. Above 0.05 means the result is not distinguishable from luck.",
            "The random-subset null asks whether the kept trades beat an equally "
            "sized random selection.",
            "The permutation null asks whether fitting on real labels beats "
            "fitting the same pipeline on shuffled labels.",
            "With 467 test trades and ~30% kept, the null standard deviation is "
            "large; that width, not the headline number, is the story.",
        ],
    }
    output = paths.OUT / "phase7_significance.json"
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Report: {output}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=paths.DATASET / "s146_signals.csv")
    parser.add_argument("--forecasts", type=Path,
                        default=paths.FORECASTS / "kronos_zeroshot.csv")
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--min-fraction", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)
    if not args.dataset.is_file():
        raise SystemExit(f"dataset not found: {args.dataset}")
    forecasts = args.forecasts if args.forecasts.is_file() else None
    run(args.dataset, forecasts, args.iterations, args.min_fraction, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
