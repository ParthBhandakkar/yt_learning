# yt_learning — Strategy Lab

Causal backtests of YouTube/ICT-style strategies on Exness history, with realistic round-turn costs.

## Causality / leakage status (s96 / s97 / s98)

| Strategy | Leakage-free design? | How entry/exit is gated | Notes |
|----------|----------------------|-------------------------|-------|
| **s96** Tuned MSS+OB | **Yes (by design)** | 5m search only **after 1H MSS candle closes**; strict OTE; session filter | Built to remove s95 same-hour lookahead |
| **s97** Trend mean-rev | **Yes (by design)** | Indicators at bar `i`; signal on **close**; fill **next open**; stops use **prior-bar** ATR/SMA | ATR-normalized; no HTF peek |
| **s98** Unified trail | **Yes (by design)** | **Completed 4H** bias only; 1H signal on close; fill **next 1H open**; ATR chandelier | Gold-first system |
| Older ICT/scalps (many s01–s81) | **Mixed** | Some fixed (s06/s28/s42/s62/s69); s95 needs `--strict-mss-causal` | Do not assume all are clean |

## Exness contract match (per pair)

Backtests use **price + R-multiples**, not lots. Pip **size** must match Exness.

| Symbol | Exness pip | Contract / 1 lot | ≈$ / Exness pip @ 1 lot | Our model |
|--------|------------|------------------|-------------------------|-----------|
| EURUSD, GBPUSD, AUDUSD, NZDUSD | 0.0001 | 100,000 | $10 | Match |
| USDCAD, USDCHF | 0.0001 | 100,000 | ~$10 / rate | Pip size match |
| USDJPY | **0.01** | 100,000 | ~$6.67 @ 150 | Match |
| XAUUSD | **0.01** | 100 oz | $1 | Broker pip 0.01; **stats unit $1** (×100) |

Default RT **price** costs: FX majors ~0.00012 (~1.2 pips), JPY ~0.03 (~3 pips), gold ~0.40 (~40 Exness pips).  
**Never** set one global `BT_COST_PRICE` when mixing gold and FX.

```powershell
D:\Python\Python3_12_8\python.exe audit_exness_pip_model.py
```

## Head-to-head: s96 vs s97 vs s98 (all 8 pairs)

**Data:** Exness structured history (full available span per CSV — see windows below).  
**Costs:** per-symbol class defaults; `pnl_R` stripped → **1×** `enrich_trades_pnl` (fair).  
**Equity model:** start **$10,000**; risk **1% of initial ($100) per 1R** (fixed).  
`return_pct` ≈ `total_R` under that model.  
**Source:** `dashboard/out/full_pair_compare_s96_s97_s98/`

### Backtest duration (data windows)

Each strategy uses the **full CSV** available for its timeframe(s). Effective span = intersection of TFs it needs.

| Strategy | Timeframes used | Typical window | Calendar span |
|----------|-----------------|----------------|---------------|
| **s96** | 4H + 1H + 15m + **5m** | Limited by **5m** start | ~**Jun/Jul 2021 → 1 Jul 2026** (~5.0–5.1 years) |
| **s97** | **4H only** | Full 4H file | Most pairs **11 Mar 2021 → 1 Jul 2026** (~5.3 years); **EURUSD 4 Jan 1999 → 1 Jul 2026** (~27.5 years) |
| **s98** | **1H** (4H bias resampled from 1H) | Full 1H file | Most pairs **11 Mar 2021 → 1 Jul 2026** (~5.3 years); **XAUUSD 2 Mar 2021 → 1 Jul 2026**; **EURUSD 4 Jan 1999 → 1 Jul 2026** (~27.5 years) |

#### Per-pair CSV coverage (Exness library)

| Pair | 4H (s97) | 1H (s98) | 5m (limits s96) | ~Years (1H/4H) |
|------|----------|----------|-----------------|----------------|
| GBPUSD | 2021-03-11 → 2026-07-01 | same | 2021-06-14 → 2026-07-01 | ~5.3 |
| AUDUSD | 2021-03-11 → 2026-07-01 | same | 2021-06-14 → 2026-07-01 | ~5.3 |
| **EURUSD** | **1999-01-04 → 2026-07-01** | **same** | **1999-01-04 → 2026-07-01** | **~27.5** |
| NZDUSD | 2021-03-11 → 2026-07-01 | same | 2021-07-02 → 2026-07-01 | ~5.3 |
| USDCAD | 2021-03-11 → 2026-07-01 | same | 2021-06-14 → 2026-07-01 | ~5.3 |
| USDCHF | 2021-03-11 → 2026-07-01 | same | 2021-06-14 → 2026-07-01 | ~5.3 |
| USDJPY | 2021-03-11 → 2026-07-01 | same | 2021-06-14 → 2026-07-01 | ~5.3 |
| XAUUSD | 2021-03-02 → 2026-07-01 | same | 2021-07-02 → 2026-07-01 | ~5.3 |

