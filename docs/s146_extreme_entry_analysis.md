# S146 causal extreme-entry analysis

Generated: `2026-08-19T10:16:16.974188+00:00` from read-only MT5 H4/M15/M5 bars and tick/history data.

## Bottom line

The 17 supplied rows matched journal lines 49–65. Exact historical detector replay matched **17/17** signals on zone, model, time, trigger, and stop.
A causally known, qualifying zone more extreme than the logged zone existed in **12/17** cases. This count uses only structure confirmed by the decision time and never uses the later result to choose a zone.
Of the 11 rows with a specific extreme-zone remark, the evidence classified them as `{'partially_supported': 3, 'supported': 7, 'hindsight_or_unavailable': 1}`.
Broker truth differs materially from the manually plotted result labels: **15/17** signals filled, with **4 profits / 11 losses** and net `-6965.78` INR. Manual versus broker sign agreed on **10/15** fills; mismatches were trades `[6, 10, 11, 12, 16]`, while `[7, 8]` did not fill.

The critical distinction is **availability versus execution**: a higher/lower zone can exist at the decision time but remain unfilled, invalidate before its own confirmation, or still lose. Therefore the report does not equate a visually better zone with a tradable winner.

## Predeclared strategy comparisons

All outcomes use constant 1R risk and a 1.25R target. Short exits are evaluated on approximated ASK (MT5 bid bars plus recorded bar spread); long exits use BID. Same-bar ambiguity is stop-first.

| Variant | Cases | Filled | Target first | Stop first | Open/unfilled/other | Sum resolved R | Mean resolved R |
|---|---:|---:|---:|---:|---|---:|---:|
| current_market | 17 | 17 | 7 | 10 | — | -1.25 | -0.073529 |
| current_historical_stop | 17 | 16 | 7 | 9 | unfilled:1 | -0.25 | -0.015625 |
| actual_fill_1_25r_path | 15 | 15 | 4 | 11 | — | -6.0 | -0.4 |
| most_extreme_limit | 17 | 10 | 5 | 5 | unfilled:7 | 1.25 | 0.125 |
| most_extreme_confirmed | 8 | 8 | 2 | 5 | open:1 | -2.5 | -0.357143 |
| extreme_fresh_limit | 10 | 5 | 3 | 2 | unfilled:5 | 1.75 | 0.35 |
| extreme_fresh_confirmed | 5 | 5 | 1 | 3 | open:1 | -1.75 | -0.4375 |
| extreme_external_liquidity_limit | 6 | 3 | 2 | 1 | unfilled:3 | 1.5 | 0.5 |
| extreme_external_liquidity_confirmed | 3 | 3 | 1 | 2 | — | -0.75 | -0.25 |
| best_geometry_limit | 17 | 13 | 4 | 9 | unfilled:4 | -4.0 | -0.307692 |
| best_geometry_confirmed | 10 | 10 | 3 | 6 | open:1 | -2.25 | -0.25 |
| confluence_limit | 17 | 10 | 5 | 5 | unfilled:7 | 1.25 | 0.125 |
| confluence_confirmed | 8 | 8 | 2 | 5 | open:1 | -2.5 | -0.357143 |

## Decision

The evidence supports **testing extreme fresh limit entries**, not blindly replacing newest-first with extreme-first market entries. The broad extreme-fresh limit rule filled only 5 of 10 eligible cases (3 targets, 2 stops, +1.75R resolved); requiring external liquidity filled only 3 of 6 cases (2 targets, 1 stop, +1.5R). That is directionally better than current-market -1.25R, but far too few resolved trades to establish expectancy.
Waiting for each extreme zone's own 5m confirmation did **not** solve the sample: most-extreme confirmed had 2 targets, 5 stops, and 1 still open. Best-R:R geometry was actively harmful (-4R for limit entries). Therefore R:R alone must not choose the zone.
After review, the live detector now enforces a narrower causal rule: a 5m signal is accepted only when its 15m zone is the running still-actionable price extreme since the selected 4H destination confirmed. This keeps the existing 5m confirmation and market execution; it does not deploy the unproven direct-limit variants above. The rule is controlled by `S146_REQUIRE_RUNNING_EXTREME_15M` for immediate rollback.

## Trade-by-trade validation

