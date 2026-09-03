# S97 All-Pairs Drawdown Analysis (with Equivalent Targets)

## Equivalent Pip Targets (same return as XAUUSD 100p / 200p with 2000x leverage)

| Pair | Pip | 1x Target (pips) | 1x Target ($ price) | 2x Target (pips) | 2x Target ($ price) |
|------|-----|:-----------------:|:-------------------:|:-----------------:|:-------------------:|
| **XAUUSD** | 0.01 | 100 pips | $1.0000 | 200 pips | $2.0000 |
| EURUSD | 0.0001 | 3 pips | $0.0003 | 6 pips | $0.0006 |
| GBPUSD | 0.0001 | 3.5 pips | $0.0004 | 7 pips | $0.0007 |
| AUDUSD | 0.0001 | 2 pips | $0.0002 | 4 pips | $0.0004 |
| NZDUSD | 0.0001 | 1.5 pips | $0.0002 | 3 pips | $0.0003 |
| USDCAD | 0.0001 | 3.5 pips | $0.0004 | 7 pips | $0.0007 |
| USDCHF | 0.0001 | 2.5 pips | $0.0003 | 5 pips | $0.0005 |
| USDJPY | 0.01 | 5 pips | $0.0500 | 10 pips | $0.1000 |

> The FX targets give the **same effective dollar return** as XAUUSD 100p / 200p when using 2000x leverage.

---

## Results — 1x Target (100p XAUUSD equivalent)

### Qualification Rates
| Pair | Target | Qualified | Total | % |
|------|--------|:---------:|:-----:|:-:|
| **XAUUSD** | 100p | 86 | 86 | **100%** |
| EURUSD | 3p | 417 | 418 | **99.8%** |
| GBPUSD | 3.5p | 89 | 89 | **100%** |
| AUDUSD | 2p | 87 | 87 | **100%** |
| NZDUSD | 1.5p | 80 | 81 | **98.8%** |
| USDCAD | 3.5p | 73 | 74 | **98.6%** |
| USDCHF | 2.5p | 82 | 82 | **100%** |
| USDJPY | 5p | 103 | 103 | **100%** |

### Minimum Stop for >60% Survival
| Pair | Min Stop | As $ price | Stop : Target ratio | Median Adverse |
|------|:--------:|:----------:|:-------------------:|:--------------:|
| **XAUUSD** | **619p** | **$6.190** | **6.19×** | 463.0p |
| EURUSD | 19p | $0.0019 | 6.33× | 13.5p |
| GBPUSD | 18p | $0.0018 | 5.14× | 11.7p |
| AUDUSD | 12p | $0.0012 | 6.00× | 9.3p |
| NZDUSD | 13p | $0.0013 | 8.67× | 9.8p |
| USDCAD | 13p | $0.0013 | 3.71× | 10.7p |
| USDCHF | 13p | $0.0013 | 5.20× | 9.3p |
| USDJPY | 19p | $0.1900 | 3.80× | 14.8p |

---

## Results — 2x Target (200p XAUUSD equivalent)

| Pair | Target | Qualified | Min Stop | Stop : Target | Median Adverse |
|------|:------:|:---------:|:--------:|:-------------:|:--------------:|
| **XAUUSD** | 200p | 86/86 | **650p** ($6.50) | **3.25×** | 518.9p |
| EURUSD | 6p | 417/418 | **19p** ($0.0019) | 3.17× | 14.6p |
| GBPUSD | 7p | 89/89 | **18p** ($0.0018) | 2.57× | 12.6p |
| AUDUSD | 4p | 87/87 | **14p** ($0.0014) | 3.50× | 10.4p |
| NZDUSD | 3p | 80/81 | **13p** ($0.0013) | 4.33× | 9.8p |
| USDCAD | 7p | 73/74 | **15p** ($0.0015) | 2.14× | 11.6p |
| USDCHF | 5p | 82/82 | **13p** ($0.0013) | 2.60× | 9.7p |
| USDJPY | 10p | 103/103 | **21p** ($0.2100) | 2.10× | 15.7p |

---

## Key Findings

### 1. Qualification rates are near-perfect (>98.6%) for all pairs
Virtually every s97 trade reaches the equivalent target — the mean-reversion entries consistently produce enough favorable movement.

### 2. Stop-to-target ratio is 3–6× across all FX pairs
Same as XAUUSD (3–6×): price moves against the position significantly before reaching the target. This is the nature of fading stretched moves.

### 3. XAUUSD requires the widest absolute stop ($6.19)
But its stop-to-target ratio (6.19×) is in line with FX pairs like EURUSD (6.33×) and AUDUSD (6.00×). NZDUSD is worst at 8.67×.

### 4. Best and worst pairs for tight stops

| Rank | Pair | Stop (1x target) | Stop:Target |
|------|------|:----------------:|:-----------:|
| 1 | USDCAD | 13p | 3.71× |
| 2 | USDJPY | 19p | 3.80× |
| 3 | GBPUSD | 18p | 5.14× |
| 4 | USDCHF | 13p | 5.20× |
| 5 | AUDUSD | 12p | 6.00× |
| 6 | EURUSD | 19p | 6.33× |
| 7 | XAUUSD | 619p | 6.19× |
| 8 | NZDUSD | 13p | 8.67× |

### 5. The 2× target barely increases the stop requirement
For most pairs, the min stop for the 2× target is only 0–3 pips wider than for the 1× target. This means the adverse movement happens **early** in the trade — once the trade survives past the initial drawdown, doubling the target requires almost no additional stop width.

---

## Verdict

> **All 8 pairs achieve >98% qualification** on their equivalent targets. The minimum stop for >60% survival ranges from **12–19 pips** for FX pairs (or **619p for XAUUSD**), with a stop-to-target ratio of **3.7–8.7×**.
>
> **USDCAD** and **USDJPY** are the most efficient (tightest stop-to-target ratio at ~3.8×), while **NZDUSD** and **EURUSD** require the widest relative stops (~6–9×).
>
> Since all pairs have >98% qualification, the main consideration is **stop distance**: set stops at ~13–19 pips for FX majors (depending on pair) to capture 60%+ of trades reaching target.
