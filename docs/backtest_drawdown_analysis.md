# Backtest Drawdown Analysis — Strategy s98 & s97

> **Pip Definition**: 100 pips = $1.00 price movement on XAUUSD (1 pip = $0.01).  
> **Leverage**: 2000×.  
> **Data Source**: 1H / 4H OHLC candles with intra-trade adverse movement computed from bar highs/lows.

---

## 1. Strategy 98 — XAUUSD Trend + Liquidity + ATR Trail

**Trades**: 982  
**Timeframe**: 1H (+4H resample for bias)

### 1.1 Target: 100 pips ($1.00)

| Metric | Value |
|--------|-------|
| Trades reaching ≥100 pips favorable | **975 / 982 (99.3%)** |
| Trades never reaching target | **7 trades (0.7%)** |
| Minimum stop for >60% survival | **387 pips ($3.87)** |
| Stop-to-target ratio | 3.87× |
| Median adverse movement before target | 280.7 pips ($2.81) |
| 25th–75th percentile adverse | 119.1 – 658.2 pips |
| Actual win rate among qualifying trades (ATR trail) | 39.9% |

### 1.2 Target: 200 pips ($2.00)

| Metric | Value |
|--------|-------|
| Trades reaching ≥200 pips favorable | **974 / 982 (99.2%)** |
| Trades never reaching target | **8 trades (0.8%)** |
| Minimum stop for >60% survival | **507 pips ($5.07)** |
| Stop-to-target ratio | 2.54× |
| Median adverse movement before target | 348.4 pips ($3.48) |
| 25th–75th percentile adverse | 131.6 – 928.6 pips |
| Actual win rate among qualifying trades (ATR trail) | 39.9% |

### 1.3 Key Observation — s98 XAUUSD

> **Stop-to-target ratio improves at higher targets**: 3.87× for 100p vs 2.54× for 200p — meaning the adverse movement is front-loaded. Once the trade survives the initial drawdown, reaching the extended target requires proportionally less additional stop.  
>
> Even with an infinitely wide stop, only **39.9%** of qualifying trades are actual winners — the ATR trail exits many trades at a loss *after* the target is reached. A static stop alone is insufficient; an ATR trailing mechanism (or equivalent) is needed to lock in profits.

---

## 2. Strategy 97 — All Pairs Mean-Reversion

**Trades per pair**: 74–418  
**Timeframe**: 4H

### 2.1 Equivalent Target Calculation

The pip targets below give the **same effective dollar return** as XAUUSD 100p / 200p with 2000× leverage, calculated by scaling for each pair's pip value and margin requirement:

| Pair | 1× Target | = Price move | 2× Target | = Price move |
|------|:---------:|:------------:|:---------:|:------------:|
| **XAUUSD** | 100 pips | $1.0000 | 200 pips | $2.0000 |
| EURUSD | 3 pips | $0.0003 | 6 pips | $0.0006 |
| GBPUSD | 3.5 pips | $0.0004 | 7 pips | $0.0007 |
| AUDUSD | 2 pips | $0.0002 | 4 pips | $0.0004 |
| NZDUSD | 1.5 pips | $0.0002 | 3 pips | $0.0003 |
| USDCAD | 3.5 pips | $0.0004 | 7 pips | $0.0007 |
| USDCHF | 2.5 pips | $0.0003 | 5 pips | $0.0005 |
| USDJPY | 5 pips | $0.0500 | 10 pips | $0.1000 |

> **Example**: A 3-pip move on EURUSD gives the same dollar return as a 100-pip move on XAUUSD when using 2000× leverage.

### 2.2 Results — 1× Target

| Pair | Qualifying | % | Min Stop (>60%) | Stop:Target | Median Adverse |
|------|:----------:|:-:|:---------------:|:-----------:|:--------------:|
| **XAUUSD** | 86 / 86 | **100%** | **619p** ($6.19) | **6.19×** | 463.0p |
| EURUSD | 417 / 418 | **99.8%** | **19p** ($0.0019) | **6.33×** | 13.5p |
| GBPUSD | 89 / 89 | **100%** | **18p** ($0.0018) | **5.14×** | 11.7p |
| AUDUSD | 87 / 87 | **100%** | **12p** ($0.0012) | **6.00×** | 9.3p |
| NZDUSD | 80 / 81 | **98.8%** | **13p** ($0.0013) | **8.67×** | 9.8p |
| USDCAD | 73 / 74 | **98.6%** | **13p** ($0.0013) | **3.71×** | 10.7p |
| USDCHF | 82 / 82 | **100%** | **13p** ($0.0013) | **5.20×** | 9.3p |
| USDJPY | 103 / 103 | **100%** | **19p** ($0.1900) | **3.80×** | 14.8p |

### 2.3 Results — 2× Target

| Pair | Qualifying | % | Min Stop (>60%) | Stop:Target | Median Adverse |
|------|:----------:|:-:|:---------------:|:-----------:|:--------------:|
| **XAUUSD** | 86 / 86 | **100%** | **650p** ($6.50) | **3.25×** | 518.9p |
| EURUSD | 417 / 418 | **99.8%** | **19p** ($0.0019) | **3.17×** | 14.6p |
| GBPUSD | 89 / 89 | **100%** | **18p** ($0.0018) | **2.57×** | 12.6p |
| AUDUSD | 87 / 87 | **100%** | **14p** ($0.0014) | **3.50×** | 10.4p |
| NZDUSD | 80 / 81 | **98.8%** | **13p** ($0.0013) | **4.33×** | 9.8p |
| USDCAD | 73 / 74 | **98.6%** | **15p** ($0.0015) | **2.14×** | 11.6p |
| USDCHF | 82 / 82 | **100%** | **13p** ($0.0013) | **2.60×** | 9.7p |
| USDJPY | 103 / 103 | **100%** | **21p** ($0.2100) | **2.10×** | 15.7p |

