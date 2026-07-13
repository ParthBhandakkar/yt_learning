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


## 2026-07-13 — Mega compare harness (s106–s139)

**Q:** Why only 5 pairs and 3m+1y instead of full 9-pair / full-history run?
**A:** Pragmatic scope: all s106+ strategies on 1H keeps runtime and D: trim disk use manageable. Five liquid symbols (3 FX + gold + BTC) cover the main use cases. s96 5m stack limited to XAUUSD only (0 trades on 1y gold window in this run).

**Q:** Did any new strategy beat s98 on gold?
**A:** No on 1y: s98 +77R / PF 1.82 / DD 3.9% vs best new s132 Marco trap +67R on XAUUSD and s131 JadeCap FVG +51R. s98 remains gold specialist.

**Q:** Best new FX quality?
**A:** Raw FX3 basket (GBP/EUR/JPY) 1y: s131 +34R. Quality (avg R/trade + DD): s139 NBB (+0.73 R/trade, DD 2.4%) and s115 Umar Punjabi (+0.84 R/trade sparse). Baseline s97 still best risk-adjusted FX basket (+5R, DD 1%).

**Q:** Where are results stored?
**A:** `D:\temp\yt_learning_mega_compare\` (full job dir) and mirrored CSVs/JSON in `dashboard/out/compare_mega_traders/`. Harness: `compare_mega_traders.py`.


## 2026-07-13 — Which strategy where (live vs research)

**Q:** Mega compare shows s131/s132 beating the basket and s99 leading 3m — should live picks change?
**A:** No. **Live core stays s98 (gold) and s97 Z=2.5 (FX quality).** s131/s132 are **research** basket-R leaders (1y); s99/s131 are **research** 3m leaders. s98 basket was weak on 3m (−12R) but still #1 on gold 1y (+77R) — treat 3m as regime caveat, not a gold swap. s115/s139 are sparse high avg-R monitors only. All other s106–s139 proxies remain research library unless listed in README "Which strategy where".

## 2026-07-13 — liveTrade s98 gold deploy

**Q:** How is s98 deployed live on XAUUSD?
**A:** `liveTrade/` with `STRATEGY_ID=s98`, `SYMBOLS=XAUUSD`, `FIXED_LOT=0.01` (margin sizing disabled). MT5 credentials copied from ExnessZeroLLMBOT into gitignored `liveTrade/.env`. Engine scans closed 1H bars, enters at market on signal, manages ATR chandelier trail. Run: `D:\Python\Python3_12_8\python.exe liveTrade/run.py` (or `--check` first). Logs: `liveTrade/logs/engine.log`.

**Q:** How to smoke-test MT5 place/trail/close without disturbing live s98?
**A:** `D:\Python\Python3_12_8\python.exe liveTrade/test_order_lifecycle.py` on the demo account. Uses magic **989898** and comment `smoke_lifecycle` (live s98 uses **980098**), fixed 0.01 lot, long then short: open → modify SL once → close. Aborts on non-demo accounts; cleans up in `finally`. Safe to run while `run.py` is live — engine only manages its own magic and won't trail test tickets.
