"""
Manage open Strategy-95 positions exactly like the backtest:
  - at +PARTIAL_R: close 50% and move the stop to breakeven ("trailing" = BE lock)
  - the final +FINAL_R take-profit is set on the order, so MT5 closes the runner
State is persisted so a restart of the engine resumes management correctly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import CONFIG
from detection import PARTIAL_R
from logging_setup import get_engine_logger, record_trade
from notifier import send_email

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
STATE_PATH = Path(__file__).resolve().parent / "passes" / "positions_state.json"
MAGIC = 950095


class TradeManager:
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
        self.state[str(ticket)] = {**info, "partialed": False, "opened_utc": datetime.now(timezone.utc).isoformat()}
        self._save()

    # ---- monitoring ----
    def monitor(self):
        if mt5 is None or not self.client.ensure():
            return
        positions = {str(p.ticket): p for p in self.client.open_positions(MAGIC)}

        # 1) handle closed positions (in state but no longer open)
        for tk in list(self.state.keys()):
            if tk not in positions:
                st = self.state.pop(tk)
                log.info(f"Position {tk} {st.get('symbol')} closed (hit TP/SL/BE).")
                record_trade({"event": "closed", "ticket": tk, **st})
                self._save()

        # 2) manage live positions
        for tk, pos in positions.items():
            st = self.state.get(tk)
            if st is None:
                # reconstruct after a restart
                entry = float(pos.price_open)
                sl = float(pos.sl) if pos.sl else entry
                risk = abs(entry - sl)
                already = (risk == 0) or (abs(sl - entry) < (risk * 0.05 if risk else 1e-9))
                st = {"symbol": pos.symbol, "direction": "long" if pos.type == mt5.POSITION_TYPE_BUY else "short",
                      "entry": entry, "sl": sl, "risk": risk, "lots": float(pos.volume), "partialed": already}
                self.state[tk] = st
                self._save()

            if st.get("partialed"):
                continue
            risk = float(st.get("risk") or 0)
            if risk <= 0:
                continue
            entry = float(st["entry"])
            is_long = pos.type == mt5.POSITION_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick is None:
                continue
            price = tick.bid if is_long else tick.ask
            trigger = entry + PARTIAL_R * risk if is_long else entry - PARTIAL_R * risk
            reached = price >= trigger if is_long else price <= trigger
            if not reached:
                continue

            self._take_partial_and_breakeven(pos, st)

    def _take_partial_and_breakeven(self, pos, st):
        si = self.client.symbol_info(st["symbol"]) or mt5.symbol_info(pos.symbol)
        step = (si.volume_step if si else 0.01) or 0.01
        vmin = (si.volume_min if si else 0.01) or 0.01
        half = round((pos.volume * 0.5) / step) * step
        entry = float(st["entry"])

        if CONFIG.dry_run:
            log.info(f"[DRY RUN] {pos.symbol} reached +{PARTIAL_R}R — would close {half} lots & move SL to BE {entry:.5f}")
            st["partialed"] = True
            self._save()
            return

        # close half (only if it leaves a valid remainder)
        if half >= vmin and (pos.volume - half) >= vmin:
            res = self.client.close_partial(pos, half)
            ok = res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE
            log.info(f"{pos.symbol} partial close {half} lots @ +{PARTIAL_R}R -> {'OK' if ok else res}")
        else:
            log.info(f"{pos.symbol} volume {pos.volume} too small to split; moving SL to BE only.")

        # move stop to breakeven
        res2 = self.client.modify_sl_tp(pos, sl=entry)
        ok2 = res2 is not None and getattr(res2, "retcode", None) == mt5.TRADE_RETCODE_DONE
        log.info(f"{pos.symbol} SL -> breakeven {entry:.5f} -> {'OK' if ok2 else res2}")
        st["partialed"] = True
        self._save()
        record_trade({"event": "partial_be", "ticket": str(pos.ticket), "symbol": st["symbol"],
                      "partial_lots": half, "breakeven": entry})
        send_email(f"Strategy95 {st['symbol']} +{PARTIAL_R}R partial & breakeven",
                   f"{st['symbol']} {st['direction'].upper()} reached +{PARTIAL_R}R.\n"
                   f"Closed ~{half} lots, stop moved to breakeven {entry:.5f}. Runner targets +{CONFIG.final_r}R.")