**Caveat:** EURUSD results for **s97/s98** cover a much longer history than the other pairs, so basket totals overweight EURUSD sample length. s96 is closer to apples-to-apples (~2021–2026 on all pairs via 5m).

### Basket summary

| Strategy | Pairs +R | Trades | Total R | Return % (parallel books) | Avg R/trade | Worst pair max DD % |
|----------|----------|-------:|--------:|--------------------------:|------------:|--------------------:|
| s96 | 2 / 8 | 237 | **-38.3** | **-38.3%** | -0.162 | 22.0% |
| **s97 Z=2.5** | **5 / 8** | 480 | **+30.3** | **+30.3%** | **+0.066** | **8.3%** |
| s98 | 5 / 8 | 12,521 | +49.5 | +49.5% | +0.004 | 81.3% (FX bleed) |

**Basket takeaway:** **s97** is the best **forex basket** (highest avg R/trade, lowest DD, 5/8 pairs green). **s98** basket R is inflated by **XAUUSD only**; on FX it is unstable.

### Best for what (recommended)

| Use case | Pick | Why |
|----------|------|-----|
| **XAUUSD (gold)** | **s98** | +164 R, +164% (1%/R model), PF 1.51, DD ~19% |
| **FX multi-pair basket** | **s97 Z=2.5** | +30 R basket, +0.066 R/trade, DD ~8% |
| **GBPUSD** | **s96** | +14.6 R, PF 2.69, DD 2.8% (best quality on that pair) |
| **NZDUSD / USDCHF / USDJPY** | **s97** | Best total R with PF > 1 and small DD |
| **EURUSD / USDCAD** | Prefer **s97** or **s96** for live | s98 can lead raw R but PF ≤ 1 and DD is large |
| **AUDUSD** | None (all ≤ 0) | Least-bad: s97 |

### Per-pair results (full metrics)

#### s98 (ours)

| Pair | Trades | WR% | PF | Total R | Return % | Max DD % | Net Exness pips | Max DD (Exness pips) |
|------|-------:|----:|---:|--------:|---------:|---------:|----------------:|---------------------:|
| GBPUSD | 1120 | 32.7 | 0.97 | +10.4 | +10.4 | 19.0 | -653 | 1444 |
| AUDUSD | 1097 | 30.6 | 0.80 | -62.2 | -62.2 | 77.1 | -2712 | 3065 |
| EURUSD | 4964 | 33.4 | 0.95 | +22.0 | +22.0 | 63.6 | -4406 | 9503 |
| NZDUSD | 1109 | 32.0 | 0.79 | -76.7 | -76.7 | 81.3 | -2825 | 3103 |
| USDCAD | 1083 | 32.7 | 0.92 | +12.3 | +12.3 | 37.0 | -1360 | 3005 |
| USDCHF | 1121 | 31.8 | 0.84 | -25.9 | -25.9 | 51.2 | -2248 | 2745 |
| USDJPY | 1045 | 34.6 | 1.07 | +5.4 | +5.4 | 33.8 | +1537 | 1643 |
| **XAUUSD** | **982** | **39.6** | **1.51** | **+164.1** | **+164.1** | **19.1** | **+332,220** | **29,640** |

#### s97 Z=2.5 (remote forex claim)

| Pair | Trades | WR% | PF | Total R | Return % | Max DD % | Net Exness pips | Max DD (Exness pips) |
|------|-------:|----:|---:|--------:|---------:|---------:|----------------:|---------------------:|
| GBPUSD | 41 | 82.9 | 2.44 | +11.4 | +11.4 | 1.1 | +929 | 301 |
| AUDUSD | 38 | 57.9 | 0.92 | -1.8 | -1.8 | 4.8 | -64 | 322 |
| EURUSD | 192 | 66.2 | 1.09 | +10.5 | +10.5 | 7.5 | +545 | 935 |
| NZDUSD | 38 | 73.7 | 2.04 | +5.3 | +5.3 | 2.8 | +375 | 125 |
| USDCAD | 39 | 53.9 | 0.88 | -2.8 | -2.8 | 5.2 | -96 | 326 |
| USDCHF | 39 | 61.5 | 1.15 | +3.0 | +3.0 | 4.4 | +117 | 403 |
| USDJPY | 52 | 76.9 | 1.98 | +10.4 | +10.4 | 3.5 | +1377 | 543 |
| XAUUSD | 41 | 53.7 | 1.21 | -5.9 | -5.9 | 8.3 | +9810* | 23870 |

