# s146 trade-path analysis

Generated: `2026-08-18T10:33:21.981643+00:00`. Sample: 16 signals; 9 actual MT5 fills and 7 rejected counterfactuals.

## Verdict

The data supports **directional potential**, not yet a proven profitable strategy. From the signal reference, 8 / 16 paths eventually reached +1.25R in the available export, but only 4 reached it before the original stop without same-bar ambiguity. Of 12 paths that touched -1R, 4 later reached +1.25R after a subsequent bar.

Actual broker truth was 3 profitable and 6 losing fills, net `4191.19` account-currency units. However, the largest winner contributed `7545.91`; excluding it, the other eight fills netted `-3354.72`. The positive total is therefore highly concentrated, not evidence of stable expectancy. Five signals failed with `No money`; two failed with `Invalid stops`. Those seven are opportunity analysis only, not MT5 performance. Also, 3 filled positions exited at an SL materially different from the initial broker SL without a recorded `ladder_stop_moved` event. Their broker P/L therefore includes manual or unjournaled management and is not a clean test of current code.

Coverage is only `2026-08-17T00:00:00+00:00` through `2026-08-18T10:10:00+00:00`. 16 / 16 signal paths have less than 24 hours after entry, so late signals are right-censored and no 72-hour expectancy claim is possible.

## Per-trade evidence

`Fixed` is conservative 5m first-passage at the logged trigger/structural stop. `Recovery` means price touched the stop, then reached +1.25R on a later candle.

| # | Symbol | Model | Stop basis | Execution | Hours | Fixed 1.25R | MFE R | MAE R | Stop then +1.25R | Stop needed to survive to +1.25R | Broker P/L |
|---:|---|---|---|---|---:|---|---:|---:|---|---:|---:|
| 1 | USDCAD long | conservative_mss | liquidity_pool_plus_buffer | actual_mt5_fill | 22.92 | stop_first | 3.0871 | -2.8406 | True | 2.8406 | -876.19 |
| 2 | GBPUSD short | aggressive_liquidation | swept_wick_plus_buffer | rejected_counterfactual | 22.0 | stop_first | 7.441 | -2.3493 | True | 2.3493 | — |
| 3 | EURUSD short | aggressive_liquidation | liquidity_pool_plus_buffer | actual_mt5_fill | 20.92 | target_first | 3.3277 | -0.6553 | False | 0.6553 | 1494.09 |
| 4 | NZDJPY short | aggressive_liquidation | liquidity_pool_plus_buffer | actual_mt5_fill | 20.42 | target_first | 3.8284 | -0.9437 | False | 0.9437 | 7545.91 |
| 5 | AUDUSD short | aggressive_liquidation | liquidity_pool_plus_buffer | rejected_counterfactual | 19.75 | target_first | 3.6239 | -0.4737 | False | 0.4737 | — |
| 6 | GBPAUD long | aggressive_liquidation | liquidity_pool_plus_buffer | actual_mt5_fill | 19.17 | stop_first | 0.7532 | -1.3063 | False | None | 30.72 |
| 7 | AUDJPY short | aggressive_liquidation | liquidity_pool_plus_buffer | actual_mt5_fill | 17.25 | stop_first | 0.8064 | -1.5935 | False | None | -249.62 |
| 8 | CADJPY short | aggressive_liquidation | liquidity_pool_plus_buffer | actual_mt5_fill | 16.5 | stop_first | 1.243 | -3.6303 | False | None | -1032.93 |
| 9 | NZDCHF short | aggressive_liquidation | liquidity_pool_plus_buffer | actual_mt5_fill | 16.25 | stop_first | 5.8817 | -1.4486 | True | 1.4486 | -1369.69 |
| 10 | AUDNZD short | aggressive_liquidation | 15m_zone_distal_plus_buffer | actual_mt5_fill | 15.83 | stop_first | 0.6868 | -8.573 | False | None | -453.03 |
| 11 | GBPCAD long | aggressive_liquidation | liquidity_pool_plus_buffer | rejected_counterfactual | 15.58 | target_first | 1.6446 | -3.7541 | False | 0.4437 | — |
| 12 | EURAUD long | aggressive_liquidation | 15m_zone_distal_plus_buffer | rejected_counterfactual | 15.25 | stop_first | 1.3981 | -1.5405 | True | 1.5405 | — |
| 13 | USDCAD long | aggressive_liquidation | liquidity_pool_plus_buffer | rejected_counterfactual | 12.83 | stop_first | 0.7022 | -1.7595 | False | None | — |
| 14 | GBPJPY short | aggressive_liquidation | 15m_zone_distal_plus_buffer | rejected_counterfactual | 11.83 | stop_first | 0.9152 | -4.6826 | False | None | — |
| 15 | CHFJPY short | conservative_mss | liquidity_pool_plus_buffer | actual_mt5_fill | 11.83 | stop_first | 0.3391 | -4.3205 | False | None | -898.07 |
| 16 | CADJPY short | aggressive_liquidation | liquidity_pool_plus_buffer | rejected_counterfactual | 11.75 | stop_first | 0.7966 | -7.7484 | False | None | — |

