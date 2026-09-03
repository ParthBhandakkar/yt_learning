# Strategy Repository Index

This folder contains the results of a comprehensive search across ~400 hypotheses on 5.3 years of FX + gold data. Two strategies passed validation; everything else is documented here so you know what was tested and why it failed.

---

## 📁 strategies_validated/ — **START HERE**

**For live trading.** Contains the two strategies that survived out-of-sample validation.

### Strategy 201: Gold Liquidity Reclaim ⭐ RECOMMENDED
- **File**: `strategy_201_gold_liquidity_reclaim.py`
- **What**: Stop-run reversal on XAUUSD 1H — price sweeps a swing low, close reclaims it, buy the snap-back
- **Results**: +0.199R/trade, t=2.47, beta −0.03 (market-neutral alpha)
- **Capital**: Rs 10,000 → Rs 26,686 (1% risk) or Rs 59,033 (2% risk) over 5.1 years
- **Requirements**: Exness Standard Cent account, fix the liveTrade trail bug first
- **Action**: Run on demo for 3 months (~25 trades) after fixing the bug

### Strategy 500: FX Carry + Trend
- **File**: `strategy_500_fx_carry_trend.py`
- **What**: Earn interest differentials on 7 FX majors, don't predict spot
- **Results**: +4.25%/yr, Sharpe 0.30, positive in all rate regimes
- **Capital**: Rs 10,000 → Rs 12,400 over 5.1 years
- **Constraints**: **Check broker swap first.** If Exness keeps >2%/yr, strategy is dead.
- **Use case**: Diversifier only. Allocate 25% of capital at most.

**See `strategies_validated/README.md` for full details.**

---

## 📁 research/ — Why FX patterns failed, why carry/gold worked

~400 hypotheses tested across:
- Price patterns (trend, breakout, mean-reversion, momentum)
- Residual stat arb
- Calendar & intraday anomalies
- Tick-volume confirmation

**Key findings**:
1. **FX majors have no exploitable 4H/daily price-pattern edge.** Tested 6 families, all failed OOS. The signal is smaller than the 1.2-pip spread.
2. **Gold liquidity-reclaim works because it's market-neutral** (beta −0.03, makes money when gold falls). Every trend-based gold system was just disguised beta.
3. **Carry works because you don't predict prices** — you get paid interest for holding a position. But it's weak and broker-dependent.

**See `research/README.md` for per-file details.**

---

## 📁 tests/ — Validation, capital sims, alpha vs beta tests

Backtest reports and statistical tests:
- `bt_201_final.py` — full validation of Strategy 201
- `bt_300_alpha_test.py` — regression against gold to separate alpha from beta (this is what killed Strategy 300)
- `bt_400_final_numbers.py` — honest comparison of every candidate
- `bt_200_capital_reality.py` — why Rs 10,000 cannot trade a standard account
- Unit tests for the existing liveTrade code

**See `tests/README.md`.**

---

## 📁 dev_work/ — Archived intermediate strategies

Three strategies that passed internal tests but failed final validation:
- **Strategy 200** (4H FX trend): frozen params on EURUSD 1999-2014, failed on every OOS slice
- **Strategy 300** (multi-signal portfolio): Sharpe 1.10, looked great, **rejected as gold beta** (R² 0.34)
- **Strategy 400** (my stop-run rebuild): t=1.30 vs the existing engine's t=2.47 — proved the edge is real but not yet established in size

**See `dev_work/README.md`.**

---

## Quick Decision Tree

**1. I want to trade gold:**
→ Use Strategy 201 (liquidity reclaim) after fixing the trail bug. Demo test first.

**2. I want to trade FX:**
→ Strategy 500 (carry) if your broker's swap is good. Otherwise there is no profitable FX price-pattern system in this dataset after 400 attempts.

**3. I want to understand why the other 398 ideas failed:**
→ Read `research/README.md` and the per-file summaries.

**4. I want to see how parameter selection should work:**
→ `dev_work/dev_200_param_sweep.py` — plateau selection, not peak hunting.

**5. I want to see how to test for alpha vs beta:**
→ `tests/bt_300_alpha_test.py` — regression against the benchmark, drawdown-period behaviour.

---

## The Honest Summary

Out of ~400 tested ideas:
- **1 has real alpha** (gold liquidity reclaim), magnitude uncertain (t somewhere between 1.3 and 2.5)
- **1 is a weak but real edge** (FX carry), entirely broker-dependent
- **398 failed** — and documenting that is as valuable as the two that passed, because it stops you from re-testing the same dead ends.

Passive vol-targeted gold (no strategy at all, just hold it with sensible sizing) beat almost everything I built, including systems that looked sophisticated. That's the baseline.

The gold strategy is the real business. FX carry is a diversifier if your broker terms allow it. Everything else in this folder is a lesson in what doesn't work and why.

---

## Files Not Moved

- `core.py`, `fast_core.py`, `config.py`, `logging_setup.py` — shared utilities, left in place
- `strategy_9X_*.py` — your earlier strategies (S91-S99 and others), untouched
- `liveTrade/` — the live execution system, separate concern
- `data/` — OHLC CSVs
- `run_*.py`, `batch_*.py` — batch runners for earlier strategies
- Analysis markdown files — left at root for reference
