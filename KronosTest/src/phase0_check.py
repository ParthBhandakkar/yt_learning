#!/usr/bin/env python3
"""Phase 0 verification: environment, GPU, Kronos import, and read-only inputs.

Run:
    .venv\\Scripts\\python.exe src\\phase0_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

paths.ensure_dirs()

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


print("=== Phase 0: environment check ===")
print(f"python {sys.version.split()[0]}")

# --- torch + GPU ------------------------------------------------------------
try:
    import torch

    cuda = torch.cuda.is_available()
    name = torch.cuda.get_device_name(0) if cuda else "n/a"
    check("torch installed", True, f"{torch.__version__}")
    check("CUDA available", cuda, name)
    if cuda:
        total = torch.cuda.get_device_properties(0).total_memory / 1024**3
        # A tiny real matmul confirms the driver/kernel path, not just the flag.
        probe = (torch.randn(512, 512, device="cuda") @ torch.randn(512, 512, device="cuda")).sum().item()
        check("GPU compute works", probe == probe, f"{total:.1f} GB VRAM")
except Exception as exc:
    check("torch installed", False, f"{type(exc).__name__}: {exc}")

# --- Kronos model code ------------------------------------------------------
try:
    paths.add_kronos_to_syspath()
    from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: F401

    check("Kronos model package imports", True, str(paths.VENDOR_KRONOS.name))
except Exception as exc:
    check("Kronos model package imports", False, f"{type(exc).__name__}: {exc}")

# --- project inputs (read-only) --------------------------------------------
check("S146 run folder exists", paths.S146_RUN.is_dir(), str(paths.S146_RUN))
for name in ("trades.csv", "summary.json", "detector_signals.jsonl"):
    check(f"S146 {name}", (paths.S146_RUN / name).is_file())
check("legacy 1y raw bars", paths.S146_LEGACY_RAW.is_dir())

# --- liveTrade / backtest primitives importable ----------------------------
try:
    paths.add_repo_to_syspath()
    paths.redirect_live_logs()
    from liveTrade.detection_s146 import live_params

    params = live_params()
    check("liveTrade detection_s146 imports", True,
          f"max_wait_bars_5m={getattr(params, 'max_wait_bars_5m', '?')}")
except Exception as exc:
    check("liveTrade detection_s146 imports", False, f"{type(exc).__name__}: {exc}")

# --- our own writable folders ---------------------------------------------
for label, path in (("data", paths.DATA), ("out", paths.OUT), ("logs", paths.LOGS)):
    check(f"writable {label}", path.is_dir(), str(path))

print("\n=== Result ===")
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    raise SystemExit(1)
print("All Phase 0 checks passed.")
