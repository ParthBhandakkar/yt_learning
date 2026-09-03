#!/usr/bin/env python3
"""
Strategy 300: Multi-Signal, Volatility-Targeted, Position-Netting Portfolio

A different ARCHITECTURE, not a different pattern. Nothing is reused from the
rest of this repository.

THE DIAGNOSIS THIS IS BUILT AROUND
----------------------------------
Roughly 390 discrete-trade hypotheses were tested across this dataset. Across
almost all of them the LONG variant lost less than the SHORT variant by about the
round-turn cost, which is the fingerprint of a small but real gross edge being
eaten by per-trade friction. Measured directly on the 4H FX trend system: gross
+0.032R per trade, cost 0.018R per trade. The signal was never the main problem.
The problem was paying a full spread for every discrete bet.

So stop placing bets. Hold a continuously-adjusted portfolio instead.

  1. MANY WEAK SIGNALS, NOT ONE STRONG ONE. Five causal signals per instrument at
     different horizons. Individually weak; averaging cuts signal noise without
     needing any one of them to be significant.
  2. CONTINUOUS POSITIONS, NETTED. The system holds a target position and only
     trades the CHANGE. A signal flip from +0.4 to +0.2 trades 0.2 units, not a
     full round turn. Cost is charged on notional actually traded.
  3. NO-TRADE BUFFER. Rebalance is skipped unless the gap between target and
     current exceeds a deadband. This is the single biggest turnover lever.
  4. VOLATILITY TARGETING. Positions are inverse-volatility scaled per instrument
     and the whole book is scaled to a constant ex-ante risk target, so no single
     instrument or regime dominates.
  5. DIVERSIFICATION AS THE EDGE. 8 instruments x 5 horizons = 40 weak bets. The
     Sharpe of the blend can exceed any component's.

CAUSALITY
---------
Every signal and every volatility estimate at date t uses closes up to and
including t. The resulting target position is applied to the return from t to
t+1. Cost is charged on the position change at t. There is no point at which a
future price influences a position.

Usage:
    python strategy_300_multisignal_portfolio.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))

FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
UNIVERSE = FX + ["XAUUSD"]

# round-turn cost in PRICE units, my own estimates for an Exness Standard account
COST_PRICE = {"EURUSD": 0.00012, "GBPUSD": 0.00012, "AUDUSD": 0.00012,
              "NZDUSD": 0.00012, "USDCHF": 0.00015, "USDCAD": 0.00015,
              "USDJPY": 0.030, "XAUUSD": 0.40}

# ---- FROZEN parameters -----------------------------------------------------
EWMA_PAIRS = [(8, 32), (16, 64), (32, 128), (64, 256)]  # classic geometric ladder
BREAKOUT_N = 60          # days, position-in-range signal
VOL_N = 60               # days, volatility estimate
FORECAST_CAP = 2.0       # cap each signal (and the blend) at +/- 2 units
TARGET_VOL = 0.15        # 15% annualised ex-ante portfolio volatility
BUFFER = 0.10            # no-trade band, as a fraction of the target position
MAX_LEVERAGE = 3.0       # cap total gross notional at 3x equity


def load_daily(sym: str) -> pd.DataFrame:
    p = os.path.join(THIS, "data", sym, "4h", f"{sym}_4h.csv")
    d = pd.read_csv(p)
    idx = pd.to_datetime(d["time"], unit="s", utc=True)
    df = pd.DataFrame({"open": d["open"].values, "high": d["high"].values,
                       "low": d["low"].values, "close": d["close"].values}, index=idx)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df.resample("1D").agg({"open": "first", "high": "max",
                                  "low": "min", "close": "last"}).dropna()


def build_panel(universe=UNIVERSE):
    px = {}
    for s in universe:
        px[s] = load_daily(s)["close"]
    return pd.DataFrame(px)


def signals(close: pd.Series) -> pd.DataFrame:
    """Five causal, volatility-normalised signals in forecast units."""
    ret = np.log(close).diff()
    vol = ret.ewm(span=VOL_N, adjust=False).std()
    out = {}
    for fast, slow in EWMA_PAIRS:
        raw = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
        # normalise by the price volatility over the slow horizon -> unitless
        denom = (close * vol * np.sqrt(slow)).replace(0, np.nan)
        f = raw / denom
        # scale so the typical magnitude is ~1, using only trailing information
        scale = f.abs().expanding(min_periods=250).median()
        out[f"ewmac_{fast}_{slow}"] = (f / scale.replace(0, np.nan)).clip(-FORECAST_CAP, FORECAST_CAP)
    hi = close.rolling(BREAKOUT_N).max()
    lo = close.rolling(BREAKOUT_N).min()
    pos_in_range = 2.0 * (close - lo) / (hi - lo).replace(0, np.nan) - 1.0   # -1..+1
    out[f"breakout_{BREAKOUT_N}"] = (pos_in_range * FORECAST_CAP).clip(-FORECAST_CAP, FORECAST_CAP)
    return pd.DataFrame(out)


def forecasts(panel: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({s: signals(panel[s]).mean(axis=1).clip(-FORECAST_CAP, FORECAST_CAP)
                         for s in panel.columns})


def simulate(panel: pd.DataFrame, fc: pd.DataFrame, *, target_vol=TARGET_VOL,
             buffer=BUFFER, cost_mult=1.0, max_leverage=MAX_LEVERAGE,
             start=1.0, trade=True):
    """Daily portfolio simulation. Returns a dict of series/metrics.

    position[t] is decided from information at t and earns the t -> t+1 return.
    Cost is charged on |position[t] - position[t-1]| at t.
    """
    ret = np.log(panel).diff()
    vol = ret.ewm(span=VOL_N, adjust=False).std()          # daily vol, trailing
    dates = panel.index
    syms = list(panel.columns)
    n_s = len(syms)

    # per-instrument target weight = forecast / vol, then scale the book to target_vol
    raw_w = (fc / (vol * np.sqrt(252))).replace([np.inf, -np.inf], np.nan)
    raw_w = raw_w.fillna(0.0)

    # ex-ante portfolio vol using a trailing correlation-free approximation plus
    # a trailing realised-vol correction (both causal)
    per_inst_vol = (raw_w.abs() * vol * np.sqrt(252))
    gross_est = per_inst_vol.sum(axis=1).replace(0, np.nan)
    # assume average pairwise correlation estimated on a trailing window
    corr_adj = pd.Series(index=dates, dtype=float)
    rr = ret.fillna(0.0)
    for i in range(len(dates)):
        if i < 250:
            corr_adj.iloc[i] = np.nan
            continue
        w = rr.iloc[i - 250:i]
        c = w.corr().values
        m = (c.sum() - n_s) / (n_s * (n_s - 1)) if n_s > 1 else 0.0
        corr_adj.iloc[i] = np.sqrt(max(1.0 / n_s + (1 - 1.0 / n_s) * max(m, 0.0), 1e-6))
    est_vol = (gross_est * corr_adj).replace(0, np.nan)
    scalar = (target_vol / est_vol).clip(upper=50.0)

    w_target = raw_w.mul(scalar, axis=0)
    gross = w_target.abs().sum(axis=1)
    over = (gross / max_leverage).clip(lower=1.0)
    w_target = w_target.div(over, axis=0).fillna(0.0)

    equity = [start]
    held = np.zeros(n_s)
    pos_hist, turn_hist, cost_hist = [], [], []
    cost_rel = np.array([COST_PRICE[s] for s in syms])
    px = panel.values

    for i in range(len(dates) - 1):
        tgt = w_target.iloc[i].values.copy()
        if not np.all(np.isfinite(tgt)):
            tgt = held.copy()
        if trade:
            # no-trade buffer: only move if the gap is material
            gap = tgt - held
            thresh = buffer * np.maximum(np.abs(tgt), 0.05)
            move = np.abs(gap) > thresh
            newpos = held.copy()
            newpos[move] = tgt[move]
        else:
            newpos = tgt
        traded = np.abs(newpos - held)
        # cost as a fraction of notional traded = round_turn_price / price / 2 per side
        c_frac = np.where(px[i] > 0, cost_rel / px[i], 0.0) / 2.0
        cost = float(np.sum(traded * c_frac) * cost_mult)
        held = newpos
        r = np.nan_to_num((px[i + 1] - px[i]) / px[i])
        pnl = float(np.sum(held * r))
        equity.append(equity[-1] * (1.0 + pnl - cost))
        pos_hist.append(held.copy())
        turn_hist.append(float(traded.sum()))
        cost_hist.append(cost)

    eq = pd.Series(equity[1:], index=dates[1:len(equity)])
    rets = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    peak = eq.cummax()
    dd = (peak - eq) / peak
    ann = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    return dict(
        equity=eq, rets=rets, ann_ret=ann,
        ann_vol=rets.std() * np.sqrt(252),
        sharpe=(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0,
        maxdd=dd.max(), years=yrs,
        turnover_ann=float(np.mean(turn_hist) * 252),
        cost_ann=float(np.mean(cost_hist) * 252),
        gross_ann=ann + float(np.mean(cost_hist) * 252),
        positions=pd.DataFrame(pos_hist, index=dates[:len(pos_hist)], columns=syms),
        tstat=(rets.mean() / (rets.std() / np.sqrt(len(rets)))) if rets.std() > 0 else 0.0,
    )


def report(name, r):
    print(f"  {name:44s} ann {r['ann_ret']*100:+7.2f}%  vol {r['ann_vol']*100:5.2f}%  "
          f"Sharpe {r['sharpe']:+5.2f}  t {r['tstat']:+5.2f}  maxDD {r['maxdd']*100:5.1f}%  "
          f"turn {r['turnover_ann']:5.1f}x  costdrag {r['cost_ann']*100:4.2f}%")


if __name__ == "__main__":
    print("=" * 122)
    print("STRATEGY 300 — multi-signal, vol-targeted, position-netting portfolio")
    print("=" * 122)
    panel = build_panel()
    print(f"  panel {panel.shape[1]} instruments x {len(panel)} daily bars, "
          f"{panel.index[0].date()} -> {panel.index[-1].date()}")
    fc = forecasts(panel)

    print("\n  A) full common panel (all 8 instruments have data from 2021-03)")
    sub = panel.dropna()
    r = simulate(sub, fc.reindex(sub.index))
    report("8-instrument portfolio", r)
    print(f"     equity {r['equity'].iloc[0]:.3f} -> {r['equity'].iloc[-1]:.3f} over {r['years']:.1f}y")

    print("\n  B) does the netting architecture actually cut costs?")
    for buf in (0.0, 0.05, 0.10, 0.20, 0.40):
        rr = simulate(sub, fc.reindex(sub.index), buffer=buf)
        report(f"no-trade buffer = {buf:.2f}", rr)

    print("\n  C) FX only vs gold only")
    for lbl, u in (("FX only (7)", FX), ("gold only", ["XAUUSD"])):
        p2 = panel[u].dropna()
        r2 = simulate(p2, forecasts(p2))
        report(lbl, r2)

    print("\n  D) EURUSD alone, 27 years — architecture check on unseen history")
    e = panel[["EURUSD"]].dropna()
    fe = forecasts(e)
    for lbl, lo, hi in (("1999-2014", "1999-01-01", "2015-01-01"),
                        ("2015-2026", "2015-01-01", "2027-01-01"),
                        ("full 1999-2026", "1999-01-01", "2027-01-01")):
        m = (e.index >= lo) & (e.index < hi)
        r3 = simulate(e[m], fe[m])
        report(f"EURUSD {lbl}", r3)

    print("\n  E) cost stress on the 8-instrument portfolio")
    for cm in (1, 2, 3, 5, 10):
        rr = simulate(sub, fc.reindex(sub.index), cost_mult=cm)
        report(f"cost x{cm}", rr)

    print("\n  F) target-vol sensitivity")
    for tv in (0.10, 0.15, 0.20, 0.30):
        rr = simulate(sub, fc.reindex(sub.index), target_vol=tv)
        report(f"target vol {tv*100:.0f}%", rr)
