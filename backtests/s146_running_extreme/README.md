# S146 running-extreme one-year MT5 backtest

This package is isolated under `backtests\s146_running_extreme`. It downloads native, closed MT5 H4/M15/M5 bars and causally replays S146 running-extreme confirmations. It imports the live detector primitives and strategy zone/swing primitives; it does not copy the detector implementation.

## Exact Windows commands

Run from `d:\WorkZera\Projects\strategies\yt_learning`:

```powershell
py -3 backtests\s146_running_extreme\cli.py fetch --days 365
py -3 backtests\s146_running_extreme\cli.py run --days 365
```

The first command is read-only: it connects through `MT5Client`, resolves the current `CONFIG.symbols` basket, calls only historical rate APIs, and never places, modifies, or closes an order. It writes native bars to `data\s146_running_extreme\raw\{SYMBOL}\{4h|15m|5m}\{SYMBOL}_{tf}.csv` and records broker symbol metadata, point/digits, chunk requests, coverage, and gaps in `raw\manifest.json`.

`run` reads the raw folder and does not require MT5. It writes `data\s146_running_extreme\runs\<run-id>\config.json`, `manifest.json`, `events.jsonl`, `detector_signals.jsonl`, `trades.json`, `summary.json`, `trades.csv`, and `rejections.json`. Detector-valid signals remain in `detector_signals.jsonl` even if spread or chronological portfolio gates reject them. Use `--start`, `--end`, `--days`, `--run-id`, `--symbols`, and `--include-portfolio-gates true|false` as needed.

Dashboard:

```powershell
py -3 dashboard\s146_running_extreme\server.py
```

## Causal and execution rules

- H4/M15/M5 are native feeds; no resampling is performed. Full zones and confirmed swings are built once, then activation/confirmation timestamps and closed-bar prefixes prevent future leakage.
- Destinations are active, unmitigated 4H zones at each cutoff and are screened by configured age and BOS-lag limits. Same-direction 15m candidates must link to the nearest destination, be within their first-touch wait, and be the running price extreme since destination confirmation.
- A confirmation signal is market execution at the next M5 open. Raw M5 OHLC is bid; long fills use ask and long exits use bid, while short fills use bid and short exits use ask, with the opposite side approximated as bid plus `spread * point`. Signal-time spread cost and optional short stop padding use the configured live settings.
- Ladder mode uses the destination as the broker ceiling, records the configured partial (including an assumed partial/runner representation for 0.01-lot cases), moves to breakeven, then trails on 0.5R-style rungs and giveback. Ladder-off mode uses the configured fixed R target.
- Five-minute OHLC cannot reveal intrabar ordering. If a bar reaches both stop and favorable levels, the simulator uses stop-first and records an ambiguity flag. Data gaps are retained as explicit events.

No MT5 connection or long run is started by this package validation.

## Complete artifact and dashboard views

The corrected checked-in run is `data\s146_running_extreme\runs\s146-20260820T091500Z`, scored from `2025-08-20T09:15:00Z` through the last common closed 5m boundary `2026-08-20T09:15:00Z`. It contains 1,618 detector-valid signals, 1,167 accepted trades, 451 execution rejections, and the complete event stream. Each accepted trade in `trades.json` contains its event timeline; `events.jsonl` contains the merged chronological stream for every signal/trade across all symbols; `detector_signals.jsonl` and `rejections.json` preserve candidates that did not become trades; `trades.csv` is the flat export.

The dashboard exposes all of those records read-only:

- `/api/runs` and `/api/runs/<run>/summary` — run metadata, KPIs, breakdowns, and equity curve.
- `/api/runs/<run>/trades` — paginated/filterable executed trades.
- `/api/runs/<run>/trades/<trade_id>` — complete trade record plus linked events and candle inspector.
- `/api/runs/<run>/signals` — all detector-valid signals, including execution status and rejection reason.
- `/api/runs/<run>/events` — all chronological detector/execution/trade events with symbol/type filters.

The replay’s one-open-per-canonical-pair gate now follows `CONFIG.one_trade_per_pair`, matching the live engine when that setting is disabled or enabled. The replay remains native-bar/OHLC based rather than tick-exact; its stop-first same-bar assumption, spread-side approximation, data gaps, and ladder assumptions are recorded in each run manifest/trade record.