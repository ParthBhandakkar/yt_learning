# Response: Verified Drawdown Analysis — Honest Truth

Independent check of `drawdown_analysis_verified.md` (other developer).
Reproduced 2026-07-17 via `verify_drawdown_claims.py` on Exness XAUUSD 1H/1m/4H.
Artifacts: `dashboard/out/verify_drawdown_claims/verification_summary.json`.

---

## Bottom line

| Claim in their doc | Honest verdict |
|---|---|
| "s98 is not viable **as specified**" | **Misleading.** They judged a *re-imagined* s98 (fixed 100/200p targets + fixed stops), not the real strategy. |
| Real s98 (ATR chandelier trail) is unprofitable | **False.** Reproduced: **+162.6R, PF 1.49** full history; **+82.3R, PF 1.97** on 1y — net of Exness costs. |
| "s97 mean-reversion on gold is the strongest setup" | **False for actual exits.** Real s97 Z=2.5 on gold: **−5.5R** full history. Their "strong" gold result is again their fixed-target race on *entries*, not s97's real exits. |
| 10% risk per trade is dangerous | **True** — and it applies even to the *profitable* real s98 R-stream. |
| Tiny fixed targets are cost-fragile | **True** for their reframe; irrelevant to real s98, which does not use those targets. |

**Honest truth:** Real s98 (what we backtested and run live) remains the best gold strategy in our compare. The other doc correctly critiques a bad exit model and correctly warns about oversized risk — then wrongly attributes "not profitable" to s98 itself.

---

## 1. What the real strategies actually do

### Real s98 (`strategy_98_xau_trend_liquidity_trail.py`)
- 4H EMA bias → 1H liquidity reclaim / Donchian breakout
- Entry: next 1H open
- Exit: **ATR chandelier trail** (no fixed TP)
- Initial stop: structural swing or 1.5×ATR (whichever wider)
- Costs: Exness-calibrated round-turn (~$0.40 = 40 Exness pips on gold)

### What their doc tested instead
- Same *entry list* idea, then force:
  - Target = 100 or 200 Exness pips ($1 or $2)
  - Stop = 387 or 507 Exness pips
- That is **not** the strategy specification. Calling it "as specified" is the core framing error.

---

## 2. Reproduction — real backtests (fair Exness costs)

| Strategy | Window | Trades | Win% | PF | Total R | Max DD% | Final ($10k @ 1%/R flat) |
|---|---|--:|--:|--:|--:|--:|--:|
| **s98 ATR trail** | full | 986 | 39.6 | **1.49** | **+162.6** | 19.1 | **$26,257** |
| **s98 ATR trail** | 1y | 169 | 50.9 | **1.97** | **+82.3** | 4.9 | **$18,228** |
| s97 Z=2.5 actual exits | full | 42 | 54.8 | 1.30 | **−5.5** | 8.3 | $9,453 |
| s97 Z=2.5 actual exits | 1y | 8 | 62.5 | 3.71 | +0.2 | 1.0 | $10,019 |

Committed compare (`full_pair_compare_s96_s97_s98`) had s98 full: 982 trades, PF 1.51, +164R — matches within a few trades (data edge refresh).

**Conclusion:** Real s98 is net-profitable after costs. Real s97 on gold is not a gold champion under its own exits.

---

## 3. Their fixed-target race — checked on our 1m data

First-touch race on **986 real s98 entries**, XAUUSD 1m (1.77M bars). Ambiguous bars counted stop-first (conservative).

| Setup | Their claimed hit% | Our hit% | Gross exp R | Net @ 3p cost | Net @ 40p cost (Exness-like) |
|---|--:|--:|--:|--:|--:|
| 100p / 387p stop | 80.5% | **76.4%** | **−0.039** | −0.047 | −0.142 |
| 200p / 507p stop | 71.4% | **68.9%** | **−0.040** | −0.046 | −0.119 |

