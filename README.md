# yt_learning — Strategy Lab

Causal backtests of YouTube/ICT-style strategies on Exness history, with realistic round-turn costs.

## Exness XAUUSD conventions

| Item | Value |
|------|-------|
| Exness pip | `$0.01` (second decimal on the quote) |
| Pip value | ≈ `$1` per Exness pip per **1.0 lot** (100 oz) |
| Framework metals “pip” | `$1.00` (legacy batch units) |
| Conversion | Exness pips = framework pips × **100** |
| Default round-turn cost | `BT_COST_PRICE=0.45` (~45 Exness pips: spread + slippage) |

Override costs per run:

```powershell
$env:BT_COST_PRICE = "0.45"
$env:YT_DATA_ROOT = "O:\D temp\UltimateTradeBot\Data\Exness\structured\history"
```

## Causality / lookahead (bias)

**Not every strategy is proven fully bias-free.**

What we did:

- Fixed known HTF-close / LTF-open lookahead in **s06, s28, s42, s62, s69**.
- **s95** supports `--strict-mss-causal` (batch ranking uses it).
- Many newer strategies (**s90–s93, s97, s98**, remote **s96**) are written to be causal (closed-bar signals, next-bar fills).
- Older ICT/scalp scripts may still have edge cases; treat integrity notices in each file seriously.

Do **not** assume “all strategies are leakage-free” without re-checking the specific script.

## XAUUSD ranking (fair Exness cost model)

Same harness: Exness history, `BT_COST_PRICE=0.45`, net PnL after costs.

### Best on gold (full history)

| Rank by total PnL | Strategy | Role | Trades | PF | Exness pips (approx) |
|-------------------|----------|------|-------:|---:|---------------------:|
| **1** | **s98** Unified trend + liquidity + ATR trail | **Best total returns** | 982 | 1.50 | **+327,260** |
| 2 | **s90** Donchian + ATR chandelier | Strong trend runner | 95 | 1.97 | +204,780 |
| 3 | **s13** 3-step ICT Gold + SMT | **Best profit factor** | 53 | **5.45** | +124,570 |
| 4 | **s91** MTF liquidity reclaim | Robust sample | 523 | 1.36 | +112,900 |

### Not the gold winner

| Strategy | Notes on XAUUSD |
|----------|-----------------|
| **s96** Tuned MSS + OB (remote) | FX-oriented; on gold ~PF 0.93–0.98, slight **net loss** (fair 1× cost) |
| **s97** With-trend mean-reversion (remote) | Designed as multi-pair basket; **soft/negative on XAUUSD alone** |
| High-count ICT/Judas/scalps (e.g. s65, s29, s01, s04, s56) | Bleed after realistic gold costs |

### How to read “best”

- **Best money / total net PnL** → **s98**
- **Best efficiency (PF)** → **s13** (small sample)
- **Remote s96 is not best on gold** (easy to confuse with our older “s96” naming; unified system is **s98**)

Fair-cost note: strategies that emit `pnl_R` were briefly over-charged 1.5× round-turn in enrich; re-scoring remotes at **1×** price-based cost does **not** change the ranking (s98 still wins; remote s96/s97 still lose on gold).

## Strategy ID map (recent)

| ID | File | Origin |
|----|------|--------|
| s96 | `strategy_96_mss_ob_tuned.py` | Remote `live` (MSS+OB tuned) |
| s97 | `strategy_97_trend_meanreversion.py` | Remote `live` (4H trend MR) |
| s98 | `strategy_98_xau_trend_liquidity_trail.py` | Local unified XAUUSD system |

## How to batch-backtest on XAUUSD

```powershell
$env:YT_DATA_ROOT = "O:\D temp\UltimateTradeBot\Data\Exness\structured\history"
$env:BT_COST_PRICE = "0.45"
D:\Python\Python3_12_8\python.exe batch_xauusd_backtest.py --exness-cost --windows 365,0
```

Compare remote s96/s97 vs s98:

```powershell
D:\Python\Python3_12_8\python.exe compare_remote_live_strategies.py
D:\Python\Python3_12_8\python.exe check_fair_cost_compare.py
```

Outputs land under `dashboard/out/` (e.g. `batch_xauusd_exness_summary.csv`, `compare_remote_live/`).

## What winners share (XAUUSD)

- Higher-timeframe bias (completed bars only)
- Trade with trend or selective reclaim — not pure 1m scalp spam
- Stops large enough vs gold spread; let winners run (trail) instead of tiny fixed 2R scalps
- Next-bar open entry; one position

## What losers share

- Huge trade counts with small edge vs ~45 Exness-pip round-turn
- Session Judas / US30 logic transplanted onto gold without HTF filter
- Fixed 2R targets that cut runners
- Cost-blind backtests

## Docs

- `changelog.md` — append-only change log
- `Q&A.md` — ranking / Exness / remote-compare decisions
