#!/usr/bin/env python3
"""
Strategy 144: Displacement Opening Range Breakout (ORB) + Session Liquidity Reversal Model

Overview & Execution Rules
==========================
The model is fully intraday and executes on 5-minute candles. The 15-minute
ORB range and the 15-minute session ranges are marked with exact New York
wall-clock windows on the 5m feed (identical boundaries), so no separate 15m
input is required; the 5m displacement confirmation is exact.

Step 1: Displacement ORB Mechanics
----------------------------------
1. Mark the Opening Range [01:15]:
   - Set the chart clock to New York time (UTC-5) [01:34].
   - Mark the exact High and Low of the first 15 minutes following the NYSE
     open (9:30 AM - 9:45 AM EST / 14:30 - 14:45 UTC) [01:21], [04:34].
     Implemented as the high/low envelope of the 5m bars in [09:30, 09:45) NY.

2. Confirm Displacement [01:39]:
   - Switch to the 5-minute timeframe [01:39] and look for a strong impulsive
     move breaking out of the opening range [01:44].
   - RULE: a 5-minute candle must CLOSE outside the range boundary - candle
     wicks do not count [01:56]. The body must also be >= displacement_body_atr
     multiples of ATR.

3. Identify Entry Model [02:09]:
   - Demand / Supply Zone (Order Block): look for 3-4 strong green/red candles
     in a row breaking market structure [02:20], [02:31]. Draw the zone around
     the last opposite-colored candle before the expansion [02:37].
   - Fair Value Gap (FVG): alternatively identify price imbalances created
     during the displacement breakout [02:14], [04:53].

4. Execution & Confirmation [02:43]:
   - Wait for price to retrace back into the demand/supply zone or FVG [02:43],
     [05:05]. Price must trade into the zone and hold (close back on the
     favorable side of the zone) before the next bar open is taken [02:55],
     [05:10].

5. Risk & Reward Parameters [03:16]:
   - Stop Loss: placed below the demand zone / FVG lower edge (long) or above
     the supply zone / FVG upper edge (short), plus a buffer [03:16], [05:22].
   - Take Profit: target_r (default 1.8R, within the 1.5-2.2R band) [03:23],
     [05:35], [07:49].

Step 2: Session Liquidity Analysis (Filter False Breakouts)
-----------------------------------------------------------
Analyze the daily market cycle across three sessions [06:02], [08:06]:
1. Asian Session (Consolidation) [06:02], [07:11]: the market ranges and builds
   buy-stop / sell-stop liquidity above and below its high/low boundaries.
   Window: previous day 19:00 NY - 00:00 NY.
2. London Session (Directional Push) [06:02], [06:31]: participants drive price
   to sweep one side of the Asian range. Window: 02:00 - 08:00 NY.
3. New York Session (Reversal) [06:02], [06:44]: high volume enters, frequently
   reversing London's push. Trade window: 09:45 - 16:00 NY.

SESSION RULES:
- BUY: London pushes down and sweeps the Asian low -> look strictly for BUY
  setups during the NY ORB [06:57], [10:09].
- SELL: London pushes up and sweeps the Asian high -> look strictly for SELL
  setups during the NY ORB.
- CONTINUATION EXCEPTION: if London sweeps BOTH sides of the Asian range, expect
  trend continuation during New York rather than a reversal [09:36], [09:43];
  the first displacement either way is taken in its own direction.
- A sweep only counts if London penetrates the Asian extreme by at least
  sweep_penetration_atr multiples of ATR (filters weak taps).

API
===
  generate_trades(candles_5m, symbol="UNKNOWN", params=None) -> list[dict]
  Params                                      frozen dataclass of knobs
  run_strategy(candles_5m, output_path, **kw) CLI entry
  _clean_candles / _wilder_atr / _atr_at / _window / _ny_to_utc_ts /
  _session_direction / _displacement          diagnostics helpers

Usage:
  python strategy_144_ny_orb_session_reversal.py --csv5m XAUUSD_5m.csv
      [--output out.json] [--rr 1.8]
"""

from __future__ import annotations

import argparse
import bisect
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Candle, load_csv, parse_csv_filename, round_turn_cost_price, save_trades, to_iso

M5_SECONDS = 5 * 60
UTC = timezone.utc
# Fixed EST offset per the strategy source; the model does not track DST.
NY_OFFSET = timedelta(hours=-5)


