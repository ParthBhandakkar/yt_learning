"""
Position and pending-order management for Strategy 146.

SL and TP are attached to the order itself, so the broker always holds a stop and
a target at the 4H destination — the position stays protected even if this process
dies. This manager enforces the rules the broker cannot:

  * the exit ladder: bank a partial at S146_PARTIAL_AT_R, pull the stop to
    breakeven, then ratchet the stop behind each S146_TRAIL_STEP_R rung reached.
    MT5 cannot hold a partial-volume take profit, so the partial has to be a
    market close driven from here.
  * the hard holding limit (the draw toward a 4H zone has no deadline of its own)
  * cancelling a stop order that never triggered within its validity window
  * cancelling a stop order whose 4H destination got mitigated before the fill,
    because the reason for the trade no longer exists
  * journalling and emailing the exit once a position closes
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import CONFIG
from logging_setup import get_engine_logger, record_trade
from notifier import send_email
import s146_events

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
UTC = timezone.utc

MAGIC = 1460146
STATE_PATH = Path(__file__).resolve().parent / "passes" / "s146_active.json"


class TradeManagerS146:
    def __init__(self, client):
        self.client = client
        self.state: dict = self._load()
        # Tickets already reported as "could not set SL/TP", so a broker that
        # keeps refusing does not generate one email per poll.
        self._level_warned: set[str] = set()

    # ------------------------------------------------------------------ state
    def _load(self) -> dict:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save(self) -> None:
        try:
            STATE_PATH.parent.mkdir(exist_ok=True)
            with open(STATE_PATH, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=1, default=str)
        except OSError as exc:
            log.error(f"s146 state save failed: {exc}")

    def remember_order(self, signal: dict, order_ticket: int, lots: float,
                       placed_at: int, deal_ticket: int = 0,
                       position_ticket: int = 0, broker_symbol: str | None = None,
                       order_context: dict | None = None) -> None:
        identity = int(order_ticket or position_ticket or deal_ticket or 0)
        if identity <= 0:
            log.error("s146 cannot persist order linkage: MT5 returned no order/deal/position ID")
            return
        self.state[str(identity)] = {
            "kind": "pending", "signal": signal, "lots": lots,
            "placed_at": placed_at, "order_ticket": int(order_ticket or 0),
            "deal_ticket": int(deal_ticket or 0),
            "position_ticket": int(position_ticket or 0),
            "broker_symbol": broker_symbol,
            "order_context": order_context or {},
        }
        self.save()

    def remember_paper(self, alert: dict, opened_at: int) -> None:
        """Track a suppressed (dry-run / blocked) signal so it still produces an outcome.

        Without this a dry run emits `signal_found` and nothing else, so live
        expectancy can never be measured. Entry is assumed at the trigger, which
        matches the market entry mode and ignores spread and slippage — these are
        paper results, labelled as such, not a fill simulation.
        """
        key = f"paper:{alert['symbol']}:{alert['signal_bar_open']}"
        if key in self.state:
            return
        self.state[key] = {
            "kind": "paper",
            "signal": alert,
            "lots": alert.get("lots"),
            "entry_price": alert["trigger"],
            "opened_at": opened_at,
        }
        self.save()
        s146_events.log_trade("paper_trade_opened", {
            **alert,
            "entry_price": alert["trigger"],
            "opened_at": opened_at,
            "mode": "paper",
        }, key=f"paper_open:{alert['symbol']}:{alert['signal_bar_open']}")

    def context_for_symbol(self, broker_symbol: str) -> Optional[dict]:
        for entry in self.state.values():
            signal = entry.get("signal") or {}
            resolved = self.client.resolve_symbol(signal.get("symbol", ""))
            if resolved == broker_symbol:
                return entry
        return None

    # ---------------------------------------------------------------- monitor
    def monitor(self, destination_alive=None) -> None:
        """One maintenance pass. Safe to call on every poll."""
        if not self.client.ensure():
            return
        self._monitor_pending(destination_alive)
        self._monitor_positions()
        self._monitor_paper()

    def _monitor_pending(self, destination_alive) -> None:
        """Resting stop orders are GTC: they are never cancelled for age.

        The only reason to withdraw one is that its 4H destination was reached
        before the fill, which removes the entire purpose of the trade.
        """
        for order in self.client.pending_orders(MAGIC):
            entry = self.state.get(str(order.ticket))
            signal = (entry or {}).get("signal") or {}
            if destination_alive is None or not signal:
                continue
            try:
                if not destination_alive(signal):
                    self._cancel(order, signal, "4H destination mitigated before fill")
            except Exception as exc:  # never let a check kill the loop
                log.error(f"destination check failed: {exc}")

    def _cancel(self, order, signal: dict, reason: str) -> None:
        res = self.client.cancel_order(order)
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"s146 cancel pending #{order.ticket} ({reason}) ok={ok}")
        s146_events.log_5m("pending_order_cancelled", {
            "symbol": signal.get("symbol", order.symbol),
            "order_ticket": int(order.ticket),
            "reason": reason,
            "ok": bool(ok),
        }, key=f"cancel:{order.ticket}")
        if ok:
            self.state.pop(str(order.ticket), None)
            self.save()

    def _monitor_positions(self) -> None:
        positions = self.client.open_positions(MAGIC)
        live_tickets = {str(p.ticket) for p in positions}

        for position in positions:
            entry = self.state.get(str(position.ticket))
            if entry is None:
                # First time we see the fill: promote the pending record.
                entry = self._promote_fill(position)
            else:
                self._protect_if_unset(position, entry)
            try:
                self._manage_ladder(position, entry)
            except Exception as exc:  # a ladder fault must not stop the loop
                log.error(f"s146 ladder failed for #{position.ticket}: {exc}")
            self._enforce_hold_limit(position, entry)

        # Anything we were tracking as filled but is gone has closed.
        for ticket, entry in list(self.state.items()):
            if entry.get("kind") == "filled" and ticket not in live_tickets:
                self._record_close(ticket, entry)

    def _promote_fill(self, position) -> dict:
        pending = None
        pending_key = None
        # Prefer the position ID returned by MT5 at order submission.
        for key, entry in self.state.items():
            if entry.get("kind") == "pending" and int(entry.get("position_ticket") or 0) == int(position.ticket):
                pending, pending_key = entry, key
                break
        # If MT5 did not return position on the send result, resolve through the
        # actual position's deal history and the stored order/deal IDs.
        position_deals = []
        if pending is None and mt5 is not None:
            try:
                position_deals = list(mt5.history_deals_get(position=int(position.ticket)) or [])
            except Exception:
                position_deals = []
            deal_ids = {int(getattr(item, "ticket", 0) or 0) for item in position_deals}
            order_ids = {int(getattr(item, "order", 0) or 0) for item in position_deals}
            for key, entry in self.state.items():
                if entry.get("kind") != "pending":
                    continue
                if (int(entry.get("order_ticket") or 0) in order_ids
                        or int(entry.get("deal_ticket") or 0) in deal_ids):
                    pending, pending_key = entry, key
                    break
        signal = (pending or {}).get("signal") or {}
        if pending is None:
            log.error(f"s146 FILLED #{position.ticket} {position.symbol} has no exact signal linkage")
            s146_events.log_5m("entry_unlinked", {
                "position_ticket": int(position.ticket), "broker_symbol": position.symbol,
                "reason": "no_matching_position_order_or_deal_id",
            }, key=f"unlinked:{position.ticket}")
        sl_now, tp_now = self._align_levels(position, signal)
        record = {
            "kind": "filled", "signal": signal,
            "order_ticket": int((pending or {}).get("order_ticket") or 0),
            "deal_ticket": int((pending or {}).get("deal_ticket") or 0),
            "position_ticket": int(position.ticket),
            "broker_symbol": position.symbol,
            "lots": float(position.volume), "entry_price": float(position.price_open),
            "opened_at": int(position.time), "sl": sl_now, "tp": tp_now,
        }
        if pending_key is not None:
            self.state.pop(pending_key, None)
        self.state[str(position.ticket)] = record
        self.save()

        log.info(f"s146 FILLED #{position.ticket} {position.symbol} @ {position.price_open} "
                 f"| broker sl={sl_now} tp={tp_now}")
        s146_events.log_5m("entry_filled", {
            "signal_id": signal.get("signal_id"),
            "symbol": signal.get("symbol", position.symbol),
            "broker_symbol": position.symbol,
            "order_ticket": int(record.get("order_ticket") or 0),
            "deal_ticket": int(record.get("deal_ticket") or 0),
            "position_ticket": int(position.ticket),
            "price": float(position.price_open),
            "lots": float(position.volume),
            "broker_sl": sl_now,
            "broker_tp": tp_now,
            "suggested_stop": signal.get("stop"),
            "suggested_target": signal.get("target"),
        }, key=f"fill:{position.ticket}")
        s146_events.log_trade("trade_opened", {
            **signal,
            "order_ticket": int(record.get("order_ticket") or 0),
            "deal_ticket": int(record.get("deal_ticket") or 0),
            "position_ticket": int(position.ticket),
            "entry_price": float(position.price_open),
            "lots": float(position.volume),
            "sl": sl_now,
            "tp": tp_now,
            "opened_at": int(position.time),
        }, key=f"opened:{position.ticket}")
        record_trade({"strategy": "s146", "event": "opened",
                      "ticket": int(position.ticket), **signal,
                      "entry_price": float(position.price_open),
                      "lots": float(position.volume)})

        # A market entry was already announced by the signal email moments ago.
        # Only a resting stop order needs its own "it filled" notification.
        missing_levels = not sl_now or not tp_now
        if CONFIG.s146_entry_mode == "stop" or missing_levels:
            if missing_levels:
                levels = (
                    f"*** REMINDER: no SL/TP is set on this position. ***\n"
                    f"  Suggested stop loss   : {signal.get('stop', '?')}   "
                    f"(beyond the 15m zone distal edge)\n"
                    f"  Suggested take profit : {signal.get('target', '?')}   "
                    f"({signal.get('target_r_multiple', CONFIG.s146_tp_r)}R from entry)\n"
                )
            else:
                levels = (f"Stop   : {sl_now}\n"
                          f"Target : {tp_now}\n")
            send_email(
                f"S146 POSITION OPEN {position.symbol} "
                f"{'BUY' if position.type == 0 else 'SELL'} @ {position.price_open}"
                + ("  — SET SL/TP" if missing_levels else ""),
                f"Strategy 146 position is open\n"
                f"----------------------------------------\n"
                f"Symbol      : {position.symbol}\n"
                f"Lots        : {position.volume}\n"
                f"Entry       : {position.price_open}\n"
                f"Planned R:R : {signal.get('reward_risk', '?')}\n"
                f"Entry zone  : {signal.get('entry_zone_id', '?')}\n"
                f"Destination : {signal.get('destination_zone_id', '?')} "
                f"({signal.get('destination_type', '?')})\n"
                f"\n{levels}",
            )
        return record

    def _protect_if_unset(self, position, entry: dict) -> None:
        """Back-fill SL/TP on an already-tracked position that is missing them.

        Covers what _promote_fill cannot: an engine restart, a position opened
        while SL/TP attachment was off, or an earlier level update the broker
        rejected. A position that already carries both levels is left untouched,
        so a stop moved by hand is never overwritten.
        """
        if not CONFIG.s146_attach_sl_tp:
            return
        if position.sl and position.tp:
            return
        sl_now, tp_now = self._align_levels(position, entry.get("signal") or {})
        if (sl_now, tp_now) != (entry.get("sl"), entry.get("tp")):
            entry["sl"], entry["tp"] = sl_now, tp_now
            self.save()

    def _align_levels(self, position, signal: dict) -> tuple[float, float]:
        """Anchor the take profit to a fixed R multiple of the ACTUAL fill.

        The order is sent before its fill price is known, so the TP that went out
        with it was derived from the signal trigger. R is defined here as the
        distance from the fill to the structural stop (the 15m entry zone's distal
        edge plus buffer), so once the fill is known the target is corrected to
        CONFIG.s146_tp_r of that distance. The stop itself is structural and never
        moves. Returns the (sl, tp) actually on the position.
        """
        sl_now = float(position.sl or 0.0)
        tp_now = float(position.tp or 0.0)
        if not CONFIG.s146_attach_sl_tp or mt5 is None:
            return sl_now, tp_now

        stop = signal.get("stop")
        if not stop:
            return sl_now, tp_now
        stop = float(stop)
        entry_price = float(position.price_open)
        long_side = position.type == mt5.POSITION_TYPE_BUY
        risk = (entry_price - stop) if long_side else (stop - entry_price)
        if risk <= 0:
            log.warning(f"s146 #{position.ticket}: fill is already beyond the stop "
                        f"(entry={entry_price} stop={stop}) — leaving levels alone")
            return sl_now, tp_now

        tp_r = float(signal.get("target_r_multiple") or CONFIG.s146_tp_r)
        destination = signal.get("destination_target")
        if CONFIG.s146_ladder_enabled and destination:
            # The ladder banks S146_PARTIAL_AT_R itself with a market close, so the
            # broker target is the structural ceiling instead. A full-volume TP at
            # the partial level would close the whole position and leave no runner.
            wanted_tp = float(destination)
            tp_r = round(abs(wanted_tp - entry_price) / risk, 2)
        else:
            wanted_tp = entry_price + risk * tp_r if long_side else entry_price - risk * tp_r

        si = self.client.symbol_info(signal.get("symbol", position.symbol))
        tolerance = float(getattr(si, "point", 0.0) or 0.0) or 1e-9
        if abs(tp_now - wanted_tp) <= tolerance and abs(sl_now - stop) <= tolerance:
            return sl_now, tp_now
        if CONFIG.dry_run:
            log.info(f"[DRY RUN] would set #{position.ticket} sl={stop:.5f} tp={wanted_tp:.5f}")
            return sl_now, tp_now

        res = self.client.modify_sl_tp(position, stop, wanted_tp, validate=True)
        context = getattr(self.client, "last_order_context", {})
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"s146 #{position.ticket} levels -> sl={stop:.5f} tp={wanted_tp:.5f} "
                 f"({tp_r}R of {risk:.5f} from fill {entry_price:.5f}) ok={ok}")
        s146_events.log_5m("levels_anchored_to_fill", {
            "signal_id": signal.get("signal_id"),
            "order_ticket": int((self.state.get(str(position.ticket)) or {}).get("order_ticket") or 0),
            "deal_ticket": int((self.state.get(str(position.ticket)) or {}).get("deal_ticket") or 0),
            "symbol": signal.get("symbol", position.symbol),
            "position_ticket": int(position.ticket),
            "requested_sl": round(stop, 8),
            "requested_tp": round(wanted_tp, 8),
            "effective_sl": context.get("effective_sl"),
            "effective_tp": context.get("effective_tp"),
            "entry_price": entry_price,
            "tp_r_multiple": tp_r,
            "risk_price": round(risk, 8),
            "sl_before": sl_now,
            "tp_before": tp_now,
            "ok": bool(ok),
            "retcode": str(getattr(res, "retcode", "no result")),
            "comment": str(getattr(res, "comment", "")),
            # A rejected attempt may be retried on a later poll, so the outcome is
            # part of the key: the eventual success still gets journalled.
        }, key=f"levels:{position.ticket}:{'ok' if ok else 'fail'}")
        if not ok:
            log.error(f"s146 #{position.ticket}: SL/TP update rejected "
                      f"{getattr(res, 'retcode', 'no result')} "
                      f"{getattr(res, 'comment', '')} — position may be unprotected")
            if str(position.ticket) in self._level_warned:
                return sl_now, tp_now
            self._level_warned.add(str(position.ticket))
            send_email(
                f"S146 SL/TP NOT SET {position.symbol} #{position.ticket} — ACT NOW",
                f"Strategy 146 could not place SL/TP on a live position.\n"
                f"----------------------------------------\n"
                f"Symbol : {position.symbol}\n"
                f"Ticket : {position.ticket}\n"
                f"Lots   : {position.volume}\n"
                f"Entry  : {entry_price}\n"
                f"On the position now : sl={sl_now} tp={tp_now}\n"
                f"Wanted : sl={stop} tp={wanted_tp} ({tp_r}R)\n"
                f"Broker : {getattr(res, 'retcode', 'no result')} "
                f"{getattr(res, 'comment', '')}\n"
                f"\nSet these by hand in MT5.\n",
            )
            return sl_now, tp_now
        return round(stop, 8), round(wanted_tp, 8)

    # ----------------------------------------------------------------- ladder
    def _ladder_stop_r(self, peak_r: float, current_sl_r: Optional[float]) -> Optional[float]:
        """Where the stop belongs, in R from entry, for a given peak excursion.

        Nothing happens below the partial level. At the partial the stop comes to
        breakeven, and from the first rung ABOVE the partial it follows
        S146_TRAIL_GIVEBACK_R behind the highest rung reached:

            peak 1.25R -> breakeven      peak 2.0R -> +1.5R
            peak 1.5R  -> +1.0R          peak 2.5R -> +2.0R

        Returns None when the structural stop should be left alone.
        """
        partial_at = CONFIG.s146_partial_at_r
        step = CONFIG.s146_trail_step_r
        giveback = CONFIG.s146_trail_giveback_r
        wanted = current_sl_r
        if peak_r >= partial_at:
            wanted = max(wanted, 0.0) if wanted is not None else 0.0
        if step > 0:
            rung = math.floor(round(peak_r / step, 6)) * step
            if rung > partial_at:
                trailed = round(rung - giveback, 6)
                wanted = trailed if wanted is None else max(wanted, trailed)
        return wanted

    def _manage_ladder(self, position, entry: dict) -> None:
        """Bank the partial and ratchet the stop as the trade advances."""
        if not CONFIG.s146_ladder_enabled or mt5 is None:
            return
        signal = entry.get("signal") or {}
        stop = signal.get("stop")
        if not stop:
            return
        entry_price = float(entry.get("entry_price") or position.price_open)
        long_side = position.type == mt5.POSITION_TYPE_BUY
        risk = (entry_price - float(stop)) if long_side else (float(stop) - entry_price)
        if risk <= 0:
            return

        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            return
        # Measure against the price that would actually close the position.
        price = float(tick.bid if long_side else tick.ask)
        moved = (price - entry_price) if long_side else (entry_price - price)
        r_now = moved / risk

        ladder = entry.setdefault(
            "ladder", {"peak_r": 0.0, "partial_done": False, "sl_r": None})
        peak_r = max(float(ladder.get("peak_r") or 0.0), r_now)
        changed = peak_r > float(ladder.get("peak_r") or 0.0)
        ladder["peak_r"] = round(peak_r, 4)

        if peak_r >= CONFIG.s146_partial_at_r and not ladder.get("partial_done"):
            if self._bank_partial(position, signal, ladder, peak_r):
                changed = True

        wanted_sl_r = self._ladder_stop_r(peak_r, ladder.get("sl_r"))
        if wanted_sl_r is not None and wanted_sl_r != ladder.get("sl_r"):
            if self._ratchet_stop(position, signal, entry_price, risk,
                                  long_side, price, wanted_sl_r, peak_r):
                ladder["sl_r"] = wanted_sl_r
                changed = True

        if changed:
            self.save()

    def _bank_partial(self, position, signal: dict, ladder: dict, peak_r: float) -> bool:
        """Close S146_PARTIAL_FRACTION at market. Returns True if state changed."""
        fraction = CONFIG.s146_partial_fraction
        lots = self.client.split_volume(position.symbol, float(position.volume), fraction)
        symbol = signal.get("symbol", position.symbol)
        if lots <= 0:
            # 0.01 lots cannot be halved: run the whole position on the trail
            # instead, and stop retrying every poll.
            ladder["partial_done"] = True
            ladder["partial_skipped"] = "volume below broker minimum"
            log.info(f"s146 #{position.ticket} partial skipped: {position.volume} lots "
                     f"cannot be split — trailing the full position")
            s146_events.log_5m("partial_skipped", {
                "symbol": symbol,
                "position_ticket": int(position.ticket),
                "volume": float(position.volume),
                "fraction": fraction,
                "reason": "volume below broker minimum",
            }, key=f"partial_skip:{position.ticket}")
            return True
        if CONFIG.dry_run:
            log.info(f"[DRY RUN] would bank {lots} of {position.volume} lots on "
                     f"#{position.ticket} at {peak_r:.2f}R")
            return False

        res = self.client.close_partial(
            position, lots, f"s146 partial {CONFIG.s146_partial_at_r}R")
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"s146 #{position.ticket} partial {lots}/{position.volume} lots "
                 f"at {peak_r:.2f}R ok={ok}")
        s146_events.log_5m("partial_banked", {
            "signal_id": signal.get("signal_id"),
            "symbol": symbol,
            "position_ticket": int(position.ticket),
            "order_ticket": int((self.state.get(str(position.ticket)) or {}).get("order_ticket") or 0),
            "deal_ticket": int(getattr(res, "deal", 0) or 0),
            "lots_closed": lots,
            "lots_before": float(position.volume),
            "at_r": round(peak_r, 3),
            "target_r": CONFIG.s146_partial_at_r,
            "ok": bool(ok),
            "retcode": str(getattr(res, "retcode", "no result")),
            "comment": str(getattr(res, "comment", "")),
        }, key=f"partial:{position.ticket}:{'ok' if ok else 'fail'}")
        if not ok:
            log.error(f"s146 #{position.ticket}: partial close rejected "
                      f"{getattr(res, 'retcode', 'no result')} "
                      f"{getattr(res, 'comment', '')}")
            return False
        ladder["partial_done"] = True
        ladder["partial_lots"] = lots
        return True

    def _ratchet_stop(self, position, signal: dict, entry_price: float, risk: float,
                      long_side: bool, price: float, sl_r: float, peak_r: float) -> bool:
        """Move the stop to `sl_r` R from entry. Never moves it against the trade."""
        wanted = entry_price + risk * sl_r if long_side else entry_price - risk * sl_r
        current = float(position.sl or 0.0)
        symbol = signal.get("symbol", position.symbol)
        si = self.client.symbol_info(symbol)
        point = float(getattr(si, "point", 0.0) or 0.0) or 1e-9

        # The broker refuses a stop closer to price than trade_stops_level, and
        # modify_sl_tp silently CLAMPS it away instead of failing. On a tight
        # structural risk that clamp can land the "breakeven" stop below entry, so
        # check the level we would really end up with and skip if it is no better.
        effective, _ = self.client.safe_levels(
            position.symbol, "long" if long_side else "short",
            price, wanted, float(position.tp or 0.0),
        )
        if current:
            improved = (effective > current + point / 2.0) if long_side \
                else (effective < current - point / 2.0)
            if not improved:
                return False
        if CONFIG.dry_run:
            log.info(f"[DRY RUN] would ratchet #{position.ticket} sl -> {effective:.5f} "
                     f"({sl_r:+.2f}R, peak {peak_r:.2f}R)")
            return False

        res = self.client.modify_sl_tp(position, wanted, float(position.tp or 0.0), validate=True)
        context = getattr(self.client, "last_order_context", {})
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"s146 #{position.ticket} ladder sl -> {effective:.5f} "
                 f"({sl_r:+.2f}R at peak {peak_r:.2f}R) ok={ok}")
        s146_events.log_5m("ladder_stop_moved", {
            "signal_id": signal.get("signal_id"),
            "position_ticket": int(position.ticket),
            "order_ticket": int((self.state.get(str(position.ticket)) or {}).get("order_ticket") or 0),
            "deal_ticket": int((self.state.get(str(position.ticket)) or {}).get("deal_ticket") or 0),
            "symbol": symbol,
            "sl_before": current,
            "sl_wanted": round(wanted, 8),
            "sl_effective": round(effective, 8),
            "request_context": context,
            "sl_r": round(sl_r, 3),
            "peak_r": round(peak_r, 3),
            "clamped_by_broker": abs(effective - wanted) > point / 2.0,
            "ok": bool(ok),
            "retcode": str(getattr(res, "retcode", "no result")),
            "comment": str(getattr(res, "comment", "")),
        }, key=f"ladder:{position.ticket}:{sl_r}:{'ok' if ok else 'fail'}")
        if not ok:
            log.error(f"s146 #{position.ticket}: ladder stop rejected "
                      f"{getattr(res, 'retcode', 'no result')} "
                      f"{getattr(res, 'comment', '')}")
        return ok

    def _enforce_hold_limit(self, position, entry: dict) -> None:
        limit = CONFIG.s146_max_hold_bars_5m
        if limit <= 0:
            return
        opened = int(entry.get("opened_at") or position.time)
        held_bars = int((datetime.now(UTC).timestamp() - opened) // 300)
        if held_bars < limit:
            return
        if CONFIG.dry_run:
            log.info(f"[DRY RUN] would close #{position.ticket} on hold limit ({held_bars} bars)")
            return
        res = self.client.close_position(position, "s146 time expiry")
        ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
        log.info(f"s146 time expiry close #{position.ticket} after {held_bars} bars ok={ok}")
        s146_events.log_5m("time_expiry_close", {
            "symbol": position.symbol,
            "position_ticket": int(position.ticket),
            "bars_held_5m": held_bars,
            "ok": bool(ok),
        }, key=f"expiry:{position.ticket}")

    def _monitor_paper(self) -> None:
        """Resolve dry-run signals against closed 5m bars and journal the outcome."""
        for key, entry in list(self.state.items()):
            if entry.get("kind") != "paper":
                continue
            try:
                self._resolve_paper(key, entry)
            except Exception as exc:  # never let paper accounting kill the loop
                log.error(f"paper resolve failed for {key}: {exc}")

    def _resolve_paper(self, key: str, entry: dict) -> None:
        signal = entry.get("signal") or {}
        symbol = signal.get("symbol")
        if not symbol:
            self.state.pop(key, None)
            return

        opened_at = int(entry.get("opened_at") or 0)
        long_side = signal.get("direction") == "long"
        stop = float(signal["stop"])
        entry_price = float(entry.get("entry_price") or signal["trigger"])
        risk = abs(entry_price - stop)
        if risk <= 0:
            self.state.pop(key, None)
            return

        ladder_on = CONFIG.s146_ladder_enabled
        destination = signal.get("destination_target")
        # With the ladder on, the broker target is the structural ceiling and the
        # partial level is worked by the manager. Mirror that here exactly.
        target = float(destination) if (ladder_on and destination) else float(signal["target"])

        df = self.client.fetch_closed(symbol, "5m", CONFIG.s146_fetch_5m)
        if df is None or len(df) == 0:
            return

        partial_at = CONFIG.s146_partial_at_r
        fraction = CONFIG.s146_partial_fraction if ladder_on else 0.0

        def price_at(r_level: float) -> float:
            return entry_price + risk * r_level if long_side \
                else entry_price - risk * r_level

        def r_of(price: float) -> float:
            moved = (price - entry_price) if long_side else (entry_price - price)
            return moved / risk

        open_fraction = 1.0
        realized_r = 0.0
        sl_r: Optional[float] = None      # None = original structural stop
        peak_r = 0.0
        partial_done = False
        exit_reason = None
        exit_price = None
        exit_at = None
        bars_held = 0

        for stamp, row in df.iterrows():
            bar_open = int(stamp.timestamp())
            if bar_open < opened_at:
                continue
            bars_held += 1
            high, low = float(row.high), float(row.low)
            stop_price = stop if sl_r is None else price_at(sl_r)

            # Both touched inside one bar: 5m data cannot say which came first, so
            # assume the stop. Never let paper results flatter the strategy.
            hit_stop = low <= stop_price if long_side else high >= stop_price
            if hit_stop:
                realized_r += open_fraction * (sl_r if sl_r is not None else -1.0)
                exit_reason = "paper_trail_stop" if sl_r is not None else "paper_stop_loss"
                exit_price, exit_at = stop_price, bar_open + 300
                break

            peak_r = max(peak_r, r_of(high if long_side else low))

            # The partial sits below the target, so it always fills first.
            if ladder_on and not partial_done and peak_r >= partial_at and fraction > 0:
                realized_r += open_fraction * fraction * partial_at
                open_fraction *= (1.0 - fraction)
                partial_done = True
                sl_r = self._ladder_stop_r(peak_r, sl_r)

            hit_target = high >= target if long_side else low <= target
            if hit_target:
                realized_r += open_fraction * r_of(target)
                exit_reason = "paper_take_profit"
                exit_price, exit_at = target, bar_open + 300
                break

            sl_r = self._ladder_stop_r(peak_r, sl_r)

        limit = CONFIG.s146_max_hold_bars_5m
        if exit_reason is None and limit > 0 and bars_held >= limit:
            last = df.iloc[-1]
            exit_price = float(last.close)
            realized_r += open_fraction * r_of(exit_price)
            exit_reason = "paper_time_expiry"
            exit_at = int(df.index[-1].timestamp()) + 300

        if exit_reason is None:
            return

        r_multiple = round(realized_r, 3)
        payload = {
            **signal,
            "mode": "paper",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "r_multiple": r_multiple,
            "peak_r": round(peak_r, 3),
            "partial_banked": bool(partial_done),
            "partial_fraction": fraction if partial_done else 0.0,
            "final_stop_r": sl_r,
            "ladder": ladder_on,
            "bars_held_5m": bars_held,
            "opened_at": opened_at,
            "closed_at": exit_at,
            "profit_account_ccy": None,
        }
        s146_events.log_trade("paper_trade_closed", payload, key=f"paper_close:{key}")
        record_trade({"strategy": "s146", "event": "paper_closed", **payload})
        log.info(f"s146 PAPER {symbol} {exit_reason} | R={r_multiple} "
                 f"entry={entry_price:.5f} exit={exit_price:.5f} bars={bars_held}")
        self.state.pop(key, None)
        self.save()

    def _record_close(self, ticket: str, entry: dict) -> None:
        signal = entry.get("signal") or {}
        profit = None
        closed_at = None
        reason = "closed"
        if mt5 is not None:
            deals = mt5.history_deals_get(position=int(ticket))
            if deals:
                profit = sum(float(d.profit) + float(d.swap) + float(d.commission) for d in deals)
                closed_at = int(max(d.time for d in deals))
                last = max(deals, key=lambda d: d.time)
                mapping = {
                    getattr(mt5, "DEAL_REASON_TP", 4): "take_profit",
                    getattr(mt5, "DEAL_REASON_SL", 3): "stop_loss",
                }
                reason = mapping.get(getattr(last, "reason", None), "closed")
        payload = {
            **signal,
            "signal_id": signal.get("signal_id"),
            "order_ticket": int(entry.get("order_ticket") or 0),
            "deal_ticket": int(entry.get("deal_ticket") or 0),
            "position_ticket": int(ticket),
            "entry_price": entry.get("entry_price"),
            "lots": entry.get("lots"),
            "sl": entry.get("sl"),
            "tp": entry.get("tp"),
            "exit_reason": reason,
            "profit_account_ccy": profit,
            "closed_at": closed_at,
        }
        s146_events.log_trade("trade_closed", payload, key=f"closed:{ticket}")
        record_trade({"strategy": "s146", "event": "closed", "ticket": int(ticket), **payload})
        log.info(f"s146 position #{ticket} closed | reason={reason} | pnl={profit}")
        send_email(
            f"S146 CLOSED {signal.get('symbol', '')} {reason} pnl={profit}",
            f"Strategy 146 position closed\n"
            f"----------------------------------------\n"
            f"Symbol : {signal.get('symbol', '')}\n"
            f"Ticket : {ticket}\n"
            f"Reason : {reason}\n"
            f"P&L    : {profit} {CONFIG.account_ccy}\n"
            f"Entry  : {entry.get('entry_price')}\n"
            f"Stop   : {entry.get('sl')}\n"
            f"Target : {entry.get('tp')}\n",
        )
        self.state.pop(ticket, None)
        self.save()
