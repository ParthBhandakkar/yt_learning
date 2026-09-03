# Simple Verdict — Your Live Trading Strategies

**Date**: August 5, 2026

---

## Strategy 98 (Gold) — YOUR MAIN STRATEGY

### ✅ IS IT PROFITABLE?
**YES** — but only if you fix a critical bug first.

### 📊 THE NUMBERS
- After costs: +162.6R over 5 years, profit factor 1.49
- Rs 10,000 → Rs 26,257 at 1% risk per trade
- 986 trades, 39.6% win rate

### 🚨 CRITICAL PROBLEM
Your live code updates the trailing stop **every 15 seconds**.  
The backtest updates it **only when 1H candles close**.

**This bug costs ~60% of your edge.**

### ✅ WHAT YOU MUST DO

**Immediately**:
1. Stop trading live until you fix the trail bug
2. Change risk from 15% to 1% per trade

**Fix the trail bug**:
- File: `liveTrade/trade_manager_s98.py`
- Change: Only update trail when 1H bar closes (not every 15 seconds)
- Match: The backtest behavior exactly

**Test on demo**:
- Run for 3 months (~50 trades)
- Should see +0.15 to +0.20R per trade average
- If you see negative, the fix didn't work

**Then go live**:
- 1% risk per trade (not 15%)
- XAUUSD only
- Exness Cent account required

---

## Strategy 97 (FX Basket) — YOUR SECONDARY STRATEGY

### ⚠️ IS IT PROFITABLE?
**BARELY** — the edge is extremely thin.

### 📊 THE NUMBERS
- +0.030R per trade pooled across 8 instruments
- This means both profit AND cost are about 1 pip
- If your broker widens spreads, you lose money

### ❌ IMPORTANT
**Gold on Strategy 97 LOSES money** (−5.5R over 5 years)

Don't trade gold with S97. Use S98 for gold.

### ✅ IF YOU USE S97

**Only trade these FX pairs**:
- EURUSD, GBPUSD, USDCHF, USDJPY

**Remove from basket**:
- XAUUSD (use S98 instead)

**Settings**:
- 1% risk per trade maximum
- Z_ENTRY = 2.5 (not 2.0)
- Use only 25% of capital on S97
- Main business is S98 gold

---

## Your Current Risk Setting — DANGER

### What your .env says:
```
MAX_RISK_PCT = 15%
```

### What the backtest used:
```
Risk = 1% per trade
```

### The problem:
At 15% risk per trade:
- 7 losing trades = account blown (gone, zero)
- Historical data shows 6-trade losing streaks happened
- You are **one bad week away from ruin**

At 1% risk:
- 100 losing trades to blow account
- Max drawdown was 19% (survived)
- This is what the profitable results assumed

**Change to 1-2% immediately.**

---

## Simple Action Plan

### Today (Before Next Trade)
1. ⛔ Stop live trading
2. 📝 Change `MAX_RISK_PCT` from 15% to 1%
3. 🔧 Fix trail bug in `trade_manager_s98.py`

### Next 3 Months
1. 🧪 Run S98 on demo with fixed trail
2. 📊 Track results: should be +0.15R per trade
3. ✅ If good → deploy live at 1% risk

### For S97 (Optional)
1. 🗑️ Remove XAUUSD from basket
2. ✂️ Only trade: EURUSD, GBPUSD, USDCHF, USDJPY  
3. 📉 Risk 1% per trade
4. 📊 Allocate max 25% of capital

---

## One-Sentence Summary

**S98 on gold makes money if you fix the trail bug and risk 1% per trade; S97 on FX barely makes money and needs perfect cost conditions; gold on S97 loses money; 15% risk will blow your account.**

---

## Why Most Things Failed

You tested ~400 different trading ideas.

**What worked**:
- Gold liquidity reclaim (S98) ✅
- FX mean-reversion (S97) — barely ⚠️

**What failed**:
- 398 other concepts ❌
- All FX trend systems (signal < spread)
- All gold trend systems (just gold beta, not real alpha)
- Calendar anomalies (0 out of 39 passed)
- Stat arb (no stable edge)
- YouTuber strategies (tested 40+, none profitable)

**The honest truth**: Most trading ideas don't work. You found one that does (S98), but it requires perfect execution and the trail bug is killing it.

---

## Questions?

Read the full detailed version: `LIVE_TRADE_VERDICT.md`

Or check specific topics:
- Why FX patterns failed: `research/README.md`
- What was tested: `STRATEGIES_INDEX.md`  
- S98 validation proof: `drawdown_analysis_response.md`
- Trail bug evidence: Compare `strategy_98_xau_trend_liquidity_trail.py` vs `liveTrade/trade_manager_s98.py`
