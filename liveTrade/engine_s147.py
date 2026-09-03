"""
liveTrade engine for Strategy 147 — trade the reaction AT a 4H point of interest.

Runs forever. Work happens once per CLOSED 5m candle:

  1. 4H zones are rebuilt from long history. Every confirmed zone is a point of
     interest, with NO age limit: an old untested zone is as valid as a fresh
     one. A zone dies only when a 15m bar closes fully beyond its distal edge.
  2. Price must have ARRIVED at the zone, detected on closed 15m bars. A zone can
     be traded again on later visits.
  3. 15m must confirm in order: a change of character in the reaction direction,
     and only then a fresh aligned zone that refines the entry and sets the stop.
  4. 5m confirms on the just-closed bar via a liquidity sweep or an MSS. Target is
     a fixed R multiple from entry.

Safety: DRY_RUN suppresses all order sending, and by default the engine refuses
to trade anything other than a DEMO account. Suppressed signals are still
tracked to a paper outcome so expectancy stays measurable.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from config import CONFIG
from detection_s147 import detect_signal, live_params
from logging_setup import format_ist, get_engine_logger
from mt5_client import MT5Client, TF_MINUTES, lots_for_trade
from notifier import send_email
from risk_guard import assess_entry_risk
from trade_manager_s147 import MAGIC, TradeManagerS147
import s147_events

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
UTC = timezone.utc


def _floor_utc(dt: datetime, minutes: int) -> datetime:
    epoch_minutes = int(dt.timestamp() // 60)
    return datetime.fromtimestamp(((epoch_minutes // minutes) * minutes) * 60, tz=UTC)


class EngineS147:
    def __init__(self):
        self.client = MT5Client(magic=MAGIC)
        self.tm = TradeManagerS147(self.client)
        self.last_boundary: datetime | None = None
        self.engine_start_ts = int(datetime.now(UTC).timestamp())
        self.trading_blocked = False

    # ------------------------------------------------------------------ startup
    def start(self):
        if not self.client.connect():
            log.error("Could not connect to MT5 — aborting.")
            return

        account = self.client.account_info()
        demo = self.client.is_demo()
        if CONFIG.s147_demo_only and not demo:
            self.trading_blocked = True
            log.error("S147_DEMO_ONLY=true but the connected account is NOT a demo account. "
                      "Detection will run; orders will NOT be sent.")

        lot_desc = (f"fixed {CONFIG.fixed_lot}" if CONFIG.fixed_lot > 0
                    else f"margin Rs{CONFIG.margin_per_trade:.0f} @ 1:{CONFIG.leverage:.0f}")
        params = live_params()
        summary = (
            f"Strategy 147 live engine started\n"
            f"----------------------------------------\n"
            f"Account   : {getattr(account, 'login', '?')} "
            f"{'DEMO' if demo else 'LIVE'} {getattr(account, 'currency', '?')} "
            f"bal={getattr(account, 'balance', '?')}\n"
            f"Symbols   : {', '.join(CONFIG.symbols)}\n"
            f"DRY_RUN   : {CONFIG.dry_run}\n"
            f"Demo only : {CONFIG.s147_demo_only} (orders blocked: {self.trading_blocked})\n"
            f"Sizing    : {lot_desc} | max_concurrent={CONFIG.max_concurrent}\n"
            f"Entry mode: {CONFIG.s147_entry_mode} "
            f"(SL/TP attached: {CONFIG.s147_attach_sl_tp})\n"
            f"Target    : fixed {params.reward_risk}R from entry\n"
            f"Cost model: recorded for reporting; no setup rejection\n"
            f"4H POI age: UNLIMITED by design\n"
            f"Hold limit: {'none (runs to stop/target)' if CONFIG.s147_max_hold_bars_5m <= 0 else str(CONFIG.s147_max_hold_bars_5m) + ' 5m bars'}\n"
            f"Journals  : {s147_events.journal_paths()}\n"
        )
        log.info("liveTrade s147 started | " + summary.replace("\n", " | "))
        send_email("Strategy147 liveTrade STARTED", summary)

        try:
            self._loop()
        except KeyboardInterrupt:
            log.info("Stopped by user.")
        finally:
            self.client.shutdown()

    # --------------------------------------------------------------------- loop
    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as exc:
                log.exception(f"tick error: {exc}")
            time.sleep(CONFIG.poll_seconds)

    def _tick(self):
        now = datetime.now(UTC)
        self.tm.monitor()
        boundary = _floor_utc(now, TF_MINUTES["5m"])
        if self.last_boundary is not None and boundary <= self.last_boundary:
            return
        if (now - boundary).total_seconds() < CONFIG.candle_close_lag:
            return
        self.last_boundary = boundary
        started = time.time()
        scanned = 0
        for symbol in CONFIG.symbols:
            try:
                self._cycle(symbol, now)
                scanned += 1
            except Exception as exc:
                log.exception(f"{symbol} 5m cycle error: {exc}")
        elapsed = time.time() - started
        log.info(f"5m cycle @ {format_ist(boundary)} | scanned {scanned}/{len(CONFIG.symbols)} "
                 f"symbols in {elapsed:.1f}s ({elapsed / max(scanned, 1):.2f}s/symbol)")
        if elapsed > 240:
            log.warning(
                f"5m scan took {elapsed:.0f}s of the 300s candle it runs on — signals risk "
                f"being detected a bar late. Reduce SYMBOLS (currently "
                f"{len(CONFIG.symbols)}) or lower S147_FETCH_4H_BARS/S147_FETCH_15M_BARS/"
                f"S147_FETCH_5M_BARS (currently {CONFIG.s147_fetch_4h}/"
                f"{CONFIG.s147_fetch_15m}/{CONFIG.s147_fetch_5m})."
            )

    # -------------------------------------------------------------------- cycle
    def _cycle(self, symbol: str, now: datetime):
        if CONFIG.one_trade_per_pair and self.client.position_for_symbol(symbol, MAGIC) is not None:
            return
        if len(self.client.open_positions(MAGIC)) >= CONFIG.max_concurrent:
            return
        if any(self.client.resolve_symbol(symbol) == o.symbol
               for o in self.client.pending_orders(MAGIC)):
            return

        signal = detect_signal(self.client, symbol, self.engine_start_ts)
        if signal is None:
            return

        log.info(
            f"S147 SIGNAL {symbol} {signal['direction'].upper()} {signal['model']} | "
            f"entry={signal['trigger']:.5f} stop={signal['stop']:.5f} "
            f"target={signal['target']:.5f} rr={signal['reward_risk']} "
            f"| 4H POI {signal['poi_zone_id']} arrived {signal['poi_arrival_time_ist']}"
        )
        self._place(signal, now)

    def _place(self, signal: dict, now: datetime):
        symbol = signal["symbol"]
        direction = signal["direction"]
        entry = signal["trigger"]
        stop = signal["stop"]
        target = signal["target"]

        lots = lots_for_trade(self.client, symbol, direction, entry)
        if lots <= 0:
            log.warning(f"{symbol}: lot sizing returned 0 — skipping")
            return

        account = self.client.account_info()
        balance = float(getattr(account, "balance", 0) or 0)
        ok, reason, loss, cap = assess_entry_risk(
            self.client, symbol, direction, entry, stop, lots, balance
        )
        if not ok:
            if CONFIG.s147_attach_sl_tp:
                log.warning(f"{symbol}: risk guard blocked entry — {reason}")
                s147_events.log_5m("signal_rejected_risk_guard", {
                    "symbol": symbol, "reason": reason, "loss_at_sl": round(loss, 2),
                    "cap": None if cap == float("inf") else round(cap, 2), "lots": lots,
                }, key=f"{symbol}:risk:{signal['signal_bar_open']}")
                return
            log.warning(f"{symbol}: risk advisory (not blocking, SL is manual) — {reason}")
            s147_events.log_5m("risk_advisory", {
                "symbol": symbol, "reason": reason, "loss_at_sl": round(loss, 2),
                "cap": None if cap == float("inf") else round(cap, 2), "lots": lots,
            }, key=f"{symbol}:advisory:{signal['signal_bar_open']}")

        margin = 0.0
        if mt5 is not None:
            order_type = mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL
            margin = mt5.order_calc_margin(
                order_type, self.client.resolve_symbol(symbol), lots, entry
            ) or 0.0

        alert = {
            **signal,
            "lots": lots,
            "loss_at_sl": round(loss, 2),
            "est_margin": round(float(margin), 2),
            "attach_sl_tp": CONFIG.s147_attach_sl_tp,
            "entry_mode": CONFIG.s147_entry_mode,
            "dry_run": CONFIG.dry_run or self.trading_blocked,
        }
        self._email_signal(alert)
        s147_events.log_trade("signal_found", alert,
                              key=f"signal:{symbol}:{signal['signal_bar_open']}")

        if CONFIG.dry_run or self.trading_blocked:
            log.info(f"[{'DRY RUN' if CONFIG.dry_run else 'ORDERS BLOCKED'}] "
                     f"{symbol} would enter {direction} {lots} lots "
                     f"({CONFIG.s147_entry_mode}) @ {entry:.5f}")
            # A suppressed signal still has a knowable outcome; track it so live
            # expectancy is measurable without sending an order.
            self.tm.remember_paper(alert, int(now.timestamp()))
            return

        order_sl = stop if CONFIG.s147_attach_sl_tp else 0.0
        order_tp = target if CONFIG.s147_attach_sl_tp else 0.0
        res = self.client.open_trade(symbol, direction, lots, order_sl, order_tp, "s147")

        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            code = getattr(res, "retcode", "no result")
            comment = getattr(res, "comment", "")
            log.error(f"{symbol}: order rejected retcode={code} {comment}")
            s147_events.log_5m("order_rejected", {
                "symbol": symbol, "retcode": str(code), "comment": str(comment),
                "entry": entry, "stop": stop, "target": target, "lots": lots,
            }, key=f"{symbol}:reject:{signal['signal_bar_open']}")
            return

        ticket = int(getattr(res, "order", 0) or 0)
        fill_price = float(getattr(res, "price", 0) or 0)
        log.info(f"{symbol}: market order placed #{ticket} {lots} lots "
                 f"@ {fill_price or entry:.5f} | sl/tp attached={CONFIG.s147_attach_sl_tp}")
        s147_events.log_5m("order_placed", {
            "symbol": symbol, "order_ticket": ticket, "mode": CONFIG.s147_entry_mode,
            "direction": direction, "entry": entry, "fill_price": fill_price,
            "suggested_stop": stop, "suggested_target": target, "lots": lots,
            "sl_tp_attached": CONFIG.s147_attach_sl_tp,
        }, key=f"{symbol}:placed:{ticket}")
        self.tm.remember_order(signal, ticket, lots, int(now.timestamp()))

    def _email_signal(self, alert: dict) -> None:
        prefix = "[DRY RUN] " if alert.get("dry_run") else ""
        subject = (f"{prefix}S147 {alert['direction'].upper()} {alert['symbol']} "
                   f"@ {alert['trigger']:.5f} ({alert['reward_risk']}R) — SET SL/TP")
        if alert.get("attach_sl_tp"):
            reminder = (
                f"SL and TP were attached to the order automatically.\n"
                f"  Stop loss   : {alert['stop']:.5f}\n"
                f"  Take profit : {alert['target']:.5f}\n"
            )
        else:
            reminder = (
                f"*** REMINDER: this order was sent WITHOUT SL and TP. ***\n"
                f"Please set these levels in MT5 now:\n"
                f"  Suggested stop loss   : {alert['stop']:.5f}   "
                f"(just beyond the 15m refinement zone distal edge)\n"
                f"  Suggested take profit : {alert['target']:.5f}   "
                f"(fixed {alert['reward_risk']}R from entry)\n"
                f"  Estimated loss at that stop is Rs {alert['loss_at_sl']}.\n"
            )
        body = (
            f"{prefix}Strategy 147 trade taken — reaction at a 4H POI\n"
            f"----------------------------------------\n"
            f"Symbol       : {alert['symbol']}\n"
            f"Direction    : {alert['direction'].upper()}\n"
            f"Entry model  : {alert['model']}\n"
            f"Entry price  : {alert['trigger']:.5f}\n"
            f"Planned R:R  : {alert['reward_risk']}\n"
            f"Risk         : {alert['risk_pips']} pips "
            f"(round-turn cost {alert['round_turn_cost_pips']} pips)\n"
            f"Lots         : {alert['lots']}\n"
            f"Est. margin  : Rs {alert['est_margin']} (1:{CONFIG.leverage:.0f})\n"
            f"\n{reminder}"
            f"\nStructure, with formation times\n"
            f"  4H POI         : {alert['poi_zone_id']} ({alert['poi_type']}) "
            f"{alert['poi_lower']} - {alert['poi_upper']}\n"
            f"    formed       : {alert['poi_origin_time_ist']} "
            f"({alert['poi_origin_time_utc']})\n"
            f"    confirmed    : {alert['poi_confirmed_at_ist']}\n"
            f"    price arrived: {alert['poi_arrival_time_ist']}  (visit {alert['poi_visit']})\n"
            f"  15m CHoCH      : {alert['choch_level']} at {alert['choch_time_ist']}\n"
            f"  15m zone       : {alert['entry_zone_id']} "
            f"{alert['entry_zone_lower']} - {alert['entry_zone_upper']}\n"
            f"    formed       : {alert['entry_zone_origin_time_ist']}\n"
            f"    confirmed    : {alert['entry_zone_confirmed_at_ist']}\n"
            f"  5m zone alert  : {alert['alert_bar_close_ist']}\n"
            f"  5m confirmation: {alert['model']} @ {alert['confirmation_level']} "
            f"({alert['signal_bar_close_ist']})\n"
            f"  Swept level    : {alert['swept_level']}\n"
            f"\nJournals: {s147_events.journal_paths()}\n"
        )
        send_email(subject, body)
