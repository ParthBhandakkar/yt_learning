"""
MetaTrader 5 wrapper: connect, fetch CLOSED candles, resolve Exness symbols,
size positions by margin, place/modify/close orders.

IMPORTANT: every fetch drops the still-forming candle, so detection only ever
sees CLOSED candles (requirement #4).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

try:
    import MetaTrader5 as mt5
except Exception:  # not installed off-Windows; engine will error clearly at startup
    mt5 = None

from config import CONFIG
from logging_setup import get_engine_logger

log = get_engine_logger()

# minutes per timeframe
TF_MINUTES = {"4h": 240, "1h": 60, "15m": 15, "5m": 5}


def _tf_const(tf: str):
    return {
        "4h": mt5.TIMEFRAME_H4,
        "1h": mt5.TIMEFRAME_H1,
        "15m": mt5.TIMEFRAME_M15,
        "5m": mt5.TIMEFRAME_M5,
    }[tf]


class MT5Client:
    def __init__(self):
        self.connected = False
        self._symbol_cache: dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------ connect
    def connect(self) -> bool:
        if mt5 is None:
            raise RuntimeError("MetaTrader5 package not available. Install it on Windows: pip install MetaTrader5")
        kwargs = {}
        if CONFIG.mt5_path:
            kwargs["path"] = CONFIG.mt5_path
        if not mt5.initialize(**kwargs):
            log.error(f"MT5 initialize failed: {mt5.last_error()}")
            return False
        if CONFIG.mt5_login and CONFIG.mt5_password:
            if not mt5.login(login=int(CONFIG.mt5_login), password=CONFIG.mt5_password, server=CONFIG.mt5_server):
                log.error(f"MT5 login failed: {mt5.last_error()}")
                return False
        info = mt5.account_info()
        if info is None:
            log.error(f"MT5 account unavailable: {mt5.last_error()}")
            return False
        mode = "DEMO" if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "LIVE"
        log.info(f"MT5 connected | {info.server} | {mode} | balance={info.balance} {info.currency} | leverage 1:{info.leverage}")
        self.connected = True
        return True

    def ensure(self) -> bool:
        try:
            if self.connected and mt5.account_info() is not None:
                return True
        except Exception:
            pass
        self.connected = False
        return self.connect()

    def shutdown(self):
        if mt5 is not None:
            mt5.shutdown()

    # ------------------------------------------------------------------ symbols
    def resolve_symbol(self, symbol: str) -> Optional[str]:
        key = symbol.upper()
        if key in self._symbol_cache:
            return self._symbol_cache[key]
        candidates = []
        if CONFIG.symbol_suffix:
            candidates.append(key + CONFIG.symbol_suffix)
        candidates += [key, key + "m", key + ".m", key + "_i", key + "z"]
        for c in dict.fromkeys(candidates):
            if mt5.symbol_select(c, True):
                self._symbol_cache[key] = c
                return c
        # fuzzy search
        for it in (mt5.symbols_get(f"*{key}*") or []):
            name = getattr(it, "name", "")
            if name and mt5.symbol_select(name, True):
                self._symbol_cache[key] = name
                return name
        log.warning(f"Symbol not found on broker: {symbol}")
        self._symbol_cache[key] = None
        return None

    # ------------------------------------------------------------------ data
    def fetch_closed(self, symbol: str, tf: str, n_bars: int = 400) -> Optional[pd.DataFrame]:
        """Return a DataFrame of CLOSED candles (forming bar dropped)."""
        if not self.ensure():
            return None
        broker_sym = self.resolve_symbol(symbol)
        if broker_sym is None:
            return None
        rates = mt5.copy_rates_from_pos(broker_sym, _tf_const(tf), 0, n_bars + 2)
        if rates is None or len(rates) == 0:
            log.warning(f"No rates for {symbol} {tf}: {mt5.last_error()}")
            return None
        df = pd.DataFrame(rates)
        df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("datetime", inplace=True)
        vol = "tick_volume" if "tick_volume" in df.columns else ("real_volume" if "real_volume" in df.columns else None)
        df["volume"] = df[vol] if vol else 0
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        # drop the forming candle: keep only bars whose close time <= now
        now = datetime.now(timezone.utc)
        period = timedelta(minutes=TF_MINUTES[tf])
        df = df[df.index + period <= now]
        return df if len(df) else None

    def current_price(self, symbol: str):
        broker_sym = self.resolve_symbol(symbol)
        if broker_sym is None:
            return None
        t = mt5.symbol_info_tick(broker_sym)
        return t

    # ------------------------------------------------------------------ sizing
    def lots_for_margin(self, symbol: str, direction: str, price: float, margin_inr: float) -> float:
        """Volume whose required margin ~= margin_inr, using MT5's own margin model
        (which already reflects the account's 1:LEVERAGE)."""
        broker_sym = self.resolve_symbol(symbol)
        si = mt5.symbol_info(broker_sym)
        if si is None:
            return 0.0
        order_type = mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL
        m1 = mt5.order_calc_margin(order_type, broker_sym, 1.0, price)
        if not m1 or m1 <= 0:
            return 0.0
        raw = margin_inr / m1  # lots to reach target margin
        step = si.volume_step or 0.01
        lots = max(si.volume_min, round(raw / step) * step)
        lots = min(lots, si.volume_max, CONFIG.max_lot)
        return round(lots, 2)

    # ------------------------------------------------------------------ orders
    def open_trade(self, symbol: str, direction: str, lots: float, sl: float, tp: float, comment: str):
        broker_sym = self.resolve_symbol(symbol)
        tick = mt5.symbol_info_tick(broker_sym)
        price = tick.ask if direction == "long" else tick.bid
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_sym,
            "volume": float(lots),
            "type": mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": float(sl),
            "tp": float(tp),
            "deviation": 30,
            "magic": 950095,
            "comment": comment[:30],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(broker_sym),
        }
        res = mt5.order_send(req)
        return res

    def modify_sl_tp(self, position, sl: float, tp: Optional[float] = None):
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": position.ticket,
            "sl": float(sl),
            "tp": float(tp if tp is not None else position.tp),
        }
        return mt5.order_send(req)

    def close_partial(self, position, lots: float):
        broker_sym = position.symbol
        tick = mt5.symbol_info_tick(broker_sym)
        is_long = position.type == mt5.POSITION_TYPE_BUY
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_sym,
            "volume": float(lots),
            "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": tick.bid if is_long else tick.ask,
            "deviation": 30,
            "magic": 950095,
            "comment": "s95 partial",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(broker_sym),
        }
        return mt5.order_send(req)

    def open_positions(self, magic: int = 950095):
        pos = mt5.positions_get()
        return [p for p in (pos or []) if p.magic == magic]

    def position_for_symbol(self, symbol: str, magic: int = 950095):
        broker_sym = self.resolve_symbol(symbol)
        for p in self.open_positions(magic):
            if p.symbol == broker_sym:
                return p
        return None

    def account_info(self):
        return mt5.account_info()

    def symbol_info(self, symbol: str):
        return mt5.symbol_info(self.resolve_symbol(symbol))

    def _filling(self, broker_sym: str):
        si = mt5.symbol_info(broker_sym)
        # prefer IOC/FOK depending on what the symbol allows
        mode = getattr(si, "filling_mode", 0)
        if mode and (mode & 2):
            return mt5.ORDER_FILLING_IOC
        if mode and (mode & 1):
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN
