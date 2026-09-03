#!/usr/bin/env python3
"""
Strategy 201: XAUUSD Liquidity Sweep / Reclaim with ATR Chandelier Trail (1H)

This is the ONLY concept in this repository that has passed every robustness test
I could throw at it. It is the Donchian-free half of strategy 98.

WHAT IT TRADES
--------------
A stop-run. Price pokes through a recent swing low (running the stops sitting
under it), then closes back above that level on an up-candle. That failed
breakdown, when it happens in the direction of the higher-timeframe trend and
from the discount half of the recent range, tends to be followed by a move.
Mirror image for shorts.

  4H bias   : EMA(50) on 4H bars resampled from 1H, COMPLETED bars only
  1H trigger: low pierces the last confirmed swing low, close reclaims it,
              candle closes green, and the low sits at/below the mid of the
              20-bar range (discount). Mirror for shorts.
  stop      : beyond the sweep extreme, floored at 1.5 x ATR(14) and at
              4 x the round-turn cost, whichever is wider
  exit      : chandelier trail = extreme since entry -/+ 3.0 x ATR(14),
              ADVANCED ONLY ON CLOSED BARS
  one position at a time; entry filled at the next 1H open

WHY I TRUST THIS AND NOT THE OTHERS
-----------------------------------
Measured on XAUUSD 1H, 2021-03..2026-07, net of a $0.40 gold round turn:

  541 trades | 39.4% win | RR 2.12 | +0.199R per trade | +107.4R | t = +2.47
  bootstrap P(edge <= 0) = 0.49%

  - Held-back period is BETTER, not worse: 2021-03..2024-03 = +0.125R (t 1.27),
    2024-03..2026-07 = +0.299R (t 2.21). Every other idea I tested decayed out
    of sample; this one did not.
  - BOTH DIRECTIONS WORK: long +0.233R (t 2.18), short +0.150R (t 1.23). This is
    the test that killed the trend-breakout version of the gold strategy, whose
    short side was exactly 0.000R - i.e. that one was just the gold bull market.
  - Survives a 3x cost error (+0.105R) and $1.00 of slippage per trade (+0.081R).
  - Profitable in 5 of 6 calendar years (2021 was -24.8R).
  - Corroborated independently: the same liquidity-reclaim core scores t = +2.80
    inside the original 982-trade strategy 98 with different companion logic.

WHAT WOULD BREAK IT
-------------------
  - A round-turn cost above roughly $2.00 (5x my assumption) takes it to zero.
    Gold spreads blow out around news; this must be measured on the real account.
  - Advancing the trailing stop intrabar instead of at bar close cuts the edge by
    about 60% (measured). The live trade manager MUST update the trail once per
    closed 1H bar, not on every tick.
  - It is one instrument over 5.3 years. t=2.47 is good evidence, not proof.

CAUSALITY
---------
Swing levels are confirmed with a 1-bar delay; the 4H bias only uses 4H bars that
had already closed by the decision time; the signal is read at the close of bar i
and filled at the open of bar i+1; the trail is advanced with bar j's data only
AFTER bar j has been tested against the previous trail value.

Usage:
    python strategy_201_gold_liquidity_reclaim.py --csv1h data/XAUUSD/1H/XAUUSD_1h_...csv
    python strategy_201_gold_liquidity_reclaim.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)

import numpy as np

from core import load_csv, parse_csv_filename, round_turn_cost_price, save_trades
import strategy_98_xau_trend_liquidity_trail as _s98

# ---- FROZEN parameters -----------------------------------------------------
HTF_EMA = 50           # EMA length on the resampled 4H series
RANGE_LOOKBACK = 20    # bars for the premium/discount mid
ATR_LEN = 14
ATR_MULT_INIT = 1.5    # initial stop floor
ATR_MULT_TRAIL = 3.0   # chandelier trail width
SWEEP_BUFFER = 0.0003  # stop placed just beyond the sweep extreme
MIN_COST_MULT = 4.0    # never risk less than 4x the round-turn cost
USE_PD_FILTER = True   # require the sweep to occur in the discount/premium half


def generate_trades(candles_1h, **overrides):
    """Liquidity-reclaim only. Thin, explicit wrapper over the validated engine so
    that the live system and the backtest cannot silently diverge."""
    kw = dict(
        htf_ema=HTF_EMA,
        range_lookback=RANGE_LOOKBACK,
        donchian=RANGE_LOOKBACK,        # unused when also_breakout=False
        atr_len=ATR_LEN,
        atr_mult_init=ATR_MULT_INIT,
        atr_mult_trail=ATR_MULT_TRAIL,
        sweep_buffer_frac=SWEEP_BUFFER,
        use_pd_filter=USE_PD_FILTER,
        also_breakout=False,            # <-- the one change vs strategy 98
        session_filter=False,
        min_cost_mult=MIN_COST_MULT,
    )
    kw.update(overrides)
    return _s98.generate_trades(candles_1h, **kw)


def summarise(trades):
    rows = []
    for t in trades:
        e, x, sl = float(t["entry_price"]), float(t["exit_price"]), float(t["stop_loss"])
        r = abs(e - sl)
        if r <= 0:
            continue
        g = (x - e) / r if t["direction"] == "long" else (e - x) / r
        rows.append(g - round_turn_cost_price(e, symbol="XAUUSD") / r)
    v = np.asarray(rows)
    if len(v) < 5:
        return {}
    w, l = v[v > 0], v[v <= 0]
    eq = np.cumsum(v)
    return dict(
        n=len(v), win=(v > 0).mean(), exp=v.mean(), total=v.sum(),
        t=v.mean() / (v.std(ddof=1) / np.sqrt(len(v))),
        rr=w.mean() / abs(l.mean()),
        maxdd_R=(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max(),
    )


def selftest():
    """Confirm the wrapper reproduces the validated trade set exactly."""
    path = os.path.join(THIS, "data", "XAUUSD", "1H",
                        "XAUUSD_1h_2021-03-02_2026-07-01.csv")
    c = load_csv(path)
    mine = generate_trades(c)
    ref = _s98.generate_trades(c, also_breakout=False)
    assert len(mine) == len(ref), f"trade count differs: {len(mine)} vs {len(ref)}"
    for a, b in zip(mine, ref):
        assert a["entry_time"] == b["entry_time"] and a["exit_time"] == b["exit_time"], "timing differs"
        assert abs(a["entry_price"] - b["entry_price"]) < 1e-9, "entry differs"
        assert abs(a["stop_loss"] - b["stop_loss"]) < 1e-9, "stop differs"
    assert all(t["setup"] == "liq_reclaim" for t in mine), "a non-reclaim setup leaked in"
    s = summarise(mine)
    print(f"selftest OK — {len(mine)} trades reproduce the validated engine exactly")
    print(f"  win {s['win']*100:.1f}% | RR {s['rr']:.2f} | net {s['exp']:+.3f}R/trade | "
          f"total {s['total']:+.1f}R | t {s['t']:+.2f} | maxDD {s['maxdd_R']:.1f}R")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Strategy 201: gold liquidity reclaim")
    ap.add_argument("--csv1h")
    ap.add_argument("--output", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if not a.csv1h:
        ap.error("--csv1h is required (or use --selftest)")
    candles = load_csv(a.csv1h)
    meta = parse_csv_filename(a.csv1h)
    trades = generate_trades(candles)
    out = a.output or f"strategy_201_results_{meta.get('symbol','XAUUSD')}.json"
    save_trades(trades, out)
    s = summarise(trades)
    print(f"{len(trades)} trades -> {out}")
    if s:
        print(f"  win {s['win']*100:.1f}% | RR {s['rr']:.2f} | net {s['exp']:+.3f}R/trade | "
              f"total {s['total']:+.1f}R | t {s['t']:+.2f}")


if __name__ == "__main__":
    main()
