# KronosTest — can Kronos improve Strategy 146?

An isolated study that asks one question: **does adding a Kronos candle forecast
to S146's own signal features improve risk-adjusted, out-of-sample results?**

Nothing here writes outside this folder. The existing project is read-only from
here: `liveTrade` and `backtests/s146_running_extreme` primitives are imported,
and the checked-in replay artifacts are read, never modified. liveTrade's engine
logger is redirected into `logs/` so even its log lines stay local.

## The bar to beat

From the checked-in one-year replay `s146-20260820T091500Z` (1,167 trades):

| Split | Period | Trades | Net R | Win rate | Avg R |
|---|---|---:|---:|---:|---:|
| train | to 2026-03-31 | 700 | −134.75 | 35.9% | −0.1925 |
| test | after 2026-03-31 | 467 | −14.01 | 41.5% | −0.0300 |
| all | 1 year | 1,167 | −148.75 | 38.1% | −0.1275 |

S146 as replayed loses money, and the losses are concentrated in the earlier
period. The test half is close to breakeven, so "beat the baseline" on test is a
much lower bar than the headline −148.75R suggests.

## Method

Kronos cannot be trained on a strategy — it only predicts candles. So there are
two separate trainings, kept strictly apart:

1. **Kronos learns the market** (self-supervised, no trade labels).
2. **A small model learns which trades to take**, using S146 signal features plus
   features derived from Kronos forecasts. Only 1,167 labels exist, so this model
   is deliberately tiny (logistic regression / shallow GBDT).

Kronos becomes a filter by sampling: for each signal we draw many forecast paths
and replay S146's own stop and target along each, giving an estimated
probability the trade works in the same R units the replay uses.

## Phases

| Phase | Script | What it does |
|---|---|---|
| 0 | `src/phase0_check.py` | env, GPU, Kronos import, input availability |
| 1 | `src/phase1_fetch.py` | read-only deep MT5 history, 29 symbols x 4h/15m/5m |
| 2 | `src/phase2_dataset.py` | labelled dataset + leakage audit + fixed split |
| 3 | `src/phase3_baseline.py` | filter using **only** S146 features (the real bar) |
| 4 | `src/phase4_forecast.py` | Kronos sampled paths -> `k_*` features |
| 5a | `src/phase5_corpus.py` | fine-tune corpus, truncated before the test period |
| 5b | `src/phase5_finetune.py` | fine-tune tokenizer then predictor |
| 6 | `src/phase6_evaluate.py` | s146-only vs kronos-only vs combined, plus controls |

Support modules: `paths.py`, `features_s146.py`, `evaluation.py`,
`kronos_runner.py`, `multi_symbol_dataset.py`.

