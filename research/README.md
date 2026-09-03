# Research — Concept Exploration

These files document the 400+ hypotheses tested and the findings that led to the validated strategies.

## research_fx_concept_search.py
Broad concept scan across 4 families:
- Daily breakout + trend (6 configs)
- 4H breakout + volatility-expansion filter
- Cross-sectional currency momentum (24 configs)
- Time-series momentum on the currency panel (12 configs)

**Result**: All failed OOS. DEV positive, OOS-A and OOS-B negative across the board.

**Key finding**: G10 FX majors on 4H/daily bars have no exploitable price-pattern edge with these concepts. The signal is smaller than the spread.

---

## research_fx_statarb.py
Avellaneda-Lee style stat arb: decompose returns into a common dollar factor + idiosyncratic residual, trade the residual mean-reversion.

**Result**: DEV t=1.25 at best, no plateau. Positive forward return when s-score > +2, but asymmetric and unstable.

---

## research_fx_anomaly_scan.py
Systematic scan of 39 calendar/intraday anomalies:
- Bar-of-day seasonality (6 UTC blocks)
- Day-of-week
- Turn-of-month
- Short-horizon reversal after >X×ATR moves
- Asian-range / London-NY session continuation

**Result**: 0 passes. Nearly every hypothesis showed both long and short losing ~1.5bp, which is exactly the round-turn cost. Gross signal ≈ 0.

**Key finding**: The per-trade cost structure kills everything at 4H/1H granularity. Any tradeable FX pattern is smaller than the spread.

---

## research2_intraday_scan.py
Clean-slate intraday research using tick volume and precise session structure, which earlier work ignored.

202 hypotheses:
- Hour-of-day effects (gold + GBP)
- Session opening-range breakouts (Asia/London)
- Tick-volume confirmation (high-participation moves)
- Day-of-week on gold

**Result**: 0 passes.

---

## research_gold_confirm.py
Independent confirmation tests for the gold edge:
- S200 frozen FX parameters applied to gold 4H and 1H
- Parameter surface checks (plateau vs spike)
- Direction split (long vs short)
- S98 setup decomposition (liquidity-reclaim vs Donchian)
- Two-engine portfolio correlation
- Cost stress

**Key finding**: Gold trend strategies are just gold beta (short side makes −0.004R). **Gold liquidity-reclaim works in both directions** (long +0.233R, short +0.150R) and is uncorrelated with gold's own moves (R² 0.000). That's real alpha.

---

## research_trend.py
Early trend-following exploration across multiple timeframes and configurations.

---

## Summary of the 400-hypothesis search

**FX majors (price patterns)**: Dead. Signal < spread across almost every concept family.

**Gold (liquidity sweep/reclaim)**: Only concept with market-neutral alpha (beta −0.03, t=2.23).

**FX (carry)**: The one thing that works, because it doesn't depend on predicting spot prices — you get paid for waiting. But it's weak (+4.25%/yr) and broker-dependent.