\*Gold Exness-pip totals can look large vs R because stop distances are wide in $0.01 pips; **trust R / return %** for cross-asset comparison.

#### s96 (remote MSS+OB)

| Pair | Trades | WR% | PF | Total R | Return % | Max DD % | Net Exness pips | Max DD (Exness pips) |
|------|-------:|----:|---:|--------:|---------:|---------:|----------------:|---------------------:|
| **GBPUSD** | **24** | **62.5** | **2.69** | **+14.6** | **+14.6** | **2.8** | **+379** | **73** |
| AUDUSD | 20 | 35.0 | 0.74 | -2.6 | -2.6 | 5.3 | -88 | 131 |
| EURUSD | 97 | 34.0 | 0.65 | -21.9 | -21.9 | 22.0 | -606 | 606 |
| NZDUSD | 18 | 16.7 | 0.18 | -13.0 | -13.0 | 13.7 | -273 | 289 |
| USDCAD | 21 | 47.6 | 1.21 | +4.0 | +4.0 | 5.1 | +64 | 142 |
| USDCHF | 21 | 28.6 | 0.46 | -9.2 | -9.2 | 10.6 | -206 | 241 |
| USDJPY | 17 | 29.4 | 0.77 | -1.5 | -1.5 | 7.9 | -72 | 187 |
| XAUUSD | 19 | 26.3 | 0.99 | -8.6 | -8.6 | 8.6 | -90 | 4710 |

### Winner by pair (raw total R)

| Pair | Winner by total R | Live recommendation |
|------|-------------------|---------------------|
| GBPUSD | s96 | **s96** |
| AUDUSD | s97 (least bad) | Avoid / tiny size |
| EURUSD | s98 | Prefer **s97** (PF>1, DD 7.5% vs 63%) |
| NZDUSD | s97 | **s97** |
| USDCAD | s98 | Prefer **s96** (PF 1.21) or s97 caution |
| USDCHF | s97 | **s97** |
| USDJPY | s97 | **s97** |
| XAUUSD | s98 | **s98** |

## Phased windows (from latest data end)

Same fair cost model as above. Each CSV is trimmed to **N days back from that file’s own max timestamp** (anchor ≈ **2026-07-10** from XAUUSD 1H).

| Phase | Days | Typical start → end |
|-------|-----:|---------------------|
| **3m** | 90 | 2026-04-12 → 2026-07-10 |
| **6m** | 180 | 2026-01-11 → 2026-07-10 |
| **1y** | 365 | 2025-07-10 → 2026-07-10 |
| **2y** | 730 | 2024-07-10 → 2026-07-10 |
| **3y** | 1095 | 2023-07-11 → 2026-07-10 |

**Source:** `dashboard/out/full_pair_compare_windows/` (`summary.csv`, `basket.csv`, `best_per_pair.csv`)  
**Harness:** `full_pair_compare_windows.py`

### Basket by phase (8 pairs, parallel $10k / 1% per R books)

| Phase | Strategy | Pairs +R | Trades | Total R | Return % | Avg R/trade | Worst pair DD % |
|-------|----------|----------|-------:|--------:|---------:|------------:|----------------:|
| 3m | s96 | 0 / 8 | 4 | -4.2 | -4.2% | -1.05 | 1.1% |
| 3m | s97 Z=2.5 | 3 / 8 | 8 | +0.4 | +0.4% | +0.06 | 1.0% |
| 3m | **s98** | **3 / 8** | 378 | **+9.2** | **+9.2%** | +0.03 | 11.4% |
| 6m | s96 | 2 / 8 | 6 | -1.4 | -1.4% | -0.24 | 2.1% |
| 6m | s97 Z=2.5 | 4 / 8 | 19 | +1.5 | +1.5% | +0.08 | 2.1% |
| 6m | **s98** | **5 / 8** | 783 | **+46.1** | **+46.1%** | +0.06 | 21.8% |
| 1y | s96 | 4 / 8 | 26 | -6.3 | -6.3% | -0.24 | 4.3% |
| 1y | s97 Z=2.5 | 5 / 8 | 49 | +1.7 | +1.7% | +0.03 | 4.7% |
| 1y | **s98** | **5 / 8** | 1559 | **+97.8** | **+97.8%** | +0.06 | 20.9% |
| 2y | s96 | 2 / 8 | 58 | -19.6 | -19.6% | -0.34 | 9.4% |
| 2y | s97 Z=2.5 | 5 / 8 | 112 | +8.7 | +8.7% | **+0.08** | **5.2%** |
| 2y | **s98** | **7 / 8** | 3171 | **+170.2** | **+170.2%** | +0.06 | 37.2% |
| 3y | s96 | 2 / 8 | 94 | -25.4 | -25.4% | -0.27 | 12.5% |
| 3y | s97 Z=2.5 | 5 / 8 | 181 | +17.4 | +17.4% | **+0.10** | **5.3%** |
| 3y | **s98** | **5 / 8** | 4761 | **+124.4** | **+124.4%** | +0.03 | 49.6% |