---

## 3. The 2% That Missed — Non-Qualifying Trades

Across all 1,020 s97 trades (all pairs, both targets), only **3 trades** never reached their equivalent target:

| Pair | Trade # | Direction | Entry | Exit | Move | Outcome | pnl_R |
|------|:-------:|:---------:|-------|------|:----:|:-------:|:-----:|
| EURUSD | #250 | Long | 1.38476 | 1.37797 | **-67.9p** | Loss | **-1.0** |
| NZDUSD | #81 | Long | 0.59043 | 0.58617 | **-42.6p** | Loss | **-1.0** |
| USDCAD | #19 | Short | 1.26502 | 1.27307 | **-80.5p** | Loss | **-1.0** |

**Key characteristics:**
- All are full **-1R** losses — price went straight against direction and hit the initial stop
- None recovered even 1 pip in the favorable direction
- No partial misses, no breakevens, no marginal failures — complete stop-outs
- These represent **0.3%** of total trades across all pairs

---

## 4. Stop Distance Recommendations

### 4.1 Recommended Stops for 60%+ Survival

| Pair | 1× Target Stop | 2× Target Stop | Suggested Static Stop |
|------|:--------------:|:--------------:|:---------------------:|
| **XAUUSD** | 619p ($6.19) | 650p ($6.50) | **620–650p** ($6.20–$6.50) |
| EURUSD | 19p ($0.0019) | 19p ($0.0019) | **19p** ($0.0019) |
| GBPUSD | 18p ($0.0018) | 18p ($0.0018) | **18p** ($0.0018) |
| AUDUSD | 12p ($0.0012) | 14p ($0.0014) | **12–14p** ($0.0012–$0.0014) |
| NZDUSD | 13p ($0.0013) | 13p ($0.0013) | **13p** ($0.0013) |
| USDCAD | 13p ($0.0013) | 15p ($0.0015) | **13–15p** ($0.0013–$0.0015) |
| USDCHF | 13p ($0.0013) | 13p ($0.0013) | **13p** ($0.0013) |
| USDJPY | 19p ($0.19) | 21p ($0.21) | **19–21p** ($0.19–$0.21) |

### 4.2 Stop Efficiency Ranking (Best to Worst)

Ranked by stop-to-target ratio (lower = tighter stop relative to target):

| Rank | Pair | 1× Stop:Target | 2× Stop:Target |
|:----:|------|:--------------:|:--------------:|
| 1 | **USDCAD** | 3.71× | 2.14× |
| 2 | **USDJPY** | 3.80× | 2.10× |
| 3 | **GBPUSD** | 5.14× | 2.57× |
| 4 | **USDCHF** | 5.20× | 2.60× |
| 5 | **AUDUSD** | 6.00× | 3.50× |
| 6 | **XAUUSD** | 6.19× | 3.25× |
| 7 | **EURUSD** | 6.33× | 3.17× |
| 8 | **NZDUSD** | 8.67× | 4.33× |

### 4.3 Important Note on 2× Targets

For most pairs, the stop requirement for the 2× target is only **0–3 pips wider** than the 1× target. This confirms that adverse movement is **front-loaded** — once the trade survives past the initial drawdown, doubling the target adds almost no additional stop risk.

---

## 5. Strategy Comparison: s98 vs s97 on XAUUSD

| Metric | s98 (Trend + ATR Trail) | s97 (Mean Reversion) |
|--------|:-----------------------:|:--------------------:|
| Trade count | 982 | 86 |
| 100p qualifying | 99.3% | 100% |
| 200p qualifying | 99.2% | 100% |
| Min stop for >60% (100p) | **387p ($3.87)** | **619p ($6.19)** |
| Min stop for >60% (200p) | **507p ($5.07)** | **650p ($6.50)** |
| Stop:Target (100p) | **3.87×** | **6.19×** |
| Stop:Target (200p) | **2.54×** | **3.25×** |

**s98 (trend-following)** needs a much tighter stop than **s97 (mean-reversion)** for the same 60% survival rate. This is expected — fading stretched moves (s97) inherently requires wider stops as price overshoots before reverting.

---

## 6. Conclusions

1. **Qualification rates are near-perfect** for both strategies — >99% of trades on s98 and >98.6% on s97 reach their equivalent targets.

2. **Stop-to-target ratios range from 2× to 9×** depending on strategy and pair. Mean-reversion strategies (s97) need wider stops than trend-following (s98).

3. **USDCAD and USDJPY** are the most efficient pairs for stop placement (lowest stop-to-target ratio). **NZDUSD and EURUSD** require the widest relative stops.

4. **Adverse movement is front-loaded** — the 2× target requires almost no additional stop width beyond the 1× target.

5. **The ~2% non-qualifying trades** are all clean -1R losses with no partial recovery — immediate stop-outs.

6. **A static stop alone is insufficient for s98 XAUUSD** — even with an infinite stop, only 39.9% of trades are winners. An ATR trailing mechanism is essential to lock in profits after the target is reached.
