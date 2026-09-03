"""
MetaTrader 5 wrapper: connect, fetch CLOSED candles, resolve Exness symbols,
size positions by margin, place/modify/close orders.

IMPORTANT: every fetch drops the still-forming candle, so detection only ever
sees CLOSED candles (requirement #4).
"""
from __future__ import annotations

import math
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
    def __init__(self, magic: int = 950095):
        self.connected = False
        self._symbol_cache: dict[str, Optional[str]] = {}
        self.magic = magic
        self.last_order_context: dict = {}

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

    def _margin_with_retry(
        self, symbol: str, broker_sym: str, direction: str, volume: float,
        requested_price: float,
    ) -> tuple[float, object, dict]:
        """Calculate margin while recovering from transient MT5 zero results.

        The first attempt preserves the caller's requested price. Retries
        re-select the symbol and use the current executable side of the quote:
        ask for buys and bid for sells. A zero result is never treated as a
        valid margin estimate.
        """
        attempts = max(1, int(CONFIG.margin_calc_retries))
        delay = max(0.0, float(CONFIG.margin_calc_retry_delay_sec))
        order_type = mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL
        margins: list[float] = []
        prices: list[float] = []
        ticks: list[dict] = []
        selections: list[bool] = []
        si = None

        for attempt in range(1, attempts + 1):
            selected = True
            if attempt > 1:
                selected = bool(mt5.symbol_select(broker_sym, True))
            selections.append(selected)
            si = mt5.symbol_info(broker_sym)
            tick = mt5.symbol_info_tick(broker_sym)
            bid = getattr(tick, "bid", None)
            ask = getattr(tick, "ask", None)
            ticks.append({"bid": bid, "ask": ask})

            calc_price = float(requested_price)
            price_source = "requested"
            if attempt > 1:
                live_price = ask if direction == "long" else bid
                if live_price is not None and float(live_price) > 0:
                    calc_price = float(live_price)
                    price_source = "live_tick"
            prices.append(calc_price)

            margin = 0.0
            if si is not None and calc_price > 0:
                value = mt5.order_calc_margin(
                    order_type, broker_sym, float(volume), calc_price)
                try:
                    margin = float(value or 0.0)
                except (TypeError, ValueError):
                    margin = 0.0
            margins.append(margin)
            if margin > 0:
                context = {
                    "symbol": symbol, "broker_symbol": broker_sym,
                    "direction": direction, "volume": float(volume),
                    "attempts": attempt, "prices": prices,
                    "price_sources": ["requested"] + [
                        "live_tick" if i > 0 and ticks[i]["ask" if direction == "long" else "bid"]
                        else "requested" for i in range(1, len(ticks))],
                    "margins": margins, "ticks": ticks,
                    "symbol_selected": selections,
                    "last_error": repr(mt5.last_error()),
                    "result": "positive",
                }
                self.last_margin_context = context
                return margin, si, context

            if attempt < attempts and delay > 0:
                import time
                time.sleep(delay)

        context = {
            "symbol": symbol, "broker_symbol": broker_sym,
            "direction": direction, "volume": float(volume),
            "attempts": attempts, "prices": prices,
            "margins": margins, "ticks": ticks,
            "symbol_selected": selections,
            "symbol_info_available": si is not None,
            "last_error": repr(mt5.last_error()),
            "result": "unavailable",
        }
        self.last_margin_context = context
        return 0.0, si, context

    def lots_for_margin(self, symbol: str, direction: str, price: float, margin_inr: float) -> float:
        """Volume whose required margin ~= margin_inr, using MT5's own margin model.

        MT5 may transiently return zero while symbol or conversion data is
        refreshing, so the calculation is retried before rejecting the signal.
        """
        broker_sym = self.resolve_symbol(symbol)
        if broker_sym is None:
            self.last_margin_context = {
                "symbol": symbol, "broker_symbol": None, "direction": direction,
                "result": "symbol_unresolved", "last_error": repr(mt5.last_error()),
            }
            log.warning(f"{symbol}: margin sizing unavailable — broker symbol could not be resolved; "
                        f"mt5={mt5.last_error()}")
            return 0.0

        m1, si, diagnostic = self._margin_with_retry(
            symbol, broker_sym, direction, 1.0, price)
        if m1 <= 0 or si is None:
            tick = mt5.symbol_info_tick(broker_sym)
            log.warning(
                f"{symbol}: margin sizing unavailable after {diagnostic.get('attempts', 1)} attempt(s) — "
                f"order_calc_margin values={diagnostic.get('margins')} "
                f"side={direction} prices={diagnostic.get('prices')} "
                f"broker_symbol={broker_sym} "
                f"tick=({getattr(tick, 'bid', None)!r},{getattr(tick, 'ask', None)!r}) "
                f"trade_mode={getattr(si, 'trade_mode', None)!r} "
                f"volume=({getattr(si, 'volume_min', None)!r},"
                f"{getattr(si, 'volume_step', None)!r},"
                f"{getattr(si, 'volume_max', None)!r}) "
                f"mt5={mt5.last_error()}"
            )
            return 0.0
        # Round DOWN: rounding to nearest can exceed both the requested margin
        # and the account's available free margin at the moment of submission.
        raw = margin_inr / m1  # lots to reach target margin
        step = float(si.volume_step or 0.01)
        vmin = float(si.volume_min or step)
        vmax = min(float(si.volume_max or CONFIG.max_lot), float(CONFIG.max_lot))
        lots = math.floor(raw / step + 1e-9) * step
        if lots < vmin:
            return 0.0
        lots = min(lots, vmax)
        return round(lots, 8)

    def fit_lots_to_free_margin(
        self, symbol: str, direction: str, price: float, lots: float,
        fraction: float = 0.80,
    ) -> tuple[float, dict]:
        """Reduce volume until MT5 margin fits a free-margin safety budget.

        This is an execution-availability check, not the s146 account-risk cap:
        MAX_RISK_INR/MAX_RISK_PCT remain deliberately unused. Volume is rounded
        down so the minimum lot is never forced above the available budget.
        """
        broker_sym = self.resolve_symbol(symbol)
        info = mt5.account_info() if mt5 is not None else None
        si = mt5.symbol_info(broker_sym) if broker_sym and mt5 is not None else None
        free = float(getattr(info, "margin_free", 0.0) or 0.0)
        budget = max(0.0, free * max(0.0, min(float(fraction), 1.0)))
        result = {"broker_symbol": broker_sym, "free_margin": free,
                  "budget": budget, "requested_lots": float(lots),
                  "sized_lots": 0.0, "required_margin": None}
        if not broker_sym or si is None or budget <= 0 or lots <= 0:
            return 0.0, result
        step = float(getattr(si, "volume_step", 0.01) or 0.01)
        vmin = float(getattr(si, "volume_min", step) or step)
        vmax = min(float(getattr(si, "volume_max", lots) or lots), float(CONFIG.max_lot))
        candidate = min(float(lots), vmax)

        margin, refreshed_si, diagnostic = self._margin_with_retry(
            symbol, broker_sym, direction, candidate, price)
        if refreshed_si is not None:
            si = refreshed_si
        result["margin_diagnostic"] = diagnostic
        if margin <= 0:
            # A zero margin is unavailable data, not evidence that a smaller
            # volume is affordable. Do not spin through the volume ladder.
            result["required_margin"] = 0.0
            return 0.0, result
        # Reduce by the broker's volume step until the actual MT5 margin fits.
        candidate = min(candidate, candidate * budget / margin)
        candidate = math.floor(candidate / step + 1e-9) * step
        if candidate < vmin:
            result["required_margin"] = float(margin or 0.0)
            return 0.0, result

        required, _, diagnostic = self._margin_with_retry(
            symbol, broker_sym, direction, candidate, price)
        result["margin_diagnostic"] = diagnostic
        while candidate >= vmin and (not required or required > budget + 1e-8):
            candidate = round(candidate - step, 8)
            if candidate >= vmin:
                required, _, diagnostic = self._margin_with_retry(
                    symbol, broker_sym, direction, candidate, price)
                result["margin_diagnostic"] = diagnostic
            else:
                required = 0.0
        if candidate < vmin:
            return 0.0, result
        result.update({"sized_lots": round(candidate, 8),
                       "required_margin": float(required or 0.0)})
        return round(candidate, 8), result

    def lots_fixed(self, symbol: str) -> float:
        """Return configured fixed lot, clamped to broker symbol limits."""
        broker_sym = self.resolve_symbol(symbol)
        si = mt5.symbol_info(broker_sym)
        if si is None:
            return 0.0
        lots = CONFIG.fixed_lot
        step = si.volume_step or 0.01
        vmin = si.volume_min or 0.01
        vmax = min(si.volume_max, CONFIG.max_lot)
        lots = max(vmin, round(lots / step) * step)
        lots = min(lots, vmax)
        return round(lots, 2)

    # ------------------------------------------------------------------ orders
    def safe_levels(
        self, broker_sym: str, direction: str, price: float, sl: float, tp: float
    ) -> tuple[float, float]:
        """Return broker-valid protective levels while retaining intent upstream.

        The larger of stops-level and freeze-level is used. The caller must keep
        the structural/requested levels separately because the broker-effective
        level may be clamped farther from the live price.
        """
        si = mt5.symbol_info(broker_sym)
        sl = float(sl or 0.0)
        tp = float(tp or 0.0)
        if si is None:
            return sl, tp
        digits = int(getattr(si, "digits", 5) or 5)
        point = float(getattr(si, "point", 0.0) or 0.0)
        stops = float(getattr(si, "trade_stops_level", 0) or 0) * point
        freeze = float(getattr(si, "trade_freeze_level", 0) or 0) * point
        min_dist = max(stops, freeze)
        if min_dist > 0 and point > 0:
            # Avoid equality/rounding rejection at the broker boundary.
            min_dist += point
        if min_dist > 0:
            if sl > 0:
                sl = min(sl, price - min_dist) if long_side else max(sl, price + min_dist)
            if tp > 0:
                tp = max(tp, price + min_dist) if long_side else min(tp, price - min_dist)
        return (round(sl, digits) if sl > 0 else 0.0,
                round(tp, digits) if tp > 0 else 0.0)

    def _request_context(self, broker_sym: str, direction: str, price: float,
                         requested_sl: float, requested_tp: float,
                         effective_sl: float, effective_tp: float) -> dict:
        si = mt5.symbol_info(broker_sym)
        return {
            "broker_symbol": broker_sym, "direction": direction,
            "reference_price": float(price),
            "requested_sl": float(requested_sl or 0.0),
            "requested_tp": float(requested_tp or 0.0),
            "effective_sl": float(effective_sl or 0.0),
            "effective_tp": float(effective_tp or 0.0),
            "digits": int(getattr(si, "digits", 0) or 0) if si else None,
            "point": float(getattr(si, "point", 0.0) or 0.0) if si else None,
            "stops_level": int(getattr(si, "trade_stops_level", 0) or 0) if si else None,
            "freeze_level": int(getattr(si, "trade_freeze_level", 0) or 0) if si else None,
        }

    def _check_request(self, request: dict) -> tuple[bool, object]:
        """Run MT5 order_check; a successful check uses retcode 0 or DONE."""
        if mt5 is None or not hasattr(mt5, "order_check"):
            return True, None
        try:
            result = mt5.order_check(request)
        except Exception as exc:
            log.warning(f"MT5 order_check failed: {exc}")
            return True, None
        code = getattr(result, "retcode", None)
        accepted = code in (None, 0, getattr(mt5, "TRADE_RETCODE_DONE", 10009),
                            getattr(mt5, "TRADE_RETCODE_PLACED", 10008))
        return bool(accepted), result

    def open_trade(self, symbol: str, direction: str, lots: float, sl: float, tp: float,
                   comment: str, validate: bool = False):
        broker_sym = self.resolve_symbol(symbol)
        tick = mt5.symbol_info_tick(broker_sym) if broker_sym else None
        if tick is None:
            self.last_order_context = {"error": "no_tick", "symbol": symbol}
            return None
        price = tick.ask if direction == "long" else tick.bid
        effective_sl, effective_tp = self.safe_levels(broker_sym, direction, price, sl, tp)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_sym,
            "volume": float(lots),
            "type": mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": float(effective_sl),
            "tp": float(effective_tp),
            "deviation": 30,
            "magic": self.magic,
            "comment": comment[:30],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(broker_sym),
        }
        self.last_order_context = self._request_context(
            broker_sym, direction, price, sl, tp, effective_sl, effective_tp)
        self.last_order_context["request"] = dict(req)
        if validate:
            ok, check = self._check_request(req)
            self.last_order_context["order_check"] = {
                "ok": ok, "retcode": str(getattr(check, "retcode", "none")),
                "comment": str(getattr(check, "comment", "")),
            }
            if not ok:
                return None
        res = mt5.order_send(req)
        self.last_order_context["send_retcode"] = str(getattr(res, "retcode", "none"))
        self.last_order_context["send_comment"] = str(getattr(res, "comment", ""))
        return res

    def modify_sl_tp(self, position, sl: float, tp: Optional[float] = None,
                     validate: bool = False):
        want_sl = float(sl)
        want_tp = float(tp if tp is not None else position.tp)
        direction = "long" if position.type == mt5.POSITION_TYPE_BUY else "short"
        tick = mt5.symbol_info_tick(position.symbol)
        if tick is not None:
            price = tick.bid if direction == "long" else tick.ask
        else:
            price = float(position.price_open)
        effective_sl, effective_tp = self.safe_levels(
            position.symbol, direction, price, want_sl, want_tp
        )
        self.last_order_context = self._request_context(
            position.symbol, direction, price, want_sl, want_tp,
            effective_sl, effective_tp)
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": position.ticket,
            "sl": effective_sl,
            "tp": effective_tp,
        }
        self.last_order_context["request"] = dict(req)
        if validate:
            ok, check = self._check_request(req)
            self.last_order_context["order_check"] = {
                "ok": ok, "retcode": str(getattr(check, "retcode", "none")),
                "comment": str(getattr(check, "comment", "")),
            }
            if not ok:
                return None
        result = mt5.order_send(req)
        self.last_order_context["send_retcode"] = str(getattr(result, "retcode", "none"))
        self.last_order_context["send_comment"] = str(getattr(result, "comment", ""))
        return result

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
            "magic": self.magic,
            "comment": "s95 partial",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(broker_sym),
        }
        return mt5.order_send(req)

    def close_partial(self, position, volume: float, comment: str = "partial"):
        """Close part of a position at market, leaving the remainder open.

        `volume` must already be a valid step multiple for the symbol and must
        leave at least volume_min behind, else the broker rejects the deal.
        """
        broker_sym = position.symbol
        tick = mt5.symbol_info_tick(broker_sym)
        is_long = position.type == mt5.POSITION_TYPE_BUY
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_sym,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": tick.bid if is_long else tick.ask,
            "deviation": 30,
            "magic": self.magic,
            "comment": comment[:30],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(broker_sym),
        }
        return mt5.order_send(req)

    def split_volume(self, broker_sym: str, volume: float, fraction: float) -> float:
        """Volume to close for `fraction` of `volume`, or 0 if it cannot be split.

        Rounded DOWN to the symbol's volume step so the remainder never falls
        below the minimum. Returns 0 when either side would be under volume_min,
        which is the normal case on a 0.01 lot position.
        """
        si = mt5.symbol_info(broker_sym)
        if si is None:
            return 0.0
        step = float(getattr(si, "volume_step", 0.01) or 0.01)
        vmin = float(getattr(si, "volume_min", 0.01) or 0.01)
        digits = 8
        lots = math.floor((float(volume) * float(fraction)) / step) * step
        lots = round(lots, digits)
        if lots < vmin or round(float(volume) - lots, digits) < vmin:
            return 0.0
        return round(lots, 2)

    def close_position(self, position, comment: str = "close"):
        """Close the full remaining volume of a position at market."""
        broker_sym = position.symbol
        tick = mt5.symbol_info_tick(broker_sym)
        is_long = position.type == mt5.POSITION_TYPE_BUY
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_sym,
            "volume": float(position.volume),
            "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": tick.bid if is_long else tick.ask,
            "deviation": 30,
            "magic": self.magic,
            "comment": comment[:30],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(broker_sym),
        }
        return mt5.order_send(req)

    def place_pending_stop(
        self,
        symbol: str,
        direction: str,
        lots: float,
        trigger: float,
        sl: float,
        tp: float,
        comment: str,
        expiry_ts: Optional[int] = None,
        validate: bool = False,
    ):
        """Place a pending stop with broker-valid SL/TP and optional preflight."""
        broker_sym = self.resolve_symbol(symbol)
        effective_sl, effective_tp = self.safe_levels(
            broker_sym, direction, float(trigger), sl, tp)
        req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": broker_sym,
            "volume": float(lots),
            "type": mt5.ORDER_TYPE_BUY_STOP if direction == "long" else mt5.ORDER_TYPE_SELL_STOP,
            "price": float(trigger),
            "sl": effective_sl,
            "tp": effective_tp,
            "magic": self.magic,
            "comment": comment[:30],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(broker_sym),
        }
        self.last_order_context = self._request_context(
            broker_sym, direction, float(trigger), sl, tp, effective_sl, effective_tp)
        self.last_order_context["request"] = dict(req)
        if validate:
            ok, check = self._check_request(req)
            self.last_order_context["order_check"] = {
                "ok": ok, "retcode": str(getattr(check, "retcode", "none")),
                "comment": str(getattr(check, "comment", "")),
            }
            if not ok:
                return None
        if expiry_ts:
            timed = dict(req)
            timed["type_time"] = mt5.ORDER_TIME_SPECIFIED
            timed["expiration"] = int(expiry_ts)
            res = mt5.order_send(timed)
            if res is not None and res.retcode in {
                    mt5.TRADE_RETCODE_DONE,
                    getattr(mt5, "TRADE_RETCODE_PLACED", mt5.TRADE_RETCODE_DONE),
            }:
                self.last_order_context["send_retcode"] = str(getattr(res, "retcode", "none"))
                return res
            log.warning(
                f"{symbol}: timed pending order rejected "
                f"({getattr(res, 'retcode', 'no result')}); retrying as GTC"
            )
        result = mt5.order_send(req)
        self.last_order_context["send_retcode"] = str(getattr(result, "retcode", "none"))
        self.last_order_context["send_comment"] = str(getattr(result, "comment", ""))
        return result

    def pending_orders(self, magic: int | None = None):
        m = self.magic if magic is None else magic
        orders = mt5.orders_get()
        return [o for o in (orders or []) if o.magic == m]

    def cancel_order(self, order):
        return mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})

    def spread_now(self, symbol: str) -> float:
        """Live ask-bid spread in price terms, or 0.0 when unavailable."""
        broker_sym = self.resolve_symbol(symbol)
        if broker_sym is None:
            return 0.0
        tick = mt5.symbol_info_tick(broker_sym)
        if tick is None:
            return 0.0
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        return max(0.0, ask - bid) if bid > 0 and ask > 0 else 0.0

    def stops_distance(self, symbol: str) -> float:
        """Broker minimum distance between price and a pending/stop level."""
        si = mt5.symbol_info(self.resolve_symbol(symbol))
        if si is None:
            return 0.0
        return float(getattr(si, "trade_stops_level", 0) or 0) * float(si.point or 0)

    def is_demo(self) -> bool:
        info = mt5.account_info()
        if info is None:
            return False
        return info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO

    def open_positions(self, magic: int | None = None):
        m = self.magic if magic is None else magic
        pos = mt5.positions_get()
        return [p for p in (pos or []) if p.magic == m]

    def position_for_symbol(self, symbol: str, magic: int | None = None):
        m = self.magic if magic is None else magic
        broker_sym = self.resolve_symbol(symbol)
        for p in self.open_positions(m):
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


def lots_for_trade(client: MT5Client, symbol: str, direction: str, price: float) -> float:
    """Fixed lot when FIXED_LOT > 0, else margin-based sizing."""
    if CONFIG.fixed_lot > 0:
        return client.lots_fixed(symbol)
    return client.lots_for_margin(symbol, direction, price, CONFIG.margin_per_trade)
