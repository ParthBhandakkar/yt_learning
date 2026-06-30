#!/usr/bin/env python3
"""
liveTrade entrypoint — run Strategy 95 live on MT5 (Exness), 24/7.

    python run.py            # uses .env (DRY_RUN respected)
    python run.py --check    # one-shot connectivity + config check, then exit

Run on the Windows PC with the MT5 / Exness terminal logged in.
"""
import argparse
import sys

from config import CONFIG
from logging_setup import get_engine_logger

log = get_engine_logger()


def check():
    from mt5_client import MT5Client
    log.info("=== liveTrade config / connectivity check ===")
    log.info(f"Symbols           : {CONFIG.symbols}")
    log.info(f"DRY_RUN           : {CONFIG.dry_run}")
    log.info(f"Margin/trade      : Rs {CONFIG.margin_per_trade:.0f}  | leverage 1:{CONFIG.leverage:.0f}  | max_lot {CONFIG.max_lot}")
    log.info(f"Guards            : one_per_pair={CONFIG.one_trade_per_pair} max_concurrent={CONFIG.max_concurrent} max_daily_loss=Rs{CONFIG.max_daily_loss:.0f}")
    log.info(f"Email configured  : {CONFIG.email_ready()}")
    c = MT5Client()
    if not c.connect():
        log.error("MT5 connect FAILED. Check terminal is open/logged in and MT5_* in .env.")
        return 1
    for s in CONFIG.symbols:
        resolved = c.resolve_symbol(s)
        df = c.fetch_closed(s, "5m", 5) if resolved else None
        last = df.index[-1] if df is not None and len(df) else "—"
        lots = c.lots_for_margin(s, "long", float(df["close"].iloc[-1]), CONFIG.margin_per_trade) if df is not None and len(df) else 0
        log.info(f"  {s:<8} -> {resolved or 'NOT FOUND':<12} last_closed_5m={last} sample_lots={lots}")
    c.shutdown()
    log.info("Check complete.")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Strategy 95 live trader (MT5/Exness)")
    ap.add_argument("--check", action="store_true", help="connectivity/config check then exit")
    args = ap.parse_args()
    if args.check:
        sys.exit(check())
    from engine import Engine
    Engine().start()


if __name__ == "__main__":
    main()
