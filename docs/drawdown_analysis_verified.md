# Verified Drawdown & Edge Analysis — s97 / s98

> Independent re-analysis of `backtest_drawdown_analysis.md` and `strategy_98_drawdown_analysis.md`.
> All numbers below are **reproduced from the scripts and re-derived on fine-grained data**
> (XAUUSD 1-minute, FX majors 5-minute) pulled from the source data set.
> Pip convention: XAUUSD 1 pip = $0.01 (100 pips = $1.00). FX 1 pip = 0.0001 (JPY/gold = 0.01).

---

## 0. Bottom line (read this first)

1. **The original documents are arithmetically correct** — every figure reproduces exactly from the scripts. The problem was never the arithmetic; it was the **method and the framing**.
2. **The "99–100% of trades reach target" claim is meaningless as written.** The scan had no stop and no time limit, and the target is smaller than a single candle. Corrected for a real stop-vs-target race, the honest hit rate is **72–91%**, not 99%.
3. **Under your reframe ("capturing the target = a win"), the setups are mostly positive before costs** — but the edge is *thin*.
4. **Transaction cost decides everything.** After realistic spread+commission, only **gold (s97), EURUSD, GBPUSD, USDCHF-2x, USDJPY-2x** stay positive. AUDUSD, NZDUSD, USDCAD, and the tight-target variants turn negative.
5. **s98 (the 982-trade gold strategy) is not viable as specified** — it only breaks even at the 100p target and loses at 200p. The **s97 mean-reversion engine on gold is the strongest setup by a wide margin.**
6. **Risking 10% per trade is dangerous even with an 80%+ win rate** — see §6. It produces 30–97% drawdowns and, for the weak setups, blows the account.

---

## 1. Reproduction — the original numbers are real

Both original scripts were re-run against the committed trade files. Every headline number matched.

| Doc claim | Source | Reproduced? |
|---|---|:--:|
| s98 100p: min stop 387p, 975/982 qualify, 39.9% win | `analyze_drawdown.py` (TARGET=100) | ✅ exact |
| s98 200p: min stop 507p, 974/982 qualify | `analyze_drawdown.py` (TARGET=200) | ✅ exact |
| s97 all pairs: 619p/650p (XAU), 19p (EUR)… | `analyze_s97_drawdown.py` | ✅ exact |
| 3 non-qualifying s97 trades | `analyze_s97_drawdown.py` | ✅ exact |

**Conclusion:** no computational errors. The findings below are about *methodology*, not math.

---

## 2. Why the original method was misleading

### 2.1 The target is smaller than one candle

The original scripts scanned **forward with no stop and no time limit** and asked "did price ever move `target` pips in favor?". Because the target is a *fraction of a single bar's range*, the answer is almost always yes — on the entry bar itself.

**Proof — median range of one bar vs the target:**

| Data (timeframe) | Median 1-bar range | Target | Target ÷ bar |
|---|--:|--:|--:|
| XAUUSD 1H (s98) | **488 pips** | 100 / 200 | 20% / 41% |
| XAUUSD 4H (s97) | **970 pips** | 100 / 200 | 10% / 21% |
| EURUSD 4H | **31.7 pips** | 3 / 6 | 9% / 19% |
| GBPUSD 4H | **29.9 pips** | 3.5 / 7 | 12% / 23% |
| USDJPY 4H | **35.1 pips** | 5 / 10 | 14% / 28% |

Measured directly: **median bars-to-target = 0** for s98, s97-gold and s97-EURUSD (target hit on the entry bar). So "99% qualify" mostly says "a candle is bigger than the target" — true for random entries too. It is not evidence of edge.

### 2.2 The adverse ("stop needed") was intrabar noise

Because the target is hit on the entry bar, the "max adverse before target" is that same bar's opposite wick. On a 970-pip 4H gold bar, OHLC **cannot tell** whether the low (adverse) came before or after the high (target). The original "you need a 619p stop" conclusion rested entirely on an unresolved intrabar-ordering assumption.

### 2.3 The fix

