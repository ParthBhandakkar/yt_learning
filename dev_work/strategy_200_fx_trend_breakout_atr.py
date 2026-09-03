#!/usr/bin/env python3
"""
Strategy 200: Volatility-Normalised Trend Continuation (4H, all instruments)

WHY THIS DESIGN
---------------
S97 (mean-reversion fade) failed because its edge was ~0.03R/trade — smaller
than the noise and barely bigger than the spread — and because it is negatively
skewed (65% win rate, average win 0.46R vs average loss 0.77R). Any strategy
whose per-trade edge is a fraction of the round-turn cost is a coin flip in
practice.

This strategy is built to invert both problems:

  1. POSITIVE SKEW BY CONSTRUCTION. The loss is capped by a fixed ATR stop; the
     win is uncapped and harvested by a wide chandelier trail. Reward:risk comes
     out around 2.5-3.5 with a ~30% win rate. That is the opposite profile to
     S97 and is what "positive RR" actually requires.
  2. COST IRRELEVANCE. The stop is 3.0 * ATR(84 x 4H) ~= 14 trading days of
     volatility, i.e. 60-90 pips on FX majors. An Exness round-turn of ~1.2 pips
     is ~1.5% of 1R, versus 20-40% of 1R for the s97 tight-target variants. The
     conclusion cannot flip on a spread assumption.
  3. ONE PARAMETER SET FOR EVERY INSTRUMENT. Every threshold is in ATR units, so
     nothing is per-pair tuned. The edge either generalises or it does not.

CONCEPT
-------
Time-series momentum / breakout continuation, the most-replicated cross-asset
anomaly in the literature. Trade in the direction of the intermediate trend when
price makes a new short-term extreme.

  ENTRY (evaluated on the CLOSE of 4H bar i):
      long  if close[i] > max(high[i-30 .. i-1])  and  close[i] > EMA300[i]
      short if close[i] < min(low [i-30 .. i-1])  and  close[i] < EMA300[i]
      -> filled at the OPEN of bar i+1
  RISK:
      1R = K_INIT * ATR(84)[i]      (stop placed at entry -/+ 1R)
  EXIT:
      chandelier trail = (extreme reached since entry) -/+ K_TRAIL * ATR(84)
      The trail is advanced only with data from CLOSED bars and is checked
      against the NEXT bar. No profit target, no time stop.

PARAMETER PROVENANCE (this is the part that matters)
----------------------------------------------------
All parameters were chosen on EURUSD 4H, 1999-01 -> 2014-12 ONLY. No other
instrument and no post-2014 data was looked at during selection. Selection was
made on plateau breadth and sub-period stability, NOT on peak performance:
  - K_TRAIL is positive for every value 4..12 on the dev set (3.0 is negative
    everywhere) -> 6.0 sits mid-plateau.
  - ENTRY_N is positive for every value 20..250 -> 30 chosen (short end of the
    plateau, highest trade count).
  - ATR_N is positive for every value 28..168 -> 84 chosen (14 trading days).
  - K_INIT 3.0 gave the lowest dev drawdown and was the only setting positive in
    BOTH dev halves (1999-2006 +0.099R, 2007-2014 +0.321R).
  - EMA_N barely matters (0/150/300/600 all similar) -> 300 (50 trading days).

Everything after 2014, and every instrument other than EURUSD, is therefore
genuine out-of-sample. Run bt_200_portfolio.py for the full report.

CAUSALITY
---------
- ATR/EMA/Donchian at bar i use only bars <= i (Donchian is explicitly shifted).
- Signal read on close of i, filled at open of i+1.
- While in a trade: the stop/trail is checked against bar i's high/low using the
  trail value that was already known at the open of bar i; only afterwards is
  the trail advanced with bar i's own extreme and ATR. So the trail can never be
  tightened using information from inside the bar that tests it.
- Costs are deducted per trade from core.round_turn_cost_price.

Usage:
    python strategy_200_fx_trend_breakout_atr.py --csv4h data/EURUSD/4h/EURUSD_4h.csv
"""
from __future__ import annotations

import argparse
import os
import sys

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)

import numpy as np
import pandas as pd

from core import parse_csv_filename, round_turn_cost_price, save_trades, to_iso

# ---- FROZEN parameters (selected on EURUSD 1999-2014 only) -----------------
ENTRY_N = 30      # 4H bars in the breakout lookback (~5 trading days)
EMA_N = 300       # trend filter (~50 trading days)
ATR_N = 84        # volatility unit (~14 trading days)
K_INIT = 3.0      # initial stop in ATRs -> defines 1R
K_TRAIL = 6.0     # chandelier trail width in ATRs


def load_4h(path: str) -> pd.DataFrame:
    d = pd.read_csv(path)
    if "time" in d.columns:
        idx = pd.to_datetime(d["time"], unit="s", utc=True)
    else:
        idx = pd.to_datetime(d["time_utc"], utc=True)
    df = pd.DataFrame(
        {"open": d["open"].values, "high": d["high"].values,
         "low": d["low"].values, "close": d["close"].values},
        index=idx,
    )
    return df[~df.index.duplicated(keep="first")].sort_index()


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().values


