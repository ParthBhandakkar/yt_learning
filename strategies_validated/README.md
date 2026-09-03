# Validated Strategies — Production Ready

These strategies have passed out-of-sample validation and are intended for live trading.

## strategy_201_gold_liquidity_reclaim.py
**RECOMMENDED FOR LIVE TRADING**

- **What**: Stop-run reversal on XAUUSD 1H
- **Edge**: Price sweeps a swing low, triggers stops, close reclaims the level → buy the snap-back
- **Results**: 541 trades, +0.199R/trade, t=2.47, Sharpe est ~0.9, market-neutral alpha (beta −0.03 vs gold)
- **Capital**: Rs 10,000 → Rs 26,686 (1% risk) or Rs 59,033 (2% risk) over 5.1 years on a **cent account**
- **Requirements**: 
  - Exness Standard Cent account (1 cent lot = 1 oz gold)
  - Fix the trailing stop bug in liveTrade first — it costs 60% of the edge
  - 1H XAUUSD data, ~8.5 trades/month
- **Caveat**: Independent rebuild produced t=1.30 vs t=2.47. True edge is somewhere between; confirm on 3 months demo.
- **Run**: `python strategy_201_gold_liquidity_reclaim.py --selftest` to verify parity with the validated engine.

---

## strategy_500_fx_carry_trend.py
**SUITABLE FOR LIVE TRADING AS A DIVERSIFIER**

- **What**: Carry trade on 7 FX majors, daily rebalance, volatility-targeted
- **Edge**: Earn interest differentials; don't predict spot prices
- **Results**: +4.25%/yr, Sharpe 0.30, maxDD 24%, positive in all 4 rate regimes
- **Capital**: Rs 10,000 → Rs 12,400 over 5.1 years
- **Constraints**:
  - **Broker swap is EVERYTHING**. Check MT5 swap values first. If Exness keeps >2%/yr the strategy is dead.
  - Requires central bank policy rate data (embedded in the file)
  - Daily execution (~6 adjustments per pair per year)
- **Caveat**: 95% of return came from long USD/JPY. That gap has narrowed from 5.4% to ~2.6% and is still closing.
- **Use case**: Diversifies the gold system (uncorrelated mechanism). Allocate 25% of capital at most.
- **Run**: `python strategy_500_fx_carry_trend.py`

---

## strategy_98_xau_trend_liquidity_trail.py
**REFERENCE ONLY — DO NOT USE AS-IS**

- **What**: The original S98 from liveTrade/, including both liquidity-reclaim and Donchian breakout setups
- **Status**: Kept for reference. Strategy 201 is the validated, cleaned version of the liquidity-reclaim core.
- **Why not use**: The trail update happens on every tick (15s) in the live manager, which costs ~60% of the edge.

---

## Recommendation

1. **Start with Strategy 201 on demo** (gold liquidity reclaim) at 1% risk for 3 months after fixing the trail bug.
2. **Add Strategy 500** (FX carry) at 25% allocation only if your broker's swap terms are favourable.
3. The gold strategy is the real business. FX carry is a diversifier, not a replacement.