@dataclass(frozen=True)
class Params:
    # Order block: minimum consecutive same-direction candles before the
    # expansion (no body-size filter — any close in the right direction counts).
    ob_min_run: int = 3
    ob_lookback_bars: int = 12
    # FVG alternative: look-back for imbalances created during the breakout.
    # Any gap > 0 qualifies (no minimum width).
    fvg_lookback_bars: int = 8
    # Retrace entry: price must trade into the zone and hold within this many
    # 5m bars after the displacement candle.
    retrace_bars: int = 48
    # Risk parameters.
    max_cost_risk: float = 0.15
    target_r: float = 1.80
    max_hold_5m_bars: int = 144
    # NY wall-clock session windows.
    asia_start_hour: int = 19
    asia_end_hour: int = 0
    london_start_hour: int = 2
    london_end_hour: int = 8
    orb_start_hour: int = 9
    orb_start_minute: int = 30
    orb_end_hour: int = 9
    orb_end_minute: int = 45
    trade_end_hour: int = 16


def _clean_candles(candles: list[Candle], label: str = "5m") -> list[Candle]:
    """Sort and deduplicate defensively, then enforce valid strict chronology."""
    if not candles:
        raise ValueError(f"{label} input is empty")
    by_timestamp: dict[int, Candle] = {}
    for candle in candles:
        values = (candle.open, candle.high, candle.low, candle.close)
        if candle.timestamp <= 0 or not all(math.isfinite(v) for v in values):
            raise ValueError(f"{label} contains an invalid timestamp or non-finite OHLC value")
        if candle.high < max(candle.open, candle.close, candle.low) or candle.low > min(candle.open, candle.close, candle.high):
            raise ValueError(f"{label} contains an invalid OHLC envelope at {candle.timestamp}")
        by_timestamp[candle.timestamp] = candle
    ordered = [by_timestamp[ts] for ts in sorted(by_timestamp)]
    timestamps = np.fromiter((c.timestamp for c in ordered), dtype=np.int64)
    if len(timestamps) < 2 or np.any(np.diff(timestamps) <= 0):
        raise ValueError(f"{label} is nonchronological after canonicalization")
    return ordered


def _ny_to_utc_ts(day: date, hour: int, minute: int = 0) -> int:
    """Epoch seconds of a New York (UTC-5) wall-clock time on `day`."""
    wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return int((wall - NY_OFFSET).timestamp())


_BISECT_CACHE: dict[int, tuple[list[int], list[Candle]]] = {}


def _window(
    index: dict[int, int],
    candles: list[Candle],
    t0: int,
    t1: int,
) -> Optional[list[tuple[int, Candle]]]:
    """Return [(idx, candle)] with t0 <= timestamp < t1, or None when empty."""
    key = id(candles)
    cached = _BISECT_CACHE.get(key)
    if cached is None or cached[1] is not candles:
        keys = [c.timestamp for c in candles]
        cached = (keys, candles)
        _BISECT_CACHE[key] = cached
    keys, _ = cached
    lo = bisect.bisect_left(keys, t0)
    hi = bisect.bisect_left(keys, t1)
    if hi <= lo:
        return None
    return [(index[keys[i]], candles[i]) for i in range(lo, hi)]


