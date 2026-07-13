"""
liveTrade engine for Strategy 98 — XAUUSD 1H trend + liquidity + ATR trail.

Scans 1H on each closed candle only. One position per symbol; exits via
chandelier ATR trail (no fixed TP).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from config import CONFIG
from detection_s98 import detect_signal
from logging_setup import get_engine_logger, record_pass, record_trade
from mt5_client import MT5Client, TF_MINUTES, lots_for_trade
from notifier import send_email, trade_email
from risk_guard import (
    assess_entry_risk,
    entry_delay_ok,
    signal_matches_last_closed_bar,
)
from trade_manager_s98 import MAGIC, TradeManagerS98

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
UTC = timezone.utc
SIGNAL_STATE_PATH = Path(__file__).resolve().parent / "passes" / "s98_signal_state.json"


def _floor_utc(dt: datetime, minutes: int) -> datetime:
    em = int(dt.timestamp() // 60)
    return datetime.fromtimestamp(((em // minutes) * minutes) * 60, tz=UTC)


class EngineS98:
    def __init__(self):
        self.client = MT5Client(magic=MAGIC)
        self.tm = TradeManagerS98(self.client)
        self.last_boundary: datetime | None = None
        self.last_signal_time: dict[str, str] = self._load_signal_state()

    def _load_signal_state(self) -> dict[str, str]:
        try:
            data = json.load(open(SIGNAL_STATE_PATH))
            return {str(k): str(v) for k, v in data.items()}
        except Exception:
            return {}

    def _save_signal_state(self):
        try:
            SIGNAL_STATE_PATH.parent.mkdir(exist_ok=True)
            json.dump(self.last_signal_time, open(SIGNAL_STATE_PATH, "w"), indent=1)
        except Exception as e:
            log.error(f"signal state save failed: {e}")

    def start(self):
        if not self.client.connect():
            log.error("Could not connect to MT5 — aborting.")
            return
        acc = self.client.account_info()
        lot_desc = f"fixed {CONFIG.fixed_lot}" if CONFIG.fixed_lot > 0 else f"margin Rs{CONFIG.margin_per_trade:.0f}"
        send_email(
            "Strategy98 liveTrade STARTED",
            f"Engine started.\nSymbols: {', '.join(CONFIG.symbols)}\n"
            f"DRY_RUN={CONFIG.dry_run} | lot={lot_desc}\n"
            f"Risk caps: Rs{CONFIG.max_risk_inr:.0f} and/or {CONFIG.max_risk_pct:.1f}% equity | "
            f"max entry delay {CONFIG.s98_max_entry_delay_sec}s after 1H close\n"
            f"Account: {getattr(acc, 'login', '?')} {getattr(acc, 'currency', '?')} "
            f"bal={getattr(acc, 'balance', '?')}",
        )
        log.info(
            f"liveTrade s98 started | DRY_RUN={CONFIG.dry_run} | symbols={CONFIG.symbols} | lot={lot_desc} | "
            f"risk_cap=Rs{CONFIG.max_risk_inr:.0f}/{CONFIG.max_risk_pct:.1f}%"
        )
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
        self.tm.monitor()
        boundary = _floor_utc(now, TF_MINUTES["1h"])
        if self.last_boundary is None or boundary > self.last_boundary:
            if (now - boundary).total_seconds() >= CONFIG.candle_close_lag:
                self.last_boundary = boundary
                self._run_1h_cycle(now)

    def _run_1h_cycle(self, now: datetime):
        tlog = get_engine_logger()
        tlog.info(f"=== 1h cycle @ {now.isoformat()} ===")
        for sym in CONFIG.symbols:
            try:
                self._cycle_1h(sym, now, tlog)
            except Exception as e:
                tlog.exception(f"{sym} 1h cycle error: {e}")

    def _cycle_1h(self, sym: str, now: datetime, tlog):
        if CONFIG.one_trade_per_pair and self.client.position_for_symbol(sym) is not None:
            tlog.info(f"{sym}: position open — skip new entry")
            return
        if len(self.client.open_positions(MAGIC)) >= CONFIG.max_concurrent:
            tlog.info(f"{sym}: max concurrent reached — skip")
            return
        if self._daily_loss_exceeded():
            tlog.info("daily loss limit hit — no new trades today")
            return

        df1 = self.client.fetch_closed(sym, "1h", 400)
        if df1 is None or len(df1) < 80:
            tlog.info(f"{sym}: insufficient 1h data")
            return

        signal = detect_signal(df1)
        if signal is None:
            tlog.info(f"{sym}: no 1H signal")
            return

        sig_time = signal["signal_time"]
        last_bar = df1.index[-1]
        if not signal_matches_last_closed_bar(sig_time, last_bar):
            tlog.warning(
                f"{sym}: signal bar {sig_time} is not the latest closed 1H ({last_bar}) — skip stale signal"
            )
            return

        ok_delay, delay_sec = entry_delay_ok(sig_time, now, CONFIG.s98_max_entry_delay_sec)
        if not ok_delay:
            tlog.warning(
                f"{sym}: entry too late ({delay_sec:.0f}s after signal close; "
                f"max {CONFIG.s98_max_entry_delay_sec}s) — skip (restart/stale entry guard)"
            )
            return

        if self.last_signal_time.get(sym) == sig_time:
            tlog.info(f"{sym}: signal already acted on ({sig_time})")
            return

        self._execute(sym, signal, tlog, now)
        self.last_signal_time[sym] = sig_time
        self._save_signal_state()

    def _execute(self, sym: str, signal: dict, tlog, now: datetime):
        direction = signal["direction"]
        sl = signal["sl"]
        risk = signal["risk"]
        tick = self.client.current_price(sym)
        if tick is None:
            tlog.error(f"{sym}: no tick — skip")
            return
        entry = float(tick.ask if direction == "long" else tick.bid)
        lots = lots_for_trade(self.client, sym, direction, entry)
        base = {
            "symbol": sym,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": 0.0,
            "lots": lots,
            "risk_price": risk,
            "setup": signal["setup"],
            "signal_time": signal["signal_time"],
            "time_utc": now.isoformat(),
            "strategy": "s98",
        }
        record_pass("1h", {**base, "event": "entry_signal"})

        if lots <= 0:
            tlog.error(f"{sym}: lot size 0 — skip")
            return

        acc = self.client.account_info()
        balance = float(getattr(acc, "balance", 0) or 0)
        risk_ok, risk_reason, loss_at_sl, risk_cap = assess_entry_risk(
            self.client, sym, direction, entry, sl, lots, balance
        )
        if not risk_ok:
            tlog.warning(f"{sym}: RISK GUARD — skip entry | {risk_reason}")
            record_pass(
                "1h",
                {
                    **base,
                    "event": "risk_guard_skip",
                    "loss_at_sl": loss_at_sl,
                    "risk_cap": risk_cap,
                    "reason": risk_reason,
                },
            )
            return

        tlog.info(
            f"{sym}: risk OK — loss@SL Rs{loss_at_sl:.0f} (cap Rs{risk_cap:.0f}) "
            f"SL dist {abs(entry - sl):.2f} pts setup={signal['setup']}"
        )

        if CONFIG.dry_run:
            tlog.info(f"[DRY RUN] {sym} {direction.upper()} entry~{entry:.2f} SL {sl:.2f} lots={lots}")
            record_trade({"event": "dry_run_entry", **base})
            trade_email({**base, "dry_run": True})
            return

        res = self.client.open_trade(sym, direction, lots, sl, 0.0, comment=f"s98 {direction}")
        if res is None or getattr(res, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            tlog.error(f"{sym}: order_send failed -> {res}")
            return

        pos = self.client.position_for_symbol(sym)
        fill = float(pos.price_open) if pos else entry
        ticket = getattr(res, "order", None) or (pos.ticket if pos else 0)
        extreme = fill
        if pos:
            self.tm.register(
                pos.ticket,
                {
                    "symbol": sym,
                    "direction": direction,
                    "entry": fill,
                    "sl": sl,
                    "risk": abs(fill - sl),
                    "lots": lots,
                    "extreme": extreme,
                    "atr_mult_trail": signal["atr_mult_trail"],
                    "setup": signal["setup"],
                },
            )
        tlog.info(f"{sym}: EXECUTED {direction.upper()} {lots} lots @ {fill:.2f} SL {sl:.2f} (ticket {ticket})")
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
