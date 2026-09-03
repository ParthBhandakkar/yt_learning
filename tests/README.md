# Tests & Validation Scripts

Backtest reports, capital simulations, and statistical tests for the validated strategies.

## bt_201_final.py
Full validation of Strategy 201 (gold liquidity reclaim):
- Comparison against the original S98 engine
- Time split (2021-03..2024-03 vs held-back period)
- Direction breakdown, year-by-year, hold-time distribution
- Cost & slippage stress tests
- Monte Carlo (20,000 reshuffles)
- INR capital simulation (Rs 10,000 at various risk levels, standard vs cent account)

**Run**: `python bt_201_final.py`

---

## bt_200_portfolio.py
Full validation + INR capital simulation for Strategy 200 (4H trend breakout).

**Result**: Failed OOS. DEV (EURUSD 1999-2014) +0.233R, OOS-A (EURUSD 2015+) −0.129R, OOS-B (other 6 pairs) −0.040R. FX basket at 2% risk turns Rs 10,000 → Rs 7,387.

**Archived** as an example of honest OOS failure.

---

## bt_200_capital_reality.py
Minimum viable capital analysis:
- What the smallest tradeable position (0.01 lot) already risks per pair
- Why Rs 10,000 cannot trade a standard Exness account (min lot too large)
- Why Rs 1,000 margin at 1:2000 is mathematically not survivable
- Cent account requirements

**Key finding**: Volatility-based stops on standard accounts require Rs 50,000+ capital to risk a sane 2% per trade. Cent accounts make Rs 10,000 viable.

---

## bt_300_alpha_test.py
The decisive test: regression against gold returns to separate alpha from beta.

**Results**:
- Strategy 300 (my multi-signal portfolio): beta 0.54 (t=28.9), R² 0.34, alpha t=1.51 → **rejected as disguised gold beta**
- Strategy 201 (stop-run reversal): beta −0.03, R² 0.000, alpha t=2.23, +84.9% during gold's 705 drawdown days → **real alpha**

**Benchmark**: Vol-targeted gold buy-and-hold beats almost everything (Sharpe 1.15).

---

## bt_400_final_numbers.py
Honest comparison of every candidate still standing:
- Passive vol-targeted gold: Rs 30,173
- Stop-run reversal (existing engine): Rs 26,686 at 1% risk
- Stop-run reversal (my rebuild): Rs 16,931 at 1% risk
- Strategy 300: Rs 26,717 (rejected — it's gold beta)
- FX majors (anything): Rs 379 – Rs 7,400 (dead)

**Key finding**: The gap between two implementations of the same concept (t=2.47 vs t=1.30) means the true edge is somewhere in that range and unproven at either end. Requires forward testing.

---

## Other test files
Various backtesting scripts for individual strategies, parameter grids, and capital simulations. File names are self-explanatory (e.g., `bt_s09.py`, `bt_aggregate.py`, etc.)
