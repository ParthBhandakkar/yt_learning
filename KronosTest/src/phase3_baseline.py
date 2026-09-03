#!/usr/bin/env python3
"""Phase 3: filter S146 using only its own signal-time features (no Kronos).

This is deliberately run before any Kronos work. If plain geometry, spread and
timing features already recover most of the achievable improvement, then Kronos
has to beat this bar rather than beat the raw strategy, and we find that out
cheaply.

Models are intentionally small: ~700 training trades cannot support anything
large without memorising noise.

Also runs two controls:
  * shuffled labels  - should produce no out-of-sample edge; if it does, the
    harness is broken.
  * single-feature cuts - shows whether one obvious variable (e.g. spread cost)
    explains any gain, which is more trustworthy than a fitted model.

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase3_baseline.py
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
from evaluation import (  # noqa: E402
    FilterResult, evaluate_filter, print_result, summarize,
)

paths.ensure_dirs()
UTC = timezone.utc

RANDOM_STATE = 17


def load_dataset(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame = frame[frame["label_r"].notna()].copy()
    frame["meta_month"] = (pd.to_datetime(frame["meta_signal_time_utc"], utc=True)
                           .dt.strftime("%Y-%m"))
    return frame


def feature_matrix(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    values = frame[columns].to_numpy(dtype=float, na_value=np.nan)
    # Median imputation computed per call is fine because the caller passes the
    # training frame first and reuses the returned medians for the test frame.
    return values


def _impute(train: np.ndarray, test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    medians = np.nanmedian(train, axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    train_filled = np.where(np.isfinite(train), train, medians)
    test_filled = np.where(np.isfinite(test), test, medians)
    return train_filled, test_filled


def _standardize(train: np.ndarray, test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = train.mean(axis=0)
    std = train.std(axis=0)
    std = np.where(std > 1e-12, std, 1.0)
    return (train - mean) / std, (test - mean) / std


def fit_scores(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray,
               train_r: np.ndarray, model: str) -> tuple[np.ndarray, np.ndarray]:
    """Return (train_scores, test_scores). Higher score = more attractive trade."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression, Ridge

    if model == "logistic":
        estimator = LogisticRegression(C=0.1, max_iter=2000, random_state=RANDOM_STATE)
        estimator.fit(train_x, train_y)
        return (estimator.predict_proba(train_x)[:, 1],
                estimator.predict_proba(test_x)[:, 1])
    if model == "ridge_r":
        estimator = Ridge(alpha=10.0, random_state=RANDOM_STATE)
        estimator.fit(train_x, train_r)
        return estimator.predict(train_x), estimator.predict(test_x)
    if model == "gbdt":
        estimator = HistGradientBoostingClassifier(
            max_depth=3, max_iter=120, learning_rate=0.05,
            min_samples_leaf=40, l2_regularization=1.0, random_state=RANDOM_STATE)
        estimator.fit(train_x, train_y)
        return (estimator.predict_proba(train_x)[:, 1],
                estimator.predict_proba(test_x)[:, 1])
    raise ValueError(f"unknown model: {model}")


