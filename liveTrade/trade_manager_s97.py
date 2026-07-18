"""
Manage open Strategy-97 positions exactly like the backtest exit logic:

  1) HARD ATR STOP  — fixed at entry (K_SL*ATR), placed on the broker as the SL.
                      Broker enforces it intrabar (backtest: stop checked first).
  2) MEAN-REVERT TP — dynamic target SMA +/- Z_EXIT*ATR, recomputed on every
                      CLOSED 4H bar from that bar's SMA/ATR and pushed to the
                      broker as the order TP (fills intrabar like the backtest).
  3) TIME STOP      — flat at market once the position has been open for
                      MAX_HOLD_BARS closed 4H bars.

State persists across restarts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config import CONFIG
from detection_s97 import mean_revert_target
from logging_setup import get_engine_logger, record_trade
from notifier import send_email

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
STATE_PATH = Path(__file__).resolve().parent / "passes" / "positions_state_s97.json"
MAGIC = 970097


class TradeManagerS97:
    def __init__(self, client):
        self.client = client
        self.state = self._load()

    # ---- persistence ----
    def _load(self) -> dict:
        try:
            return json.load(open(STATE_PATH))
        except Exception:
            return {}

    def _save(self):
        try:
            STATE_PATH.parent.mkdir(exist_ok=True)
            json.dump(self.state, open(STATE_PATH, "w"), indent=1, default=str)
        except Exception as e:
            log.error(f"state save failed: {e}")

    def register(self, ticket: int, info: dict):
        self.state[str(ticket)] = {
            **info,
            "held_bars": 0,
            "last_bar_ts": info.get("signal_time"),
            "opened_utc": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    # ---- monitoring (called every tick) ----
    def monitor(self):
        if mt5 is None or not self.client.ensure():
            return
        positions = {str(p.ticket): p for p in self.client.open_positions(MAGIC)}

        # 1) reconcile closed positions
        for tk in list(self.state.keys()):
            if tk not in positions:
                st = self.state.pop(tk)
                log.info(f"Position {tk} {st.get('symbol')} closed (TP/SL/time).")
                record_trade({"event": "closed", "ticket": tk, **st})
                self._save()

        # 2) adopt any live position without state (e.g. after restart)
        for tk, pos in positions.items():
            if tk not in self.state:
                entry = float(pos.price_open)
                sl = float(pos.sl) if pos.sl else entry
                is_long = pos.type == mt5.POSITION_TYPE_BUY
                self.state[tk] = {
                    "symbol": pos.symbol,
                    "direction": "long" if is_long else "short",
                    "entry": entry,
                    "sl": sl,
                    "risk": abs(entry - sl),
                    "lots": float(pos.volume),
                    "held_bars": 0,
                    "last_bar_ts": None,
                    "opened_utc": datetime.now(timezone.utc).isoformat(),
                }
                self._save()

    # ---- per-4H-bar management (called once each closed 4H bar) ----
    def on_bar(self, closed_bar_ts):
        """Update dynamic mean-revert TP and enforce the time stop.
        `closed_bar_ts` is the timestamp of the 4H bar that just closed."""
        if mt5 is None or not self.client.ensure():
            return
        positions = {str(p.ticket): p for p in self.client.open_positions(MAGIC)}
        bar_iso = pd.Timestamp(closed_bar_ts).isoformat()

        for tk, pos in positions.items():
            st = self.state.get(tk)
            if st is None:
                continue
            # count this closed bar once
            if st.get("last_bar_ts") != bar_iso:
                st["held_bars"] = int(st.get("held_bars", 0)) + 1
                st["last_bar_ts"] = bar_iso

            sym = st["symbol"]
            direction = st["direction"]

            # ---- 3) time stop ----
            if st["held_bars"] >= CONFIG.s97_max_hold_bars:
                self._flat_market(pos, st, "time_stop")
                continue

            # ---- 2) refresh mean-revert TP from the just-closed bar ----
            df4 = self.client.fetch_closed(sym, "4h", CONFIG.s97_fetch_bars)
            if df4 is None or len(df4) < CONFIG.s97_sma_n + 2:
                continue
            tgt = mean_revert_target(df4, direction)
            if tgt is None:
                continue

            # only push a valid TP (must be on the profitable side of price)
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                continue
            price = tick.bid if direction == "long" else tick.ask
            valid = (tgt > price) if direction == "long" else (tgt < price)
            if not valid:
                # target already passed intra-bar would have filled; if not,
                # keep previous TP rather than pushing an invalid level.
                st.setdefault("tp", float(pos.tp) if pos.tp else 0.0)
                self._save()
                continue

            if abs((pos.tp or 0.0) - tgt) < (tick.ask - tick.bid + 1e-9):
                st["tp"] = tgt
                self._save()
                continue

            if CONFIG.dry_run:
                log.info(f"[DRY RUN] {sym} mean-revert TP {pos.tp:.5f} -> {tgt:.5f} (held {st['held_bars']})")
                st["tp"] = tgt
                self._save()
                continue

            res = self.client.modify_sl_tp(pos, sl=float(pos.sl), tp=tgt)
            ok = res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE
            if ok:
                log.info(f"{sym} TP {pos.tp:.5f} -> {tgt:.5f} (held {st['held_bars']})")
                st["tp"] = tgt
                self._save()
                record_trade({"event": "tp_update", "ticket": tk, "symbol": sym,
                              "tp": tgt, "held_bars": st["held_bars"]})
            else:
                log.warning(f"{sym} TP modify failed: {res}")

    def _flat_market(self, pos, st, reason: str):
        sym = st["symbol"]
        if CONFIG.dry_run:
            log.info(f"[DRY RUN] {sym} {reason}: would close {pos.volume} lots at market (held {st['held_bars']})")
            record_trade({"event": f"dry_run_{reason}", "ticket": str(pos.ticket), **st})
            return
        res = self.client.close_position(pos)
        ok = res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE
        log.info(f"{sym} {reason}: close {pos.volume} lots -> {'OK' if ok else res}")
        if ok:
            record_trade({"event": reason, "ticket": str(pos.ticket), **st})
            send_email(f"Strategy97 {sym} {reason}",
                       f"{sym} {st['direction'].upper()} closed by {reason} after "
                       f"{st['held_bars']} bars.")
