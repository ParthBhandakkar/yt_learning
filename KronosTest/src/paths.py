"""Central paths, symbol basket and timeframe constants for the KronosTest study.

Everything this study writes stays under KronosTest. The existing project is
read-only from here: we import liveTrade/backtest primitives and read the
checked-in S146 replay artifacts, but never write outside this folder.
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- repository layout -------------------------------------------------------
KRONOS_TEST = Path(__file__).resolve().parents[1]
REPO = KRONOS_TEST.parent
LIVE = REPO / "liveTrade"
VENDOR_KRONOS = KRONOS_TEST / "vendor" / "Kronos"

# --- our own outputs (never outside KronosTest) -------------------------------
DATA = KRONOS_TEST / "data"
RAW = DATA / "raw"                    # phase 1: deep MT5 history
DATASET = DATA / "dataset"            # phase 2: labelled signal dataset
FORECASTS = DATA / "forecasts"        # phase 4: Kronos forecast features
MODELS = DATA / "models"              # phase 5: fine-tuned checkpoints
OUT = KRONOS_TEST / "out"             # reports, metrics, plots
LOGS = KRONOS_TEST / "logs"

# --- read-only inputs from the existing project ------------------------------
S146_RUNS = REPO / "data" / "s146_running_extreme" / "runs"
S146_RUN_ID = "s146-20260820T091500Z"   # the corrected checked-in one-year run
S146_RUN = S146_RUNS / S146_RUN_ID
S146_LEGACY_RAW = REPO / "data" / "s146_running_extreme" / "raw"

# --- timeframes --------------------------------------------------------------
# S146 uses 4h for the destination zone, 15m for the entry zone, and 5m for
# confirmation plus execution. All three feed Kronos as separate contexts.
TF_SECONDS = {"4h": 14400, "15m": 900, "5m": 300}
TIMEFRAMES = tuple(TF_SECONDS)

# --- symbol basket -----------------------------------------------------------
# The authoritative live S146 basket (liveTrade/run.py DEFAULT_SYMBOLS["s146"]).
# This is a superset of the 21 folders that already hold partial data, so the
# fine-tune corpus is as large as possible while staying strategy-relevant.
S146_BASKET = (
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF",
    "NZDJPY", "NZDCAD", "NZDCHF",
    "CADJPY", "CADCHF",
    "CHFJPY",
    "XAUUSD",
)

# The 7 pairs that have a full 4h/15m/5m one-year replay with labelled trades.
LABELLED_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD")


def ensure_dirs() -> None:
    for path in (DATA, RAW, DATASET, FORECASTS, MODELS, OUT, LOGS):
        path.mkdir(parents=True, exist_ok=True)


def add_repo_to_syspath() -> None:
    """Make liveTrade + backtests importable exactly the way the project does.

    liveTrade modules import a bare top-level `config`, so the package module is
    aliased into sys.modules the same way backtests/s146_running_extreme does.
    """
    for item in (str(REPO), str(LIVE)):
        if item not in sys.path:
            sys.path.insert(0, item)
    from liveTrade import config as live_config  # noqa: PLC0415
    sys.modules.setdefault("config", live_config)


def redirect_live_logs() -> None:
    """Point liveTrade's engine logger at KronosTest/logs.

    liveTrade.logging_setup builds a RotatingFileHandler into liveTrade/logs at
    import time. This study must not write anywhere outside KronosTest, so the
    file handler is swapped for one under our own logs folder. Console output is
    left alone.
    """
    import logging  # noqa: PLC0415
    from logging.handlers import RotatingFileHandler  # noqa: PLC0415

    LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("live.engine")
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler):
            logger.removeHandler(handler)
            handler.close()
    replacement = RotatingFileHandler(LOGS / "live_engine.log", maxBytes=5_000_000,
                                      backupCount=2, encoding="utf-8")
    replacement.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(replacement)


def add_kronos_to_syspath() -> None:
    """Make the vendored Kronos `model` package importable."""
    item = str(VENDOR_KRONOS)
    if item not in sys.path:
        sys.path.insert(0, item)


def raw_csv(symbol: str, timeframe: str, root: Path | None = None) -> Path:
    """Canonical raw bar path, matching the existing project layout."""
    base = RAW if root is None else root
    return base / symbol / timeframe / f"{symbol}_{timeframe}.csv"
