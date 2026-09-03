"""
Position and paper-outcome management for Strategy 147.

S147 has no holding limit: a trade runs until its stop or its target, so unlike
S146 there is no time-expiry rule to enforce. What this manager does:

  * promotes a fill into a tracked position and journals `trade_opened`
  * journals `trade_closed` with the realized R once a position disappears
  * resolves DRY-RUN signals against closed 5m bars into paper_trade_opened /
    paper_trade_closed, so expectancy is measurable even when no order is sent

Every journalled timestamp carries epoch plus readable UTC and IST.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import CONFIG
from logging_setup import get_engine_logger, record_trade
from notifier import send_email
import s147_events

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
UTC = timezone.utc

MAGIC = 1470147
STATE_PATH = Path(__file__).resolve().parent / "passes" / "s147_active.json"


class TradeManagerS147:
    def __init__(self, client):
        self.client = client
        self.state: dict = self._load()

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
            log.error(f"s147 state save failed: {exc}")

    def remember_order(self, signal: dict, order_ticket: int, lots: float, placed_at: int) -> None:
        self.state[str(order_ticket)] = {
            "kind": "pending",
            "signal": signal,
            "lots": lots,
            "placed_at": placed_at,
        }
        self.save()

    def remember_paper(self, alert: dict, opened_at: int) -> None:
        """Track a suppressed (dry-run / blocked) signal so it still yields an outcome."""
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
        s147_events.log_trade("paper_trade_opened", {
            **alert,
            "entry_price": alert["trigger"],
            "mode": "paper",
            **s147_events.stamp("opened_at", opened_at),
        }, key=f"paper_open:{alert['symbol']}:{alert['signal_bar_open']}")

    # ---------------------------------------------------------------- monitor
    def monitor(self) -> None:
        """One maintenance pass. Safe to call on every poll."""
        if not self.client.ensure():
            return
        self._monitor_positions()
        self._monitor_paper()

    def _monitor_positions(self) -> None:
        positions = self.client.open_positions(MAGIC)
        live_tickets = {str(p.ticket) for p in positions}
        for position in positions:
            if self.state.get(str(position.ticket)) is None:
                self._promote_fill(position)
        for ticket, entry in list(self.state.items()):
            if entry.get("kind") == "filled" and ticket not in live_tickets:
                self._record_close(ticket, entry)

    def _promote_fill(self, position) -> dict:
        pending = None
        pending_key = None
        for key, entry in self.state.items():
            if entry.get("kind") != "pending":
                continue
            signal = entry.get("signal") or {}
            if self.client.resolve_symbol(signal.get("symbol", "")) == position.symbol:
                pending, pending_key = entry, key
                break
        signal = (pending or {}).get("signal") or {}
        record = {
            "kind": "filled",
            "signal": signal,
            "lots": float(position.volume),
            "entry_price": float(position.price_open),
            "opened_at": int(position.time),
            "sl": float(position.sl),
            "tp": float(position.tp),
        }
        if pending_key is not None:
            self.state.pop(pending_key, None)
        self.state[str(position.ticket)] = record
        self.save()

        log.info(f"s147 FILLED #{position.ticket} {position.symbol} @ {position.price_open} "
                 f"| broker sl={position.sl} tp={position.tp}")
        s147_events.log_5m("entry_filled", {
            "symbol": signal.get("symbol", position.symbol),
            "position_ticket": int(position.ticket),
            "price": float(position.price_open),
            "lots": float(position.volume),
            "broker_sl": float(position.sl),
            "broker_tp": float(position.tp),
            "suggested_stop": signal.get("stop"),
            "suggested_target": signal.get("target"),
            **s147_events.stamp("filled_at", int(position.time)),
        }, key=f"fill:{position.ticket}")
        s147_events.log_trade("trade_opened", {
            **signal,
            "position_ticket": int(position.ticket),
            "entry_price": float(position.price_open),
            "lots": float(position.volume),
            "sl": float(position.sl),
            "tp": float(position.tp),
            **s147_events.stamp("opened_at", int(position.time)),
        }, key=f"opened:{position.ticket}")
        record_trade({"strategy": "s147", "event": "opened",
                      "ticket": int(position.ticket), **signal,
                      "entry_price": float(position.price_open),
                      "lots": float(position.volume)})

        if not position.sl or not position.tp:
            send_email(
                f"S147 POSITION OPEN {position.symbol} "
                f"{'BUY' if position.type == 0 else 'SELL'} @ {position.price_open}"
                f"  — SET SL/TP",
                f"Strategy 147 position is open\n"
                f"----------------------------------------\n"
                f"Symbol      : {position.symbol}\n"
                f"Lots        : {position.volume}\n"
                f"Entry       : {position.price_open}\n"
                f"Planned R:R : {signal.get('reward_risk', '?')}\n"
                f"4H POI      : {signal.get('poi_zone_id', '?')} "
                f"({signal.get('poi_type', '?')}) "
                f"{signal.get('poi_lower')} - {signal.get('poi_upper')}\n"
                f"POI arrival : {signal.get('poi_arrival_time_ist', '?')}\n"
                f"15m zone    : {signal.get('entry_zone_id', '?')} "
                f"{signal.get('entry_zone_lower')} - {signal.get('entry_zone_upper')}\n"
                f"\n*** No SL/TP is set on this position. ***\n"
                f"  Suggested stop loss   : {signal.get('stop', '?')}\n"
                f"  Suggested take profit : {signal.get('target', '?')}\n",
            )
        return record

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
            "position_ticket": int(ticket),
            "entry_price": entry.get("entry_price"),
            "lots": entry.get("lots"),
            "sl": entry.get("sl"),
            "tp": entry.get("tp"),
            "exit_reason": reason,
            "profit_account_ccy": profit,
            **s147_events.stamp("closed_at", closed_at),
        }
        s147_events.log_trade("trade_closed", payload, key=f"closed:{ticket}")
        record_trade({"strategy": "s147", "event": "closed", "ticket": int(ticket), **payload})
        log.info(f"s147 position #{ticket} closed | reason={reason} | pnl={profit}")
        send_email(
            f"S147 CLOSED {signal.get('symbol', '')} {reason} pnl={profit}",
            f"Strategy 147 position closed\n"
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

    # ------------------------------------------------------------ paper mode
    def _monitor_paper(self) -> None:
        for key, entry in list(self.state.items()):
            if entry.get("kind") != "paper":
                continue
            try:
                self._resolve_paper(key, entry)
            except Exception as exc:  # never let paper accounting kill the loop
                log.error(f"s147 paper resolve failed for {key}: {exc}")

    def _resolve_paper(self, key: str, entry: dict) -> None:
        signal = entry.get("signal") or {}
        symbol = signal.get("symbol")
        if not symbol:
            self.state.pop(key, None)
            return

        opened_at = int(entry.get("opened_at") or 0)
        long_side = signal.get("direction") == "long"
        stop = float(signal["stop"])
        target = float(signal["target"])
        entry_price = float(entry.get("entry_price") or signal["trigger"])

        df = self.client.fetch_closed(symbol, "5m", CONFIG.s147_fetch_5m)
        if df is None or len(df) == 0:
            return

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
            hit_stop = low <= stop if long_side else high >= stop
            hit_target = high >= target if long_side else low <= target
            # Both touched in one bar: 5m data cannot say which came first, so
            # assume the stop. Paper results must never flatter the strategy.
            if hit_stop:
                exit_reason, exit_price, exit_at = "paper_stop_loss", stop, bar_open + 300
                break
            if hit_target:
                exit_reason, exit_price, exit_at = "paper_take_profit", target, bar_open + 300
                break

        # No holding limit by design; only an explicit backstop can expire a trade.
        limit = CONFIG.s147_max_hold_bars_5m
        if exit_reason is None and limit > 0 and bars_held >= limit:
            last = df.iloc[-1]
            exit_reason = "paper_time_expiry"
            exit_price = float(last.close)
            exit_at = int(df.index[-1].timestamp()) + 300

        if exit_reason is None:
            return

        risk = abs(entry_price - stop)
        moved = (exit_price - entry_price) if long_side else (entry_price - exit_price)
        r_multiple = round(moved / risk, 3) if risk > 0 else None
        payload = {
            **signal,
            "mode": "paper",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "r_multiple": r_multiple,
            "bars_held_5m": bars_held,
            "profit_account_ccy": None,
            **s147_events.stamp("opened_at", opened_at),
            **s147_events.stamp("closed_at", exit_at),
        }
        s147_events.log_trade("paper_trade_closed", payload, key=f"paper_close:{key}")
        record_trade({"strategy": "s147", "event": "paper_closed", **payload})
        log.info(f"s147 PAPER {symbol} {exit_reason} | R={r_multiple} "
                 f"entry={entry_price:.5f} exit={exit_price:.5f} bars={bars_held}")
        self.state.pop(key, None)
        self.save()