| # | Symbol | Manual | MT5 P/L | Qualifying/fresh | More extreme | Logged rank | Current market | Extreme limit | Extreme confirmed | Remark verdict |
|---:|---|---|---:|---:|---:|---:|---|---|---|---|
| 1 | GBPUSD short | loss | -2089.79 | 4/2 | 3 | 4 | stop_first | stop_first | open | partially_supported |
| 2 | GBPJPY short | loss | -1060.24 | 2/0 | 1 | 2 | stop_first | target_first | stop_first | supported |
| 3 | AUDUSD short | loss | -1078.5 | 4/0 | 3 | 4 | stop_first | unfilled | no_future_touch | partially_supported |
| 4 | NZDUSD long | profit | 1853.56 | 1/0 | 0 | 1 | target_first | target_first | target_first | — |
| 5 | CADJPY short | profit | 1582.19 | 1/0 | 0 | 1 | target_first | unfilled | no_future_touch | — |
| 6 | USDJPY short | profit | -312.85 | 1/0 | 0 | 1 | stop_first | stop_first | invalidated_before_confirmation | — |
| 7 | CHFJPY short | profit | not filled | 4/3 | 3 | 4 | stop_first | unfilled | no_future_touch | supported |
| 8 | USDCAD long | profit | not filled | 2/1 | 1 | 2 | target_first | unfilled | no_future_touch | — |
| 9 | AUDJPY short | profit | 1891.01 | 2/1 | 1 | 2 | target_first | unfilled | no_future_touch | — |
| 10 | USDCAD long | profit | -1093.31 | 3/2 | 2 | 3 | target_first | target_first | stop_first | supported |
| 11 | NZDJPY short | profit | -1065.69 | 2/1 | 1 | 2 | stop_first | unfilled | no_future_touch | supported |
| 12 | USDCAD long | profit | -1727.81 | 2/1 | 1 | 2 | target_first | target_first | stop_first | — |
| 13 | GBPUSD short | loss | -368.19 | 3/1 | 2 | 3 | stop_first | stop_first | stop_first | partially_supported |
| 14 | AUDUSD short | profit | 796.71 | 4/3 | 3 | 4 | target_first | unfilled | no_future_touch | supported |
| 15 | GBPNZD short | loss | -1764.63 | 1/0 | 0 | 1 | stop_first | stop_first | invalidated_before_confirmation | supported |
| 16 | EURUSD short | profit | -807.97 | 2/1 | 1 | 2 | stop_first | target_first | target_first | supported |
| 17 | NZDCAD long | loss | -1720.27 | 1/0 | 0 | 1 | stop_first | stop_first | stop_first | hindsight_or_unavailable |

## Ranking rules (fixed before outcomes)

- **Most extreme:** highest qualifying supply for shorts; lowest qualifying demand for longs.
- **Extreme fresh:** same ranking, but the zone must not have been touched by decision time.
- **External liquidity:** extreme fresh zone with a confirmed swing/equal-high/equal-low pool beyond it.
- **Best geometry:** largest destination reward divided by structural stop risk.
- **Confluence:** 35% extremity, 25% freshness, 20% external liquidity, 20% capped destination R:R.
- `limit` waits at the zone's proximal edge. `confirmed` waits for that zone's own future causal 5m liquidation/MSS; it does not transfer confirmation from the original, less-extreme zone.

## Hindsight controls and limitations

- Each decision uses rolling live-depth windows and bars closed by that timestamp only.
- Swing levels appear only after their right-side confirmation bars; future mitigation never removes a zone retrospectively.
- The two actual engine restart boundaries are enforced for `S146_REQUIRE_NEW_15M=true`.
- Outcomes are a separate pass after ranking. They never affect candidate eligibility or score.
- This is 17 signals from one day. Unresolved/unfilled cases are not silently discarded, and the highest sample-R variant should not be treated as proven without forward demo validation.
- Five-minute bars cannot resolve exact intrabar order; the stop-first rule is deliberately pessimistic. Tick files around signals and detected extreme fills are exported for inspection.

## Files

- Full machine report: `data\s146_mt5\s146_extreme_entry_analysis.json`
- Flat comparison: `data\s146_mt5\s146_extreme_entry_analysis.csv`
- Native exports and ticks: `data\s146_mt5\extreme_entry`
