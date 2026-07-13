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
    # Strategy selection (s95 = legacy multi-TF MSS/OB; s98 = XAUUSD trend+liquidity+ATR trail)
    strategy_id: str = field(default_factory=lambda: _s("STRATEGY_ID", "s95").lower())

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
    fixed_lot: float = field(default_factory=lambda: _f("FIXED_LOT", 0))

    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", True))
    one_trade_per_pair: bool = field(default_factory=lambda: _b("ONE_TRADE_PER_PAIR", True))
    max_concurrent: int = field(default_factory=lambda: _i("MAX_CONCURRENT_TRADES", 5))
    max_daily_loss: float = field(default_factory=lambda: _f("MAX_DAILY_LOSS_INR", 10000))
    max_risk_inr: float = field(default_factory=lambda: _f("MAX_RISK_INR", 500))
    max_risk_pct: float = field(default_factory=lambda: _f("MAX_RISK_PCT", 2.0))

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

    # Strategy 98 params (defaults match strategy_98_xau_trend_liquidity_trail.py backtest)
    s98_htf_ema: int = field(default_factory=lambda: _i("S98_HTF_EMA", 50))
    s98_range_lookback: int = field(default_factory=lambda: _i("S98_RANGE_LOOKBACK", 20))
    s98_donchian: int = field(default_factory=lambda: _i("S98_DONCHIAN", 20))
    s98_atr_len: int = field(default_factory=lambda: _i("S98_ATR_LEN", 14))
    s98_atr_mult_init: float = field(default_factory=lambda: _f("S98_ATR_MULT_INIT", 1.5))
    s98_atr_mult_trail: float = field(default_factory=lambda: _f("S98_ATR_MULT_TRAIL", 3.0))
    s98_also_breakout: bool = field(default_factory=lambda: _b("S98_ALSO_BREAKOUT", True))
    s98_session_filter: bool = field(default_factory=lambda: _b("S98_SESSION_FILTER", False))
    s98_use_pd_filter: bool = field(default_factory=lambda: _b("S98_USE_PD_FILTER", True))
    s98_max_entry_delay_sec: int = field(default_factory=lambda: _i("S98_MAX_ENTRY_DELAY_SEC", 900))

    def email_ready(self) -> bool:
        return all([self.smtp_host, self.smtp_user, self.smtp_pass, self.email_to])


CONFIG = Config()
