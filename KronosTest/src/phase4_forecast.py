#!/usr/bin/env python3
"""Phase 4: generate Kronos forecast features for every labelled S146 signal.

For each signal we take the 5m bars that had closed by the signal timestamp, draw
many independent Kronos sample paths, and replay S146's stop/target along each
path. The resulting probabilities and R statistics become ``k_*`` features.

KronosPredictor.predict averages its samples internally, which would throw away
exactly the distribution we want, so each path is requested as its own series
via predict_batch and grouped afterwards.

Resumable: completed signals are appended to the output CSV and skipped on rerun.

Usage (from KronosTest):
    .venv\\Scripts\\python.exe src\\phase4_forecast.py --limit 32     # smoke test
    .venv\\Scripts\\python.exe src\\phase4_forecast.py
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from kronos_runner import (  # noqa: E402
    KRONOS_FEATURES, aggregate_paths, context_frame, future_timestamps,
    load_symbol_bars, score_path,
)

paths.ensure_dirs()
paths.add_kronos_to_syspath()

UTC = timezone.utc

DEFAULT_TOKENIZER = "NeoQuasar/Kronos-Tokenizer-base"
DEFAULT_MODEL = "NeoQuasar/Kronos-small"
LOOKBACK_5M = 512
HORIZON_5M = 96          # 8h: resolves 91.3% of this run's trades
SAMPLE_PATHS = 30
SERIES_PER_BATCH = 60    # ~2 signals per call at 30 paths; fits 12GB comfortably

OUTPUT_COLUMNS = ["meta_trade_id", "meta_symbol", "meta_signal_time_utc",
                  *KRONOS_FEATURES, "k_error"]

# Replaced by _make_autocast() at the start of run().
def _autocast():  # pragma: no cover - trivial default
    import contextlib
    return contextlib.nullcontext()


def load_predictor(tokenizer_path: str, model_path: str, device: str, max_context: int):
    from model import Kronos, KronosPredictor, KronosTokenizer

    tokenizer = KronosTokenizer.from_pretrained(tokenizer_path)
    model = Kronos.from_pretrained(model_path)
    model.eval()
    return KronosPredictor(model, tokenizer, device=device, max_context=max_context)


def existing_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {row["meta_trade_id"] for row in csv.DictReader(handle)
                if row.get("meta_trade_id")}


def _make_autocast(device: str, amp: bool):
    """float16 autocast for the sampling loop, or a no-op when disabled.

    Sampling is already stochastic and the tokenizer quantises to 10-bit
    codebooks, so reduced precision inside attention does not meaningfully
    change the path distribution while roughly halving the runtime.

    float16 rather than bfloat16: the vendor's inference converts the decoded
    tensor straight to numpy, which has no bfloat16 dtype.
    """
    import contextlib

    import torch

    if not amp or not device.startswith("cuda") or not torch.cuda.is_available():
        return contextlib.nullcontext
    return lambda: torch.autocast(device_type="cuda", dtype=torch.float16)


def run(dataset_path: Path, output_path: Path, tokenizer_path: str, model_path: str,
        device: str, sample_paths: int, horizon: int, lookback: int,
        series_per_batch: int, limit: int | None, temperature: float,
        top_p: float, seed: int, amp: bool = True) -> dict[str, Any]:
    import torch

    torch.manual_seed(seed)
    np.random.seed(seed)
    global _autocast
    _autocast = _make_autocast(device, amp)
    if device.startswith("cuda"):
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

    frame = pd.read_csv(dataset_path)
    frame = frame[frame["label_r"].notna()].copy()
    frame = frame[frame["meta_context_ok_5m"] == 1]
    frame = frame.sort_values("meta_signal_epoch").reset_index(drop=True)
    done = existing_ids(output_path)
    if done:
        print(f"resuming: {len(done):,} signal(s) already forecast")
        frame = frame[~frame["meta_trade_id"].isin(done)].reset_index(drop=True)
    if limit is not None:
        frame = frame.head(limit).copy()
    if frame.empty:
        print("nothing to do")
        return {"processed": 0}

    print(f"forecasting {len(frame):,} signal(s)")
    print(f"  model={model_path}  paths={sample_paths}  horizon={horizon} bars"
          f"  lookback={lookback}  device={device}")

    bars_cache: dict[str, Any] = {}
    predictor = load_predictor(tokenizer_path, model_path, device, lookback)

    new_file = not output_path.is_file()
    handle = output_path.open("a", encoding="utf-8", newline="")
    writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
    if new_file:
        writer.writeheader()

    seconds_5m = paths.TF_SECONDS["5m"]
    processed = 0
    failed = 0
    started = time.time()
    pending: list[dict[str, Any]] = []

    def flush(batch: list[dict[str, Any]]) -> None:
        nonlocal processed, failed
        if not batch:
            return
        frames: list[pd.DataFrame] = []
        x_stamps: list[pd.Series] = []
        y_stamps: list[pd.Series] = []
        owners: list[int] = []
        for index, job in enumerate(batch):
            for _ in range(sample_paths):
                frames.append(job["frame"])
                x_stamps.append(job["x_stamp"])
                y_stamps.append(job["y_stamp"])
                owners.append(index)
        try:
            with _autocast():
                predictions = predictor.predict_batch(
                    df_list=frames, x_timestamp_list=x_stamps, y_timestamp_list=y_stamps,
                    pred_len=horizon, T=temperature, top_p=top_p, sample_count=1,
                    verbose=False)
        except Exception as exc:  # keep going; record which signals failed
            for job in batch:
                writer.writerow({"meta_trade_id": job["trade_id"],
                                 "meta_symbol": job["symbol"],
                                 "meta_signal_time_utc": job["signal_time_utc"],
                                 "k_error": f"{type(exc).__name__}: {exc}"})
                failed += 1
            handle.flush()
            print(f"  batch failed: {type(exc).__name__}: {exc}")
            return

        grouped: dict[int, list[dict[str, float]]] = {}
        for owner, prediction in zip(owners, predictions):
            job = batch[owner]
            scores = score_path(
                prediction["high"].to_numpy(dtype=float),
                prediction["low"].to_numpy(dtype=float),
                prediction["close"].to_numpy(dtype=float),
                job["direction"], job["entry"], job["stop"], job["target"])
            if scores:
                grouped.setdefault(owner, []).append(scores)
        for index, job in enumerate(batch):
            aggregated = aggregate_paths(grouped.get(index, []))
            row = {"meta_trade_id": job["trade_id"], "meta_symbol": job["symbol"],
                   "meta_signal_time_utc": job["signal_time_utc"]}
            if aggregated:
                row.update({key: round(value, 8) for key, value in aggregated.items()})
                processed += 1
            else:
                row["k_error"] = "no_scorable_paths"
                failed += 1
            writer.writerow(row)
        handle.flush()

    try:
        for record in frame.to_dict("records"):
            symbol = record["meta_symbol"]
            if symbol not in bars_cache:
                bars_cache[symbol] = load_symbol_bars(paths.raw_csv(symbol, "5m"))
            bars = bars_cache[symbol]
            signal_epoch = int(record["meta_signal_epoch"])
            end_index = bars.closed_before(signal_epoch, seconds_5m)
            context = context_frame(bars, end_index, lookback)
            forward = future_timestamps(bars, end_index, horizon)
            if context is None or forward is None:
                writer.writerow({"meta_trade_id": record["meta_trade_id"],
                                 "meta_symbol": symbol,
                                 "meta_signal_time_utc": record["meta_signal_time_utc"],
                                 "k_error": "insufficient_bars"})
                failed += 1
                continue
            context_frame_df, x_stamp = context
            direction = 1 if str(record["meta_direction"]).lower() == "long" else -1
            entry = float(record["meta_trigger"])
            stop = float(record["meta_effective_stop"])
            target = float(record["meta_destination_target"])
            if direction * (entry - stop) <= 0:
                writer.writerow({"meta_trade_id": record["meta_trade_id"],
                                 "meta_symbol": symbol,
                                 "meta_signal_time_utc": record["meta_signal_time_utc"],
                                 "k_error": "invalid_risk"})
                failed += 1
                continue
            pending.append({
                "trade_id": record["meta_trade_id"], "symbol": symbol,
                "signal_time_utc": record["meta_signal_time_utc"],
                "frame": context_frame_df, "x_stamp": x_stamp, "y_stamp": forward,
                "direction": direction, "entry": entry, "stop": stop, "target": target,
            })
            if len(pending) * sample_paths >= series_per_batch:
                flush(pending)
                pending = []
                elapsed = time.time() - started
                rate = processed / elapsed if elapsed > 0 else 0.0
                remaining = len(frame) - processed - failed
                print(f"  {processed:,} done, {failed} failed, {rate:.2f}/s, "
                      f"~{remaining / rate / 60:.1f} min left" if rate > 0 else
                      f"  {processed:,} done, {failed} failed")
        flush(pending)
    finally:
        handle.close()

    elapsed = time.time() - started
    summary = {
        "created_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "phase": "phase4_kronos_zero_shot",
        "tokenizer": tokenizer_path, "model": model_path,
        "sample_paths": sample_paths, "horizon_bars_5m": horizon,
        "lookback_bars_5m": lookback, "temperature": temperature, "top_p": top_p,
        "seed": seed, "processed": processed, "failed": failed,
        "elapsed_seconds": round(elapsed, 1),
        "output": str(output_path),
        "barrier_model": (
            "entry = signal trigger, stop = effective stop, target = 4h destination "
            "proximal; all three are signal-time values. Stop-first when a forecast "
            "bar spans both, matching the replay's pessimism."),
        "simplifications": [
            "The live ladder (partial at 1.25R, breakeven, 0.5R rungs with giveback) "
            "is not replayed inside forecast paths; a plain stop/target barrier is "
            "used, so k_expected_r is not directly comparable to label_r.",
            "Future bar timestamps come from the real calendar so weekend and "
            "holiday gaps are respected; only timestamps are used, never prices.",
        ],
    }
    report = paths.OUT / "phase4_forecast_summary.json"
    report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nprocessed {processed:,}  failed {failed}  in {elapsed / 60:.1f} min")
    print(f"Features: {output_path}")
    print(f"Summary : {report}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=paths.DATASET / "s146_signals.csv")
    parser.add_argument("--output", type=Path,
                        default=paths.FORECASTS / "kronos_zeroshot.csv")
    parser.add_argument("--tokenizer", default=DEFAULT_TOKENIZER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--paths-per-signal", type=int, default=SAMPLE_PATHS)
    parser.add_argument("--horizon", type=int, default=HORIZON_5M)
    parser.add_argument("--lookback", type=int, default=LOOKBACK_5M)
    parser.add_argument("--series-per-batch", type=int, default=SERIES_PER_BATCH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-amp", action="store_true",
                        help="disable bfloat16 autocast in the sampling loop")
    args = parser.parse_args(argv)
    if not args.dataset.is_file():
        raise SystemExit(f"dataset not found: {args.dataset}. Run phase2_dataset.py first.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run(args.dataset, args.output, args.tokenizer, args.model, args.device,
        args.paths_per_signal, args.horizon, args.lookback, args.series_per_batch,
        args.limit, args.temperature, args.top_p, args.seed, amp=not args.no_amp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