Race **target vs stop, first-touch**, on the **finest timeframe available**, so intrabar ordering is resolved by real sub-bar data. Where a single fine bar still straddles both levels it is flagged *ambiguous* and bracketed. With 1m gold / 5m FX, ambiguity collapsed to **0–27 trades** out of hundreds — the results below are effectively exact.

---

## 3. Verified results — "does target hit before stop?"

Fine data (XAUUSD = 1m, FX = 5m). `R = stop distance`; a win pays `target/stop` R.

| Setup | tgt/stop (p) | Reward:Risk | **Hit rate** | Gross expectancy |
|---|---|:--:|:--:|:--:|
| XAUUSD s97 **2x** | 200 / 650 | 1 : 3.25 | **83.7%** | **+0.095R** |
| XAUUSD s97 **1x** | 100 / 619 | 1 : 6.19 | **88.4%** | +0.026R |
| XAUUSD s98 100p | 100 / 387 | 1 : 3.87 | 80.5% | +0.014R |
| XAUUSD s98 200p | 200 / 507 | 1 : 2.53 | 71.4% | −0.005R |
| EURUSD 2x | 6 / 19 | 1 : 3.17 | 82.3% | +0.083R |
| EURUSD 1x | 3 / 19 | 1 : 6.33 | 90.7% | +0.050R |
| GBPUSD 1x | 3.5 / 18 | 1 : 5.14 | 89.9% | +0.074R |
| GBPUSD 2x | 7 / 18 | 1 : 2.57 | 77.5% | +0.077R |
| USDCHF 2x | 5 / 13 | 1 : 2.60 | 81.7% | +0.131R |
| USDJPY 2x | 10 / 21 | 1 : 2.10 | 74.8% | +0.104R |
| USDCAD 2x | 7 / 15 | 1 : 2.14 | 71.6% | +0.050R |
| AUDUSD 1x | 2 / 12 | 1 : 6.00 | 86.2% | +0.006R |
| NZDUSD 1x | 1.5 / 13 | 1 : 8.67 | 91.4% | +0.019R |

**Note:** hit rates fell vs my earlier coarse-data estimate (e.g. AUDUSD 93%→86%, USDJPY-1x 88%→82%). The fine data is less flattering — which is exactly why it matters.

---

## 4. Transaction cost is the deciding factor

Tiny targets are extremely cost-sensitive. **Break-even cost (BEcost)** = the round-turn cost (in pips) at which the edge hits zero. `typCost` = a ballpark Exness-style round-turn (spread + commission) — **replace with your real fills.**

| Setup | Hit% | BEcost (pips) | typCost | Net expectancy | Verdict |
|---|:--:|:--:|:--:|:--:|:--:|
| **XAUUSD s97 2x** | 83.7% | **61.6** | 3.0 | **+0.090R** | ✅ strong |
| **XAUUSD s97 1x** | 88.4% | **16.4** | 3.0 | +0.022R | ✅ |
| USDJPY 2x | 74.8% | 2.17 | 0.9 | +0.061R | ✅ |
| USDCHF 2x | 81.7% | 1.71 | 1.2 | +0.039R | ✅ |
| EURUSD 2x | 82.3% | 1.57 | 0.8 | +0.041R | ✅ |
| GBPUSD 2x | 77.5% | 1.38 | 1.0 | +0.021R | ✅ (thin) |
| GBPUSD 1x | 89.9% | 1.33 | 1.0 | +0.018R | ✅ (thin) |
| EURUSD 1x | 90.7% | 0.95 | 0.8 | +0.008R | ⚠️ razor-thin |
| USDCHF 1x | 90.2% | 0.99 | 1.2 | −0.016R | ❌ loss |
| USDJPY 1x | 81.6% | 0.57 | 0.9 | −0.017R | ❌ loss |
| USDCAD 2x/1x | 71.6/82.4% | 0.76 / 0.60 | 1.2 | −0.030 / −0.046R | ❌ loss |
| AUDUSD 1x/2x | 86.2/77.0% | 0.07 / −0.14 | 0.9 | −0.069 / −0.074R | ❌ loss |
| NZDUSD 1x/2x | 91.4/82.7% | 0.25 / 0.23 | 1.2 | −0.073 / −0.074R | ❌ loss |
| s98 100p | 80.5% | 5.28 | 3.0 | +0.006R | ⚠️ ~flat |
| s98 200p | 71.4% | −2.31 | 3.0 | −0.010R | ❌ loss |

