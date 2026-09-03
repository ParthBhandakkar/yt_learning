#!/usr/bin/env python3
"""Phase 8: did fine-tuning actually make the forecasts better?

The trade-level evaluation cannot answer this: with 467 test trades the noise
band is about +/-12R, wide enough to hide any real change. Forecast accuracy on
held-out bars has thousands of samples instead of hundreds, so it can.

Both models are scored on the *same* held-out windows, all of which start after
the fine-tune cutoff, so the fine-tuned model has never seen them.

Metrics, all computed on the context-standardised scale so symbols with very
different price levels are comparable:
  * MAE / RMSE of the predicted close path
  * directional accuracy at several horizons
  * coverage: how often the actual terminal close lands inside the sampled range
  * a naive random-walk baseline (predict the last close), because a forecast
    that cannot beat "no change" is not forecasting

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase8_forecast_accuracy.py --windows 400
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
from kronos_runner import context_frame, future_timestamps, load_symbol_bars  # noqa: E402

paths.ensure_dirs()
paths.add_kronos_to_syspath()
UTC = timezone.utc

HORIZONS = (1, 6, 12, 48, 96)


def read_cutoff(audit_path: Path) -> int:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    return int(audit["split"]["cutoff_epoch"])


def build_windows(symbols: list[str], cutoff_epoch: int, lookback: int, horizon: int,
                  windows_per_symbol: int, seed: int) -> list[dict[str, Any]]:
    """Held-out windows whose context starts at or after the fine-tune cutoff."""
    rng = np.random.default_rng(seed)
    jobs: list[dict[str, Any]] = []
    seconds = paths.TF_SECONDS["5m"]
    for symbol in symbols:
        path = paths.raw_csv(symbol, "5m")
        if not path.is_file():
            continue
        bars = load_symbol_bars(path)
        # First index whose whole context+horizon lies after the cutoff.
        first = int(np.searchsorted(bars.epochs, cutoff_epoch))
        lowest = first + lookback
        highest = len(bars) - horizon
        if highest - lowest < 2:
            continue
        picks = rng.choice(np.arange(lowest, highest),
                           size=min(windows_per_symbol, highest - lowest),
                           replace=False)
        for end_index in sorted(int(p) for p in picks):
            context = context_frame(bars, end_index, lookback)
            forward = future_timestamps(bars, end_index, horizon)
            if context is None or forward is None:
                continue
            frame, x_stamp = context
            actual = bars.close[end_index:end_index + horizon].copy()
            jobs.append({
                "symbol": symbol, "frame": frame, "x_stamp": x_stamp,
                "y_stamp": forward, "actual_close": actual,
                "last_close": float(bars.close[end_index - 1]),
                "context_std": float(np.std(bars.close[end_index - lookback:end_index])),
                "start_utc": pd.Timestamp(bars.epochs[end_index], unit="s", tz="UTC").isoformat(),
                "_seconds": seconds,
            })
    return jobs


def score_model(name: str, tokenizer_path: str, model_path: str, jobs: list[dict[str, Any]],
                horizon: int, lookback: int, paths_per_window: int, device: str,
                series_per_batch: int, temperature: float, top_p: float) -> dict[str, Any]:
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer

    print(f"\n=== scoring {name} ===")
    print(f"  tokenizer {tokenizer_path}")
    print(f"  model     {model_path}")
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_path)
    network = Kronos.from_pretrained(model_path)
    network.eval()
    predictor = KronosPredictor(network, tokenizer, device=device, max_context=lookback)

    autocast = (lambda: torch.autocast(device_type="cuda", dtype=torch.float16)) \
        if device.startswith("cuda") and torch.cuda.is_available() else None

    absolute: list[float] = []
    squared: list[float] = []
    naive_absolute: list[float] = []
    direction_hits: dict[int, list[float]] = {h: [] for h in HORIZONS}
    naive_direction: dict[int, list[float]] = {h: [] for h in HORIZONS}
    coverage: list[float] = []

    per_batch = max(1, series_per_batch // paths_per_window)
    for offset in range(0, len(jobs), per_batch):
        chunk = jobs[offset:offset + per_batch]
        frames, x_stamps, y_stamps, owners = [], [], [], []
        for index, job in enumerate(chunk):
            for _ in range(paths_per_window):
                frames.append(job["frame"])
                x_stamps.append(job["x_stamp"])
                y_stamps.append(job["y_stamp"])
                owners.append(index)
        try:
            context = autocast() if autocast else None
            if context is not None:
                with context:
                    predictions = predictor.predict_batch(
                        df_list=frames, x_timestamp_list=x_stamps,
                        y_timestamp_list=y_stamps, pred_len=horizon,
                        T=temperature, top_p=top_p, sample_count=1, verbose=False)
            else:
                predictions = predictor.predict_batch(
                    df_list=frames, x_timestamp_list=x_stamps,
                    y_timestamp_list=y_stamps, pred_len=horizon,
                    T=temperature, top_p=top_p, sample_count=1, verbose=False)
        except Exception as exc:
            print(f"  batch failed: {type(exc).__name__}: {exc}")
            continue

        grouped: dict[int, list[np.ndarray]] = {}
        for owner, prediction in zip(owners, predictions):
            grouped.setdefault(owner, []).append(prediction["close"].to_numpy(dtype=float))
        for index, job in enumerate(chunk):
            samples = grouped.get(index)
            if not samples:
                continue
            stack = np.vstack(samples)
            mean_path = stack.mean(axis=0)
            actual = job["actual_close"]
            scale = job["context_std"] if job["context_std"] > 0 else 1.0
            error = (mean_path - actual) / scale
            absolute.append(float(np.mean(np.abs(error))))
            squared.append(float(np.mean(error ** 2)))
            naive_absolute.append(float(np.mean(np.abs((job["last_close"] - actual) / scale))))
            for h in HORIZONS:
                if h > horizon:
                    continue
                actual_move = actual[h - 1] - job["last_close"]
                predicted_move = mean_path[h - 1] - job["last_close"]
                if actual_move == 0:
                    continue
                direction_hits[h].append(float(np.sign(predicted_move) == np.sign(actual_move)))
                naive_direction[h].append(0.5)
            low, high = stack[:, -1].min(), stack[:, -1].max()
            coverage.append(float(low <= actual[-1] <= high))
        done = min(offset + per_batch, len(jobs))
        if done % max(per_batch * 5, per_batch) == 0 or done == len(jobs):
            print(f"  {done}/{len(jobs)} windows")

    result = {
        "name": name, "tokenizer": tokenizer_path, "model": model_path,
        "windows_scored": len(absolute),
        "mae_normalized": round(float(np.mean(absolute)), 6) if absolute else None,
        "rmse_normalized": round(float(np.sqrt(np.mean(squared))), 6) if squared else None,
        "naive_last_close_mae": round(float(np.mean(naive_absolute)), 6) if naive_absolute else None,
        "skill_vs_naive": (round(1.0 - float(np.mean(absolute)) / float(np.mean(naive_absolute)), 6)
                           if absolute and np.mean(naive_absolute) > 0 else None),
        "terminal_coverage": round(float(np.mean(coverage)), 6) if coverage else None,
        "directional_accuracy": {
            str(h): round(float(np.mean(values)), 6) for h, values in direction_hits.items()
            if values},
    }
    del predictor, network, tokenizer
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    cutoff = read_cutoff(args.audit)
    symbols = [s.strip().upper() for value in (args.symbols or paths.LABELLED_SYMBOLS)
               for s in str(value).split(",") if s.strip()]
    symbols = list(dict.fromkeys(symbols))
    per_symbol = max(1, args.windows // max(1, len(symbols)))
    jobs = build_windows(symbols, cutoff, args.lookback, args.horizon, per_symbol, args.seed)
    print(f"held-out windows: {len(jobs):,} across {len(symbols)} symbol(s), "
          f"all starting after the fine-tune cutoff")

    candidates = [("zero_shot", args.pretrained_tokenizer, args.pretrained_predictor)]
    if args.finetuned_tokenizer and args.finetuned_predictor:
        if Path(args.finetuned_tokenizer).is_dir() and Path(args.finetuned_predictor).is_dir():
            candidates.append(("fine_tuned", args.finetuned_tokenizer, args.finetuned_predictor))
        else:
            print("fine-tuned checkpoints not found; scoring zero-shot only")

    results = [score_model(name, tokenizer, model, jobs, args.horizon, args.lookback,
                           args.paths_per_window, args.device, args.series_per_batch,
                           args.temperature, args.top_p)
               for name, tokenizer, model in candidates]

    print("\n=== comparison (lower MAE is better; skill>0 beats 'no change') ===")
    for result in results:
        print(f"  {result['name']:<11} MAE {result['mae_normalized']}  "
              f"naive {result['naive_last_close_mae']}  "
              f"skill {result['skill_vs_naive']}  "
              f"coverage {result['terminal_coverage']}")
        print(f"              directional: {result['directional_accuracy']}")

    report = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": "phase8_forecast_accuracy",
        "cutoff_epoch": cutoff, "symbols": symbols,
        "windows_requested": args.windows, "windows_built": len(jobs),
        "lookback": args.lookback, "horizon": args.horizon,
        "paths_per_window": args.paths_per_window,
        "temperature": args.temperature, "top_p": args.top_p, "seed": args.seed,
        "results": results,
        "how_to_read": [
            "skill_vs_naive > 0 means the forecast beats predicting no change. At or "
            "below 0 the model adds nothing over the last close.",
            "directional_accuracy near 0.5 means no directional information.",
            "Metrics are on the context-standardised scale so symbols with different "
            "price levels contribute comparably.",
            "All windows start after the fine-tune cutoff, so the fine-tuned model "
            "has not seen them.",
        ],
    }
    output = paths.OUT / "phase8_forecast_accuracy.json"
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"\nReport: {output}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audit", type=Path, default=paths.DATASET / "s146_signals_audit.json")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--windows", type=int, default=350)
    parser.add_argument("--lookback", type=int, default=512)
    parser.add_argument("--horizon", type=int, default=96)
    parser.add_argument("--paths-per-window", type=int, default=10)
    parser.add_argument("--series-per-batch", type=int, default=360)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--pretrained-tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--pretrained-predictor", default="NeoQuasar/Kronos-small")
    parser.add_argument("--finetuned-tokenizer",
                        default=str(paths.MODELS / "s146_5m" / "tokenizer" / "best_model"))
    parser.add_argument("--finetuned-predictor",
                        default=str(paths.MODELS / "s146_5m" / "basemodel" / "best_model"))
    args = parser.parse_args(argv)
    if not args.audit.is_file():
        raise SystemExit(f"audit not found: {args.audit}")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
