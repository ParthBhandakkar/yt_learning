# s146 stop forensics (tick level)

Generated: `2026-08-18T11:53:42.500648+00:00`. Source: MT5 tick history, magic `1460146`.

## What the ticks show

- Stop triggers reconstructed from ticks: 10 of 16 trades.
- Stops where the market itself never reached the structural level (spread-only stop-outs): **3**.
- Median spread as a share of the structural risk: **21.4%** (max 94.3%).
- Median structural risk vs 5m ATR before the signal: **3.21x**.

A short's stop fires on ASK, but the stop level was derived from BID bar highs, so a short is stopped roughly one spread earlier than the level intended. Long stops fire on BID, the same basis as the bars, so longs do not have this bias.

## Per-trade evidence

| # | Symbol | Dir | Basis | Risk (points) | Spread at entry | Spread % of risk | Risk / 5m ATR | Market hit stop? | Spread-only stop | Broker P/L |
|---:|---|---|---|---:|---:|---:|---:|---|---|---:|
| 1 | USDCAD | long | actual_fill | 61.4 | 0.000140 | 22.8 | 3.25 | True | False | -876.19 |
| 2 | GBPUSD | short | counterfactual_trigger | 50.6 | 0.000100 | 19.8 | 1.38 | True | False | — |
| 3 | EURUSD | short | actual_fill | 75.3 | 0.000080 | 10.6 | 1.95 | False | None | 1494.09 |
| 4 | NZDJPY | short | actual_fill | 101.7 | 0.014000 | 13.8 | 3.16 | False | None | 7545.91 |
| 5 | AUDUSD | short | counterfactual_trigger | 66.6 | 0.000090 | 13.5 | 2.06 | False | None | — |
| 6 | GBPAUD | long | actual_fill | 214.5 | 0.000210 | 9.8 | 3.21 | False | None | 30.72 |
| 7 | AUDJPY | short | actual_fill | 88.7 | 0.011000 | 12.4 | 5.07 | False | None | -249.62 |
| 8 | CADJPY | short | actual_fill | 58.3 | 0.011000 | 18.9 | 2.88 | False | True | -1032.93 |
| 9 | NZDCHF | short | actual_fill | 32.4 | 0.000140 | 43.2 | 3.49 | False | True | -1369.69 |
| 10 | AUDNZD | short | actual_fill | 56.0 | 0.000180 | 32.1 | 3.00 | False | None | -453.03 |
| 11 | GBPCAD | long | counterfactual_trigger | 84.3 | 0.000180 | 21.4 | 3.35 | True | False | — |
| 12 | EURAUD | long | counterfactual_trigger | 79.3 | 0.000170 | 21.4 | 4.44 | True | False | — |
| 13 | USDCAD | long | counterfactual_trigger | 69.9 | 0.000457 | 65.5 | 4.41 | True | False | — |
| 14 | GBPJPY | short | counterfactual_trigger | 63.6 | 0.060000 | 94.3 | 1.45 | True | False | — |
| 15 | CHFJPY | short | actual_fill | 84.8 | 0.040000 | 47.2 | 3.48 | False | True | -898.07 |
| 16 | CADJPY | short | counterfactual_trigger | 40.5 | 0.033727 | 83.3 | 2.28 | True | False | — |

### Spread-only stop-outs

