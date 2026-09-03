#!/usr/bin/env python3
"""
Strategy 200 — full validation + INR capital simulation.

Sections
  1. Coverage
  2. Per-instrument results (net of Exness round-turn cost)
  3. Out-of-sample tests: post-2014 (params were fixed on EURUSD 1999-2014),
     instrument-level leave-one-out, direction split, cost stress
  4. Portfolio equity simulation in INR, event-driven, with real broker
     constraints (lot step, minimum lot, margin availability)

Sizing modes compared
  spec   : the literal brief — Rs 1,000 margin per trade at 1:2000
  riskN  : risk N% of current equity per trade (stop distance defines the lot)

Account types
  standard : 1 lot = 100,000 units (gold 100 oz), min volume 0.01 lot
  cent     : 1 lot = 1,000 units   (Exness Standard Cent), min volume 0.01 lot
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

THIS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS)

from strategy_200_fx_trend_breakout_atr import generate_trades, load_4h

FX = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"]
ALL = FX + ["XAUUSD"]
USD_BASE = {"USDJPY", "USDCHF", "USDCAD"}      # USD is the base currency
USDINR = 88.0                                  # stated assumption
LEVERAGE = 2000.0
START_INR = 10_000.0
rng = np.random.default_rng(11)


# --------------------------------------------------------------------------- io
def csv_for(sym: str) -> str:
    return os.path.join(THIS, "data", sym, "4h", f"{sym}_4h.csv")


def value_per_price_unit_usd(sym: str, lots: float, price: float, contract: float) -> float:
    """USD P&L per 1.0 of price movement, for `lots` lots."""
    if sym == "XAUUSD":
        return contract * lots
    if sym in USD_BASE:
        return contract * lots / price          # P&L accrues in the quote ccy
    return contract * lots


def notional_usd(sym: str, lots: float, price: float, contract: float) -> float:
    if sym == "XAUUSD":
        return contract * lots * price
    if sym in USD_BASE:
        return contract * lots                  # base is USD
    return contract * lots * price


# -------------------------------------------------------------------- statistics
def stat(v):
    v = np.asarray(v, float)
    if len(v) < 2:
        return None
    mu, sd = v.mean(), v.std(ddof=1)
    t = mu / (sd / np.sqrt(len(v))) if sd > 0 else 0.0
    w, l = v[v > 0], v[v <= 0]
    eq = np.cumsum(v)
    dd = (np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()
    return dict(n=len(v), exp=mu, t=t, win=(v > 0).mean(), total=v.sum(), dd=dd,
                rr=(w.mean() / abs(l.mean())) if len(w) and len(l) else np.nan,
                pf=(w.sum() / abs(l.sum())) if len(l) and l.sum() != 0 else np.nan)


def boot_p(v, n=20000):
    v = np.asarray(v, float)
    return float((rng.choice(v, size=(n, len(v)), replace=True).mean(axis=1) <= 0).mean())


# ------------------------------------------------------------------------ run
print("=" * 100)
print("STRATEGY 200 — 4H trend continuation (30-bar breakout + EMA300 bias, 3xATR stop, 6xATR trail)")
print("Parameters frozen on EURUSD 1999-2014 before any other data was examined.")
print("=" * 100)

trades_by: dict[str, list] = {}
df_by: dict[str, pd.DataFrame] = {}
print("\n1) COVERAGE")
for s in ALL:
    df = load_4h(csv_for(s))
    df_by[s] = df
    trades_by[s] = generate_trades(df, s)
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    print(f"   {s:8s} {len(df):6d} bars  {df.index[0].date()} -> {df.index[-1].date()}  "
          f"({yrs:5.1f}y)  {len(trades_by[s]):4d} trades")

print("\n2) PER-INSTRUMENT, NET OF EXNESS ROUND-TURN COST")
print(f"   {'sym':8s} {'n':>4s} {'win%':>6s} {'RR':>5s} {'PF':>5s} {'netExp':>8s} {'totalR':>8s} "
      f"{'t':>6s} {'maxDD_R':>8s} {'cost/R':>7s}")
for s in ALL:
    v = [t["pnl_R"] for t in trades_by[s]]
    st = stat(v)
    cr = np.mean([t["cost_R"] for t in trades_by[s]])
    print(f"   {s:8s} {st['n']:4d} {st['win']*100:5.1f}% {st['rr']:5.2f} {st['pf']:5.2f} "
          f"{st['exp']:+8.3f} {st['total']:+8.1f} {st['t']:+6.2f} {st['dd']:8.1f} {cr:7.4f}")

fx_trades = [t for s in FX for t in trades_by[s]]
all_trades = [t for s in ALL for t in trades_by[s]]
for label, grp in (("FX BASKET (7)", fx_trades), ("FX + GOLD (8)", all_trades)):
    st = stat([t["pnl_R"] for t in grp])
    print(f"   {'-'*92}")
    print(f"   {label:8s} {st['n']:4d} {st['win']*100:5.1f}% {st['rr']:5.2f} {st['pf']:5.2f} "
          f"{st['exp']:+8.3f} {st['total']:+8.1f} {st['t']:+6.2f} {st['dd']:8.1f}")

print("\n3) OUT-OF-SAMPLE / ROBUSTNESS")
dev = [t for t in trades_by["EURUSD"] if t["entry_time"] < "2015"]
oos_time = [t for t in trades_by["EURUSD"] if t["entry_time"] >= "2015"]
oos_inst = [t for s in FX if s != "EURUSD" for t in trades_by[s]]
for lbl, grp in (("DEV   EURUSD 1999-2014 (params fitted here)", dev),
                 ("OOS-A EURUSD 2015-2026 (unseen time)", oos_time),
                 ("OOS-B other 6 FX pairs, all dates (unseen instruments)", oos_inst)):
    st = stat([t["pnl_R"] for t in grp])
    print(f"   {lbl:52s} n={st['n']:4d} exp={st['exp']:+.3f}R t={st['t']:+.2f} "
          f"RR={st['rr']:.2f} P(exp<=0)={boot_p([t['pnl_R'] for t in grp])*100:5.1f}%")

print("\n   leave-one-instrument-out (FX basket):")
for s in FX:
    v = [t["pnl_R"] for q in FX if q != s for t in trades_by[q]]
    st = stat(v)
    print(f"     excl {s:8s} n={st['n']:4d} exp={st['exp']:+.3f} t={st['t']:+.2f}")

print("\n   direction split (FX basket):")
for d in ("long", "short"):
    v = [t["pnl_R"] for t in fx_trades if t["direction"] == d]
    st = stat(v)
    print(f"     {d:6s} n={st['n']:4d} win%={st['win']*100:5.1f} exp={st['exp']:+.3f} "
          f"t={st['t']:+.2f} RR={st['rr']:.2f}")

print("\n   by calendar year (FX basket):")
by_year = defaultdict(list)
for t in fx_trades:
    by_year[t["entry_time"][:4]].append(t["pnl_R"])
for y in sorted(by_year):
    v = np.array(by_year[y])
    print(f"     {y}  n={len(v):4d} win%={(v>0).mean()*100:5.1f} exp={v.mean():+.3f} total={v.sum():+7.1f}R")

print("\n   cost stress (FX basket) — multiples of the assumed Exness round-turn:")
for m in (1, 2, 3, 5, 10):
    v = [t["gross_R"] - m * t["cost_R"] for t in fx_trades]
    st = stat(v)
    print(f"     cost x{m:<3d} exp={st['exp']:+.3f}R t={st['t']:+.2f} total={st['total']:+7.1f}R")

print("\n   parameter sensitivity (FX basket, one axis at a time):")
for name, kw in (("k_trail", "k_trail"), ("k_init", "k_init"), ("entry_n", "entry_n")):
    vals = {"k_trail": [4.0, 5.0, 6.0, 8.0, 10.0],
            "k_init": [1.5, 2.0, 2.5, 3.0, 4.0],
            "entry_n": [20, 30, 45, 60, 120]}[name]
    line = []
    for val in vals:
        tr = [t for s in FX for t in generate_trades(df_by[s], s, **{kw: val})]
        st = stat([t["pnl_R"] for t in tr])
        line.append(f"{val}:{st['exp']:+.3f}(t{st['t']:+.1f})")
    print(f"     {name:8s} " + "  ".join(line))


# ----------------------------------------------------------- capital simulation
def simulate(instruments, mode, *, account="standard", risk_pct=1.0,
             margin_per_trade=1000.0, start=START_INR, max_concurrent=99,
             verbose=False):
    """Event-driven INR equity simulation with broker constraints."""
    scale = 1.0 if account == "standard" else 0.01
    min_lot, lot_step = 0.01, 0.01

    events = []
    for s in instruments:
        for t in trades_by[s]:
            events.append((pd.Timestamp(t["entry_time"]), 1, s, t))   # 1 = open
            events.append((pd.Timestamp(t["exit_time"]), 0, s, t))    # 0 = close
    events.sort(key=lambda x: (x[0], x[1]))                            # closes first

    equity = start
    used_margin = 0.0
    live: dict[int, dict] = {}
    curve = [(events[0][0], equity)]
    peak, maxdd = equity, 0.0
    taken = skipped_lot = skipped_margin = skipped_cap = 0
    wins = losses = 0
    pnls = []

    for ts, kind, sym, t in events:
        key = id(t)
        if kind == 1:
            if equity <= 0:
                continue
            price = t["entry_price"]
            contract = (100.0 if sym == "XAUUSD" else 100_000.0) * scale
            if mode == "spec":
                target_notional_usd = margin_per_trade * LEVERAGE / USDINR
                if sym == "XAUUSD":
                    lots = target_notional_usd / (contract * price)
                elif sym in USD_BASE:
                    lots = target_notional_usd / contract
                else:
                    lots = target_notional_usd / (contract * price)
            else:
                risk_inr = equity * risk_pct / 100.0
                vpu = value_per_price_unit_usd(sym, 1.0, price, contract)   # per 1 lot
                risk_per_lot_inr = t["risk_price"] * vpu * USDINR
                lots = risk_inr / risk_per_lot_inr if risk_per_lot_inr > 0 else 0.0
            lots = np.floor(lots / lot_step) * lot_step
            if lots < min_lot - 1e-12:
                skipped_lot += 1
                continue
            if len(live) >= max_concurrent:
                skipped_cap += 1
                continue
            need = notional_usd(sym, lots, price, contract) / LEVERAGE * USDINR
            if need > equity - used_margin:
                skipped_margin += 1
                continue
            used_margin += need
            live[key] = dict(lots=lots, margin=need, sym=sym, t=t)
            taken += 1
        else:
            p = live.pop(key, None)
            if p is None:
                continue
            used_margin -= p["margin"]
            contract = (100.0 if sym == "XAUUSD" else 100_000.0) * scale
            move = (t["exit_price"] - t["entry_price"]) if t["direction"] == "long" \
                else (t["entry_price"] - t["exit_price"])
            vpu = value_per_price_unit_usd(sym, p["lots"], t["exit_price"], contract)
            gross_inr = move * vpu * USDINR
            cost_inr = t["cost_R"] * t["risk_price"] * vpu * USDINR
            pnl = gross_inr - cost_inr
            equity += pnl
            pnls.append(pnl)
            wins += pnl > 0
            losses += pnl <= 0
            peak = max(peak, equity)
            maxdd = max(maxdd, (peak - equity) / peak if peak > 0 else 0.0)
            curve.append((ts, equity))
            if equity <= 0:
                equity = 0.0
                break
    return dict(final=equity, maxdd=maxdd, taken=taken, skipped_lot=skipped_lot,
                skipped_margin=skipped_margin, skipped_cap=skipped_cap,
                wins=wins, losses=losses, curve=curve, pnls=pnls,
                blown=equity <= 0.0)


print("\n" + "=" * 100)
print("4) CAPITAL SIMULATION — start Rs 10,000, USDINR 88, leverage 1:2000")
print("=" * 100)
span = (min(pd.Timestamp(t["entry_time"]) for t in fx_trades),
        max(pd.Timestamp(t["exit_time"]) for t in fx_trades))
print(f"   full FX span {span[0].date()} -> {span[1].date()}")

print("\n   (a) THE LITERAL BRIEF: Rs 1,000 margin per trade @ 1:2000, standard account")
for label, insts in (("7 FX pairs", FX), ("7 FX + gold", ALL)):
    r = simulate(insts, "spec")
    print(f"     {label:12s} trades taken {r['taken']:4d} | final Rs {r['final']:>12,.0f} | "
          f"maxDD {r['maxdd']*100:5.1f}% | {'ACCOUNT BLOWN' if r['blown'] else 'survived'}")

print("\n   (b) RISK-BASED SIZING, standard account (min lot 0.01 = 1,000 units)")
for rp in (0.5, 1.0, 2.0, 5.0):
    r = simulate(FX, "risk", account="standard", risk_pct=rp)
    print(f"     risk {rp:4.1f}%/trade: taken {r['taken']:4d} of {len(fx_trades)} "
          f"(skipped: lot<min {r['skipped_lot']}, margin {r['skipped_margin']}) | "
          f"final Rs {r['final']:>11,.0f} | maxDD {r['maxdd']*100:5.1f}%")

print("\n   (c) RISK-BASED SIZING, Exness Standard CENT account (1 lot = 1,000 units)")
print(f"     {'risk%':>6} {'taken':>6} {'skipped':>8} {'final Rs':>13} {'CAGR':>7} {'maxDD':>7} "
      f"{'win%':>6} {'profit Rs':>12}")
for rp in (0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0):
    r = simulate(FX, "risk", account="cent", risk_pct=rp)
    yrs = (span[1] - span[0]).days / 365.25
    cagr = ((r["final"] / START_INR) ** (1 / yrs) - 1) * 100 if r["final"] > 0 else -100
    wr = r["wins"] / max(1, r["wins"] + r["losses"]) * 100
    print(f"     {rp:6.1f} {r['taken']:6d} {r['skipped_lot']+r['skipped_margin']:8d} "
          f"{r['final']:13,.0f} {cagr:6.1f}% {r['maxdd']*100:6.1f}% {wr:5.1f}% "
          f"{r['final']-START_INR:12,.0f}")

print("\n   (d) HEADLINE RUN — cent account, 2% risk per trade, 7 FX pairs")
r = simulate(FX, "risk", account="cent", risk_pct=2.0)
yrs = (span[1] - span[0]).days / 365.25
pn = np.array(r["pnls"])
print(f"     start                Rs {START_INR:,.0f}")
print(f"     final                Rs {r['final']:,.0f}")
print(f"     net profit           Rs {r['final']-START_INR:,.0f}  ({(r['final']/START_INR-1)*100:+.0f}%)")
print(f"     period               {span[0].date()} -> {span[1].date()}  ({yrs:.1f} years)")
print(f"     CAGR                 {((r['final']/START_INR)**(1/yrs)-1)*100:.1f}%")
print(f"     trades taken         {r['taken']}  ({r['taken']/yrs:.0f}/yr)")
print(f"     win rate             {r['wins']/max(1,r['wins']+r['losses'])*100:.1f}%")
print(f"     avg win / avg loss   Rs {pn[pn>0].mean():,.0f} / Rs {pn[pn<=0].mean():,.0f}  "
      f"(RR {abs(pn[pn>0].mean()/pn[pn<=0].mean()):.2f})")
print(f"     max drawdown         {r['maxdd']*100:.1f}%")
print(f"     biggest single win   Rs {pn.max():,.0f}   biggest loss Rs {pn.min():,.0f}")

print("\n   (e) same run, but with a common-period restart (2021-03 onwards, all 7 pairs live)")
_orig = {s: trades_by[s] for s in ALL}
trades_by = {s: [t for t in _orig[s] if t["entry_time"] >= "2021-03"] for s in ALL}
r2 = simulate(FX, "risk", account="cent", risk_pct=2.0)
sp2 = (pd.Timestamp("2021-03-01", tz="UTC"), span[1])
y2 = (sp2[1] - sp2[0]).days / 365.25
print(f"     {sp2[0].date()} -> {sp2[1].date()} ({y2:.1f}y): final Rs {r2['final']:,.0f} "
      f"({(r2['final']/START_INR-1)*100:+.0f}%), CAGR {((r2['final']/START_INR)**(1/y2)-1)*100:.1f}%, "
      f"maxDD {r2['maxdd']*100:.1f}%, {r2['taken']} trades")
trades_by = _orig

print("\n   (f) Monte-Carlo on the headline run (20k reshuffles of the same trades, 2% risk)")
base = simulate(FX, "risk", account="cent", risk_pct=2.0)
pn_R = np.array([t["pnl_R"] for t in fx_trades])
finals, dds = [], []
for _ in range(20000):
    v = rng.permutation(pn_R)
    eq = START_INR
    peak, dd = eq, 0.0
    for x in v:
        eq *= (1 + 0.02 * x)
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    finals.append(eq)
    dds.append(dd)
finals, dds = np.array(finals), np.array(dds)
print(f"     final equity  p5 Rs {np.percentile(finals,5):,.0f} | median Rs {np.percentile(finals,50):,.0f} "
      f"| p95 Rs {np.percentile(finals,95):,.0f}")
print(f"     max drawdown  median {np.median(dds)*100:.0f}% | p95 {np.percentile(dds,95)*100:.0f}%")
print(f"     P(final < start) = {(finals < START_INR).mean()*100:.1f}%   "
      f"P(drawdown > 50%) = {(dds > 0.5).mean()*100:.1f}%")