**Phased takeaway:** On recent windows, **s98 basket total R** leads every phase (gold + some FX runs), but **s97** stays the better **FX quality** pick (higher avg R/trade, much lower DD on 2y/3y). **s96** remains sparse on short windows; strength shows on **GBPUSD** from 1y+.

### XAUUSD by phase (gold)

| Phase | s96 R | s97 R | **s98 R** | s98 PF | s98 DD % | s98 trades |
|-------|------:|------:|----------:|-------:|---------:|-----------:|
| 3m | 0 | +0.2 | **+13.4** | 1.45 | 3.6% | 45 |
| 6m | 0 | +0.4 | **+36.5** | 1.86 | 2.9% | 84 |
| 1y | 0 | +1.2 | **+77.4** | 1.82 | 3.9% | 169 |
| 2y | -5.3 | -0.8 | **+100.1** | 1.66 | 8.2% | 352 |
| 3y | -4.2 | -3.2 | **+117.1** | 1.60 | 10.4% | 529 |

### Winner by pair × phase (raw total R)

| Pair | 3m | 6m | 1y | 2y | 3y |
|------|----|----|----|----|----|
| GBPUSD | s98* | s98* | **s96** | **s96** | **s96** |
| AUDUSD | s98 | s98 | s98 | s98* | s97* |
| EURUSD | s96* | s98 | s98* | s98* | **s97** |
| NZDUSD | s97* | s97* | s98* | s98* | **s97** |
| USDCAD | **s98** | **s98** | **s98** | **s98** | **s98** |
| USDCHF | s97 | s98 | **s97** | s98* | s98* |
| USDJPY | s97 | s96 | s96 | **s98** | **s98** |
| XAUUSD | **s98** | **s98** | **s98** | **s98** | **s98** |

\*Winner by total R but R ≤ 0 or PF ≤ 1 — treat as least-bad, not a live pick.

## Strategy ID map

| ID | File | Role |
|----|------|------|
| s96 | `strategy_96_mss_ob_tuned.py` | Remote MSS+OB; best on **GBPUSD** |
| s97 | `strategy_97_trend_meanreversion.py` | Remote 4H trend MR; **best FX basket** |
| s98 | `strategy_98_xau_trend_liquidity_trail.py` | Local unified; **best XAUUSD** |

## How to reproduce

```powershell
$env:YT_DATA_ROOT = "O:\D temp\UltimateTradeBot\Data\Exness\structured\history"
# Do NOT set BT_COST_PRICE for mixed FX+gold runs
D:\Python\Python3_12_8\python.exe full_pair_compare_s96_s97_s98.py
# Phased 3m/6m/1y/2y/3y from each CSV's latest bar (optional OUT on D: if O: is full)
$env:YT_WINDOW_OUT = "D:\temp\yt_learning_window_compare"
D:\Python\Python3_12_8\python.exe full_pair_compare_windows.py
D:\Python\Python3_12_8\python.exe audit_exness_pip_model.py
```

Gold-only historical batch (optional override):

```powershell
$env:BT_COST_PRICE = "0.45"
D:\Python\Python3_12_8\python.exe batch_xauusd_backtest.py --exness-cost --windows 365,0
```

## Docs

- `changelog.md` — append-only change log  
- `Q&A.md` — ranking / Exness / compare decisions  
- `dashboard/out/full_pair_compare_s96_s97_s98/` — full-span CSV/JSON  
- `dashboard/out/full_pair_compare_windows/` — phased 3m/6m/1y/2y/3y summaries  

