#!/usr/bin/env python3
"""
liveTrade entrypoint — run Strategy 95 or 98 live on MT5 (Exness), 24/7.

    python run.py            # uses .env (DRY_RUN respected)
    python run.py --check    # one-shot connectivity + config check, then exit

Run on the Windows PC with the MT5 / Exness terminal logged in.
"""
import argparse
import sys

from config import CONFIG
from logging_setup import get_engine_logger
from mt5_client import MT5Client, lots_for_trade

log = get_engine_logger()


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
        f"max_concurrent={CONFIG.max_concurrent} max_daily_loss=Rs{CONFIG.max_daily_loss:.0f} "
        f"max_risk=Rs{CONFIG.max_risk_inr:.0f}/{CONFIG.max_risk_pct:.1f}%"
    )
    if CONFIG.strategy_id == "s98":
        log.info(f"s98 entry delay   : {CONFIG.s98_max_entry_delay_sec}s max after 1H close")
    log.info(f"Email configured  : {CONFIG.email_ready()}")
    magic = 980098 if CONFIG.strategy_id == "s98" else 950095
    c = MT5Client(magic=magic)
    if not c.connect():
        log.error("MT5 connect FAILED. Check terminal is open/logged in and MT5_* in .env.")
        return 1
    for s in CONFIG.symbols:
        resolved = c.resolve_symbol(s)
        tf = "1h" if CONFIG.strategy_id == "s98" else "5m"
        df = c.fetch_closed(s, tf, 5) if resolved else None
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
    ap.add_argument("--check", action="store_true", help="connectivity/config check then exit")
    args = ap.parse_args()
    if args.check:
        sys.exit(check())
    if CONFIG.strategy_id == "s98":
        from engine_s98 import EngineS98
        EngineS98().start()
    else:
        from engine import Engine
        Engine().start()


if __name__ == "__main__":
    main()