## Setup

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install torch==2.7.0 --index-url https://download.pytorch.org/whl/cu126
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe src\phase0_check.py
```

`.venv\pip.ini` pins PyPI only, because the machine's user-level pip config
points at a dead `pypi.ngc.nvidia.com` index. The user config is left untouched.

## Data reality (measured, not assumed)

The MT5 terminal reports `maxbars = 100000`, which caps intraday history:

| Timeframe | Fetched per symbol | Limited by |
|---|---:|---|
| 4h | ~15,650 (11.0y) | broker depth (~12.6y available) |
| 15m | ~79,390 (3.2y) | terminal maxbars |
| 5m | ~82,100 (1.1y) | terminal maxbars |

**Five years of 5m is not obtainable** without raising "Max bars in chart" in the
terminal and restarting it, which would interrupt the running live engine. So the
split was designed around what exists: fine-tune on everything before
2026-03-31, evaluate only on trades after it.

## Results

### Forecast quality on held-out bars (Phase 8, 350 windows after the cutoff)

This is the cleanest measurement, because it has thousands of samples rather
than hundreds of trades.

| Model | Normalized MAE | Naive "no change" | Skill vs naive | Directional acc. | Terminal coverage |
|---|---:|---:|---:|---|---:|
| zero-shot | 1.805 | 0.694 | **−1.60** | 0.45–0.51 | 18% |
| fine-tuned | 1.325 | 0.694 | **−0.91** | 0.46–0.50 | 16% |

Fine-tuning cut MAE by 27%, so it genuinely worked. Both models are still far
worse than predicting no change, and directional accuracy is a coin flip.

### Forecast vs realised outcome (Phase 6)

Correlation between Kronos's headline signal and realised R, all 1,167 trades:

| Feature | Zero-shot Pearson | Fine-tuned Pearson |
|---|---:|---:|
| `k_p_target_first` | −0.005 | −0.019 |
| `k_expected_r` | −0.024 | −0.028 |
| `k_mean_terminal_r` | −0.015 | −0.029 |

Zero, and marginally negative. Adding Kronos to the S146 features made test net R
worse in every model (zero-shot delta −17.21R / −3.82R; fine-tuned −8.14R / −9.00R).

### Significance (Phase 7, 300 iterations per null)

The decisive finding. A single shuffled-label control gave −18.67R in Phase 3 and
+12.71R in Phase 6 — a ~31R swing from pure noise. Against proper nulls:

| Filter | Kept | Observed | Null mean ± sd | p (subset) | p (perm) |
|---|---:|---:|---|---:|---:|
| s146_only logistic | 154 | +11.33R | −3.2 ± 12.8 | 0.140 | 0.090 |
| s146_only gbdt | 151 | +0.83R | −4.2 ± 11.7 | 0.363 | 0.310 |
| kronos_only gbdt | 151 | +3.97R | −4.2 ± 11.7 | 0.263 | 0.237 |
| combined logistic | 99 | −5.88R | −2.0 ± 10.2 | 0.667 | 0.533 |
| combined gbdt | 151 | −2.98R | −4.2 ± 11.7 | 0.457 | 0.400 |

**Every filter is indistinguishable from noise.** With 467 test trades and ~30%
kept, the null standard deviation is ~12R, so the headline +11.33R is under one
standard deviation above chance. That width, not the headline, is the story.

### Conclusion

Kronos does not improve Strategy 146. Zero-shot it has no information about these
trades; fine-tuned it forecasts better but still cannot beat "no change" and
still has no directional edge. The apparent improvement from S146's own features
is also not statistically real on this sample.

The one durable, intuitive signal is cost: the single best cut in Phase 3 was
simply **low spread at signal time** (+16.30R, 30.6% kept), which beat all three
fitted models and matches the project's own note that S146's edge is thin and
cost-sensitive. That is worth pursuing; Kronos is not.

## Guardrails

* Features come only from `detector_signals.jsonl` signal-time fields. Fill and
  outcome fields are in an explicit denylist and audited in
  `data/dataset/s146_signals_audit.json`.
* Entry/stop/target used inside forecasts are signal-time values (trigger,
  effective stop, 4h destination), never the realised fill.
* Context bars must **close** at or before the signal timestamp.
* Thresholds are chosen on the train split only.
* A **shuffled-label control** runs in every evaluation phase. If it shows edge,
  the harness is broken and every other number is void.
* Bootstrap intervals on mean R accompany every result, because a few hundred
  trades move net R by tens of R on luck alone.

## Known limitations

* Rows are the 1,167 trades the portfolio gates already admitted. Skipping a
  trade would in reality free a slot for another signal; that second-order
  portfolio effect is not modelled.
* Forecast paths use a plain stop/target barrier, not the live ladder (partial at
  1.25R, breakeven, 0.5R rungs with giveback), so `k_expected_r` is not directly
  comparable to `label_r`.
* Outcomes inherit the replay's assumptions: native-bar OHLC, stop-first on
  same-bar ambiguity, spread-side approximation.
* Future bar timestamps are taken from the real calendar so session gaps are
  respected. Only timestamps are used, never future prices.
