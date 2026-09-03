#!/usr/bin/env python3
"""Phase 1 diagnostic: why intraday history is shallow.

Checks the terminal's own max-bars limit and whether MT5 extends 5m/15m history
when the same older window is requested repeatedly (MT5 downloads history
asynchronously, so a first empty answer is not proof of absence).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

paths.ensure_dirs()
paths.add_repo_to_syspath()

from liveTrade.mt5_client import MT5Client, mt5  # noqa: E402

paths.redirect_live_logs()

UTC = timezone.utc


def main() -> int:
    client = MT5Client(magic=1460146)
    if not client.connect():
        raise SystemExit("MT5 connection failed")
    try:
        info = mt5.terminal_info()
        print("--- terminal ---")
        for field in ("maxbars", "build", "data_path", "connected", "dlls_allowed"):
            print(f"  {field:<14} {getattr(info, field, 'n/a')}")

        broker = client.resolve_symbol("EURUSD")
        print(f"\n--- EURUSD ({broker}) retry probe ---")
        constants = {"5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15}
        # A window that the first probe reported as empty for 5m.
        window_start = datetime(2025, 3, 1, tzinfo=UTC)
        window_end = window_start + timedelta(days=7)
        for label, constant in constants.items():
            print(f"  {label} 2025-03-01..2025-03-08")
            for attempt in range(1, 6):
                rates = mt5.copy_rates_range(broker, constant, window_start, window_end)
                count = 0 if rates is None else len(rates)
                err = mt5.last_error()
                print(f"    attempt {attempt}: rows={count} last_error={err}")
                if count > 0:
                    break
                time.sleep(3)

        print("\n--- oldest reachable bar per timeframe (copy_rates_from_pos walk) ---")
        for label, constant in (("4h", mt5.TIMEFRAME_H4), ("15m", mt5.TIMEFRAME_M15),
                                ("5m", mt5.TIMEFRAME_M5), ("1m", mt5.TIMEFRAME_M1)):
            best = 0
            oldest = None
            for count in (5_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000):
                rates = mt5.copy_rates_from_pos(broker, constant, 0, count)
                got = 0 if rates is None else len(rates)
                if got > best:
                    best = got
                    oldest = datetime.fromtimestamp(int(rates[0]["time"]), UTC)
                if got < count:
                    break
            span = "n/a" if oldest is None else f"{oldest.date()} ({(datetime.now(UTC) - oldest).days / 365.25:.1f}y)"
            print(f"  {label:<4} max_bars={best:>9,}  oldest={span}")
    finally:
        client.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
