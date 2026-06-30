"""
liveTrade engine — 24/7 multi-symbol Strategy-95 runner.

Per-timeframe cadence (only scans a timeframe when its candle has CLOSED):
  4H cycle  -> detect liquidity-sweep BIAS  (writes passes/4h_passes.jsonl)
  1H cycle  -> displacement MSS for biases   (passes/1h_passes.jsonl)
  15M cycle -> order block in OTE            (passes/15m_passes.jsonl)
  5M cycle  -> fresh 5M tap -> EXECUTE        (passes/5m_passes.jsonl)

Each symbol carries independent bias tracks (16h TTL). One position per pair.
Open positions are managed (partial @0.5R + breakeven) every loop.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import CONFIG
from logging_setup import get_engine_logger, get_tf_logger, record_pass, record_trade
import detection as D
from detection import BIAS_TTL_HOURS, FINAL_R, PARTIAL_R
from mt5_client import MT5Client, TF_MINUTES
from notifier import trade_email, send_email
from trade_manager import TradeManager, MAGIC

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

log = get_engine_logger()
UTC = timezone.utc
TF_ORDER = ["4h", "1h", "15m", "5m"]


def _pip_size(price: float) -> float:
    p = abs(price)
    if p >= 1000: return 1.0
    if p >= 100: return 0.1
    if p >= 10: return 0.01
    return 0.0001


def _floor_utc(dt: datetime, minutes: int) -> datetime:
    em = int(dt.timestamp() // 60)
    return datetime.fromtimestamp(((em // minutes) * minutes) * 60, tz=UTC)


class Track:
    __slots__ = ("sweep_ts", "bias", "expires", "mss", "status")

    def __init__(self, bias, expires):
        self.sweep_ts = bias.sweep_timestamp
        self.bias = bias
        self.expires = expires
        self.mss = None
        self.status = "bias"      # bias -> mss -> ob -> done


class Engine:
    def __init__(self):
        self.client = MT5Client()
        self.tm = TradeManager(self.client)
        self.tracks: dict[str, dict] = {s: {} for s in CONFIG.symbols}   # symbol -> {sweep_iso: Track}
        self.last_boundary: dict[str, datetime] = {}

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        if not self.client.connect():
            log.error("Could not connect to MT5 — aborting.")
            return
        acc = self.client.account_info()
        send_email("Strategy95 liveTrade STARTED",
                   f"Engine started.\nSymbols: {', '.join(CONFIG.symbols)}\n"
                   f"DRY_RUN={CONFIG.dry_run} | margin Rs{CONFIG.margin_per_trade:.0f} x 1:{CONFIG.leverage:.0f}\n"
                   f"Account: {getattr(acc,'login','?')} {getattr(acc,'currency','?')} bal={getattr(acc,'balance','?')}")
        log.info(f"liveTrade started | DRY_RUN={CONFIG.dry_run} | symbols={CONFIG.symbols}")
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
        # 1) manage open positions every tick
        self.tm.monitor()
        # 2) run any timeframe whose candle just closed (with settle lag)
        for tf in TF_ORDER:
            boundary = _floor_utc(now, TF_MINUTES[tf])
            last = self.last_boundary.get(tf)
            if (last is None or boundary > last) and (now - boundary).total_seconds() >= CONFIG.candle_close_lag:
                self.last_boundary[tf] = boundary
                self._run_tf_cycle(tf, now)

    # ---------------------------------------------------------------- cycles
    def _run_tf_cycle(self, tf: str, now: datetime):
        tlog = get_tf_logger(tf)
        tlog.info(f"=== {tf} cycle @ {now.isoformat()} ===")
        for sym in CONFIG.symbols:
            try:
                if tf == "4h":
                    self._cycle_4h(sym, now, tlog)
                elif tf == "1h":
                    self._cycle_1h(sym, now, tlog)
                elif tf == "15m":
                    self._cycle_15m(sym, now, tlog)
                elif tf == "5m":
                    self._cycle_5m(sym, now, tlog)
            except Exception as e:
                tlog.exception(f"{sym} {tf} cycle error: {e}")
        # prune expired tracks
        for sym in CONFIG.symbols:
            for k in [k for k, t in self.tracks[sym].items() if t.expires < now or t.status == "done"]:
                self.tracks[sym].pop(k, None)

    def _cycle_4h(self, sym, now, tlog):
        df4 = self.client.fetch_closed(sym, "4h", 320)
        if df4 is None:
            tlog.info(f"{sym}: no 4h data"); return
        ttl_cut = now - timedelta(hours=BIAS_TTL_HOURS)
        new = 0
        for bias in D.detect_biases(df4):
            if bias.sweep_timestamp.to_pydatetime() < ttl_cut:
                continue
            key = bias.sweep_timestamp.isoformat()
            if key in self.tracks[sym]:
                continue
            expires = bias.sweep_timestamp + pd.Timedelta(hours=BIAS_TTL_HOURS)
            self.tracks[sym][key] = Track(bias, expires)
            new += 1
            lvl = bias.sweep_level
            tlog.info(f"{sym}: 4H BIAS {bias.direction.value} | swept {lvl.level_type} {lvl.price:.5f} @ {bias.sweep_timestamp}")
            record_pass("4h", {"symbol": sym, "bias": bias.direction.value,
                               "swept_level": float(lvl.price), "swept_type": lvl.level_type,
                               "level_formed": str(lvl.datetime), "sweep_time": str(bias.sweep_timestamp),
                               "expires": str(expires)})
        if new == 0:
            tlog.info(f"{sym}: no new 4H bias")

    def _cycle_1h(self, sym, now, tlog):
        pend = [t for t in self.tracks[sym].values() if t.status == "bias" and t.expires >= now]
        if not pend:
            return
        df1 = self.client.fetch_closed(sym, "1h", 320)
        if df1 is None:
            return
        for t in pend:
            mss = D.find_mss(df1, t.bias, t.expires)
            if mss is not None:
                t.mss = mss; t.status = "mss"
                tlog.info(f"{sym}: 1H MSS {t.bias.direction.value} @ {mss.mss.break_price:.5f} ({mss.timestamp})")
                record_pass("1h", {"symbol": sym, "direction": t.bias.direction.value,
                                   "mss_break_price": float(mss.mss.break_price),
                                   "mss_time": str(mss.timestamp), "sweep_time": str(t.sweep_ts)})

    def _cycle_15m(self, sym, now, tlog):
        pend = [t for t in self.tracks[sym].values() if t.status == "mss" and t.expires >= now]
        if not pend:
            return
        df15 = self.client.fetch_closed(sym, "15m", 320)
        if df15 is None:
            return
        for t in pend:
            ob = D.find_ob_zone(df15, t.bias, t.mss)
            if ob is not None:
                t.status = "ob"
                tlog.info(f"{sym}: 15M OB in OTE {ob.bottom:.5f}-{ob.top:.5f}")
                record_pass("15m", {"symbol": sym, "direction": t.bias.direction.value,
                                    "ob_top": float(ob.top), "ob_bottom": float(ob.bottom),
                                    "ob_time": str(ob.datetime), "sweep_time": str(t.sweep_ts)})

    def _cycle_5m(self, sym, now, tlog):
        pend = [t for t in self.tracks[sym].values() if t.status in ("ob", "mss") and t.expires >= now]
        if not pend:
            return
        # guards
        if CONFIG.one_trade_per_pair and self.client.position_for_symbol(sym) is not None:
            return
        if len(self.client.open_positions(MAGIC)) >= CONFIG.max_concurrent:
            tlog.info(f"{sym}: max concurrent trades reached — skip"); return
        if self._daily_loss_exceeded():
            tlog.info("daily loss limit hit — no new trades today"); return

        df15 = self.client.fetch_closed(sym, "15m", 320)
        df5 = self.client.fetch_closed(sym, "5m", 600)
        if df15 is None or df5 is None or len(df5) < 5:
            return
        last_closed_5m = df5.index[-1]

        for t in pend:
            ms = df5.index.searchsorted(t.mss.timestamp)
            win = df5.iloc[ms:]
            ob = D.find_entry(df15, win, t.bias, t.mss)
            if ob is None:
                continue
            # FRESH: only act if the tap is on the just-closed 5M candle
            if pd.Timestamp(ob.timestamp) < last_closed_5m:
                continue
            trade = D.build_trade(sym, t.bias, ob, df5)
            if trade is None:
                continue
            t.status = "done"
            self._execute(sym, t, ob, trade, tlog, now)
            return  # one entry per 5m cycle per symbol

    # ---------------------------------------------------------------- execution
    def _execute(self, sym, track, ob, trade, tlog, now):
        direction, entry, sl, tp, risk = trade["direction"], trade["entry"], trade["sl"], trade["tp"], trade["risk"]
        risk_pips = risk / _pip_size(entry)
        lots = self.client.lots_for_margin(sym, direction, entry, CONFIG.margin_per_trade)
        lvl = track.bias.sweep_level
        base = {"symbol": sym, "direction": direction, "entry": entry, "sl": sl, "tp": tp,
                "lots": lots, "risk_pips": risk_pips, "margin": CONFIG.margin_per_trade,
                "leverage": CONFIG.leverage, "partial_r": PARTIAL_R, "final_r": FINAL_R,
                "time_utc": now.isoformat(), "swept_level": f"{lvl.price:.5f}",
                "sweep_dir": track.bias.direction.value, "mss_price": f"{track.mss.mss.break_price:.5f}"}
        record_pass("5m", {**base, "event": "entry_signal"})

        if lots <= 0:
            tlog.error(f"{sym}: lot size computed 0 — skip"); return

        if CONFIG.dry_run:
            tlog.info(f"[DRY RUN] {sym} {direction.upper()} entry {entry:.5f} SL {sl:.5f} TP {tp:.5f} lots~{lots}")
            record_trade({"event": "dry_run_entry", **base})
            trade_email({**base, "dry_run": True})
            return

        res = self.client.open_trade(sym, direction, lots, sl, tp, comment=f"s95 {direction}")
        if res is None or getattr(res, "retcode", None) != mt5.TRADE_RETCODE_DONE:
            tlog.error(f"{sym}: order_send failed -> {res}")
            return
        # use actual fill price for management
        pos = self.client.position_for_symbol(sym)
        fill = float(pos.price_open) if pos else entry
        actual_risk = abs(fill - sl)
        ticket = getattr(res, "order", None) or (pos.ticket if pos else 0)
        if pos:
            self.tm.register(pos.ticket, {"symbol": sym, "direction": direction, "entry": fill,
                                          "sl": sl, "tp": tp, "risk": actual_risk, "lots": lots})
        tlog.info(f"{sym}: EXECUTED {direction.upper()} {lots} lots @ {fill:.5f} SL {sl:.5f} TP {tp:.5f} (ticket {ticket})")
        record_trade({"event": "executed", "ticket": str(ticket), **base, "fill": fill})
        trade_email({**base, "entry": fill, "dry_run": False})

    # ---------------------------------------------------------------- guards
    def _daily_loss_exceeded(self) -> bool:
        if CONFIG.max_daily_loss <= 0 or mt5 is None:
            return False
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(start, datetime.now(UTC))
        if not deals:
            return False
        realized = sum(d.profit for d in deals if d.magic == MAGIC)
        return realized <= -abs(CONFIG.max_daily_loss)
