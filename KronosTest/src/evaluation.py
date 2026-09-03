"""Shared trade-filter evaluation.

Everything a filter claims has to survive these numbers, so the metrics live in
one place and are reused by the baseline phase and the Kronos phases.

Design choices that matter:
  * Thresholds are always chosen on the training split only.
  * Improvements are reported with a bootstrap interval, because a few hundred
    test trades can easily move net R by tens of R on luck alone.
  * A label-shuffle control is available so a pipeline bug that manufactures
    edge is visible instead of celebrated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np


@dataclass
class TradeStats:
    trades: int
    net_r: float
    average_r: float
    win_rate: float
    profit_factor: float
    max_drawdown_r: float
    gross_win_r: float
    gross_loss_r: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "trades": self.trades,
            "net_r": round(self.net_r, 6),
            "average_r": round(self.average_r, 6),
            "win_rate": round(self.win_rate, 6),
            "profit_factor": (None if not math.isfinite(self.profit_factor)
                              else round(self.profit_factor, 6)),
            "max_drawdown_r": round(self.max_drawdown_r, 6),
            "gross_win_r": round(self.gross_win_r, 6),
            "gross_loss_r": round(self.gross_loss_r, 6),
        }


def summarize(returns: Sequence[float]) -> TradeStats:
    values = np.asarray([r for r in returns if r is not None and math.isfinite(r)], dtype=float)
    if values.size == 0:
        return TradeStats(0, 0.0, 0.0, 0.0, float("nan"), 0.0, 0.0, 0.0)
    wins = values[values > 0]
    losses = values[values < 0]
    equity = np.cumsum(values)
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
    drawdown = peak - equity
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    return TradeStats(
        trades=int(values.size),
        net_r=float(values.sum()),
        average_r=float(values.mean()),
        win_rate=float((values > 0).mean()),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        max_drawdown_r=float(drawdown.max()) if drawdown.size else 0.0,
        gross_win_r=gross_win,
        gross_loss_r=gross_loss,
    )


def bootstrap_average_r(returns: Sequence[float], iterations: int = 10_000,
                        seed: int = 7) -> dict[str, float | None]:
    """Percentile interval for mean R, so 'improvement' can be judged against noise."""
    values = np.asarray([r for r in returns if r is not None and math.isfinite(r)], dtype=float)
    if values.size < 2:
        return {"mean": None, "lower_5pct": None, "upper_95pct": None,
                "prob_mean_positive": None}
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(iterations, values.size), replace=True).mean(axis=1)
    return {
        "mean": round(float(values.mean()), 6),
        "lower_5pct": round(float(np.percentile(draws, 5)), 6),
        "upper_95pct": round(float(np.percentile(draws, 95)), 6),
        "prob_mean_positive": round(float((draws > 0).mean()), 6),
    }


def choose_threshold(scores: Sequence[float], returns: Sequence[float],
                     min_fraction: float = 0.25,
                     grid: int = 200) -> dict[str, Any]:
    """Pick the score cut that maximises net R on the data given (train only).

    ``min_fraction`` stops the search from selecting a threshold that keeps a
    handful of trades, which is the classic way an in-sample filter looks
    brilliant and then means nothing out of sample.
    """
    score_array = np.asarray(scores, dtype=float)
    return_array = np.asarray(returns, dtype=float)
    valid = np.isfinite(score_array) & np.isfinite(return_array)
    score_array, return_array = score_array[valid], return_array[valid]
    if score_array.size == 0:
        return {"threshold": float("-inf"), "train_net_r": 0.0, "kept": 0, "kept_fraction": 0.0}

    minimum_kept = max(1, int(math.ceil(min_fraction * score_array.size)))
    candidates = np.unique(np.quantile(score_array, np.linspace(0.0, 1.0, grid)))
    best = {"threshold": float(candidates[0]), "train_net_r": float(return_array.sum()),
            "kept": int(score_array.size), "kept_fraction": 1.0}
    for candidate in candidates:
        mask = score_array >= candidate
        kept = int(mask.sum())
        if kept < minimum_kept:
            continue
        net = float(return_array[mask].sum())
        if net > best["train_net_r"]:
            best = {"threshold": float(candidate), "train_net_r": net, "kept": kept,
                    "kept_fraction": round(kept / score_array.size, 6)}
    return best


def group_breakdown(keys: Iterable[Any], returns: Sequence[float],
                    kept: Sequence[bool]) -> dict[str, dict[str, Any]]:
    """Per-group before/after view, to expose edge concentrated in one bucket."""
    buckets: dict[str, dict[str, list[float]]] = {}
    for key, value, keep in zip(keys, returns, kept):
        if value is None or not math.isfinite(value):
            continue
        bucket = buckets.setdefault(str(key), {"all": [], "kept": []})
        bucket["all"].append(value)
        if keep:
            bucket["kept"].append(value)
    result: dict[str, dict[str, Any]] = {}
    for name, bucket in sorted(buckets.items()):
        before = summarize(bucket["all"])
        after = summarize(bucket["kept"])
        result[name] = {
            "before": before.as_dict(), "after": after.as_dict(),
            "delta_net_r": round(after.net_r - before.net_r, 6),
        }
    return result


@dataclass
class FilterResult:
    name: str
    threshold: float
    train: dict[str, Any] = field(default_factory=dict)
    test_before: dict[str, Any] = field(default_factory=dict)
    test_after: dict[str, Any] = field(default_factory=dict)
    test_bootstrap_after: dict[str, Any] = field(default_factory=dict)
    test_bootstrap_before: dict[str, Any] = field(default_factory=dict)
    kept_fraction: float = 0.0
    by_symbol: dict[str, Any] = field(default_factory=dict)
    by_month: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "threshold": round(self.threshold, 6),
            "kept_fraction": round(self.kept_fraction, 6),
            "train": self.train,
            "test_before": self.test_before, "test_after": self.test_after,
            "test_bootstrap_before": self.test_bootstrap_before,
            "test_bootstrap_after": self.test_bootstrap_after,
            "by_symbol": self.by_symbol, "by_month": self.by_month,
            "notes": self.notes,
        }


def evaluate_filter(name: str, train_scores: Sequence[float], train_returns: Sequence[float],
                    test_scores: Sequence[float], test_returns: Sequence[float],
                    test_symbols: Sequence[str], test_months: Sequence[str],
                    min_fraction: float = 0.25) -> FilterResult:
    chosen = choose_threshold(train_scores, train_returns, min_fraction=min_fraction)
    threshold = chosen["threshold"]

    test_score_array = np.asarray(test_scores, dtype=float)
    keep = test_score_array >= threshold
    kept_returns = [r for r, k in zip(test_returns, keep) if k]

    before = summarize(test_returns)
    after = summarize(kept_returns)
    return FilterResult(
        name=name,
        threshold=threshold,
        train={"chosen_on": "train_split_only", **chosen},
        test_before=before.as_dict(),
        test_after=after.as_dict(),
        test_bootstrap_before=bootstrap_average_r(test_returns),
        test_bootstrap_after=bootstrap_average_r(kept_returns),
        kept_fraction=(float(keep.mean()) if keep.size else 0.0),
        by_symbol=group_breakdown(test_symbols, test_returns, list(keep)),
        by_month=group_breakdown(test_months, test_returns, list(keep)),
    )


def print_result(result: FilterResult) -> None:
    before, after = result.test_before, result.test_after
    print(f"\n  [{result.name}]  threshold={result.threshold:.4f}  "
          f"kept={result.kept_fraction:.1%}")
    print(f"    test before: {before['trades']:>4} trades  net {before['net_r']:+8.2f}R  "
          f"win {before['win_rate']:.1%}  avg {before['average_r']:+.4f}R")
    print(f"    test after : {after['trades']:>4} trades  net {after['net_r']:+8.2f}R  "
          f"win {after['win_rate']:.1%}  avg {after['average_r']:+.4f}R")
    boot = result.test_bootstrap_after
    if boot.get("mean") is not None:
        print(f"    avg R 90% interval [{boot['lower_5pct']:+.4f}, {boot['upper_95pct']:+.4f}]  "
              f"P(mean>0)={boot['prob_mean_positive']:.1%}")
    positive_symbols = sum(1 for stats in result.by_symbol.values()
                           if stats["after"]["net_r"] > 0)
    print(f"    symbols net-positive after filter: {positive_symbols}/{len(result.by_symbol)}")


__all__ = ["TradeStats", "summarize", "bootstrap_average_r", "choose_threshold",
           "group_breakdown", "FilterResult", "evaluate_filter", "print_result"]
