[IST 22-JUN-2026 12:00:00] - Auto-detect CSV column formats in load_csv; save backtest results to dashboard/out/ inside the project
[IST 22-JUN-2026 14:00:00] - Fix UnboundLocalError for timedelta in strategy_44 and strategy_52
[IST 22-JUN-2026 15:00:00] - Compute PnL stats from entry/exit prices when strategies omit pnl_pips
[IST 22-JUN-2026 16:00:00] - Add scrollable candlestick chart with entry/exit markers below results chart
[IST 22-JUN-2026 17:00:00] - Refactor price chart to TradingView Lightweight Charts with native pan/zoom
[IST 23-JUN-2026 10:00:00] - Add BACKTEST INTEGRITY NOTICE comments to all 22 strategy files documenting leaks and fixes
[IST 23-JUN-2026 18:00:00] - Fix hindsight bias in strategies 06, 13, 17, 29, 31, 39, 42 using causal_backtest helpers
[IST 23-JUN-2026 12:00:00] - Fix minor hindsight in strategies 04, 08, 44 via causal_backtest helpers
[IST 23-JUN-2026 14:00:00] - Fix major hindsight bias in strategies 54, 56, 62, 69, 77, 78, 81 via causal_backtest (per-day/bar walks, simulate_exits)
[IST 23-JUN-2026 19:00:00] - Add causal_backtest.py shared helpers; fix CRITICAL strategies 01, 05, 28, 52, 65; close-only iFVG and confirmed swings in core.py
[IST 24-JUN-2026 15:45:00] - Fix dashboard backtest 500 when profit factor is infinite (JSON serialization)
[IST 24-JUN-2026 16:15:00] - Fix compute_volume_profile hang on gold/large-range instruments (capped session bins)
[IST 24-JUN-2026 16:30:00] - Speed up strategy 01 walk-forward; raise dashboard backtest timeout to 600s
[IST 24-JUN-2026 17:00:00] - Speed up strategy 06: per-day 1m scope, binary search, no full-file scans
[IST 24-JUN-2026 17:15:00] - Fix zero PnL when trade direction is bullish/bearish instead of long/short
[IST 24-JUN-2026 18:00:00] - Auto-load XAUUSD CSVs from Exness structured history when no upload provided
[IST 24-JUN-2026 19:00:00] - Add TradingView-style profit/loss boxes on price chart between entry and exit
[IST 22-JUN-2026 20:00:00] - Price chart: LWC v5 canvas trade zones primitive, OHLC legend, neobrutal theme
[IST 22-JUN-2026 21:00:00] - Align chart zones with reference: background-layer zones, autoscale SL/TP, debounced lazy load, pointer hover
[IST 22-JUN-2026 22:00:00] - Async backtest jobs with live progress UI; fix strategy 81 timeout via bisect + default 1y library window
[IST 22-JUN-2026 23:00:00] - Numba JIT layer (fast_core.py); O(log n) candle index lookups across all strategies; JIT MSS/resample/exits
[IST 22-JUN-2026 23:30:00] - Fix chart black band on hover by removing primitive drawBackground layer; cap zone autoscale
[IST 25-JUN-2026 01:30:00] - Fix price chart hover black band (remove Y clamping, restore background-layer zones) and restore trade zone/marker rendering for invalid open-trade exit times
[IST 25-JUN-2026 12:45:00] - Add automatic strategy concept overlays on price chart (VP, FVG, liquidity, sessions, fib, structure) with per-category toggles and active-trade filtering
[IST 25-JUN-2026 18:30:00] - Improve concept overlay label placement (compact tags, collision avoidance, trade-zone reservation); compact trade zone labels with hover detail; fix uploaded CSV backtests ignoring library window trim
[IST 25-JUN-2026 20:15:00] - Fix price chart hover black band: enable LWC autoSize, cream pane backgrounds, taller chart wrap, and integer sizing sync
