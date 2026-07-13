#!/usr/bin/env python3
"""
One-shot MT5 order lifecycle smoke test for Strategy 98 plumbing.

Places a tiny 0.01 lot LONG and SHORT on XAUUSD (demo only), trails SL once each,
then closes. Uses TEST_MAGIC so the live s98 engine (MAGIC 980098) ignores these
positions.

    cd liveTrade
    python test_order_lifecycle.py

Exit code 0 = all steps passed, 1 = failure or aborted (non-demo account, etc.).
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from config import CONFIG
from mt5_client import MT5Client
from trade_manager_s98 import MAGIC as LIVE_MAGIC

try:
    import MetaTrader5 as mt5
except ImportError:
    print("FAIL: MetaTrader5 package not installed (Windows + MT5 terminal required).")
    sys.exit(1)

# Distinct from live s98 engine (980098) and legacy s95 (950095).
TEST_MAGIC = 989898
TEST_LOT = 0.01
TEST_COMMENT = "smoke_lifecycle"
SYMBOL = "XAUUSD"
STEP_PAUSE_SEC = 0.5


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SmokeReport:
    steps: list[StepResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append(StepResult(name, ok, detail))
        status = "PASS" if ok else "FAIL"
        line = f"  [{status}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)

    @property
    def passed(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)


def _retcode_name(code: int) -> str:
    names = {
        mt5.TRADE_RETCODE_DONE: "DONE",
        mt5.TRADE_RETCODE_REQUOTE: "REQUOTE",
        mt5.TRADE_RETCODE_REJECT: "REJECT",
        mt5.TRADE_RETCODE_INVALID_STOPS: "INVALID_STOPS",
        mt5.TRADE_RETCODE_INVALID_VOLUME: "INVALID_VOLUME",
        mt5.TRADE_RETCODE_MARKET_CLOSED: "MARKET_CLOSED",
        mt5.TRADE_RETCODE_NO_MONEY: "NO_MONEY",
    }
    return names.get(code, str(code))


def _format_result(res) -> str:
    if res is None:
        err = mt5.last_error()
        return f"order_send returned None | last_error={err}"
    rc = getattr(res, "retcode", None)
    comment = getattr(res, "comment", "")
    return f"retcode={rc} ({_retcode_name(rc) if rc is not None else '?'}) comment={comment!r}"


def _is_demo_account(info) -> bool:
    if info is None:
        return False
    if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO:
        return True
    server = (getattr(info, "server", "") or "").lower()
    return any(token in server for token in ("demo", "trial", "test"))


def _min_stop_distance(si) -> float:
    point = float(si.point or 0.01)
    stops = int(getattr(si, "trade_stops_level", 0) or getattr(si, "stops_level", 0) or 0)
    return max(stops * point, point * 50, 0.50)


def _initial_sl(direction: str, price: float, si) -> float:
    dist = _min_stop_distance(si) * 3
    if direction == "long":
        return round(price - dist, int(si.digits))
    return round(price + dist, int(si.digits))


def _trailed_sl(direction: str, entry: float, current_sl: float, price: float, si) -> float:
    dist = _min_stop_distance(si)
    digits = int(si.digits)
    if direction == "long":
        # Move SL up toward price but stay below bid and above prior SL.
        candidate = min(price - dist, entry)
        new_sl = max(current_sl + dist * 0.5, candidate)
        new_sl = min(new_sl, price - dist)
        return round(max(new_sl, current_sl), digits)
    candidate = max(price + dist, entry)
    new_sl = min(current_sl - dist * 0.5, candidate)
    new_sl = max(new_sl, price + dist)
    return round(min(new_sl, current_sl), digits)


def _wait_position(client: MT5Client, symbol: str, direction: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    broker_sym = client.resolve_symbol(symbol)
    while time.time() < deadline:
        for pos in client.open_positions(TEST_MAGIC):
            if pos.symbol == broker_sym:
                is_long = pos.type == mt5.POSITION_TYPE_BUY
                if (direction == "long" and is_long) or (direction == "short" and not is_long):
                    return pos
        time.sleep(STEP_PAUSE_SEC)
    return None


def _cleanup_test_positions(client: MT5Client, report: SmokeReport) -> None:
    positions = client.open_positions(TEST_MAGIC)
    if not positions:
        return
    for pos in positions:
        res = client.close_partial(pos, float(pos.volume))
        ok = res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE
        report.add(f"cleanup ticket {pos.ticket}", ok, _format_result(res))


def _run_side(
    client: MT5Client,
    report: SmokeReport,
    direction: str,
    si,
) -> bool:
    label = direction.upper()
    tick = client.current_price(SYMBOL)
    if tick is None:
        report.add(f"{label} place", False, "no tick")
        return False

    price = float(tick.ask if direction == "long" else tick.bid)
    sl = _initial_sl(direction, price, si)

    res = client.open_trade(SYMBOL, direction, TEST_LOT, sl, 0.0, TEST_COMMENT)
    place_ok = res is not None and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE
    report.add(f"{label} place 0.01", place_ok, _format_result(res))
    if not place_ok:
        return False

    time.sleep(STEP_PAUSE_SEC)
    pos = _wait_position(client, SYMBOL, direction)
    if pos is None:
        report.add(f"{label} position visible", False, "timeout waiting for position")
        return False
    report.add(f"{label} position visible", True, f"ticket={pos.ticket} sl={pos.sl}")

    trail_sl = _trailed_sl(direction, float(pos.price_open), float(pos.sl or sl), price, si)
    res_trail = client.modify_sl_tp(pos, sl=trail_sl, tp=0.0)
    trail_ok = res_trail is not None and getattr(res_trail, "retcode", None) == mt5.TRADE_RETCODE_DONE
    report.add(f"{label} trail SL", trail_ok, f"{pos.sl} -> {trail_sl} | {_format_result(res_trail)}")
    if not trail_ok:
        _cleanup_test_positions(client, report)
        return False

    time.sleep(STEP_PAUSE_SEC)
    pos = _wait_position(client, SYMBOL, direction)
    if pos is None:
        report.add(f"{label} close", False, "position disappeared before close")
        return False

    res_close = client.close_partial(pos, float(pos.volume))
    close_ok = res_close is not None and getattr(res_close, "retcode", None) == mt5.TRADE_RETCODE_DONE
    report.add(f"{label} close", close_ok, _format_result(res_close))

    time.sleep(STEP_PAUSE_SEC)
    still_open = _wait_position(client, SYMBOL, direction, timeout=2.0)
    gone_ok = still_open is None
    report.add(f"{label} position gone", gone_ok, "still open" if still_open else "closed")
    return place_ok and trail_ok and close_ok and gone_ok


def main() -> int:
    print("=== MT5 order lifecycle smoke test ===")
    print(f"Symbol={SYMBOL} lot={TEST_LOT} test_magic={TEST_MAGIC} live_s98_magic={LIVE_MAGIC}")

    if TEST_LOT != 0.01:
        print("FAIL: TEST_LOT must remain 0.01 for safety.")
        return 1

    report = SmokeReport()
    client = MT5Client(magic=TEST_MAGIC)

    try:
        if not client.connect():
            print("FAIL: MT5 connect failed.")
            return 1

        info = client.account_info()
        if info is None:
            print("FAIL: account_info unavailable.")
            return 1

        mode = "DEMO" if _is_demo_account(info) else "LIVE"
        print(
            f"Account: login={info.login} server={info.server} mode={mode} "
            f"balance={info.balance} {info.currency}"
        )

        if not _is_demo_account(info):
            print(
                "ABORT: Account is not DEMO. Refusing to place test orders on a live account.\n"
                "       Use an Exness trial/demo server in liveTrade/.env (e.g. Exness-MT5Trial8)."
            )
            return 1

        broker_sym = client.resolve_symbol(SYMBOL)
        if broker_sym is None:
            report.add("resolve symbol", False, SYMBOL)
            return 1
        report.add("resolve symbol", True, broker_sym)

        si = client.symbol_info(SYMBOL)
        if si is None:
            report.add("symbol info", False, broker_sym)
            return 1
        report.add("symbol info", True, f"digits={si.digits} stops={getattr(si, 'trade_stops_level', '?')}")

        _cleanup_test_positions(client, report)

        long_ok = _run_side(client, report, "long", si)
        short_ok = _run_side(client, report, "short", si)

        print()
        print(f"LONG cycle:  {'PASS' if long_ok else 'FAIL'}")
        print(f"SHORT cycle: {'PASS' if short_ok else 'FAIL'}")
        print(f"OVERALL:     {'PASS' if report.passed else 'FAIL'}")
        return 0 if report.passed else 1
    finally:
        _cleanup_test_positions(client, report)
        client.shutdown()


if __name__ == "__main__":
    sys.exit(main())
