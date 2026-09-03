# s146 MT5 reconciliation

Generated: 2026-08-18T10:12:29.560972+00:00 | magic: `1460146`

MT5 is the source of truth for actual fills, broker exits, and realized P/L. The manual result column reflects the supplied chart/table result.

| # | Symbol | Dir | Manual result | 5m bars: manual levels | MT5 status/result | Entry UTC | Entry price | Exit price | Realized P/L | Exit/comment | Match |
|---:|---|---|---|---|---|---|---:|---:|---:|---|---|
| 1 | USDCAD | long | Profit | stop_first | closed / Loss | 2026-08-17T11:15:18+00:00 | 1.38609 | 1.38548 | -876.19 | [sl 1.38548] | False |
| 2 | GBPUSD | short | Profit | stop_first | no MT5 match / — | — | — | — | — | — | — |
| 3 | EURUSD | short | Profit | target_first | closed / Profit | 2026-08-17T13:15:17+00:00 | 1.15951 | 1.15876 | 1494.09 | [sl 1.15876] | True |
| 4 | NZDJPY | short | Profit | target_first | closed / Profit | 2026-08-17T13:45:17+00:00 | 94.19200000000001 | 93.837 | 7545.91 | [tp 93.83700] | True |
| 5 | AUDUSD | short | Profit | target_first | no MT5 match / — | — | — | — | — | — | — |
| 6 | GBPAUD | long | Profit | stop_first | closed / Profit | 2026-08-17T15:00:19+00:00 | 1.9058200000000003 | 1.90585 | 30.72 | [sl 1.90585] | True |
| 7 | AUDJPY | short | Profit | stop_first | closed / Loss | 2026-08-17T16:55:27+00:00 | 113.337 | 113.351 | -249.62 | [sl 113.35100] | False |
| 8 | CADJPY | short | Profit | stop_first | closed / Loss | 2026-08-17T17:40:16+00:00 | 114.965 | 115.023 | -1032.93 | [sl 115.023] | False |
| 9 | NZDCHF | short | Loss | stop_first | closed / Loss | 2026-08-17T17:55:26+00:00 | 0.47856000000000004 | 0.47888 | -1369.69 | [sl 0.47888] | True |
| 10 | AUDNZD | short | Profit | stop_first | closed / Loss | 2026-08-17T18:20:22+00:00 | 1.2037900000000001 | 1.20406 | -453.03 | [sl 1.20406] | False |
| 11 | GBPCAD | long | Loss | target_first | no MT5 match / — | — | — | — | — | — | — |
| 12 | EURAUD | long | Loss | stop_first | no MT5 match / — | — | — | — | — | — | — |
| 13 | USDCAD | long | Profit | stop_first | no MT5 match / — | — | — | — | — | — | — |
| 14 | GBPJPY | short | Loss | stop_first | no MT5 match / — | — | — | — | — | — | — |
| 15 | CHFJPY | short | Profit | stop_first | closed / Loss | 2026-08-17T22:20:26+00:00 | 196.58 | 196.665 | -898.07 | [sl 196.665] | False |
| 16 | CADJPY | short | Profit | stop_first | no MT5 match / — | — | — | — | — | — | — |
