# Q&A

## 2026-07-11 ? Leakage audit + XAUUSD ranking

**Q:** Same HTF-close / LTF-open lookahead as Strategy 95 elsewhere?
**A:** Yes ? fixed in strategies 42, 69, 62, 28, 06. Strategy 95 keeps dashboard toggle; ranking used `--strict-mss-causal`.

**Q:** How is ?best? defined?
**A:** Primary = profit factor; tie-break = total pnl_R; require ?20 trades. Full table still reported for all runs.

**Q:** Best on XAUUSD after causal fixes?
**A:** Strategy 13 (3-Step ICT Gold + SMT) leads both 1y and full (PF 5.47, 53 trades). Note: all 53 trades fall in the last ~1y window (full history did not add older trades). Strong full-history runner-up: Strategy 90 (PF 1.98, 95 trades). Robust mid-pack: Strategy 91 (PF 1.37 full, 523 trades).


## 2026-07-12 ? Exness pip + Strategy 96

**Q:** What is Exness XAUUSD pip size?
**A:** 1 pip = $0.01 (second decimal). 1.0 lot = 100 oz; pip value ? $1 per Exness pip. Framework metals pip remains $1 for legacy batch comparability; Exness pips = framework ? 100.

**Q:** What cost to use for Exness Standard-style gold?
**A:** Round-turn $0.45 price (~45 Exness pips) via BT_COST_PRICE=0.45 (avg spread ~20?35 Exness pips + slippage).

**Q:** Which new strategy for best returns?
**A:** Strategy 96 default: 4H EMA bias + 1H reclaim OR Donchian + ATR trail, no session filter. Full: PF 1.50 / +160R / 982 trades. 1y: PF 1.97 / +80R. Reclaim-only has higher PF but lower total PnL.


## 2026-07-12 ? Full Exness re-batch

**Q:** Re-run every strategy with the same Exness cost model as s96?
**A:** Yes. atch_xauusd_backtest.py --exness-cost --no-resume ? atch_xauusd_exness_summary.csv. Cost $0.45 RT. s96 leads total Exness-pip PnL (full 327,260 / 1y 239,030). s13 leads PF (5.45, 53 trades). s28 timed out/failed.


## 2026-07-13 ? Remote live s96/s97 vs local

**Q:** New strategies on origin/live ? how do they do on XAUUSD under Exness costs?
**A:** Fetched strategy_96_mss_ob_tuned.py (MSS+OB tuned) and strategy_97_trend_meanreversion.py. Local unified strategy renumbered to s98 to avoid ID clash. Same BT_COST_PRICE=0.45. On XAUUSD: remote s96 ? 0 trades (1y) / 19 trades PF 0.93 full; remote s97 ? PF 0.74?0.77 and net loss (author noted XAUUSD soft). Local s98 still leads total returns.


## 2026-07-13 ? s98 vs remote s97 forex basket

**Q:** How does our gold-best s98 compare to remote s97 on the 8-pair forex basket it claims?
**A:** Fair 1x-cost re-run on Exness data. s97 Z=2.5 best cross-pair quality (+30.6R basket, +0.067R/trade, 5/8 pairs). s98 wins only via XAUUSD (+164R); FX-only about -94R. Use s97 for FX basket, s98 for gold.


## 2026-07-13 ? Exness pip/lot match per pair

**Q:** Do our backtests use the same pip/contract conventions as Exness for each pair?
**A:** Pip SIZE now matches via EXNESS_INSTRUMENT_SPECS in core.py (FX 0.0001, JPY 0.01, XAU 0.01 broker / $1 framework). Contract sizes documented (FX 100k, gold 100 oz). We do **not** simulate lot inventory ? PnL is price/R based. Fixed prior USDJPY heuristic bug (0.1 ? 0.01). Never set one BT_COST_PRICE across gold+FX. Run udit_exness_pip_model.py to verify.

## 2026-07-13 ? Phased windows from latest bar

**Q:** Besides full history, can we see recent 3m / 6m / 1y / 2y / 3y results from the newest data?
**A:** Yes. Anchor ~2026-07-10 (XAUUSD 1H max). Windows: 90/180/365/730/1095 days back per CSV. s98 leads basket total R every phase (gold-heavy); s97 remains best FX quality (avg R/trade + low DD on 2y/3y); s96 shines on GBPUSD from 1y+. See README phased section + dashboard/out/full_pair_compare_windows/.


## 2026-07-13 ? YouTuber strategy expansion

