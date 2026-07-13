# Q&A

## 2026-07-11 ‚Äî Leakage audit + XAUUSD ranking

**Q:** Same HTF-close / LTF-open lookahead as Strategy 95 elsewhere?
**A:** Yes ‚Äî fixed in strategies 42, 69, 62, 28, 06. Strategy 95 keeps dashboard toggle; ranking used `--strict-mss-causal`.

**Q:** How is ‚Äúbest‚Äù defined?
**A:** Primary = profit factor; tie-break = total pnl_R; require ‚â•20 trades. Full table still reported for all runs.

**Q:** Best on XAUUSD after causal fixes?
**A:** Strategy 13 (3-Step ICT Gold + SMT) leads both 1y and full (PF 5.47, 53 trades). Note: all 53 trades fall in the last ~1y window (full history did not add older trades). Strong full-history runner-up: Strategy 90 (PF 1.98, 95 trades). Robust mid-pack: Strategy 91 (PF 1.37 full, 523 trades).


## 2026-07-12 ó Exness pip + Strategy 96

**Q:** What is Exness XAUUSD pip size?
**A:** 1 pip = $0.01 (second decimal). 1.0 lot = 100 oz; pip value ò $1 per Exness pip. Framework metals pip remains $1 for legacy batch comparability; Exness pips = framework ◊ 100.

**Q:** What cost to use for Exness Standard-style gold?
**A:** Round-turn $0.45 price (~45 Exness pips) via BT_COST_PRICE=0.45 (avg spread ~20ñ35 Exness pips + slippage).

**Q:** Which new strategy for best returns?
**A:** Strategy 96 default: 4H EMA bias + 1H reclaim OR Donchian + ATR trail, no session filter. Full: PF 1.50 / +160R / 982 trades. 1y: PF 1.97 / +80R. Reclaim-only has higher PF but lower total PnL.


## 2026-07-12 ó Full Exness re-batch

**Q:** Re-run every strategy with the same Exness cost model as s96?
**A:** Yes. atch_xauusd_backtest.py --exness-cost --no-resume ? atch_xauusd_exness_summary.csv. Cost $0.45 RT. s96 leads total Exness-pip PnL (full 327,260 / 1y 239,030). s13 leads PF (5.45, 53 trades). s28 timed out/failed.


## 2026-07-13 ó Remote live s96/s97 vs local

**Q:** New strategies on origin/live ó how do they do on XAUUSD under Exness costs?
**A:** Fetched strategy_96_mss_ob_tuned.py (MSS+OB tuned) and strategy_97_trend_meanreversion.py. Local unified strategy renumbered to s98 to avoid ID clash. Same BT_COST_PRICE=0.45. On XAUUSD: remote s96 ? 0 trades (1y) / 19 trades PF 0.93 full; remote s97 ? PF 0.74ñ0.77 and net loss (author noted XAUUSD soft). Local s98 still leads total returns.


## 2026-07-13 ó s98 vs remote s97 forex basket

**Q:** How does our gold-best s98 compare to remote s97 on the 8-pair forex basket it claims?
**A:** Fair 1x-cost re-run on Exness data. s97 Z=2.5 best cross-pair quality (+30.6R basket, +0.067R/trade, 5/8 pairs). s98 wins only via XAUUSD (+164R); FX-only about -94R. Use s97 for FX basket, s98 for gold.


## 2026-07-13 ó Exness pip/lot match per pair

**Q:** Do our backtests use the same pip/contract conventions as Exness for each pair?
**A:** Pip SIZE now matches via EXNESS_INSTRUMENT_SPECS in core.py (FX 0.0001, JPY 0.01, XAU 0.01 broker / $1 framework). Contract sizes documented (FX 100k, gold 100 oz). We do **not** simulate lot inventory ó PnL is price/R based. Fixed prior USDJPY heuristic bug (0.1 ? 0.01). Never set one BT_COST_PRICE across gold+FX. Run udit_exness_pip_model.py to verify.

## 2026-07-13 ‚Äî Phased windows from latest bar

**Q:** Besides full history, can we see recent 3m / 6m / 1y / 2y / 3y results from the newest data?
**A:** Yes. Anchor ~2026-07-10 (XAUUSD 1H max). Windows: 90/180/365/730/1095 days back per CSV. s98 leads basket total R every phase (gold-heavy); s97 remains best FX quality (avg R/trade + low DD on 2y/3y); s96 shines on GBPUSD from 1y+. See README phased section + dashboard/out/full_pair_compare_windows/.