So under *their own reframe*, the edge is already **negative before costs** on our replay (hit rates a few points below theirs; expectancy clearly red). With realistic gold cost (~40 Exness pips RT), it is deeply negative.

That does **not** mean real s98 is broken — it means grafting tiny fixed targets onto structural-stop entries is a bad exit design. Real s98 lets winners run via ATR trail; that is why the win rate is ~40% but expectancy is positive (+0.17R/trade).

Their gold `typCost=3` pips also contradicts their own caveat (§8: real gold can be 10–30 pips). Our `core.py` uses ~40 Exness pips RT for XAUUSD.

---

## 4. Risk sizing — their warning applies to the real profitable stream

Simulation on the **actual** s98 R-stream (960 closed R multiples, sum +162.6R):

| Risk / trade | Mode | Final | Max DD | Blown? |
|---|---|--:|--:|---|
| **1%** | flat | $26,257 | 19% | no |
| **1%** | compound | $46,159 | 18% | no |
| **2%** | flat | $42,515 | 37% | no |
| **10%** | flat | $0 | 96% | **YES @ trade 121** |
| **10%** | compound | huge | 93% | no (but untradable DD) |
| **15%** | flat | $0 | 100% | **YES @ trade 101** |

**Takeaways:**
- Their §6 ruin math is correct in spirit: **10%+ risk turns even a real edge into account blow-ups or 90%+ drawdowns.**
- Our *research* backtests use **1% of initial equity per 1R** — that is the fair reading of "s98 is profitable."
- Live config (`FIXED_LOT=0.01` + `MAX_RISK_PCT=15`) is **not** the same as 1%/R. Wide structural stops on 0.01 lot can still risk large % of a small demo; the 15% cap skips the worst, but live risk is higher than the backtest model. Prefer aligning live toward **1–2% risk** (or keep 0.01 lot only when stop distance implies ≤2% equity).

---

## 5. Scorecard — what to keep vs discard

### Keep (valid)
1. No-stop / no-time-limit "99% hit target" scans are meaningless when the target is smaller than one bar.
2. First-touch target-vs-stop on fine TF is the right way to evaluate *fixed* TP/SL systems.
3. **10% risk per trade is reckless** for this edge size.
4. Tiny-target FX variants are cost-killed quickly.

### Discard / correct (invalid as stated)
1. **"Retire s98 as specified"** — false. Real s98 is the ATR-trail system and remains profitable after costs.
2. **"s97 gold is the strongest"** — false for actual s97 exits on gold (−5.5R).
3. Their §3–4 tables describe a **hypothetical exit overlay**, not our live/backtest strategy.
4. Referenced scripts (`bt_target_stop_race.py`, `bt_capital_sim.py`, etc.) and `data/` 1m layout were **not in the repo** — unreproducible as committed. Our check rebuilt the race independently.

### Our caveats (honest)
1. Short windows (e.g. 3m mega compare) can be weak for s98; full + 1y are strong.
2. Live fixed-lot ≠ backtest 1%/R — do not claim live will match +164R without matching risk.
3. 2021–2026 sample; past edge ≠ future guarantee.
4. Intrabar trail exits on 1H bars still use conservative assumptions (same as our backtest).

---

## 6. Recommendations (updated)

1. **Keep s98 as the live gold strategy** (ATR trail) — verified profitable after Exness costs.
2. **Do not switch gold live to s97** based on that doc — s97's own gold exits are net negative over full history.
3. **Act on the risk warning:** target **1–2% equity risk per trade** live; treat 10–15% as demo stress only.
4. Ignore "retire s98" and ignore fixed 100/200p overlays unless someone deliberately redesigns a *new* strategy around those exits (which our 1m race says would lose).

---

## Reproduce

```powershell
cd "O:\D temp\yt_learning"
$env:YT_DATA_ROOT="O:\D temp\UltimateTradeBot\Data\Exness\structured\history"
D:\Python\Python3_12_8\python.exe verify_drawdown_claims.py
```

See `dashboard/out/verify_drawdown_claims/verification_summary.json`.
