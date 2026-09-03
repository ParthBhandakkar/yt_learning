#!/usr/bin/env python3
"""
liveTrade entrypoint — run Strategy 97 or 98 live on MT5 (Exness), 24/7.

    python run.py --strategy 97          # with-trend mean-reversion basket (4H)
    python run.py --strategy 98          # XAUUSD trend + liquidity + ATR trail (1H)
    python run.py --strategy 97 --check  # one-shot connectivity/config check, then exit
    python run.py                        # falls back to STRATEGY_ID in .env

Run on the Windows PC with the MT5 / Exness terminal open and logged in.
Keep it running continuously (Task Scheduler / NSSM / a simple restart loop).
"""
import argparse
import os
import sys

from config import CONFIG
from logging_setup import get_engine_logger
from mt5_client import MT5Client, lots_for_trade

log = get_engine_logger()

# Default symbol baskets per strategy (used only when SYMBOLS is not set in .env)
DEFAULT_SYMBOLS = {
    "s97": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "XAUUSD"],
    "s98": ["XAUUSD"],
    "s95": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"],
    # s146 was developed and measured on GBPCAD; override with SYMBOLS in .env.
    "s146": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "NZDUSD", "USDCAD",
    # Crosses
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF",
    "NZDJPY", "NZDCAD", "NZDCHF",
    "CADJPY", "CADCHF",
    "CHFJPY",
    # Gold
    "XAUUSD"],
}
MAGICS = {"s95": 950095, "s97": 970097, "s98": 980098, "s146": 1460146, "s147": 1470147}
SCAN_TF = {"s95": "5m", "s97": "4h", "s98": "1h", "s146": "5m", "s147": "5m"}

# s147 reacts AT a 4H POI rather than travelling toward one; same basket as s146.
DEFAULT_SYMBOLS["s147"] = list(DEFAULT_SYMBOLS["s146"])


def _normalize_strategy(raw: str) -> str:
    """Accept 95/97/98/146/147 or s95/s97/s98/s146/s147."""
    s = (raw or "").strip().lower()
    if s in ("95", "s95"):
        return "s95"
    if s in ("97", "s97"):
        return "s97"
    if s in ("98", "s98"):
        return "s98"
    if s in ("146", "s146"):
        return "s146"
    if s in ("147", "s147"):
        return "s147"
    raise SystemExit(f"Unknown strategy '{raw}'. Use 97, 98, 146 or 147.")


def _apply_strategy(strategy: str):
    """Set the active strategy on CONFIG and pick default symbols if not overridden."""
    CONFIG.strategy_id = strategy
    if not (os.getenv("SYMBOLS") or "").strip():
        CONFIG.symbols = DEFAULT_SYMBOLS[strategy]


