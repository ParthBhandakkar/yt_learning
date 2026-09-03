"""
liveTrade engine for Strategy 146 — trade toward an unmitigated 4H zone.

Runs forever. Work happens once per CLOSED 5m candle:

  1. 4H zones are rebuilt from long history; unmitigated ones are the targets.
     Old 4H zones are allowed, which is the point of the strategy.
  2. 15m entry zones are tracked from the selected 4H destination's
     confirmation. A signal is allowed only from the running price extreme:
     highest still-actionable supply for shorts, lowest demand for longs.
  3. The 5m confirmation must be on the just-closed bar.
  4. An order is placed at the trigger with SL at the 15m zone distal edge plus
     buffer and TP at a fixed multiple of that risk (S146_TP_R, default 1.25R).
     The 4H destination is no longer the target, only the qualifier: it must sit
     at least S146_MIN_RR away for the signal to be taken at all.

Safety: DRY_RUN suppresses all order sending, and by default the engine refuses
to trade anything other than a DEMO account.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from config import CONFIG
from detection_s146 import (
    H4_SECONDS,
    cached_zones,
    detect_signal,
    live_params,
    to_candles,
    unmitigated_zones,
)
from logging_setup import format_ist, get_engine_logger
from mt5_client import MT5Client, TF_MINUTES, lots_for_trade
from notifier import send_email
from risk_guard import sl_loss_at_stop
from trade_manager_s146 import MAGIC, TradeManagerS146
import s146_events

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
UTC = timezone.utc


def _floor_utc(dt: datetime, minutes: int) -> datetime:
    epoch_minutes = int(dt.timestamp() // 60)
    return datetime.fromtimestamp(((epoch_minutes // minutes) * minutes) * 60, tz=UTC)


class EngineS146:
    def __init__(self):
        self.client = MT5Client(magic=MAGIC)
        self.tm = TradeManagerS146(self.client)
        self.last_boundary: datetime | None = None
        self.engine_start_ts = int(datetime.now(UTC).timestamp())
        self.trading_blocked = False
        # (symbol, destination_zone_id) -> (4H bar slot, verdict)
        self._destination_checks: dict[tuple[str, str], tuple[int, bool]] = {}

    # ------------------------------------------------------------------ startup
    def start(self):
        if not self.client.connect():
            log.error("Could not connect to MT5 — aborting.")
            return

        account = self.client.account_info()
        demo = self.client.is_demo()
        if CONFIG.s146_demo_only and not demo:
            self.trading_blocked = True
            log.error("S146_DEMO_ONLY=true but the connected account is NOT a demo account. "
                      "Detection will run; orders will NOT be sent.")

        lot_desc = (f"fixed {CONFIG.fixed_lot}" if CONFIG.fixed_lot > 0
                    else f"margin Rs{CONFIG.margin_per_trade:.0f} @ 1:{CONFIG.leverage:.0f}")
        params = live_params()
        journals = s146_events.journal_paths()
        summary = (
            f"Strategy 146 live engine started\n"
            f"----------------------------------------\n"
            f"Account   : {getattr(account, 'login', '?')} "
            f"{'DEMO' if demo else 'LIVE'} {getattr(account, 'currency', '?')} "
            f"bal={getattr(account, 'balance', '?')}\n"
            f"Symbols   : {', '.join(CONFIG.symbols)}\n"
            f"DRY_RUN   : {CONFIG.dry_run}\n"
            f"Demo only : {CONFIG.s146_demo_only} (orders blocked: {self.trading_blocked})\n"
            f"Sizing    : {lot_desc} | free-margin budget {CONFIG.s146_free_margin_fraction:.0%} | "
            f"max_concurrent={CONFIG.max_concurrent}\n"
            f"Exposure  : same destination <= {CONFIG.s146_max_same_destination} | "
            f"currency leg <= {CONFIG.s146_max_currency_exposure}\n"
            f"Cost gate : skip when spread > {CONFIG.s146_max_spread_pct_of_risk:.0%} of risk "
            f"| short stop spread pad {CONFIG.s146_short_stop_spread_pad}x\n"
            f"Entry mode: {CONFIG.s146_entry_mode} "
            f"(SL/TP attached: {CONFIG.s146_attach_sl_tp})\n"
            f"Risk guard : DISABLED for s146 (loss_at_sl is informational only)\n"
            f"Stop      : liquidity pool outside 15m zone when available, "
            f"else distal edge + {CONFIG.s146_stop_buffer_bps} bps buffer\n"
            f"Exit ladder: {CONFIG.s146_ladder_enabled} | partial {CONFIG.s146_partial_fraction:.0%} "
            f"at {CONFIG.s146_partial_at_r}R | trail step/giveback "
            f"{CONFIG.s146_trail_step_r}R/{CONFIG.s146_trail_giveback_r}R | "
            f"broker TP = 4H destination\n"
            f"Target    : {CONFIG.s146_tp_r}R partial milestone "
            f"(4H destination is the broker-side ceiling)\n"
            f"R:R band  : {params.min_target_reward_risk} - {params.max_target_reward_risk} "
            f"(measured to the 4H destination)\n"
            f"Hold limit: {params.max_hold_bars_5m} 5m bars\n"
            f"15m select: running extreme since 4H confirmation="
            f"{CONFIG.s146_require_running_extreme_15m} "
            f"(highest supply for short / lowest demand for long)\n"
            f"Legacy new-15m fallback: {CONFIG.s146_require_new_15m} "
            f"(engine start {format_ist(datetime.now(UTC))})\n"
            f"Journals  : {journals}\n"
        )
        log.info("liveTrade s146 started | " + summary.replace("\n", " | "))
        send_email("Strategy146 liveTrade STARTED", summary)

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
        self.tm.monitor(destination_alive=self._destination_alive)
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
                 f"symbols in {elapsed:.1f}s "
                 f"({elapsed / max(scanned, 1):.2f}s/symbol)")
        # 300s is the candle; warn with headroom so there is time to react before
        # detection actually slips a bar.
        if elapsed > 240:
            log.warning(
                f"5m scan took {elapsed:.0f}s of the 300s candle it runs on — signals risk "
                f"being detected a bar late. Reduce SYMBOLS (currently "
                f"{len(CONFIG.symbols)}) or lower S146_FETCH_4H_BARS/"
                f"S146_FETCH_15M_BARS/S146_FETCH_5M_BARS "
                f"(currently {CONFIG.s146_fetch_4h}/{CONFIG.s146_fetch_15m}/"
                f"{CONFIG.s146_fetch_5m})."
            )

    def _destination_alive(self, signal: dict) -> bool:
        """True while the signal's 4H destination is still unmitigated.

        Called from the pending-order monitor on every poll, so it must not
        refetch 4H history at poll frequency. A 4H bar cannot change more than
        once every four hours; anything sooner reuses the previous answer.
        """
        symbol = signal["symbol"]
        wanted = signal["destination_zone_id"]
        now = int(datetime.now(UTC).timestamp())
        bar_slot = now // H4_SECONDS
        cached = self._destination_checks.get((symbol, wanted))
        if cached is not None and cached[0] == bar_slot:
            return cached[1]

        df4 = self.client.fetch_closed(symbol, "4h", CONFIG.s146_fetch_4h)
        if df4 is None or len(df4) < 5:
            return True  # cannot prove otherwise; leave the order alone
        h4 = to_candles(df4)
        params = live_params()
        zones = cached_zones(symbol, "4h", h4, H4_SECONDS,
                             params.h4_swing_left, params.h4_swing_right)
        fresh_ids = {zone.zone_id for zone in unmitigated_zones(zones, h4)}
        known_ids = {zone.zone_id for zone in zones}
        # If the id is not in this window at all we cannot judge it; only treat it
        # as mitigated when the zone is present but no longer fresh.
        alive = wanted in fresh_ids or wanted not in known_ids
        self._destination_checks[(symbol, wanted)] = (bar_slot, alive)
        return alive

    # ---------------------------------------------------------------- exposure
    @staticmethod
    def _pair_currencies(symbol: str) -> tuple[str, str] | None:
        letters = "".join(ch for ch in str(symbol).upper() if ch.isalpha())
        if letters.endswith("M") and len(letters) == 7:
            letters = letters[:-1]
        return (letters[:3], letters[3:6]) if len(letters) >= 6 else None

    @classmethod
    def _currency_legs(cls, symbol: str, direction: str) -> list[str]:
        pair = cls._pair_currencies(symbol)
        if not pair:
            return []
        base, quote = pair
        return [f"{base}_{'long' if direction == 'long' else 'short'}",
                f"{quote}_{'short' if direction == 'long' else 'long'}"]

    def _active_exposure(self) -> tuple[list[dict], dict[str, int], dict[str, int]]:
        """Return active s146 records plus currency and destination counts.

        Pending orders are included so a burst of signals cannot reserve more
        correlated exposure than the configured limits.
        """
        records: list[dict] = []
        currencies: dict[str, int] = {}
        destinations: dict[str, int] = {}
        for position in self.client.open_positions(MAGIC):
            entry = self.tm.state.get(str(position.ticket), {})
            signal = entry.get("signal") or {}
            symbol_name = signal.get("symbol") or str(position.symbol).removesuffix("m")
            direction = signal.get("direction") or (
                "long" if position.type == mt5.POSITION_TYPE_BUY else "short")
            records.append({"kind": "filled", "symbol": symbol_name, "direction": direction,
                            "signal": signal, "position_ticket": int(position.ticket)})
        for order in self.client.pending_orders(MAGIC):
            entry = self.tm.state.get(str(order.ticket), {})
            signal = entry.get("signal") or {}
            if not signal:
                continue
            records.append({"kind": "pending", "symbol": signal.get("symbol"),
                            "direction": signal.get("direction"), "signal": signal,
                            "order_ticket": int(order.ticket)})
        for item in records:
            for leg in self._currency_legs(item["symbol"], item["direction"]):
                currencies[leg] = currencies.get(leg, 0) + 1
            destination = (item["signal"] or {}).get("destination_zone_id")
            if destination:
                destinations[destination] = destinations.get(destination, 0) + 1
        return records, currencies, destinations

    def _exposure_rejection(self, signal: dict) -> tuple[str | None, dict]:
        records, currencies, destinations = self._active_exposure()
        destination = signal.get("destination_zone_id")
        max_dest = CONFIG.s146_max_same_destination
        if destination and max_dest > 0 and destinations.get(destination, 0) >= max_dest:
            return "same_destination_cap", {"destination": destination,
                                             "active": destinations.get(destination, 0),
                                             "limit": max_dest}
        max_currency = CONFIG.s146_max_currency_exposure
        if max_currency > 0:
            for leg in self._currency_legs(signal["symbol"], signal["direction"]):
                if currencies.get(leg, 0) >= max_currency:
                    return "currency_exposure_cap", {"leg": leg,
                                                      "active": currencies.get(leg, 0),
                                                      "limit": max_currency}
        return None, {"active_trades": len(records), "currency": currencies,
                       "destinations": destinations}

    @staticmethod
    def _signal_id(signal: dict) -> str:
        raw = f"s146|{signal['symbol']}|{signal['direction']}|{signal['signal_bar_open']}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    # -------------------------------------------------------------------- cycle
    def _cycle(self, symbol: str, now: datetime):
        if CONFIG.one_trade_per_pair and self.client.position_for_symbol(symbol, MAGIC) is not None:
            return
        active_count = len(self.client.open_positions(MAGIC)) + len(self.client.pending_orders(MAGIC))
        if active_count >= CONFIG.max_concurrent:
            return
        if any(self.client.resolve_symbol(symbol) == o.symbol
               for o in self.client.pending_orders(MAGIC)):
            return

        signal = detect_signal(self.client, symbol, self.engine_start_ts)
        if signal is None:
            return

        log.info(
            f"S146 SIGNAL {symbol} {signal['direction'].upper()} {signal['model']} | "
            f"trigger={signal['trigger']:.5f} stop={signal['stop']:.5f} "
            f"target={signal['target']:.5f} ({signal['reward_risk']}R) | "
            f"stop_basis={signal.get('stop_basis', '?')} "
            f"liq_pool={bool(signal.get('stop_liquidity_pool'))} | "
            f"15m_extreme={signal.get('entry_zone_is_running_extreme', False)} "
            f"campaign_zones={signal.get('campaign_zone_count', 1)} | "
            f"4H dest={signal['destination_target']:.5f} "
            f"({signal['destination_reward_risk']}R)"
        )
        self._place(signal, now)

    def _place(self, signal: dict, now: datetime):
        symbol = signal["symbol"]
        direction = signal["direction"]
        trigger = signal["trigger"]
        stop = signal["stop"]
        target = signal["target"]
        signal_id = self._signal_id(signal)

        rejection, exposure = self._exposure_rejection(signal)
        if rejection:
            log.info(f"S146 {symbol}: signal blocked by {rejection} {exposure}")
            s146_events.log_5m("signal_blocked_exposure", {
                "signal_id": signal_id, "symbol": symbol, "direction": direction,
                "reason": rejection, "details": exposure,
                "destination_zone_id": signal.get("destination_zone_id"),
            }, key=f"exposure:{signal_id}")
            return

        # ---- pre-order destination-quality gate ----------------------
        # This deliberately uses requested trigger-to-stop risk, before any
        # short-side spread padding, lot sizing, or order submission. It is a
        # shadow-validation hypothesis and is disabled by default.
        spread = self.client.spread_now(symbol)
        requested_risk = abs(trigger - stop)
        quality_enabled = CONFIG.s146_preorder_destination_quality_gate
        quality_limits = {
            "max_destination_width_r": CONFIG.s146_preorder_max_destination_width_r,
            "max_destination_origin_lag_h4_bars": (
                CONFIG.s146_preorder_max_destination_origin_lag_h4_bars),
            "max_spread_pct_of_requested_risk": (
                CONFIG.s146_preorder_max_spread_pct_of_requested_risk),
        }
        quality: dict[str, object] = {
            "enabled": quality_enabled,
            "requested_risk": requested_risk,
            "spread": spread,
            "destination_zone_id": signal.get("destination_zone_id"),
            "destination_lower": signal.get("destination_lower"),
            "destination_upper": signal.get("destination_upper"),
            "destination_origin_time": signal.get("destination_origin_time"),
            "destination_confirmed_at": signal.get("destination_confirmed_at"),
            **quality_limits,
        }
        failed_checks: list[str] = []
        if quality_enabled:
            if requested_risk <= 0:
                failed_checks.append("invalid_requested_risk")
            else:
                spread_share = spread / requested_risk
                quality["spread_pct_of_requested_risk"] = spread_share * 100.0
                if (quality_limits["max_spread_pct_of_requested_risk"] > 0
                        and spread_share > quality_limits["max_spread_pct_of_requested_risk"]):
                    failed_checks.append("spread_pct_of_requested_risk")

            if quality_limits["max_destination_width_r"] > 0:
                try:
                    destination_width = abs(
                        float(signal["destination_upper"]) - float(signal["destination_lower"]))
                except (KeyError, TypeError, ValueError):
                    destination_width = None
                    failed_checks.append("missing_destination_bounds")
                if requested_risk > 0:
                    destination_width_r = (
                        destination_width / requested_risk if destination_width is not None else None)
                    quality["destination_width"] = destination_width
                    quality["destination_width_r"] = destination_width_r
                    if (destination_width_r is None
                            or destination_width_r > quality_limits["max_destination_width_r"]):
                        failed_checks.append("destination_width_r")

            if quality_limits["max_destination_origin_lag_h4_bars"] > 0:
                try:
                    origin_time = int(signal["destination_origin_time"])
                    confirmed_at = int(signal["destination_confirmed_at"])
                except (KeyError, TypeError, ValueError):
                    failed_checks.append("missing_destination_confirmation_metadata")
                else:
                    origin_lag_h4_bars = (confirmed_at - origin_time) / H4_SECONDS
                    quality["destination_origin_lag_h4_bars"] = origin_lag_h4_bars
                    if origin_lag_h4_bars > quality_limits["max_destination_origin_lag_h4_bars"]:
                        failed_checks.append("destination_origin_lag_h4_bars")

            if failed_checks:
                quality["failed_checks"] = failed_checks
                log.warning(
                    f"S146 {symbol}: skipped by pre-order destination-quality gate — "
                    f"{', '.join(failed_checks)} | requested_risk={requested_risk:.6f} "
                    f"spread={spread:.6f}"
                )
                s146_events.log_5m("signal_rejected_destination_quality", {
                    "signal_id": signal_id, "symbol": symbol, "direction": direction,
                    "reason": "preorder_destination_quality", **quality,
                }, key=f"destination_quality:{signal_id}")
                return

        # ---- cost gate -------------------------------------------------
        # The trade pays about two spreads round trip and a short's stop fires on
        # ask, one spread inside the structural level. If the spread is a large
        # share of the intended risk there is no room left for the move.
        structural_risk = requested_risk
        cost_share = (spread / structural_risk) if structural_risk > 0 else None
        limit = CONFIG.s146_max_spread_pct_of_risk
        if limit > 0 and cost_share is not None and cost_share > limit:
            log.warning(f"S146 {symbol}: skipped on spread cost — spread {spread:.6f} is "
                        f"{cost_share:.1%} of risk (limit {limit:.0%})")
            s146_events.log_5m("signal_rejected_spread_cost", {
                "signal_id": signal_id, "symbol": symbol, "direction": direction,
                "spread": spread, "structural_risk": structural_risk,
                "spread_pct_of_risk": round(cost_share * 100.0, 2),
                "round_trip_cost_r": round(2.0 * cost_share, 4),
                "limit_pct": round(limit * 100.0, 2),
            }, key=f"spread_cost:{signal_id}")
            return

        # Optional short-side stop correction, off by default.
        stop_multiple = 1.0
        if direction == "short" and CONFIG.s146_short_stop_spread_pad > 0 and spread > 0:
            padded = stop + spread * CONFIG.s146_short_stop_spread_pad
            padded_risk = padded - trigger
            if padded_risk > 0 and structural_risk > 0:
                stop_multiple = padded_risk / structural_risk
                stop = padded

        lots = lots_for_trade(self.client, symbol, direction, trigger)
        if lots <= 0:
            log.warning(f"{symbol}: lot sizing returned 0 — skipping")
            sizing_event = {
                "signal_id": signal_id, "symbol": symbol, "direction": direction,
                "requested_margin": CONFIG.margin_per_trade,
                "reason": "broker_minimum_or_margin_unavailable",
            }
            diagnostic = getattr(self.client, "last_margin_context", None)
            if diagnostic:
                sizing_event["margin_diagnostic"] = diagnostic
            s146_events.log_5m(
                "sizing_rejected", sizing_event, key=f"sizing:{signal_id}")
            return
        if stop_multiple > 1.0:
            # Wider stop must not mean more money at risk.
            lots = lots / stop_multiple

        lots, margin_snapshot = self.client.fit_lots_to_free_margin(
            symbol, direction, trigger, lots, CONFIG.s146_free_margin_fraction)
        if lots <= 0:
            log.warning(f"{symbol}: free-margin sizing returned 0 — skipping")
            s146_events.log_5m("sizing_rejected", {
                "signal_id": signal_id, "symbol": symbol, "direction": direction,
                "reason": "free_margin_budget_below_broker_minimum",
                **margin_snapshot,
            }, key=f"free_margin:{signal_id}")
            return

        # This is execution-availability sizing only. MAX_RISK_INR/MAX_RISK_PCT
        # remain deliberately unused for s146; loss_at_sl is informational.
        loss = sl_loss_at_stop(self.client, symbol, direction, trigger, stop, lots)
        margin = float(margin_snapshot.get("required_margin") or 0.0)
        alert = {
            **signal, "signal_id": signal_id, "lots": lots,
            "stop": stop, "structural_stop_unpadded": signal["stop"],
            "stop_multiple_vs_structural": round(stop_multiple, 4),
            "loss_at_sl": round(loss, 2), "est_margin": round(margin, 2),
            "spread_at_signal": spread,
            "spread_pct_of_risk": round(cost_share * 100.0, 2) if cost_share is not None else None,
            "round_trip_cost_r": round(2.0 * cost_share, 4) if cost_share is not None else None,
            "free_margin_snapshot": margin_snapshot,
            "exposure_snapshot": exposure,
            "attach_sl_tp": CONFIG.s146_attach_sl_tp,
            "entry_mode": CONFIG.s146_entry_mode,
            "dry_run": CONFIG.dry_run or self.trading_blocked,
        }
        self._email_signal(alert)
        s146_events.log_trade("signal_found", alert, key=f"signal:{signal_id}")

        if CONFIG.dry_run or self.trading_blocked:
            log.info(f"[{'DRY RUN' if CONFIG.dry_run else 'ORDERS BLOCKED'}] "
                     f"{symbol} would enter {direction} {lots} lots "
                     f"({CONFIG.s146_entry_mode}) @ {trigger:.5f}")
            self.tm.remember_paper(alert, int(now.timestamp()))
            return

        # With the ladder on, the broker TP is the 4H destination; the manager
        # banks the partial and trails the remainder.
        attached_target = (float(signal["destination_target"])
                           if CONFIG.s146_ladder_enabled and signal.get("destination_target")
                           else target)
        order_sl = stop if CONFIG.s146_attach_sl_tp else 0.0
        order_tp = attached_target if CONFIG.s146_attach_sl_tp else 0.0
        if CONFIG.s146_entry_mode == "stop":
            res = self.client.place_pending_stop(
                symbol, direction, lots, trigger, order_sl, order_tp,
                "s146 draw", None, validate=True)
        else:
            res = self.client.open_trade(
                symbol, direction, lots, order_sl, order_tp, "s146", validate=True)

        context = getattr(self.client, "last_order_context", {})
        success_codes = {mt5.TRADE_RETCODE_DONE,
                         getattr(mt5, "TRADE_RETCODE_PLACED", mt5.TRADE_RETCODE_DONE)}
        if res is None or res.retcode not in success_codes:
            check = context.get("order_check") or {}
            code = getattr(res, "retcode", None) if res is not None else None
            code = code if code is not None else check.get("retcode", "no result")
            comment = (getattr(res, "comment", "") if res is not None
                       else check.get("comment", ""))
            log.error(f"{symbol}: order rejected retcode={code} {comment}")
            s146_events.log_5m("order_rejected", {
                "signal_id": signal_id, "symbol": symbol,
                "broker_symbol": context.get("broker_symbol"),
                "retcode": str(code), "comment": str(comment),
                "trigger": trigger, "structural_stop": stop,
                "structural_target": target, "attached_target": attached_target,
                "lots": lots, "free_margin_snapshot": margin_snapshot,
                "request_context": context,
            }, key=f"reject:{signal_id}:{code}")
            return

        order_ticket = int(getattr(res, "order", 0) or 0)
        deal_ticket = int(getattr(res, "deal", 0) or 0)
        position_ticket = int(getattr(res, "position", 0) or 0)
        fill_price = float(getattr(res, "price", 0) or 0)
        log.info(f"{symbol}: {CONFIG.s146_entry_mode} order placed #{order_ticket} {lots} lots "
                 f"@ {fill_price or trigger:.5f} | sl/tp attached={CONFIG.s146_attach_sl_tp}")
        s146_events.log_5m("order_placed", {
            "signal_id": signal_id, "symbol": symbol,
            "broker_symbol": context.get("broker_symbol"),
            "order_ticket": order_ticket, "deal_ticket": deal_ticket,
            "position_ticket": position_ticket, "mode": CONFIG.s146_entry_mode,
            "direction": direction, "trigger": trigger, "fill_price": fill_price,
            "structural_stop": stop, "structural_target": target,
            "requested_sl": context.get("requested_sl"),
            "requested_tp": context.get("requested_tp"),
            "effective_sl": context.get("effective_sl"),
            "effective_tp": context.get("effective_tp"),
            "lots": lots, "sl_tp_attached": CONFIG.s146_attach_sl_tp,
            "free_margin_snapshot": margin_snapshot,
            "result_retcode": str(getattr(res, "retcode", "")),
            "result_comment": str(getattr(res, "comment", "")),
        }, key=f"placed:{signal_id}:{order_ticket}")
        self.tm.remember_order(
            alert, order_ticket, lots, int(now.timestamp()),
            deal_ticket=deal_ticket, position_ticket=position_ticket,
            broker_symbol=context.get("broker_symbol"), order_context=context)

    def _email_signal(self, alert: dict) -> None:
        prefix = "[DRY RUN] " if alert.get("dry_run") else ""
        attached = alert.get("attach_sl_tp")
        subject = (f"{prefix}S146 {alert['direction'].upper()} {alert['symbol']} "
                   f"@ {alert['trigger']:.5f} ({alert['reward_risk']}R target)"
                   + ("" if attached else " — SET SL/TP"))
        if attached:
            reminder = (
                f"SL and TP were attached to the order automatically.\n"
                f"  Stop loss   : {alert['stop']:.5f}   "
                f"({alert.get('stop_basis', 'structural stop')})\n"
                f"  Take profit : 4H destination {alert['destination_target']:.5f} "
                f"(partial {CONFIG.s146_partial_at_r}R is managed by the ladder)\n"
                f"  Estimated loss at that stop: Rs {alert['loss_at_sl']}.\n"
                f"  Ladder      : bank {CONFIG.s146_partial_fraction:.0%} at "
                f"{CONFIG.s146_partial_at_r}R, then trail by "
                f"{CONFIG.s146_trail_giveback_r}R on {CONFIG.s146_trail_step_r}R rungs.\n"
            )
        else:
            reminder = (
                f"*** REMINDER: this order was sent WITHOUT SL and TP. ***\n"
                f"Please set these levels in MT5 now:\n"
                f"  Suggested stop loss   : {alert['stop']:.5f}   "
                f"(just beyond the 15m zone distal edge)\n"
                f"  Suggested take profit : {alert['target']:.5f}   "
                f"({alert['reward_risk']}R from entry)\n"
                f"  Estimated loss at that stop is Rs {alert['loss_at_sl']}.\n"
            )
        body = (
            f"{prefix}Strategy 146 trade taken\n"
            f"----------------------------------------\n"
            f"Symbol       : {alert['symbol']}\n"
            f"Direction    : {alert['direction'].upper()}\n"
            f"Entry model  : {alert['model']}\n"
            f"Entry mode   : {alert['entry_mode']}\n"
            f"Signal price : {alert['trigger']:.5f}\n"
            f"Planned R:R  : {alert['reward_risk']} (fixed target)\n"
            f"Lots         : {alert['lots']}\n"
            f"Est. margin  : Rs {alert['est_margin']} (1:{CONFIG.leverage:.0f})\n"
            f"\n{reminder}"
            f"\nStructure\n"
            f"  4H destination : {alert['destination_zone_id']} "
            f"({alert['destination_type']}) "
            f"{alert['destination_lower']} - {alert['destination_upper']}\n"
            f"  4H draw would be worth {alert['destination_reward_risk']}R "
            f"at {alert['destination_target']:.5f} (qualifier only, not the TP)\n"
            f"  15m entry zone : {alert['entry_zone_id']} "
            f"{alert['entry_zone_lower']} - {alert['entry_zone_upper']}\n"
            f"  Running extreme: {alert.get('entry_zone_is_running_extreme')} "
            f"({alert.get('campaign_zone_count', 1)} actionable zone(s) since 4H confirmation)\n"
            f"  5m confirmation: {alert['model']} @ level {alert['confirmation_level']}\n"
            f"  Swept level    : {alert['swept_level']}\n"
            f"\nJournals: {s146_events.journal_paths()}\n"
        )
        send_email(subject, body)