**Reading it:** for most FX pairs BEcost is under ~1 pip — a normal spread erases the edge. **Only gold has real headroom** (16–62 pips of slack), because its target (100–200p) dwarfs its cost.

---

## 5. What the original docs got right

- **"Adverse is front-loaded / 2x beats 1x":** confirmed. For almost every instrument the **2x target has higher net expectancy** than 1x — doubling the target barely widens the stop needed.
- **Gold entries are genuinely good** — the s97 mean-reversion engine on gold is the one setup with a robust, cost-proof edge.

---

## 6. Capital simulation — risking 10% of capital per trade

**Question:** start with capital, risk **10% per trade**, what happens before the account blows?

**Setup.** Start = **$10,000**. Each trade risks 10% of capital (1R = 10%). Outcomes are the **verified, chronological** race results, **net of typical cost**. Two money-management models:

- **Compounding** — risk 10% of *current* equity: `equity ×= (1 + 0.10 × netR)`. Cannot reach exactly $0, so we report max drawdown.
- **Flat** — risk 10% of *initial* equity (a fixed $1,000): `equity += 0.10 × $10,000 × netR`. **10 straight full losses = account gone.**

### 6.1 Results per setup

| Setup | Trades | Win% | Longest losing streak | Compound final | Compound maxDD | Flat final | Flat blown? | P(≥50% DD)\* | P(flat ruin)\* |
|---|:--:|:--:|:--:|--:|:--:|--:|:--:|:--:|:--:|
| **XAUUSD s97 2x** | 86 | 83.7% | 2 | **$19,494** | 30.7% | $17,757 | no | 0.5% | 0.00% |
| **XAUUSD s97 1x** | 86 | 88.4% | 1 | $11,306 | 28.4% | $11,861 | no | 0.1% | 0.00% |
| USDCHF 2x | 82 | 81.7% | 2 | $12,157 | 33.4% | $13,200 | no | 7.7% | 0.00% |
| USDJPY 2x | 103 | 74.8% | 5 | $14,962 | 42.3% | $16,252 | no | 26.7% | 0.04% |
| GBPUSD 2x | 89 | 77.5% | 4 | $10,319 | 63.6% | $11,889 | no | 25.0% | 0.01% |
| EURUSD 2x | 418 | 82.3% | 3 | $31,409 | 62.5% | $27,032 | no | 79.5% | 0.68% |
| XAUUSD **s98 100p** | 982 | 80.5% | 3 | **$4,933** | **86.1%** | $15,780 | no | **100%** | 26.5% |
| XAUUSD **s98 200p** | 982 | 71.4% | 6 | **$465** | **97.8%** | **$0** | **YES @ trade 163** | 100% | 100% |
| **PORTFOLIO** (gold-2x + EUR/GBP/CHF/JPY 2x) | 778 | 80.8% | 4 | **$114,929** | 66.2% | $46,130 | no | 98.1% | 1.74% |

\* Monte-Carlo over 20,000 random orderings of the same trades. *P(≥50% DD)* = chance equity ever halves (compounding). *P(flat ruin)* = chance the flat account hits $0.

### 6.2 What this shows

- **The profitable setups make money but with violent drawdowns.** The portfolio grows 10k → 115k, but max drawdown is **66%** and there's a **98% chance of at least halving your account** somewhere along the way. Most people cannot survive that psychologically or with a prop-firm limit.
- **s98 confirms it is broken at 10% risk.** The 200p variant **blows the flat account at trade 163** and draws down 97.8% compounding — it is a guaranteed ruin. The 100p variant survives but with an 86% drawdown and a 26.5% chance of flat-account ruin.
- **Even great win rates don't save you at 10% risk**, because the loss is 10% of capital and losing streaks cluster.