## Predeclared alternative simulations

Results below are in constant-money-risk R. Censored trades are excluded from means; therefore these are diagnostics, not comparable final backtests.

| Variant | Resolved | Mean R | Sum R | Outcomes |
|---|---:|---:|---:|---|
| Original stop + full exit at 1.25R | 16 | -0.4375 | -7.0 | stop_first=12, target_first=4 |
| 1.25x stop, lot reduced, target 1.25 widened-R | 16 | -0.4375 | -7.0 | stop_first=12, target_first=4 |
| 1.25x stop, lot reduced, keep original TP | 16 | -0.5 | -8.0 | stop_first=12, target_first=4 |
| 1.5x stop, lot reduced, target 1.25 widened-R | 15 | -0.4 | -6.0 | stop_first=11, target_first=4, censored=1 |
| 1.5x stop, lot reduced, keep original TP | 15 | -0.3889 | -5.8335 | stop_first=10, target_first=5, censored=1 |
| Current ladder: 50% at 1.25R | 16 | -0.4219 | -6.75 | stop=16 |
| Diagnostic ladder: 50% at 1.0R | 16 | -0.3594 | -5.75 | stop=16 |

## What to change first

1. **Fix execution viability before tuning the signal.** Size from current free margin and broker order checks, not a static margin allocation. This is not reintroducing the removed account-risk cap; it prevents valid signals from becoming `No money`. Normalize/validate SL and TP against broker `stops_level`, current bid/ask, digits, and freeze level immediately before submission; retry an `Invalid stops` entry only with corrected broker-valid levels while preserving the structural stop for management.
2. **Do not conclude that every stopped trade merely needed a wider stop.** Use the per-trade `required_stop_multiple` evidence. Demo-test only the predeclared 1.25x and 1.5x variants with lot size divided by the same multiplier. Never widen the stop while keeping lots unchanged.
3. **Keep live fills and backtests aligned.** The current live engine enters at market after the confirmation close, while the historical strategy uses a next-bar resting stop. Build one execution model and evaluate it with spread/slippage. The actual-fill path table deliberately starts at the next complete bar to avoid using pre-fill prices.
4. **Treat the ladder as unproven.** Compare full 1.25R, the current 1.25R partial, and a 1R partial over a much larger sample. A partial can improve hit-rate and reduce variance while lowering expectancy when runners fail. Do not choose it from these 16 trades alone.
5. **Add portfolio controls.** Several simultaneous shorts share USD/JPY/GBP/AUD exposure and reuse the same 4H destination. Cap aggregate currency-direction exposure and permit one active campaign per destination zone; this addresses correlated loss clusters without filtering on minimum stop distance.
6. **Log exact execution evidence.** Persist order request/result, ticket-to-signal linkage, every partial and SL modification, and tick bid/ask around entry/exit. The current reconciliation links fills by nearest symbol/direction because `trade_opened` linkage is absent.

## What not to change from this sample

Do not optimize many confirmation delays, stop multipliers, partial levels, or symbol exclusions against one trading day. Do not remove broker SL/TP, disable `S146_DEMO_ONLY`, add the declined minimum-stop-distance filter, or interpret eventual direction as tradable expectancy.

## Method and limitations

- Actual fills use MT5 entry price/time and the logged original structural stop. Rejected signals use trigger at signal close and are explicitly counterfactual.
- For actual fills, analysis begins at the next full 5m candle. Same-candle fill-to-extreme paths are intentionally excluded.
- MT5 rate bars are bid-based. Long exits use bid OHLC; short exits approximate ask by adding the bar spread times inferred FX point (0.001 JPY pairs, otherwise 0.00001). Tick history is required for exact sequencing.
- If stop and target are touched in one candle, stop is assumed. Ladder simulations also check the active stop before favorable movement, avoiding optimistic intrabar ordering.
- The 1.25x/1.5x simulations report both a target re-anchored to 1.25 widened-R and the original TP. Lots are reduced by the stop multiplier to preserve money risk. They do not include commissions or slippage.
- Actual MT5 realized P/L remains the source of truth; OHLC simulations diagnose paths only.