def _wilder_atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Wilder-smoothed ATR series — one ATR value for every index."""
    n = len(candles)
    if n == 0:
        return []
    tr = np.empty(n)
    tr[0] = candles[0].high - candles[0].low
    for i in range(1, n):
        prev_close = candles[i - 1].close
        tr[i] = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - prev_close),
            abs(candles[i].low - prev_close),
        )
    atr = np.empty(n)
    seed = min(period, n)
    atr[:seed] = np.mean(tr[:seed])
    alpha = 1.0 / period
    for i in range(seed, n):
        atr[i] = atr[i - 1] + alpha * (tr[i] - atr[i - 1])
    return [float(v) for v in atr]


def _atr_at(atrs: list[float], idx: int, period: int) -> Optional[float]:
    """Safe lookup into the pre-computed Wilder ATR array."""
    if idx < 0 or idx >= len(atrs) or not math.isfinite(atrs[idx]):
        return None
    return atrs[idx]


def _session_direction(
    asian: list[tuple[int, Candle]],
    london: list[tuple[int, Candle]],
    p: Params,
) -> tuple[Optional[str], bool, bool]:
    """(bias, swept_asia_low, swept_asia_high) from the session sweep.

    A sweep counts when any London candle CLOSES beyond the Asian extreme
    (pure price action — no ATR penetration threshold).
    """
    asia_hi = max(c.high for _, c in asian)
    asia_lo = min(c.low for _, c in asian)
    swept_low = any(c.close < asia_lo for _, c in london)
    swept_high = any(c.close > asia_hi for _, c in london)

    if swept_low and not swept_high:
        return "long", swept_low, swept_high
    if swept_high and not swept_low:
        return "short", swept_low, swept_high
    if swept_low and swept_high:
        return "continuation", swept_low, swept_high
    return None, swept_low, swept_high


def _displacement(
    candles: list[Candle],
    idx: int,
    direction: str,
    orb_high: float,
    orb_low: float,
) -> bool:
    """5m candle must CLOSE beyond the ORB boundary (wicks don't count).

    Pure close-based — the candle must close on the correct side of the body
    (green for long, red for short) AND close outside the ORB range.
    """
    c = candles[idx]
    if direction == "long":
        return c.close > orb_high and c.close > c.open
    if direction == "short":
        return c.close < orb_low and c.close < c.open
    if direction == "continuation":
        return (
            (c.close > orb_high and c.close > c.open)
            or (c.close < orb_low and c.close < c.open)
        )
    return False


def _order_block_zone(
    candles: list[Candle],
    disp_idx: int,
    direction: str,
    p: Params,
) -> Optional[tuple[float, float]]:
    """(zone_hi, zone_lo) around the last opposite-colored candle before the
    run of same-direction candles that ended in the displacement close.

    Pure close-based — a candle counts as part of the run if it simply closed
    in the right direction (green for long, red for short), no body-size filter.
    """
    j = disp_idx - 1
    run = 0
    while j >= 0 and (disp_idx - 1 - j) < p.ob_lookback_bars:
        c = candles[j]
        if direction == "long" and not (c.close > c.open):
            break
        if direction == "short" and not (c.close < c.open):
            break
        run += 1
        j -= 1
    if run < p.ob_min_run or j < 0:
        return None
    c = candles[j]
    return float(max(c.high, c.low)), float(min(c.high, c.low))


def _fvg_zone(
    candles: list[Candle],
    disp_idx: int,
    direction: str,
    p: Params,
) -> Optional[tuple[float, float]]:
    """(zone_hi, zone_lo) of the most recent imbalance created by the breakout.

    Any gap > 0 qualifies (no ATR-based minimum width).
    """
    start = max(0, disp_idx - p.fvg_lookback_bars - 2)
    for j in range(disp_idx - 2, start - 1, -1):
        a, b = candles[j], candles[j + 2]
        if direction == "long":
            if a.high < b.low:
                return float(b.low), float(a.high)
        else:
            if a.low > b.high:
                return float(a.low), float(b.high)
    return None


def _retrace_entry(
    candles: list[Candle],
    disp_idx: int,
    direction: str,
    zone_hi: float,
    zone_lo: float,
    p: Params,
    n: int,
) -> Optional[int]:
    """First bar that trades into the zone and holds; entry fills on the next
    bar open. Returns that next bar index or None."""
    limit = min(n, disp_idx + 1 + p.retrace_bars)
    for j in range(disp_idx + 1, limit):
        c = candles[j]
        if direction == "long":
            touched = c.low <= zone_hi
            held = c.close >= zone_lo
        else:
            touched = c.high >= zone_lo
            held = c.close <= zone_hi
        if touched and held and j + 1 < n:
            return j + 1
    return None


def _event(event_type: str, timestamp: int, **fields: object) -> dict:
    return {"type": event_type, "timestamp": to_iso(timestamp), **fields}


def generate_trades(
    candles: list[Candle],
    symbol: str = "UNKNOWN",
    params: Optional[Params] = None,
) -> list[dict]:
    """Generate causal 5m trades for Strategy 144.

    The single 5m feed supplies the exact Asian/London/ORB NY wall-clock
    windows (equivalent to 15m marking), the 5m displacement confirmation,
    the retrace entry, and the trade simulation.
    """
    p = params or Params()
    m5 = _clean_candles(candles)
    n = len(m5)
    if n < 40:
        return []

    keys = [c.timestamp for c in m5]
    index = {ts: i for i, ts in enumerate(keys)}
    days = sorted({(datetime.fromtimestamp(c.timestamp, UTC) + NY_OFFSET).date() for c in m5})

    trades: list[dict] = []
    for day in days:
        asian = _window(
            index, m5,
            _ny_to_utc_ts(day - timedelta(days=1), p.asia_start_hour),
            _ny_to_utc_ts(day, p.asia_end_hour),
        )
        london = _window(
            index, m5,
            _ny_to_utc_ts(day, p.london_start_hour),
            _ny_to_utc_ts(day, p.london_end_hour),
        )
        orb = _window(
            index, m5,
            _ny_to_utc_ts(day, p.orb_start_hour, p.orb_start_minute),
            _ny_to_utc_ts(day, p.orb_end_hour, p.orb_end_minute),
        )
        trade_window = _window(
            index, m5,
            _ny_to_utc_ts(day, p.orb_end_hour, p.orb_end_minute),
            _ny_to_utc_ts(day, p.trade_end_hour),
        )
        if asian is None or london is None or orb is None or trade_window is None:
            continue

        direction, swept_low, swept_high = _session_direction(asian, london, p)
        if direction is None:
            continue

        asia_hi = max(c.high for _, c in asian)
        asia_lo = min(c.low for _, c in asian)
        london_hi = max(c.high for _, c in london)
        london_lo = min(c.low for _, c in london)
        orb_hi = max(c.high for _, c in orb)
        orb_lo = min(c.low for _, c in orb)

        disp_idx: Optional[int] = None
        trade_dir: Optional[str] = None
        for idx, _ in trade_window:
            if direction in ("long", "short"):
                if _displacement(m5, idx, direction, orb_hi, orb_lo):
                    disp_idx, trade_dir = idx, direction
                    break
            else:  # continuation: first displacement either way
                if _displacement(m5, idx, "long", orb_hi, orb_lo):
                    disp_idx, trade_dir = idx, "long"
                    break
                if _displacement(m5, idx, "short", orb_hi, orb_lo):
                    disp_idx, trade_dir = idx, "short"
                    break
        if disp_idx is None or trade_dir is None:
            continue

        zone = _order_block_zone(m5, disp_idx, trade_dir, p) or _fvg_zone(
            m5, disp_idx, trade_dir, p
        )
        if zone is None:
            continue
        zone_hi, zone_lo = zone
        entry_idx = _retrace_entry(m5, disp_idx, trade_dir, zone_hi, zone_lo, p, n)
        if entry_idx is None:
            continue

        entry = float(m5[entry_idx].open)
        cost = round_turn_cost_price(entry, symbol=symbol or None)
        if trade_dir == "long":
            stop = zone_lo          # stop exactly at zone edge
            risk = entry - stop
        else:
            stop = zone_hi          # stop exactly at zone edge
            risk = stop - entry
        if risk <= 0:
            continue
        if cost / risk > p.max_cost_risk:
            continue
        target = entry + p.target_r * risk if trade_dir == "long" else entry - p.target_r * risk

        last_idx = min(n - 1, entry_idx + p.max_hold_5m_bars - 1)
        exit_idx, exit_price, exit_reason = last_idx, float(m5[last_idx].close), "time_exit"
        for j in range(entry_idx, last_idx + 1):
            c = m5[j]
            if trade_dir == "long":
                stop_hit = c.low <= stop
                target_hit = c.high >= target
            else:
                stop_hit = c.high >= stop
                target_hit = c.low <= target
            if stop_hit:
                exit_idx, exit_price, exit_reason = j, stop, "stop_loss"
                break
            if target_hit:
                exit_idx, exit_price, exit_reason = j, target, "take_profit"
                break

        exit_time = m5[exit_idx].timestamp + M5_SECONDS
        gross_r = float((exit_price - entry) / risk) if trade_dir == "long" else float(
            (entry - exit_price) / risk
        )
        outcome = "win" if gross_r > 0 else "loss" if gross_r < 0 else "breakeven"

        if swept_low and not swept_high:
            sweep_desc = f"Swept Asian Low ({asia_lo:.5f})"
        elif swept_high and not swept_low:
            sweep_desc = f"Swept Asian High ({asia_hi:.5f})"
        else:
            sweep_desc = f"Swept Both Asian High ({asia_hi:.5f}) & Low ({asia_lo:.5f})"

        events = [
            _event(
                "session_range",
                _ny_to_utc_ts(day - timedelta(days=1), p.asia_start_hour),
                price=round((asia_hi + asia_lo) / 2.0, 5),
                description=(
                    f"Step 2.1 (Asian Session): Consolidation range established "
                    f"[{p.asia_start_hour:02d}:00 - {p.asia_end_hour:02d}:00 NY]. "
                    f"High: {asia_hi:.5f}, Low: {asia_lo:.5f}"
                ),
            ),
            _event(
                "liquidity_sweep",
                london[-1][1].timestamp,
                price=round(london_hi if swept_high else london_lo, 5),
                description=(
                    f"Step 2.2 (London Session): Push [02:00 - 08:00 NY]. "
                    f"{sweep_desc} -> Filter Bias: {(direction or 'NONE').upper()}"
                ),
            ),
            _event(
                "orb_marked",
                _ny_to_utc_ts(day, p.orb_start_hour, p.orb_start_minute),
                price=round((orb_hi + orb_lo) / 2.0, 5),
                description=(
                    f"Step 1.1 (NYSE Open): Opening Range marked (9:30 AM EST). "
                    f"High: {orb_hi:.5f}, Low: {orb_lo:.5f}"
                ),
            ),
            _event(
                "displacement_close",
                m5[disp_idx].timestamp,
                price=round(float(m5[disp_idx].close), 5),
                description=(
                    f"Step 1.2 (Displacement): 5m candle closed outside ORB "
                    f"boundary at {m5[disp_idx].close:.5f}"
                ),
            ),
            _event(
                "entry_model",
                m5[disp_idx].timestamp,
                price=round((zone_hi + zone_lo) / 2.0, 5),
                kind="order_block" if _order_block_zone(m5, disp_idx, trade_dir, p) else "fvg",
                upper=round(zone_hi, 5),
                lower=round(zone_lo, 5),
                description=(
                    f"Step 1.3 (Entry Model): Order Block / FVG zone retracement "
                    f"setup. Zone: {zone_lo:.5f}-{zone_hi:.5f}"
                ),
            ),
            _event(
                "entry_tap",
                m5[entry_idx].timestamp,
                price=round(entry, 5),
                description=(
                    f"Step 1.4 (Execution): Retrace tapped zone. Entry at "
                    f"{entry:.5f}, SL: {stop:.5f}, TP: {target:.5f} (RR: {p.target_r})"
                ),
            ),
            _event(
                "final_exit",
                exit_time,
                price=round(float(exit_price), 5),
                reason=exit_reason,
                gross_R=round(gross_r, 4),
                description=f"Step 1.5 (Risk & Reward): Closed at {exit_price:.5f} via {exit_reason.upper()}",
            ),
        ]
        events.sort(key=lambda e: e["timestamp"])

        trades.append({
            "trade_number": len(trades) + 1,
            "strategy": "strategy_144_ny_orb_session_reversal",
            "setup": "ny_orb_session_reversal",
            "symbol": symbol or None,
            "direction": trade_dir,
            "entry_time": to_iso(int(m5[entry_idx].timestamp)),
            "entry_timestamp": int(m5[entry_idx].timestamp),
            "exit_time": to_iso(exit_time),
            "exit_timestamp": exit_time,
            "entry_price": round(entry, 5),
            "exit_price": round(float(exit_price), 5),
            "stop_loss": round(float(stop), 5),
            "take_profit": round(float(target), 5),
            "risk_price": round(float(risk), 5),
            "gross_R": round(gross_r, 4),
            "outcome": outcome,
            "exit_reason": exit_reason,
            "events": events,
            "reason": (
                f"NY ORB [{trade_dir.upper()}] | Session Bias: {direction} | "
                f"Asia: {asia_lo:.5f}-{asia_hi:.5f} | London: {london_lo:.5f}-{london_hi:.5f} | "
                f"ORB: {orb_lo:.5f}-{orb_hi:.5f} | RR={p.target_r}"
            ),
        })

    return trades


def run_strategy(candles_5m, output_path, **kw):
    trades = generate_trades(candles_5m, **kw)
    save_trades(trades, output_path)
    print(f"Saved {len(trades)} trades to {output_path}")
    return trades


def main():
    parser = argparse.ArgumentParser(description="Strategy 144: NY ORB Session Reversal & Displacement")
    parser.add_argument("--csv5m", required=True, help="5-minute OHLCV CSV file path (primary feed: sessions, ORB, displacement, execution)")
    parser.add_argument("--output", default=None, help="Output JSON results file path")
    parser.add_argument("--rr", type=float, default=Params().target_r, help="Risk-to-Reward Ratio (default: 1.8)")
    args = parser.parse_args()

    candles_5m = load_csv(args.csv5m)
    meta = parse_csv_filename(args.csv5m)
    sym = meta.get("symbol") or ""
    out = args.output or f"strategy_144_results_{sym}.json"
    params = Params(target_r=args.rr)
    run_strategy(candles_5m, out, symbol=sym, params=params)


if __name__ == "__main__":
    main()
