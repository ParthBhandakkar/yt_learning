"""
liveTrade engine for Strategy 97 — generic with-trend mean-reversion (4H basket).

Scans 4H on each CLOSED candle only, across the symbol basket. Entry at market
when the signal bar closes (backtest: next-bar open). Exit is managed by
TradeManagerS97 (fixed ATR stop on broker + dynamic mean-revert TP + time stop),
matching the backtest exactly.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from config import CONFIG
from detection_s97 import detect_signal
from logging_setup import get_engine_logger, get_tf_logger, record_pass, record_trade
from mt5_client import MT5Client, TF_MINUTES, lots_for_trade
from notifier import send_email, trade_email
from trade_manager_s97 import MAGIC, TradeManagerS97

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
UTC = timezone.utc


def _floor_utc(dt: datetime, minutes: int) -> datetime:
    em = int(dt.timestamp() // 60)
    return datetime.fromtimestamp(((em // minutes) * minutes) * 60, tz=UTC)


class EngineS97:
    def __init__(self):
        self.client = MT5Client(magic=MAGIC)
        self.tm = TradeManagerS97(self.client)
        self.last_boundary: datetime | None = None
        self.last_signal_time: dict[str, str] = {}

    def start(self):
        if not self.client.connect():
            log.error("Could not connect to MT5 — aborting.")
            return
        acc = self.client.account_info()
        lot_desc = f"fixed {CONFIG.fixed_lot}" if CONFIG.fixed_lot > 0 else f"margin Rs{CONFIG.margin_per_trade:.0f}"
        send_email(
            "Strategy97 liveTrade STARTED",
            f"Engine started.\nSymbols: {', '.join(CONFIG.symbols)}\n"
            f"DRY_RUN={CONFIG.dry_run} | lot={lot_desc}\n"
            f"Z_ENTRY={CONFIG.s97_z_entry} K_SL={CONFIG.s97_k_sl} Z_EXIT={CONFIG.s97_z_exit} "
            f"MAX_HOLD={CONFIG.s97_max_hold_bars}\n"
            f"Account: {getattr(acc, 'login', '?')} {getattr(acc, 'currency', '?')} "
            f"bal={getattr(acc, 'balance', '?')}",
        )
        log.info(f"liveTrade s97 started | DRY_RUN={CONFIG.dry_run} | symbols={CONFIG.symbols} | lot={lot_desc}")
        try:
            self._loop()
        except KeyboardInterrupt:
            log.info("Stopped by user.")
        finally:
            self.client.shutdown()

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                log.exception(f"tick error: {e}")
            time.sleep(CONFIG.poll_seconds)

    def _tick(self):
        now = datetime.now(UTC)
        # manage open positions every tick (closed-position reconciliation)
        self.tm.monitor()
        boundary = _floor_utc(now, TF_MINUTES["4h"])
        if self.last_boundary is None or boundary > self.last_boundary:
            if (now - boundary).total_seconds() >= CONFIG.candle_close_lag:
                self.last_boundary = boundary
                self._run_4h_cycle(now)

    def _run_4h_cycle(self, now: datetime):
        tlog = get_tf_logger("4h")
        tlog.info(f"=== 4h cycle @ {now.isoformat()} ===")
        # 1) per-bar management of open positions (dynamic TP + time stop)
        try:
            self.tm.on_bar(self.last_boundary)
        except Exception as e:
            tlog.exception(f"on_bar management error: {e}")
        # 2) scan each symbol for a fresh entry
        for sym in CONFIG.symbols:
            try:
                self._cycle_4h(sym, now, tlog)
            except Exception as e:
                tlog.exception(f"{sym} 4h cycle error: {e}")

    def _cycle_4h(self, sym: str, now: datetime, tlog):
        if CONFIG.one_trade_per_pair and self.client.position_for_symbol(sym) is not None:
            tlog.info(f"{sym}: position open — skip new entry")
            return
        if len(self.client.open_positions(MAGIC)) >= CONFIG.max_concurrent:
            tlog.info(f"{sym}: max concurrent reached — skip")
            return
        if self._daily_loss_exceeded():
            tlog.info("daily loss limit hit — no new trades today")
            return

        df4 = self.client.fetch_closed(sym, "4h", CONFIG.s97_fetch_bars)
        if df4 is None or len(df4) < CONFIG.s97_trend_ema + 5:
            tlog.info(f"{sym}: insufficient 4h data")
            return

        signal = detect_signal(df4)
        if signal is None:
            tlog.info(f"{sym}: no 4H signal")
            return

        sig_time = signal["signal_time"]
        if self.last_signal_time.get(sym) == sig_time:
            tlog.info(f"{sym}: signal already acted on ({sig_time})")
            return

        self._execute(sym, signal, tlog, now)
        self.last_signal_time[sym] = sig_time

    def _execute(self, sym: str, signal: dict, tlog, now: datetime):
        direction = signal["direction"]
        risk = signal["risk"]
        tick = self.client.current_price(sym)
        if tick is None:
            tlog.error(f"{sym}: no tick — skip")
            return
        entry = float(tick.ask if direction == "long" else tick.bid)
        # anchor the fixed ATR stop to the actual fill (same risk distance as backtest)
        sl = entry - risk if direction == "long" else entry + risk
        tp0 = signal["tp0"]
        lots = lots_for_trade(self.client, sym, direction, entry)
        base = {
            "symbol": sym,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp0,
            "lots": lots,
            "risk_price": risk,
            "setup": signal["setup"],
            "signal_time": signal["signal_time"],
            "entry_z": signal.get("entry_z"),
            "time_utc": now.isoformat(),
            "strategy": "s97",
        }
        record_pass("4h", {**base, "event": "entry_signal"})

        if lots <= 0:
            tlog.error(f"{sym}: lot size 0 — skip")
            return

        if CONFIG.dry_run:
            tlog.info(f"[DRY RUN] {sym} {direction.upper()} entry~{entry:.5f} SL {sl:.5f} "
                      f"TP {tp0:.5f} lots={lots} z={signal.get('entry_z'):+.2f}")
            record_trade({"event": "dry_run_entry", **base})
            trade_email({**base, "dry_run": True})
            return

        # TP is set as the initial mean-revert target; the manager refreshes it each bar
        res = self.client.open_trade(sym, direction, lots, sl, tp0, comment=f"s97 {direction}")
        if res is None or getattr(res, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            tlog.error(f"{sym}: order_send failed -> {res}")
            return

        pos = self.client.position_for_symbol(sym)
        fill = float(pos.price_open) if pos else entry
        ticket = getattr(res, "order", None) or (pos.ticket if pos else 0)
        if pos:
            self.tm.register(
                pos.ticket,
                {
                    "symbol": sym,
                    "direction": direction,
                    "entry": fill,
                    "sl": float(pos.sl) if pos.sl else sl,
                    "tp": tp0,
                    "risk": abs(fill - (float(pos.sl) if pos.sl else sl)),
                    "lots": lots,
                    "signal_time": signal["signal_time"],
                },
            )
        tlog.info(f"{sym}: EXECUTED {direction.upper()} {lots} lots @ {fill:.5f} SL {sl:.5f} "
                  f"TP {tp0:.5f} (ticket {ticket})")
        record_trade({"event": "executed", "ticket": str(ticket), **base, "fill": fill})
        trade_email({**base, "entry": fill, "dry_run": False})

    def _daily_loss_exceeded(self) -> bool:
        if CONFIG.max_daily_loss <= 0 or mt5 is None:
            return False
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(start, datetime.now(UTC))
        if not deals:
            return False
        realized = sum(d.profit for d in deals if d.magic == MAGIC)
        return realized <= -abs(CONFIG.max_daily_loss)