def check():
    log.info("=== liveTrade config / connectivity check ===")
    log.info(f"Strategy          : {CONFIG.strategy_id}")
    log.info(f"Symbols           : {CONFIG.symbols}")
    log.info(f"DRY_RUN           : {CONFIG.dry_run}")
    if CONFIG.fixed_lot > 0:
        log.info(f"Lot sizing        : FIXED {CONFIG.fixed_lot} (max_lot cap {CONFIG.max_lot})")
    else:
        log.info(
            f"Lot sizing        : margin Rs {CONFIG.margin_per_trade:.0f}  | "
            f"leverage 1:{CONFIG.leverage:.0f}  | max_lot {CONFIG.max_lot}"
        )
    log.info(
        f"Guards            : one_per_pair={CONFIG.one_trade_per_pair} "
        f"max_concurrent={CONFIG.max_concurrent} max_daily_loss=Rs{CONFIG.max_daily_loss:.0f}"
    )
    if CONFIG.strategy_id == "s97":
        log.info(
            f"S97 params        : Z_ENTRY={CONFIG.s97_z_entry} K_SL={CONFIG.s97_k_sl} "
            f"Z_EXIT={CONFIG.s97_z_exit} EMA={CONFIG.s97_trend_ema} MAX_HOLD={CONFIG.s97_max_hold_bars}"
        )
    log.info(f"Email configured  : {CONFIG.email_ready()}")

    magic = MAGICS.get(CONFIG.strategy_id, 950095)
    tf = SCAN_TF.get(CONFIG.strategy_id, "5m")
    c = MT5Client(magic=magic)
    if not c.connect():
        log.error("MT5 connect FAILED. Check terminal is open/logged in and MT5_* in .env.")
        return 1
    if CONFIG.strategy_id == "s147":
        log.info(
            f"S147 params       : REWARD_RISK={CONFIG.s147_reward_risk} "
            f"cost_gate=disabled "
            f"choch_wait={CONFIG.s147_max_wait_bars_15m_choch} "
            f"zone_wait={CONFIG.s147_max_wait_bars_15m_zone} "
            f"5m_wait={CONFIG.s147_max_wait_bars_5m} "
            f"hold={'none' if CONFIG.s147_max_hold_bars_5m <= 0 else CONFIG.s147_max_hold_bars_5m} "
            f"demo_only={CONFIG.s147_demo_only}"
        )
        log.info("S147 4H POI age   : UNLIMITED by design")
        log.info(f"S147 account mode : {'DEMO' if c.is_demo() else 'LIVE (orders blocked when demo_only)'}")
        log.info("S147 journals     : logs/s147_4h_events.log, logs/s147_15m_events.log, "
                 "logs/s147_5m_events.log, logs/s147_trades.log")
    if CONFIG.strategy_id == "s146":
        log.info(
            f"S146 params       : MIN_RR={CONFIG.s146_min_rr} MAX_RR={CONFIG.s146_max_rr} "
            f"entry_mode={CONFIG.s146_entry_mode} hold={CONFIG.s146_max_hold_bars_5m} bars "
            f"running_extreme_15m={CONFIG.s146_require_running_extreme_15m} "
            f"destination_quality_gate={CONFIG.s146_preorder_destination_quality_gate} "
            f"legacy_require_new_15m={CONFIG.s146_require_new_15m} "
            f"demo_only={CONFIG.s146_demo_only}"
        )
        log.info(f"S146 account mode : {'DEMO' if c.is_demo() else 'LIVE (orders blocked when demo_only)'}")
        log.info("S146 journals     : logs/s146_4h_events.log, logs/s146_15m_events.log, "
                 "logs/s146_5m_events.log, logs/s146_trades.log")

    min_bars = CONFIG.s97_fetch_bars if CONFIG.strategy_id == "s97" else 5
    for s in CONFIG.symbols:
        resolved = c.resolve_symbol(s)
        df = c.fetch_closed(s, tf, min_bars) if resolved else None
        last = df.index[-1] if df is not None and len(df) else "—"
        lots = (
            lots_for_trade(c, s, "long", float(df["close"].iloc[-1]))
            if df is not None and len(df)
            else 0
        )
        log.info(f"  {s:<8} -> {resolved or 'NOT FOUND':<12} last_closed_{tf}={last} lots={lots}")
    c.shutdown()
    log.info("Check complete.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Strategy live trader (MT5/Exness)")
    ap.add_argument("--strategy", "-s", default=None,
                    help="which strategy to run: 97, 98, 146 or 147 "
                         "(overrides STRATEGY_ID in .env)")
    ap.add_argument("--check", action="store_true", help="connectivity/config check then exit")
    args = ap.parse_args()

    strategy = _normalize_strategy(args.strategy) if args.strategy else CONFIG.strategy_id
    _apply_strategy(strategy)

    if args.check:
        sys.exit(check())

    if strategy == "s147":
        from engine_s147 import EngineS147
        EngineS147().start()
    elif strategy == "s146":
        from engine_s146 import EngineS146
        EngineS146().start()
    elif strategy == "s98":
        from engine_s98 import EngineS98
        EngineS98().start()
    elif strategy == "s97":
        from engine_s97 import EngineS97
        EngineS97().start()
    else:
        from engine import Engine
        Engine().start()


if __name__ == "__main__":
    main()
