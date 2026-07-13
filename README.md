# yt_learning — Strategy Lab

Causal backtests of YouTube/ICT-style strategies on Exness history, with realistic round-turn costs.

## Which strategy where

Mechanical proxies, fair `enrich_trades_pnl`, **1y** windows unless noted. Source: `dashboard/out/compare_mega_traders/` (+ earlier s96/s97/s98 compares).

| Use case | Strategy | Why |
|----------|----------|-----|
| **Gold — live / primary** | **s98** | Still **#1 on XAUUSD** (+77R 1y, best PF/DD among gold leaders). Do **not** swap live gold for new YouTuber proxies. |
| **FX — live / risk-adjusted** | **s97 Z=2.5** | Best **risk-adjusted FX basket** (small DD ~1%, solid avg R/trade). Primary live FX pick. |
| **5-pair basket — research** | **s131**, **s132** | **s131** JadeCap session FVG led 1y basket (+131R); **s132** Marco liquidity trap second (+91R). Raw R leaders — **not** live replacements for s98/s97. |
| **Short window (3m) — research** | **s99**, **s131** | **s99** POS Golden Setup and **s131** led 3m basket; **s98** was weak on 3m basket (−12R) — regime caveat only, **not** a reason to drop s98 on gold. |
| **Sparse high avg-R — research** | **s115**, **s139** | **s115** Umar Asia–London sweep (+0.84 R/trade) and **s139** NBB 8:30 killzone (+0.73 R/trade) — few trades, interesting per-trade quality; monitor only. |

**Live core (unchanged):** **s98** gold, **s97** FX quality, **s96** GBPUSD where noted below.

**Research library only:** all other new YouTuber proxies (**s99–s102**, **s106–s139**) unless listed above — compare and paper-trade; do not treat as live until re-validated on your symbols and costs.

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
| BTCUSD | **0.1** | 1 BTC | $0.10 | Match; RT cost ~$25 price |

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

## YouTuber expansion (s99–s102 vs s96/s97/s98)

New strategies are **mechanical proxies** of discretionary YouTube teachings (not live 1:1 parity). Rules + source video are in each file header (dashboard `Video:` discovery).

