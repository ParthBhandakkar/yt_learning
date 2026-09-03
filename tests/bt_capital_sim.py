#!/usr/bin/env python3
"""
Capital simulation: risk 10% of capital per trade.

Uses the VERIFIED chronological win/loss sequence from the target-vs-stop race
(fine data: XAUUSD=1m, FX=5m) and applies transaction cost.

Two money-management models:
  A) COMPOUNDING  10% of CURRENT equity per trade  -> equity *= (1 + 0.10*netR)
  B) FLAT         10% of INITIAL equity per trade  -> equity += 0.10*initial*netR

netR (net of cost, R = stop distance):
  win  netR = (target - cost) / stop
  loss netR = -(stop + cost) / stop

"Blown" definitions:
  - FLAT model: equity <= 0 (10 straight full losses = -100%).
  - COMPOUNDING: never hits 0; we report max drawdown and a 90%-DD ruin flag.
"""
import json, os, random
import bt_target_stop_race as R

HERE = os.path.dirname(os.path.abspath(__file__))
START = 10_000.0
RISK = 0.10

TYP_COST = {
    "EURUSD": 0.8, "GBPUSD": 1.0, "AUDUSD": 0.9, "NZDUSD": 1.2,
    "USDCAD": 1.2, "USDCHF": 1.2, "USDJPY": 0.9, "XAUUSD": 3.0,
}


def outcomes(pair, tgt, stop, trades, frames):
    """Return chronological list of (entry_time, netR)."""
    pip = R.PIP[pair]; cost = TYP_COST[pair]
    tpx, spx = tgt * pip, stop * pip
    win_r = (tgt - cost) / stop
    loss_r = -(stop + cost) / stop
    out = []
    for t in trades:
        res, _ = R.race_trade(frames, R._ts(t["entry_time"]),
                              float(t["entry_price"]), t["direction"], tpx, spx)
        if res == "open":
            continue
        r = win_r if res in ("win", "ambiguous") else loss_r
        out.append((t["entry_time"], r))
    return out


def longest_loss_streak(seq):
    m = c = 0
    for _, r in seq:
        if r < 0:
            c += 1; m = max(m, c)
        else:
            c = 0
    return m


def simulate(seq):
    """seq = list of netR in chronological order. Returns metrics for both models."""
    # Compounding
    eq = START; peak = START; maxdd = 0.0; mineq = START
    for _, r in seq:
        eq *= (1 + RISK * r)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak)
        mineq = min(mineq, eq)
    comp_final = eq
    # Flat (additive)
    feq = START; fpeak = START; fmaxdd = 0.0; blown = False; blow_i = None
    for i, (_, r) in enumerate(seq):
        feq += RISK * START * r
        if feq <= 0 and not blown:
            blown = True; blow_i = i + 1; feq = 0
        fpeak = max(fpeak, feq)
        fmaxdd = max(fmaxdd, (fpeak - feq) / fpeak if fpeak > 0 else 1)
        if blown:
            break
    return {
        "n": len(seq),
        "comp_final": comp_final,
        "comp_maxdd": maxdd * 100,
        "comp_mineq": mineq,
        "flat_final": feq,
        "flat_maxdd": fmaxdd * 100,
        "flat_blown": blown,
        "flat_blow_trade": blow_i,
        "streak": longest_loss_streak(seq),
    }


def montecarlo(seq, trials=20000):
    """Shuffle order; prob of >=50% compounding DD and prob of FLAT ruin."""
    rs = [r for _, r in seq]
    dd50 = 0; flat_ruin = 0
    for _ in range(trials):
        random.shuffle(rs)
        eq = START; peak = START; hit = False
        feq = START; fblown = False
        for r in rs:
            eq *= (1 + RISK * r)
            peak = max(peak, eq)
            if (peak - eq) / peak >= 0.50:
                hit = True
            feq += RISK * START * r
            if feq <= 0:
                fblown = True
        if hit:
            dd50 += 1
        if fblown:
            flat_ruin += 1
    return dd50 / trials * 100, flat_ruin / trials * 100


def report(name, seq):
    if not seq:
        print(f"\n### {name}: no trades"); return
    m = simulate(seq)
    dd50, ruin = montecarlo(seq)
    wr = sum(1 for _, r in seq if r > 0) / len(seq) * 100
    print(f"\n### {name}")
    print(f"  trades={m['n']}  winrate={wr:.1f}%  longest_loss_streak={m['streak']}")
    print(f"  COMPOUND 10%: final=${m['comp_final']:,.0f}  maxDD={m['comp_maxdd']:.1f}%  "
          f"minEquity=${m['comp_mineq']:,.0f}")
    print(f"  FLAT 10%    : final=${m['flat_final']:,.0f}  maxDD={m['flat_maxdd']:.1f}%  "
          f"blown={'YES @trade '+str(m['flat_blow_trade']) if m['flat_blown'] else 'no'}")
    print(f"  MonteCarlo  : P(>=50% DD, compounding)={dd50:.1f}%   P(flat ruin)={ruin:.2f}%")


def main():
    random.seed(42)
    s97 = json.load(open(os.path.join(HERE, "s97_all_pairs_trades.json")))
    s98 = json.load(open(os.path.join(HERE, "dashboard", "out",
          "s98_strategy_98_xau_trend_liquidity_trail_20260717_141427.json")))["trades"]

    print("=" * 78)
    print(f"CAPITAL SIMULATION — start ${START:,.0f}, risk {RISK*100:.0f}% per trade")
    print("Using verified race outcomes (XAUUSD=1m, FX=5m) net of typical cost")
    print("=" * 78)

    # Best gold engines
    xf = R.load_pair_frames("XAUUSD")
    report("XAUUSD s97 2x (200p / 650p)", outcomes("XAUUSD", 200, 650, s97["XAUUSD"], xf))
    report("XAUUSD s97 1x (100p / 619p)", outcomes("XAUUSD", 100, 619, s97["XAUUSD"], xf))
    report("XAUUSD s98 100p (100p / 387p)", outcomes("XAUUSD", 100, 387, s98, xf))
    report("XAUUSD s98 200p (200p / 507p)", outcomes("XAUUSD", 200, 507, s98, xf))

    # Cost-surviving FX 2x setups
    for pair, tg, st in [("EURUSD", 6, 19), ("GBPUSD", 7, 18),
                         ("USDCHF", 5, 13), ("USDJPY", 10, 21)]:
        f = R.load_pair_frames(pair)
        report(f"{pair} s97 2x ({tg}p / {st}p)", outcomes(pair, tg, st, s97[pair], f))

    # Combined chronological portfolio of the profitable-after-cost setups
    combo = []
    combo += outcomes("XAUUSD", 200, 650, s97["XAUUSD"], xf)
    for pair, tg, st in [("EURUSD", 6, 19), ("GBPUSD", 7, 18),
                         ("USDCHF", 5, 13), ("USDJPY", 10, 21)]:
        f = R.load_pair_frames(pair)
        combo += outcomes(pair, tg, st, s97[pair], f)
    combo.sort(key=lambda x: x[0])
    report("PORTFOLIO (gold s97-2x + EUR/GBP/CHF/JPY 2x), chronological", combo)


if __name__ == "__main__":
    main()
