"""Load liveTrade configuration from the .env file into a typed Config object."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:  # allow running without python-dotenv (env vars only)
    def load_dotenv(*a, **k):
        return False

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except ValueError:
        return default


def _s(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass
class Config:
    # MT5
    mt5_login: int = field(default_factory=lambda: _i("MT5_LOGIN", 0))
    mt5_password: str = field(default_factory=lambda: _s("MT5_PASSWORD"))
    mt5_server: str = field(default_factory=lambda: _s("MT5_SERVER"))
    mt5_path: str = field(default_factory=lambda: _s("MT5_PATH"))
    symbol_suffix: str = field(default_factory=lambda: _s("MT5_SYMBOL_SUFFIX"))

    symbols: List[str] = field(default_factory=lambda: [
        s.strip().upper() for s in _s("SYMBOLS", "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,NZDUSD,USDCAD").split(",") if s.strip()
    ])

    account_ccy: str = field(default_factory=lambda: _s("ACCOUNT_CCY", "INR"))
    margin_per_trade: float = field(default_factory=lambda: _f("MARGIN_PER_TRADE_INR", 1000))
    leverage: float = field(default_factory=lambda: _f("LEVERAGE", 2000))
    max_lot: float = field(default_factory=lambda: _f("MAX_LOT", 50))

    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", True))
    one_trade_per_pair: bool = field(default_factory=lambda: _b("ONE_TRADE_PER_PAIR", True))
    max_concurrent: int = field(default_factory=lambda: _i("MAX_CONCURRENT_TRADES", 5))
    max_daily_loss: float = field(default_factory=lambda: _f("MAX_DAILY_LOSS_INR", 10000))

    poll_seconds: int = field(default_factory=lambda: _i("POLL_SECONDS", 15))
    candle_close_lag: int = field(default_factory=lambda: _i("CANDLE_CLOSE_LAG_SEC", 8))

    smtp_host: str = field(default_factory=lambda: _s("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _i("SMTP_PORT", 465))
    smtp_user: str = field(default_factory=lambda: _s("SMTP_USER"))
    smtp_pass: str = field(default_factory=lambda: _s("SMTP_PASS"))
    email_from: str = field(default_factory=lambda: _s("EMAIL_FROM"))
    email_to: str = field(default_factory=lambda: _s("EMAIL_TO"))

    # Strategy 95 params (override via .env)
    partial_r: float = field(default_factory=lambda: _f("PARTIAL_R", 0.5))
    final_r: float = field(default_factory=lambda: _f("FINAL_R", 1.5))
    bias_ttl_hours: float = field(default_factory=lambda: _f("BIAS_TTL_HOURS", 16))
    min_displacement_pct: float = field(default_factory=lambda: _f("MIN_DISPLACEMENT_PCT", 0.10))
    sl_buffer_pips: float = field(default_factory=lambda: _f("FOREX_SL_BUFFER_PIPS", 15))

    def email_ready(self) -> bool:
        return all([self.smtp_host, self.smtp_user, self.smtp_pass, self.email_to])


CONFIG = Config()