| ID | Creator | Mechanical proxy | Source video |
|----|---------|------------------|--------------|
| **s99** | Power of Stocks | Golden Setup: day-open + round-level break, 3R | [Token IQ Part-1](https://www.youtube.com/watch?v=8cbKitkmxFc) |
| **s100** | TopG Traders | 4H BOS + S/D zone tap on 1H, 2R | [Course playlist](https://www.youtube.com/playlist?list=PLwdM5wWQGYyD46U5NBDjOuSV7PyEUJQ81) |
| **s101** | Vinbull Trading Academy | 4H EMA bias + 1H engulf at swing S/R, 2R | [S/R PA](https://www.youtube.com/watch?v=P34rJtjc7kw) |
| **s102** | Trading Techstreet | Prior-day H/L + pin/engulf break-entry scalp, 1.5R | [Gold/crypto analysis](https://www.youtube.com/watch?v=TUurudYuDtg) |
| **s103** | Power of Stocks | **5EMA alert candle** (canonical mechanical; separate from s99) | same channel / 5EMA teaching |
| **s104** | Power of Stocks | Inside-candle breakout (separate from s99/s103) | same channel |
| **s105** | TopG Traders | **CHOCH reversal** (separate from s100 continuation) | same playlist |

**Already covered in lab:** Faiz SMC (most s01–s81). **Gap filled:** the four creators above.

**Markets:** 7 FX + XAUUSD + **BTCUSD** (Exness library).  
**Harness:** `compare_youtuber_strats.py` → `dashboard/out/compare_youtuber_strats/`  
**Windows:** full + 3m / 6m / 1y from data end ≈ **2026-07-10**.

### Basket (9 pairs, parallel $10k / 1% per R)

| Window | s96 | s97 | s98 | **s99** | s100 | s101 | s102 |
|--------|----:|----:|----:|--------:|-----:|-----:|-----:|
| full Total R | -39 | +35 | +95 | **+671** | -210 | -384 | -446 |
| full worst DD% | 22 | **8** | 82 | 96 | 110 | 211 | 165 |
| 1y Total R | -6 | +4 | **+113** | +6 | -5 | -87 | -62 |
| 3m Total R | -4 | +1 | -0 | **+12** | -11 | -32 | +4 |

**Caveats:** s99 full-history basket is inflated by **EURUSD’s long CSV** (same issue as earlier s97/s98 full runs) and very high trade count / drawdown — **not** a quality win. On **1y**, **s98** leads basket total R with better structure than s99.

### XAUUSD (gold)

| Window | Best | Total R | PF | DD% |
|--------|------|--------:|---:|----:|
| full | **s98** | +163 | 1.49 | 19% |
| 1y | **s98** | +77 | 1.82 | 3.9% |
| 6m | **s98** | +37 | 1.86 | 2.9% |
| 3m | **s98** | +13 | 1.45 | 3.6% |

s99 is #2 on gold most windows but with much larger DD than s98.

### BTCUSD

| Window | Best | Total R | Notes |
|--------|------|--------:|-------|
| full | **s99** | +144 | High trade count; DD ~33% |
| 1y | **s98** | +15 | s102 close (+15 R, better PF 1.81) |
| 6m | **s99** | +42 | s102 +10 R / PF 2.19 |
| 3m | **s99** | +27 | — |

### Live vs research (s99–s102)

See **[Which strategy where](#which-strategy-where)** for the live/research split. Summary:

| Use case | Pick | Status |
|----------|------|--------|
| **XAUUSD** | **s98** | **Live** — s99 is #2 on gold but higher DD; not a live replacement |
| **FX basket quality** | **s97 Z=2.5** | **Live** |
| **GBPUSD** | **s96** (full history) | **Live** |
| **3m basket research** | **s99** | **Research** — leads short window; s98 basket weak on 3m (regime caveat) |
| **BTCUSD** | **s99** / **s102** | **Research only** on recent windows |
| **s100 / s101** | — | Negative full baskets — research library only |

## Mega trader expansion (s106–s139 vs s96/s97/s98/s99)

Additional **mechanical proxies** from YouTube/Instagram creators (s106–s139). Rules + source in each file header.

| ID | Creator | Mechanical proxy |
|----|---------|------------------|
| **s106** | Shreya FRX | London-session FVG pullback (4H EMA bias, 1.8R) |
| **s107** | Shreya FRX | Liquidity sweep + post-sweep FVG confluence (2R) |
| **s110** | Thoughts Magic Trading | 4H trendline tap + 1H rejection bounce (2R) |
| **s111** | MambaFX | 48-bar S/R breakout expansion (2R; 5m teaching on 1H) |
| **s112** | Booming Bulls | UTC 00–05 morning range breakout (2R) |
| **s113** | Booming Bulls | Impulse break + retest sniper (2.5R) |
| **s114** | Stock Learners (Gautam Jha) | PDH/PDL sweep + trigger break (2R) |
| **s115** | Umar Punjabi | Asia box London sweep + BOS retest |
| **s120** | Fabio Valentini | IVB NY opening-range breakout (2.5R) |
| **s121** | Fabio Valentini | AMT failed-breakout mean reversion |
| **s122** | TG Capital (Tyler) | London Trident stacked EMA + FVG (3R cap) |
| **s123** | Umar Ashraf | Break-and-hold at PDH/PDL (2.5R) |
| **s124** | Brando (Elite Options) | HTF S/R momentum breakout adapted for FX/gold (3R) |
| **s125** | Trader Kane | Lab model reversal (range/SMT proxy) |
| **s126** | Trader Kane | Lab model continuation |
| **s127** | Trader Kane | PO3 50% manipulation reversal |
| **s130** | JadeCap (Kyle Ng) | Prior-day swing SFP / daily sweep (2R) |
| **s131** | JadeCap (Kyle Ng) | Session liquidity sweep + aligned FVG (2R) |
| **s132** | Marco Trades | Liquidity trap reversal (2R) |
| **s133** | Marco Trades | Impulse zone + CHOCH retest (2R) |
| **s134** | Alex Temiz (AT09) | First-red-day fade after extended rally (2R) |
| **s135** | Alex Temiz (AT09) | Key resistance + lower-high short (2R) |
| **s136** | Andrea Cimi | Cash-session ORB with volume initiative proxy (2R) |
| **s137** | Andrea Cimi | PDH/PDL sweep + reclaim (2R) |
| **s138** | Marci Silfrain | Top-down weekly bias + 1H EMA pullback (2R) |
| **s139** | Omor NBB Trader | 8:30 NY kill-zone PD sweep + FVG (2R) |

**Markets:** GBPUSD, EURUSD, USDJPY, XAUUSD, BTCUSD.  
**Harness:** `compare_mega_traders.py` → `dashboard/out/compare_mega_traders/`  
**Windows:** 3m + 1y from data end ≈ **2026-07-10**. s96 (5m stack) on **XAUUSD only**.

### Basket (5 pairs, parallel $10k / 1% per R)

| Window | Best basket R | Runner-up | s98 | s97 | s99 |
|--------|--------------:|----------|----:|----:|----:|
| **3m** | **s99 +28** | s131 +24 | -12 | +1 | **+28** |
| **1y** | **s131 +131** | s132 +91 | +69 | +9 | +40 |

### XAUUSD — does anything beat s98?

| Window | **s98** | Best new challenger | New R | Notes |
|--------|--------:|--------------------|------:|-------|
| **3m** | +13 (PF 1.45) | s134 Temiz FRD | +20 | Tiny sample (31 trades) |
| **1y** | **+77 (PF 1.82, DD 3.9%)** | s132 Marco trap | +67 | s131 +51 R; **s98 still #1** |

### FX quality (GBPUSD + EURUSD + USDJPY, 1y)

| Strategy | FX3 total R | Avg R/trade | Worst pair DD % |
|----------|------------:|------------:|----------------:|
| **s131** JadeCap FVG | **+34** | +0.30 | 9.1% |
| s139 NBB kill-zone | +10 | **+0.73** | 2.4% |
| s115 Umar Punjabi | +7 | **+0.84** | 2.1% |
| **s97 Z=2.5** (baseline) | +5 | **+0.26** | **1.0%** |
| s98 (baseline) | -23 | -0.04 | 20.9% |

### Live vs research (s106–s139)

See **[Which strategy where](#which-strategy-where)**. Mega compare confirms live core unchanged; new batch wins on **research** axes only:

| Use case | Pick | Status |
|----------|------|--------|
| **XAUUSD** | **s98** | **Live** — +77R 1y; s132 (+67R) and s131 (+51R) closest but did **not** beat s98 |
| **FX basket quality** | **s97 Z=2.5** | **Live** — s131/s139/s115 beat raw R on FX3 but are sparse or higher-variance |
| **5-pair basket R (1y)** | **s131**, **s132** | **Research** — basket leaders, not live replacements |
| **Sparse avg-R (1y FX3)** | **s115**, **s139** | **Research** — high R/trade, low trade count |
| **BTCUSD (1y)** | **s132**, **s99** | **Research only** — high DD |
| **All other s106–s138** | — | **Research library** — not live picks unless re-validated |

## Strategy ID map

| ID | File | Role |
|----|------|------|
| s96 | `strategy_96_mss_ob_tuned.py` | Remote MSS+OB; best on **GBPUSD** |
| s97 | `strategy_97_trend_meanreversion.py` | Remote 4H trend MR; **best FX basket** |
| s98 | `strategy_98_xau_trend_liquidity_trail.py` | Local unified; **best XAUUSD** |
| s99 | `strategy_99_pos_golden_setup.py` | Power of Stocks Golden Setup proxy |
| s100 | `strategy_100_topg_structure_sd.py` | TopG structure S/D continuation proxy |
| s101 | `strategy_101_vinbull_pa_sr.py` | Vinbull PA S/R proxy |
| s102 | `strategy_102_techstreet_level_scalp.py` | Techstreet level scalp proxy |
| s103 | `strategy_103_pos_5ema.py` | Power of Stocks **5EMA** (separate) |
| s104 | `strategy_104_pos_inside_candle.py` | Power of Stocks **inside candle** (separate) |
| s105 | `strategy_105_topg_choch_reversal.py` | TopG **CHOCH reversal** (separate) |
| s106 | `strategy_106_shreya_frx_london_fvg.py` | Shreya FRX London FVG |
| s107 | `strategy_107_shreya_frx_sweep_fvg.py` | Shreya FRX sweep + FVG |
| s110 | `strategy_110_tmt_trendline_bounce.py` | TMT trendline bounce |
| s111 | `strategy_111_mamba_breakout_sr.py` | MambaFX S/R breakout |
| s112 | `strategy_112_bb_morning_range.py` | Booming Bulls morning range |
| s113 | `strategy_113_bb_sniper_retest.py` | Booming Bulls sniper retest |
| s114 | `strategy_114_sl_pdh_sweep.py` | Stock Learners PDH/PDL sweep |
| s115 | `strategy_115_up_asia_london_sweep.py` | Umar Punjabi Asia-London sweep |
| s120 | `strategy_120_fabio_orb_ivb.py` | Fabio IVB ORB |
| s121 | `strategy_121_fabio_amt_meanrev.py` | Fabio AMT mean reversion |
| s122 | `strategy_122_tg_trident.py` | TG Capital Trident |
| s123 | `strategy_123_umar_break_hold.py` | Umar Ashraf break-and-hold |
| s124 | `strategy_124_brando_sr_breakout.py` | Brando S/R momentum breakout |
| s125 | `strategy_125_kane_lab_reversal.py` | Kane Lab reversal |
| s126 | `strategy_126_kane_lab_continuation.py` | Kane Lab continuation |
| s127 | `strategy_127_kane_po3_fifty.py` | Kane PO3 50% |
| s130 | `strategy_130_jadecap_daily_sweep.py` | JadeCap daily sweep |
| s131 | `strategy_131_jadecap_session_fvg.py` | JadeCap session FVG |
| s132 | `strategy_132_marco_liquidity_trap.py` | Marco liquidity trap |
| s133 | `strategy_133_marco_sd_choch.py` | Marco CHOCH retest |
| s134 | `strategy_134_temiz_first_red_day.py` | Temiz first red day |
| s135 | `strategy_135_temiz_level_lower_high.py` | Temiz lower-high short |
| s136 | `strategy_136_cimi_orb.py` | Cimi ORB |
| s137 | `strategy_137_cimi_sweep_reclaim.py` | Cimi sweep reclaim |
| s138 | `strategy_138_silfrain_trend_pullback.py` | Silfrain trend pullback |
| s139 | `strategy_139_nbb_830_killzone.py` | NBB 8:30 kill-zone FVG |

## How to reproduce

```powershell
$env:YT_DATA_ROOT = "O:\D temp\UltimateTradeBot\Data\Exness\structured\history"
# Do NOT set BT_COST_PRICE for mixed FX+gold+BTC runs
D:\Python\Python3_12_8\python.exe full_pair_compare_s96_s97_s98.py
# Phased 3m/6m/1y/2y/3y from each CSV's latest bar (optional OUT on D: if O: is full)
$env:YT_WINDOW_OUT = "D:\temp\yt_learning_window_compare"
D:\Python\Python3_12_8\python.exe full_pair_compare_windows.py
# YouTuber proxies s99-s102 vs baselines (includes BTCUSD)
$env:YT_YOUTUBER_OUT = "D:\temp\yt_learning_youtuber_compare"
D:\Python\Python3_12_8\python.exe compare_youtuber_strats.py
# Mega compare s106-s139 vs s96/s97/s98/s99 (5 pairs, 3m+1y)
$env:YT_MEGA_OUT = "D:\temp\yt_learning_mega_compare"
D:\Python\Python3_12_8\python.exe compare_mega_traders.py
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
- `dashboard/out/compare_youtuber_strats/` — s99–s102 vs s96/s97/s98 (+ BTCUSD)  
- `dashboard/out/compare_mega_traders/` — s106–s139 mega compare (5 pairs, 3m+1y)  