### 6.3 The math of ruin at 10% risk

Independent of strategy, here is what consecutive losses do to a 10%-risk account:

| Consecutive losses | Compounding (10% of current) left | Flat (10% of initial) left |
|:--:|:--:|:--:|
| 3 | 72.9% | 70% |
| 5 | 59.0% | 50% |
| 7 | 47.8% | 30% |
| **10** | 34.9% | **0% (blown)** |
| 14 | 22.9% | 0% |
| 22 | 9.8% | 0% |

Longest losing streaks actually observed: **1–6** (s98-200p hit 6). A run of **10** losses — which flat-10% cannot survive — is well within reach for the weaker setups over hundreds of trades.

### 6.4 Risk-per-trade sensitivity (portfolio)

Same trades, same order, only the risk fraction changes:

| Risk per trade | Final equity | Max drawdown |
|:--:|--:|:--:|
| 0.5% | $11,946 | 4.4% |
| 1.0% | $14,192 | 8.8% |
| 2.0% | $19,690 | 17.1% |
| 5.0% | $45,764 | 39.0% |
| **10.0%** | **$114,929** | **66.2%** |

**10% maximizes final equity but at a 66% drawdown.** At **1–2% per trade** the same edge compounds steadily with a survivable 9–17% drawdown. **For this edge, 1–2% is the rational risk; 10% is gambling on top of a real edge.**

---

## 7. Recommendations

1. **Trade gold via the s97 mean-reversion engine.** It's the only instrument with a large cost buffer (BEcost 16–62 pips). Prefer the **2x target** (+0.090R net, 30.7% DD at 10% risk).
2. **Keep the cost-surviving FX-2x setups** (EURUSD, GBPUSD, USDCHF, USDJPY) as diversifiers — but only after confirming your real spreads beat their BEcost.
3. **Drop AUDUSD, NZDUSD, USDCAD, USDCHF-1x, USDJPY-1x.** No edge after realistic cost.
4. **Retire s98 as specified.** It's break-even at best (100p) and a guaranteed blow-up at 200p under 10% risk. Rebuild on the s97 gold logic.
5. **Cut risk to 1–2% per trade.** 10% produces 60–98% drawdowns even on the good setups. The edge is real but small; position size is what turns it into ruin or a smooth curve.
6. **Plug in real fills.** The `typCost` values are ballpark. Update `TYP_COST` in `bt_race_summary.py` / `bt_capital_sim.py` and re-run before risking money.

---

## 8. How to reproduce

| File | Purpose |
|---|---|
| `bt_target_stop_race.py` | First-touch target-vs-stop race on finest available TF, with intrabar-ambiguity bracketing. |
| `bt_race_summary.py` | Verified hit rate + gross expectancy + transaction-cost break-even (§3–4). |
| `bt_capital_sim.py` | Capital simulation at 10% risk, compounding & flat, with Monte-Carlo ruin (§6). |
| `dl_curl.sh` | Records how the fine-grained OHLC (1m gold, 5m FX) was fetched. |

```bash
python3 bt_race_summary.py     # §3–4 tables
python3 bt_capital_sim.py      # §6 simulation
```

**Data used:** XAUUSD 1-minute (1.76M bars, 2021-07→2026-07) and 5-minute for the FX majors, placed under `data/<PAIR>/<tf>/`. Trades from `s97_all_pairs_trades.json` and `dashboard/out/s98_…141427.json` (982 trades).

### Caveats
- Cost assumptions are estimates; real spreads (especially gold, which can be $0.10–0.30 = 10–30 pips) must be verified. Even so, gold's buffer keeps 2x profitable.
- The race assumes fills exactly at target/stop levels with no slippage or partial fills.
- A few early trades pre-date the fine data and fell back to 1H/4H (shown in the race engine's per-TF counts); the effect is negligible.
- Past performance on this specific 2021–2026 sample does not guarantee future results.
