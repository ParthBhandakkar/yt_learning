# S146 destination-capability entry analysis

Generated `2026-08-29T20:58:37.178735+00:00` from the read-only archive. No MT5 connection or live-code change was made.

## Exact question tested

A trade is a destination-capable entry only when price reaches the logged `signal_record.destination_target` before the original broker stop. The actual fill and original attached stop are used. The fill-containing partial 5m bar is excluded; ties in later bars are stop-first.

## Baseline

- Resolved: **84**; destination first: **15**; stop first: **69**.
- Destination reach rate: **17.9%** (95% Wilson interval 11.1–27.4%).
- Holding unchanged to destination/original stop: **-10.81R**, expectancy **-0.129R** per resolved trade.

## Best exploratory pattern

`spread_pct_of_risk<=15 AND destination_width_r<=1 AND destination_origin_to_bos_bars_4h<=4`

Meaning: spread at entry is no more than 15% of initial risk; the selected 4H zone is no wider than 1R; and its break of structure confirmed within four 4H bars (16 hours) of the origin candle.

- Keeps **12** resolved trades: **7** reached destination and **5** hit the original stop first (**58.3%** precision).
- Captures **7/15** destination-capable trades; the excluded cohort was **11.1%** destination-capable.
- Destination-only result: **21.86R**, expectancy **1.821R**.
- Discovery half: 5/6 (83.3%); untouched second half: 2/6 (33.3%).
- Leave-one-IST-day-out precision range: 50.0–66.7%.
- Classified as basic split-stable: **True**.

## Broader pattern present in most destination-capable trades

`destination_width_r<=1 AND destination_origin_to_bos_bars_4h<=4 AND round_trip_cost_r<=0.35`

Meaning: retain the same narrow, quickly confirmed 4H destination, but use estimated round-trip cost no greater than 0.35R instead of the stricter one-way spread cap.

- Keeps **16**: **8** destination hits / **8** failures (**50.0%**).
- Captures **8/15** (53.3%) of all destination-capable trades while only **8/69** failures satisfy it.
- Discovery: 5/6; second half: 3/10; destination-only result **21.41R**.

## Pre-order causal translation

The strict discovery ratio used actual fill-to-original-stop risk. Replacing that denominator with requested signal risk makes the screen available before order submission and is the correct form for shadow testing.

- Pre-order equivalent keeps **15** trades with **7** hits (**46.7%**) and **18.86R**.
- Discovery: 5/8; second half: 2/7.
- It is weaker than the fill-based result, so the fill-based 58.3% must not be assumed achievable as a pre-trade gate.

## Interpretation

This is an entry-quality screen, not an exit-policy backtest. It intentionally ignores realized P/L, MFE, partial exits, and trailing-stop events when selecting patterns. A high-quality subset may still differ after costs and management are changed.

The rule search is exploratory: thresholds and up to three causal conditions were tested on the first half, then checked on the chronological second half. Even a split-stable rule is a hypothesis, not proven edge, because this archive contains only eleven days and correlated currency pairs.

## Data limitations

- The price archive has no pre-19-August warm-up. Older 4H/15m candle-formation features are null rather than reconstructed with future or incomplete data.
- Logged actionable running-extreme fields exist on 77/91 trades; missing early values remain null.
- Journal-derived blocker counts are causal but can overstate old blockers when pre-archive invalidation cannot be observed.
- Five-minute OHLC cannot identify intrabar order, so same-bar stop/destination ties are pessimistically losses.

## Outputs

- Flat audit: `data\s146_mt5\archive_since_2026-08-19_IST\analysis\s146_destination_quality.csv`
- Full report: `data\s146_mt5\archive_since_2026-08-19_IST\analysis\s146_destination_quality.json`
