# Strategy 98 — Drawdown Analysis

## Setup
- **Instrument**: XAUUSD
- **Leverage**: 2000x
- **Pip definition**: 100 pips = $1.00 price movement (1 pip = $0.01)
- **Backtest trades**: 982 trades from s98 ATR-trailed strategy
- **Methodology**: For each trade, compute actual adverse price movement (from 1H candle data) that occurred *before* price first reached the target in the favorable direction. Find the stop distance that lets >60% of trades survive to reach target.

---

## Case 1: Target = 100 pips ($1.00 profit)

| Metric | Value |
|--------|-------|
| Trades reaching target | **975 / 982 (99.3%)** |
| **Minimum stop for >60% survival** | **387 pips ($3.87)** |
| Survival at that stop | 587 / 975 (60.2%) |
| Median adverse before target | 280.7 pips ($2.81) |
| 25th–75th percentile adverse | 119.1 – 658.2 pips |

### Verdict — 100 pip target
> **A stop of ~$3.87 (387 pips) is the minimum required for 60% of trades to survive to a $1 target.** The adverse movement before reaching even this modest target is substantial (median $2.81). A stop tighter than ~$4 would stop out more than 40% of trades before they reach $1 in profit. However, even with an infinitely wide stop, only 39.9% of qualifying trades are *actual* winners in the backtest — the ATR trail can still exit at a loss after hitting the target.

---

## Case 2: Target = 200 pips ($2.00 profit)

| Metric | Value |
|--------|-------|
| Trades reaching target | **974 / 982 (99.2%)** |
| **Minimum stop for >60% survival** | **507 pips ($5.07)** |
| Survival at that stop | 585 / 974 (60.1%) |
| Median adverse before target | 348.4 pips ($3.48) |
| 25th–75th percentile adverse | 131.6 – 928.6 pips |

### Verdict — 200 pip target
> **A stop of ~$5.07 (507 pips) is the minimum required for 60% survival to a $2 target.** The marginal increase in stop from the 100-pip case ($3.87 → $5.07) is modest relative to the doubling of the target. The number of qualifying trades barely changes (975 → 974), indicating the strategy's entries consistently produce large favorable swings.

---

## Cross-Case Summary

| | Target 100 pips ($1) | Target 200 pips ($2) |
|---|---|---|
| Qualifying trades | 975 / 982 | 974 / 982 |
| Min stop for >60% | **387 pips ($3.87)** | **507 pips ($5.07)** |
| Stop-to-target ratio | **3.87x** | **2.54x** |
| Median adverse | 280.7 pips | 348.4 pips |

## Key Takeaway
- **Stop-to-target ratio improves at higher targets**: 3.87x for 100 pips vs 2.54x for 200 pips — meaning the extra adverse movement to reach 200 pips is proportionally smaller than what's needed to reach 100 pips.
- The strategy's entries are excellent (99%+ hit both targets) but the adverse movement before reaching target is consistently 2.5–3.9x the target size, requiring wide stops.
- A static stop alone is insufficient — you need the ATR trailing mechanism (or equivalent) to lock in profits after the target is reached, since even surviving trades can reverse to a loss.
