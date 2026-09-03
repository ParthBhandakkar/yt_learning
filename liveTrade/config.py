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
    # MT5 can transiently return zero margin while symbol/quote data is being
    # refreshed. Retry before treating the result as unsafe to trade.
    margin_calc_retries: int = field(default_factory=lambda: _i("MARGIN_CALC_RETRIES", 3))
    margin_calc_retry_delay_sec: float = field(
        default_factory=lambda: _f("MARGIN_CALC_RETRY_DELAY_SEC", 0.20))
    leverage: float = field(default_factory=lambda: _f("LEVERAGE", 2000))
    max_lot: float = field(default_factory=lambda: _f("MAX_LOT", 50))
    fixed_lot: float = field(default_factory=lambda: _f("FIXED_LOT", 0))

    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", False))
    one_trade_per_pair: bool = field(default_factory=lambda: _b("ONE_TRADE_PER_PAIR", True))
    max_concurrent: int = field(default_factory=lambda: _i("MAX_CONCURRENT_TRADES", 5))
    max_daily_loss: float = field(default_factory=lambda: _f("MAX_DAILY_LOSS_INR", 10000))
    # Defaults: %-only cap in the 5–20% band (INR=0 so flat Rs cap does not undercut %)
    max_risk_inr: float = field(default_factory=lambda: _f("MAX_RISK_INR", 0))
    max_risk_pct: float = field(default_factory=lambda: _f("MAX_RISK_PCT", 15.0))

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

    # Strategy 97 params (defaults match strategy_97_trend_meanreversion.py backtest)
    s97_trend_ema: int = field(default_factory=lambda: _i("S97_TREND_EMA", 200))
    s97_sma_n: int = field(default_factory=lambda: _i("S97_SMA_N", 20))
    s97_atr_n: int = field(default_factory=lambda: _i("S97_ATR_N", 14))
    s97_z_entry: float = field(default_factory=lambda: _f("S97_Z_ENTRY", 2.0))
    s97_z_exit: float = field(default_factory=lambda: _f("S97_Z_EXIT", 0.5))
    s97_k_sl: float = field(default_factory=lambda: _f("S97_K_SL", 2.5))
    s97_max_hold_bars: int = field(default_factory=lambda: _i("S97_MAX_HOLD_BARS", 48))
    # EMA200 is recursive (adjust=False); fetch enough closed 4H bars that it
    # converges to the full-history backtest value. >=800 matches; 1500 = safety.
    s97_fetch_bars: int = field(default_factory=lambda: _i("S97_FETCH_BARS", 1500))

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

    # Strategy 146 params (defaults match strategy_146_naked_4h_poi_draw.py backtest)
    s146_stop_buffer_bps: float = field(default_factory=lambda: _f("S146_STOP_BUFFER_BPS", 0.5))
    s146_min_rr: float = field(default_factory=lambda: _f("S146_MIN_RR", 1.5))
    s146_max_rr: float = field(default_factory=lambda: _f("S146_MAX_RR", 20.0))
    # Take profit is banked at a FIXED multiple of the structural risk instead of
    # waiting for the 4H destination. The destination still has to sit at least
    # S146_MIN_RR away for the signal to qualify, so entry selection is unchanged;
    # only the exit is closer. Risk is measured entry -> stop (15m zone distal edge
    # plus buffer), and the level is re-anchored to the real fill once known.
    s146_tp_r: float = field(default_factory=lambda: _f("S146_TP_R", 1.25))
    s146_max_wait_bars_5m: int = field(default_factory=lambda: _i("S146_MAX_WAIT_BARS_5M", 864))
    s146_max_hold_bars_5m: int = field(default_factory=lambda: _i("S146_MAX_HOLD_BARS_5M", 864))
    # Fetch depth drives both network time and zone-build time, which is what
    # makes a 5m scan overrun its own candle. Depth only has to cover the oldest
    # structure still eligible:
    #   4h : 600 bars = 100 days, ample for the 7-day destination age cap. RAISE
    #        this if you widen S146_MAX_DEST_AGE_DAYS or disable it, otherwise
    #        older unmitigated zones simply will not be visible.
    #   15m: 1500 bars = ~15 days; covers campaigns allowed by the 7-day
    #        destination age cap and survives engine restarts.
    #   5m : 1500 bars = ~5 days, covers max_wait_bars_5m (864) plus margin.
    s146_fetch_4h: int = field(default_factory=lambda: _i("S146_FETCH_4H_BARS", 600))
    s146_fetch_15m: int = field(default_factory=lambda: _i("S146_FETCH_15M_BARS", 1500))
    s146_fetch_5m: int = field(default_factory=lambda: _i("S146_FETCH_5M_BARS", 1500))
    # "market" enters as soon as the 5m confirmation closes (default).
    # "stop" mirrors the backtest by resting a buy-stop/sell-stop past the signal
    # candle; such orders are GTC and are never cancelled for being unfilled.
    s146_entry_mode: str = field(default_factory=lambda: _s("S146_ENTRY_MODE", "market").lower())
    # True = the engine owns SL/TP on the broker. Required by the exit ladder
    # below, which cannot ratchet a stop it does not control. Set False only to
    # go back to placing levels by hand from the signal email.
    s146_attach_sl_tp: bool = field(default_factory=lambda: _b("S146_ATTACH_SL_TP", True))
    # ---------------------------------------------------------------- exits
    # Laddered exit management. Live-only: the backtest exits at a single fixed R,
    # so re-run it with an equivalent model before comparing results.
    #
    #   * The broker holds SL plus a TP at the 4H DESTINATION, so the position is
    #     always protected even if this process dies.
    #   * At S146_PARTIAL_AT_R the manager banks S146_PARTIAL_FRACTION at market
    #     and pulls the stop to breakeven.
    #   * Above that the stop ratchets: rungs sit on a S146_TRAIL_STEP_R grid and
    #     the stop follows S146_TRAIL_GIVEBACK_R behind the highest rung reached.
    #     Stops never move against the position.
    #
    # The take profit deliberately does NOT chase price. A target kept ahead of
    # the highest rung can never fill, so the advancing stop is what ends the
    # trade and the 4H destination is its ceiling.
    s146_ladder_enabled: bool = field(default_factory=lambda: _b("S146_LADDER_ENABLED", True))
    s146_partial_at_r: float = field(default_factory=lambda: _f("S146_PARTIAL_AT_R", 1.25))
    s146_partial_fraction: float = field(
        default_factory=lambda: _f("S146_PARTIAL_FRACTION", 0.5))
    s146_trail_step_r: float = field(default_factory=lambda: _f("S146_TRAIL_STEP_R", 0.5))
    s146_trail_giveback_r: float = field(
        default_factory=lambda: _f("S146_TRAIL_GIVEBACK_R", 0.5))
    # ---------------------------------------------------------------- stops
    # Anchor the stop behind the nearest pool of resting orders just outside the
    # 15m entry zone (equal highs/lows preferred, since that is where stops sit)
    # instead of always using the zone's distal edge. The zone edge stays both the
    # fallback and the floor: a pool inside the zone would put the stop inside the
    # structure the trade is built on. A pool further away than
    # S146_STOP_LIQ_MAX_MULT times the plain zone-distal stop is ignored.
    s146_stop_use_liquidity: bool = field(
        default_factory=lambda: _b("S146_STOP_USE_LIQUIDITY", True))
    s146_stop_liq_lookback_5m: int = field(
        default_factory=lambda: _i("S146_STOP_LIQ_LOOKBACK_5M", 72))
    s146_stop_liq_max_mult: float = field(
        default_factory=lambda: _f("S146_STOP_LIQ_MAX_MULT", 1.5))
    # Execution safety: this is a margin-availability check only. It does not
    # re-enable MAX_RISK_INR/MAX_RISK_PCT, which remain informational/disabled
    # for s146. Leave headroom for price movement and broker fees.
    s146_free_margin_fraction: float = field(
        default_factory=lambda: _f("S146_FREE_MARGIN_FRACTION", 0.80))
    # Cost gate. A trade pays roughly two spreads round trip, and a SHORT stop
    # fires on ask so it sits one spread tighter than the structural level. When
    # the live spread is a large share of the intended risk the cost consumes the
    # move before it happens. Measured on the 16 logged signals: every signal
    # above 30% lost, and tick data showed spreads of 43-94% of risk around the
    # 21:00-23:00 UTC rollover. This is a COST filter, not a minimum stop
    # distance in pips: a wide-risk trade at a normal spread is never rejected.
    # Set to 0 to disable.
    s146_max_spread_pct_of_risk: float = field(
        default_factory=lambda: _f("S146_MAX_SPREAD_PCT_OF_RISK", 0.30))
    # Experimental pre-order destination-quality screen, derived from the
    # 19-29 Aug archive. It is deliberately OFF until shadow validation proves
    # the hypothesis on new data. When enabled it evaluates the selected 4H
    # destination using requested trigger-to-stop risk, before sizing/orders:
    #   - compact zone width (<= 1R),
    #   - prompt confirmation (origin-to-BOS <= 4 H4 bars), and
    #   - low one-way spread (<= 15% of requested risk).
    # Set a numeric limit <= 0 to disable that individual check.
    s146_preorder_destination_quality_gate: bool = field(
        default_factory=lambda: _b("S146_PREORDER_DESTINATION_QUALITY_GATE", False))
    s146_preorder_max_destination_width_r: float = field(
        default_factory=lambda: _f("S146_PREORDER_MAX_DESTINATION_WIDTH_R", 1.0))
    s146_preorder_max_destination_origin_lag_h4_bars: float = field(
        default_factory=lambda: _f("S146_PREORDER_MAX_DESTINATION_ORIGIN_LAG_H4_BARS", 4.0))
    s146_preorder_max_spread_pct_of_requested_risk: float = field(
        default_factory=lambda: _f("S146_PREORDER_MAX_SPREAD_PCT_OF_REQUESTED_RISK", 0.15))
    # Optional correction for the short-side ask/bid stop asymmetry. Tick data
    # confirmed 3 shorts were stopped at a level the bid never traded, but once
    # the cost gate above is active this padding did not improve results on the
    # sample, so it defaults to OFF. Lots are divided by the resulting stop
    # multiple so the money risked does not change.
    s146_short_stop_spread_pad: float = field(
        default_factory=lambda: _f("S146_SHORT_STOP_SPREAD_PAD", 0.0))
    # Conservative portfolio gates. Counts include open positions and pending
    # orders; zero disables the corresponding gate.
    s146_max_same_destination: int = field(
        default_factory=lambda: _i("S146_MAX_SAME_DESTINATION", 1))
    s146_max_currency_exposure: int = field(
        default_factory=lambda: _i("S146_MAX_CURRENCY_EXPOSURE", 3))
    # Require a signaling 15m zone to be the running price extreme among all
    # still-actionable same-direction zones confirmed after its selected 4H
    # destination activated. Shorts use the highest proximal supply; longs the
    # lowest proximal demand. This structural campaign boundary survives process
    # restarts. Disable for immediate rollback to the legacy newest-first logic.
    s146_require_running_extreme_15m: bool = field(
        default_factory=lambda: _b("S146_REQUIRE_RUNNING_EXTREME_15M", True))
    # Legacy fallback only when running-extreme mode is disabled: require the
    # 15m entry zone and its 5m confirmation to form after engine start.
    s146_require_new_15m: bool = field(default_factory=lambda: _b("S146_REQUIRE_NEW_15M", True))
    # Destination (4H POI) quality filters. Live-only: the backtest has no
    # equivalent, so re-run the backtest with the same limits before trusting
    # live results against it. Set either to 0 to disable that filter.
    #   age  : how old the POI's origin candle may be, measured to the latest
    #          closed 5m bar. 7 days keeps the draw inside the current regime.
    #   lag  : 4H bars allowed between the POI's origin candle and the BOS that
    #          confirmed it. A wide gap means origin and break are unrelated.
    s146_max_dest_age_days: float = field(default_factory=lambda: _f("S146_MAX_DEST_AGE_DAYS", 7.0))
    s146_max_dest_bos_lag_bars: float = field(default_factory=lambda: _f("S146_MAX_DEST_BOS_LAG_BARS", 8.0))
    # Refuse to send orders on a non-demo account.
    s146_demo_only: bool = field(default_factory=lambda: _b("S146_DEMO_ONLY", True))

    # Strategy 147 params (defaults match strategy_147_4h_poi_reaction.py backtest).
    # S147 waits for price to ARRIVE at a 4H POI and trades the reaction away from
    # it. There is deliberately NO age limit on the 4H zone.
    s147_stop_buffer_bps: float = field(default_factory=lambda: _f("S147_STOP_BUFFER_BPS", 0.5))
    # Fixed reward multiple. This is the model, not a tunable.
    s147_reward_risk: float = field(default_factory=lambda: _f("S147_REWARD_RISK", 1.5))
    s147_max_wait_bars_15m_choch: int = field(
        default_factory=lambda: _i("S147_MAX_WAIT_BARS_15M_CHOCH", 16))
    s147_max_wait_bars_15m_zone: int = field(
        default_factory=lambda: _i("S147_MAX_WAIT_BARS_15M_ZONE", 16))
    s147_max_wait_bars_5m: int = field(default_factory=lambda: _i("S147_MAX_WAIT_BARS_5M", 288))
    s147_min_stop_bps: float = field(default_factory=lambda: _f("S147_MIN_STOP_BPS", 1.0))
    # 4H depth is generous on purpose: an old untested POI is still tradable here.
    s147_fetch_4h: int = field(default_factory=lambda: _i("S147_FETCH_4H_BARS", 3000))
    s147_fetch_15m: int = field(default_factory=lambda: _i("S147_FETCH_15M_BARS", 1500))
    s147_fetch_5m: int = field(default_factory=lambda: _i("S147_FETCH_5M_BARS", 1500))
    s147_entry_mode: str = field(default_factory=lambda: _s("S147_ENTRY_MODE", "market").lower())
    s147_attach_sl_tp: bool = field(default_factory=lambda: _b("S147_ATTACH_SL_TP", False))
    s147_demo_only: bool = field(default_factory=lambda: _b("S147_DEMO_ONLY", True))
    # No holding limit by design: a trade runs until its stop or its target.
    # Set above 0 only as an emergency backstop.
    s147_max_hold_bars_5m: int = field(default_factory=lambda: _i("S147_MAX_HOLD_BARS_5M", 0))

    def email_ready(self) -> bool:
        return all([self.smtp_host, self.smtp_user, self.smtp_pass, self.email_to])


CONFIG = Config()