def run(dataset_path: Path, min_fraction: float) -> dict[str, Any]:
    frame = load_dataset(dataset_path)
    features = sorted(column for column in frame.columns if column.startswith("f_"))
    # Constant columns carry no information and destabilise standardisation.
    varying = [column for column in features if frame[column].nunique(dropna=True) > 1]
    dropped = sorted(set(features) - set(varying))

    train = frame[frame["meta_split"] == "train"]
    test = frame[frame["meta_split"] == "test"]
    print(f"dataset {dataset_path.name}: {len(frame):,} trades  "
          f"({len(train):,} train / {len(test):,} test)")
    print(f"features: {len(varying)} used, {len(dropped)} dropped as constant")
    if dropped:
        print(f"  dropped: {', '.join(dropped)}")

    train_raw = feature_matrix(train, varying)
    test_raw = feature_matrix(test, varying)
    train_x, test_x = _impute(train_raw, test_raw)
    train_x, test_x = _standardize(train_x, test_x)

    train_r = train["label_r"].to_numpy(dtype=float)
    test_r = test["label_r"].to_numpy(dtype=float)
    train_y = (train_r > 0).astype(int)

    before = summarize(test_r)
    print(f"\nunfiltered test baseline: {before.trades} trades  "
          f"net {before.net_r:+.2f}R  win {before.win_rate:.1%}  avg {before.average_r:+.4f}R")

    results: list[FilterResult] = []
    for model in ("logistic", "ridge_r", "gbdt"):
        train_scores, test_scores = fit_scores(train_x, train_y, test_x, train_r, model)
        result = evaluate_filter(
            f"s146_only::{model}", train_scores, train_r, test_scores, test_r,
            test["meta_symbol"].tolist(), test["meta_month"].tolist(),
            min_fraction=min_fraction)
        results.append(result)
        print_result(result)

    # --- control 1: shuffled labels -----------------------------------------
    rng = np.random.default_rng(RANDOM_STATE)
    shuffled = train_r.copy()
    rng.shuffle(shuffled)
    train_scores, test_scores = fit_scores(train_x, (shuffled > 0).astype(int),
                                           test_x, shuffled, "gbdt")
    control = evaluate_filter(
        "control::shuffled_labels", train_scores, shuffled, test_scores, test_r,
        test["meta_symbol"].tolist(), test["meta_month"].tolist(),
        min_fraction=min_fraction)
    control.notes.append("Labels shuffled in training. A real edge here would mean "
                         "the harness leaks, not that the strategy works.")
    results.append(control)
    print_result(control)

    # --- control 2: single-feature cuts -------------------------------------
    print("\n  [single-feature cuts on test, threshold from train]")
    single: dict[str, Any] = {}
    for column in varying:
        train_values = train[column].to_numpy(dtype=float, na_value=np.nan)
        test_values = test[column].to_numpy(dtype=float, na_value=np.nan)
        if not np.isfinite(train_values).any() or not np.isfinite(test_values).any():
            continue
        for sign, label in ((1.0, "high"), (-1.0, "low")):
            result = evaluate_filter(
                f"single::{column}:{label}", sign * train_values, train_r,
                sign * test_values, test_r, test["meta_symbol"].tolist(),
                test["meta_month"].tolist(), min_fraction=min_fraction)
            single[result.name] = result.as_dict()
    ranked = sorted(single.items(), key=lambda item: item[1]["test_after"]["net_r"],
                    reverse=True)[:8]
    for name, payload in ranked:
        print(f"    {name:<44} test net {payload['test_after']['net_r']:+8.2f}R  "
              f"kept {payload['kept_fraction']:.1%}")

    report = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": "phase3_s146_features_only",
        "dataset": str(dataset_path),
        "trades": {"train": int(len(train)), "test": int(len(test))},
        "features_used": varying, "features_dropped_constant": dropped,
        "min_kept_fraction": min_fraction,
        "test_unfiltered": before.as_dict(),
        "models": {result.name: result.as_dict() for result in results},
        "single_feature_cuts": single,
        "interpretation": [
            "Any model whose test net R stays negative has not fixed S146.",
            "control::shuffled_labels must show no edge; if it does, distrust "
            "every other number in this file.",
        ],
    }
    output = paths.OUT / "phase3_baseline.json"
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"\nReport: {output}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path,
                        default=paths.DATASET / "s146_signals.csv")
    parser.add_argument("--min-fraction", type=float, default=0.25,
                        help="minimum share of trades a threshold must keep")
    args = parser.parse_args(argv)
    if not args.dataset.is_file():
        raise SystemExit(f"dataset not found: {args.dataset}. Run phase2_dataset.py first.")
    run(args.dataset, args.min_fraction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