**Q:** Can we encode Power of Stocks / TopG / Vinbull / Techstreet and compare to our baselines including BTC?
**A:** Yes ? as mechanical proxies (discretionary teachings compressed to causal rules). Files s99?s102 with Video: links for the dashboard. BTCUSD Exness pip=0.1, RT ~$25. Fair compare on 7 FX + XAU + BTC (full/3m/6m/1y). Results: s98 still best on gold; s97 best FX quality; s99 leads raw full basket R but with huge DD and EURUSD-length inflation ? not a live pick; s100/s101 lose on full history; BTC exploratory edge to s99/s102 on recent windows. Repo was previously almost all Faiz SMC.

**Q:** What is the Power of Stocks Golden Setup mechanical rule we used?
**A:** UTC day open ? nearest round band (BTC 500 / XAU 10 / FX 0.005) ? bias from open vs mid ? 1H close break of bias-side round ? next-open fill ? SL ~0.4*step (ATR floor) ? 3R TP; max 2/day. Source: Token IQ Part-1 + Golden Setup playlist.


## 2026-07-13 ? Keep creator variants as separate files

**Q:** Should Power of Stocks Golden Setup and 5EMA be one rewritten strategy?
**A:** No ? keep each as its own file (s99 Golden, s103 5EMA, s104 inside candle) and compare them. Same for TopG: s100 continuation S/D vs s105 CHOCH reversal. More strategies, fair comparison ? do not overwrite one variant with another.


## 2026-07-13 ? Fabio / TG / Umar / Brando / Kane expansion (s120?s127)

**Q:** How were orderflow-heavy traders (Fabio, Kane) proxied without tick data?
**A:** Volume spike vs 10-bar avg substitutes aggression; FVG violation substitutes iFVG; 4H H/L sweeps substitute liquidity grabs. ES/NQ SMT divergence is **not** modeled on single-instrument CSVs ? Kane s125?s127 use sweep + range/PO3 structure only. Not 1:1 parity with their futures orderflow platforms.

**Q:** Why multiple files per creator?
**A:** Same rule as s99/s103 and s100/s105: Fabio s120 ORB vs s121 AMT mean reversion; Kane s125 reversal vs s126 continuation vs s127 PO3 50%. Compare separately ? do not merge variants.

