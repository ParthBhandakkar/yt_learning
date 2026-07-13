"""Unit tests for liveTrade risk_guard (no MT5 required)."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from risk_guard import (  # noqa: E402
    entry_delay_ok,
    max_allowed_risk,
    signal_bar_close_time,
    signal_matches_last_closed_bar,
    sl_loss_at_stop,
)


class _FakeClient:
    def resolve_symbol(self, symbol: str):
        return symbol

    def ensure(self):
        return False


class RiskGuardTests(unittest.TestCase):
    def test_signal_bar_close(self):
        close = signal_bar_close_time("2026-07-13T16:00:00+00:00")
        self.assertEqual(close.hour, 17)

    def test_entry_delay_rejects_late_restart(self):
        now = datetime(2026, 7, 13, 17, 50, tzinfo=timezone.utc)
        ok, delay = entry_delay_ok("2026-07-13T16:00:00+00:00", now, 900)
        self.assertFalse(ok)
        self.assertGreater(delay, 900)

    def test_entry_delay_accepts_fresh(self):
        now = datetime(2026, 7, 13, 17, 0, 8, tzinfo=timezone.utc)
        ok, delay = entry_delay_ok("2026-07-13T16:00:00+00:00", now, 900)
        self.assertTrue(ok)
        self.assertLess(delay, 60)

    def test_signal_must_match_last_bar(self):
        bar_open = datetime(2026, 7, 13, 16, 0, tzinfo=timezone.utc)
        self.assertTrue(signal_matches_last_closed_bar("2026-07-13T16:00:00+00:00", bar_open))
        self.assertFalse(signal_matches_last_closed_bar("2026-07-13T12:00:00+00:00", bar_open))

    @patch.dict(os.environ, {"MAX_RISK_INR": "500", "MAX_RISK_PCT": "2.0", "ACCOUNT_CCY": "INR"}, clear=False)
    def test_max_allowed_risk_min_of_caps(self):
        from importlib import reload

        import config as cfg

        reload(cfg)
        cap = max_allowed_risk(9337.67)
        self.assertAlmostEqual(cap, 186.75, places=1)

    @patch.dict(os.environ, {"ACCOUNT_CCY": "INR"}, clear=False)
    def test_xauusd_fallback_risk_order_of_magnitude(self):
        from importlib import reload

        import config as cfg
        import risk_guard as rg

        reload(cfg)
        reload(rg)
        loss = rg.sl_loss_at_stop(_FakeClient(), "XAUUSD", "short", 3994.44, 4122.48, 0.01)
        # ~128 USD/oz * 47 INR ≈ 6000 INR
        self.assertGreater(loss, 5000)
        self.assertLess(loss, 7000)


if __name__ == "__main__":
    unittest.main()
