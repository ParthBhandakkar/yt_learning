"""Multi-symbol K-line dataset for Kronos fine-tuning.

The vendor's ``CustomKlineDataset`` reads one CSV, sorts it by timestamp and
slides a window across consecutive rows. Pointing it at a concatenation of 29
symbols would interleave instruments at shared timestamps and every training
window would be a mixture, so this class keeps each symbol separate and only
emits windows that lie wholly inside one symbol's series.

Everything else matches the vendor exactly, so the model sees identically shaped
and identically normalised batches:
  * feature order ``[open, high, low, close, volume, amount]``
  * time features ``[minute, hour, weekday, day, month]``
  * per-window standardisation then clipping to +/- clip
  * window length ``lookback_window + predict_window + 1``
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

FEATURES = ["open", "high", "low", "close", "volume", "amount"]
TIME_FEATURES = ["minute", "hour", "weekday", "day", "month"]


class MultiSymbolKlineDataset(Dataset):
    """Windows drawn from many per-symbol CSVs without crossing symbols.

    Each symbol is split chronologically by ``train_ratio``/``val_ratio`` so the
    validation windows always sit after the training windows for that symbol.
    """

    def __init__(self, corpus_dir: str | Path, data_type: str = "train",
                 lookback_window: int = 512, predict_window: int = 96,
                 clip: float = 5.0, seed: int = 100, train_ratio: float = 0.9,
                 val_ratio: float = 0.1, test_ratio: float = 0.0,
                 max_windows: int | None = None) -> None:
        self.corpus_dir = Path(corpus_dir)
        self.data_type = data_type
        self.lookback_window = int(lookback_window)
        self.predict_window = int(predict_window)
        self.window = self.lookback_window + self.predict_window + 1
        self.clip = float(clip)
        self.seed = int(seed)
        self.train_ratio = float(train_ratio)
        self.val_ratio = float(val_ratio)
        self.test_ratio = float(test_ratio)
        self.current_epoch = 0

        self.blocks: list[np.ndarray] = []
        self.stamp_blocks: list[np.ndarray] = []
        self.index: list[tuple[int, int]] = []   # (block, start row)
        self.symbols: list[str] = []

        self._load()
        if max_windows is not None and len(self.index) > max_windows:
            stride = int(np.ceil(len(self.index) / max_windows))
            self.index = self.index[::stride][:max_windows]
        print(f"[{data_type.upper()}] symbols={len(self.symbols)} "
              f"windows={len(self.index):,} window_len={self.window}")

    # ------------------------------------------------------------------ load
    def _files(self) -> list[Path]:
        manifest = self.corpus_dir / "corpus.json"
        if manifest.is_file():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            names = [entry["symbol"] for entry in (payload.get("files") or {}).values()
                     if not entry.get("error")]
            files = [self.corpus_dir / f"{name}.csv" for name in sorted(names)]
            return [path for path in files if path.is_file()]
        return sorted(self.corpus_dir.glob("*.csv"))

    def _load(self) -> None:
        for path in self._files():
            frame = pd.read_csv(path)
            if frame.empty or not set(FEATURES[:4]).issubset(frame.columns):
                continue
            frame["timestamps"] = pd.to_datetime(frame["timestamps"])
            frame = frame.sort_values("timestamps").reset_index(drop=True)
            if "volume" not in frame:
                frame["volume"] = 0.0
            if "amount" not in frame:
                frame["amount"] = 0.0
            stamps = frame["timestamps"].dt
            time_frame = pd.DataFrame({
                "minute": stamps.minute, "hour": stamps.hour,
                "weekday": stamps.weekday, "day": stamps.day, "month": stamps.month,
            })
            values = frame[FEATURES].ffill().to_numpy(dtype=np.float32)
            if not np.isfinite(values).all():
                values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)

            total = len(values)
            train_end = int(total * self.train_ratio)
            val_end = int(total * (self.train_ratio + self.val_ratio))
            if self.data_type == "train":
                lower, upper = 0, train_end
            elif self.data_type == "val":
                lower, upper = train_end, val_end
            else:
                lower, upper = val_end, total
            if upper - lower < self.window:
                continue

            block = len(self.blocks)
            self.blocks.append(values[lower:upper])
            self.stamp_blocks.append(time_frame.iloc[lower:upper].to_numpy(dtype=np.float32))
            self.symbols.append(path.stem)
            starts = (upper - lower) - self.window + 1
            self.index.extend((block, start) for start in range(starts))

    # ---------------------------------------------------------------- access
    def set_epoch_seed(self, epoch: int) -> None:
        """Vendor training loops call this each epoch; kept for compatibility."""
        self.current_epoch = int(epoch)

    def __len__(self) -> int:
        return len(self.index)

    def __getitem__(self, idx: int):
        block, start = self.index[idx % len(self.index)]
        end = start + self.window
        values = self.blocks[block][start:end]
        stamps = self.stamp_blocks[block][start:end]

        mean = values.mean(axis=0)
        std = values.std(axis=0)
        normalized = (values - mean) / (std + 1e-5)
        normalized = np.clip(normalized, -self.clip, self.clip)
        return torch.from_numpy(normalized.astype(np.float32)), torch.from_numpy(stamps)


__all__ = ["MultiSymbolKlineDataset", "FEATURES", "TIME_FEATURES"]
