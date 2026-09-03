"""Kronos forecasting helpers: bar loading, sampled paths, barrier scoring.

The useful thing about Kronos for this study is that it samples rather than
producing one number. Drawing many paths and replaying S146's own stop/target
against each gives an estimated probability that the trade works, expressed in
the same R units the replay already uses.

Leakage rules enforced here:
  * Context bars must CLOSE at or before the signal timestamp.
  * Entry and risk come from signal-time fields (trigger, effective stop), never
    from the realised fill.
  * Future bar timestamps are used only as a calendar (which bars exist), never
    their prices. A live engine knows the session calendar too.
"""
from __future__ import annotations

import bisect
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

EPS = 1e-12


@dataclass
class SymbolBars:
    """Bar arrays for one symbol/timeframe, sorted by bar-open epoch."""
    epochs: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return int(self.epochs.size)

    def closed_before(self, epoch: int, seconds: int) -> int:
        """Index count of bars whose close is at or before ``epoch``."""
        return bisect.bisect_right(self.epochs.tolist(), epoch - seconds)


def load_symbol_bars(path: Path) -> SymbolBars:
    epochs: list[int] = []
    values: list[tuple[float, float, float, float, float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                epoch = int(float(row["time"]))
                candle = (float(row["open"]), float(row["high"]),
                          float(row["low"]), float(row["close"]),
                          float(row.get("tick_volume") or 0.0))
            except (KeyError, TypeError, ValueError):
                continue
            epochs.append(epoch)
            values.append(candle)
        order = np.argsort(np.asarray(epochs))
    array = np.asarray(values, dtype=np.float64)
    return SymbolBars(
        epochs=np.asarray(epochs, dtype=np.int64)[order],
        open=array[order, 0], high=array[order, 1], low=array[order, 2],
        close=array[order, 3], volume=array[order, 4],
    )


def context_frame(bars: SymbolBars, end_index: int, lookback: int
                  ) -> tuple[pd.DataFrame, pd.Series] | None:
    """Kronos input frame for the ``lookback`` bars ending at ``end_index`` (exclusive)."""
    start = end_index - lookback
    if start < 0 or end_index > len(bars):
        return None
    frame = pd.DataFrame({
        "open": bars.open[start:end_index],
        "high": bars.high[start:end_index],
        "low": bars.low[start:end_index],
        "close": bars.close[start:end_index],
        "volume": bars.volume[start:end_index],
    })
    stamps = pd.to_datetime(bars.epochs[start:end_index], unit="s", utc=True)
    return frame, pd.Series(stamps)


def future_timestamps(bars: SymbolBars, start_index: int, horizon: int) -> pd.Series | None:
    if start_index + horizon > len(bars):
        return None
    stamps = pd.to_datetime(bars.epochs[start_index:start_index + horizon], unit="s", utc=True)
    return pd.Series(stamps)


def score_path(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               direction: int, entry: float, stop: float, target: float
               ) -> dict[str, float]:
    """Replay S146's barriers along one forecast path.

    Uses the replay's own pessimism: when a bar spans both the stop and the
    target, the stop is taken first.
    """
    risk = direction * (entry - stop)
    if risk <= EPS:
        return {}
    reward_r = direction * (target - entry) / risk

    favorable = high if direction > 0 else low
    adverse = low if direction > 0 else high
    favorable_r = direction * (favorable - entry) / risk
    adverse_r = direction * (adverse - entry) / risk

    hit_stop = adverse_r <= -1.0 + EPS
    hit_target = favorable_r >= reward_r - EPS
    stop_index = int(np.argmax(hit_stop)) if hit_stop.any() else -1
    target_index = int(np.argmax(hit_target)) if hit_target.any() else -1

    if stop_index >= 0 and (target_index < 0 or stop_index <= target_index):
        outcome, realized, bars_to_exit = "stop", -1.0, stop_index + 1
    elif target_index >= 0:
        outcome, realized, bars_to_exit = "target", reward_r, target_index + 1
    else:
        outcome = "timeout"
        realized = float(direction * (close[-1] - entry) / risk)
        bars_to_exit = len(close)

    running_mfe = float(np.maximum.accumulate(favorable_r)[min(bars_to_exit, len(favorable_r)) - 1])
    running_mae = float(np.minimum.accumulate(adverse_r)[min(bars_to_exit, len(adverse_r)) - 1])
    return {
        "target_first": 1.0 if outcome == "target" else 0.0,
        "stop_first": 1.0 if outcome == "stop" else 0.0,
        "timeout": 1.0 if outcome == "timeout" else 0.0,
        "realized_r": realized,
        "mfe_r": running_mfe,
        "mae_r": running_mae,
        "terminal_r": float(direction * (close[-1] - entry) / risk),
        "bars_to_exit": float(bars_to_exit),
        "reward_r": float(reward_r),
    }


def aggregate_paths(scores: Sequence[dict[str, float]]) -> dict[str, float | None]:
    """Turn per-path outcomes into the ``k_*`` feature block."""
    usable = [item for item in scores if item]
    if not usable:
        return {}
    def column(name: str) -> np.ndarray:
        return np.asarray([item[name] for item in usable], dtype=float)

    realized = column("realized_r")
    terminal = column("terminal_r")
    return {
        "k_paths": float(len(usable)),
        "k_p_target_first": float(column("target_first").mean()),
        "k_p_stop_first": float(column("stop_first").mean()),
        "k_p_timeout": float(column("timeout").mean()),
        "k_expected_r": float(realized.mean()),
        "k_median_r": float(np.median(realized)),
        "k_expected_r_p25": float(np.percentile(realized, 25)),
        "k_mean_mfe_r": float(column("mfe_r").mean()),
        "k_mean_mae_r": float(column("mae_r").mean()),
        "k_mean_terminal_r": float(terminal.mean()),
        "k_p_terminal_favorable": float((terminal > 0).mean()),
        "k_dispersion_r": float(terminal.std()),
        "k_mean_bars_to_exit": float(column("bars_to_exit").mean()),
        "k_reward_r": float(column("reward_r").mean()),
        # Odds ratio of the two barriers; the single most direct "is this trade
        # worth taking" summary the forecast can give.
        "k_target_stop_ratio": float(
            (column("target_first").mean() + 1e-6) / (column("stop_first").mean() + 1e-6)),
    }


KRONOS_FEATURES = (
    "k_paths", "k_p_target_first", "k_p_stop_first", "k_p_timeout",
    "k_expected_r", "k_median_r", "k_expected_r_p25", "k_mean_mfe_r",
    "k_mean_mae_r", "k_mean_terminal_r", "k_p_terminal_favorable",
    "k_dispersion_r", "k_mean_bars_to_exit", "k_reward_r", "k_target_stop_ratio",
)

__all__ = ["SymbolBars", "load_symbol_bars", "context_frame", "future_timestamps",
           "score_path", "aggregate_paths", "KRONOS_FEATURES"]
