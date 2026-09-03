#!/usr/bin/env python3
"""
Strategy 500: FX Carry + Trend, volatility-targeted, position-netting (7 majors)

WHY THIS EXISTS — A GAP IN ALL MY EARLIER WORK
----------------------------------------------
Every FX backtest I ran previously measured SPOT PRICE MOVEMENT ONLY. None of
them accrued swap / rollover. For a strategy that holds positions for days or
weeks that is not a rounding error - between 2021 and 2026 the US-Japan policy
rate gap reached ~5.5%, so a held long USDJPY position earned roughly 5% a year
in interest before the spot move. I was measuring half the return.

Carry - being long high-interest currencies and short low-interest ones - is the
oldest and most replicated return source in currency markets. It is also the one
thing in this dataset that does NOT depend on predicting price direction, which
is exactly why the ~400 price-pattern hypotheses I tested all failed: they were
all trying to forecast the unforecastable part.

THE STRATEGY
------------
  carry signal  : for pair BASE/QUOTE, carry = r_BASE - r_QUOTE (annualised %),
                  from central bank policy rates, cross-sectionally standardised
  trend overlay : EWMA(32/128) crossover, volatility-normalised
  combination   : forecast = 0.5*z(carry) + 0.5*z(trend)
                  Carry's known failure mode is the "carry crash" - high-yielders
                  collapse in risk-off episodes. The documented fix is a trend
                  overlay, which exits the position while it is falling instead
                  of holding it into the crash.
  sizing        : inverse-volatility per pair, whole book scaled to a constant
                  ex-ante volatility target, gross exposure capped
  execution     : continuous positions, only the CHANGE is traded, with a
                  no-trade buffer. Cost is charged on notional actually traded.
  P&L           : spot move + accrued carry - trading cost - broker swap markup

RATE DATA
---------
Policy rates are encoded as step functions from public central bank decisions.
They are approximate to about +/-0.25%, which is immaterial next to differentials
of 3-5%; a sensitivity test on rate error is included. Retail brokers do not pay
the full differential, so a symmetric annual markup is deducted on every open
position, on BOTH the long and short side, and stressed up to 2%/yr.

CAUSALITY
---------
Signals at date t use closes and rates known at t; the resulting position earns
the t -> t+1 return. Rates are stepped in on the announcement date, so no future
rate decision influences a position.

Usage:
    python strategy_500_fx_carry_trend.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
BASE_QUOTE = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"),
              "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"),
              "USDJPY": ("USD", "JPY"), "USDCHF": ("USD", "CHF"),
              "USDCAD": ("USD", "CAD")}
COST_PRICE = {"EURUSD": 0.00012, "GBPUSD": 0.00012, "AUDUSD": 0.00012,
              "NZDUSD": 0.00012, "USDCHF": 0.00015, "USDCAD": 0.00015,
              "USDJPY": 0.030}

# ---- policy rates: (effective date, rate %) --------------------------------
RATES = {
    "USD": [("2021-01-01", 0.125), ("2022-03-17", 0.375), ("2022-05-05", 0.875),
            ("2022-06-16", 1.625), ("2022-07-28", 2.375), ("2022-09-22", 3.125),
            ("2022-11-03", 3.875), ("2022-12-15", 4.375), ("2023-02-02", 4.625),
            ("2023-03-23", 4.875), ("2023-05-04", 5.125), ("2023-07-27", 5.375),
            ("2024-09-19", 4.875), ("2024-11-08", 4.625), ("2024-12-19", 4.375),
            ("2025-09-18", 4.125), ("2025-10-30", 3.875), ("2025-12-11", 3.625)],
    "EUR": [("2021-01-01", -0.50), ("2022-07-27", 0.00), ("2022-09-14", 0.75),
            ("2022-11-02", 1.50), ("2022-12-21", 2.00), ("2023-02-08", 2.50),
            ("2023-03-22", 3.00), ("2023-05-10", 3.25), ("2023-06-21", 3.50),
            ("2023-08-02", 3.75), ("2023-09-20", 4.00), ("2024-06-12", 3.75),
            ("2024-09-18", 3.50), ("2024-10-23", 3.25), ("2024-12-18", 3.00),
            ("2025-02-05", 2.75), ("2025-03-12", 2.50), ("2025-04-17", 2.25),
            ("2025-06-05", 2.00)],
    "GBP": [("2021-01-01", 0.10), ("2021-12-16", 0.25), ("2022-02-03", 0.50),
            ("2022-03-17", 0.75), ("2022-05-05", 1.00), ("2022-06-16", 1.25),
            ("2022-08-04", 1.75), ("2022-09-22", 2.25), ("2022-11-03", 3.00),
            ("2022-12-15", 3.50), ("2023-02-02", 4.00), ("2023-03-23", 4.25),
            ("2023-05-11", 4.50), ("2023-06-22", 5.00), ("2023-08-03", 5.25),
            ("2024-08-01", 5.00), ("2024-11-07", 4.75), ("2025-02-06", 4.50),
            ("2025-05-08", 4.25), ("2025-08-07", 4.00), ("2026-02-05", 3.75),
            ("2026-05-07", 3.50)],
    "JPY": [("2021-01-01", -0.10), ("2024-03-19", 0.00), ("2024-07-31", 0.25),
            ("2025-01-24", 0.50), ("2025-10-30", 0.75), ("2026-06-16", 1.00)],
    "CHF": [("2021-01-01", -0.75), ("2022-06-16", -0.25), ("2022-09-22", 0.50),
            ("2022-12-15", 1.00), ("2023-03-23", 1.50), ("2023-06-22", 1.75),
            ("2024-03-21", 1.50), ("2024-06-20", 1.25), ("2024-09-26", 1.00),
            ("2024-12-12", 0.50), ("2025-03-20", 0.25), ("2025-06-19", 0.00)],
    "AUD": [("2021-01-01", 0.10), ("2022-05-03", 0.35), ("2022-06-07", 0.85),
            ("2022-07-05", 1.35), ("2022-08-02", 1.85), ("2022-09-06", 2.35),
            ("2022-10-04", 2.60), ("2022-11-01", 2.85), ("2022-12-06", 3.10),
            ("2023-02-07", 3.35), ("2023-03-07", 3.60), ("2023-05-02", 3.85),
            ("2023-06-06", 4.10), ("2023-11-07", 4.35), ("2025-02-18", 4.10),
            ("2025-05-20", 3.85), ("2025-08-12", 3.60), ("2026-07-07", 3.85)],
    "NZD": [("2021-01-01", 0.25), ("2021-10-06", 0.50), ("2021-11-24", 0.75),
            ("2022-02-23", 1.00), ("2022-04-13", 1.50), ("2022-05-25", 2.00),
            ("2022-07-13", 2.50), ("2022-08-17", 3.00), ("2022-10-05", 3.50),
            ("2022-11-23", 4.25), ("2023-02-22", 4.75), ("2023-04-05", 5.25),
            ("2023-05-24", 5.50), ("2024-08-14", 5.25), ("2024-10-09", 4.75),
            ("2024-11-27", 4.25), ("2025-02-19", 3.75), ("2025-04-09", 3.50),
            ("2025-05-28", 3.25), ("2025-08-20", 3.00), ("2025-11-26", 2.50)],
    "CAD": [("2021-01-01", 0.25), ("2022-03-02", 0.50), ("2022-04-13", 1.00),
            ("2022-06-01", 1.50), ("2022-07-13", 2.50), ("2022-09-07", 3.25),
            ("2022-10-26", 3.75), ("2022-12-07", 4.25), ("2023-01-25", 4.50),
            ("2023-06-07", 4.75), ("2023-07-12", 5.00), ("2024-06-05", 4.75),
            ("2024-07-24", 4.50), ("2024-09-04", 4.25), ("2024-10-23", 3.75),
            ("2024-12-11", 3.25), ("2025-01-29", 3.00), ("2025-03-12", 2.75),
            ("2025-09-17", 2.50), ("2026-03-11", 2.25)],
}

# ---- FROZEN parameters -----------------------------------------------------
EWMA_FAST, EWMA_SLOW = 32, 128
VOL_N = 60
W_CARRY, W_TREND = 0.5, 0.5
TARGET_VOL = 0.15
BUFFER = 0.15
MAX_GROSS = 3.0
SWAP_MARKUP = 0.010          # 1.0%/yr broker haircut, charged on any open position


def load_daily(sym: str) -> pd.Series:
    p = os.path.join(THIS, "data", sym, "4h", f"{sym}_4h.csv")
    d = pd.read_csv(p)
    idx = pd.to_datetime(d["time"], unit="s", utc=True)
    s = pd.Series(d["close"].values, index=idx)
    s = s[~s.index.duplicated(keep="first")].sort_index()
    return s.resample("1D").last().dropna()


def rate_series(ccy: str, index: pd.DatetimeIndex, shift_pct: float = 0.0) -> pd.Series:
    steps = pd.Series({pd.Timestamp(d, tz="UTC"): r for d, r in RATES[ccy]}).sort_index()
    return steps.reindex(steps.index.union(index)).ffill().reindex(index) + shift_pct


def build(rate_err: dict | None = None):
    px = pd.DataFrame({s: load_daily(s) for s in PAIRS}).dropna()
    idx = px.index
    err = rate_err or {}
    r = {c: rate_series(c, idx, err.get(c, 0.0)) for c in RATES}
    carry = pd.DataFrame({s: (r[BASE_QUOTE[s][0]] - r[BASE_QUOTE[s][1]]) / 100.0
                          for s in PAIRS}, index=idx)
    return px, carry


def forecasts(px: pd.DataFrame, carry: pd.DataFrame, w_carry=W_CARRY, w_trend=W_TREND):
    ret = np.log(px).diff()
    vol = ret.ewm(span=VOL_N, adjust=False).std()
    # carry, standardised cross-sectionally each day (dollar-neutral-ish signal)
    zc = carry.sub(carry.mean(axis=1), axis=0)
    zc = zc.div(zc.std(axis=1).replace(0, np.nan), axis=0)
    # trend, volatility-normalised then scaled to comparable units
    raw = px.ewm(span=EWMA_FAST, adjust=False).mean() - px.ewm(span=EWMA_SLOW, adjust=False).mean()
    tr = raw / (px * vol * np.sqrt(EWMA_SLOW)).replace(0, np.nan)
    tr = tr / tr.abs().expanding(min_periods=250).median().replace(0, np.nan)
    return (w_carry * zc.clip(-2, 2) + w_trend * tr.clip(-2, 2)).clip(-2, 2), vol


def simulate(px, carry, fc, vol, *, target_vol=TARGET_VOL, buffer=BUFFER,
             cost_mult=1.0, swap_markup=SWAP_MARKUP, include_carry=True,
             max_gross=MAX_GROSS, start=1.0):
    syms = list(px.columns)
    n_s = len(syms)
    ann_vol = (vol * np.sqrt(252)).replace(0, np.nan)
    raw_w = (fc / ann_vol).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    est = (raw_w.abs() * ann_vol).sum(axis=1) * np.sqrt(1.0 / n_s + 0.35)
    scalar = (target_vol / est.replace(0, np.nan)).clip(upper=50.0)
    w = raw_w.mul(scalar, axis=0)
    gross = w.abs().sum(axis=1)
    w = w.div((gross / max_gross).clip(lower=1.0), axis=0).fillna(0.0)

    P = px.values
    C = carry.values
    W = w.values
    cost_unit = np.array([COST_PRICE[s] for s in syms])
    eq = [start]
    held = np.zeros(n_s)
    turn, carry_pnl_hist, spot_pnl_hist = [], [], []
    for i in range(len(px) - 1):
        tgt = W[i].copy()
        if not np.all(np.isfinite(tgt)):
            tgt = held.copy()
        gap = tgt - held
        move = np.abs(gap) > buffer * np.maximum(np.abs(tgt), 0.05)
        newpos = held.copy()
        newpos[move] = tgt[move]
        traded = np.abs(newpos - held)
        c_frac = np.where(P[i] > 0, cost_unit / P[i], 0.0) / 2.0
        cost = float(np.sum(traded * c_frac) * cost_mult)
        held = newpos
        spot = float(np.sum(held * (P[i + 1] - P[i]) / P[i]))
        cr = 0.0
        if include_carry:
            cr = float(np.sum(held * C[i]) / 252.0
                       - swap_markup / 252.0 * np.sum(np.abs(held)))
        eq.append(eq[-1] * (1.0 + spot + cr - cost))
        turn.append(float(traded.sum()))
        carry_pnl_hist.append(cr)
        spot_pnl_hist.append(spot)

    e = pd.Series(eq[1:], index=px.index[1:len(eq)])
    r = e.pct_change().dropna()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    dd = ((e.cummax() - e) / e.cummax()).max()
    return dict(equity=e, rets=r, years=yrs,
                ann=(e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1,
                vol=r.std() * np.sqrt(252),
                sharpe=r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0,
                tstat=r.mean() / (r.std() / np.sqrt(len(r))) if r.std() > 0 else 0.0,
                maxdd=dd, turn=float(np.mean(turn) * 252),
                carry_ann=float(np.mean(carry_pnl_hist) * 252),
                spot_ann=float(np.mean(spot_pnl_hist) * 252),
                positions=w)


def show(lbl, r):
    print(f"  {lbl:46s} ann {r['ann']*100:+7.2f}%  vol {r['vol']*100:5.2f}%  "
          f"Sharpe {r['sharpe']:+5.2f}  t {r['tstat']:+5.2f}  DD {r['maxdd']*100:5.1f}%  "
          f"turn {r['turn']:5.1f}x")


if __name__ == "__main__":
    px, carry = build()
    print("=" * 124)
    print("STRATEGY 500 — FX carry + trend, 7 majors, daily, netted positions, swap accrued")
    print("=" * 124)
    print(f"  {len(px)} daily bars, {px.index[0].date()} -> {px.index[-1].date()} "
          f"({(px.index[-1]-px.index[0]).days/365.25:.1f}y)")
    print("\n  average annualised carry available per pair (long the pair as quoted):")
    for s in PAIRS:
        print(f"    {s:8s} mean {carry[s].mean()*100:+6.2f}%   "
              f"range {carry[s].min()*100:+6.2f}% .. {carry[s].max()*100:+6.2f}%")

    fc, vol = forecasts(px, carry)
    print("\n  A) the decomposition — how much comes from carry vs spot?")
    base = simulate(px, carry, fc, vol)
    show("carry + trend (full)", base)
    print(f"     of which: spot {base['spot_ann']*100:+.2f}%/yr, "
          f"carry {base['carry_ann']*100:+.2f}%/yr")
    show("SAME positions, swap income switched OFF", simulate(px, carry, fc, vol, include_carry=False))

    print("\n  B) which signal is doing the work?")
    for lbl, wc, wt in (("carry only", 1.0, 0.0), ("trend only", 0.0, 1.0),
                        ("50/50", 0.5, 0.5), ("70/30 carry-heavy", 0.7, 0.3)):
        f2, v2 = forecasts(px, carry, wc, wt)
        show(lbl, simulate(px, carry, f2, v2))

    print("\n  C) robustness")
    for m in (1, 2, 3, 5):
        show(f"trading cost x{m}", simulate(px, carry, fc, vol, cost_mult=m))
    for mk in (0.0, 0.01, 0.02, 0.03):
        show(f"broker swap markup {mk*100:.1f}%/yr", simulate(px, carry, fc, vol, swap_markup=mk))
    rng = np.random.default_rng(9)
    errs = []
    for _ in range(20):
        e = {c: rng.uniform(-0.4, 0.4) for c in RATES}
        px2, c2 = build(rate_err=e)
        f3, v3 = forecasts(px2, c2)
        errs.append(simulate(px2, c2, f3, v3)["sharpe"])
    print(f"  rate table error +/-0.4% per currency, 20 draws: Sharpe "
          f"min {min(errs):+.2f} median {np.median(errs):+.2f} max {max(errs):+.2f}")

    print("\n  D) time split")
    for lbl, lo, hi in (("2021-03 .. 2023-01", "2021-01-01", "2023-01-01"),
                        ("2023-01 .. 2024-09", "2023-01-01", "2024-09-01"),
                        ("2024-09 .. 2026-07", "2024-09-01", "2027-01-01")):
        m = (px.index >= lo) & (px.index < hi)
        show(lbl, simulate(px[m], carry[m], fc[m], vol[m]))

    print("\n  E) sanity floor — what do naive alternatives give?")
    show("always long the top-carry pair only",
         simulate(px, carry, (carry.rank(axis=1, pct=True) > 0.85).astype(float) * 2 - 0.0, vol))
    eqw = pd.DataFrame(0.0, index=px.index, columns=PAIRS)
    show("flat (no positions)", simulate(px, carry, eqw, vol))


# ---------------------------------------------------------------------------
# RAW carry variant.
#
# The cross-sectional standardisation above subtracts the average carry across
# the 7 pairs each day. Between 2021 and 2026 nearly all the available carry sat
# on the USD side (Fed at 5.4% vs BoJ at -0.1%), so demeaning deleted the very
# thing being tested. This variant sizes directly off the raw differential, which
# is the actual carry trade: long the positive-carry pairs, short the negative.
# ---------------------------------------------------------------------------

def raw_carry_forecast(px, carry, cap=2.0, scale_pct=2.0, trend_gate=False,
                       w_trend=0.0):
    """forecast = carry / scale, optionally gated or blended with trend."""
    vol = np.log(px).diff().ewm(span=VOL_N, adjust=False).std()
    f = (carry * 100.0 / scale_pct).clip(-cap, cap)
    if trend_gate or w_trend > 0:
        raw = px.ewm(span=EWMA_FAST, adjust=False).mean() - px.ewm(span=EWMA_SLOW, adjust=False).mean()
        tr = raw / (px * vol * np.sqrt(EWMA_SLOW)).replace(0, np.nan)
        tr = (tr / tr.abs().expanding(min_periods=250).median().replace(0, np.nan)).clip(-cap, cap)
        if trend_gate:
            # hold the carry position only while price is not moving against it
            f = f.where(np.sign(f) == np.sign(tr), 0.0)
        else:
            f = ((1 - w_trend) * f + w_trend * tr).clip(-cap, cap)
    return f, vol


if __name__ == "__main__":
    print("\n" + "=" * 124)
    print("F) RAW CARRY (no cross-sectional demeaning) — the actual carry trade")
    print("=" * 124)
    for lbl, kw in (("raw carry, sized by differential", dict()),
                    ("raw carry + trend gate", dict(trend_gate=True)),
                    ("raw carry 70 / trend 30", dict(w_trend=0.3)),
                    ("raw carry 50 / trend 50", dict(w_trend=0.5))):
        f, v = raw_carry_forecast(px, carry, **kw)
        show(lbl, simulate(px, carry, f, v))

    print("\n  raw carry, time split (is it one regime or a repeatable edge?)")
    f, v = raw_carry_forecast(px, carry)
    for lbl, lo, hi in (("2021-03 .. 2022-06 (pre-hikes)", "2021-01-01", "2022-06-01"),
                        ("2022-06 .. 2023-07 (hiking)", "2022-06-01", "2023-07-01"),
                        ("2023-07 .. 2024-09 (plateau)", "2023-07-01", "2024-09-01"),
                        ("2024-09 .. 2026-07 (cutting)", "2024-09-01", "2027-01-01")):
        m = (px.index >= lo) & (px.index < hi)
        if m.sum() < 120:
            continue
        show(lbl, simulate(px[m], carry[m], f[m], v[m]))

    print("\n  raw carry, per-pair contribution (full period, spot + carry, net)")
    r = simulate(px, carry, f, v)
    w = r["positions"]
    ret = px.pct_change().shift(-1)
    for s in PAIRS:
        spot_c = (w[s] * ret[s]).sum()
        carry_c = (w[s] * carry[s] / 252.0).sum()
        print(f"    {s:8s} avg pos {w[s].mean():+5.2f}  spot {spot_c*100:+7.1f}%  "
              f"carry {carry_c*100:+6.1f}%  total {(spot_c+carry_c)*100:+7.1f}%")

    print("\n  raw carry, robustness")
    for mk in (0.0, 0.01, 0.02):
        show(f"swap markup {mk*100:.1f}%/yr", simulate(px, carry, f, v, swap_markup=mk))
    for m in (1, 3, 5):
        show(f"trading cost x{m}", simulate(px, carry, f, v, cost_mult=m))


# ---------------------------------------------------------------------------
# G) CARRY THRESHOLD.
#
# Raw carry above is diluted: GBPUSD, NZDUSD and USDCAD averaged |carry| below
# 0.6%/yr, which cannot cover a 1%/yr broker swap markup, so holding them is a
# coin-flip on spot with a guaranteed cost. The ex-ante rule (decided on
# reasoning, not on results) is: only hold a pair when the differential exceeds
# the markup by a margin. This is the same logic as the minimum-risk floor in a
# stop strategy - never take a bet whose expected return is below its cost.
# ---------------------------------------------------------------------------

def carry_threshold_forecast(px, carry, thresh_pct=1.5, cap=2.0, scale_pct=2.0):
    vol = np.log(px).diff().ewm(span=VOL_N, adjust=False).std()
    f = (carry * 100.0 / scale_pct).clip(-cap, cap)
    return f.where(carry.abs() * 100.0 >= thresh_pct, 0.0), vol


if __name__ == "__main__":
    print("\n" + "=" * 124)
    print("G) CARRY WITH A MINIMUM-DIFFERENTIAL THRESHOLD")
    print("=" * 124)
    for th in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        f, v = carry_threshold_forecast(px, carry, th)
        r = simulate(px, carry, f, v)
        active = (f.abs() > 0).sum(axis=1).mean()
        print(f"  threshold {th:4.1f}%/yr  avg pairs held {active:4.2f}  "
              f"ann {r['ann']*100:+7.2f}%  vol {r['vol']*100:5.2f}%  Sharpe {r['sharpe']:+5.2f}  "
              f"t {r['tstat']:+5.2f}  DD {r['maxdd']*100:5.1f}%  turn {r['turn']:4.1f}x")

    print("\n  chosen configuration: threshold 1.5%/yr")
    f, v = carry_threshold_forecast(px, carry, 1.5)
    base = simulate(px, carry, f, v)
    show("carry >= 1.5%, markup 1.0%/yr", base)
    print("\n  time split")
    for lbl, lo, hi in (("2021-03 .. 2022-06 (pre-hikes)", "2021-01-01", "2022-06-01"),
                        ("2022-06 .. 2023-07 (hiking)", "2022-06-01", "2023-07-01"),
                        ("2023-07 .. 2024-09 (plateau)", "2023-07-01", "2024-09-01"),
                        ("2024-09 .. 2026-07 (cutting)", "2024-09-01", "2027-01-01")):
        m = (px.index >= lo) & (px.index < hi)
        if m.sum() < 120:
            continue
        show(lbl, simulate(px[m], carry[m], f[m], v[m]))

    print("\n  robustness")
    for mk in (0.0, 0.005, 0.01, 0.015, 0.02, 0.03):
        show(f"broker swap markup {mk*100:.1f}%/yr", simulate(px, carry, f, v, swap_markup=mk))
    for m in (1, 3, 5, 10):
        show(f"trading cost x{m}", simulate(px, carry, f, v, cost_mult=m))
    rng = np.random.default_rng(31)
    sh = []
    for _ in range(20):
        e = {c: rng.uniform(-0.4, 0.4) for c in RATES}
        px2, c2 = build(rate_err=e)
        f2, v2 = carry_threshold_forecast(px2, c2, 1.5)
        sh.append(simulate(px2, c2, f2, v2)["sharpe"])
    print(f"  rate-table error +/-0.4%/ccy, 20 draws: Sharpe min {min(sh):+.2f} "
          f"median {np.median(sh):+.2f} max {max(sh):+.2f}")

    print("\n  per-pair contribution")
    w = base["positions"]
    ret = px.pct_change().shift(-1)
    for s in PAIRS:
        sc = (w[s] * ret[s]).sum()
        cc = (w[s] * carry[s] / 252.0).sum()
        days = (w[s].abs() > 0).mean()
        print(f"    {s:8s} held {days*100:3.0f}% of days  avg pos {w[s].mean():+5.2f}  "
              f"spot {sc*100:+7.1f}%  carry {cc*100:+6.1f}%  total {(sc+cc)*100:+7.1f}%")

    print("\n  RS 10,000 AT THIS CONFIGURATION")
    e = base["equity"]
    print(f"    Rs 10,000 -> Rs {10000*e.iloc[-1]/e.iloc[0]:,.0f} over {base['years']:.1f}y "
          f"(CAGR {base['ann']*100:.1f}%, maxDD {base['maxdd']*100:.0f}%)")
    print(f"    turnover {base['turn']:.1f}x/yr -> roughly "
          f"{base['turn']*100/2:.0f} position adjustments per year across the book")
    print(f"    benchmark: gold vol-targeted buy-and-hold was Rs 30,173, Sharpe 1.15")