**Q:** How was Brando (options, no stops) adapted for FX/gold?
**A:** s124 uses daily swing + psychological round levels, NY-open momentum candle, and a **hard SL** beyond the level (Brando's "size for zero" is a sizing philosophy, not backtestable without options premium decay). Catalyst/news filter omitted ? levels + momentum only.

**Q:** TG Capital Trident on 1H only?
**A:** s122 approximates Tyler's 30M Trident with 1H stacked EMAs + FVG + doji wick into 50% zone during London window 07?11 UTC; daily EMA(200) bias. True 1:20+ RR targets not encoded ? default 3R cap.


## 2026-07-13 ? YouTuber batch 2 (s110-s115)

**Q:** How were Thoughts Magic Trading / MambaFX / Booming Bulls / Stock Learners / Umar Punjabi encoded?
**A:** One primary mechanical proxy each (Booming Bulls gets two files: s112 morning range vs s113 sniper retest). All use causal 1H rules (next-bar open fills, confirmed swings, no future peek). Lower-TF teachings (Mamba 5m/1m, Gautam 1m, Umar 5m BOS) are approximated on 1H unless a dedicated CSV TF is provided later.

**Q:** Why only s110-s115 and not s110-s119?
**A:** Five creators yielded six documented distinct setups after research; IDs 116-119 reserved for future variants (e.g. Booming Bulls forex trend PDF, Mamba NY-open-only filter, TMT session FCC box) if we add them later.

**Q:** Key mechanical rules per file?
**A:** s110: 4H rising trendline from last two swing lows ? 1H tap + bullish rejection ? 2R. s111: 48-bar 3-touch S/R ? expansion breakout close ? 2R. s112: UTC 00-05 narrow box ? post-05:15 UTC breakout ? 2R, 1/day. s113: 2??ATR impulse break ? retest broken level ? 2.5R. s114: PDH/PDL sweep + trigger break ? opposite PD or 2R. s115: Asia 00-06 UTC box ? London 07-10 sweep ? BOS retest ? Asia mid/2R.


## 2026-07-13 ? YouTuber batch 3 (s130-s139)

**Q:** How were JadeCap / Marco Trades / Alex Temiz / Andrea Cimi / Marci Silfrain / Omor NBB encoded?
**A:** Ten separate causal files (two each for JadeCap, Marco, Temiz, Cimi; one each for Silfrain and NBB). Equity tape, footprint, and small-cap setups are proxied on 1H OHLCV for FX/XAU/BTC. Each file has Source + Video for dashboard discovery.

**Q:** Why two files per creator where applicable?
**A:** Same rule as s99/s103 and s100/s105: distinct public setups stay in separate files for fair comparison, not merged variants.

**Q:** Key mechanical rules per file?
**A:** s130: prior-day swing SFP at NY window ? 2R. s131: Asia/London level sweep + aligned FVG ? 2R. s132: respected swing liquidity trap ? 2R. s133: impulse zone + sweep CHOCH + zone retest ? 2R. s134: 4+ bullish 1H streak then first red close fade ? 2R. s135: daily resistance tag + lower-high short ? 2R. s136: UTC-13 ORB break with volume initiative proxy ? 2R. s137: PDH/PDL sweep + reclaim ? 2R/opposite PD. s138: weekly EMA bias + 1H EMA pullback ? 2R. s139: 8:30 NY window (UTC 12-14) PD sweep + bias-aligned FVG ? 2R.

**Q:** Marci Silfrain spelling?
**A:** Public sources use Marci Silfrain (also seen as Silfrain alone). Robbins Cup / Words of Rizdom interview is the primary source for s138.


## 2026-07-13 ? Shreya FRX (s106/s107)

**Q:** Instagram reels blocked or no transcript ? what rules were encoded?
**A:** Reel captions + hashtags were fetched (USDCAD/NZDUSD/EURUSD, London session, FVG, ICT/SMC, RR ~1.5?2R). Profile (LinkedIn @shreya-aa25881b0) confirms FX/gold day trader using liquidity, market structure, SMC. No dedicated YouTube course found; rules proxied from reel tags + standard ICT liquidity-sweep/FVG sequence.

**Q:** Why two files instead of one?
**A:** Reels emphasize pure FVG London entries (s106) and broader SMC sweep?FVG confluence (s107 hashtags #ict #smc #fvg). Split matches repo convention (separate setups, not merged variants).

**Q:** Session and RR defaults?
**A:** s106: UTC 07:00?10:00 London, 1.8R (reel +1.92R / +1.8R). s107: sweep 07?10, entry window to 12:00, 2R, SL beyond sweep extreme. Both use 4H EMA(50) bias and max 1 trade/day.


## 2026-07-13 â€” Mega compare harness (s106â€“s139)

**Q:** Why only 5 pairs and 3m+1y instead of full 9-pair / full-history run?
**A:** Pragmatic scope: all s106+ strategies on 1H keeps runtime and D: trim disk use manageable. Five liquid symbols (3 FX + gold + BTC) cover the main use cases. s96 5m stack limited to XAUUSD only (0 trades on 1y gold window in this run).

**Q:** Did any new strategy beat s98 on gold?
**A:** No on 1y: s98 +77R / PF 1.82 / DD 3.9% vs best new s132 Marco trap +67R on XAUUSD and s131 JadeCap FVG +51R. s98 remains gold specialist.

**Q:** Best new FX quality?
**A:** Raw FX3 basket (GBP/EUR/JPY) 1y: s131 +34R. Quality (avg R/trade + DD): s139 NBB (+0.73 R/trade, DD 2.4%) and s115 Umar Punjabi (+0.84 R/trade sparse). Baseline s97 still best risk-adjusted FX basket (+5R, DD 1%).

**Q:** Where are results stored?
**A:** `D:\temp\yt_learning_mega_compare\` (full job dir) and mirrored CSVs/JSON in `dashboard/out/compare_mega_traders/`. Harness: `compare_mega_traders.py`.


## 2026-07-13 â€” Which strategy where (live vs research)

**Q:** Mega compare shows s131/s132 beating the basket and s99 leading 3m â€” should live picks change?
**A:** No. **Live core stays s98 (gold) and s97 Z=2.5 (FX quality).** s131/s132 are **research** basket-R leaders (1y); s99/s131 are **research** 3m leaders. s98 basket was weak on 3m (âˆ’12R) but still #1 on gold 1y (+77R) â€” treat 3m as regime caveat, not a gold swap. s115/s139 are sparse high avg-R monitors only. All other s106â€“s139 proxies remain research library unless listed in README "Which strategy where".

## 2026-07-13 â€” liveTrade s98 gold deploy

**Q:** How is s98 deployed live on XAUUSD?
**A:** `liveTrade/` with `STRATEGY_ID=s98`, `SYMBOLS=XAUUSD`, `FIXED_LOT=0.01` (margin sizing disabled). MT5 credentials copied from ExnessZeroLLMBOT into gitignored `liveTrade/.env`. Engine scans closed 1H bars, enters at market on signal, manages ATR chandelier trail. Run: `D:\Python\Python3_12_8\python.exe liveTrade/run.py` (or `--check` first). Logs: `liveTrade/logs/engine.log`.

**Q:** How to smoke-test MT5 place/trail/close without disturbing live s98?
**A:** `D:\Python\Python3_12_8\python.exe liveTrade/test_order_lifecycle.py` on the demo account. Uses magic **989898** and comment `smoke_lifecycle` (live s98 uses **980098**), fixed 0.01 lot, long then short: open â†’ modify SL once â†’ close. Aborts on non-demo accounts; cleans up in `finally`. Safe to run while `run.py` is live â€” engine only manages its own magic and won't trail test tickets.

## 2026-07-13 â€” s98 wide SL / ~Rs6000 risk on 0.01 lot

**Q:** Live s98 SHORT showed SL ~4122 on entry ~3994 (~128 pts) and MT5 ~Rs6000 risk on 0.01 lot â€” bug or intended?
**A:** **Intended by strategy design, problematic for fixed-lot live.** Donchian breakout SHORT uses `max(structural swing high, entry + 1.5Ã—ATR)` as stop. Setup was `donchian_breakout`; structural stop beyond prior 20-bar high (~4122) is correct per backtest logic. Backtests size at **~1% of equity per 1R** (`full_pair_compare_s96_s97_s98.py`); `FIXED_LOT=0.01` does **not** scale down when SL is wide â€” so ~128 pts Ã— ~Rs47/pt â‰ˆ **Rs6000** (~64% of Rs9.3k demo) is consistent math, not an MT5 display glitch.

**Q:** Was the entry a stale signal after power-cut restart?
**A:** **Partially.** Signal bar was the correct latest closed 1H (`16:00 UTC`, closed `17:00`). Engine restarted at `17:50 UTC` (~50 min after the backtest fill window at next 1H open). SL was computed from that bar's structure, not from a hours-old wrong bar â€” but **late restart entry** diverges from backtest timing. Fix: `S98_MAX_ENTRY_DELAY_SEC` (default 900s) + `MAX_RISK_INR` / `MAX_RISK_PCT` guards skip such entries on restart.

**Q:** What to do with an open position that already has a wide SL?
**A:** Trailing tightened SL (4122 â†’ ~4053) per ATR chandelier; still large vs equity. New guards do not retroactively fix open tickets â€” **consider manual close** if risk is unacceptable, then **restart** `run.py` after pulling the update so guards apply to future entries.


## 2026-07-13 ï¿½ Live risk % band

**Q:** Keep late-entry guard but don't cap risk at only 2%?
**A:** Yes. Root issue on the wide-SL short was mainly **~50 min late entry after restart**, not the SL formula. Keep `S98_MAX_ENTRY_DELAY_SEC=900`. Risk cap moved to **MAX_RISK_PCT=15** (user band 5ï¿½20%); `MAX_RISK_INR=0` so the old Rs500 floor does not undercut the %. Extreme ~60% equity SL distances are still blocked; moderate structural stops within ~15% of balance can trade.


## 2026-07-14 ï¿½ BTCUSD leakage-free tournament (s140)

**Q:** Can we find a BTCUSD strategy with ~1.0ï¿½1.2 million net price points in the recent year like s98 did for gold?
**A:** We ran a staged, leakage-free tournament (dev / select / sealed final). Metric = cumulative BTCUSD price points after $25 RT per leg. Position modes compared: single and pyramid_max_3 (no unlimited stacking). Final window fixed to **13 Jul 2025 ï¿½ 12 Jul 2026**.

**Q:** What was frozen as s140?
**A:** 	rend_donchian_atr on **1d**, pyramid_max_3, donchian=40, trend_len=100, atr_mult_init=2.5, atr_mult_trail=3.5, HTF bias on. Select-year net **+67,395** points (9 trades, PF 4.84) vs buy&hold **+59,431**.

**Q:** Did the sealed holdout hit the 1Mï¿½1.2M target?
**A:** **No.** Final net **-14,574** points (19 trades, PF 0.74, max DD 33,124). hit_low=false, hit_high=false. Cost stress at $40/$60 remained negative. Champion beat final buy&hold (-53,243) but still lost money in points.

**Q:** Why not switch to the challenger that was positive on final?
**A:** Protocol forbids retuning after opening the sealed year. Read-only, 
egime_hybrid 6h printed up to **+22,866** on that same final slice ï¿½ still far below 1M and **not** a new champion without a fresh sealed test. Promoting it now would be selection bias.

**Q:** Is s140 live-ready?
**A:** **No.** Research only. Live gold remains **s98**. Do not deploy s140 to liveTrade/.

**Q:** Where are artifacts?
**A:** dashboard/out/btc_strategy_tournament/ (selection_manifest.json, inal_holdout_summary.json, trades/equity). Harness: tc_strategy_tournament.py.

**Q:** How does the BTC feature store enforce causality?
**A:** Every feature row carries `event_time`, `available_time`, `source`, `lag_seconds`, and per-column `{name}_missing` flags. Joins use `pandas.merge_asof(..., direction='backward')` only. `assert_causal()` verifies all joined `available_time` columns are ? `decision_time`. Scalers/imputers fit on the training window only (`train_end` split before `transform_preprocess`).

## 2026-07-14 ï¿½ BTCUSD round-two research

**Q:** Did round two produce a deployable s141?
**A:** **No.** `status=no_deployable_champion`. No candidate passed DSR (>=0.80) and concentration gates together. `strategy_141_*.py` was intentionally not created.

**Q:** What was the best experimental candidate?
**A:** `squeeze_1d` (`squeeze_expansion`, `pyramid_max_3`): OOS net **+155,044** @ $25 RT / **+153,439** @ $40, PF 1.60, 107 trades, 8/13 positive quarterly paths, PBO 0.083. Failed: DSR 0.52 and ~91% regime profit concentration.

**Q:** How close to the 1.0ï¿½1.2M point target?
**A:** Still far. Best OOS ~155k under 1-BTC exposure and capped pyramiding ï¿½ roughly 6ï¿½8x below the aspirational band. Round two confirms the target is not reachable without changing the exposure contract.

**Q:** Was s140 modified?
**A:** **No.** s140 and its artifacts remain the failed round-one sealed experiment.

**Q:** What about live trading?
**A:** Unchanged. Live gold remains **s98**. Do not deploy BTC research configs to `liveTrade/`.

**Q:** Where are artifacts / how to reproduce?
**A:** `dashboard/out/btc_round2/`. Harness: `btc_round2_tournament.py` stages ingest/build-features/screen/validate/freeze/replay. Use project `.venv`.


## 2026-07-15 ? BTCUSD round-three (no leverage; stacking)

**Q:** Why no leverage in round three?
**A:** Strategy selection must be capital-independent. Each unit is exactly 1 BTC; leverage is a later capital decision. Pyramiding/order-stacking (many concurrent 1-BTC units) is allowed and was swept unconstrained.

**Q:** Did academic sleeves (seasonality / ITSM / turn-of-candle / funding) work?
**A:** Seasonality, ITSM, and turn-of-candle were net negative after $25 RT on Exness BTCUSD. Funding carry on 4h was the best new sleeve (~+48k OOS single-unit) but failed DSR. Round-2 survivors still led the board.

**Q:** Best stacked result without leverage?
**A:** Raw max 1y: `r2_tsm48_1h` + grid_scale_in_unlimited ? **+368k** points at peak **91** units (DD ~1.0M). Sane ranked: `r2_crash_short_6h` grid unlimited ? **+195k** at peak 50. Gap to 1M/yr ? **632k**.

**Q:** Was s141 frozen?
**A:** **No.** `status=no_deployable_champion`. Zero sleeves passed DSR?0.80. Artifacts in `dashboard/out/btc_round3/`.

**Q:** What closes the gap?
**A:** Under a strict 1-BTC-unit / no-leverage contract, 1M/yr requires either a much higher per-unit edge (expected move ? $25 RT at high frequency) or accepting very large peak concurrent BTC notional. Cross-sleeve merging of correlated survivors destroyed value (net negative).

## 2026-07-15 - BTCUSD round-four (single-unit novel alpha)

**Q:** Why drop pyramiding in round four?
**A:** Round three showed stacking inflated points via correlated heat (peak 91 units) without closing the 1M gap. Round four isolates whether a single 1-BTC unit can earn robust novel alpha.

**Q:** Was s141 frozen?
**A:** **Yes.** `status=frozen` -> `strategy_141_btc_adaptive_ensemble.py` = `regswitch_xwide_1d` (1d Donchian 80 + trend 150 + ATR trail 7.0 + crash overlay). First candidate to clear full gates (DSR 0.814, PBO 0.091, PF 2.21, 78 OOS trades).

**Q:** Did it hit 1.0-1.2M points/year?
**A:** **No.** Champion 1y ~ **+75.6k**; best experimental 1y ~ **+77.3k** (`regswitch_wide_1d`, DSR fail). Gap ~ **924k**. Freeze is for robustness, not target attainment.

**Q:** How did the four tracks fare?
**A:** Track 4 (max-capture regime-switch) dominated (~142k multi-year OOS). Track 2 lead-lag weak; Track 3 ML sparse; Track 1 real aggTrades microstructure deeply negative after $25 RT.

**Q:** Live deploy?
**A:** **No.** Research freeze only. Live gold remains **s98**. Do not deploy s141 to `liveTrade/`.

**Q:** Artifacts?
**A:** `dashboard/out/btc_round4/`. Harness: `btc_round4_tournament.py`. Canvas: `btc-round4-research.canvas.tsx`.

## 2026-07-15 - BTCUSD round-five (ceiling + novel single-unit)

**Q:** Where is research data stored now?
**A:** Default cache moved to `O:\D temp\Data\btc_research_data` (D: was full). Override with `BTC_RESEARCH_DATA_ROOT`. Temp extracts use the same disk via `_research_temp_dir()`.

**Q:** Can single-unit hit 1.0-1.2M points/year?
**A:** Only if trading fine bars with a high capture rate. Perfect-foresight bar ceiling: 1m ~5.95M/yr (need ~17%), 1h ~1.54M (need ~65%), 1d ~393k (**impossible**). Swing ceilings are only ~7-11k/yr.

**Q:** Was s142 frozen?
**A:** **No.** `status=no_deployable_champion`. Novel families failed gates. Best experimental `r4_xwide_1d` ~+60.5k 1y (PBO~0.37, trades 58).

**Q:** What about the ~1.2M lead-lag screen result?
**A:** Artifact from joining Binance 15m onto Exness 1m/5m. Fixed by refusing coarse-to-fine joins; retested lead-lag ~0.

**Q:** Does real-IS PBO change s141?
**A:** Round-four PBO used synthetic IS (`OOS*1.05`). Round-five uses real train-window nets; shared PBO ~0.37 would have failed s141-style candidates. Keep s141 as historical freeze; do not treat it as re-validated.

**Q:** Artifacts?
**A:** `dashboard/out/btc_round5/`. Harness: `btc_round5_tournament.py`. Canvas: `btc-round5-research.canvas.tsx`.

## 2026-07-15 - BTCUSD round-six (frontier exhaustion)

**Q:** What was wrong with the R5 swing ceiling (~7?11k/yr)?
**A:** Bug: zig-zag retraced from the last pivot instead of the running extreme, collapsing legs to ~threshold size. Round six rewrote it; corrected 1m swing best ? **8.78M**/yr @ 2ï¿½RT.

**Q:** Did engine exits and event labeling get fixed?
**A:** Yes. `simulate_legs` now honors `max_hold_bars`, `giveback_frac`, `breakeven_at_R`, and `flip` (SAR). `simulate_event_outcomes()` labels events independently for ML training (portfolio simulation still used for OOS PnL).

**Q:** Was the 1M/yr target reached?
**A:** **No.** Three full screen?validate loops; 0 eligible. Best experimental `r4_xwide_1d` ~**+66.4k** 1y (below s141 ~76k). Capture vs 1h bar ceiling ? **4.3%**. Gap ? **934k**.

**Q:** Was s142 frozen?
**A:** **No.** `status=no_deployable_champion`. Best eligible would need to beat s141 and pass gates; none did.

**Q:** Is the gap irreducible?
**A:** Under the hard constraints (1 BTC unit, no pyramid/leverage, $25 RT, round-2 gates), yes for the family classes explored. Ceiling math still says 1m?1h are the only feasible arenas; every high-frequency approach died on churn cost, and sparse approaches cannot capture enough of the 1h move.

**Q:** Data completeness?
**A:** `scripts/ingest_btc_round6.py` filled missing agg months (2026-07 archive not published yet), Binance futures 1m klines 2021-07..2026-07, and ETHUSDT 1h. Manifest: `dashboard/out/btc_round6/ingest_manifest.json`.

**Q:** Artifacts?
**A:** `dashboard/out/btc_round6/`. Harness: `btc_round6_tournament.py`. Candidates: `btc_round6_candidates.py`. Canvas: `btc-round6-research.canvas.tsx`.

## 2026-07-15 - BTCUSD round-seven (derivatives + correctness audit)

**Q:** What engine/gate bugs did round seven fix?
**A:** (1) Flip and `max_hold` closes were labeled `"open"`, so `summarize_legs` dropped their PnL from screen OOS while fold nets counted them ? now classified win/loss/breakeven by net; `"open"` only for end-of-data force-close. (2) Breakeven ratchet applied on the same bar it armed ? now deferred to next bar. (3) Meta gate `min(min_proba, median)` passed ~half of signals ? replaced with cost-aware EV gate. (4) Shared-grid PBO alone could fail unrelated families ? added per-family PBO; gate uses `max(shared, family)`.

**Q:** Did new free derivatives data close the gap?
**A:** **No.** Binance Vision 5m metrics (OI, L/S, taker), Binance perp?spot basis, DVOL, Fear & Greed produced small honest positives (`oi_sq_z2_4h` ~+11k OOS, `ls_contr_4h` ~+9k, `jump_1h` ~+12k 1y, basis fade ~+15k OOS) but all failed DSR/PF/$40 and/or PBO. Best remains `r4_xwide_1d` ~**+66.4k** 1y.

**Q:** What about the ~1M+ basis_fade print early in loop 1?
**A:** Artifact: Exness close joined to Binance spot as ?basis?. Fixed to **Binance futures vs Binance spot only**, with advanced-bucket zeroing and 2h metrics staleness. Honest fade collapsed to ~+15k OOS.

**Q:** Does lower RT ($5/$10) unlock the target?
**A:** Informational only. `r4_xwide_1d` 1y moves 66.8k ? 66.7k ? 66.4k across $5/$10/$25 ? daily champion is not cost-bound. Freeze decisions stay at $25.

**Q:** Was s142 frozen?
**A:** **No.** `status=no_deployable_champion`. 0 eligible across 3 loops; best below s141 (~76k) and ~15ï¿½ below 1M.

**Q:** Artifacts?
**A:** `dashboard/out/btc_round7/`. Ingest: `scripts/ingest_btc_round7.py`. Harness: `btc_round7_tournament.py`. Candidates: `btc_round7_candidates.py`. Canvas: `btc-round7-research.canvas.tsx`.


## BTC Round-8 (cost truth, KB, backlog, synth, alpha)

**Q:** Is the $25 RT cost real?
**A:** It was a **hardcoded conservative estimate** in `core.py` (`BTCUSD.rt_cost_price=25`). Read-only MT5 probe (demo) + published floors: **Raw ˜ $14 RT**, Zero ˜ $18.8, Standard ˜ $10. Freeze gates stay $25/$40 for cross-round comparability; measured RT is a secondary reporting axis.

**Q:** Was overnight swap modeled before?
**A:** **No.** Probe shows swap mode=points, long ˜ **-$13.02/night/1 BTC** (triple Friday), short ˜ 0. Engine now supports `holding_cost_per_night` / swap long-short. On `r4_xwide_1d`, Raw without swap ˜ 61.8k 1y; with swap ˜ **59.8k** 1y. Swap-free Extended accounts would avoid this debit.

**Q:** Is Binance basis vs Exness execution a bug?
**A:** **No for features.** Same-venue feature construction (Binance futures vs Binance spot / premium index) + Exness execution is correct. Mixing Exness close with Binance spot as “basis” was the R7 artifact and remains forbidden.

**Q:** Did the knowledge base get built?
**A:** **Yes.** `research_kb/` with append-only `experiments.jsonl`, CLI (`add/search/render/validate/stats`), rendered `KNOWLEDGE.md`, R1–R7 backfill, and auto-append from round-8 `report`.

**Q:** Were past untested ideas closed?
**A:** **Yes.** Screened: VPIN, Hawkes, Lee–Mykland jump, DOW, funding-window v2, IV-RV, on-chain, imbalance bars, bandit, R2 dynamic ensemble, R3 funding carry, premium dislocation. **Parked with reasons:** symbolic regression, options skew/term, Kalman/wavelet/fracdiff/entropy, true Cont–Kukanov OFI, real liquidationSnapshot, macro-liquidity.

**Q:** Did MarketSimulator synthetic data help?
**A:** **No.** TSTR + mixed augmentation vs bootstrap/GARCH: **verdict rejected**. Synthetic train looks strong; real-test proxy trend stays negative (~-8.8k). Lack of BTC calibration is the main risk.

**Q:** Did Round-8 beat s141?
**A:** On 1y **yes experimentally**: `r2_ens_1h` ~**76.9k** vs s141 ~75.6k (+~1.4k). It still **fails gates** (PF 1.093, DSR ~0.33, family PBO ~0.92). No eligible champion; **no s142**. Gap to 1M ˜ **923k**.

**Q:** Does lower measured RT unlock 1M/yr?
**A:** **No.** Ensembles improve at $5/$10 (info-only cost table) but still far below target; daily champions barely move with RT. Binding limits remain single-unit capacity + robustness gates, not the $25 estimate alone.

**Q:** Artifacts?
**A:** `dashboard/out/btc_round8/`. Probe/ingest/cost scripts under `scripts/`. Harness: `btc_round8_tournament.py`. Candidates: `btc_round8_candidates.py`. Synth: `btc_synth_experiment.py`. KB: `research_kb/`. Canvas: `btc-round8-research.canvas.tsx`.

## BTC Round-9 (exhaustive TA + ML + dual track)

**Q:** What did Round-9 fix from Round-8?
**A:** Causal frozen regimes for long 1h/15m series (no same-bar-sign proxy), wired month/regime concentration gates, causal feature z-scores, validation always loads regimes for stratification, swap reporting axis. R8 tops re-verified; baseline ~76.9k 1y.

**Q:** Was the classic-TA / youtuber / paper universe actually screened?
**A:** **Yes.** Candlesticks, chart/harmonic/Elliott/Wyckoff proxies, Ichimoku, pivots, VWAP/value-area, OBV/VSA/S-D/Fib, HA/Renko, seasonality, quarter-hour imbalance, Asia-open trend, MSS/FVG/ORB/PDH/CHOCH/zMR/first-red, plus LightGBM meta and optional gplearn. Artifacts under `dashboard/out/btc_round9/`.

**Q:** Did anything beat the R8 baseline on 1y?
**A:** **No (honest re-run).** Best experimental is `qhi_1h` ~**72.4k** 1y (~4.5k under baseline ~76.9k). Near leaders: `fvg_stack_proxy_1h` ~66k, `qhi_15m` ~66k, `qhi_mom_15m` ~51k. All fail PF/DSR/family PBO and/or regime concentration ? **0 eligible**. (Pre-fix `elliott_15m` ~120.9k was inflated by zigzag lookahead and is discarded.)

**Q:** Did the portfolio track reach 1M?
**A:** **No.** Honest greedy 12-sleeve decorrelated sum ? **394k** 1y (optimistic concurrent 1-BTC sleeves). Portfolio PF ok; mean DSR and portfolio PBO ~0.72 fail ? `no_portfolio_champion`. Gap still ? **606k**.

**Q:** Was s142 frozen?
**A:** **No.** `status=no_deployable_champion`. Live gold remains s98.

**Q:** Artifacts?
**A:** `dashboard/out/btc_round9/`. Harness: `btc_round9_tournament.py`. Candidates/features/ML: `btc_round9_*.py`. Canvas: `btc-round9-research.canvas.tsx`. KB auto-appended `r9-tournament-no_deployable_champion-2026-07-16`. Pre-fix numbers archived as `*_pre_causality_fix.*`.

**Q:** Are the first Round-9 freeze numbers (elliott_15m ~120.9k) fully trustworthy?
**A:** **No ? superseded.** Causality audit fixed zigzag confirm_idx, ML label cutoff, and PDH/pivot prior-day OHLC. Honest 3-loop re-tournament completed 16-Jul-2026: no deployable classic or portfolio champion; trust `report_summary.json` / leaderboard after the fix, not the provisional elliott_15m freeze.

## 2026-07-17 — Other developer said s98 is not profitable. True?

**Q:** `drawdown_analysis_verified.md` says retire s98 and that s97-gold is strongest. Is that right?
**A:** **No for the real strategy.** Independent replay (`verify_drawdown_claims.py`):
- Real **s98 ATR trail** on XAUUSD: full **+162.6R / PF 1.49 / DD 19%**; 1y **+82.3R / PF 1.97 / DD 4.9%** (Exness costs).
- Real **s97 Z=2.5** on gold (actual exits): full **-5.5R** — not a gold champion.
- Their tables race **fixed 100/200p targets + fixed stops** on s98-like entries. That overlay is **not** s98 as coded/live; on our 1m first-touch race it is already **negative expectancy** even before realistic gold cost.
- Their **10% risk** ruin warning is **valid** even on the profitable real R-stream (flat 10% blows by trade ~121). Backtests assume **1%/R**; live should stay near **1–2%** risk, not 10–15%.

Full write-up: `drawdown_analysis_response.md`. Summary JSON: `dashboard/out/verify_drawdown_claims/verification_summary.json`.
