"""
Manage open Strategy-98 positions with ATR chandelier trailing (matches backtest exit).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import CONFIG
from detection_s98 import df_to_candles
from logging_setup import get_engine_logger, record_trade
from notifier import send_email
from strategy_98_xau_trend_liquidity_trail import _atr  # noqa: E402

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
STATE_PATH = Path(__file__).resolve().parent / "passes" / "positions_state_s98.json"
MAGIC = 980098


class TradeManagerS98:
    def __init__(self, client):
        self.client = client
        self.state = self._load()

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
            "trail": float(info["sl"]),
            "extreme": float(info.get("extreme", info["entry"])),
            "opened_utc": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def monitor(self):
        if mt5 is None or not self.client.ensure():
            return
        positions = {str(p.ticket): p for p in self.client.open_positions(MAGIC)}

        for tk in list(self.state.keys()):
            if tk not in positions:
                st = self.state.pop(tk)
                log.info(f"Position {tk} {st.get('symbol')} closed (trailed SL or manual).")
                record_trade({"event": "closed", "ticket": tk, **st})
                self._save()

        for tk, pos in positions.items():
            st = self.state.get(tk)
            if st is None:
                entry = float(pos.price_open)
                sl = float(pos.sl) if pos.sl else entry
                is_long = pos.type == mt5.POSITION_TYPE_BUY
                st = {
                    "symbol": pos.symbol,
                    "direction": "long" if is_long else "short",
                    "entry": entry,
                    "sl": sl,
                    "risk": abs(entry - sl),
                    "lots": float(pos.volume),
                    "trail": sl,
                    "extreme": entry,
                    "atr_mult_trail": CONFIG.s98_atr_mult_trail,
                }
                self.state[tk] = st
                self._save()

            self._update_trail(pos, st)

    def _update_trail(self, pos, st):
        sym = st.get("symbol", "")
        df1 = self.client.fetch_closed(sym, "1h", 80)
        if df1 is None or len(df1) < CONFIG.s98_atr_len + 2:
            return

        candles = df_to_candles(df1)
        h = np.array([c.high for c in candles], dtype=np.float64)
        l = np.array([c.low for c in candles], dtype=np.float64)
        c = np.array([c.close for c in candles], dtype=np.float64)
        atr_arr = _atr(h, l, c, CONFIG.s98_atr_len)
        atr_now = float(atr_arr[-1])
        if atr_now <= 0:
            return

        is_long = st["direction"] == "long"
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return
        price = tick.bid if is_long else tick.ask
        mult = float(st.get("atr_mult_trail", CONFIG.s98_atr_mult_trail))
        trail = float(st.get("trail", st["sl"]))
        extreme = float(st.get("extreme", st["entry"]))

        if is_long:
            extreme = max(extreme, price)
            new_trail = max(trail, extreme - mult * atr_now)
            if new_trail <= trail + 1e-9:
                st["extreme"] = extreme
                return
        else:
            extreme = min(extreme, price)
            new_trail = min(trail, extreme + mult * atr_now)
            if new_trail >= trail - 1e-9:
                st["extreme"] = extreme
                return

        if CONFIG.dry_run:
            log.info(f"[DRY RUN] {pos.symbol} trail SL {trail:.5f} -> {new_trail:.5f}")
            st["trail"] = new_trail
            st["extreme"] = extreme
            self._save()
            return

        res = self.client.modify_sl_tp(pos, sl=new_trail, tp=0.0)
        ok = res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE
        if ok:
            log.info(f"{pos.symbol} trail SL {trail:.5f} -> {new_trail:.5f}")
            st["trail"] = new_trail
            st["sl"] = new_trail
            st["extreme"] = extreme
            self._save()
            record_trade({
                "event": "trail_update",
                "ticket": str(pos.ticket),
                "symbol": sym,
                "trail": new_trail,
                "extreme": extreme,
            })
        else:
            log.warning(f"{pos.symbol} trail modify failed: {res}")