def _ema(c: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(c).ewm(span=n, adjust=False).mean().values


def generate_trades(
    df: pd.DataFrame,
    symbol: str,
    entry_n: int = ENTRY_N,
    ema_n: int = EMA_N,
    atr_n: int = ATR_N,
    k_init: float = K_INIT,
    k_trail: float = K_TRAIL,
    cost_mult: float = 1.0,
) -> list[dict]:
    """Causal backtest of one instrument. Returns trade dicts with pnl in R."""
    o, h, l, c = (df[x].values.astype(float) for x in ("open", "high", "low", "close"))
    n = len(c)
    if n < max(entry_n, ema_n, atr_n) + 20:
        return []

    a = _atr(h, l, c, atr_n)
    e = _ema(c, ema_n)
    # highest high / lowest low of the entry_n bars ENDING AT i-1 (shift(1) = no
    # peeking at the bar we are testing)
    hh = pd.Series(h).rolling(entry_n).max().shift(1).values
    ll = pd.Series(l).rolling(entry_n).min().shift(1).values

    trades: list[dict] = []
    i = max(entry_n, ema_n, atr_n) + 5
    while i < n - 1:
        if np.isnan(hh[i]) or a[i] <= 0:
            i += 1
            continue
        long_sig = c[i] > hh[i] and c[i] > e[i]
        short_sig = c[i] < ll[i] and c[i] < e[i]
        if not (long_sig or short_sig):
            i += 1
            continue

        pos = 1 if long_sig else -1
        ei = i + 1
        entry = o[ei]
        risk = k_init * a[i]
        if risk <= 0:
            i += 1
            continue
        init_stop = entry - risk if pos == 1 else entry + risk
        trail = init_stop
        extreme = entry
        mfe = 0.0

        j = ei
        exit_i = None
        exit_px = None
        while j < n:
            if pos == 1:
                if l[j] <= trail:                     # stop/trail hit first
                    exit_i, exit_px = j, trail
                    break
                extreme = max(extreme, h[j])
                mfe = max(mfe, (h[j] - entry) / risk)
                trail = max(trail, extreme - k_trail * a[j])   # applies from j+1
            else:
                if h[j] >= trail:
                    exit_i, exit_px = j, trail
                    break
                extreme = min(extreme, l[j])
                mfe = max(mfe, (entry - l[j]) / risk)
                trail = min(trail, extreme + k_trail * a[j])
            j += 1

        open_at_end = exit_i is None
        if open_at_end:                                # mark-to-market the tail
            exit_i, exit_px = n - 1, c[n - 1]

        gross_R = (exit_px - entry) / risk if pos == 1 else (entry - exit_px) / risk
        cost_price = round_turn_cost_price(entry, symbol=symbol) * cost_mult
        net_R = gross_R - cost_price / risk
        direction = "long" if pos == 1 else "short"
        trades.append({
            "trade_number": len(trades) + 1,
            "symbol": symbol,
            "direction": direction,
            "entry_time": to_iso(int(pd.Timestamp(df.index[ei]).timestamp())),
            "exit_time": to_iso(int(pd.Timestamp(df.index[exit_i]).timestamp())),
            "entry_idx": int(ei),
            "exit_idx": int(exit_i),
            "entry_price": float(entry),
            "stop_loss": float(init_stop),
            "take_profit": float(exit_px),
            "exit_price": float(exit_px),
            "risk_price": float(risk),
            "atr_at_entry": float(a[i]),
            "held_bars": int(exit_i - ei + 1),
            "mfe_R": round(float(mfe), 3),
            "pnl_R": round(float(net_R), 4),
            "gross_R": round(float(gross_R), 4),
            "cost_R": round(float(cost_price / risk), 4),
            "outcome": "win" if net_R > 0 else "loss",
            "still_open": bool(open_at_end),
            "reason": (f"Trend continuation: {entry_n}-bar breakout with EMA{ema_n} bias; "
                       f"stop {k_init}xATR({atr_n}) = 1R, chandelier trail {k_trail}xATR"),
        })
        i = exit_i + 1        # one position per instrument, no overlap
    return trades


def main():
    ap = argparse.ArgumentParser(description="Strategy 200: 4H trend continuation")
    ap.add_argument("--csv4h", required=True)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    df = load_4h(args.csv4h)
    meta = parse_csv_filename(args.csv4h)
    sym = args.symbol or meta.get("symbol", "FX")
    trades = generate_trades(df, sym)
    out = args.output or f"strategy_200_results_{sym}.json"
    save_trades(trades, out)
    v = np.array([t["pnl_R"] for t in trades]) if trades else np.array([0.0])
    wins, losses = v[v > 0], v[v <= 0]
    print(f"{sym}: {len(trades)} trades | win {100*(v>0).mean():.1f}% | "
          f"net {v.mean():+.3f}R/trade | total {v.sum():+.1f}R | "
          f"RR {abs(wins.mean()/losses.mean()) if len(wins) and len(losses) else float('nan'):.2f}")


if __name__ == "__main__":
    main()