- **CADJPY short** (#8): stop `115.023250875`, ask reached `115.02400` while bid only reached `115.01300`; spread at trigger `0.011000`.
- **NZDCHF short** (#9): stop `0.47888394300000003`, ask reached `0.47890` while bid only reached `0.47876`; spread at trigger `0.000140`.
- **CHFJPY short** (#15): stop `196.66483275`, ask reached `196.66500` while bid only reached `196.64400`; spread at trigger `0.021000`.

## Spread-padded stop simulation (constant money risk)

The short stop is padded by `mult x entry spread`; lots are divided by the same stop multiplier so the money at risk is unchanged. Target stays at the original 1.25R distance.

| Pad multiple | Target first | Stop first | Censored | Sum money-R | Mean money-R |
|---|---:|---:|---:|---:|---:|
| 0.0x spread | 4 | 12 | 0 | -7.0 | -0.4375 |
| 1.0x spread | 4 | 12 | 0 | -7.42 | -0.4637 |
| 1.5x spread | 5 | 11 | 0 | -5.838 | -0.3649 |
| 2.0x spread | 5 | 11 | 0 | -6.084 | -0.3803 |

## Cost gate simulation (the fix that is being adopted)

Skip a signal when the live spread is more than the stated share of the intended risk. Money-R is net of a round-trip spread and held at constant money risk.

| Variant | Kept | Wins | Sum net money-R | Mean net money-R | Skipped trades |
|---|---:|---:|---:|---:|---|
| cost<=None_pad0.0x_tp1.25R | 16 | 4 | -8.185 | -0.5116 | — |
| cost<=None_pad0.0x_tp1.5R | 16 | 4 | -7.185 | -0.4491 | — |
| cost<=None_pad1.5x_tp1.25R | 16 | 5 | -7.426 | -0.4641 | — |
| cost<=None_pad1.5x_tp1.5R | 16 | 5 | -6.393 | -0.3996 | — |
| cost<=0.35_pad0.0x_tp1.25R | 11 | 4 | -3.185 | -0.2896 | 9, 13, 14, 15, 16 |
| cost<=0.35_pad0.0x_tp1.5R | 11 | 4 | -2.185 | -0.1987 | 9, 13, 14, 15, 16 |
| cost<=0.35_pad1.5x_tp1.25R | 11 | 4 | -3.66 | -0.3327 | 9, 13, 14, 15, 16 |
| cost<=0.35_pad1.5x_tp1.5R | 11 | 4 | -2.779 | -0.2526 | 9, 13, 14, 15, 16 |
| cost<=0.3_pad0.0x_tp1.25R | 10 | 4 | -2.185 | -0.2185 | 9, 10, 13, 14, 15, 16 |
| cost<=0.3_pad0.0x_tp1.5R | 10 | 4 | -1.185 | -0.1185 | 9, 10, 13, 14, 15, 16 |
| cost<=0.3_pad1.5x_tp1.25R | 10 | 4 | -2.66 | -0.266 | 9, 10, 13, 14, 15, 16 |
| cost<=0.3_pad1.5x_tp1.5R | 10 | 4 | -1.779 | -0.1779 | 9, 10, 13, 14, 15, 16 |
| cost<=0.25_pad0.0x_tp1.25R | 10 | 4 | -2.185 | -0.2185 | 9, 10, 13, 14, 15, 16 |
| cost<=0.25_pad0.0x_tp1.5R | 10 | 4 | -1.185 | -0.1185 | 9, 10, 13, 14, 15, 16 |
| cost<=0.25_pad1.5x_tp1.25R | 10 | 4 | -2.66 | -0.266 | 9, 10, 13, 14, 15, 16 |
| cost<=0.25_pad1.5x_tp1.5R | 10 | 4 | -1.779 | -0.1779 | 9, 10, 13, 14, 15, 16 |
| cost<=0.2_pad0.0x_tp1.25R | 7 | 3 | -1.008 | -0.144 | 1, 9, 10, 11, 12, 13, 14, 15, 16 |
| cost<=0.2_pad0.0x_tp1.5R | 7 | 3 | -0.258 | -0.0369 | 1, 9, 10, 11, 12, 13, 14, 15, 16 |
| cost<=0.2_pad1.5x_tp1.25R | 7 | 3 | -1.482 | -0.2118 | 1, 9, 10, 11, 12, 13, 14, 15, 16 |
| cost<=0.2_pad1.5x_tp1.5R | 7 | 3 | -0.852 | -0.1217 | 1, 9, 10, 11, 12, 13, 14, 15, 16 |

## Limitations

- 16 signals from one trading day; treat every number as descriptive.
- Counterfactual rows never reached the broker and are labelled as such.
- The simulation uses 5m bars for path resolution after the tick-verified stop question, assumes stop-before-target inside a bar, and excludes commission and slippage.
- Padding widens risk in price terms, so lots must fall to keep money risk constant. This is not a minimum stop-distance signal filter: no signal is rejected.
