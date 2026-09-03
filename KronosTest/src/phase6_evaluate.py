#!/usr/bin/env python3
"""Phase 6: does Kronos add anything over S146's own features?

Compares four feature sets on the same fixed time split, plus controls:

  A  s146_only     - the Phase 3 bar
  B  kronos_only   - forecast features alone
  C  combined      - both
  D  raw rules     - single Kronos quantities with no fitted model at all

The question is never "is C profitable" but "is C better than A by more than
noise". Raw rules matter because a hand-checkable cut that works is far more
trustworthy than a fitted model on a few hundred trades.

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase6_evaluate.py
    .venv\\Scripts\\python.exe src\\phase6_evaluate.py --forecasts data\\forecasts\\kronos_finetuned.csv --tag finetuned
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
    FilterResult, bootstrap_average_r, evaluate_filter, print_result, summarize,
)
from kronos_runner import KRONOS_FEATURES  # noqa: E402
from phase3_baseline import RANDOM_STATE, _impute, _standardize, fit_scores  # noqa: E402

paths.ensure_dirs()
UTC = timezone.utc

# Kronos quantities that can act as a filter with no model fitted at all.
RAW_RULES = (
    ("k_p_target_first", 1.0),
    ("k_p_stop_first", -1.0),
    ("k_expected_r", 1.0),
    ("k_median_r", 1.0),
    ("k_target_stop_ratio", 1.0),
    ("k_mean_terminal_r", 1.0),
    ("k_p_terminal_favorable", 1.0),
    ("k_mean_mae_r", 1.0),
    ("k_dispersion_r", -1.0),
)


def load_joined(dataset_path: Path, forecast_path: Path) -> pd.DataFrame:
    dataset = pd.read_csv(dataset_path)
    dataset = dataset[dataset["label_r"].notna()].copy()
    forecasts = pd.read_csv(forecast_path)
    usable = forecasts[forecasts["k_p_target_first"].notna()].copy()
    columns = ["meta_trade_id", *[c for c in KRONOS_FEATURES if c in usable.columns]]
    merged = dataset.merge(usable[columns], on="meta_trade_id", how="inner")
    merged["meta_month"] = (pd.to_datetime(merged["meta_signal_time_utc"], utc=True)
                            .dt.strftime("%Y-%m"))
    print(f"dataset {len(dataset):,} trades  forecasts {len(forecasts):,} "
          f"({len(usable):,} usable)  joined {len(merged):,}")
    return merged


def varying_columns(frame: pd.DataFrame, prefix: str) -> list[str]:
    return [column for column in sorted(frame.columns)
            if column.startswith(prefix) and frame[column].nunique(dropna=True) > 1]


def run(dataset_path: Path, forecast_path: Path, tag: str,
        min_fraction: float) -> dict[str, Any]:
    frame = load_joined(dataset_path, forecast_path)
    train = frame[frame["meta_split"] == "train"]
    test = frame[frame["meta_split"] == "test"]
    train_r = train["label_r"].to_numpy(dtype=float)
    test_r = test["label_r"].to_numpy(dtype=float)
    train_y = (train_r > 0).astype(int)
    test_symbols = test["meta_symbol"].tolist()
    test_months = test["meta_month"].tolist()

    before = summarize(test_r)
    print(f"\ntrain {len(train):,}  test {len(test):,}")
    print(f"unfiltered test: {before.trades} trades  net {before.net_r:+.2f}R  "
          f"win {before.win_rate:.1%}  avg {before.average_r:+.4f}R")

    s146_columns = varying_columns(frame, "f_")
    kronos_columns = [c for c in varying_columns(frame, "k_") if c != "k_paths"]
    print(f"features: {len(s146_columns)} s146, {len(kronos_columns)} kronos")

    feature_sets = {
        "s146_only": s146_columns,
        "kronos_only": kronos_columns,
        "combined": s146_columns + kronos_columns,
    }

    results: list[FilterResult] = []
    for set_name, columns in feature_sets.items():
        if not columns:
            continue
        train_x, test_x = _impute(train[columns].to_numpy(dtype=float, na_value=np.nan),
                                  test[columns].to_numpy(dtype=float, na_value=np.nan))
        train_x, test_x = _standardize(train_x, test_x)
        for model in ("logistic", "gbdt"):
            train_scores, test_scores = fit_scores(train_x, train_y, test_x, train_r, model)
            result = evaluate_filter(
                f"{set_name}::{model}", train_scores, train_r, test_scores, test_r,
                test_symbols, test_months, min_fraction=min_fraction)
            results.append(result)
            print_result(result)

    # --- D: raw Kronos rules, no fitted model -------------------------------
    print("\n  [raw Kronos rules, threshold from train only]")
    raw: dict[str, Any] = {}
    for column, sign in RAW_RULES:
        if column not in frame.columns or frame[column].nunique(dropna=True) <= 1:
            continue
        result = evaluate_filter(
            f"raw::{column}{'+' if sign > 0 else '-'}",
            sign * train[column].to_numpy(dtype=float, na_value=np.nan), train_r,
            sign * test[column].to_numpy(dtype=float, na_value=np.nan), test_r,
            test_symbols, test_months, min_fraction=min_fraction)
        raw[result.name] = result.as_dict()
        boot = result.test_bootstrap_after
        print(f"    {result.name:<34} net {result.test_after['net_r']:+8.2f}R  "
              f"kept {result.kept_fraction:>5.1%}  "
              f"P(avg>0)={boot.get('prob_mean_positive') or 0:.0%}")

    # --- control: shuffled labels on the combined set ------------------------
    columns = feature_sets["combined"]
    train_x, test_x = _impute(train[columns].to_numpy(dtype=float, na_value=np.nan),
                              test[columns].to_numpy(dtype=float, na_value=np.nan))
    train_x, test_x = _standardize(train_x, test_x)
    rng = np.random.default_rng(RANDOM_STATE)
    shuffled = train_r.copy()
    rng.shuffle(shuffled)
    train_scores, test_scores = fit_scores(train_x, (shuffled > 0).astype(int),
                                           test_x, shuffled, "gbdt")
    control = evaluate_filter("control::shuffled_labels", train_scores, shuffled,
                              test_scores, test_r, test_symbols, test_months,
                              min_fraction=min_fraction)
    control.notes.append("Training labels shuffled; any edge here invalidates the harness.")
    results.append(control)
    print_result(control)

    # --- does Kronos beat the S146-only bar? --------------------------------
    lookup = {result.name: result for result in results}
    comparison: dict[str, Any] = {}
    for model in ("logistic", "gbdt"):
        base = lookup.get(f"s146_only::{model}")
        combined = lookup.get(f"combined::{model}")
        if not base or not combined:
            continue
        delta = combined.test_after["net_r"] - base.test_after["net_r"]
        comparison[model] = {
            "s146_only_net_r": base.test_after["net_r"],
            "combined_net_r": combined.test_after["net_r"],
            "delta_net_r": round(delta, 6),
            "s146_only_avg_r": base.test_after["average_r"],
            "combined_avg_r": combined.test_after["average_r"],
            "combined_prob_avg_positive": combined.test_bootstrap_after.get("prob_mean_positive"),
        }

    # Correlation between the headline Kronos signal and the realised outcome is
    # the most direct check of whether the forecast knows anything at all.
    diagnostics: dict[str, Any] = {}
    for column in ("k_p_target_first", "k_expected_r", "k_mean_terminal_r"):
        if column not in frame.columns:
            continue
        values = frame[column].to_numpy(dtype=float)
        outcomes = frame["label_r"].to_numpy(dtype=float)
        mask = np.isfinite(values) & np.isfinite(outcomes)
        if mask.sum() < 10:
            continue
        diagnostics[column] = {
            "pearson_vs_label_r": round(float(np.corrcoef(values[mask], outcomes[mask])[0, 1]), 6),
            "spearman_vs_label_r": round(float(pd.Series(values[mask]).corr(
                pd.Series(outcomes[mask]), method="spearman")), 6),
            "mean_label_r_top_quartile": round(float(
                outcomes[mask][values[mask] >= np.percentile(values[mask], 75)].mean()), 6),
            "mean_label_r_bottom_quartile": round(float(
                outcomes[mask][values[mask] <= np.percentile(values[mask], 25)].mean()), 6),
        }

    print("\n  [does the forecast correlate with the outcome at all?]")
    for column, stats in diagnostics.items():
        print(f"    {column:<24} pearson {stats['pearson_vs_label_r']:+.4f}  "
              f"spearman {stats['spearman_vs_label_r']:+.4f}  "
              f"top-quartile avg R {stats['mean_label_r_top_quartile']:+.4f} vs "
              f"bottom {stats['mean_label_r_bottom_quartile']:+.4f}")

    print("\n  [combined vs s146-only on test]")
    for model, stats in comparison.items():
        print(f"    {model:<9} s146 {stats['s146_only_net_r']:+8.2f}R -> "
              f"combined {stats['combined_net_r']:+8.2f}R  "
              f"(delta {stats['delta_net_r']:+.2f}R)")

    report = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": f"phase6_evaluation::{tag}",
        "dataset": str(dataset_path), "forecasts": str(forecast_path),
        "trades": {"train": int(len(train)), "test": int(len(test))},
        "min_kept_fraction": min_fraction,
        "test_unfiltered": before.as_dict(),
        "test_unfiltered_bootstrap": bootstrap_average_r(test_r),
        "feature_sets": {name: columns for name, columns in feature_sets.items()},
        "models": {result.name: result.as_dict() for result in results},
        "raw_kronos_rules": raw,
        "forecast_vs_outcome": diagnostics,
        "combined_vs_s146_only": comparison,
        "interpretation": [
            "Kronos only earns its place if combined beats s146_only by clearly "
            "more than the bootstrap width.",
            "Near-zero correlation in forecast_vs_outcome means the forecast has "
            "no information about these trades, whatever a fitted model claims.",
            "Rows are the trades the portfolio gates already admitted; skipping "
            "one would in reality free a slot, which is not modelled.",
        ],
    }
    output = paths.OUT / f"phase6_evaluation_{tag}.json"
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"\nReport: {output}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=paths.DATASET / "s146_signals.csv")
    parser.add_argument("--forecasts", type=Path,
                        default=paths.FORECASTS / "kronos_zeroshot.csv")
    parser.add_argument("--tag", default="zeroshot")
    parser.add_argument("--min-fraction", type=float, default=0.25)
    args = parser.parse_args(argv)
    for path in (args.dataset, args.forecasts):
        if not path.is_file():
            raise SystemExit(f"missing input: {path}")
    run(args.dataset, args.forecasts, args.tag, args.min_fraction)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
